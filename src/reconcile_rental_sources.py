#!/usr/bin/env python3
"""Reconcile 28Hse and Midland rental deals at unit/day/rent level.

This is a conservative audit, not a record-linkage system:
- first require the same building group and transaction date;
- then compare rent and saleable area with small tolerances;
- use floor/unit tokens when both sources expose them;
- greedy one-to-one matching prevents one Midland row being counted twice.

Outputs:
  data/derived/rental_source_matches.csv
  data/derived/rental_source_reconciliation.json
  reports/rental_source_reconciliation.md
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from build_building_metrics import unit_token

ROOT = Path(__file__).resolve().parents[1]
HSE = ROOT / "data/derived/28hse_rent_history.csv"
MID = ROOT / "data/derived/midland_rentals_2026-10-18.csv"
OUT_CSV = ROOT / "data/derived/rental_source_matches.csv"
OUT_JSON = ROOT / "data/derived/rental_source_reconciliation.json"
OUT_MD = ROOT / "reports/rental_source_reconciliation.md"


def building_token(building, unit) -> str:
    text = str(building or "") + " " + str(unit or "")
    text = re.sub(r"\s+", "", text)
    m = re.search(r"(第?\d+期|\d+座|[A-Za-z]座|\d+號)", text)
    return m.group(1).lower() if m else ""


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    h = pd.read_csv(HSE)
    m = pd.read_csv(MID)
    h = h[h.group_id.notna()].copy()
    m = m[m.group_id.notna()].copy()
    h["date"] = pd.to_datetime(h.transaction_date, errors="coerce").dt.date
    m["date"] = pd.to_datetime(m.transaction_date, errors="coerce").dt.date
    h["rent"] = pd.to_numeric(h.rent_hkd, errors="coerce")
    m["rent"] = pd.to_numeric(m.rent_hkd, errors="coerce")
    h["area"] = pd.to_numeric(h.area_sqft, errors="coerce")
    m["area"] = pd.to_numeric(m.area_sqft, errors="coerce")
    h["unit_token"] = [unit_token("", r.unit, r.estate_slug) for r in h.itertuples()]
    m["unit_token"] = [unit_token(r.floor, r.unit, r.building_name) for r in m.itertuples()]
    h["building_token"] = [building_token(r.estate_slug, r.unit) for r in h.itertuples()]
    m["building_token"] = [building_token(r.building_name, r.unit) for r in m.itertuples()]
    return h, m


def compatible(h, m) -> bool:
    if h.building_token and m.building_token and h.building_token != m.building_token:
        return False
    return True


def score_pair(h, m):
    if pd.isna(h.rent) or pd.isna(m.rent) or pd.isna(h.area) or pd.isna(m.area):
        return 0
    if not compatible(h, m):
        return 0
    rent_ok = abs(float(h.rent) - float(m.rent)) <= 1.0
    area_ok = abs(float(h.area) - float(m.area)) <= 1.0
    if rent_ok and area_ok:
        if h.unit_token and m.unit_token and h.unit_token == m.unit_token:
            return 100
        if h.unit_token and m.unit_token and h.unit_token != m.unit_token:
            return 0
        return 70
    if abs(float(h.rent) - float(m.rent)) <= max(500.0, float(h.rent) * 0.01) and \
       abs(float(h.area) - float(m.area)) <= max(5.0, float(h.area) * 0.01):
        if h.unit_token and m.unit_token and h.unit_token != m.unit_token:
            return 0
        return 45
    return 0


def main() -> int:
    h, m = load()
    both_groups = sorted(set(h.group_id) & set(m.group_id))
    h = h[h.group_id.isin(both_groups)].copy().reset_index(drop=True)
    m = m[m.group_id.isin(both_groups)].copy().reset_index(drop=True)
    m["mid_index_tmp"] = range(len(m))
    mid_by = {k: v for k, v in m.groupby("group_id").__iter__()}
    candidate_rows = []
    for hrow in h.itertuples():
        candidates = mid_by.get(hrow.group_id, None)
        if candidates is None or candidates.empty:
            continue
        for mrow in candidates.itertuples():
            delta = abs((hrow.date - mrow.date).days) if pd.notna(hrow.date) and pd.notna(mrow.date) else 99
            if delta > 1:
                continue
            sc = score_pair(hrow, mrow)
            if delta == 1:
                sc = 0 if sc == 0 else max(40, sc - 10)
            if sc:
                candidate_rows.append({
                    "score": sc, "hse_index": hrow.Index, "mid_index": mrow.mid_index_tmp,
                    "group_id": hrow.group_id, "date": hrow.date, "date_delta": delta,
                })
    candidates = pd.DataFrame(candidate_rows)
    matched_h, matched_m = set(), set()
    matches = []
    if len(candidates):
        for r in candidates.sort_values(["score", "date_delta", "date"], ascending=[False, True, True]).itertuples():
            if r.hse_index in matched_h or r.mid_index in matched_m:
                continue
            hr = h.loc[r.hse_index]
            mr = m.loc[r.mid_index]
            matched_h.add(r.hse_index)
            matched_m.add(r.mid_index)
            matches.append({
                "match_type": ("exact" if r.score >= 100 and r.date_delta == 0 else
                               "date_shift_1d" if r.date_delta == 1 else "relaxed"),
                "score": r.score,
                "date_delta": r.date_delta,
                "group_id": hr.group_id,
                "date": str(hr.date),
                "hse_unit": hr.unit,
                "mid_unit": " ".join(str(x) for x in (mr.building_name, mr.floor, mr.unit) if pd.notna(x)),
                "hse_rent": hr.rent,
                "mid_rent": mr.rent,
                "rent_diff": round(float(hr.rent) - float(mr.rent), 1),
                "hse_area": hr.area,
                "mid_area": mr.area,
                "area_diff": round(float(hr.area) - float(mr.area), 1),
                "hse_url": hr.source_url,
                "mid_url": mr.source_url,
                "hse_record": None,
                "mid_record": mr.source_record_id,
            })
    mdf = pd.DataFrame(matches)
    mdf.to_csv(OUT_CSV, index=False)
    exact = int((mdf.match_type == "exact").sum()) if len(mdf) else 0
    date_shift = int((mdf.match_type == "date_shift_1d").sum()) if len(mdf) else 0
    relaxed = int((mdf.match_type == "relaxed").sum()) if len(mdf) else 0
    summary = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
        "hse_rows_in_overlap_groups": int(len(h)),
        "mid_rows_in_overlap_groups": int(len(m)),
        "overlap_groups": len(both_groups),
        "matched_pairs": int(len(mdf)),
        "exact_pairs": exact,
        "date_shift_pairs": date_shift,
        "relaxed_pairs": relaxed,
        "hse_unmatched": int(len(h) - len(matched_h)),
        "mid_unmatched_in_overlap": int(len(m) - len(matched_m)),
        "exact_rate_of_hse": round(exact / len(h), 4) if len(h) else 0,
        "date_shift_rate_of_hse": round(date_shift / len(h), 4) if len(h) else 0,
        "relaxed_rate_of_hse": round(relaxed / len(h), 4) if len(h) else 0,
        "exact_rent_diff_median": round(float(mdf.loc[mdf.match_type == "exact", "rent_diff"].median()), 1) if exact else None,
        "exact_area_diff_median": round(float(mdf.loc[mdf.match_type == "exact", "area_diff"].median()), 1) if exact else None,
        "note": "Exact = same group/day and same rent/area plus matching floor-unit token; relaxed = same group/day and within 1% rent/area without conflicting unit token. This is a lower-bound overlap because 28Hse group assignment is partial.",
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    lines = [
        "# 28Hse × Midland 租务同单元对账（2026-10-18）",
        "",
        "## 口径",
        "",
        "- 只对比两边都已归属到 map 楼宇组的记录。",
        "- `exact`：同一楼宇组、同一成交日、租金差 ≤HK$1、面积差 ≤1 呎，并且层/室 token 相同。",
        "- `date_shift_1d`：同上，但两个平台成交日相差 1 天（常见的申报/登记日差异）。",
        "- `relaxed`：同一楼宇组、同一成交日、租金/面积各在 1% 内，且没有相互冲突的层/室 token。",
        "- 采用一对一贪心匹配，同一个 Midland 记录不会被重复计算。",
        "- 这是 overlap 的下界；28Hse 的 group_id 只覆盖部分记录（未归属记录不参与本对账）。",
        "",
        "## 总览",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 28Hse 在共同组内记录 | {len(h):,} |",
        f"| Midland 在共同组内记录 | {len(m):,} |",
        f"| 两边都有记录的楼宇组 | {len(both_groups):,} |",
        f"| 匹配对（exact + relaxed） | {len(mdf):,} |",
        f"| exact 匹配对 | {exact:,} |",
        f"| 日期相差 1 天的高置信匹配 | {date_shift:,} |",
        f"| relaxed 匹配对 | {relaxed:,} |",
        f"| 28Hse 未匹配（共同组内） | {len(h)-len(matched_h):,} |",
        f"| Midland 未匹配（共同组内） | {len(m)-len(matched_m):,} |",
        f"| exact 占 28Hse 共同组记录 | {summary['exact_rate_of_hse']:.1%} |",
        f"| exact+1日 占 28Hse 共同组记录 | {(exact+date_shift)/len(h):.1%} |",
        "",
        "## 抽样匹配",
        "",
    ]
    if len(mdf):
        sample = mdf.head(20)
        lines += ["| 类型 | 楼宇组 | 日期差 | 28Hse 日期 | 28Hse 单元 | Midland 单元 | 28Hse 月租 | Midland 月租 | 面积 |", "|---|---|---:|---|---|---|---:|---:|---:|"]
        for r in sample.itertuples():
            lines.append(f"| {r.match_type} | {r.group_id} | {r.date_delta}天 | {r.date} | {r.hse_unit} | {r.mid_unit} | {r.hse_rent:,.0f} | {r.mid_rent:,.0f} | {r.hse_area:,.0f} |")
    else:
        lines.append("没有找到同组同日同租金的匹配对。")
    lines += ["", "## 解释", "", "- exact 比例低不等于数据错误：两个平台的租务成交均为市场申报，日期、面积口径、四舍五入和经纪录入都可能不同。", "- 这份报告只做重叠校验，不用来替代租金中位数；主指标仍按楼宇组内 12 个月配对计算。", ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
