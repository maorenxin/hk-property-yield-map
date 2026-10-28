#!/usr/bin/env python3
"""Build Phase-1 official CSDI/LandsD building-footprint outlines.

Input:  data/raw/csdi/2026-10-18/pages/*.json (fetched via SSH egress / Japan)
        data/derived/footprints_phase1.geojson (OSM prototype, optional comparison)
Output: data/derived/footprints_phase1_official.geojson
        site/footprints.js
        reports/footprint_gold_sample.csv
        reports/footprint_gold_standard.md
        site/footprint_review.html
"""
from __future__ import annotations

import json
import math
import random
import re
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "data/raw/csdi/2026-10-18/pages"
OUT_GEO = ROOT / "data/derived/footprints_phase1_official.geojson"
OUT_JS = ROOT / "site/footprints.js"
OUT_SAMPLE = ROOT / "reports/footprint_gold_sample.csv"
OUT_REPORT = ROOT / "reports/footprint_gold_standard.md"
OUT_REVIEW = ROOT / "site/footprint_review.html"
OSM_GEO = ROOT / "data/derived/footprints_phase1.geojson"


def norm_name(s) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)


def ring_area_m2(ring: list[list[float]]) -> float:
    if len(ring) < 3:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    k = math.cos(math.radians(lat0))
    xs = [p[0] * 111320 * k for p in ring]
    ys = [p[1] * 110540 for p in ring]
    return abs(sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(xs) - 1))) / 2


def centroid(ring: list[list[float]]) -> list[float]:
    return [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]


def dist_m(a: list[float], b: list[float]) -> float:
    dx = (a[0] - b[0]) * 111320 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (a[1] - b[1]) * 110540
    return math.hypot(dx, dy)


def pip(pt: list[float], ring: list[list[float]]) -> bool:
    x, y = pt
    inside = False
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


def load_targets() -> dict[str, dict]:
    data = json.loads((ROOT / "site/map_data.js").read_text()[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    builds = {b["id"]: b for b in data["buildings"]}
    rvd = pd.read_csv(ROOT / "data/derived/building_age_rvd.csv")
    hi = rvd[rvd.match.isin(["name+addr", "estate+phase", "name"])]
    gids_hi = {f"{r.hma_id}:{r.bldg_grp_id}" for r in hi.itertuples()}
    bb = pd.read_csv(ROOT / "data/derived/building_metrics_by_building_12m.csv")
    gids_bld = {f"{r.hma_id}:{r.bldg_grp_id}" for r in bb.itertuples()}

    def is_street(b: dict) -> bool:
        s = str(b.get("bldg") or "")
        return bool(b.get("age_mixed")) or ("住宅物業" in s and " / " in s)

    return {gid: builds[gid] for gid in sorted(builds)
            if gid in gids_hi and gid in gids_bld and is_street(builds[gid])}


def load_page(gid: str) -> list[dict]:
    fp = PAGES / (gid.replace(":", "_") + ".json")
    if not fp.exists():
        return []
    d = json.loads(fp.read_text())
    out = []
    for f in d.get("features", []):
        geom = f.get("geometry") or {}
        for ring in geom.get("rings", []):
            if len(ring) < 4:
                continue
            ring = [[round(float(p[0]), 6), round(float(p[1]), 6)] for p in ring]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            dedup = [ring[0]] + [p for a, p in zip(ring, ring[1:]) if p != a]
            if len(dedup) < 4:
                continue
            area = ring_area_m2(dedup)
            if area < 4:
                continue
            out.append({"attrs": f.get("attributes") or {}, "ring": dedup,
                        "centroid": centroid(dedup), "area": area})
    return out


def is_generic(name: str) -> bool:
    if name in {"大廈", "大樓", "住宅", "樓", "花園", "中心", "廣場", "別墅", "山莊"}:
        return True
    return bool(re.fullmatch(r"(第)?\d+座|[a-z]座|\d+期|第?\d+期", name))


def match_group(b: dict, cands: list[dict], bldg_names: list[str]) -> tuple[list[dict], str, dict]:
    p = [float(b["lon"]), float(b["lat"])]
    group_name_raw = str(b.get("name") or "")
    group_n = norm_name(group_name_raw)
    # Long street/building descriptions are not used as an estate prefix.
    estate_like = 3 <= len(group_n) <= 20 and "/" not in group_name_raw and "(" not in group_name_raw
    specific = {norm_name(n) for n in bldg_names if n and len(norm_name(n)) >= 3 and not is_generic(norm_name(n))}
    generic = {norm_name(n) for n in bldg_names if n and norm_name(n) and is_generic(norm_name(n))}

    for c in cands:
        a = c["attrs"]
        on = norm_name(a.get("BuildingNameTC") or a.get("BuildingNameEN"))
        c["official_name"] = on
        c["dist"] = dist_m(p, c["centroid"])
        c["inside"] = pip(p, c["ring"])
        c["group_hit"] = bool(estate_like and on and ((group_n in on) or (len(on) >= 3 and on in group_n)))
        c["specific_hit"] = bool(on and not is_generic(on) and any(
            n == on or (len(n) >= 3 and (n in on or on in n)) for n in specific))
        c["generic_hit"] = bool(on and on in generic)
        c["name_hit"] = c["group_hit"] or (c["specific_hit"] and c["dist"] <= 80)

    def plausible(c: dict, min_area: float) -> bool:
        a = c["attrs"]
        storeys, top = a.get("Storeys"), a.get("TopHeight")
        return (c["area"] >= min_area
                and (storeys is None or storeys >= 2)
                and (top is None or top >= 10))

    name_hits = [c for c in cands if c["name_hit"] and c["area"] >= 20]
    if name_hits:
        # A group-name hit can legitimately cover every tower in an estate; cap only
        # when a broad page contains an unreasonable number of neighbours.
        name_hits.sort(key=lambda c: (not c["group_hit"], c["dist"]))
        picked, conf = name_hits[:60], "name"
    else:
        # Central points often land on a clubhouse/podium rather than a tower.
        # Only a plausible residential Tower containing the point is high-confidence.
        pips = [c for c in cands if c["inside"]
                and c["attrs"].get("BuildingBlockType") == "Tower"
                and plausible(c, 100)]
        if pips:
            pips.sort(key=lambda c: (c["dist"], -c["area"]))
            picked, conf = pips[:5], "pip"
        else:
            towers = [c for c in cands if c["attrs"].get("BuildingBlockType") == "Tower"
                      and plausible(c, 50)]
            near = min(towers, key=lambda c: c["dist"]) if towers else None
            if near and near["dist"] <= 30:
                picked, conf = [near], "near"
            else:
                picked, conf = [], "none"
    info = {"n_candidates": len(cands), "n_name_hits": len(name_hits),
            "n_specific": len(specific), "n_generic": len(generic)}
    return picked, conf, info


def feature_for(gid: str, b: dict, picked: list[dict], conf: str, info: dict) -> dict:
    p = [float(b["lon"]), float(b["lat"])]
    rings = [c["ring"] for c in picked]
    min_dist = round(min(dist_m(p, c["centroid"]) for c in picked), 1) if picked else None
    area = round(sum(c["area"] for c in picked), 1)
    return {"type": "Feature", "properties": {
        "id": gid, "name": b.get("name") or "", "district": b.get("district") or "",
        "source": "CSDI/LandsD", "match": conf, "n_poly": len(picked),
        "min_dist_m": min_dist, "area_m2": area,
        "n_candidates": info["n_candidates"], "n_name_hits": info["n_name_hits"],
    }, "geometry": {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}}


def pair_agreement(off: dict | None, osm: dict | None) -> dict:
    if not off or not osm:
        return {"osm_check": "missing" if not osm else "no_official", "centroid_m": None, "area_ratio": None}
    op = [r for poly in off["geometry"]["coordinates"] for r in poly]
    sp = [r for poly in osm["geometry"]["coordinates"] for r in poly]
    best = None
    for a in op:
        ca, aa = centroid(a), ring_area_m2(a)
        for b in sp:
            cb, ab = centroid(b), ring_area_m2(b)
            d = dist_m(ca, cb)
            ratio = (aa / ab) if ab else None
            if best is None or d < best[0]:
                best = (d, ratio, pip(ca, b) or pip(cb, a))
    if best is None:
        return {"osm_check": "missing", "centroid_m": None, "area_ratio": None}
    d, ratio, inside = best
    ok = d <= 25 and (ratio is None or 0.5 <= ratio <= 2.0)
    return {"osm_check": "pass" if (ok or inside) else "mismatch",
            "centroid_m": round(d, 1), "area_ratio": round(ratio, 2) if ratio else None}


def main() -> int:
    targets = load_targets()
    bb = pd.read_csv(ROOT / "data/derived/building_metrics_by_building_12m.csv")
    feats, rows = [], []
    stats = {"name": 0, "pip": 0, "near": 0, "none": 0}
    for gid, b in targets.items():
        cands = load_page(gid)
        h, g = gid.split(":")
        names = [str(r.building_name) for r in bb[(bb.hma_id == h) & (bb.bldg_grp_id == g)].itertuples()
                 if pd.notna(r.building_name)]
        picked, conf, info = match_group(b, cands, names)
        stats[conf] += 1
        f = feature_for(gid, b, picked, conf, info) if picked else None
        if f:
            feats.append(f)
        rows.append({"id": gid, "name": b.get("name") or "", "district": b.get("district") or "",
                     "match": conf, "n_poly": len(picked),
                     "min_dist_m": f["properties"]["min_dist_m"] if f else None,
                     "area_m2": f["properties"]["area_m2"] if f else None,
                     "n_candidates": info["n_candidates"], "n_name_hits": info["n_name_hits"],
                     "yield": b.get("yield")})
    print("official match stats:", stats, "| features:", len(feats))
    OUT_GEO.parent.mkdir(exist_ok=True)
    OUT_GEO.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")))
    OUT_JS.write_text("window.HK_FOOTPRINTS=" + json.dumps({
        "license": "© 地政總署 / DATA.GOV.HK · CSDI Building (2026-10-18 snapshot)",
        "source": "CSDI Building FeatureServer; fetched through SSH egress (Japan) because local *.gov.hk TLS is unavailable",
        "features": feats}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT_GEO.relative_to(ROOT)} ({OUT_GEO.stat().st_size//1024}KB) and {OUT_JS.relative_to(ROOT)} ({OUT_JS.stat().st_size//1024}KB)")

    osm_feats = {}
    if OSM_GEO.exists():
        try:
            for f in json.loads(OSM_GEO.read_text()).get("features", []):
                osm_feats[f["properties"]["id"]] = f
        except Exception:
            pass
    official_ids = {f["properties"]["id"] for f in feats}
    osm_supp = []
    for gid in targets:
        if gid in official_ids or gid not in osm_feats:
            continue
        f = json.loads(json.dumps(osm_feats[gid]))
        f["properties"].update({"source": "OSM", "match": "osm"})
        osm_supp.append(f)
    if osm_supp:
        all_feats = feats + osm_supp
        OUT_GEO.write_text(json.dumps({"type": "FeatureCollection", "features": all_feats}, ensure_ascii=False, separators=(",", ":")))
        OUT_JS.write_text("window.HK_FOOTPRINTS=" + json.dumps({
            "license": "© 地政總署 / DATA.GOV.HK · CSDI Building + OSM ODbL supplement",
            "source": "CSDI Building official; OSM supplemental outlines for official misses",
            "features": all_feats}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"added {len(osm_supp)} OSM supplemental outlines for official misses")
    for r in rows:
        off = next((f for f in feats if f["properties"]["id"] == r["id"]), None)
        ag = pair_agreement(off, osm_feats.get(r["id"]))
        r.update({"osm_check": ag["osm_check"], "osm_centroid_m": ag["centroid_m"], "osm_area_ratio": ag["area_ratio"]})
        r["review_label"] = "pass" if r["match"] in ("name", "pip") else ("review" if r["match"] == "near" else "miss")

    # Stratified 100-group gold sample, seed fixed for reproducibility.
    random.seed(42)
    by = {}
    for r in rows:
        by.setdefault(r["match"], []).append(r)
    quotas = {"name": 58, "pip": 24, "near": 10, "none": 8}
    sample = []
    for k, q in quotas.items():
        pool = by.get(k, [])
        sample += random.sample(pool, min(q, len(pool)))
    if len(sample) < 100:
        rest = [r for r in rows if r not in sample]
        sample += random.sample(rest, min(100 - len(sample), len(rest)))
    sample = sample[:100]
    pd.DataFrame(sample).to_csv(OUT_SAMPLE, index=False)
    matched_sample = [r for r in sample if r["match"] in ("name", "pip")]
    osm_checked = [r for r in sample if r["osm_check"] in ("pass", "mismatch")]
    osm_pass = [r for r in osm_checked if r["osm_check"] == "pass"]
    print(f"gold sample: {len(sample)} rows -> {OUT_SAMPLE.relative_to(ROOT)}")
    print(f"gold sample official name/pip pass: {len(matched_sample)}/{len([r for r in sample if r['match']!='none'])}; OSM agreement: {len(osm_pass)}/{len(osm_checked)}")

    # Review page: official blue, OSM orange, group point.
    feat_by_id = {f["properties"]["id"]: f for f in feats}
    cards = []
    for r in sample:
        f = feat_by_id.get(r["id"])
        b = targets[r["id"]]
        polys = []
        if f:
            polys += [ring for poly in f["geometry"]["coordinates"] for ring in poly]
        osm = osm_feats.get(r["id"])
        if osm:
            polys += [ring for poly in osm["geometry"]["coordinates"] for ring in poly]
        # Nearby official candidates for miss cases, so a miss can be reviewed.
        if not f:
            polys += [c["ring"] for c in load_page(r["id"])[:25]]
        pts = [p for ring in polys for p in ring] + [[float(b["lon"]), float(b["lat"])]]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        pad = max(x1 - x0, y1 - y0, 1e-5) * 0.12 + 1e-5
        x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
        w, h = 280, 205

        def sx(x): return (x - x0) / (x1 - x0) * w
        def sy(y): return h - (y - y0) / (y1 - y0) * h

        official_paths = ""
        if f:
            near = r["match"] == "near"
            fill = "rgba(180,83,9,.14)" if near else "rgba(37,99,235,.30)"
            stroke = "#b45309" if near else "#1d4ed8"
            dash = ' stroke-dasharray="5 3"' if near else ""
            official_paths = "".join(
                '<path d="' + " ".join(("M" if i == 0 else "L") + f"{sx(pt[0]):.1f} {sy(pt[1]):.1f}" for i, pt in enumerate(ring)) +
                f' Z" fill="{fill}" stroke="{stroke}" stroke-width="1.1"{dash}/>'
                for poly in f["geometry"]["coordinates"] for ring in poly)
        elif polys:
            official_paths = "".join(
                '<path d="' + " ".join(("M" if i == 0 else "L") + f"{sx(pt[0]):.1f} {sy(pt[1]):.1f}" for i, pt in enumerate(ring)) +
                ' Z" fill="rgba(148,163,184,.12)" stroke="#94a3b8" stroke-width=".8"/>'
                for ring in polys)
        osm_paths = ""
        if osm:
            osm_paths = "".join(
                '<path d="' + " ".join(("M" if i == 0 else "L") + f"{sx(pt[0]):.1f} {sy(pt[1]):.1f}" for i, pt in enumerate(ring)) +
                ' Z" fill="none" stroke="#ea580c" stroke-width="1.0" stroke-dasharray="4 3"/>'
                for poly in osm["geometry"]["coordinates"] for ring in poly)
        px, py = sx(float(b["lon"])), sy(float(b["lat"]))
        color = {"name": "#059669", "pip": "#d97706", "near": "#b91c1c", "none": "#64748b"}[r["match"]]
        cards.append(f'''<div class="card"><div class="hd"><b>{str(r["name"])[:34]}</b><span class="tag" style="background:{color}">{r["match"]} · {r["n_poly"]} 面 · {r["min_dist_m"] if r["min_dist_m"] is not None else "—"}m</span></div>
<svg viewBox="0 0 {w} {h}">{official_paths}{osm_paths}<circle cx="{px:.1f}" cy="{py:.1f}" r="4.5" fill="#0f172a" stroke="#fff" stroke-width="1.5"/><text x="{px+7:.1f}" y="{py-6:.1f}" font-size="9" fill="#0f172a">组点位</text></svg>
<div class="meta">{r["district"]} · {r["id"]} · 官方 {r["area_m2"] if r["area_m2"] is not None else "—"} m² · OSM {r["osm_check"]}{(" / " + str(r["osm_centroid_m"]) + "m") if r["osm_centroid_m"] is not None else ""}</div></div>''')
    OUT_REVIEW.write_text(f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Footprint 金标准抽检（100 组）</title>
<style>body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f3f6fa;margin:0;padding:20px;color:#0b1c33}}
h1{{font-size:20px;margin:0 0 6px}}p{{color:#64748b;font-size:13px;margin:0 0 16px;line-height:1.5}}
.legend span{{display:inline-block;margin-right:16px}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:12px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:10px;box-shadow:0 1px 3px rgba(15,23,42,.06)}}
.hd{{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:6px;font-size:12.5px}}
.tag{{color:#fff;font-size:10px;border-radius:999px;padding:1px 7px;white-space:nowrap}}
svg{{display:block;background:#f8fafc;border-radius:6px;width:100%;height:auto}}
.meta{{margin-top:5px;font-size:10.5px;color:#64748b}}</style></head><body>
<h1>Building footprint 金标准抽检（100 组）</h1>
<p>抽样：按匹配类型分层（name 58 / pip 24 / near 10 / none 8，固定 seed=42）。<b>蓝色实线</b>=CSDI 官方匹配轮廓；<b>橙色虚线</b>=OSM 原型轮廓；<b>灰面</b>=未匹配组的附近官方候选；黑点=地图组点位。人工核对：官方轮廓是否覆盖该组楼栋、是否有误配；蓝色/黄色实线为官方 name/pip，黄色虚线为 near 低置信候选；橙色虚线为 OSM 原型。</p>
<div class="legend"><span>🟦 官方 CSDI（name/pip）</span><span>🟨 官方 CSDI（near 低置信）</span><span>🟧 OSM 原型</span><span>⬤ 组点位</span></div>
<div class="grid">{"".join(cards)}</div></body></html>''', encoding="utf-8")
    print(f"review page: {OUT_REVIEW.relative_to(ROOT)} ({OUT_REVIEW.stat().st_size//1024}KB)")

    match_n = sum(1 for r in rows if r["match"] != "none")
    nested = [r for r in matched_sample if r["match"] == "name"]
    OUT_REPORT.write_text(f'''# 建筑轮廓 Phase 1：官方 CSDI 金标准抽检

- 数据源：地政总署 CSDI Building FeatureServer（342,195 个有效建筑面，月度更新）；本机 `*.gov.hk` TLS 不可达，构建期通过 SSH 日本出口直接取回原始 ArcGIS JSON，按组缓存。
- 目标：当前数据中「有逐栋明细 + 至少一个 RVD 高置信楼龄锚点 + 街区/混合组」的交集，共 **{len(targets)} 组**（调研中的 616 是早期「逐栋+RVD」粗估；本轮把逐栋序列扩到全部街区组后，Phase 1 的街区/混合交集为 {len(targets)}）。
- 匹配：官方面名/屋苑名优先；其次组点位落入面内；最后最近 Tower ≤30m。匹配多栋时保留 MultiPolygon，不强行合并成一个面。
- 匹配结果：name **{stats['name']}** / pip **{stats['pip']}** / near **{stats['near']}** / none **{stats['none']}**，官方轮廓覆盖 **{match_n}/{len(targets)}（{match_n/len(targets)*100:.1f}%）**。
- 100 组分层抽检：{len([r for r in sample if r['match'] != 'none'])} 个抽检组有官方轮廓，其中 **{len(matched_sample)} 个达到 name/pip 高置信**；OSM 原型与官方可比较样本中，质心≤25m 且面积比 0.5–2.0（或互相包含）通过 **{len(osm_pass)}/{len(osm_checked)}（{len(osm_pass)/max(1,len(osm_checked))*100:.1f}%）**。
- 产物：`site/footprints.js`、`data/derived/footprints_phase1_official.geojson`、`reports/footprint_gold_sample.csv`、`site/footprint_review.html`。
- 补充：对 {stats['none']} 个官方 none 组，有 **{len(osm_supp)}** 组存在 OSM 轮廓，已作为灰色虚线“OSM 补充轮廓”直接叠加到地图；其余仍保留气泡。
- 注意：CSDI 有名称字段仅覆盖部分建筑；官方轮廓是“组内可见建筑面”的并集近似，不等同于法律意义上的宗地边界。near 类型（{stats['near']} 组）和 none 类型需要人工复核后再宣称高置信。
''', encoding="utf-8")
    print(f"report: {OUT_REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
