# 香港住宅租售比验证报告

- 生成时间：2026-10-28T18:08:31+08:00
- 数据截止：2026-08
- 计算层级：区域 + RVD 类别 + 月度；地图层级：香港/九龙/新界三大区

## 结果

- Hong Kong：毛回报率 3.26%，月租 505.0 HK$/m²，售价 180625.0 HK$/m²，类别样本 5
- Kowloon：毛回报率 3.19%，月租 457.0 HK$/m²，售价 175618.0 HK$/m²，类别样本 5
- New Territories：毛回报率 2.96%，月租 307.0 HK$/m²，售价 116190.0 HK$/m²，类别样本 5

## 验证

- PASS raw_csv_vs_rvd_parquet 1.1M: {"check": "raw_csv_vs_rvd_parquet", "table": "1.1M", "title": "PRIVATE  DOMESTIC  -  AVERAGE  RENTS  BY  CLASS [MONTHLY]", "raw_non_null_rows": 4973, "tidy_non_null_rows": 4973, "missing_or_extra_keys": 0, "value_mismatches": 0, "remark_mismatches": 0, "pass": true}
- PASS raw_csv_vs_rvd_parquet 1.2M: {"check": "raw_csv_vs_rvd_parquet", "table": "1.2M", "title": "PRIVATE  DOMESTIC  -  AVERAGE  PRICES  BY  CLASS [MONTHLY]", "raw_non_null_rows": 4950, "tidy_non_null_rows": 4950, "missing_or_extra_keys": 0, "value_mismatches": 0, "remark_mismatches": 0, "pass": true}
- PASS raw_csv_vs_rvd_parquet 1.3M: {"check": "raw_csv_vs_rvd_parquet", "table": "1.3M", "title": "PRIVATE  DOMESTIC  -  RENTAL  INDICES  BY  CLASS  ( TERRITORY-WIDE ) [MONTHLY]", "raw_non_null_rows": 3232, "tidy_non_null_rows": 3232, "missing_or_extra_keys": 0, "value_mismatches": 0, "remark_mismatches": 0, "pass": true}
- PASS raw_csv_vs_rvd_parquet 1.4M: {"check": "raw_csv_vs_rvd_parquet", "table": "1.4M", "title": "PRIVATE  DOMESTIC  -  PRICE  INDICES  BY  CLASS  ( TERRITORY-WIDE ) [MONTHLY]", "raw_non_null_rows": 3232, "tidy_non_null_rows": 3232, "missing_or_extra_keys": 0, "value_mismatches": 0, "remark_mismatches": 0, "pass": true}
- PASS mirror_manifest_sha256 raw/1.1M.csv: {"check": "mirror_manifest_sha256", "file": "raw/1.1M.csv", "expected_sha256": "4fa2c99820dae1488b59dff814d76d96169d8878937d4e830131b9e0662dbf3d", "actual_sha256": "4fa2c99820dae1488b59dff814d76d96169d8878937d4e830131b9e0662dbf3d", "bytes_expected": 29253, "bytes_actual": 29253, "pass": true}
- PASS mirror_manifest_sha256 raw/1.2M.csv: {"check": "mirror_manifest_sha256", "file": "raw/1.2M.csv", "expected_sha256": "4f398097f02a5ef003b92ccd8612144f2e440b1d499dc3678586d3f1a49fda42", "actual_sha256": "4f398097f02a5ef003b92ccd8612144f2e440b1d499dc3678586d3f1a49fda42", "bytes_expected": 41936, "bytes_actual": 41936, "pass": true}
- PASS mirror_manifest_sha256 raw/1.3M.csv: {"check": "mirror_manifest_sha256", "file": "raw/1.3M.csv", "expected_sha256": "94953fe99a89e3e00eb5c557dd02184dda0e5621aa2ae9ee059e07e63fc26ad4", "actual_sha256": "94953fe99a89e3e00eb5c557dd02184dda0e5621aa2ae9ee059e07e63fc26ad4", "bytes_expected": 25298, "bytes_actual": 25298, "pass": true}
- PASS mirror_manifest_sha256 raw/1.4M.csv: {"check": "mirror_manifest_sha256", "file": "raw/1.4M.csv", "expected_sha256": "5c298ae13c1406cb77fcbfcebea16d4a72fc867c418831451ba4f49d659822ea", "actual_sha256": "5c298ae13c1406cb77fcbfcebea16d4a72fc867c418831451ba4f49d659822ea", "bytes_expected": 25270, "bytes_actual": 25270, "pass": true}
- PASS mirror_manifest_sha256 rvd.parquet: {"check": "mirror_manifest_sha256", "file": "rvd.parquet", "expected_sha256": "a4ed7dd86edf002a39dae5d29e252dce592f443f1c12e48e2f4e0a9f9fd360c7", "actual_sha256": "a4ed7dd86edf002a39dae5d29e252dce592f443f1c12e48e2f4e0a9f9fd360c7", "bytes_expected": 466808, "bytes_actual": 466808, "pass": true}
- PASS mirror_manifest_sha256 areas.parquet: {"check": "mirror_manifest_sha256", "file": "areas.parquet", "expected_sha256": "aa3b8841e1608fce38178b77c2b673daafc07e19953c695d2239cdc6757e99a2", "actual_sha256": "aa3b8841e1608fce38178b77c2b673daafc07e19953c695d2239cdc6757e99a2", "bytes_expected": 324416, "bytes_actual": 324416, "pass": true}
- PASS derived_aggregate_vs_official_index_trend rent: {"check": "derived_aggregate_vs_official_index_trend", "metric": "rent", "periods_compared": 320, "sign_concordance": 0.83125, "yoy_correlation": 0.767397, "pass": true, "note": "Aggregate of RVD regional/class averages compared with RVD territory-wide index; levels are not expected to be identical because index weighting differs."}
- PASS derived_aggregate_vs_official_index_trend price: {"check": "derived_aggregate_vs_official_index_trend", "metric": "price", "periods_compared": 320, "sign_concordance": 0.85625, "yoy_correlation": 0.874742, "pass": true, "note": "Aggregate of RVD regional/class averages compared with RVD territory-wide index; levels are not expected to be identical because index weighting differs."}
- PASS centaline_ccl_raw_integrity : {"check": "centaline_ccl_raw_integrity", "source": "Centaline CCL public API", "points": 1711, "first_date": "1994-01-02", "last_date": "2026-10-11", "latest_index": 160.05, "vote_date": "2026-10-11T00:00:00", "publish_date": "2026-10-16T16:00:00", "pass": true, "note": "Exact value-level integrity check on the downloaded public JSON; no interpolation is used here."}
- PASS derived_rvd_average_vs_centaline_ccl : {"check": "derived_rvd_average_vs_centaline_ccl", "periods_compared": 320, "sign_concordance": 0.83125, "yoy_correlation": 0.874017, "pass": true, "note": "RVD regional/class average price vs Centaline CCL; CCL is a weekly transaction-based index and RVD uses a different methodology, so exact levels are not expected."}
- PASS rvd_official_index_vs_centaline_ccl : {"check": "rvd_official_index_vs_centaline_ccl", "periods_compared": 320, "sign_concordance": 0.94375, "yoy_correlation": 0.971997, "pass": true, "note": "RVD All Classes price index vs Centaline CCL; CCL is a weekly transaction-based index and RVD uses a different methodology, so exact levels are not expected."}
- PASS centaline_detail_formula_and_coverage : {"check": "centaline_detail_formula_and_coverage", "sale_rows": 904, "rent_rows": 990, "matched_cells": 68, "districts": 18, "formula_exact": true, "published_unit_price_mismatches_over_0_51_hkd": 0, "building_points": 40, "coordinates_in_hong_kong_bounds": true, "pass": true, "note": "Current snapshot sample; same district + saleable-area band + month pairing. Building points use Centaline building-group grid coordinates converted from HK 1980 Grid to WGS84; 0.51 tolerance covers published integer rounding."}
- PASS news_vs_our_public_index : {"check": "news_vs_our_public_index", "source_name": "RTHK", "headline": "HK home prices ease slightly in July", "period": "2026-07", "reported_value": 321.5, "our_value": 320.3, "absolute_difference": -1.1999999999999886, "pass": true, "exact_match": false, "note": "RTHK reports the RVD all-size price index for July 2026. The reported figure differs from the current RVD snapshot; keep this as a visible revision/definition gap.", "source_url": "https://gbcode.rthk.hk/TuniS/news.rthk.hk/rthk/en/component/k2/1867761-20260827.htm"}
- PASS crawl_manifest_integrity : {"check": "crawl_manifest_integrity", "leaves": 205, "page_files": 3366, "pages_match": true, "hash_sample_checked": 40, "hash_sample_ok": 40, "pass": true, "note": "抽样核对了 40 个原始分页的 SHA-256 与字节数；全量哈希清单见 manifest.json"}
- PASS full_transaction_corpus_coverage : {"check": "full_transaction_corpus_coverage", "records": 1806389, "scope": "full_history", "api_total": 2595027, "coverage_pct": 69.61, "duplicates_dropped": 50, "date_min": "1990-01-12", "date_max": "2026-10-28", "pass": true, "note": "中原接口 day='' 全历史；当前快照覆盖地图明细组，非全港全量"}
- PASS building_level_map_points : {"check": "building_level_map_points", "building_points": 1973, "building_with_yield": 1328, "grid_coord_points": 996, "estate_fallback_points": 332, "estate_points": 421, "coordinates_in_hong_kong_bounds": true, "pass": true, "note": "楼宇组级点位（CentaMap 坐标）+ 屋苑中心近似坐标（明确标注 geo=estate）"}
- PASS external_chart_corroboration : {"check": "external_chart_corroboration", "etnet_vs_lr_exact_months": "67/67", "price_etnet_vs_ours_corr": 0.9227, "price_etnet_vs_ours_3m_direction": 87.88, "volume_ours_vs_lr_corr": 0.9343, "pass": true, "note": "ET Net 成交宗数逐月复现官方 LR；ET Net 均价 vs 我们的等权屋苑中位呎价强相关；我们的成交量 vs LR 相关 0.9+"}

## 限制

- RVD 1.1M/1.2M 是区域和类别平均值，不是逐套配对的挂牌或成交记录。
- RVD 1.3M/1.4M 与 Centaline CCL 是独立指数；聚合平均价、RVD 指数和 CCL 的水平不应被要求逐点相等。
- 地图同时包含 RVD 三大区历史层与 Centaline 18 区样本快照层；两者时间窗、单位和来源分开标注。
- 当前环境直连 RVD/data.gov.hk 发生连接关闭，因此原始文件通过公开可追溯镜像下载，并用镜像 manifest 校验；报告保留官方原始 URL 供复核。
