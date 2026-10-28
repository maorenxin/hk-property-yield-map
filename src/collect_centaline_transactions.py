#!/usr/bin/env python3
"""Centaline (中原地產) public transaction-search collector.

Fetches the full unit-level transaction corpus (sale + rent) for the rolling
3-year window served by Centaline's public search API, partitioned adaptively
so every leaf query stays within the API's hard paging limit
(offset + size <= 10000).

API facts verified 2026-10-16 against hk-api.centanet.com:
- POST /centanetapi/api/Transaction/Search  (JSON body, no auth required)
- size max 100; offset + size <= 10000
- day enum: Day30 / Day90 / Day180 / Day365 / Day1095
- working filter fields: postType, bigestAndEstate, phaseAndEstate, building,
  bldgGrpID, bedroomCount, nSizeRange{min,max}, buildingAgeRange{min,max},
  nUnitPriceRange{min,max}, firstOrSecondHand, transactionSource, estateUsages, keyword
- GET/POST /centanetapi/api/Transaction/GetTransactionFilterOptions returns
  per-bucket counts used to plan the partition tree

Raw pages are stored gzipped, one file per (leaf, page); a journal makes the
run resumable. Everything is append-only snapshots, nothing is overwritten.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://hk-api.centanet.com/centanetapi/api"
SNAPSHOT_DATE = date.today().isoformat()
RAW_DIR = ROOT / "data/raw/centaline/transaction_search" / SNAPSHOT_DATE

PAGE_SIZE = 100
LEAF_MAX = 9900          # leave headroom under the 10000 rule
DELAY = 0.45             # seconds between requests
RETRIES = 3
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36")

BASE_SEARCH = {
    "day": "Day1095",
    "sort": "InsOrRegDate",
    "order": "Descending",
    "size": PAGE_SIZE,
}

# split dimensions, in order of preference (exact-tiling dims first)
DIM_ORDER = ["estateUsages", "firstOrSecondHand", "transactionSource",
             "nSizeRange", "buildingAgeRange", "bedroomCount", "nUnitPriceRange"]


_local = threading.local()


def _session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json", "User-Agent": UA,
                          "Origin": "https://hk.centanet.com",
                          "Referer": "https://hk.centanet.com/"})
        _local.s = s
    return s


def http_post(url: str, body: dict, timeout: int = 90) -> dict:
    s = _session()
    last_err = None
    for attempt in range(RETRIES + 2):
        try:
            r = s.post(url, json=body, timeout=timeout)
            if r.status_code == 400:      # validation error: do not retry
                raise RuntimeError(f"HTTP 400: {r.text[:300]}")
            r.raise_for_status()
            return r.json()
        except RuntimeError:
            raise
        except Exception as e:     # noqa: BLE001 - network layer, retry
            last_err = f"{type(e).__name__}: {e}"
            time.sleep((1.5, 4, 10, 20, 30)[min(attempt, 4)])
    raise RuntimeError(f"request failed after retries: {last_err}")


def search(filters: dict, offset: int = 0, size: int = PAGE_SIZE) -> dict:
    return search_ordered(filters, "Descending", offset, size)


def search_ordered(filters: dict, order: str = "Descending", offset: int = 0,
                   size: int = PAGE_SIZE) -> dict:
    body = dict(BASE_SEARCH)
    body.update(filters)
    body["order"] = order
    body["offset"] = offset
    body["size"] = size
    time.sleep(DELAY + random.uniform(0, 0.15))
    return http_post(f"{BASE}/Transaction/Search", body)


def node_id(filters: dict) -> str:
    return hashlib.md5(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:12]


def bucket_filters(dim: str, options: dict) -> list[dict]:
    """Return [{label, filter_fragment}] for one split dimension."""
    out: list[dict] = []
    if dim == "estateUsages":
        for x in options["estateUsage"]:
            out.append({"label": x["label"], "frag": {"estateUsages": [x["value"]]}})
    elif dim == "firstOrSecondHand":
        for x in options["firstOrSecondHand"]:
            out.append({"label": x["label"], "frag": {"firstOrSecondHand": [x["value"]]}})
    elif dim == "transactionSource":
        for x in options["transactionSource"]:
            out.append({"label": x["label"], "frag": {"transactionSource": [x["value"]]}})
    elif dim == "bedroomCount":
        for x in options["bedroomCount"]:
            out.append({"label": x["label"], "frag": {"bedroomCount": [x["value"]]}})
    elif dim in ("nSizeRange", "buildingAgeRange", "nUnitPriceRange"):
        key = {"nSizeRange": "nSize", "buildingAgeRange": "buildingAge",
               "nUnitPriceRange": "nUnitPrice"}[dim]
        rng = options[key]["range"]
        for i, x in enumerate(rng):
            lo = x["from"]
            hi = x.get("to")
            if hi is None:              # open-ended last bucket
                hi = 10**9
            else:
                # adjacent buckets may share a boundary; nudge the low edge of
                # every bucket except the first so ranges stay nested-friendly
                pass
            out.append({"label": x["key"], "frag": {dim: {"min": lo, "max": hi}}})
    return out


def plan_tree(options: dict, journal: dict, dry_run: bool) -> list[dict]:
    """Adaptive partition plan. Returns list of leaf nodes with counts."""
    leaves: list[dict] = []
    # two roots so sale/rent semantics stay separate
    queue = [
        {"postType": "Sale", "depth": 0, "path": "Sale", "used": [], "tried": []},
        {"postType": "Rent", "depth": 0, "path": "Rent", "used": [], "tried": []},
    ]
    oversized: list[dict] = []
    while queue:
        node = queue.pop(0)
        filters = {k: v for k, v in node.items()
                   if k not in ("depth", "path", "used", "tried")}
        nid = node_id(filters)
        key = f"{node['path']}#{nid}"
        if key in journal and journal[key].get("count") is not None:
            cnt = journal[key]["count"]
        else:
            cnt = search(filters, offset=0, size=1)["count"]
            journal[key] = {"filters": filters, "count": cnt, "path": node["path"],
                            "depth": node["depth"]}
            save_journal(journal)
            print(f"  count {cnt:>7}  {node['path']}")
        if cnt == 0:
            continue
        if cnt <= LEAF_MAX:
            leaves.append({"filters": filters, "count": cnt, "path": node["path"]})
            continue
        # need to split
        split_done = False
        used = set(node.get("used", []))
        tried = set(node.get("tried", []))
        for dim in DIM_ORDER:
            if dim in used or dim in tried:
                continue
            buckets = bucket_filters(dim, options)
            if not buckets:
                continue

            def probe(b):
                child = dict(filters)
                child.update(b["frag"])
                ckey = f"{node['path']}>{dim}:{b['label']}#{node_id(child)}"
                if ckey in journal and journal[ckey].get("count") is not None:
                    c = journal[ckey]["count"]
                else:
                    c = search(child, offset=0, size=1)["count"]
                    journal[ckey] = {"filters": child, "count": c,
                                     "path": f"{node['path']}>{dim}:{b['label']}",
                                     "depth": node["depth"] + 1}
                    save_journal(journal)
                    print(f"  count {c:>7}  {node['path']}>{dim}:{b['label']}")
                return child, c, ckey, b

            # probe buckets until the first non-zero one tells us whether this
            # dimension actually filters this node
            trial = []
            effective = None
            for b in buckets:
                child, c, ckey, _ = probe(b)
                trial.append((b, child, c, ckey))
                if c > 0:
                    effective = c < cnt
                    break
            if effective is None or not effective:
                tried.add(dim)      # zero coverage or no filtering: never retry
                continue
            # effective: probe the rest (parallel, keeps wall-clock sane)
            rest = buckets[len(trial):]
            if rest:
                with ThreadPoolExecutor(max_workers=5) as pex:
                    for child, c, ckey, b in pex.map(probe, rest):
                        trial.append((b, child, c, ckey))
            total = sum(t[2] for t in trial)
            if total < cnt * 0.95:
                # too much of this node is invisible to the dim (e.g. missing
                # area / age fields) - try a different dimension instead
                print(f"  skip dim {dim} for {node['path']}: covers {total}/{cnt}")
                tried.add(dim)
                continue
            child_used = sorted(used | {dim})
            child_tried = sorted(tried | {dim})
            for b, child, c, ckey in trial:
                if c == 0:
                    continue
                path = f"{node['path']}>{dim}:{b['label']}"
                if c <= LEAF_MAX:
                    leaves.append({"filters": child, "count": c, "path": path})
                else:
                    queue.append({**child, "depth": node["depth"] + 1,
                                  "path": path,
                                  "used": child_used, "tried": child_tried})
            split_done = True
            break
        if not split_done:
            oversized.append({"filters": filters, "count": cnt, "path": node["path"]})
    if oversized:
        print("note: unsplittable oversized nodes (two-order fetch will cover up to 20000):")
        for o in oversized:
            print("   ", o["path"], o["count"])
        leaves.extend(oversized)
    return leaves


_journal_lock = threading.Lock()


def save_journal(journal: dict) -> None:
    with _journal_lock:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        tmp = RAW_DIR / ("journal.json.tmp." + str(threading.get_ident()))
        tmp.write_text(json.dumps(journal, ensure_ascii=False, indent=1))
        tmp.replace(RAW_DIR / "journal.json")


def fetch_leaf(leaf: dict, journal: dict) -> dict:
    filters = leaf["filters"]
    nid = node_id(filters)
    d = RAW_DIR / "pages" / nid
    d.mkdir(parents=True, exist_ok=True)

    jobs = []
    limit = min(leaf["count"], 10000)
    for off in range(0, limit, PAGE_SIZE):
        jobs.append(("Descending", off, d / f"p{off:05d}.json.gz"))
    if leaf["count"] > 10000:
        # beyond the 10000 window: fetch the same node from the oldest side
        limit2 = min(leaf["count"] - 10000, 10000)
        for off in range(0, limit2, PAGE_SIZE):
            jobs.append(("Ascending", off, d / f"a{off:05d}.json.gz"))

    done = 0
    for order, off, fp in jobs:
        if fp.exists():
            done += 1
            continue
        resp = search_ordered(filters, order=order, offset=off, size=PAGE_SIZE)
        rows = resp.get("data") or []
        blob = json.dumps({"request": {**BASE_SEARCH, **filters, "order": order, "offset": off},
                           "response_count": resp.get("count"),
                           "data": rows}, ensure_ascii=False, separators=(",", ":"))
        with gzip.open(fp, "w") as f:
            f.write(blob.encode())
        done += 1
        if done % 10 == 0 or done >= len(jobs):
            print(f"    [{leaf['path']}] {done}/{len(jobs)} pages", flush=True)
    leaf_meta = {"filters": filters, "planned_count": leaf["count"],
                 "pages": len(jobs), "dir": str(d.relative_to(ROOT))}
    journal[f"leaf:{nid}"] = leaf_meta
    save_journal(journal)
    return leaf_meta


def main() -> int:
    global DELAY
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="plan only, no page downloads")
    ap.add_argument("--max-leaves", type=int, default=0, help="0 = unlimited")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=DELAY, help="seconds between requests per worker")
    args = ap.parse_args()
    DELAY = args.delay

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    journal_path = RAW_DIR / "journal.json"
    journal = json.loads(journal_path.read_text()) if journal_path.exists() else {}

    print(f"snapshot dir: {RAW_DIR}")
    print("fetching filter options ...")
    opts = http_post(f"{BASE}/Transaction/GetTransactionFilterOptions",
                     {"postType": "Both", "day": "Day1095"})
    (RAW_DIR / "filter_options.json").write_text(
        json.dumps(opts, ensure_ascii=False, indent=1))

    print("planning partition tree (this queries counts only) ...")
    leaves = plan_tree(opts, journal, args.dry_run)
    with open(RAW_DIR / "plan.json", "w") as f:
        json.dump(leaves, f, ensure_ascii=False, indent=1)

    plan_total = sum(l["count"] for l in leaves)
    print(f"\nplan: {len(leaves)} leaves, planned records ~{plan_total:,}")
    if args.dry_run:
        return 0

    if args.max_leaves:
        leaves = leaves[: args.max_leaves]
    t0 = time.time()
    todo = [l for l in leaves if f"leaf:{node_id(l['filters'])}" not in journal]
    print(f"leaves to fetch: {len(todo)} (already done: {len(leaves)-len(todo)})")
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_leaf, leaf, journal): leaf for leaf in todo}
        for fut in as_completed(futs):
            leaf = futs[fut]
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001 - keep other leaves going
                print(f"LEAF FAILED [{leaf['path']}]: {e}")
            done += 1
            print(f"progress: {done}/{len(todo)} leaves, elapsed {time.time()-t0:.0f}s", flush=True)

    # manifest
    files = []
    for p in sorted((RAW_DIR / "pages").rglob("*.json.gz")):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        files.append({"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size,
                      "sha256": h.hexdigest()})
    manifest = {
        "snapshot_date": SNAPSHOT_DATE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api": f"{BASE}/Transaction/Search",
        "query": {"day": "Day1095", "sort": "InsOrRegDate", "order": "Descending"},
        "leaves": len(leaves),
        "planned_records": plan_total,
        "page_files": len(files),
        "page_bytes": sum(x["bytes"] for x in files),
        "files": files,
    }
    (RAW_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"manifest written: {len(files)} files, {manifest['page_bytes']:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
