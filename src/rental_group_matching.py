#!/usr/bin/env python3
"""Deterministic phase/tower -> building-group matcher for rental sources.

The matcher is deliberately conservative: an exact phase/tower or garden-name
rule wins; ambiguous or conflicting rules produce no group id and are audited.
It is shared by the 28Hse and Midland parsers.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config/rental_group_rules.json"
RULES = json.loads(RULES_PATH.read_text(encoding="utf-8"))

VARIANTS = str.maketrans({
    "阁": "閣", "楼": "樓", "台": "台", "臺": "台", "湾": "灣",
    "丽": "麗", "华": "華", "恒": "恆", "汇": "匯", "玺": "璽",
    "东": "東", "龙": "龍", "凤": "鳳", "银": "銀", "铁": "鐵",
})
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass
class MatchResult:
    group_id: str | None
    match_method: str
    match_confidence: int
    match_reason: str
    review_flag: bool
    candidate_groups: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def norm(s) -> str:
    v = unicodedata.normalize("NFKC", str(s or "")).strip().lower().translate(VARIANTS)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", v)


def _phase_set(raw: str) -> set[str]:
    n = unicodedata.normalize("NFKC", str(raw or "")).lower().translate(VARIANTS)
    out: set[str] = set()
    for a in re.findall(r"(\d{1,2}[a-z]?)\s*期", n):
        out.add(a)
    for a in re.findall(r"(?:phase|p)\s*([0-9]{1,2}[a-z]?)", n, re.I):
        out.add(a.lower())
    for cn in re.findall(r"([一二三四五六七八九十]+)期", n):
        out.add(str(CN_DIGITS.get(cn[0], cn)))
    if "5/6期" in n or "5-6期" in n:
        out.update({"5", "6"})
    return out


def _num_token(s: str) -> int | None:
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def towers(raw: str) -> set[int]:
    n = unicodedata.normalize("NFKC", str(raw or "")).lower()
    out: set[int] = set()
    # Blocks / 座: capture the whole list/range before the unit marker.
    for m in re.finditer(r"((?:\d{1,3}[a-z]?\s*(?:[-–~至,、&及/]\s*)?)+)\s*(?:座|block)\s*", n):
        chunk = m.group(1)
        for part in re.split(r"[,、&及]", chunk):
            part = part.strip()
            if not part:
                continue
            rng = re.match(r"(\d{1,3})\s*[-–~至]\s*(\d{1,3})", part)
            if rng:
                lo, hi = int(rng.group(1)), int(rng.group(2))
                if lo <= hi and hi - lo <= 60:
                    out.update(range(lo, hi + 1))
            else:
                x = _num_token(part)
                if x is not None:
                    out.add(x)
    for m in re.finditer(r"(?:第)?\s*(\d{1,3})\s*座", n):
        out.add(int(m.group(1)))
    for m in re.finditer(r"block\s*(\d{1,3})", n):
        out.add(int(m.group(1)))
    return out


def tower_labels(raw: str) -> set[str]:
    """Tower labels that preserve letter suffixes: 13A, 23A, 5B."""
    n = unicodedata.normalize("NFKC", str(raw or "")).lower().translate(VARIANTS)
    out: set[str] = set()
    for m in re.finditer(r"(\d{1,2})\s*([a-z])?\s*座", n):
        out.add(str(int(m.group(1))) + (m.group(2) or ""))
    for m in re.finditer(r"block\s*(\d{1,2})\s*([a-z])?", n):
        out.add(str(int(m.group(1))) + (m.group(2) or ""))
    return out


def letter_towers(raw: str) -> set[str]:
    """Letter blocks: A座, B座, ... (淘大花園 / 德福花園 style)."""
    n = unicodedata.normalize("NFKC", str(raw or "")).upper()
    return {m.group(1).upper() for m in re.finditer(r"(?<![A-Z])([A-Z])\s*座", n)}


def address_numbers(raw: str) -> set[int]:
    """Numbers before 號, ignoring the unit number after a floor marker."""
    n = unicodedata.normalize("NFKC", str(raw or "")).lower().translate(VARIANTS)
    addr = re.split(r"(高層|中層|低層|地下|頂層|天台|g/f|lg/f|\d+樓)", n)[0]
    out: set[int] = set()
    for m in re.finditer(r"(\d{1,3})\s*(?:[-–~至]\s*(\d{1,3}))?\s*號", addr):
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if lo <= hi and hi - lo <= 50:
            out.update(range(lo, hi + 1))
    return out


def detect_estate(raw: str, hint: str = "") -> str | None:
    text = norm(raw) + "|" + norm(hint)
    best = None
    for name, cfg in RULES.get("estates", {}).items():
        for alias in cfg.get("aliases", []):
            if norm(alias) and norm(alias) in text:
                if best is None or len(norm(alias)) > len(best[0]):
                    best = (norm(alias), name)
    return best[1] if best else None


def _ok(gid: str, method: str, confidence: int, reason: str, candidates: Iterable[str] = ()) -> MatchResult:
    return MatchResult(gid, method, confidence, reason, False, ";".join(sorted(set(candidates))))


def _fail(method: str, reason: str, candidates: Iterable[str] = ()) -> MatchResult:
    return MatchResult(None, method, 0, reason, True, ";".join(sorted(set(candidates))))


def match_rental(raw_text: str, estate_hint: str = "", phase_raw: str = "", tower_raw: str = "") -> MatchResult:
    """Match one rental raw record.

    `raw_text` should include the source's full display string where possible
    (28Hse stores it in `unit`; Midland can join phase/building/unit fields).
    """
    raw = " ".join(str(x or "") for x in (raw_text, phase_raw, tower_raw))
    estate = detect_estate(raw, estate_hint)
    if not estate:
        return _fail("unmatched", "estate_not_recognized")
    cfg = RULES["estates"][estate]
    n = norm(raw)
    if cfg.get("type") == "estate_only":
        return _ok(cfg["group"], "synthetic_estate", 90,
                   "source has no bldgGrpId; estate-level synthetic group")
    ts = towers(raw)
    tl = tower_labels(raw)
    letters = letter_towers(raw)
    ph = _phase_set(raw)

    if estate == "太古城":
        # Phase-wide groups are safe only when they describe the whole phase.
        for phase, gid in (cfg.get("phase_groups") or {}).items():
            if norm(phase) in n:
                return _ok(gid, "exact_phase", 100, f"phase={phase}")
        garden_map = cfg.get("gardens") or {}
        # Remove phase/terrace labels before scanning garden names: in
        # "海天花園 景天閣", 海天 is a phase prefix, not a garden.  Keep the
        # original string so 海天閣 without a phase still matches.
        garden_text = n
        for phase in ("海天花園", "海景花園", "星輝台", "高山台", "安盛台", "金殿台", "翠湖台", "觀海台"):
            garden_text = garden_text.replace(norm(phase), "")
        for garden in sorted(garden_map, key=lambda x: len(norm(x)), reverse=True):
            if norm(garden) in garden_text or (garden_text == n and norm(garden) in n):
                return _ok(garden_map[garden], "exact_tower_name", 100, f"garden={garden}")
        for t in ts:
            garden = (cfg.get("tower_to_garden") or {}).get(str(t))
            if garden and norm(garden) in n:
                return _ok(garden_map[garden], "exact_phase_tower", 100, f"tower={t}->{garden}")
        return _fail("unmatched", "taikoo_garden_missing")

    if estate == "匯璽":
        has_iii = "iii" in n or "5期" in n
        has_ii = "ii" in n.replace("iii", "") or "3期" in n
        if has_iii and has_ii and "3期" in n:
            return _fail("phase_conflict", "3期_with_III")
        if has_iii:
            return _ok(cfg["groups"]["III"], "exact_phase", 100, "phase=5期/III")
        if has_ii:
            return _ok(cfg["groups"]["II"], "exact_phase", 100, "phase=3期/II")
        return _ok(cfg["groups"]["I"], "phase_only", 95, "phase=1/2A/2B/unspecified")

    if estate == "日出康城":
        projects = cfg.get("projects") or []
        # Longest project alias wins: 晉海II must beat 晉海.
        found = []
        for rule in projects:
            for alias in rule.get("aliases", []):
                if norm(alias) in n:
                    found.append((len(norm(alias)), alias, rule))
        if not found:
            return _fail("unmatched", "lohas_project_missing")
        max_len = max(x[0] for x in found)
        found = [x for x in found if x[0] == max_len]
        matched = []
        for _, alias, rule in found:
            rt = set(rule.get("towers") or [])
            if (not rt) or (ts & rt):
                matched.append((rule["group"], alias))
        groups = sorted({g for g, _ in matched})
        if len(groups) == 1:
            return _ok(groups[0], "exact_phase_tower", 100, f"project={matched[0][1]}")
        if not groups:
            return _fail("unmatched", "lohas_tower_not_in_group", [r["group"] for _, _, r in found])
        return _fail("ambiguous_tie", "lohas_multiple_groups", groups)

    if estate == "美孚新邨":
        nums = address_numbers(raw)
        exact_hits, range_hits = [], []
        for rule in cfg.get("rules") or []:
            rp = {str(x).lower() for x in (rule.get("phases") or [])}
            rs = [norm(x) for x in (rule.get("streets") or []) if norm(x)]
            rn = set(rule.get("numbers") or [])
            if rp and not (ph & rp):
                continue
            if rs and not any(x in n for x in rs):
                continue
            if rn and (nums & rn):
                exact_hits.append(rule["group"])
            elif rn and nums and min(rn) <= min(nums) <= max(rn):
                range_hits.append(rule["group"])
            elif not rn and not rs:
                exact_hits.append(rule["group"])
        exact_hits = sorted(set(exact_hits))
        range_hits = sorted(set(range_hits) - set(exact_hits))
        if len(exact_hits) == 1:
            return _ok(exact_hits[0], "exact_address", 100, f"phase={sorted(ph)} nums={sorted(nums)}")
        if not exact_hits and len(range_hits) == 1:
            return _ok(range_hits[0], "range_address", 90, f"phase={sorted(ph)} nums={sorted(nums)}")
        hits = sorted(set(exact_hits + range_hits))
        if not hits:
            return _fail("unmatched", "meifoo_address_not_in_group")
        return _fail("ambiguous_tie", "meifoo_multiple_groups", hits)

    rules = cfg.get("rules") or []
    hits = []
    for rule in rules:
        rp = {str(x).lower() for x in (rule.get("phases") or [])}
        rn = [norm(x) for x in (rule.get("phase_names") or []) if norm(x)]
        rt = set(rule.get("towers") or [])
        rtl = {str(x).lower() for x in (rule.get("tower_labels") or [])}
        rl = {str(x).upper() for x in (rule.get("letters") or [])}
        phase_ok = (not rp and not rn) or (bool(ph) and bool(ph & rp)) or any(x in n for x in rn)
        if not phase_ok:
            continue
        if rtl:
            if not (tl & rtl):
                continue
        elif rt and not (ts & rt):
            continue
        if rl and not (letters & rl):
            continue
        if not ph and not ts and not tl and not letters and not rn:
            continue
        hits.append(rule["group"])
    hits = sorted(set(hits))
    if len(hits) == 1:
        method = "exact_phase_tower" if ph and ts else "phase_only"
        return _ok(hits[0], method, 100 if ph and ts else 95,
                   f"phase={sorted(ph)} tower={sorted(ts)}")
    if not hits:
        return _fail("unmatched", "phase_tower_not_in_group")
    return _fail("ambiguous_tie", "multiple_groups_after_phase_tower", hits)


def match_28hse_row(row: dict) -> MatchResult:
    return match_rental(str(row.get("unit") or ""), str(row.get("estate_slug") or ""))


def match_midland_row(row: dict) -> MatchResult:
    raw = " ".join(str(row.get(k) or "") for k in ("estate_name", "phase_name", "building_name", "floor", "unit"))
    return match_rental(raw, str(row.get("estate_name") or ""), str(row.get("phase_name") or ""),
                        str(row.get("building_name") or ""))


if __name__ == "__main__":
    for text in [
        "太古城 海景花園(東) 美菊閣 高層 F室",
        "太古城 海景花園(西) 青松閣",
        "太古城 高山台 泰山閣",
        "匯璽 3期(匯璽II) 5B座 高層",
        "匯璽 5期(匯璽III) 8座 高層",
        "日出康城 Malibu 2座",
        "映灣園 2期 7座",
        "珀麗灣 3期 09座",
        "沙田第一城 1期 08座",
        "沙田第一城 2期 20座",
        "沙田第一城 6期 24座",
    ]:
        print(text, match_rental(text).as_dict())
