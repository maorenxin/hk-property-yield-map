#!/usr/bin/env python3
"""Collect current 28Hse rental listings for buildings already mapped to 28Hse.

This is an asking-rent supplement, not transaction history.  It reads the
estate pages cached by verify_building_age_28hse.py, follows the `c<id>` rent
link, and stores one summary row per building group.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "data/raw/28hse/2026-10-18/pages"
OUT = ROOT / "data/derived/28hse_rent_listings.csv"
RENT_CACHE = ROOT / "data/raw/28hse/2026-10-18/rent_listing_pages"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def parse_estate(text: str):
    m = re.search(r'https://www\.28hse\.com/rent/[^"\']+/c(\d+)', text)
    if not m:
        return None, 0
    url = m.group(0).replace("&amp;", "&")
    c = re.search(r"共有\s*(\d+)\s*個放租樓盤|(\d+)\s*個\s*租盤", text)
    n = int((c.group(1) or c.group(2))) if c else 0
    return url, n


def parse_listings(text: str):
    t = htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))
    out = []
    for m in re.finditer(r"租\s*\$([\d,]+)\s*元", t):
        chunk = t[max(0, m.start() - 300):m.start() + 450]
        rent = float(m.group(1).replace(",", ""))
        area = re.search(r"實用面積:\s*([\d,]+)\s*呎", chunk)
        psf = re.search(r"@\s*([\d.]+)\s*元", chunk)
        unit = re.search(r"\|?\s*([^|]{1,30}?(?:高層|中層|低層)\s*[A-Z0-9]+室)", chunk)
        out.append({"rent_hkd": rent,
                    "area_sqft": float(area.group(1).replace(",", "")) if area else None,
                    "psf": float(psf.group(1)) if psf else None,
                    "unit": unit.group(1).strip() if unit else None})
    # de-dupe identical rent/area rows on the page
    seen = set(); uniq = []
    for x in out:
        key = (x["rent_hkd"], x["area_sqft"], x["unit"])
        if key not in seen:
            seen.add(key); uniq.append(x)
    return uniq


def main() -> int:
    d = pd.read_csv(ROOT / "data/derived/building_age_28hse.csv")
    # Manual address-verified override for the Hung Hom building shown in the UI.
    manual = pd.DataFrame([{
        "hma_id": "HMA090", "bldg_grp_id": "KC0135", "building_name": "興利大廈",
        "address_raw": "老龍坑街12號", "estate_slug": "興利大廈-22863",
    }])
    d = pd.concat([d, manual], ignore_index=True)
    d = d.dropna(subset=["estate_slug"]).drop_duplicates("estate_slug")
    rows = []
    for i, r in enumerate(d.itertuples(), 1):
        key = hashlib.sha1(str(r.estate_slug).encode()).hexdigest()[:12]
        fp = PAGES / f"{key}.html"
        if not fp.exists():
            continue
        estate_text = fp.read_text(encoding="utf-8", errors="ignore")
        rent_url, n = parse_estate(estate_text)
        if not rent_url or n <= 0:
            continue
        try:
            RENT_CACHE.mkdir(parents=True, exist_ok=True)
            rfp = RENT_CACHE / (hashlib.sha1(rent_url.encode()).hexdigest()[:16] + ".html")
            if rfp.exists() and rfp.stat().st_size > 5000:
                page = rfp.read_text(encoding="utf-8", errors="ignore")
            else:
                rr = requests.get(rent_url, headers={"User-Agent": UA}, timeout=30)
                if rr.status_code != 200:
                    continue
                page = rr.text
                rfp.write_text(page, encoding="utf-8")
                time.sleep(0.25)
            listings = parse_listings(page)
        except Exception:
            continue
        if not listings:
            continue
        rents = sorted(x["rent_hkd"] for x in listings)
        psfs = sorted(x["psf"] for x in listings if x["psf"])
        rows.append({
            "hma_id": r.hma_id, "bldg_grp_id": r.bldg_grp_id,
            "building_name": r.building_name, "address_raw": r.address_raw,
            "estate_slug": r.estate_slug, "rent_url": rent_url,
            "listing_count": len(listings),
            "rent_median_hkd": rents[len(rents) // 2],
            "psf_median": psfs[len(psfs) // 2] if psfs else None,
            "listings_json": json.dumps(listings, ensure_ascii=False, separators=(",", ":")),
        })
        if i % 50 == 0:
            pd.DataFrame(rows).to_csv(OUT, index=False)
            print(f"{i}/{len(d)} -> {len(rows)} rent listings", flush=True)
        time.sleep(0.18)
    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(ROOT)} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
