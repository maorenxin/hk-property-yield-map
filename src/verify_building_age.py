#!/usr/bin/env python3
"""Cross-check building completion years against RVD's official Names of Buildings.

Input : data/raw/rvd/2026-10-18/{urban,nt}.xls  (RVD 樓宇名稱, XLS edition)
        data/normalized/centaline/transactions_2026-10-16.parquet
Output: data/derived/building_age_rvd.csv  (per building: our op_year vs RVD year)
        reports/building_age_verification.md (coverage / deltas / mismatch list)

Matching rules (conservative):
- exact Chinese building-name match (any of the 3 name columns) + address
  street/number agreement -> high confidence
- exact name match and unique within RVD -> medium
- address-only exact (name is address-like, e.g. 洋松街69號) -> medium
- ambiguous / conflicting matches are left unmatched (never guessed)
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RVD_DIR = ROOT / "data/raw/rvd/2026-10-18"
NORM = ROOT / "data/normalized/centaline/transactions_2026-10-16.parquet"
OUT_CSV = ROOT / "data/derived/building_age_rvd.csv"
OUT_MD = ROOT / "reports/building_age_verification.md"
SNAPSHOT_YEAR = 2026


def norm(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).replace("\r", "").replace("\n", "")
    s = s.replace("　", "").replace(" ", "")
    s = s.replace("（", "(").replace("）", ")")
    s = s.strip("*＊")
    return s


def is_generic_name(k: str) -> bool:
    """Block/phase labels like 1座 / A座 / 西座 / 2期 cannot be matched by name alone."""
    return bool(re.fullmatch(r"(第?\d+座|[A-Za-z]座|[東西南北]座|\d+期|[一二三四五六七八九十]+期|\d+號|洋房\w*|單位\w*)", k or ""))


def name_key(s) -> str:
    s = norm(s)
    s = re.sub(r"\(.*?\)", "", s)          # drop block letters in brackets
    return s.strip()


def split_addr(s):
    s = norm(s)
    m = re.search(r"(.+?)(\d+[A-Za-z]?(?:[-\u2013/及]\d+[A-Za-z]?)*)號", s)
    if not m:
        return "", set()
    return m.group(1), set(re.findall(r"\d+[A-Za-z]?", m.group(2)))


def addr_match(our: str, cand: str) -> bool:
    os_, onums = split_addr(our)
    cs_, cnums = split_addr(cand)
    if not onums or not cnums or not os_:
        return False
    street_ok = (os_ in cs_) or (cs_ in os_) or (os_[-4:] and os_[-4:] in cs_)
    return bool(street_ok and (onums & cnums))


def load_rvd() -> list[dict]:
    records = []
    for f in ("urban.xls", "nt.xls"):
        df = pd.read_excel(RVD_DIR / f, sheet_name="All 全部", header=0)
        cols = list(df.columns)
        c_year = [c for c in cols if "Year" in str(c)][0]
        c_remark = cols[14] if len(cols) > 14 else None
        prev_name = prev_year = None
        for _, r in df.iterrows():
            names = [norm(r[cols[i]]) for i in (1, 3, 5) if norm(r[cols[i]])]
            en = [norm(r[cols[i]]) for i in (0, 2, 4) if norm(r[cols[i]])]
            addrs = [norm(r[cols[i]]) for i in (7, 9, 11) if norm(r[cols[i]])]
            y = pd.to_numeric(r[c_year], errors="coerce")
            year = int(y) if pd.notna(y) and 1900 < y <= SNAPSHOT_YEAR else None
            main = names[0] if names else None
            # inherit year for continuation rows of the same building/estate
            if year is None and main and main == prev_name and prev_year:
                year = prev_year
            if main:
                prev_name, prev_year = main, year
            elif year is None:
                prev_name, prev_year = prev_name, prev_year
            if not names or year is None:
                continue
            records.append({"names": names, "en": en, "addrs": addrs, "year": year,
                            "remark": norm(r[c_remark]) if c_remark is not None else ""})
    return records


def main() -> int:
    rvd = load_rvd()
    by_name = defaultdict(list)
    by_addr = defaultdict(list)   # (street_token, number) -> records
    for rec in rvd:
        for n in rec["names"]:
            k = name_key(n)
            if k:
                by_name[k].append(rec)
        for a in rec["addrs"]:
            street, nums = split_addr(a)
            for num in nums:
                # index by a few trailing street characters to allow area prefixes
                for L in (3, 4, 5, 6):
                    if len(street) >= L:
                        by_addr[(street[-L:], num)].append(rec)
    print(f"RVD records with year: {len(rvd)}")
    # estate-level index for block-type buildings (1座/A座/12座 ...)
    by_estate = defaultdict(list)
    for rec in rvd:
        if rec["names"]:
            by_estate[name_key(rec["names"][0])].append(rec)

    df = pd.read_parquet(NORM, columns=["hma_id", "bldg_grp_id", "building_name",
                                        "address_raw", "district", "op_year", "listing_type",
                                        "estate_name", "phase_name"])
    df["op_year_num"] = pd.to_numeric(df.op_year.astype(str).str.extract(r"(\d{4})")[0], errors="coerce")
    df = df[df.hma_id.notna() & df.bldg_grp_id.notna()]
    bld = (df.groupby(["hma_id", "bldg_grp_id", "building_name", "address_raw"], dropna=False)
           .agg(op_year=("op_year_num", "median"), district=("district", "first"),
                estate_name=("estate_name", "first"), phase_name=("phase_name", "first"),
                n=("op_year_num", "size")).reset_index())
    print(f"our distinct buildings: {len(bld)}")

    rows = []
    for r in bld.itertuples():
        our_name, our_addr = r.building_name, r.address_raw
        nk = name_key(our_name)
        cands = by_name.get(nk, []) if nk else []
        pick, conf = None, None
        if cands:
            # prefer candidates whose address agrees; never accept a name-only match
            # when both sides carry addresses that disagree (same name in another district)
            addr_ok = [c for c in cands if any(addr_match(str(our_addr or ""), a) for a in c["addrs"])]
            if addr_ok:
                years = {c["year"] for c in addr_ok}
                if len(years) == 1:
                    pick, conf = addr_ok[0], "name+addr"
            elif (len(cands) == 1 and not is_generic_name(nk)
                  and (not cands[0]["addrs"] or not norm(our_addr))):
                pick, conf = cands[0], "name"
        if pick is None and not cands and re.search(r"\d+[A-Za-z]?(?:[-\u2013]\d+[A-Za-z]?)*號", norm(our_addr) or norm(our_name)):
            # address-only fallback: only when the building name itself is an address
            os_, onums = split_addr(str(our_addr or ""))
            cand = []
            if os_ and onums:
                seen = set()
                for num in onums:
                    for L in (6, 5, 4, 3):
                        if len(os_) < L:
                            continue
                        for rec in by_addr.get((os_[-L:], num), []):
                            if id(rec) not in seen:
                                seen.add(id(rec))
                                cand.append(rec)
            years = {c["year"] for c in cand}
            if len(years) == 1:
                pick = cand[0]
                conf = "addr"
        if pick is None and is_generic_name(nk):
            # second pass: 用「屋苑/期数」页级年份给座数型楼栋（1座/A座/12座）兜底
            estate = name_key(getattr(r, "estate_name", None))
            phase = norm(getattr(r, "phase_name", None))
            cands2 = []
            for k, recs in by_estate.items():
                if estate and (k == estate or k.startswith(estate) or estate.startswith(k)):
                    cands2.extend(recs)
            if cands2:
                phase_tokens = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}", phase) if t not in ("期",)]
                year = None
                if phase_tokens:
                    ph = [c for c in cands2 if any(
                        any(tok in nm for nm in c["names"]) for tok in phase_tokens)]
                    years = {c["year"] for c in ph}
                    if len(years) == 1:
                        year = years.pop()
                        pick, conf = ph[0], "estate+phase"
                if year is None:
                    years = {c["year"] for c in cands2}
                    if len(years) == 1:
                        pick, conf = cands2[0], "estate"
        rows.append({
            "hma_id": r.hma_id, "bldg_grp_id": r.bldg_grp_id,
            "building_name": r.building_name, "address_raw": r.address_raw,
            "district": r.district,
            "op_year_centaline": None if pd.isna(r.op_year) else int(r.op_year),
            "op_year_rvd": pick["year"] if pick else None,
            "match": conf or "none",
        })

    out = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(exist_ok=True)
    out.to_csv(OUT_CSV, index=False)

    m = out[out.op_year_rvd.notna()]
    both = m.dropna(subset=["op_year_centaline"])
    delta = (both.op_year_rvd - both.op_year_centaline)
    print(f"matched buildings: {len(m)} / {len(out)} ({len(m)/len(out)*100:.1f}%)")
    print(f"matched with both years: {len(both)} | |Δ|>2y: {(delta.abs() > 2).sum()} "
          f"| exact: {(delta == 0).sum()}")

    # per-group verification (building-weighted median, mirroring the map metric)
    hi = m[m.match.eq("name+addr")]
    grp = hi.dropna(subset=["op_year_centaline"]).groupby(["hma_id", "bldg_grp_id"]).apply(
        lambda g: pd.Series({
            "n_matched": len(g),
            "med_centaline": g.op_year_centaline.median(),
            "med_rvd": g.op_year_rvd.median(),
        }), include_groups=False).reset_index()
    grp["delta"] = grp.med_rvd - grp.med_centaline
    big = grp[grp.delta.abs() > 2].sort_values("delta", key=lambda s: s.abs(), ascending=False)

    lines = [
        "# 楼龄外部校核（RVD 官方《樓宇名稱 / Names of Buildings》）",
        "",
        f"- 校核日期：2026-10-18；RVD XLS 版（urban 4.4MB / nt 3.0MB，sha256 见 `data/raw/rvd/2026-10-18/manifest.json`）。",
        f"- RVD 可匹配楼宇（有落成年份）：{len(m):,} / {len(out):,} 栋（{len(m)/len(out)*100:.1f}%）。",
        f"- 与中原 `op_year` 均有值：{len(both):,} 栋；完全一致 {(delta == 0).sum():,}，|差|≤1 年 {(delta.abs() <= 1).sum():,}，|差|>2 年 {(delta.abs() > 2).sum():,}。",
        f"- 匹配类型：name+addr {(m.match.eq('name+addr')).sum():,}；estate+phase {(m.match.eq('estate+phase')).sum():,}（座数型楼栋按屋苑/期数页匹配）；name {(m.match.eq('name')).sum():,}；addr {(m.match.eq('addr')).sum():,}；estate {(m.match.eq('estate')).sum():,}。",
        "- 覆盖楼龄的口径：name+addr、estate+phase、name 三类；addr（地址重合但楼名不同，易混入单位/洋房）与 estate（整屋苑唯一年份，过粗）仅用于校核参考，不改写数据。",
        f"- 按楼宇组（按栋中位，仅高置信匹配）：{len(grp):,} 组；\\|Δ\\|>2 年 {len(big):,} 组。",
        "",
        "## 匹配口径",
        "- 以中文楼宇名 + 地址（街道+门牌）匹配；名称相同但多栋年份冲突且地址不能区分时**不猜**，计为未匹配。",
        "- RVD 落成年份 = 最早入伙纸年份（官方口径）；城市卷覆盖至 2023（个别 2024），新楼缺失时保留中原 op_year。",
        "",
        "## 差异最大的楼宇组（|Δ|>2 年，按 |Δ| 降序，前 40）",
        "",
        "| hma:grp | RVD 中位 | 中原中位 | 差 | 匹配栋数 |",
        "|---|---|---|---|---|",
    ]
    for r in big.head(40).itertuples():
        lines.append(f"| {r.hma_id}:{r.bldg_grp_id} | {int(r.med_rvd)} | {int(r.med_centaline)} | {int(r.delta):+d} | {int(r.n_matched)} |")
    lines += ["", "## 未匹配原因概况", "", "| 匹配方式 | 栋数 |", "|---|---|"]
    for k, v in out.match.value_counts().items():
        lines.append(f"| {k} | {v} |")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_CSV.relative_to(ROOT)} and {OUT_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
