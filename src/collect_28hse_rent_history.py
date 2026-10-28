#!/usr/bin/env python3
"""Pilot 28Hse rental-transaction history for selected estates.

28Hse exposes unit-level rental deals on
/estate/detail/<slug>/transaction/rent/page-N.  This pilot collects a bounded
number of pages per estate and stores the records for cross-source validation.
"""
from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
from bs4 import BeautifulSoup
from rental_group_matching import detect_estate, match_28hse_row

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/28hse/2026-10-18/rent_history"
OUT = ROOT / "data/derived/28hse_rent_history.csv"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
SLUGS = [
    "太古城-1140", "匯璽-27511", "海茵莊園-23721",
    "美孚新邨-2520", "嘉湖山莊-4390", "新都城-4000",
]


def parse_page(html: str, slug: str):
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    table = next((t for t in tables if "celled" in (t.get("class") or [])), None)
    if table is None:
        return [], 1
    max_page = 1
    for a in soup.select("[attr1]"):
        v = a.get("attr1")
        if v and str(v).isdigit():
            max_page = max(max_page, int(v))
    rows = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        first = tds[0].get_text(" ", strip=True)
        a = tr.find("a", href=re.compile(r"/transaction/rent/deal-"))
        if not a:
            continue
        mdate = re.search(r"\d{4}-\d{2}-\d{2}", first)
        mrent = re.search(r"\$\s*([\d,]+)", tds[2].get_text(" ", strip=True))
        mpsf = re.search(r"@\s*\$?\s*([\d.]+)", tds[2].get_text(" ", strip=True))
        marea = re.search(r"([\d,]+)\s*呎", tds[1].get_text(" ", strip=True))
        if not (mdate and mrent):
            continue
        unit = (a.get("title") or "").replace(" ", " ").strip()
        rows.append({
            "estate_slug": slug,
            "transaction_date": mdate.group(0),
            "unit": unit,
            "area_sqft": float(marea.group(1).replace(",", "")) if marea else None,
            "rent_hkd": float(mrent.group(1).replace(",", "")),
            "psf": float(mpsf.group(1)) if mpsf else None,
            "source_url": a.get("href"),
        })
    return rows, max_page


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=40, help="max pages per estate")
    ap.add_argument("--all-mapped", action="store_true", help="all estates in building_age_28hse.csv")
    ap.add_argument("--offline", action="store_true", help="parse cached pages only; never fetch")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    existing = []
    if OUT.exists():
        try:
            existing = pd.read_csv(OUT).to_dict("records")
        except Exception:
            existing = []
    all_rows = list(existing)
    if args.all_mapped:
        age = pd.read_csv(ROOT / "data/derived/building_age_28hse.csv")
        cached = {p.name[:-len("_p1.html")] for p in RAW.glob("*_p1.html") if p.name.endswith("_p1.html")}
        slugs = sorted({str(x) for x in age.estate_slug.dropna().unique()} | cached | set(SLUGS))
    else:
        slugs = list(SLUGS)
    print(f"estates: {len(slugs)}; existing rows: {len(existing)}", flush=True)
    for slug in slugs:
        base = "https://www.28hse.com/estate/detail/" + quote(slug) + "/transaction/rent"
        first_fp = RAW / (slug.replace("/", "_") + "_p1.html")
        if first_fp.exists():
            html = first_fp.read_text(encoding="utf-8", errors="ignore")
        elif args.offline:
            print("skip", slug, "page1 not cached (offline)", flush=True)
            continue
        else:
            html = None
            for attempt in range(4):
                try:
                    r = requests.get(base, headers={"User-Agent": UA}, timeout=30)
                    if r.status_code == 200:
                        html = r.text
                        break
                except Exception as e:  # noqa: BLE001
                    print("retry", slug, type(e).__name__, flush=True)
                time.sleep(1.0 + attempt * 2.0)
            if html is None:
                print("skip", slug, "fetch failed")
                continue
            first_fp.write_text(html, encoding="utf-8")
            time.sleep(0.6)
        rows, max_page = parse_page(html, slug)
        all_rows += rows
        limit = min(max_page, args.pages)
        print(f"{slug}: page1={len(rows)} max_page={max_page} collect={limit}", flush=True)
        for page in range(2, limit + 1):
            fp = RAW / (slug.replace("/", "_") + f"_p{page}.html")
            if fp.exists():
                page_html = fp.read_text(encoding="utf-8", errors="ignore")
            elif args.offline:
                break
            else:
                url = base + f"/page-{page}"
                page_html = None
                for attempt in range(3):
                    try:
                        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
                        if r.status_code == 200:
                            page_html = r.text
                            break
                    except Exception as e:  # noqa: BLE001
                        print("retry-page", slug, page, type(e).__name__, flush=True)
                    time.sleep(1.0 + attempt * 2.0)
                if page_html is None:
                    break
                fp.write_text(page_html, encoding="utf-8")
                time.sleep(0.6)
            rr, _ = parse_page(page_html, slug)
            if not rr:
                break
            all_rows += rr
        pd.DataFrame(all_rows).to_csv(OUT, index=False)
    # Assign each row to the most specific building group by matching the unit's
    # phase/estate name against our group descriptions.
    data = json.loads((ROOT / "site/map_data.js").read_text()[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    groups = data.get("buildings") or []
    def nn(s):
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(s or "").lower())
    out_rows = []
    for r in all_rows:
        pref = r["estate_slug"].split("-")[0]
        cands = [g for g in groups if nn(pref) in nn(g.get("name")) or nn(pref) in nn(g.get("bldg"))]
        if pref in ("匯璽",):
            phase = "III" if "III" in r["unit"] or "3期" in r["unit"] else ("II" if "II" in r["unit"] or "2A期" in r["unit"] else "")
            if phase:
                cands = [g for g in cands if phase in str(g.get("bldg") or "")] or cands
        scored = []
        u = nn(r["unit"]).replace(nn(pref), "", 1)
        for g in cands:
            c = (nn(g.get("bldg")) + nn(g.get("name"))).replace(nn(pref), "", 1)
            m = SequenceMatcher(None, u, c).find_longest_match(0, len(u), 0, len(c))
            scored.append((m.size, -len(nn(g.get("bldg"))), g))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        if scored and scored[0][0] >= 3 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            r["group_id"] = scored[0][2]["id"]
        elif len(cands) == 1:
            r["group_id"] = cands[0]["id"]
        else:
            r["group_id"] = None
        # Six complex estates use the shared, auditable phase/tower rules.
        target = detect_estate(str(r.get("unit") or ""), str(r.get("estate_slug") or ""))
        if target in {"太古城", "匯璽", "日出康城", "映灣園", "珀麗灣", "沙田第一城"}:
            m = match_28hse_row(r)
            r["group_id"] = m.group_id
            r["match_method"] = m.match_method
            r["match_confidence"] = m.match_confidence
            r["match_reason"] = m.match_reason
            r["review_flag"] = m.review_flag
            r["candidate_groups"] = m.candidate_groups
        out_rows.append(r)
    out_df = pd.DataFrame(out_rows).drop_duplicates(subset=["source_url"], keep="first")
    out_df.to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(ROOT)} rows={len(out_df)} assigned={out_df.group_id.notna().sum() if 'group_id' in out_df else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
