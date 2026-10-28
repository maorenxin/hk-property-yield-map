#!/usr/bin/env python3
"""Offline Midland re-assignment from cached raw transaction batches.

Uses the same phase/tower matcher as 28Hse.  Existing non-target assignments
come from the current CSV; target-estate records are re-evaluated from raw.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from rental_group_matching import detect_estate, match_midland_row

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/midland/2026-10-18/remote"
OUT = ROOT / "data/derived/midland_rentals_2026-10-18.csv"
AUDIT = ROOT / "data/derived/midland_group_match_audit.csv"
SUMMARY = ROOT / "data/derived/midland_group_match_summary.json"
REPORT = ROOT / "reports/midland_group_match_validation.md"
TARGET = {"太古城", "匯璽", "日出康城", "映灣園", "珀麗灣", "沙田第一城", "嘉湖山莊", "新都城", "美孚新邨", "海怡半島", "天晉", "淘大花園", "海茵莊園", "將軍澳中心", "海之戀", "嘉亨灣", "名城", "瓏門", "栢慧豪園", "港灣豪庭", "維景灣畔", "泓景臺", "德福花園", "浪澄灣", "迎海", "新港城"}


def main() -> int:
    old = pd.read_csv(OUT) if OUT.exists() else pd.DataFrame()
    old_by_id = {str(r.source_record_id): r for r in old.itertuples()} if len(old) else {}
    records = {}
    for fp in sorted(RAW.glob("batch_*.json")):
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        for page in (d.get("pages") or ([d] if d.get("result") else [])):
            for x in page.get("result") or []:
                rid = str(x.get("id") or "")
                if rid:
                    records[rid] = x
    rows, audit = [], []
    stats = defaultdict(Counter)
    for rid, x in records.items():
        est = (x.get("estate") or {}).get("name") or ""
        phase = (x.get("phase") or {}).get("name") or ""
        bld = (x.get("building") or {}).get("name") or ""
        floor = (x.get("floor_level") or {}).get("name") or ""
        unit = x.get("flat") or ""
        match = match_midland_row({"estate_name": est, "phase_name": phase, "building_name": bld,
                                   "floor": floor, "unit": unit})
        target = detect_estate(f"{est} {phase} {bld} {floor} {unit}", est)
        oldr = old_by_id.get(rid)
        gid = match.group_id if target in TARGET else (oldr.group_id if oldr is not None and pd.notna(oldr.group_id) else None)
        method, reason, confidence = match.match_method, match.match_reason, match.match_confidence
        if target == "日出康城" and pd.isna(gid):
            gid = "HMA114:SYN-LOHAS"
            method, reason, confidence = "synthetic_phase_bucket", "no existing group; new-phase synthetic bucket", 80
        dt = str(x.get("tx_date") or "")[:10]
        rows.append({
            "source": "midland", "source_record_id": rid, "source_url": x.get("url_desc") or x.get("url"),
            "transaction_date": dt, "deal_date": dt, "group_id": gid,
            "midland_estate_id": (x.get("estate") or {}).get("id"),
            "midland_phase_id": (x.get("phase") or {}).get("id"),
            "estate_name": est, "phase_name": phase, "building_name": bld,
            "floor": floor, "unit": unit, "area_sqft": x.get("net_area") or x.get("area"),
            "rent_hkd": x.get("price"), "psf": x.get("unit_price_net"), "retrieved_at": "2026-10-18",
            "match_method": method, "match_confidence": confidence,
            "match_reason": reason, "review_flag": False if pd.notna(gid) else match.review_flag,
            "candidate_groups": match.candidate_groups,
        })
        if target in TARGET:
            stats[target]["total"] += 1
            stats[target]["assigned"] += int(pd.notna(gid))
            stats[target]["unmatched"] += int(pd.isna(gid))
            if pd.isna(gid):
                audit.append({
                    "source": "midland", "source_record_id": rid, "source_url": x.get("url_desc") or x.get("url"),
                    "estate_name": est, "phase_raw": phase, "tower_raw": bld, "floor": floor, "unit": unit,
                    "area": x.get("net_area") or x.get("area"), "rent": x.get("price"),
                    "match_status": "review", "match_reason": match.match_reason,
                    "candidate_groups": match.candidate_groups, "chosen_group": gid,
                    "confidence": match.match_confidence,
                })
    out = pd.DataFrame(rows).drop_duplicates("source_record_id")
    out.to_csv(OUT, index=False)
    pd.DataFrame(audit).to_csv(AUDIT, index=False)
    summary = {"generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
               "raw_records": len(out), "assigned": int(out.group_id.notna().sum()),
               "groups": int(out.group_id.nunique()), "targets": {k: dict(v) for k, v in stats.items()}}
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    lines = ["# Midland 多源租务楼宇组匹配验证（2026-10-18）", "", "## 结果", "",
             f"- raw 去重记录：**{len(out):,}**", f"- 已落组：**{out.group_id.notna().sum():,}**，{out.group_id.nunique()} 个组",
             "", "## 六个目标屋苑", "", "| 屋苑 | 原始 | 已落组 | 未落组 |", "|---|---:|---:|---:|"]
    for k in ["太古城", "匯璽", "日出康城", "映灣園", "珀麗灣", "沙田第一城"]:
        s = stats.get(k, Counter()); lines.append(f"| {k} | {s.get('total',0)} | {s.get('assigned',0)} | {s.get('unmatched',0)} |")
    lines += ["", "未落组/歧义记录见 `data/derived/midland_group_match_audit.csv`。", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
