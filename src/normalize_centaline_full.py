#!/usr/bin/env python3
"""Normalize current 3-year pages + full-history group backfill into one table."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from normalize_centaline_transactions import parse_record

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "data/raw/centaline/transaction_search/2026-10-16/pages"
FULL = ROOT / "data/raw/centaline/transaction_search_full/2026-10-18/pages"
RENT_FULL = ROOT / "data/raw/centaline/rent_full/2026-10-18/pages"
OUT = ROOT / "data/normalized/centaline/transactions_full_2026-10-18.parquet"
SUMMARY = ROOT / "data/normalized/centaline/transactions_full_2026-10-18.summary.json"


def main() -> int:
    pages = (sorted(CURRENT.rglob("*.json.gz"))
             + sorted(FULL.rglob("*.json.gz"))
             + sorted(RENT_FULL.rglob("*.json.gz")) if RENT_FULL.exists()
             else sorted(CURRENT.rglob("*.json.gz")) + sorted(FULL.rglob("*.json.gz")))
    print(f"page files: {len(pages):,}")
    rows = []
    seen = set()
    dup = 0
    for p in pages:
        try:
            with gzip.open(p, "rt") as f:
                blob = json.load(f)
        except Exception:
            continue
        retrieved_at = blob.get("retrieved_at")
        if not retrieved_at:
            retrieved_at = datetime.fromtimestamp(
                p.stat().st_mtime, tz=timezone.utc
            ).astimezone(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds")
        for r in blob.get("data") or []:
            rid = r.get("id")
            if rid in seen:
                dup += 1
                continue
            seen.add(rid)
            row = parse_record(r)
            row["retrieved_at"] = retrieved_at
            rows.append(row)
    df = pd.DataFrame(rows)
    for c in ["transaction_date", "registration_date", "deal_date", "analysis_date"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ["saleable_area_sqft", "gross_area_sqft", "price_hkd", "saleable_psf",
              "gross_psf", "rent_monthly_hkd", "sale_price_hkd", "bedrooms"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values(["analysis_date", "source_record_id"]).reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pages": len(pages),
        "records": len(df),
        "duplicates_dropped": dup,
        "by_listing_type": df.listing_type.value_counts().to_dict(),
        "by_data_source": df.data_source.value_counts().to_dict(),
        "date_min": str(df.analysis_date.min().date()),
        "date_max": str(df.analysis_date.max().date()),
        "registration_date_min": str(df.registration_date.min().date()),
        "registration_date_max": str(df.registration_date.max().date()),
        "distinct_estates": int(df.estate_name.nunique()),
        "distinct_bldg_grp": int(df.bldg_grp_id.nunique()),
        "output": str(OUT.relative_to(ROOT)),
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
