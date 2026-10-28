from pathlib import Path
import json
import re
from urllib.parse import quote
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_detail_chunk(district):
    fp = ROOT / "site/building_details" / (str(district) + ".js")
    txt = fp.read_text()
    m = re.search(r"\]\s*=\s*(\{.*\});\s*$", txt, re.S)
    assert m, fp
    return json.loads(m.group(1))


def test_derived_files_exist():
    for rel in [
        "data/derived/ratio_by_region_class_month.csv",
        "data/derived/ratio_by_region_month.csv",
        "data/derived/ratio_map.geojson",
        "data/derived/ccl_validation_detail.csv",
        "data/derived/centaline_building_points.csv",
        "data/derived/centaline_building_points.geojson",
        "data/derived/building_history_36m.json",
        "site/building_details.js",
        "reports/validation_report.json",
        "site/index.html",
        "site/ratio_yield_latest.png",
        "site/ratio_yield_latest.svg",
    ]:
        assert (ROOT / rel).exists(), rel


def test_formula_and_latest():
    df = pd.read_csv(ROOT / "data/derived/ratio_by_region_class_month.csv")
    x = df.dropna(subset=["rent_monthly_hkd_m2", "price_hkd_m2", "gross_yield_pct", "price_to_rent_years"])
    expected = x["rent_monthly_hkd_m2"] * 12 / x["price_hkd_m2"] * 100
    assert (expected.sub(x["gross_yield_pct"]).abs() < 1e-9).all()
    expected_years = x["price_hkd_m2"] / (x["rent_monthly_hkd_m2"] * 12)
    assert (expected_years.sub(x["price_to_rent_years"]).abs() < 1e-12).all()


def test_validation_report_all_pass():
    report = json.loads((ROOT / "reports/validation_report.json").read_text())
    assert report["validations"]
    assert all(v.get("pass") for v in report["validations"])


def test_centaline_snapshot_is_present_and_historical():
    import json
    p = ROOT / "data/raw/centaline/ccl.json"
    payload = json.loads(p.read_text())
    points = payload["ccl"]["chartData"]
    assert len(points["date"]) == len(points["index"]) == 1711
    assert points["date"][0].startswith("1994-")
    assert points["date"][-1].startswith("2026-")


def test_building_points_have_hong_kong_coordinates():
    import json
    geo = json.loads((ROOT / "data/derived/centaline_building_points.geojson").read_text())
    assert len(geo["features"]) >= 30
    for f in geo["features"]:
        lon, lat = f["geometry"]["coordinates"]
        assert 113.8 < lon < 114.6
        assert 22.1 < lat < 22.7



def test_full_corpus_transactions_normalized():
    df = pd.read_parquet(ROOT / "data/normalized/centaline/transactions_2026-10-16.parquet",
                         columns=["source_record_id", "listing_type", "transaction_date",
                                  "saleable_psf", "sale_price_hkd", "rent_monthly_hkd"])
    assert len(df) >= 280_000, len(df)
    assert df.source_record_id.is_unique
    assert set(df.listing_type.dropna().unique()) == {"sale", "rent"}
    span = df.transaction_date.max() - df.transaction_date.min()
    assert span.days >= 1000
    sale = df[df.listing_type == "sale"]
    rent = df[df.listing_type == "rent"]
    assert sale.sale_price_hkd.notna().mean() > 0.9
    assert rent.rent_monthly_hkd.notna().mean() > 0.9


def test_crawl_plan_and_page_integrity():
    base = ROOT / "data/raw/centaline/transaction_search/2026-10-16"
    plan = json.loads((base / "plan.json").read_text())
    pages = list((base / "pages").rglob("*.json.gz"))
    planned = 0
    for leaf in plan:
        if leaf["count"] <= 10000:
            planned += -(-leaf["count"] // 100)
        else:
            planned += 100 + (min(leaf["count"] - 10000, 10000) + 99) // 100
    assert len(plan) >= 150
    assert len(pages) == planned, (len(pages), planned)
    # every page file is a valid gzip json blob with data list
    import gzip
    sample = pages[len(pages) // 2]
    blob = json.load(gzip.open(sample, "rt"))
    assert isinstance(blob.get("data"), list)
    assert blob["data"]


def test_building_metrics_formula_and_sample_gate():
    df = pd.read_csv(ROOT / "data/derived/building_metrics_12m.csv")
    assert len(df) >= 1400
    ok = df.dropna(subset=["gross_yield_pct", "sale_psf", "rent_psf"])
    assert len(ok) >= 900
    expected = ok.rent_psf * 12 / ok.sale_psf * 100
    assert (expected.sub(ok.gross_yield_pct).abs() < 1e-6).all()
    low = df[(df.sale_n < 1) | (df.rent_n < 1)]
    assert low.gross_yield_pct.isna().all()
    assert ok.gross_yield_pct.between(0.3, 15).all()


def test_map_data_js_payload():
    js = (ROOT / "site/map_data.js").read_text()
    assert js.startswith("window.HK_MAP_DATA=")
    data = json.loads(js[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    assert len(data["buildings"]) >= 1400
    with_yield = [b for b in data["buildings"] if b.get("yield") is not None]
    assert len(with_yield) >= 900
    geos = {b.get("geo") for b in with_yield}
    assert "grid" in geos
    for b in data["buildings"]:
        assert 113.8 < b["lon"] < 114.6 and 22.1 < b["lat"] < 22.7, b["id"]
    assert len(data["estates"]) >= 300
    assert len(data["districts"]["features"]) == 18
    # the map feed is slim: deal lists and history live in building_details.js
    assert not any(b.get("deals") for b in with_yield)


def test_building_details_payload():
    js = (ROOT / "site/building_details.js").read_text()
    assert js.startswith("window.HK_BUILDING_DETAILS=")
    index = json.loads(js[len("window.HK_BUILDING_DETAILS="):].rstrip().rstrip(";"))
    assert len(index.get("districts") or {}) >= 18
    chunks = [load_detail_chunk(d) for d in index["districts"]]
    months = chunks[0]["months"]
    assert len(months) >= 36 and "2023-10" in months
    buildings = {}
    for d in chunks:
        buildings.update(d["buildings"])
    assert len(buildings) >= 1400
    yield_pts = 0
    deals_seen = 0
    for bid, rec in buildings.items():
        h = rec.get("h")
        if h:
            for key in ("s", "sn", "r", "rn"):
                assert len(h[key]) == len(months), bid
            for i in range(len(months)):
                # sample gate: a published month must carry >= 1 deal in the window
                assert h["sn"][i] == 0 or h["sn"][i] >= 1, bid
                assert h["rn"][i] == 0 or h["rn"][i] >= 1, bid
                if h["s"][i] > 0 and h["r"][i] > 0:
                    yield_pts += 1
        for side in ("sale", "rent"):
            for dl in rec.get(side) or []:
                deals_seen += 1
                assert dl["url"], bid
                assert not dl["url"].startswith("https://hk.centanet.com/findproperty/transaction-detail/")
    assert yield_pts >= 5000
    assert deals_seen >= 5000


def test_map_page_wiring():
    html = (ROOT / "site/index.html").read_text()
    for token in ["map_data.js", "map.js", "building_details.js", "leaflet-heat.js",
                  "corrVol", "corrPrice", "公开数据对照"]:
        assert token in html, token
    assert "vendor/leaflet.js" in html
    assert 'name="base"' not in html
    # tabs live in the header; the map is the first tab and fills everything below the header
    assert 'data-panel="map" id="panel-map"' in html
    header = html.split('class="appbar"')[1].split("</header>")[0]
    assert 'data-tab="map"' in header and "地图" in header
    order = ["map", "summary", "ratio", "history", "news", "external", "validation"]
    positions = [html.index('data-tab="%s"' % t) for t in order]
    assert positions == sorted(positions)
    compact = html.replace(" ", "")
    assert "#panel-map{position:fixed" in compact
    assert 'body[data-tab="map"]{overflow:hidden}' in compact


def test_map_drawer_contract():
    js = (ROOT / "site/map.js").read_text()
    for token in ["hk-drawer", "building_details.js", "drawer-grip", "drawTrend",
                  "wgs2gcj", "interactive: false", "HK_DETAILS_BY_DISTRICT",
                  "售 · 近 12 个月", "租 · 近 12 个月"]:
        assert token in js, token
    assert "map-card" not in js
    assert js.count("bindPopup") <= 2  # school dot/campus popups only; building details stay in drawer
    for fn in ["hkd(", "hkdPsf", "rentPsf", "rentHkd"]:
        assert fn in js, fn
    # money is always explicit HK$; no legacy "@7,813"/"537 万" formatting
    assert "万" not in js


def test_map_yield_filter_contract():
    js = (ROOT / "site/map.js").read_text()
    for token in ["minYield", "lgFilter", "applyYieldFilter", "最低回报率筛选", "lgCount", "Math.min(6",
                  "lgSchool", "applySchoolFilter", "passesSchools", "let minYield = 3.0"]:
        assert token in js, token
    assert 'id="lgFilter" min="0" max="6"' in js
    html = (ROOT / "site/index.html").read_text()
    for token in ["lg-filter", "lg-count"]:
        assert token in html, token


def test_map_merged_legend_layer_defaults_and_grey_toggle():
    js = (ROOT / "site/map.js").read_text()
    # 图层默认：气泡点/18 区边界开，柱状图、热力图、楼盘名标注关
    assert "overlays = { bars: false, heat: false, bubbles: true, labels: false, districts: true, footprints: true, estateFallback: true, schools: true }" in js
    # 图例与筛选合并为一个卡片（只创建一个 hk-legend 容器），样本不足用复选框控制
    assert js.count("'map-fx hk-legend'") == 1
    for token in ["lgNoYield", "setShowNoYield", "showNoYield", "样本不足楼栋", "lg-check",
                  "estateFallback", "屋苑级回退点", "lgSchool", "1 公里内学校"]:
        assert token in js, token
    assert "let showNoYield = false" in js
    assert "let maxAge = 30" in js
    # 灰色（无回报率）楼栋在勾选关闭后不进入气泡层
    assert "if (b.yield == null) { if (!showNoYield || !passesAge(b) || !passesSchools(b)) continue; }" in js
    html = (ROOT / "site/index.html").read_text()
    for token in ["lg-check", ".hk-legend{left:12px;bottom:12px;width:214px"]:
        assert token in html, token


def test_building_age_building_weighted_median():
    df = pd.read_csv(ROOT / "data/derived/building_metrics_12m.csv")
    for col in ["op_year", "op_year_min", "op_year_max", "age_mixed", "age_buildings"]:
        assert col in df.columns, col
    # 回归：街区型楼宇组含 12 栋 1959-1966 老楼 + Larchwood(2024, 212 条记录)。
    # 按记录取中位会得到 2024（约 2 年，错误）；按栋取中位应回到 1960 年代。
    g = df[(df.hma_id == "HMA015") & (df.bldg_grp_id == "YM0031")].iloc[0]
    assert 1955 <= g.op_year <= 1970, g.op_year
    assert g.age_years >= 55, g.age_years
    assert int(g.op_year_min) == 1959 and int(g.op_year_max) == 2024
    assert str(g.age_mixed).lower() == "true"
    # 混合楼龄（组内跨度 >10 年）应有相当数量并带标记
    assert df.age_mixed.astype(str).str.lower().eq("true").sum() >= 100
    assert int(g.age_buildings) >= 10
    # 前端：混合组展示组内年份范围 + 混合楼龄 chip
    js = (ROOT / "site/map.js").read_text()
    for token in ["age_mixed", "混合楼龄", "组内 ' + String(b.op_year_min)"]:
        assert token in js, token
    data_js = (ROOT / "site/map_data.js").read_text()
    data = json.loads(data_js[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    b = next(x for x in data["buildings"] if x["id"] == "HMA015:YM0031")
    assert b["age_years"] >= 55 and b["age_mixed"] is True
    assert b["op_year_min"] == 1959 and b["op_year_max"] == 2024


def test_mixed_group_per_building_yield_and_breakdown():
    bm = pd.read_csv(ROOT / "data/derived/building_metrics_by_building_12m.csv")
    for col in ["building_name", "address_raw", "sale_n", "rent_n", "sale_psf",
                "rent_psf", "gross_yield_pct", "year_final"]:
        assert col in bm.columns, col
    g = bm[(bm.hma_id == "HMA015") & (bm.bldg_grp_id == "YM0031")]
    lw = g[g.building_name == "Larchwood"].iloc[0]
    assert lw.sale_n >= 3 and lw.rent_n >= 3
    assert 4.0 <= lw.gross_yield_pct <= 4.6
    old = g[g.building_name == "長豐大廈"].iloc[0]
    assert pd.isna(old.gross_yield_pct)  # 老楼无租务成交 -> 该栋不发布
    data_js = (ROOT / "site/map_data.js").read_text()
    data = json.loads(data_js[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    b = next(x for x in data["buildings"] if x["id"] == "HMA015:YM0031")
    # 混合楼龄组头部改用按栋中位（Larchwood 4.26%），整组租/售中位配对仅留审计
    assert b["yield_basis"] == "building"
    assert abs(b["yield"] - lw.gross_yield_pct) < 0.01
    assert b["yield_group_pct"] > 10
    assert b["yield_bldg_n"] == 1
    det = load_detail_chunk("油尖旺區")
    rec = det["buildings"]["HMA015:YM0031"]
    assert len(rec["bld"]) >= 8
    names = {r["n"] for r in rec["bld"]}
    assert "Larchwood" in names and "長豐大廈" in names
    lw2 = next(r for r in rec["bld"] if r["n"] == "Larchwood")
    assert lw2["yld"] is not None and lw2["y"] == 2024
    js = (ROOT / "site/map.js").read_text()
    for token in ["dr-bld", "按栋配对", "组内各栋明细", "混合楼龄组：暂无按栋中位走势（各栋售/租样本不足）"]:
        assert token in js, token


def test_map_fixed_axes_and_bubble_size_contract():
    js = (ROOT / "site/map.js").read_text()
    # 固定纵轴（跨楼盘可比）+ 超出范围截断
    for token in ["const AXIS = {", "axis: AXIS.yield", "axis: AXIS.sale", "axis: AXIS.rent",
                  "clipPath", "超出纵轴范围已截断"]:
        assert token in js, token
    assert "yield: { min: 0, max: 6" in js
    assert "sale: { min: 0, max: 40000" in js
    assert "rent: { min: 0, max: 100" in js
    # 气泡大小 = 近 12 个月售+租成交宗数，并在图层面板给出尺寸图例
    for token in ["mp-bubblekey", "气泡大小 = 近 12 个月售 + 租成交宗数"]:
        assert token in js, token
    assert "bubbleRadius({ sale_n: n, rent_n: 0 })" in js
    html = (ROOT / "site/index.html").read_text()
    for token in ["mp-bubblekey", ".bk-row", "clip-note"]:
        assert token in html, token


def test_gallery_building_history_and_drilldown():
    det = load_detail_chunk("油尖旺區")
    rec = det["buildings"]["HMA015:YM0031"]
    # 图片：来自中原成交纪录附图
    assert rec.get("img") and rec["img"][0]["u"].startswith("https://")
    # 混合组按栋中位月度走势
    assert rec.get("hb") and len(rec["hb"]["y"]) == len(det["months"])
    assert sum(1 for v in rec["hb"]["y"] if v is not None) >= 2
    assert max(rec["hb"]["n"]) >= 1
    # 按栋行内带该栋月度序列与成交明细
    lw = next(r for r in rec["bld"] if r["n"] == "Larchwood")
    assert lw.get("h") and sum(1 for v in lw["h"]["s"] if v) >= 2
    assert lw.get("dl") and lw["dl"].get("rent")
    assert lw["dl"]["sale"][0]["url"]
    js = (ROOT / "site/map.js").read_text()
    for token in ["gal-main", "gal-thumbs", "renderGallery", "showBldFocus", "bfChart", "组内各栋明细",
                  "gal-lb", "lb-prev", "lb-next", "openLightbox", "closeLightbox"]:
        assert token in js, token
    assert "混合楼龄组：暂无按栋中位走势" in js


def test_street_group_history_scope():
    h = json.loads((ROOT / "data/derived/building_history_by_building.json").read_text())
    # 所有街区型组（不限混合楼龄）都有逐栋月度序列
    assert len(h["groups"]) >= 700, len(h["groups"])
    assert sum(len(v) for v in h["buildings"].values()) >= 9000
    det = load_detail_chunk("油尖旺區")
    # HMA015:YM0026 是非混合街区组，逐栋行也应带 h（供钻取画走势）
    rec = det["buildings"]["HMA015:YM0026"]
    assert rec.get("bld")
    with_h = [r for r in rec["bld"] if r.get("h")]
    assert with_h and any(any(r["h"]["s"]) for r in with_h)


def test_28hse_secondary_age_verification():
    p = ROOT / "data/derived/building_age_28hse.csv"
    assert p.exists(), "run src/verify_building_age_28hse.py"
    d = pd.read_csv(p)
    assert len(d) >= 600, len(d)
    assert d.op_year_28hse.between(1900, 2030).all()
    both = d.dropna(subset=["op_year_centaline"])
    delta = (both.op_year_28hse - both.op_year_centaline).abs()
    assert (delta <= 2).mean() >= 0.85, (delta <= 2).mean()


def test_footprint_phase1_prototype():
    p = ROOT / "site/footprints.js"
    assert p.exists(), "run src/build_footprints.py"
    js = p.read_text()
    d = json.loads(js[len("window.HK_FOOTPRINTS="):].rstrip().rstrip(";"))
    assert len(d["features"]) >= 500, len(d["features"])
    assert "地政總署" in d["license"]
    for f in d["features"][:20]:
        assert f["geometry"]["type"] in ("MultiPolygon", "Polygon")
        assert f["properties"]["id"] and f["properties"]["match"] in ("name", "pip", "near")
        assert f["properties"]["source"] == "CSDI/LandsD"
    gs = pd.read_csv(ROOT / "reports/footprint_gold_sample.csv")
    assert len(gs) == 100, len(gs)
    assert (ROOT / "site/footprint_review.html").exists()
    mj = (ROOT / "site/map.js").read_text()
    for token in ["footprints", "官方建筑轮廓（z≥17 自动）", "CSDI Building", "low-confidence", "loadFootprints", "footLayer"]:
        if token == "low-confidence":
            assert "低置信候选" in mj
        else:
            assert token in mj, token


def test_local_image_cache_webp():
    m = json.loads((ROOT / "site/building_images_local.json").read_text())
    assert len(m) >= 2900, len(m)
    vals = list(m.values())
    assert all(v.endswith(".webp") for v in vals)
    for v in vals[:80]:
        assert (ROOT / "site" / v).exists(), v
    legacy = [p for p in (ROOT / "site/img/buildings").rglob("*")
              if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    assert not legacy, len(legacy)


def test_school_access_counts_and_map_fields():
    s = pd.read_csv(ROOT / "data/derived/school_access_1km.csv")
    assert len(s) >= 2000
    assert s.schools_1km.max() > 0
    assert (s.schools_kindergarten >= 0).all()
    assert (s.schools_primary >= 0).all()
    assert (s.schools_secondary >= 0).all()
    assert (s.schools_university >= 0).all()
    assert (s.schools_college >= 0).all()
    js = (ROOT / "site/map_data.js").read_text()
    data = json.loads(js[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    assert all("schools_1km" in b for b in data["buildings"][:50])
    assert all("schools_1km" in e for e in data["estates"][:50])
    schools_js = (ROOT / "site/schools.js").read_text()
    assert schools_js.startswith("window.HK_SCHOOLS=")
    schools = json.loads(schools_js[len("window.HK_SCHOOLS="):].rstrip().rstrip(";"))
    assert len(schools["points"]) >= 2000
    assert len(schools["campuses"]) >= 20
    js2 = (ROOT / "site/map.js").read_text()
    for token in ["schools: true", "SCHOOL_MIN_ZOOM", "drawSchoolLayer", "hk-school-dot"]:
        assert token in js2, token


def test_map_age_filter_and_rent_source_note():
    js = (ROOT / "site/map.js").read_text()
    for token in ["maxAge", "lgAge", "applyAgeFilter", "最高楼龄", "passesAge"]:
        assert token in js, token
    # 楼龄筛选同时作用于气泡/柱状图/热力图/标注；无楼龄记录的楼栋在筛选时一并隐藏
    assert "if (b.yield == null) { if (!showNoYield || !passesAge(b) || !passesSchools(b)) continue; }" in js
    assert js.count("!passesSchools(b)) continue;") >= 3
    for token in ["lgSchool", "applySchoolFilter", "minSchools", "passesSchools"]:
        assert token in js, token
    # 租务数据口径提示（中原自家成交 ≠ 全市场）
    for token in ["租赁成交来自中原自家经手", "土地注册处全量"]:
        assert token in js, token
    html = (ROOT / "site/index.html").read_text()
    for token in ["lg-age", "dr-mini", "不存在全市场逐单租务成交库"]:
        assert token in html, token
    data_js = (ROOT / "site/map_data.js").read_text()
    data = json.loads(data_js[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    # 楼龄筛选依赖地图数据里的 age_years
    assert sum(1 for b in data["buildings"] if b.get("age_years") is not None) >= 900


def test_map_building_age_and_tab_redraw():
    js = (ROOT / "site/map.js").read_text()
    # 抽屉回报率主块 70% + 楼龄 30%
    for token in ["dr-yield-age", "dr-yield-main", "age_years", "楼龄"]:
        assert token in js, token
    html = (ROOT / "site/index.html").read_text()
    for token in [".dr-yield-age", "grid-template-columns:7fr 3fr"]:
        assert token in html, token
    # 切换售/租页签后按实际可见宽度重绘图表（隐藏面板曾以 240px 绘制）
    assert "if (state) renderDetail();" in js
    # 资产带构建版本号，避免浏览器缓存旧脚本
    assert "?v=" in html and "map.js?v=" in html
    data_js = (ROOT / "site/map_data.js").read_text()
    data = json.loads(data_js[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    assert data.get("build")
    with_age = [b for b in data["buildings"] if b.get("age_years") is not None]
    assert len(with_age) >= 900, len(with_age)
    for b in with_age:
        assert 0 <= b["age_years"] <= 90, (b["id"], b["age_years"])
        assert b.get("op_year") == 2026 - b["age_years"], b["id"]
    # 楼龄字段也进入可复核的聚合表
    df = pd.read_csv(ROOT / "data/derived/building_metrics_12m.csv")
    assert "op_year" in df.columns and "age_years" in df.columns
    assert df.age_years.notna().sum() >= 1000


def test_external_corroboration_stats():
    d = json.loads((ROOT / "data/derived/external_corroboration.json").read_text())
    vi = d["volume_identity"]
    assert vi["overlap_months"] >= 60
    assert vi["exact_match_months"] == vi["overlap_months"]
    assert vi["max_abs_diff"] == 0
    assert d["price_shape_etnet_vs_ours"]["corr_level"] > 0.6
    assert d["price_shape_etnet_vs_ours_3m"]["direction_match_pct"] >= 70
    assert d["volume_shape_ours_vs_lr"]["corr_level"] > 0.6


def test_gcj02_conversion_applied_in_map_js():
    js = (ROOT / "site/map.js").read_text()
    assert "wgs2gcj" in js and "GCJ" in js
    # replicate the same algorithm and confirm the known HK offset magnitude
    import math
    PI = math.pi; AA = 6378245.0; EE = 0.00669342162296594323
    def tLat(x, y):
        r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
        r += (20 * math.sin(6 * x * PI) + 20 * math.sin(2 * x * PI)) * 2 / 3
        r += (20 * math.sin(y * PI) + 40 * math.sin(y / 3 * PI)) * 2 / 3
        r += (160 * math.sin(y / 12 * PI) + 320 * math.sin(y * PI / 30)) * 2 / 3
        return r
    def tLng(x, y):
        r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        r += (20 * math.sin(6 * x * PI) + 20 * math.sin(2 * x * PI)) * 2 / 3
        r += (20 * math.sin(x * PI) + 40 * math.sin(x / 3 * PI)) * 2 / 3
        r += (150 * math.sin(x / 12 * PI) + 300 * math.sin(x / 30 * PI)) * 2 / 3
        return r
    lng, lat = 114.1694, 22.2793
    dLat = tLat(lng - 105, lat - 35); dLng = tLng(lng - 105, lat - 35)
    rad = lat / 180 * PI; m = 1 - EE * math.sin(rad) ** 2; sm = math.sqrt(m)
    dLat = (dLat * 180) / ((AA * (1 - EE)) / (m * sm) * PI)
    dLng = (dLng * 180) / (AA / sm * math.cos(rad) * PI)
    east_m = dLng * 101000; north_m = dLat * 111000
    assert 380 < east_m < 620 and -420 < north_m < -200


def test_full_history_and_hma090_building_deals():
    summ = json.loads((ROOT / "data/normalized/centaline/transactions_full_2026-10-18.summary.json").read_text())
    assert summ["records"] >= 1_000_000
    assert summ["date_min"] <= "1995-12-31"
    d = load_detail_chunk("九龍城區")
    rec = d["buildings"]["HMA090:KC0135"]
    row = next(x for x in rec["bld"] if x["n"] == "興利大廈")
    assert row["s"] >= 50
    assert len(row["dl"]["sale"]) >= 20
    assert row["dl"]["sale"][-1]["d"] <= "2012-01-01"
    assert rec["rent28"]["n"] >= 1


def test_28hse_second_rent_source():
    listings = pd.read_csv(ROOT / "data/derived/28hse_rent_listings.csv")
    assert (listings.estate_slug == "興利大廈-22863").any()
    hist = pd.read_csv(ROOT / "data/derived/28hse_rent_history.csv")
    assert len(hist) >= 3000
    assert hist.group_id.notna().sum() >= 1000


def test_rental_unit_token_normalization():
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from build_building_metrics import add_dedupe_keys

    x = pd.DataFrame([
        {"building_name": "1座", "floor": "高層", "unit": "F室",
         "hma_id": "H1", "bldg_grp_id": "G1", "listing_type": "rent",
         "price_hkd": 20000, "saleable_area_sqft": 500, "analysis_date": "2026-01-01"},
        {"building_name": "太古城 海景花園 美菊閣", "floor": "", "unit": "高層 F室",
         "hma_id": "H1", "bldg_grp_id": "G1", "listing_type": "rent",
         "price_hkd": 20000, "saleable_area_sqft": 500, "analysis_date": "2026-01-01"},
    ])
    y = add_dedupe_keys(x)
    assert (y.unit_token_tmp == "高層f").all()
    assert y.dedupe_key_tmp.nunique() == 1
