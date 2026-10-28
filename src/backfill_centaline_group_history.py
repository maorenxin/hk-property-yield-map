#!/usr/bin/env python3
"""Backfill full Centaline history for every building group already on the map.

The public API has an undocumented `day: ""` mode that returns all history.
For this project we only need the groups present in our current map (2,279),
so we query by `bldgGrpID` instead of crawling the whole 2.6M-record corpus.

Strategy:
- group + postType(S/R) count
- <= 9,800: fetch all pages
- > 9,800: split the registration-date window by year/month, then by
  first/second hand, source, and saleable-area band if a leaf is still huge.
- Every page is cached; reruns skip completed pages.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import hashlib
import json
import random
import threading
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/centaline/transaction_search_full/2026-10-18"
COUNT_CACHE = OUT / "count_cache.json"
PAGES = OUT / "pages"
MANIFEST = OUT / "manifest.csv"
BASE = "https://hk-api.centanet.com/centanetapi/api"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
PAGE_SIZE = 100
LEAF_MAX = 9800
RATE_INTERVAL = 0.35
_local = threading.local()
_rate_lock = threading.Lock()
_rate_next = 0.0
_count_cache = {}


def _pace(interval: float | None = None) -> None:
    global _rate_next
    if interval is None:
        interval = RATE_INTERVAL
    with _rate_lock:
        now = time.monotonic()
        if now < _rate_next:
            time.sleep(_rate_next - now)
            now = time.monotonic()
        _rate_next = now + interval


def session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json", "User-Agent": UA,
                          "Origin": "https://hk.centanet.com", "Referer": "https://hk.centanet.com/"})
        _local.s = s
    return s


def post(path: str, body: dict, timeout: int = 90) -> dict:
    last = None
    for attempt in range(4):
        try:
            _pace()
            r = session().post(BASE + path, json=body, timeout=timeout)
            if r.status_code == 400:
                raise RuntimeError(f"HTTP 400: {r.text[:300]}")
            if r.status_code == 429:
                last = f"HTTP 429"
                time.sleep(25 + attempt * 20)
                continue
            r.raise_for_status()
            return r.json()
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep((1.2, 3, 8, 18)[attempt])
    raise RuntimeError(last)


def count(filters: dict) -> int:
    body = {"day": "", "sort": "InsOrRegDate", "order": "Ascending",
            "offset": 0, "size": 1, "estateUsages": ["RE", "HOS"], **filters}
    key = hashlib.sha1(json.dumps(filters, sort_keys=True).encode()).hexdigest()
    if key in _count_cache:
        return int(_count_cache[key])
    d = post("/Transaction/Search", body)
    n = int(d.get("count") or 0)
    _count_cache[key] = n
    return n


def pages_for(filters: dict, path: str) -> list[dict]:
    """Return page metadata; caller fetches each page."""
    n = count(filters)
    if n <= 0:
        return []
    if n <= LEAF_MAX:
        return [{"filters": filters, "count": n, "path": path}]
    # Split by registration date first (year/month), then dimensions.
    start = filters.get("insOrRegDateTimeRange", {}).get("start")
    end = filters.get("insOrRegDateTimeRange", {}).get("end")
    if start and end:
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        if e > s + pd.Timedelta(days=31):
            mid = s + (e - s) / 2
            return (pages_for({**filters, "insOrRegDateTimeRange": {"start": str(s.date()), "end": str(mid.date())}}, path + "A")
                    + pages_for({**filters, "insOrRegDateTimeRange": {"start": str(mid.date()), "end": str(e.date())}}, path + "B"))
        # split month into first/second half, then days
        if e > s + pd.Timedelta(days=1):
            mid = s + (e - s) / 2
            return (pages_for({**filters, "insOrRegDateTimeRange": {"start": str(s.date()), "end": str(mid.date())}}, path + "A")
                    + pages_for({**filters, "insOrRegDateTimeRange": {"start": str(mid.date()), "end": str(e.date())}}, path + "B"))
    # still huge: split dimensions
    for dim, vals in [
        ("postType", ["Sale", "Rent"]),
        ("firstOrSecondHand", ["FirstHand", "SecondHand"]),
        ("transactionSource", ["Land", "AC"]),
    ]:
        kids = []
        total = 0
        for v in vals:
            f = {**filters, dim: [v]}
            c = count(f)
            total += c
            if c:
                kids += pages_for(f, path + f"_{dim}:{v}")
        if total >= int(n * 0.95):
            return kids
    # area bands as last resort
    bands = [(0, 300), (300, 500), (500, 700), (700, 900), (900, 1200), (1200, 1600), (1600, 2200), (2200, 1000000)]
    kids = []
    for i, (lo, hi) in enumerate(bands):
        f = {**filters, "nSizeRange": {"min": lo, "max": hi}}
        c = count(f)
        if c:
            kids += pages_for(f, path + f"_area:{i}")
    return kids


def fetch_page(leaf: dict, offset: int) -> dict:
    fid = hashlib.sha1(json.dumps(leaf["filters"], sort_keys=True).encode()).hexdigest()[:12]
    fp = PAGES / fid / f"p{offset:05d}.json.gz"
    if fp.exists() and fp.stat().st_size > 100:
        return {"status": "cached", "file": str(fp.relative_to(ROOT)), "path": leaf["path"]}
    body = {"day": "", "sort": "InsOrRegDate", "order": "Ascending",
            "offset": offset, "size": PAGE_SIZE, "estateUsages": ["RE", "HOS"], **leaf["filters"]}
    d = post("/Transaction/Search", body)
    blob = json.dumps({"request": body, "response_count": d.get("count"),
                       "data": d.get("data") or []}, ensure_ascii=False, separators=(",", ":"))
    fp.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(fp, "wt") as f:
        f.write(blob)
    time.sleep(0.45 + random.uniform(0, 0.15))
    return {"status": "ok", "file": str(fp.relative_to(ROOT)), "path": leaf["path"]}


def cached_group_coverage() -> set[str]:
    """Return bldgGrpID values with apparent complete full-history page sets.

    The manifest is overwritten by later incremental runs, so it is not a
    reliable coverage index.  The page cache is.  A group is considered
    covered when its highest cached offset reaches the query's response count
    (capped at the API's 9,800-row window).
    """
    stats: dict[str, dict[str, int]] = {}
    if not PAGES.exists():
        return set()
    for d in PAGES.iterdir():
        if not d.is_dir():
            continue
        fs = list(d.glob("*.json.gz"))
        if not fs:
            continue
        try:
            with gzip.open(fs[0], "rt") as f:
                x = json.load(f)
            req = x.get("request") or {}
            gid = str(req.get("bldgGrpID") or "")
            if not gid:
                continue
            count = int(x.get("response_count") or 0)
            max_off = max(int(p.stem[1:].split(".")[0]) for p in fs)
            cur = stats.setdefault(gid, {"count": 0, "max_off": -1})
            cur["count"] = max(cur["count"], count)
            cur["max_off"] = max(cur["max_off"], max_off)
        except Exception:
            continue
    return {gid for gid, v in stats.items()
            if v["count"] <= 0 or v["max_off"] + PAGE_SIZE >= min(v["count"], LEAF_MAX)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--pace", type=float, default=0.18, help="minimum seconds between API requests across workers")
    ap.add_argument("--max-groups", type=int, default=0)
    ap.add_argument("--detail-only", action="store_true", help="only groups with per-building detail")
    ap.add_argument("--missing-only", action="store_true", help="only groups without pages or with failed pages")
    ap.add_argument("--missing-cache", action="store_true", help="only groups whose full-history page cache is incomplete")
    args = ap.parse_args()
    global RATE_INTERVAL
    RATE_INTERVAL = max(0.05, float(args.pace))
    b = pd.read_csv(ROOT / "data/derived/building_metrics_12m.csv")
    groups = sorted({(str(r.hma_id), str(r.bldg_grp_id)) for r in b.itertuples()
                     if pd.notna(r.hma_id) and pd.notna(r.bldg_grp_id)})
    if args.detail_only:
        md = json.loads((ROOT / "site/map_data.js").read_text()[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
        detail = {x["id"] for x in md.get("buildings", [])
                  if x.get("age_mixed") or ("住宅物業" in str(x.get("bldg") or "") and " / " in str(x.get("bldg") or ""))}
        groups = [(h, g) for h, g in groups if f"{h}:{g}" in detail]
    priority = {"HMA090:KC0135"}
    groups.sort(key=lambda x: (f"{x[0]}:{x[1]}" not in priority, x))
    if args.missing_cache:
        covered = cached_group_coverage()
        before = len(groups)
        groups = [(h, g) for h, g in groups if g not in covered]
        print(f"cache coverage: {len(covered)} covered; remaining groups: {len(groups)}/{before}", flush=True)
    elif args.missing_only and MANIFEST.exists():
        seen = set()
        failed = set()
        try:
            mm = pd.read_csv(MANIFEST)
            for r in mm.itertuples():
                gp = str(getattr(r, "group_path", ""))
                parts = gp.split(":")
                if len(parts) >= 2:
                    key = (parts[0], parts[1])
                    seen.add(key)
                    if str(getattr(r, "status", "")) == "failed":
                        failed.add(key)
        except Exception:
            pass
        groups = [g for g in groups if g not in seen or g in failed]
    if args.max_groups:
        groups = groups[:args.max_groups]
    print(f"groups: {len(groups)}")
    PAGES.mkdir(parents=True, exist_ok=True)

    # plan all leaves (counts only); one both-sides query per group
    global _count_cache
    if COUNT_CACHE.exists():
        try:
            _count_cache = json.loads(COUNT_CACHE.read_text())
        except Exception:
            _count_cache = {}
    leaves = []
    for i, (hma, gid) in enumerate(groups, 1):
        f = {"postType": "Both", "bldgGrpID": gid,
             "insOrRegDateTimeRange": {"start": "1990-01-01", "end": "2026-10-31"}}
        try:
            ls = pages_for(f, f"{hma}:{gid}")
        except Exception as e:  # noqa: BLE001
            print("plan fail", hma, gid, e)
            continue
        leaves += ls
        if i % 25 == 0 or i == len(groups):
            COUNT_CACHE.parent.mkdir(parents=True, exist_ok=True)
            COUNT_CACHE.write_text(json.dumps(_count_cache, ensure_ascii=False))
            print(f"planned {i}/{len(groups)} groups, leaves={len(leaves)}", flush=True)

    jobs = []
    for leaf in leaves:
        n = min(leaf["count"], LEAF_MAX)
        for off in range(0, n, PAGE_SIZE):
            jobs.append((leaf, off))
    print(f"leaves={len(leaves)} pages={len(jobs)}")
    rows = []
    ok = cached = failed = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_page, leaf, off): (leaf, off) for leaf, off in jobs}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            leaf, off = futs[fut]
            try:
                r = fut.result()
                rows.append({"group_path": leaf["path"], "offset": off, "count": leaf["count"],
                             "status": r["status"], "file": r["file"]})
                if r["status"] == "cached": cached += 1
                else: ok += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                rows.append({"group_path": leaf["path"], "offset": off, "count": leaf["count"],
                             "status": "failed", "error": str(e)[:200]})
            if i % 200 == 0 or i == len(jobs):
                pd.DataFrame(rows).to_csv(MANIFEST, index=False)
                print(f"pages {i}/{len(jobs)} ok={ok} cached={cached} failed={failed}", flush=True)
    pd.DataFrame(rows).to_csv(MANIFEST, index=False)
    (OUT / "summary.json").write_text(json.dumps({
        "groups": len(groups), "leaves": len(leaves), "pages": len(jobs),
        "ok": ok, "cached": cached, "failed": failed,
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=1))
    print(f"done ok={ok} cached={cached} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
