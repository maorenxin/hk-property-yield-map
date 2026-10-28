#!/usr/bin/env python3
"""Normalize the raw Centaline transaction pages into one tidy table.

Reads every gzipped page under
data/raw/centaline/transaction_search/<date>/pages/**/*.json.gz,
dedupes by transaction id (page overlap from boundary-inclusive partition
buckets), and writes a normalized parquet + audit summary.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "2026-10-16"
RAW = ROOT / "data/raw/centaline/transaction_search" / SNAPSHOT
OUT = ROOT / "data/normalized/centaline"
PARSER_VERSION = "centaline-tx-parser-3"

NUM = ["transactionPrice", "gArea", "nArea", "gUnitPrice", "nUnitPrice",
       "bedroomCount"]


def parse_record(r: dict) -> dict:
    scope = r.get("scope") or {}
    bg = r.get("bldgGrp") or {}
    post = r.get("postType")  # "S" | "R"
    price = r.get("transactionPrice")
    n_area = r.get("nArea")
    n_unit_price = r.get("nUnitPrice")
    ins = r.get("insDate")
    reg = r.get("regDate")
    registration_date = str(reg)[:10] if reg else None
    m = str(r.get("oldTransactionID") or "")[:8]
    if not registration_date and len(m) == 8 and m.isdigit():
        registration_date = f"{m[:4]}-{m[4:6]}-{m[6:]}"
    deal_date = str(ins)[:10] if ins else None
    if deal_date:
        try:
            y = int(deal_date[:4])
            if y < 1990 or y > 2030:
                deal_date = None
        except Exception:
            deal_date = None
    tx_date = registration_date or deal_date
    analysis_date = deal_date or registration_date
    return {
        "source": "centaline",
        "source_url": r.get("detailUrl"),
        "source_record_id": r.get("id"),
        "retrieved_at": SNAPSHOT,
        "published_at": (str(ins)[:10] if ins else None),
        "transaction_date": tx_date,
        "registration_date": registration_date,
        "deal_date": deal_date,
        "analysis_date": analysis_date,
        "listing_type": ("sale" if post == "S" else "rent" if post == "R" else None),
        "transaction_type": "transaction",
        "data_source": r.get("dataSource"),                       # AC | Land
        "first_or_second_hand": r.get("firstOrSecondHand"),
        "estate_usage": (scope.get("hma") and None) or None,
        "region_terr": scope.get("terr"),                          # 港島/九龍/新界東/新界西
        "scp_mkt": scope.get("scp_mkt"),
        "district": scope.get("db"),                               # e.g. 北區
        "web_scope": scope.get("webScope"),
        "web_scope_id": scope.get("webScopeID"),
        "hma_id": scope.get("hma_id"),
        "hma": scope.get("hma"),
        "district_name": r.get("districtName"),
        "estate_name": r.get("bigEstateName") or r.get("estateName"),
        "phase_name": r.get("estateName"),
        "building_name": r.get("buildingName"),
        "floor": r.get("yAxis"),
        "unit": r.get("xAxis"),
        "address_raw": r.get("address"),
        "display_line": ((r.get("displayText") or {}).get("addr") or {}).get("line5"),
        "estate_type": r.get("estateType"),
        "unit_type": r.get("unitType"),
        "bedrooms": r.get("bedroomCount"),
        "direction": r.get("direction"),
        "op_year": r.get("opYear"),
        "saleable_area_sqft": n_area,
        "gross_area_sqft": r.get("gArea"),
        "price_hkd": price,
        "saleable_psf": n_unit_price,
        "gross_psf": r.get("gUnitPrice"),
        "rent_monthly_hkd": price if post == "R" else None,
        "sale_price_hkd": price if post == "S" else None,
        "type_code": r.get("typeCode"),
        "cuntcode": r.get("cuntcode"),
        "bldg_grp_id": bg.get("bldgGrpId"),
        "bldg_grp_name": bg.get("bldgGrpName"),
        "special_case": bool((r.get("specialCase") or {}).get("value")),
        "image_url": ((r.get("picture") or {}).get("thumbnailPath")),
        "image_type": ((r.get("picture") or {}).get("thumbnailType")),
        "raw_payload_hash": hashlib.sha1(
            json.dumps(r, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16],
        "parser_version": PARSER_VERSION,
    }


def main() -> int:
    pages = sorted((RAW / "pages").rglob("*.json.gz"))
    print(f"page files: {len(pages)}")
    rows = []
    dup = 0
    seen = set()
    for p in pages:
        with gzip.open(p, "rt") as f:
            blob = json.load(f)
        for r in blob.get("data") or []:
            rid = r.get("id")
            if rid in seen:
                dup += 1
                continue
            seen.add(rid)
            rows.append(parse_record(r))
    df = pd.DataFrame(rows)
    for c in ["transaction_date", "registration_date", "deal_date", "analysis_date"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ["saleable_area_sqft", "gross_area_sqft", "price_hkd", "saleable_psf",
              "gross_psf", "rent_monthly_hkd", "sale_price_hkd", "bedrooms"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values(["analysis_date", "source_record_id"]).reset_index(drop=True)

    OUT.mkdir(parents=True, exist_ok=True)
    pq = OUT / f"transactions_{SNAPSHOT}.parquet"
    df.to_parquet(pq, index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": SNAPSHOT,
        "pages": len(pages),
        "records": len(df),
        "duplicates_dropped": dup,
        "by_listing_type": df["listing_type"].value_counts().to_dict(),
        "by_data_source": df["data_source"].value_counts().to_dict(),
        "by_first_or_second": df["first_or_second_hand"].value_counts().to_dict(),
        "by_estate_usage_region": df["region_terr"].value_counts().to_dict(),
        "date_min": str(df["analysis_date"].min().date()),
        "date_max": str(df["analysis_date"].max().date()),
        "registration_date_min": str(df["registration_date"].min().date()),
        "registration_date_max": str(df["registration_date"].max().date()),
        "distinct_estates": int(df["estate_name"].nunique()),
        "distinct_bldg_grp": int(df["bldg_grp_id"].nunique()),
        "null_rates": {c: round(float(df[c].isna().mean()), 4) for c in
                       ["saleable_area_sqft", "saleable_psf", "bedrooms", "bldg_grp_id",
                        "rent_monthly_hkd", "sale_price_hkd"]},
        "output": str(pq.relative_to(ROOT)),
    }
    (OUT / f"transactions_{SNAPSHOT}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
