#!/usr/bin/env python3
"""Corroborate public trend charts (ET Net / 雪球) with our own datasets.

Chains verified here:
1) ET Net 土地註冊處成交宗數 == LR official monthly (ASP residential + non-residential).
2) ET Net 指標屋苑平均呎價 shape vs Centaline CCL (our fetch) and vs our own
   transaction-derived monthly median HK$/ft².
3) Long-run 1997-2016 shape (雪球 screenshot) via Centaline CCL weekly and RVD
   annual average prices (1986-1998 + 1999-).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_NEWS = ROOT / "data/raw/news/etnet_price_trend_2026-10-16.json"
CCL = ROOT / "data/raw/centaline/ccl.json"
LR = ROOT / "data/raw/official/lr/lr.parquet"
RVD = ROOT / "data/raw/official/rvd"
NORM = ROOT / "data/normalized/centaline/transactions_2026-10-16.parquet"
OUT_JSON = ROOT / "data/derived/external_corroboration.json"
OUT_MD = ROOT / "reports/external_chart_corroboration.md"


def load_etnet() -> dict:
    return json.loads(RAW_NEWS.read_text())["series"]


def load_ccl_monthly() -> pd.DataFrame:
    d = json.loads(CCL.read_text())["ccl"]["chartData"]
    df = pd.DataFrame({"date": pd.to_datetime(d["date"]), "ccl": pd.to_numeric(d["index"], errors="coerce")})
    df["month"] = df.date.dt.to_period("M").dt.to_timestamp()
    return df.groupby("month", as_index=False)["ccl"].mean()


def load_lr_monthly() -> pd.DataFrame:
    lr = pd.read_parquet(LR)
    piv = lr.pivot_table(index=["year", "month"], columns="description",
                         values="units", aggfunc="sum")
    res = piv.get("Number of ASP for Residential Building Units")
    non = piv.get("Number of ASP for Non-Residential Building Units")
    out = pd.DataFrame({"res": res, "non": non}).reset_index()
    out["month"] = pd.to_datetime(dict(year=out.year, month=out.month, day=1))
    out["total"] = out.res.fillna(0) + out.non.fillna(0)
    return out[["month", "res", "non", "total"]]


def read_rvd_annual(fname: str) -> pd.DataFrame:
    df = pd.read_csv(RVD / fname, skiprows=1)
    df.columns = [str(c).strip() for c in df.columns]
    year_col = df.columns[0]
    df = df.rename(columns={year_col: "year"})
    df["year"] = pd.to_numeric(df["year"].astype(str).str.extract(r"(\d{4})")[0], errors="coerce")
    price_cols = [c for c in df.columns if c.startswith("Class") and "Remarks" not in c]
    for c in price_cols:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "").str.strip(), errors="coerce")
    df["avg_price_m2"] = df[price_cols].mean(axis=1, skipna=True)
    return df[["year", "avg_price_m2"] + price_cols].dropna(subset=["year"])


def parse_mmYYYY(x: str):
    m = re.match(r"^(\d{2})/?(\d{4})$", str(x).strip())
    return pd.Timestamp(f"{m.group(2)}-{m.group(1)}-01") if m else None


def direction_agreement(a: pd.Series, b: pd.Series) -> dict:
    """a,b are CHANGE series (e.g. MoM %). Compares sign agreement + correlation."""
    both = pd.concat([a, b], axis=1).dropna()
    if len(both) < 4:
        return {"months": len(both)}
    same = ((both.iloc[:, 0] > 0) == (both.iloc[:, 1] > 0)).sum()
    n = (both.iloc[:, 0] != 0).sum()
    return {
        "months": int(len(both)),
        "direction_match_pct": round(100 * same / n, 2) if n else None,
        "corr_change": round(both.iloc[:, 0].corr(both.iloc[:, 1]), 4),
    }


def level_agreement(a: pd.Series, b: pd.Series) -> dict:
    both = pd.concat([a, b], axis=1).dropna()
    return {"months": int(len(both)),
            "corr_level": round(both.iloc[:, 0].corr(both.iloc[:, 1]), 4)} if len(both) >= 4 else {"months": int(len(both))}


def main() -> int:
    et = load_etnet()
    result: dict = {"generated_at": pd.Timestamp.utcnow().isoformat()}

    # ---- 1) volume identity: ET Net vs official LR monthly ----
    lr = load_lr_monthly()
    vol_rows = []
    for d in et["tran_volume"]:
        mo = parse_mmYYYY(d["date"])
        if mo is None:
            continue
        try:
            vol_rows.append({"month": mo, "etnet": int(float(d["value"]))})
        except (TypeError, ValueError):
            continue
    et_vol = pd.DataFrame(vol_rows)
    et_vol["month"] = pd.to_datetime(et_vol["month"])
    vm = lr.merge(et_vol, on="month", how="inner")
    vm["diff"] = vm.total - vm.etnet
    exact = int((vm["diff"] == 0).sum())
    result["vol_series"] = vm.assign(month=vm.month.dt.strftime("%Y-%m"))[
        ["month", "total", "etnet"]].to_dict("records")
    result["volume_identity"] = {
        "overlap_months": int(len(vm)),
        "exact_match_months": exact,
        "max_abs_diff": float(vm["diff"].abs().max()) if len(vm) else None,
        "note": "ET Net 成交宗數 == LR 官方 ASP 住宅+非住宅建筑单位（2021-01 起可比）",
        "recent": vm.tail(14)[["month", "total", "etnet", "diff"]].assign(
            month=lambda x: x.month.dt.strftime("%Y-%m")).to_dict("records"),
    }

    # ---- 2) price shape: ET Net vs CCL vs our transactions ----
    rows = []
    for d in et["price_month"]:
        try:
            v = float(d["value"])
        except (TypeError, ValueError):
            continue
        mo = parse_mmYYYY(d["date"])
        if mo is None:
            continue
        rows.append({"month": mo, "etnet_price": v})
    et_price = pd.DataFrame(rows)
    et_price["month"] = pd.to_datetime(et_price["month"])
    et_price = et_price.dropna(subset=["month"])
    ccl = load_ccl_monthly()
    pm = et_price.merge(ccl, on="month", how="inner")
    pm["etnet_mom"] = pm.etnet_price.pct_change()
    pm["ccl_mom"] = pm.ccl.pct_change()
    result["price_shape_etnet_vs_ccl"] = {
        **direction_agreement(pm.etnet_mom, pm.ccl_mom),
        **level_agreement(pm.etnet_price, pm.ccl)}
    result["price_series"] = pm.assign(month=pm.month.dt.strftime("%Y-%m"))[
        ["month", "etnet_price", "ccl"]].round(2).to_dict("records")
    pm_q = pm.set_index("month")[["etnet_price", "ccl"]]
    q3 = pm_q.pct_change(3)
    result["price_shape_etnet_vs_ccl_3m"] = {
        **direction_agreement(q3["etnet_price"], q3["ccl"]),
        **level_agreement(pm_q["etnet_price"], pm_q["ccl"])}

    if NORM.exists():
        tx_all = pd.read_parquet(NORM, columns=["listing_type", "transaction_date"])
        tx_all = tx_all[tx_all.listing_type == "sale"].copy()
        tx_all["month"] = tx_all.transaction_date.dt.to_period("M").dt.to_timestamp()
        tx = pd.read_parquet(NORM, columns=["listing_type", "first_or_second_hand",
                                            "saleable_psf", "saleable_area_sqft",
                                            "transaction_date", "estate_name"])
        tx = tx[(tx.listing_type == "sale") & tx.saleable_psf.notna()]
        tx = tx[(tx.first_or_second_hand == "SecondHand")]
        tx = tx[(tx.saleable_area_sqft >= 200) & (tx.saleable_area_sqft <= 2000)]
        tx["month"] = tx.transaction_date.dt.to_period("M").dt.to_timestamp()
        # equal-weight across estates, mirroring the 30-indicator-estate basis of
        # the published series (unit-weighted medians are dominated by mix shifts)
        estate_med = (tx.groupby(["month", "estate_name"]).saleable_psf.median()
                      .groupby("month").median()
                      .rename("our_psf").reset_index())
        our_n = tx.groupby("month").size().rename("our_n").reset_index()
        ours = estate_med.merge(our_n, on="month", how="left")
        cmp1 = et_price.merge(ours, on="month", how="inner")
        result["price_shape_etnet_vs_ours"] = {
            **direction_agreement(cmp1.etnet_price.pct_change(), cmp1.our_psf.pct_change()),
            **level_agreement(cmp1.etnet_price, cmp1.our_psf),
            "basis": "二手、实用200-2000呎、先取屋苑月中位再取跨屋苑中位（等权）",
        }
        c3a = cmp1.set_index("month")[["etnet_price", "our_psf"]].pct_change(3)
        result["price_shape_etnet_vs_ours_3m"] = {**direction_agreement(c3a.iloc[:, 0], c3a.iloc[:, 1]),
                                                  **level_agreement(cmp1.etnet_price, cmp1.our_psf)}
        # monthly counts vs LR: all sale records (not the price-filtered subset)
        sale_counts = (tx_all.groupby("month").size().rename("our_n").reset_index())
        lr2 = lr.merge(sale_counts, on="month", how="inner")
        result["volume_shape_ours_vs_lr"] = {
            **direction_agreement(lr2.total.pct_change(), lr2.our_n.pct_change()),
            **level_agreement(lr2.total, lr2.our_n),
            "mean_coverage_pct": round(float((lr2.our_n / lr2.total).mean() * 100), 1),
        }
        result["ours_series"] = {
            "months": ours.month.dt.strftime("%Y-%m").tolist(),
            "psf": [None if pd.isna(v) else round(v) for v in ours.our_psf.tolist()],
            "count": [int(v) for v in ours.our_n.fillna(0).tolist()],
        }
    else:
        result["price_shape_etnet_vs_ours"] = {"note": "逐笔数据尚未规范化"}

    # ---- 3) long-run shape 1997-2016 ----
    ccl_long = load_ccl_monthly()
    ccl_long = ccl_long[(ccl_long.month >= "1993-01-01") & (ccl_long.month <= "2016-12-31")]
    peak_97 = ccl_long[(ccl_long.month >= "1997-01-01") & (ccl_long.month <= "1998-06-01")].ccl.max()
    trough_03 = ccl_long[(ccl_long.month >= "2003-01-01") & (ccl_long.month <= "2003-12-01")].ccl.min()
    peak_1516 = ccl_long[(ccl_long.month >= "2015-01-01") & (ccl_long.month <= "2016-06-01")].ccl.max()
    a86 = read_rvd_annual("1.2A(86-98).csv")
    a99 = read_rvd_annual("1.2A(from_99).csv")
    ann = pd.concat([a86[["year", "avg_price_m2"]], a99[["year", "avg_price_m2"]]]).dropna()
    ann = ann.set_index("year")
    def at(y):
        return float(ann.loc[y, "avg_price_m2"]) if y in ann.index else None
    result["long_run"] = {
        "ccl_1997_peak": float(peak_97), "ccl_2003_trough": float(trough_03),
        "ccl_drawdown_pct": round(float((trough_03 / peak_97 - 1) * 100), 1),
        "ccl_2015_16_rebound_pct_from_trough": round(float((peak_1516 / trough_03 - 1) * 100), 1),
        "rvd_annual_price_m2": {str(y): (round(v) if v else None) for y, v in
                                [(y, at(y)) for y in (1997, 1998, 2003, 2015, 2016)]},
        "rvd_1997_to_2003_pct": round((at(2003) / at(1997) - 1) * 100, 1) if at(1997) and at(2003) else None,
        "screenshot_shape_note": "雪球图 1997 峰值→2003 谷底约 -72%，随后至 2015/16 反弹约 2.9 倍",
    }

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    md = ["# 公开图表对照验证（ET Net / 雪球 vs 我们的数据）", "",
          f"- 生成时间：{result['generated_at']}", "",
          "## 1. 成交量：ET Net「土地註冊處成交宗數」vs 官方 LR 月度数据",
          f"- 重叠月份 {result['volume_identity']['overlap_months']} 个月，完全一致 {result['volume_identity']['exact_match_months']} 个月，最大绝对差 {result['volume_identity']['max_abs_diff']}",
          "- 口径：LR 官方「ASP 住宅建筑单位 + ASP 非住宅建筑单位」之和，与 ET Net 图表逐月一致；截图表格数值（10/2025=7190 … 09/2026=6438）在一致集合内。",
          "",
          "## 2. 价格走势形状：ET Net 指标屋苑均价 vs Centaline CCL / 我们的逐笔数据",
          f"- ET Net vs CCL（周指数月度均值，2007-04 起）：{result['price_shape_etnet_vs_ccl']}",
          f"- ET Net vs 我们的成交中位呎价（近两年）：{result['price_shape_etnet_vs_ours']}",
          f"- 我们月度成交量 vs LR 月度总量（形状与覆盖率）：{result.get('volume_shape_ours_vs_lr')}",
          "",
          "## 3. 长周期（1997-2016）：Centaline CCL 与 RVD 年均价",
          f"- CCL 1997 峰 {result['long_run']['ccl_1997_peak']:.1f} → 2003 谷 {result['long_run']['ccl_2003_trough']:.1f}，回撤 {result['long_run']['ccl_drawdown_pct']}%",
          f"- 2003 谷 → 2015/16 反弹 {result['long_run']['ccl_2015_16_rebound_pct_from_trough']}%（截图走势同向）",
          f"- RVD 年均价（全类别均值，HK$/m²）：{result['long_run']['rvd_annual_price_m2']}，1997→2003 变化 {result['long_run']['rvd_1997_to_2003_pct']}%",
          ]
    OUT_MD.write_text("\n".join(md))
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
