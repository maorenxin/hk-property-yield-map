#!/usr/bin/env python3
"""Build 1-km school-access counts for every building/estate map point.

Sources
- EDB School Location and Information (data.gov.hk), authoritative points for
  kindergartens, primary and secondary schools.
- iGeoCom (Lands Department) CLASS=SCH / TYPE=TEI points for tertiary
  institutions that are not already covered by an OSM university campus.
- HDX / OpenStreetMap education facilities, amenity=university polygons, used
  so a campus edge within 1 km also counts ("touch edge").

Distance is computed in HK1980 Grid metres (EPSG:2326).  A school counts when
its point, or the nearest point of a university polygon boundary/interior, is
within 1,000 m inclusive.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as shp_transform

ROOT = Path(__file__).resolve().parents[1]
EDB = ROOT / "data/raw/education/2026-10-19/SCH_LOC_EDB.json"
IGEO = ROOT / "data/raw/education/2026-10-19/igeocom/iGeoCOM_POI.geojson"
HDX = ROOT / "data/raw/education/2026-10-19/hdx/education_facilities.geojson"
OUT = ROOT / "data/derived/school_access_1km.csv"
NORM = ROOT / "data/normalized/education/schools_2026-10-19.csv"
UNI_GEOJSON = ROOT / "data/derived/universities_campuses_2026-10-19.geojson"
REPORT = ROOT / "reports/school_access.md"
RADIUS_M = 1000.0
DEDUPE_UNI_M = 200.0  # an iGeoCom campus point this close to an OSM university polygon is a duplicate

T2326 = Transformer.from_crs(4326, 2326, always_xy=True)
T4326 = Transformer.from_crs(2326, 4326, always_xy=True)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_edb() -> pd.DataFrame:
    raw = json.loads(EDB.read_text())
    rows = []
    for r in raw:
        cat = str(r.get("中文類別") or "")
        if "特殊" in cat:
            cls = "special"
        elif "幼稚園" in cat:
            cls = "kindergarten"
        elif "小學" in cat:
            cls = "primary"
        elif "中學" in cat:
            cls = "secondary"
        else:
            cls = "other"
        if cls == "other":
            continue
        try:
            lon = float(r.get("LONGITUDE"))
            lat = float(r.get("LATITUDE"))
        except (TypeError, ValueError):
            continue
        if not (113.8 < lon < 114.6 and 22.1 < lat < 22.7):
            continue
        rows.append({
            "school_id": str(r.get("SCHOOL NO.")), "name": r.get("中文名稱") or r.get("ENGLISH NAME"),
            "category": cls, "category_raw": cat, "address": r.get("中文地址") or r.get("ENGLISH ADDRESS"),
            "lon": lon, "lat": lat, "source": "EDB School Location and Information",
        })
    df = pd.DataFrame(rows)
    # EDB publishes multiple rows for the same kindergarten (different rooms/
    # sessions) and combined primary+kindergarten schools.  Deduplicate by
    # normalised name + address, keeping the highest level as the primary
    # category so one physical institution is counted once.
    def _norm(v):
        import re, unicodedata
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", str(v or "")).lower())
    df["_name_key"] = df["name"].map(_norm)
    df["_addr_key"] = df["address"].map(_norm)
    df["_key"] = df["_name_key"] + "|" + df["_addr_key"].where(
        df["_addr_key"] != "",
        df["lon"].round(5).astype(str) + "," + df["lat"].round(5).astype(str))
    df["_priority"] = df["category"].map({"secondary": 0, "primary": 1, "kindergarten": 2, "special": 3}).fillna(9)
    df = (df.sort_values("_priority").drop_duplicates("_key", keep="first")
          .drop(columns=["_name_key", "_addr_key", "_key", "_priority"]))
    print(f"EDB schools: {len(df)} unique after name+address dedupe "
          f"(kindergarten={sum(df.category=='kindergarten')}, primary={sum(df.category=='primary')}, "
          f"secondary={sum(df.category=='secondary')}, special={sum(df.category=='special')})")
    return df


def load_igeo_tertiary() -> pd.DataFrame:
    d = json.loads(IGEO.read_text())
    rows = []
    for f in d.get("features") or []:
        p = f.get("properties") or {}
        if p.get("CLASS") != "SCH" or p.get("TYPE") != "TEI":
            continue
        try:
            e = float(p.get("EASTING")); n = float(p.get("NORTHING"))
            lon, lat = T4326.transform(e, n)
        except (TypeError, ValueError):
            continue
        if not (113.8 < lon < 114.6 and 22.1 < lat < 22.7):
            continue
        rows.append({
            "school_id": f"igeo-{p.get('GEONAMEID')}", "name": p.get("CHINESENAME") or p.get("ENGLISHNAME"),
            "category": "tertiary", "category_raw": "iGeoCom TEI", "address": p.get("C_ADDRESS") or p.get("E_ADDRESS"),
            "lon": lon, "lat": lat, "source": "iGeoCom (Lands Department)",
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["_key"] = df.lon.round(5).astype(str) + "|" + df.lat.round(5).astype(str) + "|" + df.name.fillna("")
    df = df.drop_duplicates("_key").drop(columns="_key")
    print(f"iGeoCom tertiary points: {len(df)}")
    return df


def load_hdx_universities():
    d = json.loads(HDX.read_text())
    feats, geoms = [], []
    for f in d.get("features") or []:
        p = f.get("properties") or {}
        if p.get("amenity") != "university":
            continue
        try:
            g = shape(f["geometry"])
            if not g.is_valid:
                g = g.buffer(0)
            gp = shp_transform(T2326.transform, g)
        except Exception:
            continue
        if gp.is_empty:
            continue
        name = p.get("name_en") or p.get("name") or p.get("id")
        feats.append({
            "type": "Feature", "properties": {"id": p.get("id"), "name": name, "amenity": "university"},
            "geometry": f["geometry"],
        })
        geoms.append((name, gp))
    UNI_GEOJSON.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False))
    print(f"HDX university campus polygons: {len(geoms)}")
    return geoms, feats


def count_points(bx: np.ndarray, by: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    if len(sx) == 0:
        return np.zeros(len(bx), dtype=int)
    # Chunk rows to keep memory bounded.
    out = np.empty(len(bx), dtype=int)
    step = 512
    for i in range(0, len(bx), step):
        dx = bx[i:i+step, None] - sx[None, :]
        dy = by[i:i+step, None] - sy[None, :]
        out[i:i+step] = (dx * dx + dy * dy <= RADIUS_M * RADIUS_M).sum(axis=1)
    return out


def main() -> int:
    edb = load_edb()
    igeo = load_igeo_tertiary()
    uni_geoms, uni_feats = load_hdx_universities()

    # Exclude iGeoCom points already represented by an OSM university polygon.
    if not igeo.empty:
        keep = []
        for r in igeo.itertuples():
            x, y = T2326.transform(r.lon, r.lat)
            p = Point(x, y)
            if any(g.distance(p) <= DEDUPE_UNI_M for _, g in uni_geoms):
                continue
            keep.append(r.Index)
        igeo = igeo.loc[keep].copy()
        print(f"iGeoCom tertiary after university dedupe: {len(igeo)}")

    all_schools = pd.concat([edb, igeo], ignore_index=True)
    NORM.parent.mkdir(parents=True, exist_ok=True)
    all_schools.to_csv(NORM, index=False, encoding="utf-8-sig")
    # Compact asset for the small blue school-dot layer.  Campuses are kept as
    # OSM polygons so the map can show the university footprint as well.
    school_points = [
        {"n": r.name, "c": r.category, "lon": float(r.lon), "lat": float(r.lat),
         "a": r.address, "s": r.source}
        for r in all_schools.itertuples()
    ]
    payload = {"points": school_points, "campuses": uni_feats}
    (ROOT / "site/schools.js").write_text(
        "window.HK_SCHOOLS=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";",
        encoding="utf-8")
    print(f"site schools layer: {len(school_points)} points + {len(uni_feats)} campuses")

    points = []
    for level, path, keycols in [
        ("building", ROOT / "data/derived/building_metrics_12m.csv", ["hma_id", "bldg_grp_id"]),
        ("estate", ROOT / "data/derived/estate_metrics_12m.csv", ["estate_name"]),
    ]:
        d = pd.read_csv(path)
        d = d[d.lon.notna() & d.lat.notna()].copy()
        bx, by = T2326.transform(d.lon.to_numpy(), d.lat.to_numpy())
        # EDB points
        edb_x, edb_y = T2326.transform(edb.lon.to_numpy(), edb.lat.to_numpy())
        counts = {
            "schools_1km": np.zeros(len(d), dtype=int),
            "schools_kindergarten": np.zeros(len(d), dtype=int),
            "schools_primary": np.zeros(len(d), dtype=int),
            "schools_secondary": np.zeros(len(d), dtype=int),
            "schools_special": np.zeros(len(d), dtype=int),
            "schools_university": np.zeros(len(d), dtype=int),
            "schools_college": np.zeros(len(d), dtype=int),
        }
        for cls, col in [("kindergarten", "schools_kindergarten"), ("primary", "schools_primary"),
                         ("secondary", "schools_secondary"), ("special", "schools_special")]:
            sub = edb[edb.category == cls]
            counts[col] = count_points(bx, by, *T2326.transform(sub.lon.to_numpy(), sub.lat.to_numpy()))
        # University polygons: count if the polygon touches/contains the 1-km buffer.
        for i, (x, y) in enumerate(zip(bx, by)):
            p = Point(x, y)
            counts["schools_university"][i] = sum(1 for _, g in uni_geoms if g.distance(p) <= RADIUS_M)
        # Remaining iGeoCom tertiary points.
        if not igeo.empty:
            counts["schools_college"] = count_points(bx, by, *T2326.transform(igeo.lon.to_numpy(), igeo.lat.to_numpy()))
        counts["schools_1km"] = (counts["schools_kindergarten"] + counts["schools_primary"] +
                                 counts["schools_secondary"] + counts["schools_university"] +
                                 counts["schools_college"])
        out = d[["lon", "lat"] + keycols].copy()
        out.insert(0, "level", level)
        for k, v in counts.items():
            out[k] = v
        points.append(out)
        print(f"{level} points: {len(out)}, median schools/1km={np.median(counts['schools_1km']):.1f}, "
              f"max={counts['schools_1km'].max()}")

    result = pd.concat(points, ignore_index=True)
    result.to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(result)} rows)")

    # Source manifest and report.
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "radius_m": RADIUS_M,
        "sources": [
            {"name": "EDB School Location and Information", "url": "https://www.edb.gov.hk/attachment/en/student-parents/sch-info/sch-search/sch-location-info/SCH_LOC_EDB.json",
             "path": str(EDB.relative_to(ROOT)), "bytes": EDB.stat().st_size, "sha256": _sha(EDB)},
            {"name": "iGeoCom", "url": "https://open.hkmapservice.gov.hk/OpenData/directDownload?productName=iGeoCom&sheetName=iGeoCom&productFormat=GEOJSON",
             "path": str(IGEO.relative_to(ROOT)), "bytes": IGEO.stat().st_size, "sha256": _sha(IGEO)},
            {"name": "HDX/OSM education facilities", "url": "https://production-raw-data-api.s3.amazonaws.com/ISO3/HKG/education_facilities/hotosm_hkg_education_facilities_osm_geojson.zip",
             "path": str(HDX.relative_to(ROOT)), "bytes": HDX.stat().st_size, "sha256": _sha(HDX)},
        ],
    }
    (ROOT / "data/raw/education/2026-10-19/manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    lines = [
        "# 学校可达性（1 公里）", "",
        f"- 半径：{int(RADIUS_M)} m，点/校园轮廓距离 ≤ {int(RADIUS_M)} m 即计入（含边界触碰）。",
        "- 幼儿园/小学/中学：教育局《学校位置及资料》，按名称+地址去重（同址多记录合并）；特殊学校单列，不计入总数。",
        "- 大学：HDX / OpenStreetMap `amenity=university` 校园多边形；校园边界或内部距楼盘 ≤1km 即计入。",
        "- 大专院校：iGeoCom `CLASS=SCH, TYPE=TEI` 点；已落在 OSM 大学 campus 200m 内的重复点剔除。",
        f"- 学校点：{len(edb)}（幼 {sum(edb.category=='kindergarten')} / 小 {sum(edb.category=='primary')} / 中 {sum(edb.category=='secondary')} / 特殊 {sum(edb.category=='special')}）。",
        f"- 大学 campus：{len(uni_geoms)}；独立大专点：{len(igeo)}。",
        "", f"输出：`{OUT.relative_to(ROOT)}`；规范化学校表：`{NORM.relative_to(ROOT)}`。", "",
    ]
    for name in ["翰林峰", "太古城", "港島南岸", "海茵莊園"]:
        r = result[(result.level == "estate") & (result.estate_name == name)]
        if len(r):
            x = r.iloc[0]
            lines.append(f"- {name}：1km 内 {int(x.schools_1km)} 所（幼 {int(x.schools_kindergarten)} / 小 {int(x.schools_primary)} / 中 {int(x.schools_secondary)} / 大学/大专 {int(x.schools_university)+int(x.schools_college)}）")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
