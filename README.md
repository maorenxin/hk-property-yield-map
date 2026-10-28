# 香港住宅租售比研究

**长期访问地图：** https://maorenxin.github.io/hk-property-yield-map/

- 数据每天香港时间 **02:00** 自动更新（GitHub Actions，`workflow_dispatch` 也可手动触发）。
- 静态地图由 GitHub Pages 发布；全量原始数据与最新核心数据在仓库的 Releases 中。
- 技术实现与人工复核入口见 `README.md` 后续章节；数据许可与边界见 `DATA_NOTICE.md`。
- GitHub 仓库：https://github.com/maorenxin/hk-property-yield-map
- 全量数据：https://github.com/maorenxin/hk-property-yield-map/releases/tag/data-full-2026-10-28
- 每日核心快照：https://github.com/maorenxin/hk-property-yield-map/releases/tag/data-latest

> 自动更新只刷新香港逐笔成交/租赁、楼栋指标和地图。


以公开数据计算并交叉验证香港住宅的**年化毛租金回报率**（gross_yield_pct = 月租 × 12 ÷ 售价 × 100），
当前包含两层数据与一张楼栋级交互地图：

1. **官方基准层**：差饷物业估价署（RVD）区域 × 类别平均租金/售价与指数（季度自 82/86 年、月度自 1999 年，至 2026-08）。
2. **全量逐笔语料层**：中原地产公开成交纪录 API 的 **3 年全量快照 + 楼宇组全历史回填**（成交日 1990-01-12 ~ 2026-10-18；中原原始 **1,775,807 条**，另加 28Hse/Midland 租务补充；去重后指标管线 **1,797,431 条**），
   单元级字段（期数、座、楼层段、室号、实用/建筑面积、呎价、成交价/月租、成交日/登记日、来源），覆盖 2,082 个屋苑、2,284 个楼宇组。

## 数据颗粒度（截至 2026-10-18）

| 数据 | 粒度 | 覆盖 | 说明 |
|---|---|---|---|
| 中原逐笔成交（3 年快照 + 全历史回填） | **单元级**（座/层段/室 + 面积 + 价格 + 日期 + 成交日/登记日） | **1,775,807 条**（1990-01 ~ 2026-10；其中 3 年快照 296,708 条，覆盖 98.4%） | 公开可得的最细粒度；原始分页快照按 SHA-256 存档；**1,406/2,278 个楼宇组已有全历史页缓存**；其余 872 组保留 3 年快照 |
| 28Hse 租务补充 | 单元级租务成交 + 当前租盘 | 1,435 个已映射屋苑扫描；**22,587 条**成交（12,458 条可归属，113 个楼宇组，387 个有数据屋苑）；**229 栋**当前租盘 | 约 5.5 年历史；六个试点屋苑已到各自末页；叫租不等于成交；仅作第二来源，不并入成交中位 |
| Midland 租务补充 | 单元级 3 年租务成交 | **32,592 条 raw / 15,709 条已归属 / 506 组**（2022-06 ~ 2026-10；另 1 条 2017 异常） | 经 GPU 主机取得；本机 CloudFront 403；映射 1,869/1,972 组、1,106 个屋苑，raw 32,592 条 / 15,709 条已归属 / 506 组 |
| 中原 CCL | 周度指数 | 1994-01-02 ~ 2026-10-11，1,711 点 | 趋势校验用 |
| RVD 1.1M/1.2M/1.3M/1.4M | 区域 × A-E 类月度平均 | 1999-03 ~ 2026-08 | 官方基准，非逐套 |
| RVD 1.1A/1.2A/1.1Q/1.2Q(82/86-98) | 年/季平均 | 1982/1986 ~ 1998 | 长周期（1997 峰值）佐证 |
| LR 官方月度统计镜像 | 月度 | 2021-01 ~ 2026-09 | 与 ET Net 图表逐月互证 |
| ET Net 樓市走勢圖内嵌数据 | 月度 | 价格 2007-06 起；成交 1991-01 起 | 用户截图的原始数据源 |

已知缺口：3 年快照约 1.6%（≈4,951 条）来自分片桶边界与缺字段记录；全历史页缓存已覆盖 1,406 个楼宇组；其余 872 个楼宇组保留 3 年快照。楼盘点位 **1,972 个**，其中 1,508 个为 CentaMap 楼宇组精确坐标、464 个为屋苑中心近似坐标（弹窗内标注 `geo=estate`）。

## 运行

依赖：Python 3.10 + pandas / numpy / pyproj / shapely / requests。

```bash
# 0) 抓取/更新逐笔语料（幂等、断点续跑；--delay 控制速率）
python3 src/collect_centaline_transactions.py --workers 4

# 1) 规范化 → 2) 楼龄外校 → 3) 楼栋级指标 → 4) 外部图表对照 → 5) 构建报告与地图
python3 src/normalize_centaline_transactions.py
python3 src/verify_building_age.py      # RVD 官方《樓宇名稱》XLS → building_age_rvd.csv + 校核报告
python3 src/verify_building_age_28hse.py  # 28Hse 屋苑页（第三方）补未匹配楼栋（--from-cache 可离线重建）
python3 src/backfill_centaline_group_history.py --detail-only  # 按楼宇组回填中原全历史（day=""，断点续跑）
python3 src/normalize_centaline_full.py       # 合并 3 年快照 + 全历史回填
python3 src/collect_28hse_rent_listings.py    # 28Hse 当前租盘（叫租，第二来源）
python3 src/collect_28hse_rent_history.py --all-mapped --pages 1000  # 28Hse 租务历史（本地 403 时继续下一步）
python3 src/fetch_28hse_deep_remote.py --ssh-host gpu --workers 2 --delay 1  # 深层页经 GPU 出口（429 时保留缓存）
python3 src/collect_midland_rentals_remote.py --map-only  # Midland 全量映射（经 ssh gpu）
python3 src/collect_midland_rentals_remote.py --fetch-only  # Midland 3 年租务拉取
python3 src/build_building_metrics.py   # 第一遍：楼宇组坐标与指标
python3 src/build_school_access.py      # 教育局学校点 + iGeoCom/HDX 大学校园 → 1km 学校数
python3 src/build_building_metrics.py   # 第二遍：把 1km 学校数并入指标/地图点
python3 src/cache_building_images.py    # 画廊图片本地缓存（每组 2 张，WebP 宽度 640，断点续跑）
python3 src/finalize_image_cache.py --apply  # 按区审计并清理未引用旧图
python3 src/validate_external_charts.py
python3 src/build_artifacts.py
python3 src/build_static_figure.py

python3 -m pytest -q
python3 -m http.server 8765 --directory site   # 打开 http://127.0.0.1:8765/index.html
```

`site/index.html` 也可直接双击打开；高德底图可直接加载，OSM 瓦片建议通过本地 http 服务。
前端依赖在 `site/vendor/`（Leaflet 1.1.1 + leaflet.heat + Plotly），可离线。

## 地图 Tab（默认首页）

- 顶部 header 固定全部 Tab（**地图（第 1 个）** / 综述 / 当前租售比 / 历史走势 / 新闻对比 / 公开数据对照 / 多源规则 / 数据与验证）；地图 Tab 内 **header 之外全屏为地图**。
- 底图固定 **高德地图**，自动做 **GCJ-02 坐标纠偏**（香港偏移约 (+500m, -300m)）。
- 图层叠加（开关式面板）：**气泡点**、**短名标签**（默认关闭，开启后≥z15；悬停显示完整名称）、18 区边界（虚线轮廓）、**官方建筑轮廓**；柱状图和热力图按钮已移除。**默认开气泡点、18 区边界和官方轮廓（低缩放不加载轮廓，放大到 z≥17 自动切换）**。
- **悬停**显示轻量提示（楼盘名 + 回报率）；**点击**任意楼盘气泡即在地图右侧滑出**详情抽屉**（可拖拽调宽，× / Esc 关闭）：
  - 头部为 **年化毛回报率（左 70%）+ 楼龄（右 30%）**；楼龄先按**每栋楼**取入伙年份（**RVD 官方落成年份优先**，未匹配则用中原 `op_year`），再在楼宇组内**跨栋取中位**（避免成交量大的新楼绑架整组，如大角咀街区组 12 栋 1959–1966 老楼 + Larchwood 2024）；组内跨度 >10 年标为「混合楼龄」并显示组内年份范围。以快照 2026-10 为基准，无记录显示 —；同时写入 `data/derived/building_metrics_12m.csv` 与地图数据；
  - **年化毛回报率走势**（近 3 个月滚动中位，售/租各 ≥3 宗才发布，0 表示样本不足）；混合楼龄组不按整组配对，而是**逐栋计算滚动中位后取栋间中位**（悬停显示当月可配对栋数）；
  - **组内各栋明细**（混合楼龄组 / 街区型组）：逐栋列出 入伙年份、售/租宗数、售呎价、租呎价与该栋回报率（售/租各 ≥3 宗才计算）；**点击任意一栋**即展开该栋的统计、月度走势（回报率优先，无则售价）与最近成交/租赁明细；逐栋月度序列覆盖**全部街区型组**（804 组 / 22,988 栋），钻取体验一致；混合楼龄组的头部回报率改为**按栋中位**，原始整组配对数保留在 `yield_group_pct` 供审计；
  - **成交附图**：抽屉顶部展示 1 张大图 + 最多 5 张缩略图（来自中原成交纪录附图，实景/VR），可点击切换、**点击大图全屏查看（←/→ 翻页，Esc 关闭）**；本地缓存优先（`site/img/buildings/`，每组 2 张、宽度 640），未缓存的使用外链并在失败时自动隐藏；
  - **「售 / 租」两个 Tab**：各自的售价/租金中位与区间（金额统一港币：**HK$/呎、HK$/呎/月、整单 HK$**）、**售价/租金走势**（2023-10 ~ 2026-10）与最近成交/租赁明细（行内「详情」直接跳转中原原纪录）。
- 楼栋历史序列与明细按需加载（`site/building_details.js`，首次点击楼盘时加载；地图底数据为 `site/map_data.js`）。
- **楼盘名标注默认关闭**；打开后只显示半透明短名胶囊（≤12 字），不再把长英文街区名铺在地图上；鼠标悬停气泡/轮廓时显示短名、回报率、售租宗数，以及完整街区名。
- 左下为**单张图例卡片**（固定宽度，筛选前后不变形）：渐变色彩条（0–6%+）、**最低回报率筛选拉杆（0–6%）**、**最高楼龄筛选拉杆**（无入伙年份记录的楼栋在楼龄筛选时一并隐藏）合为一体；两个筛选同时作用于气泡点和短名标注并同步计数；「样本不足/混合组无逐栋配对楼栋（1,016 个，灰色）」复选框控制灰色点是否显示。数据窗口：当前值滚动近 12 个月；楼栋详情保留全历史，走势为全历史近 3 个月滚动中位。
- **楼龄外校**：RVD 官方《樓宇名稱》XLS（urban+nt）逐栋匹配，详见 `reports/building_age_verification.md`；当前 10,362 栋可匹配（60.0%），高置信 7,736 栋中 96% 与中原一致，1,515 个楼宇组采用 RVD 年份；RVD 未覆盖的楼栋再用 **28Hse 屋苑页**补充第三方校核（sitemap 11,019 个屋苑 slug；已抓 1,473 页、**1,488 栋命中、87.0% 与中原一致**，**457 个楼宇组**补充年份；总覆盖约 **68.6%**；详见 `reports/building_age_28hse.md`）。图片缓存：每组最多 6 张、已缓存 **10,450 张 WebP（262.1 MiB，按 18 区目录存放）**；1 个上游 URL 已确认 HTTP 404（未进入本地映射，不影响展示）。28Hse 当前租盘作为第二租务来源（叫租，不是成交），详见 `reports/rent_data_coverage.md`。
- **租/售数据来源不同**：买卖成交 = 土地注册处（全市场登记）；租务成交 = 中原自家经手 + 28Hse 22,587 条 + Midland 32,592 条 raw（15,709 条已归属）（经 GPU 主机；香港一般住宅租约不在土地注册处登记，无全市场逐单库）。核验方法与逐条对照见 `reports/rent_data_coverage.md`；抽屉「租」页内也标注该口径。
- **走势图纵轴固定**（便于不同楼盘横向对比）：年化毛回报率 0–6%；售价 0–40,000 HK$/呎；租金 0–100 HK$/呎/月。超出固定范围的序列在绘图区内截断，并标注「超出纵轴范围已截断」。
- **气泡大小 = 近 12 个月售 + 租成交宗数**（10 / 50 / 150+ 宗三档图例见左上「图层」面板；悬停提示同时显示售/租宗数）。
- 售/租 Tab 切换与抽屉宽度调整后，走势图会按实际可见宽度重绘（避免隐藏面板被压成 240px）；页面资源带构建版本号（`?v=`），重建后自动失效浏览器缓存。

本地服务：launchd 常驻 `com.mao.hk-yield-web`（keepalive）在 <http://127.0.0.1:8765/index.html> 提供页面；
停用：`launchctl bootout gui/$(id -u)/com.mao.hk-yield-web`。

## 公开数据对照（“公开数据对照” Tab）

- **成交量**：ET Net「土地註冊處成交宗數」与 LR 官方「ASP 住宅+非住宅」月度值在可比月份 **67/67 完全一致**（最大差 0）；
  我们的逐笔售类月度计数 vs LR 总量：水平相关 **0.93**、环比方向一致 80%、平均覆盖率约 92%（口径差异见报告）。
- **价格**：ET Net 指标屋苑均价 vs 我们的“二手、200-2000 呎、先屋苑中位再跨屋苑等权中位”序列：水平相关 **0.92**，3 个月变化方向一致 **87.9%**。
- **长周期（1997-2016）**：CCL 1997 峰→2003 谷回撤 **-68.5%**、RVD 年均价 -59.7%，与雪球截图（约 -72% 后回升）同形。

## 多源规则（“多源规则” Tab）

- 页面内展示匹配管线（归一化 → 精确 phase/tower → 受控模糊 → audit）、六类超大屋苑的精确规则表、来源口径、买卖重复审查和详情分页说明。
- 规则源：`config/rental_group_rules.json`；共享匹配器：`src/rental_group_matching.py`。
- 28Hse 重算：太古城 3,723/3,766 落组，匯璽 6,430/6,430 落组（II/III 已分开）；Midland raw 32,592 条 / 15,709 条已归属 / 506 组。
- 审计：`data/derived/rental_group_match_audit.csv`、`data/derived/midland_group_match_audit.csv`；验证报告：`reports/rental_group_match_validation.md`、`reports/midland_group_match_validation.md`。

## 产物

- `site/index.html`：分 Tab 报告（**地图（默认）** / 综述 / 当前租售比 / 历史走势 / 新闻对比 / 公开数据对照 / 多源规则 / 数据与验证）
- `site/building_details/<区>.js`：20 个分区文件（65 MB 总计，最大约 8.0 MB），点击楼盘时只加载该区；全历史走势 + 售/租明细；组级明细最新 60 宗/侧（每页 20 条），单栋明细最多 20 宗/侧
- `site/map_data.js`、`site/map.js`：地图数据与前端逻辑；`data/derived/building_map_points.geojson`：楼盘点位（含成交明细预览）
- `data/raw/centaline/transaction_search/2026-10-16/`：逐笔语料原始快照（3,366 页 gzip + `journal.json` + `plan.json` + `manifest.json` 含全量 SHA-256）
- `data/normalized/centaline/transactions_2026-10-16.parquet`：规范化逐笔表（含 `raw_payload_hash`/`parser_version`）
- `data/normalized/centaline/transactions_full_2026-10-18.parquet`：全历史逐笔表（买卖/租赁，成交日 + 登记日）；回填方法与覆盖见 `reports/full_history_backfill.md`
- `data/derived/28hse_rent_history.csv`、`28hse_rent_listings.csv`：28Hse 第二租务来源（22,587 条历史成交 + 229 栋当前租盘）
- `data/derived/building_metrics_12m.csv`、`estate_metrics_12m.csv`、`district_metrics_12m.csv`：楼栋/屋苑/分区 12 个月指标
- `data/derived/external_corroboration.json`：ET Net × LR × CCL × 我们数据的对照统计
- `data/raw/news/etnet_price_trend_2026-10-16.json`：ET Net 图表内嵌原始序列
- `data/raw/official/lr/lr.parquet`、`data/raw/official/rvd/`：LR 与 RVD 原始数据
- `reports/validation_report.md/.json`：验证清单（含语料覆盖、地图点位、外部对照、哈希校验）；三源租务去重见 `reports/rent_data_coverage.md`
- `site/ratio_yield_latest.png/.svg`：静态图；第一阶段 1,000 条样本的产物（`centaline_building_points.*` 等）保留存档

## 口径与限制

- “租售比”一律指**年化毛租金回报率**；税费、管理费、空置等未扣除，不得称为净回报。
- 逐笔层为中原地產**成交纪录**（含 AC 与土地注册处来源），与 LR 全量口径不完全相同；比分片桶边界导致约 1.6% 缺口。
- 楼栋回报率 = 该楼宇组近 12 个月售价呎价中位与租金呎价中位配对；两侧样本各 ≥3 才发布，低于 3 显示“样本不足”。
- 屋苑中心近似坐标已在弹窗中点名；不用于任何空间统计。

## 建筑轮廓（Phase 1，官方 CSDI）
- 数据源：**地政总署 CSDI Building**（342,195 个有效建筑面，月度更新）；本机 `*.gov.hk` TLS 直连仍不可达，**595/595 组已改经 `ssh japan` 直连官方 FeatureServer 重抓**，不再依赖 DeepSeek Fetch 代理。OSM（Overpass，ODbL）保留为交叉校验/补充原型。
- 对象：当前「有逐栋明细 + 至少一个 RVD 高置信楼龄锚点 + 街区/混合组」交集 **595 组**；匹配优先级：官方面名/屋苑名 → 点位落入住宅 Tower 面内 → 最近 Tower ≤30m。
- 结果：**551/595 组**有官方轮廓；其中 name **434** / pip **30** / near **87** / none **44**。name+pip 为高置信 **464 组（78.0%）**；near 以**橙色虚线低置信候选**显示；官方 none 中有 **13 组**用 OSM 灰色虚线补充，其余 31 组保留气泡。
- 100 组金标准抽检：官方 name/pip 自动判据通过 **82/92**；OSM 原型与官方可比较样本中通过 **65/89（73.0%）**，因此 Phase 1 以官方 CSDI 为主源。详见 `reports/footprint_gold_standard.md`、`reports/footprint_gold_sample.csv`、`site/footprint_review.html`。
- 交互：图层开关「官方建筑轮廓（Phase 1）」；zoom≥14 显示；开启后已匹配组不再画气泡，未匹配组保留气泡；填充色=回报率，橙色虚线=near 低置信，点击轮廓打开详情。
- 脚本：`python3 src/fetch_csdi_footprints.py --ssh-host japan`（官方 FeatureServer，经 SSH 隧道，断点续跑）→ `python3 src/build_official_footprints.py`（生成 `site/footprints.js` 与金标准抽检）；OSM 对照：`python3 src/fetch_osm_footprints.py` → `python3 src/build_footprints.py`。
