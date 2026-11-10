#!/usr/bin/env python3
"""Geocode address-level fallback points for buildings missing from the map.

Centaline's transaction payload can contain a building code (1-...) or an
estate code (2-...).  The mapproject Estate/Brief endpoint resolves either to
the authoritative map coordinate and canonical address.  Cache entries are
address-scoped so the daily build remains deterministic and resumable.
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from build_building_metrics import address_matches, orphan_address_candidates

ROOT = Path(__file__).resolve().parents[1]
NORM_LATEST = ROOT / "data/normalized/centaline/transactions_full_latest.parquet"
METRICS = ROOT / "data/derived/building_metrics_12m.csv"
ESTATES = ROOT / "data/derived/estate_metrics_12m.csv"
CACHE = ROOT / "data/derived/orphan_coordinate_cache.json"
URL = "https://hk.centanet.com/mapproject/api/Estate/Brief"
AUTOCOMPLETE_URL = "https://hk-api.centanet.com/centanetapi/api/Transaction/GetTransactionAutoComplete"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Referer": "https://hk.centanet.com/mapproject/list/sale",
}
_thread_local = threading.local()


def thread_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return session


def hk_today() -> date:
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).date()


def stale_error(item: dict, days: int = 7) -> bool:
    stamp = item.get("checked_at")
    if not stamp:
        return True
    try:
        checked = datetime.fromisoformat(stamp)
        return (datetime.now(ZoneInfo("Asia/Hong_Kong")) - checked).days >= days
    except ValueError:
        return True


def is_resolved(item: dict | None, expected_address: str | None = None) -> bool:
    if not item or item.get("status") != "ok":
        return False
    if item.get("lat") is None or item.get("lon") is None:
        return False
    if item.get("address_match") is True:
        return True
    actual_address = item.get("address")
    return bool(
        expected_address
        and actual_address
        and address_matches(expected_address, str(actual_address))
    )


def unresolved_coordinates(cache: dict, expected_by_key: dict[str, str]) -> list[str]:
    return sorted(
        key
        for key, expected_address in expected_by_key.items()
        if not is_resolved(cache.get(key), expected_address)
    )


def coordinate_exit_code(unresolved: int, max_unresolved: int) -> int:
    if unresolved < 0 or max_unresolved < 0:
        raise ValueError("coordinate counts must be non-negative")
    return 0 if unresolved <= max_unresolved else 2


def request_coordinate(session: requests.Session, type_code: str, expected_address: str,
                       retries: int = 5) -> dict:
    last_error = None
    for attempt in range(retries):
        try:
            response = session.get(URL, params={"typeCode": type_code, "dataSize": "Normal"}, timeout=30)
            if response.status_code == 200:
                payload = response.json()
                gmap = payload.get("gMap") or payload.get("center") or {}
                lat, lon = gmap.get("lat"), gmap.get("lng", gmap.get("lon"))
                if lat is not None and lon is not None:
                    actual = str(payload.get("address") or "")
                    match = address_matches(expected_address, actual)
                    return {
                        "status": "ok",
                        "type_code": type_code,
                        "resolved_type_code": payload.get("typeCode"),
                        "address": actual,
                        "address_match": match,
                        "lat": float(lat),
                        "lon": float(lon),
                    }
                return {
                    "status": "not_found",
                    "type_code": type_code,
                    "error": f"no gMap in response: {str(payload)[:240]}",
                }
            elif response.status_code == 429:
                wait = (30, 60, 120, 240, 300)[min(attempt, 4)]
                print(f"429 for {type_code}; backing off {wait}s", flush=True)
                time.sleep(wait)
                last_error = "HTTP 429"
                continue
            elif response.status_code == 204:
                return {
                    "status": "not_found",
                    "type_code": type_code,
                    "error": "HTTP 204: no coordinate match",
                }
            else:
                last_error = f"HTTP {response.status_code}: {response.text[:240]}"
        except Exception as exc:  # noqa: BLE001 - retry network/JSON failures
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(4 * (attempt + 1))
    return {"status": "error", "type_code": type_code, "error": last_error or "unknown"}


def request_autocomplete_coordinate(session: requests.Session, address: str,
                                    retries: int = 3) -> dict:
    last_error = None
    for attempt in range(retries):
        try:
            response = session.post(
                AUTOCOMPLETE_URL,
                params={"resultSet": "WithNewProp"},
                json={"keyword": address},
                timeout=30,
            )
            if response.status_code == 200:
                payload = response.json()
                candidates = []
                for kind in ("building", "estate"):
                    candidates.extend(payload.get(kind) or [])
                for item in candidates:
                    center = item.get("center") or {}
                    lat, lon = center.get("latitude"), center.get("longitude")
                    label = str(item.get("label") or "")
                    if lat is None or lon is None or not address_matches(address, label):
                        continue
                    search = item.get("search") or {}
                    building_codes = search.get("building") or []
                    return {
                        "status": "ok",
                        "type_code": building_codes[0] if building_codes else item.get("value"),
                        "resolved_type_code": item.get("value"),
                        "address": label,
                        "address_match": True,
                        "lat": float(lat),
                        "lon": float(lon),
                        "fallback": "autocomplete",
                    }
                return {
                    "status": "not_found",
                    "type_code": None,
                    "error": "no exact address match in autocomplete",
                }
            elif response.status_code == 429:
                time.sleep((30, 60, 120)[min(attempt, 2)])
                last_error = "HTTP 429"
                continue
            elif response.status_code == 204:
                return {
                    "status": "not_found",
                    "type_code": None,
                    "error": "HTTP 204: no autocomplete match",
                }
            else:
                last_error = f"HTTP {response.status_code}: {response.text[:240]}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(3 * (attempt + 1))
    return {"status": "error", "type_code": None, "error": last_error or "unknown"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=hk_today().isoformat())
    parser.add_argument("--base-parquet", type=Path, default=NORM_LATEST)
    parser.add_argument("--metrics", type=Path, default=METRICS)
    parser.add_argument("--estates", type=Path, default=ESTATES)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--pace", type=float, default=0.35)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--max-unresolved",
        type=int,
        default=0,
        help="allow this many total unresolved addresses without failing the step (default: 0)",
    )
    parser.add_argument("--refresh-errors", action="store_true")
    parser.add_argument("--refresh-mismatch", action="store_true")
    args = parser.parse_args()
    if args.max_unresolved < 0:
        parser.error("--max-unresolved must be non-negative")

    snapshot = date.fromisoformat(args.snapshot)
    df = pd.read_parquet(args.base_parquet, columns=[
        "hma_id", "bldg_grp_id", "estate_name", "building_name", "address_raw", "type_code",
        "district", "region_terr", "analysis_date",
    ])
    df["analysis_date"] = pd.to_datetime(df.analysis_date, errors="coerce")
    metrics = pd.read_csv(args.metrics, low_memory=False)
    estates = pd.read_csv(args.estates, low_memory=False) if args.estates.exists() else pd.DataFrame()
    cutoff = pd.Timestamp(snapshot) - pd.Timedelta(days=365)
    candidates = orphan_address_candidates(df, df[df.analysis_date >= cutoff], metrics, estates)
    cache = json.loads(args.cache.read_text()) if args.cache.exists() else {}
    address_by_key = {row.addr_key: row.address_raw for row in candidates.itertuples()}
    candidate_keys = set(address_by_key)
    todo = []
    for row in candidates.itertuples():
        item = cache.get(row.addr_key)
        if is_resolved(item, row.address_raw):
            continue
        if item and item.get("status") == "ok" and not args.refresh_mismatch:
            continue
        if item and item.get("status") in {"error", "not_found"} and not args.refresh_errors and not stale_error(item):
            continue
        todo.append(row)
    if args.limit:
        todo = todo[:args.limit]
    cached_ok = sum(is_resolved(cache.get(key), address_by_key[key]) for key in candidate_keys)
    print(f"candidate addresses={len(candidates)}, cached_ok={cached_ok}, "
          f"todo={len(todo)}", flush=True)

    ok = errors = unmatched = 0
    error_details = []

    def work(row):
        result = request_coordinate(thread_session(), row.type_code, row.address_raw)
        attempts = [result]
        fallback = getattr(row, "fallback_type_code", "")
        if not is_resolved(result, row.address_raw) and fallback:
            fallback_result = request_coordinate(thread_session(), fallback, row.address_raw)
            attempts.append(fallback_result)
            if is_resolved(fallback_result, row.address_raw):
                fallback_result["fallback_from"] = result.get("type_code")
                result = fallback_result
        if not is_resolved(result, row.address_raw):
            autocomplete_result = request_autocomplete_coordinate(thread_session(), row.address_raw)
            attempts.append(autocomplete_result)
            if is_resolved(autocomplete_result, row.address_raw):
                autocomplete_result["fallback_from"] = result.get("type_code")
                result = autocomplete_result
            else:
                result = next(
                    (attempt for attempt in attempts if attempt.get("status") == "error"),
                    result,
                )
        time.sleep(args.pace + random.uniform(0, 0.08))
        return row, result

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(work, row) for row in todo]
        for i, future in enumerate(as_completed(futures), 1):
            row, result = future.result()
            result.update({
                "hma_id": row.hma_id,
                "address_raw": row.address_raw,
                "building_names": row.building_names,
                "checked_at": datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds"),
            })
            cache[row.addr_key] = result
            if is_resolved(result, row.address_raw):
                ok += 1
            elif result.get("status") == "error":
                errors += 1
                error_details.append(
                    f"{row.address_raw}: {result.get('error') or 'unknown coordinate error'}"
                )
            else:
                unmatched += 1
            if i % 25 == 0 or i == len(todo):
                args.cache.parent.mkdir(parents=True, exist_ok=True)
                args.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
                print(
                    f"{i}/{len(todo)} ok={ok} unmatched={unmatched} errors={errors}",
                    flush=True,
                )

    args.cache.parent.mkdir(parents=True, exist_ok=True)
    args.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    unresolved = unresolved_coordinates(cache, address_by_key)
    total_ok = len(candidate_keys) - len(unresolved)
    print(
        f"coordinate cache: {total_ok}/{len(candidates)} resolved; "
        f"unresolved={len(unresolved)}; this run ok={ok}, unmatched={unmatched}, errors={errors}"
    )
    unresolved_over_budget = len(unresolved) > args.max_unresolved
    if errors:
        level = "error" if unresolved_over_budget else "warning"
        print(
            f"::{level}::coordinate resolution errors for {errors} address(es); "
            f"total unresolved={len(unresolved)}/{args.max_unresolved}",
            flush=True,
        )
        for detail in error_details[:20]:
            print(f"coordinate error: {detail}", flush=True)
        if len(error_details) > 20:
            print(f"coordinate error: ... {len(error_details) - 20} more", flush=True)
    if unresolved:
        if unresolved_over_budget:
            print(
                f"::error::coordinate unresolved budget exceeded: "
                f"{len(unresolved)} > {args.max_unresolved}",
                flush=True,
            )
        else:
            print(
                f"::warning::coordinate unresolved: {len(unresolved)} address(es); "
                f"allowed={args.max_unresolved}",
                flush=True,
            )
        for key in unresolved[:20]:
            item = cache.get(key) or {}
            expected = address_by_key.get(key, key)
            actual = item.get("address") or "-"
            if not item:
                reason = "missing cache; not processed or unavailable"
            elif item.get("status") == "ok":
                reason = "address mismatch"
            else:
                reason = item.get("error") or item.get("status") or "unresolved"
            print(
                f"coordinate unresolved: {expected} -> {actual}; {reason}",
                flush=True,
            )
        if len(unresolved) > 20:
            print(f"coordinate unresolved: ... {len(unresolved) - 20} more", flush=True)
    return coordinate_exit_code(len(unresolved), args.max_unresolved)


if __name__ == "__main__":
    raise SystemExit(main())
