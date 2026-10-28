# 公开图表对照验证（ET Net / 雪球 vs 我们的数据）

- 生成时间：2026-10-16T14:48:38.973446+00:00

## 1. 成交量：ET Net「土地註冊處成交宗數」vs 官方 LR 月度数据
- 重叠月份 67 个月，完全一致 67 个月，最大绝对差 0.0
- 口径：LR 官方「ASP 住宅建筑单位 + ASP 非住宅建筑单位」之和，与 ET Net 图表逐月一致；截图表格数值（10/2025=7190 … 09/2026=6438）在一致集合内。

## 2. 价格走势形状：ET Net 指标屋苑均价 vs Centaline CCL / 我们的逐笔数据
- ET Net vs CCL（周指数月度均值，2007-04 起）：{'months': 227, 'direction_match_pct': np.float64(73.89), 'corr_change': np.float64(0.4129), 'corr_level': np.float64(0.9904)}
- ET Net vs 我们的成交中位呎价（近两年）：{'months': 36, 'direction_match_pct': np.float64(77.14), 'corr_change': np.float64(0.3249), 'corr_level': np.float64(0.9227), 'basis': '二手、实用200-2000呎、先取屋苑月中位再取跨屋苑中位（等权）'}
- 我们月度成交量 vs LR 月度总量（形状与覆盖率）：{'months': 36, 'direction_match_pct': np.float64(80.0), 'corr_change': np.float64(0.7468), 'corr_level': np.float64(0.9343), 'mean_coverage_pct': 92.2}

## 3. 长周期（1997-2016）：Centaline CCL 与 RVD 年均价
- CCL 1997 峰 101.3 → 2003 谷 31.9，回撤 -68.5%
- 2003 谷 → 2015/16 反弹 359.2%（截图走势同向）
- RVD 年均价（全类别均值，HK$/m²）：{'1997': 83599, '1998': 58880, '2003': 33717, '2015': 143616, '2016': 141581}，1997→2003 变化 -59.7%