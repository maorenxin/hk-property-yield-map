#!/usr/bin/env python3
"""Collect Centaline's full public rental history by month.

The building-group backfill only covers records that already carry a
`bldgGrpId`.  New developments often have no group id, so this collector
queries the same public endpoint by month (postType=Rent) and keeps every
record, including null-group ones.  Pages are immutable, gzipped, hashed and
resumable.  The default pace is deliberately conservative (one request every
1.2 s, one worker) because the upstream API starts returning 429 when hammered.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/centaline/rent_full/2026-10-18"
PAGES = OUT / "pages"
MANIFEST = OUT / "manifest.csv"
SUMMARY = OUT / "summary.json"
URL = "https://hk-api.centanet.com/centanetapi/api/Transaction/Search"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Origin": "https://hk.centanet.com",
    "Referer": "https://hk.centanet.com/",
    "Connection": "close",
}


def month_iter(start: str, end: str):
    d = pd.Timestamp(start).date().replace(day=1)
    e = pd.Timestamp(end).date().replace(day=1)
    while d <= e:
        nxt = (d.replace(day=28) + pd.Timedelta(days=4)).replace(day=1)
        yield d, nxt
        d = nxt


def body(month_start: date, month_end: date, offset: int, size: int) -> dict:
    return {
        "day": "",
        "sort": "InsOrRegDate",
        "order": "Ascending",
        "offset": offset,
        "size": size,
        "estateUsages": ["RE", "HOS"],
        "postType": "Rent",
        "insOrRegDateTimeRange": {"start": str(month_start), "end": str(month_end)},
    }


def request_json(session: requests.Session, payload: dict, proxies, timeout: int = 60):
    last = None
    for attempt in range(6):
        try:
            r = session.post(URL, json=payload, headers=HEADERS, timeout=timeout, proxies=proxies)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code == 429:
                wait = (60, 120, 300)[min(attempt, 2)]
                print(f"429 pause {wait}s", flush=True)
                time.sleep(wait)
            else:
                time.sleep(3 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(last)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01")
    ap.add_argument("--end", default="2026-10")
    ap.add_argument("--pace", type=float, default=1.2, help="seconds between requests")
    ap.add_argument("--max-pages", type=int, default=0, help="stop after N page requests (0 = all)")
    ap.add_argument("--proxy", default="", help="e.g. socks5h://127.0.0.1:1081")
    ap.add_argument("--limit-month", default="", help="optional YYYY-MM for a small test")
    args = ap.parse_args()
    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
    PAGES.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    rows, ok, cached, failed = [], 0, 0, 0
    total_pages = 0
    stopped_429 = False
    for ms, me in month_iter(args.start, args.end):
        key = str(ms)[:7]
        if args.limit_month and key != args.limit_month:
            continue
        # count first
        try:
            r = request_json(session, body(ms, me, 0, 1), proxies)
        except Exception as exc:  # noqa: BLE001
            print(f"{key} count failed: {exc}", flush=True)
            failed += 1
            stopped_429 = "429" in str(exc)
            if stopped_429:
                break
            continue
        count = int(r.json().get("count") or 0)
        pages = (count + 99) // 100
        print(f"{key} count={count} pages={pages}", flush=True)
        for page_no in range(pages):
            if args.max_pages and total_pages >= args.max_pages:
                break
            off = page_no * 100
            fp = PAGES / key / f"p{off:05d}.json.gz"
            if fp.exists() and fp.stat().st_size > 100:
                cached += 1
                total_pages += 1
                continue
            try:
                r = request_json(session, body(ms, me, off, 100), proxies)
                d = r.json()
                blob = json.dumps(
                    {"request": body(ms, me, off, 100), "response_count": d.get("count"),
                     "retrieved_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
                     "data": d.get("data") or []},
                    ensure_ascii=False, separators=(",", ":"))
                fp.parent.mkdir(parents=True, exist_ok=True)
                raw = blob.encode("utf-8")
                with gzip.open(fp, "wb") as f:
                    f.write(raw)
                rows.append({"month": key, "offset": off, "count": count,
                             "status": "ok", "bytes": len(raw),
                             "sha256": hashlib.sha256(raw).hexdigest(), "file": str(fp.relative_to(ROOT))})
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                rows.append({"month": key, "offset": off, "count": count,
                             "status": "failed", "error": str(exc)[:300], "file": ""})
                print(f"FAIL {key} p{off}: {exc}", flush=True)
                if "429" in str(exc):
                    stopped_429 = True
                    break
            total_pages += 1
            if total_pages % 25 == 0:
                pd.DataFrame(rows).to_csv(MANIFEST, index=False)
                print(f"progress pages={total_pages} ok={ok} cached={cached} failed={failed}", flush=True)
            if args.pace:
                time.sleep(args.pace)
        if args.max_pages and total_pages >= args.max_pages:
            break
        if stopped_429:
            break
    pd.DataFrame(rows).to_csv(MANIFEST, index=False)
    summary = {"generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
               "months": [str(x)[:7] for x in pd.period_range(args.start, args.end, freq="M")],
               "pages_ok": ok, "pages_cached": cached, "pages_failed": failed,
               "stopped_429": stopped_429, "summary": str(SUMMARY.relative_to(ROOT))}
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
    if stopped_429:
        return 2
    return 3 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
