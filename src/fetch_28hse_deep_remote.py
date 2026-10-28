#!/usr/bin/env python3
"""Fetch missing deep 28Hse rental-history pages through a remote egress.

28Hse starts returning 403 on the local Mac after page 100.  The GPU host gets
200, so this helper fetches only pages that are not already cached.  It is
resumable and never overwrites a cached page.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from collect_28hse_rent_history import RAW, parse_page

ROOT = Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssh-host", default="gpu")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--delay", type=float, default=0.8, help="seconds between requests per worker")
    ap.add_argument("--cooldown", type=float, default=60, help="seconds to pause after HTTP 429")
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--pages", type=int, default=1000)
    args = ap.parse_args()
    slugs = sorted({p.name[:-len("_p1.html")] for p in RAW.glob("*_p1.html") if p.name.endswith("_p1.html")})
    tasks = []
    for slug in slugs:
        fp1 = RAW / (slug.replace("/", "_") + "_p1.html")
        if not fp1.exists():
            continue
        _, max_page = parse_page(fp1.read_text(encoding="utf-8", errors="ignore"), slug)
        for page in range(2, min(max_page, args.pages) + 1):
            fp = RAW / (slug.replace("/", "_") + f"_p{page}.html")
            if fp.exists() and fp.stat().st_size > 1000:
                continue
            url = ("https://www.28hse.com/estate/detail/" + quote(slug)
                   + "/transaction/rent/page-" + str(page))
            tasks.append((slug, page, fp, url))
    # Fetch short estates first; a 429 then cannot starve every other estate.
    totals = {}
    for slug, _, _, _ in tasks:
        totals[slug] = totals.get(slug, 0) + 1
    tasks.sort(key=lambda t: (totals[t[0]], t[0], t[1]))
    print(f"deep pages to fetch: {len(tasks)}; workers={args.workers}", flush=True)

    def one(task):
        slug, page, fp, url = task
        last = ""
        for attempt in range(max(1, args.max_retries)):
            try:
                cmd = "curl -sS -f --compressed -A '" + UA + "' '" + url + "'"
                p = subprocess.run(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", args.ssh_host, cmd],
                    capture_output=True, text=True, timeout=90,
                )
                if p.returncode == 0 and len(p.stdout) > 1000 and "<html" in p.stdout[:500].lower():
                    fp.write_text(p.stdout, encoding="utf-8")
                    return "ok", slug, page, len(p.stdout)
                last = f"rc={p.returncode} {p.stderr[:100]}"
                if "429" in p.stderr or "429" in p.stdout:
                    if attempt + 1 < max(1, args.max_retries):
                        time.sleep(args.cooldown)
                        continue
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
            time.sleep(1.0 + attempt * 2.0)
        return "fail", slug, page, last
        return "fail", slug, page, last

    ok = failed = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for i, (status, slug, page, info) in enumerate(ex.map(one, tasks), 1):
            if status == "ok":
                ok += 1
            else:
                failed += 1
                print(f"FAIL {slug} p{page}: {info}", flush=True)
            if i % 25 == 0 or i == len(tasks):
                print(f"progress {i}/{len(tasks)} ok={ok} failed={failed}", flush=True)
            time.sleep(args.delay)
    print(f"done: ok={ok} failed={failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
