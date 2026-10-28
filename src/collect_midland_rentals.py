#!/usr/bin/env python3
"""Collect Midland rental transactions (3-year API window) for map groups.

Midland's public web app uses a guest JWT in the `token` cookie.  The token is
read from the local .env (never committed) and the API is accessed with a
Chrome TLS fingerprint via curl_cffi.  This collector only keeps rental
transactions (`tx_type=L`); sale history is already covered by Centaline.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import math
import random
import re
import threading
import time
import unicodedata
from pathlib import Path

import pandas as pd
from curl_cffi import requests as cr

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-10-18"
RAW = ROOT / "data/raw/midland" / DATE
AUTO = RAW / "autocomplete"
RENT_RAW = RAW / "rent_pages"
MAP_OUT = ROOT / "data/derived/midland_group_map.csv"
RENT_OUT = ROOT / "data/derived/midland_rentals_2026-10-18.csv"
BASE = "https://data.midland.com.hk"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/155.0 Safari/537.36"
_local = threading.local()


def token() -> str:
    p = ROOT / ".env"
    if not p.exists():
        raise SystemExit("missing .env with MIDLAND_TOKEN")
    for line in p.read_text().splitlines():
        if line.startswith("MIDLAND_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("MIDLAND_TOKEN not found in .env")


TOKEN = token()
HEADERS = {"Authorization": "Bearer " + TOKEN, "User-Agent": UA,
           "Origin": "https://www.midland.com.hk", "Referer": "https://www.midland.com.hk/"}


def get(path: str, params: dict, timeout: int = 60):
    last = None
    for i in range(4):
        try:
            r = cr.get(BASE + path, params=params, headers=HEADERS, timeout=timeout, impersonate="chrome")
            if r.status_code == 200:
                return r.json()
            last = f"http {r.status_code}: {r.text[:120]}"
            if r.status_code in (400, 401, 403, 404):
                break
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.5 + i * 3)
    raise RuntimeError(last)


def norm(s) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)


def dist_m(a, b):
    dx = (a[0] - b[0]) * 111320 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (a[1] - b[1]) * 110540
    return math.hypot(dx, dy)


def autocomplete(text: str):
    key = hashlib.sha1(text.encode()).hexdigest()[:16]
    fp = AUTO / f"{key}.json"
    if fp.exists():
        try:
            d = json.loads(fp.read_text())
            if isinstance(d, list):
                return d
        except Exception:
            pass
    d = get("/search/v2/autocomplete/estates", {"lang": "zh-hk", "text": text, "map": "false"}, timeout=40)
    rows = [x for g in d for x in (g.get("result") or [])]
    AUTO.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    time.sleep(0.25 + random.uniform(0, 0.2))
    return rows


def load_groups() -> list[dict]:
    data = json.loads((ROOT / "site/map_data.js").read_text()[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    builds = {b["id"]: b for b in data["buildings"] if b.get("lon") is not None}
    bb = pd.read_csv(ROOT / "data/derived/building_metrics_by_building_12m.csv")
    out = []
    for gid, b in builds.items():
        h, g = gid.split(":")
        names = [str(x.building_name) for x in bb[(bb.hma_id == h) & (bb.bldg_grp_id == g)].itertuples()
                 if pd.notna(x.building_name)]
        out.append({"gid": gid, "name": b.get("name") or "", "bldg": b.get("bldg") or "",
                    "lon": float(b["lon"]), "lat": float(b["lat"]), "names": names})
    return out


def map_one(g: dict) -> dict:
    queries = []
    q = re.sub(r"^\([^)]*\)\s*", "", str(g["name"] or "")).strip()
    if q:
        queries.append(q)
    specifics = [n for n in g["names"] if len(norm(n)) >= 3 and not re.fullmatch(r"(第)?\d+座|[a-z]座|\d+期|第?\d+期", norm(n))]
    for n in list(dict.fromkeys(specifics))[:3]:
        if n not in queries:
            queries.append(n)
    best = None
    for q in queries:
        try:
            rows = autocomplete(q)
        except Exception as e:  # noqa: BLE001
            print("autocomplete fail", g["gid"], q, e)
            continue
        for x in rows:
            typ = (x.get("search") or {}).get("type")
            if typ not in ("estate", "phase", "building"):
                continue
            nm = ((x.get("name") or {}).get("zh-hk") or "")
            if norm(nm) != norm(q):
                continue
            loc = x.get("location") or {}
            if loc.get("lon") is None:
                continue
            d = dist_m((g["lon"], g["lat"]), (float(loc["lon"]), float(loc["lat"])))
            if d > 500:
                continue
            parent = (x.get("parent_estate") or {}).get("id") or (x.get("search") or {}).get("id")
            candidate = {"gid": g["gid"], "query": q, "midland_id": (x.get("search") or {}).get("id"),
                         "midland_type": typ, "midland_name": nm, "parent_estate_id": parent,
                         "dist_m": round(d, 1)}
            if best is None or candidate["dist_m"] < best["dist_m"]:
                best = candidate
        if best and best["dist_m"] <= 100:
            break
    return best or {"gid": g["gid"], "query": queries[0] if queries else "", "midland_id": None,
                    "midland_type": None, "midland_name": None, "parent_estate_id": None, "dist_m": None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--skip-map", action="store_true")
    args = ap.parse_args()
    AUTO.mkdir(parents=True, exist_ok=True)
    RENT_RAW.mkdir(parents=True, exist_ok=True)
    groups = load_groups()
    print(f"groups: {len(groups)}")
    if args.skip_map and MAP_OUT.exists():
        mapping = pd.read_csv(MAP_OUT)
    else:
        rows = []
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            for i, r in enumerate(ex.map(map_one, groups), 1):
                rows.append(r)
                if i % 50 == 0 or i == len(groups):
                    pd.DataFrame(rows).to_csv(MAP_OUT, index=False)
                    print(f"mapped {i}/{len(groups)} hit={sum(bool(x['midland_id']) for x in rows)}", flush=True)
        mapping = pd.DataFrame(rows)
        mapping.to_csv(MAP_OUT, index=False)
    hit = mapping[mapping.midland_id.notna()]
    print(f"mapped groups: {len(hit)}/{len(mapping)}; unique estates: {hit.parent_estate_id.nunique()}")
    estate_ids = sorted({str(x) for x in hit.parent_estate_id.dropna() if str(x).startswith("E")})
    # batch estate IDs; api accepts comma-separated estate ids
    batches = [estate_ids[i:i + 30] for i in range(0, len(estate_ids), 30)]
    all_rows = []
    for bi, ids in enumerate(batches, 1):
        fp = RENT_RAW / f"batch_{bi:03d}.json"
        if fp.exists():
            d = json.loads(fp.read_text())
            pages = d.get("pages", [d]) if isinstance(d, dict) else [d]
        else:
            first = get("/search/v2/transactions", {"chart": "false", "lang": "zh-hk", "currency": "HKD",
                                                     "unit": "feet", "est_ids": ",".join(ids),
                                                     "tx_type": "L", "limit": "1000", "page": "1"}, timeout=60)
            pages = [first]
            total = int(first.get("count") or 0)
            for page in range(2, math.ceil(total / 1000) + 1):
                pages.append(get("/search/v2/transactions", {"chart": "false", "lang": "zh-hk", "currency": "HKD",
                                                              "unit": "feet", "est_ids": ",".join(ids),
                                                              "tx_type": "L", "limit": "1000", "page": str(page)}, timeout=60))
                time.sleep(0.5)
            fp.write_text(json.dumps({"pages": pages}, ensure_ascii=False, separators=(",", ":")))
            time.sleep(0.6)
        for d in pages:
            all_rows.extend(d.get("result") or [])
        if bi % 10 == 0 or bi == len(batches):
            print(f"rent batch {bi}/{len(batches)} rows={len(all_rows)}", flush=True)
    # assign rows to map groups
    mrows = []
    by_estate = {}
    for r in hit.itertuples():
        by_estate.setdefault(str(r.parent_estate_id), []).append(r)
    for x in all_rows:
        est = str((x.get("estate") or {}).get("id") or "")
        phase = str((x.get("phase") or {}).get("id") or "")
        bld = norm((x.get("building") or {}).get("name"))
        candidates = by_estate.get(est) or []
        chosen = None
        phase_hit = [r for r in candidates if str(getattr(r, "midland_id", "")) == phase]
        if phase_hit:
            chosen = phase_hit[0]
        else:
            exact_est = [r for r in candidates if str(getattr(r, "midland_id", "")) == est]
            if exact_est:
                chosen = exact_est[0]
            else:
                bhit = [r for r in candidates if bld and (bld in norm(getattr(r, "name", "")) or norm(getattr(r, "name", "")) in bld)]
                if len(bhit) == 1:
                    chosen = bhit[0]
                elif len(candidates) == 1:
                    chosen = candidates[0]
        if not chosen:
            continue
        date = str(x.get("tx_date") or "")[:10]
        area = x.get("net_area") or x.get("area")
        price = x.get("price")
        mrows.append({
            "source": "midland", "source_record_id": x.get("id"),
            "source_url": x.get("url_desc"), "transaction_date": date, "deal_date": date,
            "listing_type": "rent", "data_source": x.get("source") or "MIDLAND",
            "group_id": chosen.gid, "midland_estate_id": est,
            "midland_phase_id": phase, "estate_name": (x.get("estate") or {}).get("name"),
            "phase_name": (x.get("phase") or {}).get("name"),
            "building_name": (x.get("building") or {}).get("name"),
            "floor": (x.get("floor_level") or {}).get("name"), "unit": x.get("flat"),
            "saleable_area_sqft": area, "rent_monthly_hkd": price,
            "price_hkd": price, "saleable_psf": x.get("unit_price_net"),
            "retrieved_at": DATE,
        })
    out = pd.DataFrame(mrows)
    out.to_csv(RENT_OUT, index=False)
    print(f"rental rows assigned: {len(out):,} -> {RENT_OUT.relative_to(ROOT)}")
    print(f"mapping -> {MAP_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
