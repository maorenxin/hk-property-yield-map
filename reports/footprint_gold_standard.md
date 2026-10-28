# 建筑轮廓 Phase 1：官方 CSDI 金标准抽检

- 数据源：地政总署 CSDI Building FeatureServer（342,195 个有效建筑面，月度更新）；本机 `*.gov.hk` TLS 不可达，构建期通过 SSH 日本出口直接取回原始 ArcGIS JSON，按组缓存。
- 目标：当前数据中「有逐栋明细 + 至少一个 RVD 高置信楼龄锚点 + 街区/混合组」的交集，共 **595 组**（调研中的 616 是早期「逐栋+RVD」粗估；本轮把逐栋序列扩到全部街区组后，Phase 1 的街区/混合交集为 595）。
- 匹配：官方面名/屋苑名优先；其次组点位落入面内；最后最近 Tower ≤30m。匹配多栋时保留 MultiPolygon，不强行合并成一个面。
- 匹配结果：name **434** / pip **30** / near **87** / none **44**，官方轮廓覆盖 **551/595（92.6%）**。
- 100 组分层抽检：92 个抽检组有官方轮廓，其中 **82 个达到 name/pip 高置信**；OSM 原型与官方可比较样本中，质心≤25m 且面积比 0.5–2.0（或互相包含）通过 **65/89（73.0%）**。
- 产物：`site/footprints.js`、`data/derived/footprints_phase1_official.geojson`、`reports/footprint_gold_sample.csv`、`site/footprint_review.html`。
- 补充：对 44 个官方 none 组，有 **13** 组存在 OSM 轮廓，已作为灰色虚线“OSM 补充轮廓”直接叠加到地图；其余仍保留气泡。
- 注意：CSDI 有名称字段仅覆盖部分建筑；官方轮廓是“组内可见建筑面”的并集近似，不等同于法律意义上的宗地边界。near 类型（87 组）和 none 类型需要人工复核后再宣称高置信。
