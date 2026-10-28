#!/usr/bin/env python3
"""Third-party building-age verification for buildings unmatched by RVD (60%).

Uses 28Hse estate detail pages (sitemap-derived), which publish 入伙年份.
Matches only exact estate-slug ↔ our building-name (street-number towers like
洋松街69號, named buildings like 興發樓). Cached HTML; resume-safe.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import hashlib
import html as htmllib
import json
import re
import time
import urllib.parse
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/28hse/2026-10-18"
PAGES = RAW / "pages"
OUT = ROOT / "data/derived/building_age_28hse.csv"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def norm(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).replace("(", "（").replace(")", "）")


def load_slugs() -> dict[str, str]:
    urls = []
    for i in (1, 2, 3):
        p = RAW / f"sitemap{i}.xml.gz"
        urls += re.findall(r"<loc>([^<]+)</loc>", gzip.open(p).read().decode("utf-8", "ignore"))
    out = {}
    for u in urls:
        if "/estate/detail/" in u:
            slug = urllib.parse.unquote(u.split("/estate/detail/")[1])
            name = re.sub(r"-\d+$", "", slug)
            out.setdefault(norm(name), slug)
    return out


def fetch(slug: str):
    key = hashlib.sha1(slug.encode()).hexdigest()[:12]
    fp = PAGES / f"{key}.html"
    if fp.exists() and fp.stat().st_size > 5000:
        return slug, fp, None
    url = "https://www.28hse.com/estate/detail/" + urllib.parse.quote(slug)
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            if r.status_code == 200 and len(r.content) > 5000:
                fp.write_bytes(r.content)
                return slug, fp, None
            if r.status_code in (403, 404):
                return slug, None, f"http {r.status_code}"
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                return slug, None, f"err {type(e).__name__}"
        time.sleep(0.4 + attempt * 0.7)
    return slug, None, "fail"


def parse_year(fp: Path):
    s = fp.read_text(encoding="utf-8", errors="ignore")
    t = htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)))
    m = re.search(r"入伙年份[:：]\s*(\d{4})\s*年", t)
    year = int(m.group(1)) if m else None
    m2 = re.search(r"物業座數[:：]\s*(\d+)", t)
    blocks = int(m2.group(1)) if m2 else None
    addr = ""
    m3 = re.search(r"位於[^，。]{0,30}?([^，。]{4,40}號)", t)
    if m3:
        addr = m3.group(1)
    return year, blocks, addr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true", help="skip fetching; parse cached pages only")
    args = ap.parse_args()
    PAGES.mkdir(parents=True, exist_ok=True)
    slugs = load_slugs()
    d = pd.read_csv(ROOT / "data/derived/building_age_rvd.csv")
    un = d[d.match.eq("none")].copy()
    un["nk"] = un.building_name.map(norm)
    un = un[un.nk.isin(slugs)]
    todo = sorted({slugs[k] for k in un.nk})
    print(f"28Hse slug index: {len(slugs)} | unmatched buildings with slug: {len(un)} | pages to fetch: {len(todo)}")

    got = {}
    fails = {}
    if args.from_cache:
        for slug in todo:
            key = hashlib.sha1(slug.encode()).hexdigest()[:12]
            fp = PAGES / f"{key}.html"
            if fp.exists() and fp.stat().st_size > 5000:
                got[slug] = parse_year(fp)
    else:
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            for slug, fp, err in ex.map(fetch, todo, chunksize=8):
                if fp:
                    got[slug] = parse_year(fp)
                else:
                    fails[slug] = err
                if (len(got) + len(fails)) % 200 == 0:
                    print(f"progress {len(got)+len(fails)}/{len(todo)}", flush=True)
    print(f"parsed {len(got)} pages, failed {len(fails)}")

    rows = []
    for r in un.itertuples():
        slug = slugs.get(r.nk)
        if not slug or slug not in got:
            continue
        year, blocks, addr = got[slug]
        if year is None:
            continue
        rows.append({
            "hma_id": r.hma_id, "bldg_grp_id": r.bldg_grp_id,
            "building_name": r.building_name, "address_raw": r.address_raw,
            "op_year_centaline": r.op_year_centaline, "op_year_28hse": year,
            "estate_slug": slug, "blocks": blocks,
            "source_url": "https://www.28hse.com/estate/detail/" + urllib.parse.quote(slug),
        })
    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    both = out.dropna(subset=["op_year_centaline"])
    delta = both.op_year_28hse - both.op_year_centaline
    print(f"buildings with 28Hse year: {len(out)} | both years: {len(both)} | exact {(delta==0).sum()} | |d|>2 {(delta.abs()>2).sum()}")
    if len(out):
        print(out.head(5).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
