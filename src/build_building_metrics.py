#!/usr/bin/env python3
"""Aggregate normalized Centaline transactions into building-level metrics.

Outputs (all rolling 12-month window, plus full-window sample counts):
- data/derived/building_metrics_12m.csv     per 樓宇組 (building group)
- data/derived/building_map_points.geojson  building points for the map
- data/derived/building_deals_recent.json   recent sale/rent deal previews per building
- data/derived/building_history_full.json   full-history rolling-3m medians per building group
- data/derived/estate_metrics_12m.csv       per estate (for fallback/附表)
- data/derived/coordinate_harvest_cache.json  autocomplete estate centres
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = os.getenv("HK_SNAPSHOT", date.today().isoformat())
NORM_LATEST = ROOT / "data/normalized/centaline/transactions_full_latest.parquet"
NORM_DATED = ROOT / "data/normalized/centaline/transactions_full_2026-10-18.parquet"
NORM_FULL = NORM_LATEST if NORM_LATEST.exists() else NORM_DATED
NORM = NORM_FULL if NORM_FULL.exists() else ROOT / f"data/normalized/centaline/transactions_{SNAPSHOT}.parquet"
DERIVED = ROOT / "data/derived"
HMA_CACHE = ROOT / "data/raw/centaline/hma_cache"
AUTO_CACHE = DERIVED / "coordinate_harvest_cache.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36")
H1980 = Transformer.from_crs(2326, 4326, always_xy=True)

WINDOW_START = pd.Timestamp(SNAPSHOT) - pd.Timedelta(days=365)
MIN_N = 1          # minimum transactions per side to publish a yield (user requested 1/1)


def load_grid_coords() -> dict:
    coords = {}
    for p in sorted(HMA_CACHE.glob("HMA*.html")):
        s = p.read_text(errors="ignore")
        m = re.search(r"var grid =(\[.*?\]);\s*var", s, re.S) or \
            re.search(r"var grid =(\[.*?\]);", s, re.S)
        if not m:
            continue
        try:
            grid = json.loads(m.group(1))
        except Exception:
            continue
        hma = p.stem
        for row in grid:
            code, x, y = row.get("Code"), row.get("X"), row.get("Y")
            if code and x not in (None, "", "0") and y not in (None, "", "0"):
                try:
                    lon, lat = H1980.transform(float(x), float(y))
                    if 113 < lon < 115 and 21 < lat < 23:
                        coords[(hma, code)] = (round(lon, 6), round(lat, 6),
                                               row.get("Name") or "")
                except Exception:
                    pass
    return coords


def harvest_estate_centres(estates: list[tuple[str, str]]) -> dict:
    """estates: [(type_code, estate_name)] -> {type_code: {lon, lat, name}}"""
    cache = json.loads(AUTO_CACHE.read_text()) if AUTO_CACHE.exists() else {}
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json", "User-Agent": UA,
                         "Origin": "https://hk.centanet.com",
                         "Referer": "https://hk.centanet.com/"})
    todo = [(c, n) for c, n in estates if c and c not in cache]
    print(f"estate centres: {len(cache)} cached, {len(todo)} to fetch")
    for i, (code, name) in enumerate(todo):
        try:
            r = sess.post("https://hk-api.centanet.com/centanetapi/api/"
                          "Transaction/GetTransactionAutoComplete",
                          params={"resultSet": "WithNewProp"},
                          json={"keyword": name}, timeout=30)
            time.sleep(0.5)
            data = r.json()
        except Exception as e:  # noqa: BLE001
            print("  autocomplete failed:", name, e)
            time.sleep(2)
            continue
        hit = None
        for group in ("estate", "phase", "building"):
            for x in data.get(group) or []:
                sx = x.get("search") or {}
                big = (sx.get("bigestAndEstate") or [None])[0]
                if x.get("value") == code or big == code:
                    hit = x
                    break
            if hit:
                break
        if hit and hit.get("center"):
            cache[code] = {"lon": hit["center"].get("longitude"),
                           "lat": hit["center"].get("latitude"),
                           "label": hit.get("label")}
        else:
            cache[code] = None
        if (i + 1) % 25 == 0 or i + 1 == len(todo):
            AUTO_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
            print(f"  {i+1}/{len(todo)} estates resolved")
    AUTO_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    return cache


def agg_metrics(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    sale = df[(df.listing_type == "sale") & df.saleable_psf.notna()]
    rent = df[(df.listing_type == "rent") & df.saleable_psf.notna()]
    s = (sale.groupby(keys, dropna=False)
         .agg(sale_psf=("saleable_psf", "median"), sale_n=("saleable_psf", "size"),
              sale_psf_p25=("saleable_psf", lambda x: x.quantile(0.25)),
              sale_psf_p75=("saleable_psf", lambda x: x.quantile(0.75)),
              sale_price_median=("sale_price_hkd", "median"),
              sale_area_median=("saleable_area_sqft", "median"))
         .reset_index())
    r = (rent.groupby(keys, dropna=False)
         .agg(rent_psf=("saleable_psf", "median"), rent_n=("saleable_psf", "size"),
              rent_psf_p25=("saleable_psf", lambda x: x.quantile(0.25)),
              rent_psf_p75=("saleable_psf", lambda x: x.quantile(0.75)),
              rent_monthly_median=("rent_monthly_hkd", "median"))
         .reset_index())
    m = s.merge(r, on=keys, how="outer")
    m["gross_yield_pct"] = m.rent_psf * 12 / m.sale_psf * 100
    m.loc[(m.sale_n < MIN_N) | (m.rent_n < MIN_N), "gross_yield_pct"] = None
    m["quality"] = [
        "building" if (sn or 0) >= MIN_N and (rn or 0) >= MIN_N else "low_sample"
        for sn, rn in zip(m.sale_n.fillna(0), m.rent_n.fillna(0))]
    return m


SCHOOL_COLS = ["schools_1km", "schools_kindergarten", "schools_primary",
               "schools_secondary", "schools_special", "schools_university", "schools_college"]


def merge_school_access(df: pd.DataFrame, level: str, keys: list[str]) -> pd.DataFrame:
    p = DERIVED / "school_access_1km.csv"
    if not p.exists():
        raise FileNotFoundError(
            f"{p} missing; run `python3 src/build_school_access.py` before building metrics")
    sa = pd.read_csv(p)
    sub = sa[sa.level.eq(level)][keys + SCHOOL_COLS].copy()
    out = df.merge(sub, on=keys, how="left")
    for c in SCHOOL_COLS:
        out[c] = out[c].fillna(0).astype(int)
    return out


def deals_preview(df: pd.DataFrame, key: str = "bldg_grp_id") -> dict:
    """Most recent 60 sale + 60 rent deals per group, with source links."""
    out = {}
    sub = df[df[key].notna() & df.saleable_psf.notna()]
    if key == "estate_name":
        sub = sub[sub[key].astype(str).str.strip().ne("")]
    for bid, g in sub.groupby(key):
        entry = {}
        for side in ("sale", "rent"):
            gs = (g[g.listing_type == side]
                  .sort_values("analysis_date", ascending=False).head(60))
            entry[side] = [{
                "d": str(x.transaction_date.date()) if pd.notna(x.transaction_date) else None,
                "dd": str(getattr(x, "deal_date", None).date()) if pd.notna(getattr(x, "deal_date", None)) else None,
                "id": getattr(x, "source_record_id", None),
                "src": x.data_source or "centaline",
                "b": x.building_name, "f": x.floor, "u": x.unit,
                "p": int(x.price_hkd) if pd.notna(x.price_hkd) else None,
                "a": int(x.saleable_area_sqft) if pd.notna(x.saleable_area_sqft) else None,
                "psf": round(float(x.saleable_psf), 1) if pd.notna(x.saleable_psf) else None,
                "url": x.source_url,
            } for x in gs.itertuples()]
        out[bid] = entry
    return out


def history_preview(df: pd.DataFrame, key: str = "bldg_grp_id") -> dict:
    """Trailing-3-month rolling medians per group across the full corpus window.

    Month t covers deals in [t-2 months, t]; a side is published for a month only when
    it has >= MIN_N deals inside that window (0 = not published). Yield is derived by
    the client as rent_psf * 12 / sale_psf * 100 when both sides are published.
    """
    sub = df[df[key].notna() & df.saleable_psf.notna()].copy()
    if key == "estate_name":
        sub = sub[sub[key].astype(str).str.strip().ne("")]
    sub["analysis_date"] = pd.to_datetime(sub["analysis_date"])
    months = pd.period_range(sub["analysis_date"].min().to_period("M"),
                             sub["analysis_date"].max().to_period("M"), freq="M")
    ids = sorted(sub[key].dropna().unique())
    n = len(months)
    data = {bid: {"s": [0] * n, "sn": [0] * n, "r": [0] * n, "rn": [0] * n} for bid in ids}
    pos = {m: i for i, m in enumerate(months)}
    for bid, g in sub.groupby(key):
        g = g[g.analysis_date.notna()]
        if g.empty:
            continue
        lo = pos.get(g.analysis_date.min().to_period("M"), 0)
        hi = pos.get(g.analysis_date.max().to_period("M"), n - 1)
        e = data.get(bid)
        for side, key, nkey in (("sale", "s", "sn"), ("rent", "r", "rn")):
            gg = g[g.listing_type == side].sort_values("analysis_date")
            if gg.empty:
                continue
            dates = gg.analysis_date.to_numpy(dtype="datetime64[D]")
            vals = gg.saleable_psf.to_numpy(dtype=float)
            for i in range(lo, hi + 1):
                m = months[i]
                ws = np.datetime64((m.start_time - pd.DateOffset(months=2)).date())
                we = np.datetime64(m.end_time.date())
                left = np.searchsorted(dates, ws, side="left")
                right = np.searchsorted(dates, we, side="right")
                if right - left < MIN_N:
                    continue
                mid = float(np.median(vals[left:right]))
                if side == "sale":
                    e[key][i] = int(round(mid))
                else:
                    e[key][i] = round(mid, 1)
                e[nkey][i] = int(right - left)
    return {
        "rule": "全历史近 3 个月滚动中位；售/租各 ≥1 宗才发布（0 = 未发布）",
        "window": {"start": str(months[0]), "end": str(months[-1]), "months": n},
        "months": [str(m) for m in months],
        "buildings": data,
    }


def history_by_building(df: pd.DataFrame, mixed_ids: set[str]) -> dict:
    """Per-building monthly rolling-3m medians for mixed-age groups + group series
    aggregated as the median across buildings that publish both sides that month."""
    sub = df[df.bldg_grp_id.notna() & df.hma_id.notna() & df.saleable_psf.notna()].copy()
    sub["bid"] = sub.hma_id.astype(str) + ":" + sub.bldg_grp_id.astype(str)
    sub = sub[sub.bid.isin(mixed_ids)]
    if sub.empty:
        return {"months": [], "groups": {}, "buildings": {}}
    sub["bkey"] = sub.building_name.fillna("") + "|" + sub.address_raw.fillna("")
    months = pd.period_range(df.analysis_date.min().to_period("M"),
                             df.analysis_date.max().to_period("M"), freq="M")
    n = len(months)
    keys = sub[["bid", "bkey"]].drop_duplicates().itertuples(index=False)
    series = {(bid, bkey): {"s": [0] * n, "r": [0] * n} for bid, bkey in keys}
    pos = {m: i for i, m in enumerate(months)}
    for (bid, bkey), g in sub.groupby(["bid", "bkey"]):
        g = g[g.analysis_date.notna()]
        if g.empty:
            continue
        lo = pos.get(g.analysis_date.min().to_period("M"), 0)
        hi = pos.get(g.analysis_date.max().to_period("M"), n - 1)
        e = series.get((bid, bkey))
        for side in ("sale", "rent"):
            gg = g[g.listing_type == side].sort_values("analysis_date")
            if gg.empty:
                continue
            dates = gg.analysis_date.to_numpy(dtype="datetime64[D]")
            vals = gg.saleable_psf.to_numpy(dtype=float)
            for i in range(lo, hi + 1):
                m = months[i]
                ws = np.datetime64((m.start_time - pd.DateOffset(months=2)).date())
                we = np.datetime64(m.end_time.date())
                left = np.searchsorted(dates, ws, side="left")
                right = np.searchsorted(dates, we, side="right")
                if right - left < MIN_N:
                    continue
                e["s" if side == "sale" else "r"][i] = round(float(np.median(vals[left:right])), 1)
    per_month = {}
    for (bid, bkey), ser in series.items():
        per_month.setdefault(bid, [[] for _ in range(n)])
        for i in range(n):
            if ser["s"][i] > 0 and ser["r"][i] > 0:
                per_month[bid][i].append(ser["r"][i] * 12 / ser["s"][i] * 100)
    groups, buildings = {}, {}
    for bid, per in per_month.items():
        vals = [round(pd.Series(v).median(), 2) if v else None for v in per]
        cnt = [len(v) for v in per]
        groups[bid] = {"y": vals, "n": cnt}
    for (bid, bkey), ser in series.items():
        idx = [i for i, (sv, rv) in enumerate(zip(ser["s"], ser["r"])) if sv > 0 or rv > 0]
        buildings.setdefault(bid, {})[bkey] = {
            "m": idx,
            "s": [ser["s"][i] for i in idx],
            "r": [ser["r"][i] for i in idx],
        }
    return {"months": [str(m) for m in months], "groups": groups, "buildings": buildings}


def deals_by_building(df: pd.DataFrame, need: set[tuple[str, str]], n: int = 100) -> dict:
    """Most recent n sale + n rent deals per building, only for requested groups."""
    sub = df[df.bldg_grp_id.notna() & df.hma_id.notna() & df.saleable_psf.notna()].copy()
    mask = list(zip(sub.hma_id.astype(str), sub.bldg_grp_id.astype(str)))
    sub = sub[[m in need for m in mask]]
    out = {}
    for (h, g, bn, ad), grp in sub.groupby(
            ["hma_id", "bldg_grp_id", "building_name", "address_raw"], dropna=False):
        bid = f"{h}:{g}"
        bkey = f"{bn if pd.notna(bn) else ''}|{ad if pd.notna(ad) else ''}"
        entry = {}
        for side in ("sale", "rent"):
            gs = grp[grp.listing_type == side].sort_values("analysis_date", ascending=False).head(n)
            rows = [{
                "d": str(x.transaction_date.date()) if pd.notna(x.transaction_date) else None,
                "dd": str(getattr(x, "deal_date", None).date()) if pd.notna(getattr(x, "deal_date", None)) else None,
                "id": getattr(x, "source_record_id", None),
                "src": x.data_source or "centaline",
                "b": x.building_name, "f": x.floor, "u": x.unit,
                "p": int(x.price_hkd) if pd.notna(x.price_hkd) else None,
                "a": int(x.saleable_area_sqft) if pd.notna(x.saleable_area_sqft) else None,
                "psf": round(float(x.saleable_psf), 1) if pd.notna(x.saleable_psf) else None,
                "url": x.source_url,
            } for x in gs.itertuples()]
            if rows:
                entry[side] = rows
        if entry:
            out.setdefault(bid, {})[bkey] = entry
    return out


def unit_token(floor, unit, building) -> str:
    text = re.sub(r"\s+", "", str(building or "") + " " + str(floor or "") + " " + str(unit or ""))
    # 28Hse stores the whole display string in `unit`; Midland stores
    # floor and flat separately.  Normalise both to e.g. 高層F.
    m = re.search(r"(高層|中層|低層|地下|頂層|上層|下層|G/F|UG/F|LG/F)([A-Za-z0-9]+)室?", text, re.I)
    if m:
        return (m.group(1) + m.group(2)).lower()
    f = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(floor or "").lower())
    u = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(unit or "").lower())
    if u:
        return (f + "|" + u) if f else u
    return ""


def add_dedupe_keys(x: pd.DataFrame) -> pd.DataFrame:
    text = (x["building_name"].fillna("").astype(str) + " " +
            x["floor"].fillna("").astype(str) + " " +
            x["unit"].fillna("").astype(str)).str.replace(r"\s+", "", regex=True)
    m = text.str.extract(r"(高層|中層|低層|地下|頂層|上層|下層|G/F|UG/F|LG/F)([A-Za-z0-9]+)室?", flags=re.I)
    token = (m[0].str.lower() + m[1].str.lower()).where(m[0].notna(), "")
    f = x["floor"].fillna("").astype(str).str.lower().str.replace(r"[^0-9a-z\u4e00-\u9fff]+", "", regex=True)
    u = x["unit"].fillna("").astype(str).str.lower().str.replace(r"[^0-9a-z\u4e00-\u9fff]+", "", regex=True)
    fallback = (f + "|" + u).where((f != "") & (u != ""), u)
    token = token.where(token != "", fallback)
    x["unit_token_tmp"] = token.fillna("")
    date_s = pd.to_datetime(x["analysis_date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    key = (x["hma_id"].fillna("").astype(str) + "|" + x["bldg_grp_id"].fillna("").astype(str) + "|" +
           date_s + "|" + x["listing_type"].fillna("").astype(str) + "|" +
           x["price_hkd"].fillna("").astype(str) + "|" + x["saleable_area_sqft"].fillna("").astype(str) + "|" +
           x["unit_token_tmp"])
    cond = (x["unit_token_tmp"] != "") & (date_s != "") & x["price_hkd"].notna()
    x["dedupe_key_tmp"] = key.where(cond, "")
    return x


SYNTHETIC_ESTATES = {"日出康城": "HMA114:SYN-LOHAS", "海茵莊園": "HMA114:SYN-HAIYIN"}


def assign_synthetic_groups(df: pd.DataFrame) -> pd.DataFrame:
    """Give selected null-bldgGrp records a stable synthetic group.

    Some new phases/estates are returned by the public API without any
    bldgGrpId.  Dropping them makes the map look far emptier than the source.
    We keep them as clearly-labelled synthetic groups at the estate centre.
    """
    df = df.copy()
    for estate, gid in SYNTHETIC_ESTATES.items():
        mask = df.estate_name.astype(str).eq(estate) & df.bldg_grp_id.isna()
        if not mask.any():
            continue
        df.loc[mask, "hma_id"] = gid.split(":", 1)[0]
        df.loc[mask, "bldg_grp_id"] = gid.split(":", 1)[1]
        if estate == "海茵莊園":
            df.loc[mask, "bldg_grp_name"] = "海茵莊園 (合成屋苑组)"
        else:
            df.loc[mask, "bldg_grp_name"] = "日出康城 - 未匹配新期/座 (合成组)"
        print(f"synthetic group {estate}: {int(mask.sum())} records -> {gid}")
    return df


def main() -> int:
    df = pd.read_parquet(NORM)
    df = assign_synthetic_groups(df)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["analysis_date"] = pd.to_datetime(df.get("analysis_date", df["transaction_date"]))
    # Sale sources: Centaline returns both Land Registry and AC rows.  The
    # same transaction can use different floor text ("中層" vs "25樓"), so the
    # floor-free unit key is the safe exact identity here.  Only drop an AC
    # copy when a Land row has the same group/day/price/area/unit and a
    # non-empty unit token.
    unit_norm = (df["unit"].fillna("").astype(str).str.lower()
                 .str.replace(r"[^0-9a-z\u4e00-\u9fff]+", "", regex=True))
    date_norm = pd.to_datetime(df["analysis_date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    sale_key = (df["hma_id"].fillna("").astype(str) + "|" + df["bldg_grp_id"].fillna("").astype(str)
                + "|" + date_norm + "|" + df["price_hkd"].fillna("").astype(str) + "|"
                + df["saleable_area_sqft"].fillna("").astype(str) + "|" + unit_norm)
    land_sale_keys = set(sale_key[(df.listing_type == "sale") & (df.data_source == "Land") & (unit_norm != "")])
    sale_drop = ((df.listing_type == "sale") & (df.data_source == "AC") & (unit_norm != "")
                 & sale_key.isin(land_sale_keys))
    n_sale_drop = int(sale_drop.sum())
    df = df.loc[~sale_drop].copy()
    print(f"sale cross-source duplicate AC rows dropped: {n_sale_drop}")
    # ---- supplemental rental sources (28Hse rent deals / Midland rent deals) ----
    def append_supplement(path: Path, source_name: str, base_df: pd.DataFrame) -> pd.DataFrame:
        if not path.exists():
            return base_df
        x = pd.read_csv(path)
        if "group_id" not in x.columns:
            return base_df
        x = x[x.group_id.notna()].copy()
        if x.empty:
            return base_df
        gid = x.group_id.astype(str).str.split(":", n=1, expand=True)
        x["hma_id"], x["bldg_grp_id"] = gid[0], gid[1]
        x["source"] = source_name
        x["source_record_id"] = x.get("source_record_id", x.get("source_url"))
        x["source_url"] = x.get("source_url")
        x["transaction_date"] = pd.to_datetime(x["transaction_date"], errors="coerce")
        x["deal_date"] = pd.to_datetime(x.get("deal_date", x["transaction_date"]), errors="coerce")
        x["analysis_date"] = x["deal_date"].fillna(x["transaction_date"])
        x["listing_type"] = "rent"
        x["transaction_type"] = "transaction"
        x["data_source"] = source_name.upper()
        if "building_name" in x.columns:
            x["building_name"] = x["building_name"]
        elif source_name == "28hse":
            # 28Hse's `unit` is the full display string, not a building name.
            # Keeping it verbatim would create one fake "building" per deal and
            # explode the mixed-group per-building breakdown. Extract a tower
            # token when present; otherwise leave blank.
            x["building_name"] = (x["unit"].astype(str)
                                  .str.extract(r"(\d+[A-Za-z]?\s*座)")[0])
        else:
            x["building_name"] = x.get("unit")
        x["floor"] = x.get("floor")
        x["unit"] = x.get("unit")
        x["address_raw"] = x.get("address_raw")
        x["rent_monthly_hkd"] = x.get("rent_hkd", x.get("rent_monthly_hkd"))
        x["price_hkd"] = x.get("rent_hkd", x.get("rent_monthly_hkd"))
        x["saleable_area_sqft"] = x.get("area_sqft", x.get("saleable_area_sqft"))
        x["saleable_psf"] = x.get("psf", x.get("saleable_psf"))
        x["estate_name"] = x.get("estate_name")
        x["phase_name"] = x.get("phase_name")
        keep = ["source", "source_record_id", "source_url", "transaction_date", "deal_date",
                "analysis_date", "listing_type", "transaction_type", "data_source",
                "hma_id", "bldg_grp_id", "estate_name", "phase_name", "building_name",
                "floor", "unit", "address_raw", "rent_monthly_hkd", "price_hkd",
                "saleable_area_sqft", "saleable_psf"]
        for c in keep:
            if c not in x.columns:
                x[c] = None
        return pd.concat([base_df, x[keep]], ignore_index=True)

    # Add supplements without ever dropping legitimate primary records.  Some
    # buildings have multiple same-day/same-price deals in different units.
    # Cross-source duplicates are removed only when the floor/unit token is
    # recoverable; records without a unit token are deliberately retained.
    base = df.copy()
    base = add_dedupe_keys(base)
    base_keys = set(base.loc[base.dedupe_key_tmp != "", "dedupe_key_tmp"])
    base = base.drop(columns=["unit_token_tmp", "dedupe_key_tmp"])
    supp = append_supplement(DERIVED / "28hse_rent_history.csv", "28hse", base)
    supp = append_supplement(DERIVED / "midland_rentals_2026-10-18.csv", "midland", supp)
    extra = supp[supp.source != "centaline"].copy()
    extra = add_dedupe_keys(extra)
    priority = {"28hse": 1, "midland": 2}
    extra["priority_tmp"] = extra["source"].map(priority).fillna(9)
    seen_sources = {key: {"centaline"} for key in base_keys}
    keep_rows = []
    for r in extra.sort_values(["priority_tmp", "analysis_date", "source_record_id"]).itertuples():
        key = r.dedupe_key_tmp
        if key and any(src != r.source for src in seen_sources.get(key, set())):
            continue
        keep_rows.append(r.Index)
        if key:
            seen_sources.setdefault(key, set()).add(r.source)
    extra = extra.loc[keep_rows].drop(columns=["unit_token_tmp", "dedupe_key_tmp", "priority_tmp"])
    df = pd.concat([base, extra], ignore_index=True).sort_values(["analysis_date", "source_record_id"]).reset_index(drop=True)
    full = df.copy()
    win = df[df.analysis_date >= WINDOW_START].copy()
    print(f"records: full {len(full):,}, rolling-12m {len(win):,}")

    coords = load_grid_coords()
    print("grid coords:", len(coords))

    # ---- building level ----
    b = win[win.bldg_grp_id.notna() & win.hma_id.notna()].copy()
    bm = agg_metrics(b, ["hma_id", "bldg_grp_id"])
    bm["lon"], bm["lat"], bm["grid_name"] = None, None, None
    for i, row in bm.iterrows():
        c = coords.get((row.hma_id, row.bldg_grp_id))
        if c:
            bm.at[i, "lon"], bm.at[i, "lat"], bm.at[i, "grid_name"] = c
    # fill estate name / district from the most common values
    meta = (b.groupby(["hma_id", "bldg_grp_id"], dropna=False)
            .agg(estate_name=("estate_name", "first"), hma=("hma", "first"),
                 district=("district", "first"), region=("region_terr", "first"),
                 bldg_grp_name=("bldg_grp_name", "first")).reset_index())
    bm = bm.merge(meta, on=["hma_id", "bldg_grp_id"], how="left")
    print(f"building groups: {len(bm)}")

    # ---- building age: 先按「栋」取入伙年份中位，再在楼宇组内跨栋取中位 ----
    # 街区型楼宇组常把多栋不同年代的楼放在一组（如 1959 年老楼 + 2024 新楼），
    # 若按记录数取中位，会被成交量大的一栋绑架；因此每栋只投一票。
    oy = full[full.bldg_grp_id.notna() & full.hma_id.notna()].copy()
    oy["op_year_num"] = pd.to_numeric(
        oy["op_year"].astype(str).str.extract(r"(\d{4})")[0], errors="coerce")
    oy = oy[oy.op_year_num.between(1900, 2035)]
    oy = oy[~oy["building_name"].astype(str).str.contains("車位", na=False)]
    per_bldg = (oy.groupby(["hma_id", "bldg_grp_id", "building_name", "address_raw"], dropna=False)
                ["op_year_num"].median().round().reset_index())
    # 外部校核优先：RVD《樓宇名稱》落成年份（仅采用 名称+地址 双匹配的高置信记录）
    rvd_path = DERIVED / "building_age_rvd.csv"
    if rvd_path.exists():
        rvd = pd.read_csv(rvd_path)
        rvd = rvd[rvd.match.isin(["name+addr", "estate+phase", "name"])][
            ["hma_id", "bldg_grp_id", "building_name", "address_raw", "op_year_rvd"]]
        per_bldg = per_bldg.merge(
            rvd, on=["hma_id", "bldg_grp_id", "building_name", "address_raw"], how="left")
    else:
        per_bldg["op_year_rvd"] = None
    # 第三来源：28Hse 屋苑页（仅用于 RVD 未覆盖的楼栋）
    hse_path = DERIVED / "building_age_28hse.csv"
    if hse_path.exists():
        hse = pd.read_csv(hse_path)[
            ["hma_id", "bldg_grp_id", "building_name", "address_raw", "op_year_28hse"]]
        per_bldg = per_bldg.merge(
            hse, on=["hma_id", "bldg_grp_id", "building_name", "address_raw"], how="left")
    else:
        per_bldg["op_year_28hse"] = None
    per_bldg["year_final"] = (per_bldg["op_year_rvd"]
                              .fillna(per_bldg["op_year_28hse"])
                              .fillna(per_bldg["op_year_num"]))
    age = (per_bldg.groupby(["hma_id", "bldg_grp_id"])
           .agg(op_year=("year_final", "median"),
                op_year_min=("year_final", "min"),
                op_year_max=("year_final", "max"),
                age_buildings=("year_final", "size"),
                age_rvd_buildings=("op_year_rvd", "count"),
                age_28hse_buildings=("op_year_28hse", "count")).reset_index())
    for c in ("op_year", "op_year_min", "op_year_max"):
        age[c] = age[c].round().astype("Int64")
    bm = bm.merge(age, on=["hma_id", "bldg_grp_id"], how="left")
    as_of_year = pd.Timestamp(SNAPSHOT).year
    bm["age_years"] = (as_of_year - bm["op_year"]).astype("Int64")
    bm["age_mixed"] = (bm["op_year_max"] - bm["op_year_min"]) > 10
    n_age = int(bm.op_year.notna().sum())
    n_mixed = int(bm.age_mixed.fillna(False).sum())
    n_rvd = int((bm.age_rvd_buildings.fillna(0) > 0).sum())
    n_hse = int((bm.age_28hse_buildings.fillna(0) > 0).sum())
    print(f"building age: {n_age} / {len(bm)} groups, {n_mixed} mixed-age groups, "
          f"{n_rvd} groups use RVD years, {n_hse} groups add 28Hse years, "
          f"overall median {bm.op_year.median()}")

    # ---- per-building metrics (12m) + mixed-group yield stratified by building ----
    bldg_key = ["hma_id", "bldg_grp_id", "building_name", "address_raw"]
    bm_bldg = agg_metrics(b, bldg_key)
    bm_bldg = bm_bldg.merge(
        per_bldg[["hma_id", "bldg_grp_id", "building_name", "address_raw",
                  "year_final", "op_year_rvd", "op_year_28hse"]], on=bldg_key, how="left")
    fc = (full[full.bldg_grp_id.notna()]
          .groupby(bldg_key + ["listing_type"]).size().unstack(fill_value=0).reset_index())
    fc = fc.rename(columns={"sale": "sale_n_full", "rent": "rent_n_full"})
    bm_bldg = bm_bldg.merge(fc, on=bldg_key, how="left")
    bm_bldg["age_years"] = (as_of_year - bm_bldg["year_final"])
    bm_bldg.to_csv(DERIVED / "building_metrics_by_building_12m.csv", index=False)
    yb = (bm_bldg[bm_bldg.gross_yield_pct.notna()]
          .groupby(["hma_id", "bldg_grp_id"])["gross_yield_pct"]
          .agg(yield_bldg_median="median", yield_bldg_n="size",
               yield_bldg_min="min", yield_bldg_max="max").reset_index())
    bm = bm.merge(yb, on=["hma_id", "bldg_grp_id"], how="left")
    mixed = bm["age_mixed"].fillna(False)
    bm["yield_basis"] = "group"
    bm.loc[mixed & bm.yield_bldg_n.notna(), "yield_basis"] = "building"
    bm.loc[mixed & bm.yield_bldg_n.isna(), "yield_basis"] = "none"
    bm["yield_map_pct"] = bm["gross_yield_pct"]
    bm.loc[bm.yield_basis.eq("building"), "yield_map_pct"] = bm.yield_bldg_median
    bm.loc[bm.yield_basis.eq("none"), "yield_map_pct"] = None
    nb = int((bm.yield_basis == "building").sum())
    nn = int((bm.yield_basis == "none").sum())
    print(f"yield basis: group {int((bm.yield_basis=='group').sum())}, building {nb}, none(mixed, no per-building pair) {nn}")

    # ---- estate level ----
    e = win[win.estate_name.notna()].copy()
    em = agg_metrics(e, ["estate_name"])
    estate_codes = (e.groupby("estate_name")["type_code"]
                    .agg(lambda s: s.dropna().mode().iloc[0] if len(s.dropna()) else None)
                    .to_dict())
    em["type_code"] = em.estate_name.map(estate_codes)
    em["district"] = em.estate_name.map(
        e.groupby("estate_name")["district"].agg(lambda s: s.mode().iloc[0] if s.notna().any() else None))
    em["region"] = em.estate_name.map(
        e.groupby("estate_name")["region_terr"].agg(lambda s: s.mode().iloc[0] if s.notna().any() else None))
    # Estate-level age fallback, including null-group new estates: median across
    # distinct buildings (one vote per building), then across buildings in the estate.
    oy_estate = full[full.estate_name.notna()].copy()
    oy_estate["op_year_num"] = pd.to_numeric(
        oy_estate["op_year"].astype(str).str.extract(r"(\d{4})")[0], errors="coerce")
    oy_estate = oy_estate[oy_estate.op_year_num.between(1900, 2035)]
    oy_estate = oy_estate[~oy_estate["building_name"].astype(str).str.contains("車位", na=False)]
    per_bldg_estate = (oy_estate.groupby(["estate_name", "building_name", "address_raw"], dropna=False)
                       ["op_year_num"].median().round().reset_index())
    estate_age = (per_bldg_estate.groupby("estate_name")
                  .agg(op_year=("op_year_num", "median"),
                       op_year_min=("op_year_num", "min"),
                       op_year_max=("op_year_num", "max"),
                       age_buildings=("op_year_num", "size")).reset_index())
    em = em.merge(estate_age, on="estate_name", how="left")
    em["age_years"] = (as_of_year - em["op_year"]).round().astype("Int64")
    em["age_mixed"] = (em["op_year_max"] - em["op_year_min"]) > 10
    estate_names_with_groups = set(bm.estate_name.dropna().astype(str))
    em["has_building_group"] = em.estate_name.astype(str).isin(estate_names_with_groups)
    em.to_csv(DERIVED / "estate_metrics_12m.csv", index=False)

    tops = (e.groupby("estate_name").size().sort_values(ascending=False).head(400))
    estates_for_geo = [(estate_codes.get(n), n) for n in tops.index if estate_codes.get(n)]
    centres = harvest_estate_centres(estates_for_geo)
    em["lon"] = em.type_code.map(lambda c: (centres.get(c) or {}).get("lon") if c else None)
    em["lat"] = em.type_code.map(lambda c: (centres.get(c) or {}).get("lat") if c else None)
    em = merge_school_access(em, "estate", ["estate_name"])
    em.to_csv(DERIVED / "estate_metrics_12m.csv", index=False)
    print("estates with centres:", em.lon.notna().sum(), "of", len(em))

    # ---- district level: median of building-internal yields (same pairing as map) ----
    by = bm[(bm.gross_yield_pct.notna()) & bm.district.notna()].copy()
    dm = (by.groupby("district")
          .agg(gross_yield_pct=("gross_yield_pct", "median"),
               buildings_used=("gross_yield_pct", "size"),
               rent_monthly_hkd_sqft=("rent_psf", "median"),
               sale_price_hkd_sqft=("sale_psf", "median"),
               sale_n=("sale_n", "sum"), rent_n=("rent_n", "sum"))
          .reset_index())
    dm["price_to_rent_years"] = 100 / dm.gross_yield_pct
    dm["period_start"] = str(WINDOW_START.date())
    dm["period_end"] = str(max(win.analysis_date).date()) if len(win) else None
    dm.to_csv(DERIVED / "district_metrics_12m.csv", index=False)
    print(f"districts: {len(dm)}, with yield: {dm.gross_yield_pct.notna().sum()}")
    print(dm[["district", "gross_yield_pct", "buildings_used"]].to_string())

    # ---- estate-centre fallback for building groups without grid coordinates ----
    em_lonlat = {r.estate_name: (r.lon, r.lat) for r in em.itertuples()
                 if pd.notna(getattr(r, "lon", None))}
    import hashlib as _hl
    import math as _math

    def estate_fallback(row):
        c = em_lonlat.get(row.estate_name)
        if not c:
            return None
        h = int(_hl.md5(f"{row.hma_id}:{row.bldg_grp_id}".encode()).hexdigest()[:8], 16)
        ang = (h % 360) * _math.pi / 180
        r_m = 30 + (h % 60)
        dlat = r_m * _math.sin(ang) / 111000
        dlon = r_m * _math.cos(ang) / (101000 * _math.cos(_math.radians(c[1])))
        return (round(c[0] + dlon, 6), round(c[1] + dlat, 6))

    bm["geo"] = None
    bm.loc[bm.lon.notna(), "geo"] = "grid"
    n_fb = 0
    for i, row in bm[bm.lon.isna()].iterrows():
        fb = estate_fallback(row)
        if fb:
            bm.at[i, "lon"], bm.at[i, "lat"] = fb
            bm.at[i, "geo"] = "synthetic" if str(row.bldg_grp_id).startswith("SYN-") else "estate"
            n_fb += 1
    print(f"estate-centre fallback coords added: {n_fb}")
    bm = merge_school_access(bm, "building", ["hma_id", "bldg_grp_id"])
    with_coords = bm[bm.lon.notna()]
    with_grid = bm[bm.geo.eq("grid")]
    print(f"building groups with coords: {len(with_coords)} (grid {len(with_grid)}, estate {n_fb}), "
          f"with yield: {bm.yield_map_pct.notna().sum()}, "
          f"yielded+coords: {bm[bm.lon.notna() & bm.yield_map_pct.notna()].shape[0]}")
    bm.to_csv(DERIVED / "building_metrics_12m.csv", index=False)

    # ---- images per building group (latest deals; prefer 实景 > VR) ----
    im = full[full.bldg_grp_id.notna() & full.image_url.notna()].copy()
    im["pri"] = im.image_type.map({"Image": 0, "VirtualTour": 1}).fillna(2)
    im = im.sort_values(["hma_id", "bldg_grp_id", "pri", "analysis_date"],
                        ascending=[True, True, True, False])
    images = {}
    for (h, g), part in im.groupby(["hma_id", "bldg_grp_id"]):
        seen, out = set(), []
        for r in part.itertuples():
            u = str(r.image_url)
            if u in seen:
                continue
            seen.add(u)
            out.append({"u": u, "t": r.image_type})
            if len(out) >= 6:
                break
        images[f"{h}:{g}"] = out
    (DERIVED / "building_images.json").write_text(
        json.dumps(images, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"images: {len(images)} groups, {sum(len(v) for v in images.values())} urls")

    # ---- per-building breakdown extras for mixed-age / street-cluster groups ----
    def _truthy(v):
        return pd.notna(v) and bool(v)

    mixed_ids = {f"{r.hma_id}:{r.bldg_grp_id}" for r in bm.itertuples()
                 if _truthy(getattr(r, "age_mixed", False))}
    street_ids = {f"{r.hma_id}:{r.bldg_grp_id}" for r in bm.itertuples()
                  if "住宅物業" in str(getattr(r, "bldg_grp_name", "") or "")
                  and " / " in str(getattr(r, "bldg_grp_name", "") or "")}
    detail_ids = mixed_ids | street_ids
    hist_b = history_by_building(full, detail_ids)
    (DERIVED / "building_history_by_building.json").write_text(
        json.dumps(hist_b, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"street/mixed group building history: {len(hist_b['groups'])} groups, "
          f"{sum(len(v) for v in hist_b['buildings'].values())} buildings")

    need_pairs = {(str(r.hma_id), str(r.bldg_grp_id)) for r in bm.itertuples()
                  if f"{r.hma_id}:{r.bldg_grp_id}" in detail_ids}
    deals_b = deals_by_building(full, need_pairs, n=20)
    (DERIVED / "building_deals_by_building.json").write_text(
        json.dumps(deals_b, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"per-building deals: {len(deals_b)} groups, "
          f"{sum(len(v) for v in deals_b.values())} buildings")

    # ---- map feed ----
    deals = deals_preview(full)
    hist = history_preview(full)
    fallback_estate_names = [n for n in em.estate_name.dropna().astype(str).unique()
                             if n not in estate_names_with_groups]
    fallback_full = full[full.estate_name.astype(str).isin(fallback_estate_names)]
    estate_deals = deals_preview(fallback_full, key="estate_name")
    estate_hist = history_preview(fallback_full, key="estate_name")
    (DERIVED / "estate_deals_recent.json").write_text(
        json.dumps(estate_deals, ensure_ascii=False, separators=(",", ":")))
    (DERIVED / "estate_history_full.json").write_text(
        json.dumps(estate_hist, ensure_ascii=False, separators=(",", ":")))
    print(f"estate details: {len(estate_deals)} with deals, {len(estate_hist['buildings'])} with history")
    hist_json = json.dumps(hist, ensure_ascii=False, separators=(",", ":"))
    (DERIVED / "building_history_full.json").write_text(hist_json)
    # Legacy filename kept for existing downstream scripts/tests.
    (DERIVED / "building_history_36m.json").write_text(hist_json)
    hist_yield_months = [sum(1 for sx, rx in zip(v["s"], v["r"]) if sx > 0 and rx > 0)
                         for v in hist["buildings"].values()]
    print(f"history: {hist['window']['months']} months, {len(hist['buildings'])} buildings, "
          f"{sum(1 for k in hist_yield_months if k >= 3)} with >=3 yield months")
    feats = []
    for row in with_coords.itertuples():
        feats.append({
            "type": "Feature",
            "properties": {
                "layer": "building",
                "id": f"{row.hma_id}:{row.bldg_grp_id}",
                "name": (row.estate_name or row.grid_name or row.bldg_grp_name or ""),
                "bldg": row.bldg_grp_name or row.grid_name,
                "district": row.district, "hma": row.hma, "region": row.region,
                "sale_psf": None if pd.isna(row.sale_psf) else round(float(row.sale_psf)),
                "rent_psf": None if pd.isna(row.rent_psf) else round(float(row.rent_psf), 2),
                "sale_psf_p25": None if pd.isna(getattr(row, "sale_psf_p25", None)) else round(float(row.sale_psf_p25)),
                "sale_psf_p75": None if pd.isna(getattr(row, "sale_psf_p75", None)) else round(float(row.sale_psf_p75)),
                "rent_psf_p25": None if pd.isna(getattr(row, "rent_psf_p25", None)) else round(float(row.rent_psf_p25), 2),
                "rent_psf_p75": None if pd.isna(getattr(row, "rent_psf_p75", None)) else round(float(row.rent_psf_p75), 2),
                "yield": None if pd.isna(row.yield_map_pct) else round(float(row.yield_map_pct), 3),
                "yield_basis": row.yield_basis,
                "yield_bldg_n": None if pd.isna(getattr(row, "yield_bldg_n", None)) else int(row.yield_bldg_n),
                "yield_group_pct": None if pd.isna(row.gross_yield_pct) else round(float(row.gross_yield_pct), 3),
                "sale_n": int(row.sale_n) if pd.notna(row.sale_n) else 0,
                "rent_n": int(row.rent_n) if pd.notna(row.rent_n) else 0,
                "sale_price_median": None if pd.isna(row.sale_price_median) else int(row.sale_price_median),
                "rent_monthly_median": None if pd.isna(row.rent_monthly_median) else int(row.rent_monthly_median),
                "area_median": None if pd.isna(row.sale_area_median) else int(row.sale_area_median),
                "quality": row.quality,
                "geo": row.geo,
                "op_year": None if pd.isna(getattr(row, "op_year", None)) else int(row.op_year),
                "age_years": None if pd.isna(getattr(row, "age_years", None)) else int(row.age_years),
                "op_year_min": None if pd.isna(getattr(row, "op_year_min", None)) else int(row.op_year_min),
                "age_rvd_buildings": None if pd.isna(getattr(row, "age_rvd_buildings", None)) else int(row.age_rvd_buildings),
                "op_year_max": None if pd.isna(getattr(row, "op_year_max", None)) else int(row.op_year_max),
                "age_mixed": bool(getattr(row, "age_mixed", False)) if pd.notna(getattr(row, "age_mixed", None)) else False,
                "schools_1km": int(getattr(row, "schools_1km", 0) or 0),
                "schools_kindergarten": int(getattr(row, "schools_kindergarten", 0) or 0),
                "schools_primary": int(getattr(row, "schools_primary", 0) or 0),
                "schools_secondary": int(getattr(row, "schools_secondary", 0) or 0),
                "schools_university": int(getattr(row, "schools_university", 0) or 0),
                "schools_college": int(getattr(row, "schools_college", 0) or 0),
                "deals": deals.get(row.bldg_grp_id, {"sale": [], "rent": []}),
            },
            "geometry": {"type": "Point", "coordinates": [row.lon, row.lat]},
        })
    for row in em.itertuples():
        if pd.isna(row.lon):
            continue
        feats.append({
            "type": "Feature",
            "properties": {
                "layer": "estate", "id": row.type_code or row.estate_name,
                "name": row.estate_name, "district": row.district, "region": row.region,
                "bldg": str(row.estate_name) + " · 屋苑级",
                "yield_basis": "estate", "geo": "estate",
                "sale_psf": None if pd.isna(row.sale_psf) else round(float(row.sale_psf)),
                "rent_psf": None if pd.isna(row.rent_psf) else round(float(row.rent_psf), 2),
                "yield": None if pd.isna(row.gross_yield_pct) else round(float(row.gross_yield_pct), 3),
                "sale_n": int(row.sale_n) if pd.notna(row.sale_n) else 0,
                "rent_n": int(row.rent_n) if pd.notna(row.rent_n) else 0,
                "sale_price_median": None if pd.isna(row.sale_price_median) else int(row.sale_price_median),
                "rent_monthly_median": None if pd.isna(row.rent_monthly_median) else int(row.rent_monthly_median),
                "op_year": None if pd.isna(getattr(row, "op_year", None)) else int(row.op_year),
                "age_years": None if pd.isna(getattr(row, "age_years", None)) else int(row.age_years),
                "op_year_min": None if pd.isna(getattr(row, "op_year_min", None)) else int(row.op_year_min),
                "op_year_max": None if pd.isna(getattr(row, "op_year_max", None)) else int(row.op_year_max),
                "age_mixed": bool(getattr(row, "age_mixed", False)) if pd.notna(getattr(row, "age_mixed", None)) else False,
                "has_building_group": bool(getattr(row, "has_building_group", False)),
                "schools_1km": int(getattr(row, "schools_1km", 0) or 0),
                "schools_kindergarten": int(getattr(row, "schools_kindergarten", 0) or 0),
                "schools_primary": int(getattr(row, "schools_primary", 0) or 0),
                "schools_secondary": int(getattr(row, "schools_secondary", 0) or 0),
                "schools_university": int(getattr(row, "schools_university", 0) or 0),
                "schools_college": int(getattr(row, "schools_college", 0) or 0),
                "quality": row.quality,
            },
            "geometry": {"type": "Point", "coordinates": [row.lon, row.lat]},
        })
    gj = {"type": "FeatureCollection", "features": feats}
    (DERIVED / "building_map_points.geojson").write_text(
        json.dumps(gj, ensure_ascii=False, separators=(",", ":")))
    (DERIVED / "building_deals_recent.json").write_text(
        json.dumps(deals, ensure_ascii=False, separators=(",", ":")))

    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "history_months": hist["window"]["months"],
        "history_start": hist["window"]["start"], "history_end": hist["window"]["end"],
        "buildings_with_yield_history_ge3": int(sum(1 for k in hist_yield_months if k >= 3)),
        "window_start": str(WINDOW_START.date()),
        "snapshot": SNAPSHOT,
        "records_full": len(full), "records_12m": len(win), "sale_cross_source_dropped": n_sale_drop,
        "building_points": int(len(with_coords)),
        "building_with_yield": int(bm.gross_yield_pct.notna().sum()),
        "estate_points": int(em.lon.notna().sum()),
        "estate_with_yield": int(em.gross_yield_pct.notna().sum()),
    }
    (DERIVED / "building_metrics_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=1))
    print(json.dumps(audit, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
