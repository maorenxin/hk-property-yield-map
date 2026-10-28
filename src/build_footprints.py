#!/usr/bin/env python3
"""Phase-1 building-footprint prototype (OSM source).

- target: street/mixed groups (same set as the per-building table) that also have
  at least one high-confidence RVD age anchor
- match OSM building polygons by name -> point-in-polygon -> nearest centroid
- outputs: data/derived/footprints_phase1.geojson, site/footprints.js,
  reports/footprint_gold_sample.csv, site/footprint_review.html
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CELLDIR = ROOT / "data/raw/osm/2026-10-18/cells"
OUT_GEO = ROOT / "data/derived/footprints_phase1.geojson"
OUT_JS = ROOT / "site/footprints.js"
OUT_SAMPLE = ROOT / "reports/footprint_gold_sample.csv"
OUT_REVIEW = ROOT / "site/footprint_review.html"
R_EARTH = 6371000.0


def norm(s) -> str:
    return "".join(str(s or "").split()).replace("(", "（").replace(")", "）")


def ring_area_m2(ring):
    if len(ring) < 3:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    k = math.cos(math.radians(lat0))
    xs = [p[0] * 111320 * k for p in ring]
    ys = [p[1] * 110540 for p in ring]
    return abs(sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(xs) - 1))) / 2


def centroid(ring):
    return [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]


def dist_m(a, b):
    dx = (a[0] - b[0]) * 111320 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (a[1] - b[1]) * 110540
    return math.hypot(dx, dy)


def pip(pt, ring):
    x, y = pt
    inside = False
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


def load_osm():
    out = []
    for fp in sorted(CELLDIR.glob("*.json")):
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        for e in d.get("elements", []):
            g = e.get("geometry") or []
            if len(g) < 4:
                continue
            ring = [[p["lon"], p["lat"]] for p in g]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            tags = e.get("tags") or {}
            name = tags.get("name") or tags.get("name:zh") or tags.get("name:en") or ""
            street = tags.get("addr:street") or ""
            num = tags.get("addr:housenumber") or ""
            out.append({"id": e.get("id"), "ring": ring, "name": name,
                        "street": street, "num": num, "tags": tags,
                        "centroid": centroid(ring), "area": ring_area_m2(ring)})
    return out


def main() -> int:
    data = json.loads((ROOT / "site/map_data.js").read_text()[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    builds = {b["id"]: b for b in data["buildings"]}
    rvd = pd.read_csv(ROOT / "data/derived/building_age_rvd.csv")
    hi = rvd[rvd.match.isin(["name+addr", "estate+phase", "name"])]
    gids_hi = {f"{r.hma_id}:{r.bldg_grp_id}" for r in hi.itertuples()}
    bb = pd.read_csv(ROOT / "data/derived/building_metrics_by_building_12m.csv")
    gids_street = {f"{r.hma_id}:{r.bldg_grp_id}" for r in bb.itertuples()}
    # street/mixed = same rule as the drawer breakdown
    def is_street(b):
        s = str(b.get("bldg") or "")
        return bool(b.get("age_mixed")) or ("住宅物業" in s and " / " in s)
    targets = sorted(gid for gid in builds if gid in gids_hi and is_street(builds[gid]))
    print(f"target street/mixed groups with RVD anchors: {len(targets)}")

    osm = load_osm()
    print(f"OSM building polygons loaded: {len(osm)}")
    # spatial index by 0.005 deg
    idx = {}
    for o in osm:
        k = (int(o["centroid"][0] / 0.005), int(o["centroid"][1] / 0.005))
        idx.setdefault(k, []).append(o)

    feats, stats = [], {"name": 0, "pip": 0, "near": 0, "none": 0}
    sample_rows = []
    for gid in targets:
        b = builds[gid]
        p = [b["lon"], b["lat"]]
        names = {norm(r.building_name) for r in bb[(bb.hma_id == gid.split(":")[0]) & (bb.bldg_grp_id == gid.split(":")[1])].itertuples() if pd.notna(r.building_name)}
        k0 = (int(p[0] / 0.005), int(p[1] / 0.005))
        cands = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cands.extend(idx.get((k0[0] + dx, k0[1] + dy), []))
        cands = [c for c in cands if dist_m(p, c["centroid"]) <= 150]
        picked, conf = [], "none"
        if cands:
            name_hits = [c for c in cands if c["name"] and any(n and (n in norm(c["name"]) or norm(c["name"]) in n) for n in names)]
            name_hits = [c for c in name_hits if dist_m(p, c["centroid"]) <= 60]
            if name_hits:
                picked, conf = name_hits[:5], "name"
            else:
                pips = [c for c in cands if pip(p, c["ring"])]
                if pips:
                    pips.sort(key=lambda c: c["area"])
                    picked, conf = pips[:3], "pip"
                else:
                    near = min(cands, key=lambda c: dist_m(p, c["centroid"]))
                    if dist_m(p, near["centroid"]) <= 30:
                        picked, conf = [near], "near"
        stats[conf] += 1
        if picked:
            rings = [list(map(list, c["ring"])) for c in picked]
            feats.append({"type": "Feature", "properties": {
                "id": gid, "name": b.get("name") or "", "district": b.get("district") or "",
                "match": conf, "n_poly": len(picked),
                "min_dist_m": round(min(dist_m(p, c["centroid"]) for c in picked), 1),
            }, "geometry": {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}})
            sample_rows.append({"id": gid, "name": b.get("name") or "", "district": b.get("district") or "",
                                "match": conf, "n_poly": len(picked),
                                "min_dist_m": round(min(dist_m(p, c["centroid"]) for c in picked), 1),
                                "yield": b.get("yield"), "review_label": ""})
    print("match stats:", stats, "| features:", len(feats))
    OUT_GEO.parent.mkdir(exist_ok=True)
    OUT_GEO.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")))
    OUT_JS.write_text("window.HK_FOOTPRINTS=" + json.dumps({
        "license": "OSM ODbL 1.0 (prototype source)",
        "features": feats}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT_GEO.relative_to(ROOT)} ({OUT_GEO.stat().st_size//1024}KB) and {OUT_JS.relative_to(ROOT)} ({OUT_JS.stat().st_size//1024}KB)")

    # gold-standard sample: 100 groups stratified by match type
    random.seed(42)
    by = {}
    for r in sample_rows:
        by.setdefault(r["match"], []).append(r)
    sample = []
    quota = {"name": 44, "pip": 40, "near": 16}
    for k, q in quota.items():
        pool = by.get(k, [])
        sample += random.sample(pool, min(q, len(pool)))
    if len(sample) < 100:
        rest = [r for r in sample_rows if r not in sample]
        sample += random.sample(rest, min(100 - len(sample), len(rest)))
    pd.DataFrame(sample).to_csv(OUT_SAMPLE, index=False)
    print(f"gold sample: {len(sample)} rows -> {OUT_SAMPLE.relative_to(ROOT)}")

    # static SVG review page (no tiles; polygon vs point)
    feats_by_id = {f["properties"]["id"]: f for f in feats}
    cards = []
    for r in sample:
        f = feats_by_id.get(r["id"])
        if not f:
            continue
        polys = f["geometry"]["coordinates"]
        pts = [pt for poly in polys for ring in poly for pt in ring]
        xs = [p[0] for p in pts + [[builds[r["id"]]["lon"], builds[r["id"]]["lat"]]]]
        ys = [p[1] for p in pts + [[builds[r["id"]]["lon"], builds[r["id"]]["lat"]]]]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        pad = max((x1 - x0), (y1 - y0)) * 0.12 + 1e-5
        x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
        w, h = 260, 200
        def sx(x): return (x - x0) / (x1 - x0) * w
        def sy(y): return h - (y - y0) / (y1 - y0) * h
        paths = "".join(
            '<path d="' + " ".join(("M" if i == 0 else "L") + f"{sx(pt[0]):.1f} {sy(pt[1]):.1f}" for i, pt in enumerate(ring)) + ' Z" fill="rgba(37,99,235,.28)" stroke="#1d4ed8" stroke-width="1.2"/>'
            for poly in polys for ring in poly)
        px, py = sx(builds[r["id"]]["lon"]), sy(builds[r["id"]]["lat"])
        color = {"name": "#059669", "pip": "#d97706", "near": "#b91c1c"}[r["match"]]
        cards.append(f'''<div class="card"><div class="hd"><b>{r["name"][:28]}</b><span class="tag" style="background:{color}">{r["match"]} · {r["n_poly"]} 面 · {r["min_dist_m"]}m</span></div>
<svg width="{w}" height="{h}">{paths}<circle cx="{px:.1f}" cy="{py:.1f}" r="4.5" fill="#0f4c81" stroke="#fff" stroke-width="1.5"/><text x="{px+7:.1f}" y="{py-6:.1f}" font-size="9" fill="#0f172a">组点位</text></svg>
<div class="meta">{r["district"]} · {r["id"]} · 回报率 {r["yield"] if r["yield"] is not None else "—"}</div></div>''')
    OUT_REVIEW.write_text(f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Footprint 金标准抽检（100 组）</title>
<style>body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f3f6fa;margin:0;padding:20px;color:#0b1c33}}
h1{{font-size:20px;margin:0 0 6px}}p{{color:#64748b;font-size:13px;margin:0 0 16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:10px;box-shadow:0 1px 3px rgba(15,23,42,.06)}}
.hd{{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:6px;font-size:12.5px}}
.tag{{color:#fff;font-size:10px;border-radius:999px;padding:1px 7px;white-space:nowrap}}
svg{{display:block;background:#f8fafc;border-radius:6px;width:100%;height:auto}}
.meta{{margin-top:5px;font-size:10.5px;color:#64748b}}</style></head><body>
<h1>Building footprint 金标准抽检（100 组）</h1>
<p>蓝点 = 我们地图组点位；蓝色面 = OSM 匹配到的建筑轮廓。绿=名称匹配 / 橙=点位落在面内 / 红=最近邻≤30m。人工核对：轮廓是否覆盖该组楼栋、是否有误配。</p>
<div class="grid">{"".join(cards)}</div></body></html>''', encoding="utf-8")
    print(f"review page: {OUT_REVIEW.relative_to(ROOT)} ({OUT_REVIEW.stat().st_size//1024}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
