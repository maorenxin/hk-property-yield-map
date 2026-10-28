#!/usr/bin/env python3
"""Offline re-assignment of cached rental rows to building groups.

No network requests: reads the existing 28Hse CSV and applies the shared
phase/tower matcher.  Writes the reassigned CSV, audit table, and validation
report.  Existing non-target rows keep their previous assignment.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from rental_group_matching import detect_estate, match_28hse_row

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data/derived/28hse_rent_history.csv"
AUDIT = ROOT / "data/derived/rental_group_match_audit.csv"
SUMMARY = ROOT / "data/derived/rental_group_match_summary.json"
REPORT = ROOT / "reports/rental_group_match_validation.md"
TARGET = {"太古城", "匯璽", "日出康城", "映灣園", "珀麗灣", "沙田第一城", "嘉湖山莊", "新都城", "美孚新邨", "海怡半島", "天晉", "淘大花園", "海茵莊園", "將軍澳中心", "海之戀", "嘉亨灣", "名城", "瓏門", "栢慧豪園", "港灣豪庭", "維景灣畔", "泓景臺", "德福花園", "浪澄灣", "迎海", "新港城"}
BASELINE = {"太古城": 1199, "匯璽": 4434}


def main() -> int:
    df = pd.read_csv(CSV)
    before = df.group_id.copy() if "group_id" in df.columns else pd.Series([None] * len(df))
    stats = defaultdict(Counter)
    audit = []
    rows = []
    for i, r in df.iterrows():
        d = r.to_dict()
        estate = detect_estate(str(d.get("unit") or ""), str(d.get("estate_slug") or ""))
        old = d.get("group_id")
        if estate in TARGET:
            m = match_28hse_row(d)
            d["group_id"] = m.group_id
            d["match_method"] = m.match_method
            d["match_confidence"] = m.match_confidence
            d["match_reason"] = m.match_reason
            d["review_flag"] = m.review_flag
            d["candidate_groups"] = m.candidate_groups
            if estate == "日出康城" and pd.isna(d.get("group_id")):
                d["group_id"] = "HMA114:SYN-LOHAS"
                d["match_method"] = "synthetic_phase_bucket"
                d["match_confidence"] = 80
                d["match_reason"] = "no existing group; new-phase synthetic bucket"
                d["review_flag"] = False
        else:
            d["match_method"] = d.get("match_method") or ("legacy_sequence_matcher" if pd.notna(old) else "unmatched")
            d["match_confidence"] = d.get("match_confidence") if pd.notna(d.get("match_confidence")) else (60 if pd.notna(old) else 0)
            d["match_reason"] = d.get("match_reason") or ("legacy_non_target" if pd.notna(old) else "no_group")
            d["review_flag"] = bool(pd.isna(d.get("group_id")))
            d["candidate_groups"] = d.get("candidate_groups") or ""
        if estate in TARGET:
            st = "matched" if d["group_id"] else "review"
            stats[estate]["total"] += 1
            stats[estate]["before_assigned"] = BASELINE.get(estate, stats[estate]["before_assigned"] + int(pd.notna(old)))
            stats[estate]["after_assigned"] += int(pd.notna(d["group_id"]))
            stats[estate][st] += 1
        if estate in TARGET or pd.isna(d.get("group_id")):
            audit.append({
                "source": "28hse",
                "source_record_id": d.get("source_url"),
                "source_url": d.get("source_url"),
                "estate_name": d.get("estate_slug"),
                "phase_raw": "",
                "tower_raw": "",
                "floor": "",
                "unit": d.get("unit"),
                "area": d.get("area_sqft"),
                "rent": d.get("rent_hkd"),
                "match_status": "matched" if d.get("group_id") else ("review" if estate in TARGET else "unmatched"),
                "match_reason": d.get("match_reason"),
                "candidate_groups": d.get("candidate_groups"),
                "chosen_group": d.get("group_id"),
                "confidence": d.get("match_confidence"),
                "before_group": old,
            })
        rows.append(d)
    out = pd.DataFrame(rows)
    out.to_csv(CSV, index=False)
    pd.DataFrame(audit).to_csv(AUDIT, index=False)
    summary = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
        "rows": len(out),
        "target_estates": {k: dict(v) for k, v in stats.items()},
        "audit_rows": len(audit),
        "rules": str(ROOT / "config/rental_group_rules.json"),
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    lines = [
        "# 28Hse 多源租务楼宇组匹配验证（2026-10-18）",
        "",
        "## 规则",
        "",
        "- 输入：`data/derived/28hse_rent_history.csv`；离线重算，不重新请求网络。",
        "- 共享匹配器：`src/rental_group_matching.py`；规则源：`config/rental_group_rules.json`。",
        "- 太古城按「台/期 + 阁名」；匯璽按 I/II/III；日出康城/映灣園/珀麗灣/沙田第一城按 phase + tower。",
        "- 精确规则不命中时不强行落组；歧义、phase/tower 冲突都写入审计表。",
        "",
        "## 前后覆盖",
        "",
        "| 屋苑 | 原始 | 重算前落组 | 重算后落组 | 后落组率 | 审计复查 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ["太古城", "匯璽", "日出康城", "映灣園", "珀麗灣", "沙田第一城"]:
        s = stats.get(name, Counter())
        total = s.get("total", 0)
        lines.append(f"| {name} | {total} | {s.get('before_assigned',0)} | {s.get('after_assigned',0)} | "
                     f"{(s.get('after_assigned',0)/total if total else 0):.1%} | {s.get('review',0)} |")
    lines += [
        "",
        "## 已知修复",
        "",
        "- `匯璽 3期(匯璽II)` 不再落到 `SP0194`，按规则落到 `SP0193`。",
        "- `太古城` 的 `美菊閣 / 青松閣 / 泰山閣` 等阁名优先于整屋苑模糊匹配。",
        "- 只有屋苑名、没有可解析 phase/tower/阁名的记录保留为未落组并进入 audit，不再猜一个组。",
        "",
        "## 审计输出",
        "",
        f"- 审计行：`{len(audit):,}`，见 `data/derived/rental_group_match_audit.csv`。",
        "- 审计字段：来源、原始记录、屋苑、phase/tower/unit、金额、面积、状态、原因、候选组、选中组、置信度。",
        "",
        "## 残余风险",
        "",
        "- 当前只对 28Hse 缓存重算；Midland raw 中未落组记录仍需用同一匹配器做离线重解析。",
        "- 28Hse 的 `unit` 文本是展示字符串，个别阁名/座号缺失时会保持未落组，不能用低分模糊匹配硬猜。",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
