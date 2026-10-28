#!/usr/bin/env python3
"""Refresh the Hong Kong rolling transaction corpus for a daily GitHub Action.

The public Centaline search endpoint exposes a rolling 30-day window.  This
collector splits that window into small date slices, stores every API page as
an immutable gzip snapshot, normalizes the pages through the same parser used
by the historical pipeline, and merges the records into a canonical Parquet
table.

The script is intentionally conservative: one worker and a minimum pause
between requests.  A daily run therefore takes a few minutes instead of
risking an upstream 429 block.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from normalize_centaline_transactions import parse_record

ROOT = Path(__file__).resolve().parents[1]
URL = "https://hk-api.centanet.com/centanetapi/api/Transaction/Search"
DEFAULT_BASE = ROOT / "data/normalized/centaline/transactions_full_2026-10-18.parquet"
LATEST_PARQUET = ROOT / "data/normalized/centaline/transactions_full_latest.parquet"
LATEST_SUMMARY = ROOT / "data/normalized/centaline/transactions_full_latest.summary.json"
RAW_ROOT = ROOT / "data/raw/centaline/daily"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": USER_AGENT,
    "Origin": "https://hk.centanet.com",
    "Referer": "https://hk.centanet.com/",
    "Connection": "close",
}
PAGE_SIZE = 100
RETRIES = 5


def hk_today() -> date:
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).date()


def request_json(session: requests.Session, payload: dict, timeout: int = 60) -> dict:
    last_error = None
    for attempt in range(RETRIES):
        try:
            response = session.post(URL, json=payload, headers=HEADERS, timeout=timeout)
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}: {response.text[:240]}"
            if response.status_code == 429:
                waits = (60, 120, 300, 600, 900)
                wait = waits[min(attempt, len(waits) - 1)]
                print(f"429 from Centaline; backing off {wait}s", flush=True)
                time.sleep(wait)
            elif 500 <= response.status_code < 600:
                time.sleep(10 * (attempt + 1))
            else:
                raise RuntimeError(last_error)
        except RuntimeError:
            raise
        except Exception as exc:  # network layer: retry with bounded backoff
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Centaline request failed after {RETRIES} attempts: {last_error}")


def date_slices(end: date, days: int, chunk_days: int) -> list[tuple[date, date]]:
    """Return inclusive [start, end] slices, oldest first."""
    start = end - timedelta(days=max(days - 1, 0))
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        slice_end = min(cursor + timedelta(days=chunk_days - 1), end)
        out.append((cursor, slice_end))
        cursor = slice_end + timedelta(days=1)
    return out


def fetch_slice(
    session: requests.Session,
    post_type: str,
    start: date,
    end: date,
    raw_dir: Path,
    pace: float,
) -> list[dict]:
    count_payload = {
        "day": "",
        "sort": "InsOrRegDate",
        "order": "Descending",
        "offset": 0,
        "size": 1,
        "postType": post_type,
        "estateUsages": ["RE", "HOS"],
        "insOrRegDateTimeRange": {"start": str(start), "end": str(end)},
    }
    count_blob = request_json(session, count_payload)
    count = int(count_blob.get("count") or 0)
    pages = (count + PAGE_SIZE - 1) // PAGE_SIZE
    if pages > 100:
        raise RuntimeError(
            f"{post_type} {start}..{end} has {count:,} records ({pages} pages); "
            "reduce --chunk-days so the API's 10,000-row offset cap is not crossed"
        )
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    manifest_rows: list[dict] = []
    for page_no in range(pages):
        offset = page_no * PAGE_SIZE
        fp = raw_dir / f"p{offset:05d}.json.gz"
        if fp.exists() and fp.stat().st_size > 100:
            with gzip.open(fp, "rt", encoding="utf-8") as fh:
                blob = json.load(fh)
        else:
            payload = dict(count_payload)
            payload.update({"offset": offset, "size": PAGE_SIZE})
            body = request_json(session, payload)
            blob = {
                "request": payload,
                "response_count": body.get("count"),
                "retrieved_at": pd.Timestamp.now(tz="Asia/Hong_Kong").isoformat(timespec="seconds"),
                "data": body.get("data") or [],
            }
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            fp.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
            time.sleep(pace + random.uniform(0, 0.15))
        rows.extend(blob.get("data") or [])
        raw_bytes = fp.read_bytes()
        manifest_rows.append(
            {
                "post_type": post_type,
                "start": str(start),
                "end": str(end),
                "offset": offset,
                "count": count,
                "file": str(fp.relative_to(ROOT)),
                "bytes": len(raw_bytes),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            }
        )
        print(
            f"{post_type} {start}..{end}: page {page_no + 1}/{pages}, "
            f"{len(rows):,}/{count:,} records",
            flush=True,
        )
    if manifest_rows:
        pd.DataFrame(manifest_rows).to_csv(raw_dir / "manifest.csv", index=False)
    return rows


def load_base(path: Path) -> pd.DataFrame:
    if path.exists():
        print(f"base parquet: {path} ({path.stat().st_size / 1024**2:.1f} MiB)", flush=True)
        return pd.read_parquet(path)
    fallback = ROOT / "data/normalized/centaline/transactions_full_2026-10-18.parquet"
    if fallback.exists() and fallback != path:
        print(f"base parquet fallback: {fallback}", flush=True)
        return pd.read_parquet(fallback)
    return pd.DataFrame()


def build_dataframe(records: list[dict]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame([parse_record(r) for r in records])
    for col in ["transaction_date", "registration_date", "deal_date", "analysis_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in [
        "saleable_area_sqft",
        "gross_area_sqft",
        "price_hkd",
        "saleable_psf",
        "gross_psf",
        "rent_monthly_hkd",
        "sale_price_hkd",
        "bedrooms",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def merge_frames(base: pd.DataFrame, fresh: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if fresh.empty:
        return base, 0
    fresh = fresh.drop_duplicates(subset=["source_record_id"], keep="last").copy()
    if base.empty:
        merged = fresh
        added = len(fresh)
    else:
        base = base.copy()
        base["_fresh_priority"] = 0
        fresh["_fresh_priority"] = 1
        merged = pd.concat([base, fresh], ignore_index=True, sort=False)
        merged = merged.sort_values(
            ["source_record_id", "_fresh_priority"], na_position="first"
        ).drop_duplicates(subset=["source_record_id"], keep="last")
        added = int(len(set(fresh["source_record_id"]) - set(base["source_record_id"])))
    merged = merged.drop(columns=["_fresh_priority"], errors="ignore")
    for col in ["transaction_date", "registration_date", "deal_date", "analysis_date"]:
        if col in merged.columns:
            merged[col] = pd.to_datetime(merged[col], errors="coerce")
    if "analysis_date" in merged.columns and "source_record_id" in merged.columns:
        merged = merged.sort_values(["analysis_date", "source_record_id"]).reset_index(drop=True)
    return merged, added


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=hk_today().isoformat())
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--chunk-days", type=int, default=7)
    parser.add_argument("--pace", type=float, default=1.0)
    parser.add_argument("--base-parquet", type=Path, default=LATEST_PARQUET)
    parser.add_argument("--out", type=Path, default=LATEST_PARQUET)
    args = parser.parse_args()
    args.base_parquet = args.base_parquet.resolve()
    args.out = args.out.resolve()

    snapshot = date.fromisoformat(args.snapshot)
    raw_root = RAW_ROOT / snapshot.isoformat()
    raw_root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    all_records: list[dict] = []
    source_counts: dict[str, int] = {}

    for post_type in ("Sale", "Rent"):
        records: list[dict] = []
        for start, end in date_slices(snapshot, args.days, args.chunk_days):
            slice_dir = raw_root / post_type.lower() / f"{start}_{end}"
            records.extend(fetch_slice(session, post_type, start, end, slice_dir, args.pace))
        unique = {str(r.get("id")): r for r in records if r.get("id")}
        source_counts[post_type] = len(unique)
        all_records.extend(unique.values())
        print(f"{post_type}: {len(unique):,} unique records", flush=True)

    fresh = build_dataframe(all_records)
    if fresh.empty or fresh["source_record_id"].isna().any():
        raise RuntimeError("Centaline returned no usable records or records without stable IDs")
    base = load_base(args.base_parquet)
    merged, added = merge_frames(base, fresh)
    if merged.empty:
        raise RuntimeError("no transaction data available after merge")

    merged = merged.drop_duplicates(subset=["source_record_id"], keep="last")
    if not merged["source_record_id"].is_unique:
        raise RuntimeError("merged transaction table has duplicate source_record_id values")
    if len(fresh) < 10:
        raise RuntimeError(f"daily refresh returned only {len(fresh)} records; refusing to publish")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(args.out, index=False)
    summary = {
        "generated_at": datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds"),
        "snapshot": snapshot.isoformat(),
        "window_days": args.days,
        "source_unique_records": source_counts,
        "base_records": int(len(base)),
        "fresh_records": int(len(fresh)),
        "new_source_record_ids": added,
        "records": int(len(merged)),
        "duplicates_dropped": int(len(base) + len(fresh) - len(merged)),
        "by_listing_type": merged["listing_type"].value_counts().to_dict(),
        "by_data_source": merged["data_source"].value_counts().to_dict(),
        "date_min": str(merged["analysis_date"].min().date()),
        "date_max": str(merged["analysis_date"].max().date()),
        "registration_date_min": str(merged["registration_date"].min().date()) if merged["registration_date"].notna().any() else None,
        "registration_date_max": str(merged["registration_date"].max().date()) if merged["registration_date"].notna().any() else None,
        "distinct_estates": int(merged["estate_name"].nunique()),
        "distinct_bldg_grp": int(merged["bldg_grp_id"].nunique()),
        "output": str(args.out.relative_to(ROOT)) if args.out.is_relative_to(ROOT) else str(args.out),
    }
    LATEST_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "data/normalized/centaline" / f"transactions_full_{snapshot}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
