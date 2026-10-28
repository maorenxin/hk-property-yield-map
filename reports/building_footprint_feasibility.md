# 建筑轮廓（围栏）替代气泡点 — 可行性调研（2026-10-18）

## 结论
可行，但**不建议一次性把 1,982 个圆点全部换成建筑轮廓**：约 **1,679 组（84.7%）是多栋/街区复合组**，不是单栋楼；最近邻直接套面会产生明显误配（实测大角咀 29 个点中仅 14 个落在最近轮廓内，18 个存在 10 米内并列候选）。

推荐分阶段：**官方 CSDI Building 主源 + OSM 补缺**；Phase 1 先做 616 个高置信组（31%），未匹配组保留气泡。

## 数据源（实测）
| 数据源 | 许可 | 获取 | 规模 | 结论 |
|---|---|---|---|---|
| CSDI Building（地政总署） | 政府开放数据条款（需署名/免责声明） | FeatureServer，无 key，单次≤3,000 | 342,195 有效建筑面（Tower 213,358 / Podium 9,034），月度更新 | 首选主源 |
| iB1000 数码地形图 | 同上 | data.gov.hk / HKMS 2.0（GML/FGDB/DWG） | 单幅 GML 约 3MB；无缝 FGDB 1.84GB；双周更新 | 官方备源 |
| OSM / Overpass | ODbL 1.0（署名 + share-alike） | 公共 Overpass，无限流 key | 大角咀 927 个 building way + 8 relation；太古城 288+6；日出康城 144 | 补缺源 |
| Overture Buildings | ODbL | S3/Azure/PMTiles | 全球约 277GB | 暂不推荐 |

## 实测证据
- CSDI：大角咀范围 1,429 个建筑面（1,213 Tower，810 有名称），太古城 475 个；查询 4–7 秒/次。
- OSM：大角咀 935 个要素，响应 9.3 秒/819KB；927 个 way 中仅 196 个有名称（21%）、278 个有地址（30%）。
- OSM 缺新楼：Larchwood 在 OSM 仍是 `landuse=construction`（非 building）；官方 Building 同坐标 10 米内可查到 2 个建筑面。
- 匹配风险：大角咀 29 个地图组最近轮廓中心距离均 ≤25 米，但只有 14/29 点落在多边形内。

## 我们数据的结构
- 1,982 个地图组；938 组有回报率；geo=grid 1,508 / estate 474。
- 1,679 组出现 ≥2 个栋名；663 组有逐栋明细（6,497 栋，平均 9.8 栋/组）。
- 1,454 组至少有一个 RVD 楼龄锚点：其中"有逐栋明细+RVD" 616 组（31.1%）、"无逐栋明细+RVD" 838 组（42.3%）、其余约 528 组（26.6%）。

## 推荐路线
- **Phase 1（建议先做）**：616 个"逐栋明细 + RVD 锚点"组；按楼名/地址 + 坐标 15–25 米双条件匹配 CSDI Tower/Active，组内多栋 union；zoom≥15 显示轮廓；填充色 = 回报率；不再画对应气泡，成交宗数用角标/hover。
- **Phase 2**：扩展到 838 个有 RVD 锚点但无逐栋明细的组（屋苑名/地址匹配整组 Tower+Podium）；理论上限覆盖 1,454/1,982 ≈ 73.4%；先做 100 组金标准验收。
- **Phase 3**：剩余约 528 组保留气泡或人工补录；如需全港逐栋，再上 PMTiles/矢量瓦片。

## 前端与性能
- Leaflet `preferCanvas:true` 已就绪，多边形 Canvas 图层可与热力/柱状图共存。
- 体积：大角咀 927 面最小 GeoJSON 330KB；6,497 栋约 2.3MB；全港 342,195 面约 124MB（不可进首屏）。建议只发布匹配组的 union 轮廓，按区分片、0.5 米简化、6 位小数、gzip。
- 交互：matched=多边形（填充=回报率），unmatched=气泡；圆面积原本表达成交宗数，换成面后需改用角标/hover。

## 风险
1. **坐标偏移**：高德底图 GCJ-02；OSM/CSDI 为 WGS84/HK1980。实测大角咀偏移约 +0.0050°E / -0.0027°N（约 0.6km），须"HK1980→WGS84→GCJ-02"只纠偏一次。
2. 官方 342k 面含 Tower/Podium/临时结构；全港有名称仅 68,185（19.9%），不能只靠名称匹配。
3. 许可：官方数据需标注"地政总署 / DATA.GOV.HK"；OSM/Overture 为 ODbL（衍生分发需评估 share-alike）。非法律意见。
4. 待补：Overture 香港 bbox 实际要素数、CSDI 全量下载大小、616 组逐组几何精度。

（本调研只读，未改动生产站点。）

## Phase 1 实施结果（2026-10-18）

- 实际目标口径：街区/混合组 ∩ 逐栋明细 ∩ RVD 高置信楼龄锚点 = **595 组**（报告前文的 616 是早期“逐栋 + RVD”粗估）。
- 官方 CSDI 逐组查询 **595/595 成功**（全部经 `ssh japan`，无 DeepSeek 代理）；匹配 **551 组（92.6%）**，其中 name 434 / pip 30 / near 87 / none 44。
- 100 组分层金标准抽检：82 组 name/pip 高置信，10 组 near 低置信，8 组 none；OSM 对照 **65/89（73.0%）** 通过，因此上线以官方 CSDI 为主源。
- 详情见 `reports/footprint_gold_standard.md`、`reports/footprint_gold_sample.csv`、`site/footprint_review.html`。

## 本机 gov.hk TLS 可用路径（2026-10-18）

本机直连 `portal.csdi.gov.hk` 仍报 `SSL_ERROR_SYSCALL`，但经日本服务器建立 SSH 隧道后可用：

```bash
ssh -N -L 18443:portal.csdi.gov.hk:443 japan
curl --connect-to portal.csdi.gov.hk:443:127.0.0.1:18443 \
  'https://portal.csdi.gov.hk/server/rest/services?f=json'
```

实测返回 **HTTP 200**（`currentVersion: 10.91`）。本轮已用该 SSH 出口重抓全部 595 组，`site/footprints.js` 的来源说明已改为 SSH egress，不再依赖 DeepSeek Fetch 代理。
