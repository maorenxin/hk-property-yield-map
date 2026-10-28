from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "centaline"
DERIVED = ROOT / "data" / "derived"
META = RAW
API_URL = "https://hk-api.centanet.com/centanetapi/api/Transaction/Search"
GENERATED_AT = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")

DISTRICT_MAP = {
    "中西區": "Central and Western",
    "灣仔區": "Wan Chai",
    "東區": "Eastern",
    "南區": "Southern",
    "九龍城區": "Kowloon City",
    "觀塘區": "Kwun Tong",
    "深水埗區": "Sham Shui Po",
    "黃大仙區": "Wong Tai Sin",
    "油尖旺區": "Yau Tsim Mong",
    "離島區": "Islands",
    "葵青區": "Kwai Tsing",
    "華景 | 荔灣 (葵青區)": "Kwai Tsing",
    "北區": "North",
    "西貢區": "Sai Kung",
    "將軍澳 (西貢區)": "Sai Kung",
    "沙田區": "Sha Tin",
    "大埔區": "Tai Po",
    "荃灣區": "Tsuen Wan",
    "屯門區": "Tuen Mun",
    "元朗區": "Yuen Long",
}
AREA_BINS = [0, 400, 600, 800, 1200, np.inf]
AREA_LABELS = ["<400", "400-599", "600-799", "800-1199", "1200+"]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_records(kind: str) -> pd.DataFrame:
    p = RAW / f"{kind}_sample_1000.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = []
    for x in d["records"]:
        scope = x.get("scope") or {}
        rows.append({
            "source_record_id": x.get("id"),
            "district_raw": scope.get("db"),
            "district": DISTRICT_MAP.get(scope.get("db")),
            "hma": scope.get("hma"),
            "estate_name": x.get("bigEstateName") or x.get("estateName"),
            "address_raw": (x.get("displayText") or {}).get("addr", {}).get("line5"),
            "transaction_price_hkd": x.get("transactionPrice"),
            "saleable_area_sqft": x.get("nArea"),
            "unit_price_hkd_sqft_published": x.get("nUnitPrice"),
            "ins_date": pd.to_datetime(x.get("insDate")),
            "post_type": x.get("postType"),
            "source_url": API_URL,
            "detail_url": x.get("detailUrl"),
            "bedroom_count": x.get("bedroomCount"),
            "data_source": x.get("dataSource"),
            "first_or_second_hand": x.get("firstOrSecondHand"),
            "op_year": x.get("opYear"),
        })
    out = pd.DataFrame(rows)
    out["month"] = out["ins_date"].dt.to_period("M").dt.to_timestamp()
    out["size_band"] = pd.cut(out["saleable_area_sqft"], bins=AREA_BINS, labels=AREA_LABELS, right=False)
    out["unit_price_hkd_sqft_calc"] = out["transaction_price_hkd"] / out["saleable_area_sqft"]
    out = out[
        out["district"].notna()
        & out["transaction_price_hkd"].gt(0)
        & out["saleable_area_sqft"].gt(0)
    ].copy()
    return out


def main():
    sale = load_records("sale")
    rent = load_records("rent")
    sale.to_csv(DERIVED / "centaline_sale_sample_normalized.csv", index=False, date_format="%Y-%m-%d")
    rent.to_csv(DERIVED / "centaline_rent_sample_normalized.csv", index=False, date_format="%Y-%m-%d")

    keys = ["district", "size_band", "month"]
    sale_cell = sale.groupby(keys, observed=True).agg(
        sale_price_hkd=("transaction_price_hkd", "median"),
        sale_price_hkd_sqft=("unit_price_hkd_sqft_calc", "median"),
        sale_n=("source_record_id", "count"),
    ).reset_index()
    rent_cell = rent.groupby(keys, observed=True).agg(
        rent_monthly_hkd=("transaction_price_hkd", "median"),
        rent_monthly_hkd_sqft=("unit_price_hkd_sqft_calc", "median"),
        rent_n=("source_record_id", "count"),
    ).reset_index()
    cells = sale_cell.merge(rent_cell, on=keys, how="inner")
    cells["gross_yield_pct"] = cells["rent_monthly_hkd_sqft"] * 12 / cells["sale_price_hkd_sqft"] * 100
    cells["price_to_rent_years"] = cells["sale_price_hkd_sqft"] / (cells["rent_monthly_hkd_sqft"] * 12)
    cells["pair_quality"] = np.where((cells["sale_n"] >= 5) & (cells["rent_n"] >= 5), "cell_n>=5_each", "low_sample")
    cells.to_csv(DERIVED / "centaline_matched_cells.csv", index=False, date_format="%Y-%m-%d")

    district = cells.groupby("district", as_index=False).agg(
        gross_yield_pct=("gross_yield_pct", "median"),
        price_to_rent_years=("price_to_rent_years", "median"),
        sale_price_hkd_sqft=("sale_price_hkd_sqft", "median"),
        rent_monthly_hkd_sqft=("rent_monthly_hkd_sqft", "median"),
        matched_cells=("gross_yield_pct", "count"),
        sale_records=("sale_n", "sum"),
        rent_records=("rent_n", "sum"),
        period_start=("month", "min"),
        period_end=("month", "max"),
    )
    district["source"] = "Centaline Transaction Search API; latest 1000 sale + latest 1000 rent sample"
    district["metric_definition"] = "median of same district + saleable-area band + month matched cells; HKD/ft²"
    district["quality"] = np.where(district["matched_cells"] >= 3, "sample_snapshot", "low_sample")
    district.to_csv(DERIVED / "centaline_district_ratio.csv", index=False, date_format="%Y-%m-%d")

    manifest = {
        "generated_at": GENERATED_AT,
        "api_url": API_URL,
        "request_parameters": {
            "postType": ["Sale", "Rent"],
            "day": "Day1095",
            "sort": "InsOrRegDate",
            "order": "Descending",
            "size_per_request": 100,
            "offsets": list(range(0, 1000, 100)),
        },
        "samples": {
            "sale": {"rows": int(len(sale)), "min_date": sale["ins_date"].min().isoformat(), "max_date": sale["ins_date"].max().isoformat()},
            "rent": {"rows": int(len(rent)), "min_date": rent["ins_date"].min().isoformat(), "max_date": rent["ins_date"].max().isoformat()},
        },
        "input_files": {
            p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
            for p in sorted(RAW.glob("*_sample_1000.json"))
        },
        "output_files": {
            p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
            for p in sorted(DERIVED.glob("centaline_*"))
        },
        "districts": int(district["district"].nunique()) if "district" in district else int(district["district"].nunique()),
        "matched_cells": int(len(cells)),
    }
    (META / "detail_sample_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({
        "sale_rows": len(sale), "rent_rows": len(rent),
        "districts": int(district["district"].nunique()),
        "matched_cells": len(cells),
        "latest": district[["district", "gross_yield_pct", "matched_cells"]].to_dict(orient="records"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

