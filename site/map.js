/* Hong Kong rent-to-price map (Leaflet) — v2 redesign (2026-10-17)
 * - fixed 高德 basemap with GCJ-02 correction (WGS84 → GCJ-02 for overlays)
 * - layers: yield bars (canvas), heat, bubbles, estate labels, district outlines
 * - interaction: hover = lightweight tip; click = right-side detail drawer
 * - drawer: yield trend + 售/租 tabs (stats, 36-month rolling trend, deal lists)
 *   details load lazily per district from building_details/<district>.js
 * - all money values are formatted explicitly as HK$ (呎价/呎租/整单)
 */
(function () {
  const AMAP_VEC = 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}';

  // ---------- GCJ-02 (WGS84 → GCJ-02) ----------
  const PI = 3.1415926535897932384626, AA = 6378245.0, EE = 0.00669342162296594323;
  function outOfChina(lng, lat) { return !(lng > 73.66 && lng < 135.05 && lat > 3.86 && lat < 53.55); }
  function tLat(x, y) {
    let r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
    r += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
    r += (20 * Math.sin(y * PI) + 40 * Math.sin(y / 3 * PI)) * 2 / 3;
    r += (160 * Math.sin(y / 12 * PI) + 320 * Math.sin(y * PI / 30)) * 2 / 3;
    return r;
  }
  function tLng(x, y) {
    let r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
    r += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
    r += (20 * Math.sin(x * PI) + 40 * Math.sin(x / 3 * PI)) * 2 / 3;
    r += (150 * Math.sin(x / 12 * PI) + 300 * Math.sin(x / 30 * PI)) * 2 / 3;
    return r;
  }
  function wgs2gcj(lng, lat) {
    if (outOfChina(lng, lat)) return [lat, lng];
    let dLat = tLat(lng - 105, lat - 35), dLng = tLng(lng - 105, lat - 35);
    const rad = lat / 180 * PI;
    let m = Math.sin(rad); m = 1 - EE * m * m;
    const sm = Math.sqrt(m);
    dLat = (dLat * 180) / ((AA * (1 - EE)) / (m * sm) * PI);
    dLng = (dLng * 180) / (AA / sm * Math.cos(rad) * PI);
    return [lat + dLat, lng + dLng];
  }
  function disp(lon, lat) { return wgs2gcj(lon, lat); }

  // ---------- formatting (explicit HK$ units everywhere) ----------
  const NF = new Intl.NumberFormat('en-HK');
  const hkd = (v) => v == null ? '—' : 'HK$' + NF.format(Math.round(v));
  const hkdPsf = (v) => v == null ? '—' : 'HK$' + NF.format(Math.round(v)) + '/呎';
  const rentPsf = (v) => v == null ? '—' : 'HK$' + Number(v).toFixed(1) + '/呎/月';
  const rentHkd = (v) => v == null ? '—' : 'HK$' + NF.format(Math.round(v)) + '/月';
  const psfRange = (a, b) => (a == null || b == null) ? '—'
    : 'HK$' + NF.format(Math.round(a)) + ' – HK$' + NF.format(Math.round(b)) + '/呎';
  const rentPsfRange = (a, b) => (a == null || b == null) ? '—'
    : 'HK$' + Number(a).toFixed(1) + ' – HK$' + Number(b).toFixed(1) + '/呎/月';
  const intFmt = (v) => v == null ? '—' : NF.format(Math.floor(v));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const CENTA = 'https://hk.centanet.com/findproperty/transaction-detail/';

  // Keep map labels short: full English street-cluster names are useful in
  // the drawer, but unreadable when repeated over every footprint.
  function cleanLabel(s) {
    let v = String(s == null ? '' : s).trim();
    v = v.replace(/^\([^)]*\)\s*/, '').replace(/\s*\([^)]*\)\s*$/, '');
    v = v.replace(/\s*(住宅物業|別墅洋房|私人住宅|屋苑)\s*$/, '');
    v = v.replace(/\s*(戰前[-－至]?\d{0,4}年代|\d{2,4}年代)\s*$/, '');
    v = v.split(' / ')[0].split('/')[0];
    return v.replace(/\s+/g, ' ').trim();
  }
  function shortLabel(b) {
    let v = cleanLabel(b && b.name);
    if (!/[\u4e00-\u9fff]/.test(v) || v.length > 18) v = cleanLabel(b && b.bldg);
    if (!v) v = cleanLabel(b && (b.name || b.bldg)) || '未命名楼盘';
    return v.length > 12 ? v.slice(0, 12) + '…' : v;
  }

  // ---------- map ----------
  const data = window.HK_MAP_DATA || { buildings: [], estates: [], districts: null, window: '' };
  const map = L.map('map', { zoomControl: false, attributionControl: true, preferCanvas: true })
    .setView([22.34, 114.16], 11);
  L.control.zoom({ position: 'topright' }).addTo(map);
  window.__hkMap = map;
  L.tileLayer(AMAP_VEC, { subdomains: '1234', maxZoom: 18, attribution: '© 高德地图' }).addTo(map);

  // ---------- yield colour scale ----------
  const YIELD_STOPS = [[0, '#eaf3fb'], [1, '#c6dbef'], [2, '#6baed6'], [3, '#4292c6'], [4, '#2171b5'], [5, '#08519c'], [6, '#08306b']];
  function yieldColor(y) {
    if (y == null || isNaN(y)) return '#aab2bd';
    if (y >= 6) return '#08306b';
    if (y >= 5) return '#08519c';
    if (y >= 4) return '#2171b5';
    if (y >= 3) return '#4292c6';
    if (y >= 2) return '#6baed6';
    if (y >= 1) return '#c6dbef';
    return '#eaf3fb';
  }

  // ---------- canvas yield bars ----------
  const Bars = L.Layer.extend({
    onAdd(m) {
      this._canvas = L.DomUtil.create('canvas', 'leaflet-zoom-animated');
      this._canvas.style.pointerEvents = 'none';
      m.getPanes().overlayPane.appendChild(this._canvas);
      m.on('moveend zoomend viewreset resize', this._reset, this);
      this._reset();
    },
    onRemove(m) {
      m.off('moveend zoomend viewreset resize', this._reset, this);
      L.DomUtil.remove(this._canvas);
    },
    _reset() {
      const m = map, size = m.getSize();
      const tl = m.containerPointToLayerPoint([0, 0]);
      const c = this._canvas; c.width = size.x; c.height = size.y;
      L.DomUtil.setPosition(c, tl);
      const ctx = c.getContext('2d');
      ctx.clearRect(0, 0, c.width, c.height);
      const z = m.getZoom();
      const zoomK = Math.pow(1.55, Math.max(0, z - 11)) * 0.35;
      const widthK = Math.min(1, Math.max(0.4, (z - 10) / 4));
      for (const b of data.buildings) {
        if (b.yield == null || b.yield <= minYield || !passesAge(b) || !passesSchools(b)) continue;
        const p = m.latLngToContainerPoint(disp(b.lon, b.lat));
        if (p.x < -40 || p.y < -220 || p.x > size.x + 40 || p.y > size.y + 40) continue;
        const h = Math.min(140, (8 + b.yield * 22) * zoomK);
        const w = Math.max(2.5, Math.max(3, Math.min(12, 3 + Math.log2(1 + (b.sale_n + b.rent_n)) * 1.4)) * widthK);
        const col = yieldColor(b.yield);
        ctx.fillStyle = col;
        ctx.globalAlpha = 0.88;
        ctx.fillRect(p.x - w / 2, p.y - h, w, h);
        ctx.globalAlpha = 1;
        ctx.fillStyle = '#ffffff';
        ctx.globalAlpha = 0.55;
        ctx.fillRect(p.x - w / 2, p.y - h, w, Math.min(3, h));
        ctx.globalAlpha = 1;
        ctx.fillStyle = '#1f2a37';
        ctx.beginPath(); ctx.arc(p.x, p.y, 2, 0, 6.29); ctx.fill();
      }
    },
  });

  // ---------- state ----------
  const overlays = { bars: false, heat: false, bubbles: true, labels: false, districts: true, footprints: true, estateFallback: true, schools: true };
  let minYield = 3.0;                   // 默认只看 >3% 的回报率
  let yieldRaf = null, ageRaf = null, schoolRaf = null;
  let maxAge = 30;                      // 默认只看 ≤30 年
  let showNoYield = false;              // 默认不显示无回报率灰点
  let minSchools = 0;                   // 1 公里内学校数下限（0 = 不限）
  let heatLayer = null, barsLayer = null, bubbleLayer = null, labelLayer = null, districtLayer = null;
  let footLayer = null, footData = null, footPromise = null;
  let schoolLayer = null, schoolData = null, schoolPromise = null;
  const FOOTPRINT_MIN_ZOOM = 17, SCHOOL_MIN_ZOOM = 11;

  function bubbleRadius(b) {
    return Math.max(5, Math.min(20, 4 + Math.sqrt((b.sale_n || 0) + (b.rent_n || 0)) * 1.35));
  }

  // The source API has no bldgGrpId for some new estates.  Present those
  // estate-level rows as a clearly labelled fallback point instead of dropping
  // their rents/sales from the map entirely.
  function estateAsBuilding(e) {
    return Object.assign({}, e, {
      id: 'estate:' + e.name,
      bldg: e.bldg || (e.name + ' · 屋苑级'),
      estate: e.name,
      geo: 'estate',
      yield_basis: 'estate',
      layer: 'estate',
    });
  }

  // ---------- hover tip (lightweight; details live in the drawer) ----------
  let hoverTip = null;
  function tipEl() {
    if (!hoverTip) {
      hoverTip = document.createElement('div');
      hoverTip.className = 'hk-tip';
      map.getContainer().appendChild(hoverTip);
    }
    return hoverTip;
  }
  function showTip(e, b, tag) {
    const t = tipEl();
    const name = shortLabel(b);
    const detail = (b.bldg && b.bldg !== name) ? b.bldg : (b.name && b.name !== name ? b.name : '');
    const stats = (b.yield == null ? '无回报率' : b.yield.toFixed(2) + '%')
      + ' · 售' + (b.sale_n || 0) + '/租' + (b.rent_n || 0) + ' 宗'
      + ' · 学校 ' + (b.schools_1km || 0) + ' 所' + (tag ? ' · ' + tag : '');
    t.innerHTML = '<b>' + esc(name) + '</b><span>' + esc(stats) + '</span>'
      + (detail ? '<small>' + esc(detail) + '</small>' : '')
      + '<small>1km 学校：幼 ' + (b.schools_kindergarten || 0) + ' / 小 ' + (b.schools_primary || 0)
      + ' / 中 ' + (b.schools_secondary || 0) + ' / 大专 ' + ((b.schools_university || 0) + (b.schools_college || 0)) + '</small>';
    moveTip(e);
    t.style.display = 'block';
  }
  function moveTip(e) {
    const p = map.latLngToContainerPoint(e.latlng);
    const t = tipEl();
    const size = map.getSize();
    const w = t.offsetWidth || 260, h = t.offsetHeight || 54;
    let x = p.x + 12;
    let y = p.y - h - 10;
    if (x + w > size.x - 8) x = p.x - w - 12;
    if (x < 8) x = 8;
    if (y < 8) y = p.y + 14;
    if (y + h > size.y - 8) y = size.y - h - 8;
    t.style.left = x + 'px';
    t.style.top = y + 'px';
  }
  function hideTip() { if (hoverTip) hoverTip.style.display = 'none'; }

  // ---------- drawer ----------
  let drawer = null, drScroll = null, current = null, state = null;
  let dealPages = { sale: 0, rent: 0 };
  const detailPromises = {};
  const encodeDistrict = (s) => encodeURIComponent(String(s || '未知')).replace(/[!'()*]/g,
    (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());

  function loadDetails(b) {
    const dist = b.district || '未知';
    const cache = window.HK_DETAILS_BY_DISTRICT = window.HK_DETAILS_BY_DISTRICT || {};
    if (cache[dist]) return Promise.resolve(cache[dist]);
    if (!detailPromises[dist]) {
      detailPromises[dist] = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'building_details/' + encodeDistrict(dist) + '.js' + (data.build ? '?v=' + data.build : '');
        s.onload = () => resolve(cache[dist] || { months: [], buildings: {} });
        s.onerror = () => reject(new Error('building_details/' + dist + ' 加载失败'));
        document.head.appendChild(s);
      });
    }
    return detailPromises[dist];
  }

  function median(nums) {
    const a = nums.filter((v) => v != null && isFinite(v)).sort((x, y) => x - y);
    if (!a.length) return null;
    const m = Math.floor(a.length / 2);
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }

  function drawerHtml(b) {
    const title = shortLabel(b);
    const fullName = b.bldg || b.name || '';
    const sub = fullName && fullName !== title ? fullName : '';
    const chips = [
      b.district, b.hma,
      b.quality === 'building'
        ? (b.yield_basis === 'building' ? '按栋配对'
           : b.yield_basis === 'none' ? '无单栋配对'
           : b.yield_basis === 'estate' ? '屋苑级配对'
           : '楼宇组内配对')
        : '低样本',
      b.age_mixed ? '混合楼龄' : '',
      b.layer === 'estate' ? '屋苑级（源数据无楼宇组 ID）' : '',
      b.geo === 'synthetic' ? '合成组（源数据无楼宇组 ID）' : '',
      b.geo === 'estate' ? '坐标为屋苑中心近似' : '',
      b.geo === 'synthetic' ? '合成组坐标按屋苑中心近似' : '',
    ].filter(Boolean).map((t) => '<span class="dr-chip">' + esc(t) + '</span>').join('');
    const y = b.yield;
    const ageMixed = !!(b.age_mixed && b.op_year_min != null && b.op_year_max != null);
    const ageSub = ageMixed
      ? '<div class="n" title="组内含多栋不同楼龄建筑；楼龄按栋取中位">组内 ' + String(b.op_year_min) + '–' + String(b.op_year_max) + '</div>'
      : (b.op_year != null ? '<div class="n">入伙 ' + esc(String(b.op_year)) + '</div>' : '');
    const ageCell = '<div class="dr-yield-age"' + (ageMixed
      ? ' title="该楼宇组含多栋不同楼龄建筑：楼龄按栋取中位，组内入伙年份 ' + String(b.op_year_min) + '–' + String(b.op_year_max) + '"'
      : '') + '><div class="k">楼龄</div>' + (b.age_years == null
      ? '<div class="v">—</div><div class="n">无入伙记录</div>'
      : '<div class="v">约 ' + intFmt(b.age_years) + ' 年</div>' + ageSub) + '</div>';
    const basisNote = b.geo === 'synthetic'
      ? '月租 × 12 ÷ 售价 · 近 12 个月 · 合成组内配对（源数据无楼宇组 ID）'
      : b.yield_basis === 'building'
      ? '月租 × 12 ÷ 售价 · 近 12 个月 · 按栋中位（' + (b.yield_bldg_n || 0) + ' 栋可配对）'
      : b.yield_basis === 'none'
        ? '混合楼龄组：组内无单栋同时满足售/租 ≥1 宗'
        : b.yield_basis === 'estate'
          ? '月租 × 12 ÷ 售价 · 近 12 个月 · 屋苑级配对（源数据无楼宇组 ID）'
          : '月租 × 12 ÷ 售价 · 近 12 个月 · 楼宇组内配对';
    const yHero = '<div class="dr-yield"><div class="dr-yield-main">' + (y == null
      ? '<div class="dr-yield-val" style="font-size:22px">样本不足</div><div class="dr-yield-lab">年化毛回报率（近 12 个月）<small>' + basisNote + '</small></div>'
      : '<div class="dr-yield-val">' + y.toFixed(2) + '%</div><div class="dr-yield-lab">年化毛回报率<small>' + basisNote + '</small></div>') + '</div>' + ageCell + '</div>';
    const saleStats =
      '<div class="dr-stat"><div class="k">售价中位（呎价）</div><div class="v">' + hkdPsf(b.sale_psf) + '</div><div class="n">' + (b.sale_n || 0) + ' 宗成交 · 近 12 个月</div></div>' +
      '<div class="dr-stat"><div class="k">售价区间 P25–P75</div><div class="v small2">' + psfRange(b.sale_psf_p25, b.sale_psf_p75) + '</div></div>' +
      '<div class="dr-stat"><div class="k">售价中位（整单）</div><div class="v">' + hkd(b.sale_price_median) + '</div></div>' +
      '<div class="dr-stat"><div class="k">面积中位</div><div class="v">' + (b.area_median == null ? '—' : intFmt(b.area_median) + ' 呎') + '</div></div>';
    const rentStats =
      '<div class="dr-stat"><div class="k">租金中位（呎租）</div><div class="v">' + rentPsf(b.rent_psf) + '</div><div class="n">' + (b.rent_n || 0) + ' 宗租赁 · 近 12 个月</div></div>' +
      '<div class="dr-stat"><div class="k">租金区间 P25–P75</div><div class="v small2">' + rentPsfRange(b.rent_psf_p25, b.rent_psf_p75) + '</div></div>' +
      '<div class="dr-stat"><div class="k">月租中位（整单）</div><div class="v">' + rentHkd(b.rent_monthly_median) + '</div></div>' +
      '<div class="dr-stat"><div class="k">近期租盘面积中位</div><div class="v" id="drRentArea">—</div><div class="n">依最近租赁明细</div></div>';
    const geoNote = b.geo === 'estate' ? ' · 点位为屋苑中心近似' : b.geo === 'synthetic' ? ' · 合成组点位为屋苑中心近似' : '';
    return '' +
      '<div class="dr-head"><div>' +
      '<div class="dr-title">' + esc(title) + '</div>' +
      (sub ? '<div class="dr-sub">' + esc(sub) + '</div>' : '') +
      '<div class="dr-chips">' + chips + '</div></div>' +
      '<button class="dr-close" aria-label="关闭详情面板" title="关闭">×</button></div>' +
      '<div class="dr-gallery" id="drGallery"></div>' +
      yHero +
      '<div class="dr-mini" style="border-color:#bfdbfe;background:#eff6ff">1 公里内学校：<b>' + (b.schools_1km || 0) + '</b> 所 · 幼稚园 ' + (b.schools_kindergarten || 0) + ' · 小学 ' + (b.schools_primary || 0) + ' · 中学 ' + (b.schools_secondary || 0) + ' · 大学/大专 ' + ((b.schools_university || 0) + (b.schools_college || 0)) + '<br><span style="color:#64748b">学校点 / 大学校园轮廓距楼盘 ≤1km（含边界触碰）</span></div>' +
      '<section class="dr-block"><div class="dr-block-title">年化毛回报率走势 <span class="dr-block-note" id="drRule">近 3 个月滚动中位</span></div>' +
      '<div class="dr-chart" id="drChartYield"><div class="chart-empty">加载明细中…</div></div>' +
      '<div id="drBld"></div></section>' +
      '<div class="dr-tabs" role="tablist">' +
      '<button class="dr-tab active" data-side="sale">售 · 近 12 个月 ' + (b.sale_n || 0) + ' 宗</button>' +
      '<button class="dr-tab" data-side="rent" id="drRentTab">租 · 近 12 个月 ' + (b.rent_n || 0) + ' 宗</button></div>' +
      '<section class="dr-side active" data-side-panel="sale">' +
      '<div class="dr-stats">' + saleStats + '</div>' +
      '<div class="dr-block"><div class="dr-block-title">售价走势 <span class="dr-block-note" id="drSaleAxisNote">近 3 个月滚动中位 · 纵轴固定 HK$0–40,000/呎</span></div>' +
      '<div class="dr-chart" id="drChartSale"><div class="chart-empty">加载明细中…</div></div></div>' +
      '<div class="deals-title">成交明细 <small>最新记录 · 每页 20 条</small></div>' +
      '<div id="drDealsSale"><div class="deals-empty">加载明细中…</div></div></section>' +
      '<section class="dr-side" data-side-panel="rent">' +
      '<div class="dr-stats">' + rentStats + '</div>' +
      '<div class="dr-mini">租赁成交来自中原自家经手（及后续补充的公开来源）；香港住宅租约不注册于土地注册处，因此不存在全市场逐单租金成交库。买卖成交为土地注册处全量登记。两侧宗数不可直接对比覆盖率。</div>' +
      '<div id="drRentSources"></div>' +
      '<div class="dr-block"><div class="dr-block-title">租金走势 <span class="dr-block-note">近 3 个月滚动中位 · 纵轴固定 HK$0–100/呎/月</span></div>' +
      '<div class="dr-chart" id="drChartRent"><div class="chart-empty">加载明细中…</div></div></div>' +
      '<div class="deals-title">租赁明细 <small>最新记录 · 每页 20 条</small></div>' +
      '<div id="drDealsRent"><div class="deals-empty">加载明细中…</div></div></section>' +
      '<div class="dr-foot">来源：买卖 = 中原公开纪录 / 土地注册处登记；租赁 = 中原公开纪录 + 公开平台租盘补充（租盘为叫租，不等同成交）· ' + esc(data.window || '') + ' · 走势为全历史近 3 个月滚动中位（售/租各 ≥1 宗才发布）· 楼龄按栋取入伙年份中位（混合楼龄组取跨栋中位）·「详情」跳转原纪录' + geoNote + '</div>';
  }

  function ensureDrawer() {
    if (drawer) return;
    drawer = document.createElement('aside');
    drawer.id = 'hk-drawer';
    drawer.setAttribute('aria-hidden', 'true');
    drawer.innerHTML = '<div class="drawer-grip" title="拖动调整宽度"></div><div class="drawer-scroll" id="drScroll"></div>';
    document.body.appendChild(drawer);
    drScroll = drawer.querySelector('.drawer-scroll');
    initGrip();
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
  }

  function setSide(side) {
    if (!drawer) return;
    drawer.querySelectorAll('.dr-tab').forEach((t) => t.classList.toggle('active', t.dataset.side === side));
    drawer.querySelectorAll('.dr-side').forEach((p) => p.classList.toggle('active', p.dataset.sidePanel === side));
    if (state) renderDetail();   // 面板从 display:none 变为可见后按实际宽度重绘图表
  }

  function openDrawer(b) {
    ensureDrawer();
    current = b;
    state = null;
    dealPages = { sale: 0, rent: 0 };
    drScroll.innerHTML = drawerHtml(b);
    drScroll.scrollTop = 0;
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    drScroll.querySelector('.dr-close').addEventListener('click', closeDrawer);
    drScroll.querySelectorAll('.dr-tab').forEach((t) => t.addEventListener('click', () => setSide(t.dataset.side)));
    setSide('sale');
    loadDetails(b).then((d) => {
      if (current !== b) return;
      const rec = (d.buildings || {})[b.id] || (d.estates || {})[b.name] || null;
      state = { b, rec, months: d.months || [], details: d };
      renderDetail();
      if (rec && !(rec.sale || []).length && (rec.rent || []).length) setSide('rent');
    }).catch(() => {
      ['drChartYield', 'drChartSale', 'drChartRent'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '<div class="chart-empty">明细数据加载失败（building_details.js）</div>';
      });
    });
  }

  function closeDrawer() {
    if (!drawer) return;
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    current = null;
    state = null;
  }

  // ---------- trend charts (hand-rolled SVG, crisp + light) ----------
  // 固定纵轴（跨楼盘统一可比）：yield 0–6%、售价 0–40,000 HK$/呎、租金 0–100 HK$/呎/月
  const AXIS = {
    yield: { min: 0, max: 6, ticks: [0, 2, 4, 6] },
    sale: { min: 0, max: 40000, ticks: [0, 10000, 20000, 30000, 40000] },
    rent: { min: 0, max: 100, ticks: [0, 25, 50, 75, 100] },
  };
  // Keep a fixed axis per tier rather than auto-scaling each building.  The
  // 40k tier covers ordinary housing; luxury/independent-house groups expand
  // to the next fixed tier so their lines are not clipped away.
  const SALE_TIERS = [40000, 60000, 80000, 100000, 150000];
  function saleAxisFor(vals) {
    let mx = 0;
    for (const v of vals || []) if (v != null && isFinite(v) && v > mx) mx = v;
    const max = SALE_TIERS.find((t) => mx <= t) || SALE_TIERS[SALE_TIERS.length - 1];
    return { min: 0, max: max, ticks: [0, max / 4, max / 2, max * 0.75, max] };
  }

  function drawTrend(host, months, vals, opt) {
    host.innerHTML = '';
    // Trim the common 1990..2026 monthly skeleton to the first/last real
    // observation for this series, so recent listings do not sit in a huge
    // blank leading area.
    let first = vals.findIndex((v) => v != null && isFinite(v));
    if (first < 0) {
      host.innerHTML = '<div class="chart-empty">' + (opt.empty || '样本不足，暂无可绘制的月度序列') + '</div>';
      return;
    }
    let last = vals.length - 1;
    while (last > first && (vals[last] == null || !isFinite(vals[last]))) last--;
    const orig = [];
    for (let i = first; i <= last; i++) orig.push(i);
    months = months.slice(first, last + 1);
    vals = vals.slice(first, last + 1);
    const pts = [];
    vals.forEach((v, i) => { if (v != null && isFinite(v)) pts.push({ i, v }); });
    if (pts.length < 2) {
      host.innerHTML = '<div class="chart-empty">' + (opt.empty || '样本不足，暂无可绘制的月度序列') + '</div>';
      return;
    }
    const W = Math.max(240, host.clientWidth - 6), H = opt.h || 150;
    const PL = 48, PR = 12, PT = 12, PB = 22;
    let vmin, vmax, tickVals;
    if (opt.axis) {
      vmin = opt.axis.min; vmax = opt.axis.max; tickVals = opt.axis.ticks;
    } else {
      vmin = Math.min.apply(null, pts.map((p) => p.v));
      vmax = Math.max.apply(null, pts.map((p) => p.v));
      const span = Math.max(vmax - vmin, Math.abs(vmax) * 0.05, 1e-9);
      vmin -= span * 0.15; vmax += span * 0.15;
      tickVals = [vmax, (vmin + vmax) / 2, vmin];
    }
    const X = (i) => PL + (W - PL - PR) * (months.length <= 1 ? 0.5 : i / (months.length - 1));
    const Y = (v) => PT + (H - PT - PB) * (1 - (v - vmin) / (vmax - vmin));
    const segs = []; let cur = [];
    vals.forEach((v, i) => {
      if (v == null || !isFinite(v)) { if (cur.length) segs.push(cur); cur = []; }
      else cur.push([X(i), Y(v)]);
    });
    if (cur.length) segs.push(cur);
    const line = segs.map((s) => 'M' + s.map((p) => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('L')).join('');
    const area = segs.filter((s) => s.length > 1).map((s) =>
      'M' + s.map((p) => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('L') +
      'L' + s[s.length - 1][0].toFixed(1) + ' ' + (H - PB) + 'L' + s[0][0].toFixed(1) + ' ' + (H - PB) + 'Z').join('');
    const fmt = opt.fmt || ((v) => String(Math.round(v)));
    const yTicks = tickVals.map((v) =>
      '<text x="' + (PL - 6) + '" y="' + (Y(v) + 4).toFixed(1) + '" text-anchor="end">' + fmt(v) + '</text>').join('');
    const idx = [0, Math.floor((months.length - 1) / 2), months.length - 1].filter((v, i, a) => a.indexOf(v) === i);
    const xTicks = idx.map((i) =>
      '<text x="' + X(i).toFixed(1) + '" y="' + (H - 6) + '" text-anchor="' + (i === 0 ? 'start' : (i === months.length - 1 ? 'end' : 'middle')) + '">' + months[i] + '</text>').join('');
    const dots = pts.map((p) =>
      '<circle cx="' + X(p.i).toFixed(1) + '" cy="' + Y(p.v).toFixed(1) + '" r="2.6" fill="#fff" stroke="' + opt.color + '" stroke-width="1.6" data-i="' + p.i + '" data-oi="' + orig[p.i] + '"/>').join('');
    const clipId = 'clip_' + opt.gid;
    const outside = pts.some((p) => p.v < vmin || p.v > vmax);
    host.innerHTML = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '" role="img">' +
      '<defs><clipPath id="' + clipId + '"><rect x="' + PL + '" y="' + PT + '" width="' + (W - PL - PR) + '" height="' + (H - PT - PB) + '"/></clipPath>' +
      '<linearGradient id="g_' + opt.gid + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="' + opt.color + '" stop-opacity=".20"/>' +
      '<stop offset="1" stop-color="' + opt.color + '" stop-opacity="0"/></linearGradient></defs>' +
      '<g clip-path="url(#' + clipId + ')">' +
      '<path d="' + area + '" fill="url(#g_' + opt.gid + ')"/>' +
      '<path d="' + line + '" fill="none" stroke="' + opt.color + '" stroke-width="2" stroke-linejoin="round"/>' +
      dots + '</g>' + yTicks + xTicks +
      (outside ? '<text class="clip-note" x="' + (W - PR) + '" y="' + (PT + 9) + '" text-anchor="end">超出纵轴范围已截断</text>' : '') +
      '</svg><div class="chart-tip"></div>';
    const tip = host.querySelector('.chart-tip');
    host.querySelectorAll('circle').forEach((c) => {
      c.addEventListener('mouseenter', () => {
        const i = +c.dataset.i;
        const oi = +c.dataset.oi;
        tip.textContent = opt.tip ? opt.tip(oi, vals[i], months[i]) : (months[i] + ' · ' + fmt(vals[i]));
        const ch = host.querySelector('svg').getBoundingClientRect();
        const cr = c.getBoundingClientRect();
        tip.style.left = (cr.left + cr.width / 2 - ch.left) + 'px';
        tip.style.top = (cr.top - ch.top) + 'px';
        tip.style.display = 'block';
      });
      c.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
    });
  }

  function dealsTable(list, side, page) {
    if (!list || !list.length) {
      return '<div class="deals-empty">暂无' + (side === 'sale' ? '成交' : '租赁') + '明细</div>';
    }
    const pageSize = 20;
    const pages = Math.max(1, Math.ceil(list.length / pageSize));
    page = Math.max(0, Math.min(pages - 1, page | 0));
    const start = page * pageSize;
    const visible = list.slice(start, start + pageSize);
    const sourceLabel = (src) => src === 'MIDLAND' ? '美联' : src === '28HSE' ? '28Hse' : src === 'AC' ? '中原' : src === 'Land' ? '土登' : '其他';
    const counts = {};
    list.forEach((d) => { counts[d.src || 'AC'] = (counts[d.src || 'AC'] || 0) + 1; });
    const sourceSummary = Object.keys(counts).sort().map((k) => sourceLabel(k) + ' ' + counts[k]).join(' · ');
    const landTitle = '土地注册处登记，由中原 API 同步；不是独立的中原 AC 经手成交';
    const rows = visible.map((d, j) => {
      const href = d.url ? (d.url.indexOf('http') === 0 ? d.url : CENTA + d.url) : null;
      const price = side === 'sale' ? hkd(d.p) : rentHkd(d.p);
      const psfCell = side === 'sale' ? hkdPsf(d.psf) : rentPsf(d.psf);
      const srcTxt = sourceLabel(d.src);
      return '<tr>' +
        '<td class="date">' + esc(d.d || '—') + '<span class="src-inline"' + (d.src === 'Land' ? ' title="' + esc(landTitle) + '"' : '') + '>' + esc(srcTxt) + '</span></td>' +
        '<td class="unit">' + (esc([d.b, d.f, d.u].filter(Boolean).join(' ')) || '—') + '</td>' +
        '<td class="r">' + price + '</td>' +
        '<td class="r">' + (d.a == null ? '—' : intFmt(d.a) + '呎') + '</td>' +
        '<td class="r">' + psfCell + '</td>' +
        '<td class="link"><button type="button" class="dr-link" data-record-i="' + (start + j) + '">详情›</button></td></tr>';
    }).join('');
    const emptyAc = side === 'sale' && !counts.AC;
    const sourceBar = '<div class="deals-sourcebar"><span>来源：' + esc(sourceSummary) + '</span>' +
      (side === 'sale' ? '<small>土登 = 土地注册处登记（中原 API 同步）' + (emptyAc ? '；本组未检出中原 AC 成交' : '') + '</small>' : '<small>中原 / 美联 / 28Hse 均为市场成交申报</small>') + '</div>';
    const pager = pages > 1 ? '<div class="deals-pager"><button type="button" data-deal-page="' + (page - 1) + '"' + (page === 0 ? ' disabled' : '') + '>‹ 上一页</button><span>第 ' + (page + 1) + ' / ' + pages + ' 页 · 共 ' + list.length + ' 条</span><button type="button" data-deal-page="' + (page + 1) + '"' + (page === pages - 1 ? ' disabled' : '') + '>下一页 ›</button></div>' : '';
    return sourceBar + '<table class="deals"><thead><tr>' +
      '<th>日期</th><th>座 / 层 / 室</th><th style="text-align:right">' + (side === 'sale' ? '售价' : '月租') + '</th>' +
      '<th style="text-align:right">面积</th><th style="text-align:right">' + (side === 'sale' ? '呎价' : '呎租') + '</th><th></th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>' + pager;
  }

  function renderDealPanel(side) {
    const host = document.getElementById(side === 'sale' ? 'drDealsSale' : 'drDealsRent');
    if (!host) return;
    const list = state && state.rec ? (state.rec[side] || []) : [];
    host.innerHTML = dealsTable(list, side, dealPages[side] || 0);
    host.querySelectorAll('[data-deal-page]').forEach((b) => b.addEventListener('click', () => {
      dealPages[side] = +b.dataset.dealPage;
      renderDealPanel(side);
    }));
    host.querySelectorAll('[data-record-i]').forEach((btn) => btn.addEventListener('click', () => {
      const d = list[+btn.dataset.recordI];
      if (d) openRecordModal(d, side);
    }));
  }

  // The upstream Centaline detail page can return 500 when its own API is
  // rate-limited (429).  Keep the verified local snapshot usable: the "详情"
  // button opens this local record card, with the external page as optional.
  function openRecordModal(d, side) {
    if (!d) return;
    const href = d.url ? (d.url.indexOf('http') === 0 ? d.url : CENTA + d.url) : null;
    const srcLabel = d.src === 'MIDLAND' ? '美联' : d.src === '28HSE' ? '28Hse' : d.src === 'AC' ? '中原' : d.src === 'Land' ? '土登' : (d.src || '其他');
    const price = side === 'sale' ? hkd(d.p) : rentHkd(d.p);
    const psf = side === 'sale' ? hkdPsf(d.psf) : rentPsf(d.psf);
    let modal = document.getElementById('recordModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'recordModal';
      modal.className = 'record-modal';
      modal.innerHTML = '<div class="rm-card" role="dialog" aria-modal="true" aria-label="记录详情"><button class="rm-close" aria-label="关闭">×</button><div class="rm-body"></div></div>';
      document.body.appendChild(modal);
      modal.addEventListener('click', (e) => { if (e.target === modal) closeRecordModal(); });
      modal.querySelector('.rm-close').addEventListener('click', closeRecordModal);
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeRecordModal(); });
    }
    const rows = [
      ['日期', d.d || '—'],
      ['成交日', d.dd || '—'],
      ['来源', srcLabel],
      ['座 / 层 / 室', [d.b, d.f, d.u].filter(Boolean).join(' ') || '—'],
      [side === 'sale' ? '售价' : '月租', price],
      ['面积', d.a == null ? '—' : intFmt(d.a) + ' 呎'],
      [side === 'sale' ? '呎价' : '呎租', psf],
      ['记录 ID', d.id || (d.url ? String(d.url).split('_').pop() : '—')],
    ];
    const body = modal.querySelector('.rm-body');
    body.innerHTML = '<div class="rm-kicker">' + (side === 'sale' ? '成交记录' : '租赁记录') + '</div>' +
      '<h3>本地快照详情</h3>' +
      '<div class="rm-grid">' + rows.map(([k, v]) => '<div><span>' + esc(k) + '</span><b>' + esc(String(v)) + '</b></div>').join('') + '</div>' +
      '<div class="rm-note">中原原页当前可能因上游 API 429 返回 500；以上字段来自本项目已保存的本地快照，不依赖外部页面。</div>' +
      (href ? '<div class="rm-actions"><a href="' + esc(href) + '" target="_blank" rel="noopener" title="中原原页可能因上游 429 返回 500；本地快照已完整保留">打开中原原页（可能 500）↗</a><button type="button" class="rm-copy">复制链接</button></div>' : '');
    modal.classList.add('open');
    document.body.classList.add('rm-open');
    const copy = body.querySelector('.rm-copy');
    if (copy) copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(href); copy.textContent = '已复制'; }
      catch (e) { copy.textContent = '复制失败'; }
    });
  }

  function closeRecordModal() {
    const m = document.getElementById('recordModal');
    if (m) m.classList.remove('open');
    document.body.classList.remove('rm-open');
  }

  const imgSrc = (x) => (x && (x.l || x.u)) || '';
  const imgLabel = (t) => t === 'VirtualTour' ? 'VR 睇樓' : t === 'StreetView' ? '街景' : '实景';

  function openLightbox(list, start, b) {
    let lb = document.getElementById('galLightbox');
    if (!lb) {
      lb = document.createElement('div');
      lb.id = 'galLightbox';
      lb.className = 'gal-lb';
      lb.innerHTML = '<button class="lb-close" aria-label="关闭">×</button>' +
        '<button class="lb-prev" aria-label="上一张">‹</button><button class="lb-next" aria-label="下一张">›</button>' +
        '<img id="lbImg" referrerpolicy="no-referrer" alt=""><div class="lb-cap" id="lbCap"></div>';
      document.body.appendChild(lb);
      lb.addEventListener('click', (e) => { if (e.target === lb) closeLightbox(); });
      lb.querySelector('.lb-close').addEventListener('click', closeLightbox);
      lb.querySelector('.lb-prev').addEventListener('click', () => lbStep(-1));
      lb.querySelector('.lb-next').addEventListener('click', () => lbStep(1));
      lb.querySelector('img').addEventListener('error', () => {
        const img = lb.querySelector('img');
        img.alt = '图片加载失败';
        img.removeAttribute('src');
      });
    }
    lb._list = list;
    lb._i = start | 0;
    lb._place = (b && (b.district || b.hma)) || '';
    document.body.classList.add('lb-open');
    lb.classList.add('open');
    lbShow();
  }

  function lbShow() {
    const lb = document.getElementById('galLightbox');
    if (!lb || !lb._list) return;
    const n = lb._list.length;
    lb._i = (lb._i + n) % n;
    const x = lb._list[lb._i];
    const img = lb.querySelector('img');
    img.alt = '';
    img.src = imgSrc(x);
    lb.querySelector('#lbCap').textContent = (lb._i + 1) + ' / ' + n + ' · ' + imgLabel(x.t) + (lb._place ? ' · ' + lb._place : '');
    const showArrows = n > 1;
    lb.querySelector('.lb-prev').style.display = showArrows ? '' : 'none';
    lb.querySelector('.lb-next').style.display = showArrows ? '' : 'none';
    [1, -1].forEach((d) => {
      const y = lb._list[(lb._i + d + n) % n];
      if (n > 1 && y) { const pre = new Image(); pre.src = imgSrc(y); }
    });
  }

  function lbStep(d) {
    const lb = document.getElementById('galLightbox');
    if (!lb || !lb._list) return;
    lb._i += d;
    lbShow();
  }

  function closeLightbox() {
    const lb = document.getElementById('galLightbox');
    if (!lb) return;
    lb.classList.remove('open');
    lb._list = null;
    document.body.classList.remove('lb-open');
    const img = lb.querySelector('img');
    if (img) img.removeAttribute('src');
  }

  document.addEventListener('keydown', (e) => {
    const lb = document.getElementById('galLightbox');
    if (!lb || !lb.classList.contains('open')) return;
    if (e.key === 'Escape') { closeLightbox(); e.stopPropagation(); }
    else if (e.key === 'ArrowLeft') { lbStep(-1); e.preventDefault(); }
    else if (e.key === 'ArrowRight') { lbStep(1); e.preventDefault(); }
  });

  function renderGallery(host, imgs, b) {
    const list = (imgs || []).filter((x) => x && x.u);
    if (!list.length) { host.innerHTML = ''; return; }
    let idx = 0;
    host.innerHTML = '<div class="gal-main" title="点击全屏查看"><img id="galMain" referrerpolicy="no-referrer" loading="lazy" alt=""><span class="gal-badge" id="galBadge"></span><span class="gal-cap">中原成交附图 · ' + esc(b.district || b.hma || '') + '</span></div>' +
      (list.length > 1 ? '<div class="gal-thumbs">' + list.map((x, i) =>
        '<button class="gal-th" type="button" data-i="' + i + '"><img referrerpolicy="no-referrer" loading="lazy" src="' + esc(imgSrc(x)) + '" alt=""></button>').join('') + '</div>' : '') +
      '<div class="gal-src">图片：中原地产成交纪录附图（版权归中原／原作者）· ' + (list[0].l ? '已缓存 ' + list.filter((x) => x.l).length + ' 张' : '在线加载') + '</div>';
    const main = host.querySelector('#galMain');
    const badge = host.querySelector('#galBadge');
    const setIdx = (i) => {
      idx = i;
      main.src = imgSrc(list[i]);
      badge.textContent = imgLabel(list[i].t);
      host.querySelectorAll('.gal-th').forEach((t, j) => t.setAttribute('aria-current', j === i ? 'true' : 'false'));
    };
    main.addEventListener('error', () => {
      if (idx + 1 < list.length) setIdx(idx + 1);
      else host.innerHTML = '';
    });
    host.querySelectorAll('.gal-th').forEach((t) => t.addEventListener('click', () => setIdx(+t.dataset.i)));
    host.querySelector('.gal-main').addEventListener('click', () => openLightbox(list, idx, b));
    setIdx(0);
  }

  function showBldFocus(rec, i, host) {
    const r = (rec.bld || [])[i];
    if (!r) return;
    host.querySelectorAll('tr.brow').forEach((tr) => tr.classList.toggle('sel', +tr.dataset.i === i));
    let focus = host.querySelector('.bld-focus');
    if (!focus) {
      focus = document.createElement('div');
      focus.className = 'bld-focus';
      host.querySelector('details').appendChild(focus);
    }
    const dealsS = (r.dl && r.dl.sale) || [];
    const dealsR = (r.dl && r.dl.rent) || [];
    const srcLabel = (d) => d.src === 'MIDLAND' ? '美联' : d.src === 'Land' ? '土地注册处' : d.src === 'AC' ? '中原' : d.src === '28HSE' ? '28Hse' : '中原';
    const dealRow = (d, side) => '<tr><td>' + (side === 'sale' ? '<span class="dr-badge sale">售</span>' : '<span class="dr-badge rent">租</span>') + '</td>' +
      '<td class="date">' + esc(d.d || '—') + '<span class="src-inline">' + esc(srcLabel(d)) + '</span></td><td class="unit">' + esc([d.b, d.f, d.u].filter(Boolean).join(' ')) + '</td>' +
      '<td class="r">' + (side === 'sale' ? hkd(d.p) : rentHkd(d.p)) + '</td>' +
      '<td class="r">' + (d.a == null ? '—' : intFmt(d.a) + '呎') + '</td>' +
      '<td class="r">' + (side === 'sale' ? hkdPsf(d.psf) : rentPsf(d.psf)) + '</td></tr>';
    focus.innerHTML = '<div class="bf-head"><button class="bf-back" type="button">‹ 返回组概览</button>' +
      '<div><div class="bf-title">' + esc(r.n || '—') + '</div><div class="bf-sub">' + esc(r.a || '') + '</div></div></div>' +
      '<div class="bf-stats">' +
        '<span>入伙 <b>' + (r.y == null ? '—' : r.y) + '</b></span>' +
        '<span>售/租 <b>' + (r.s || 0) + '/' + (r.r || 0) + '</b></span>' +
        '<span>售 <b>' + (r.sp == null ? '—' : 'HK$' + NF.format(r.sp)) + '</b>/呎</span>' +
        '<span>租 <b>' + (r.rp == null ? '—' : 'HK$' + Number(r.rp).toFixed(1)) + '</b>/呎/月</span>' +
        '<span>回报率 <b>' + (r.yld == null ? '—' : r.yld.toFixed(2) + '%') + '</b></span>' +
      '</div>' +
      '<div class="bf-chart-title" id="bfChartTitle">该栋走势（全历史）</div>' +
      '<div class="dr-chart" id="bfChart"><div class="chart-empty">加载中…</div></div>' +
      (dealsS.length || dealsR.length
        ? '<table class="deals bf-deals"><thead><tr><th></th><th>日期</th><th>座 / 层 / 室</th><th style="text-align:right">价格</th><th style="text-align:right">面积</th><th style="text-align:right">呎价</th></tr></thead><tbody>' +
          dealsS.map((d) => dealRow(d, 'sale')).join('') + dealsR.map((d) => dealRow(d, 'rent')).join('') + '</tbody></table>'
        : '<div class="deals-empty">该栋暂无可用成交明细</div>');
    focus.querySelector('.bf-back').addEventListener('click', () => {
      focus.remove();
      host.querySelectorAll('tr.brow').forEach((tr) => tr.classList.remove('sel'));
    });
    const chartEl = focus.querySelector('#bfChart');
    const titleEl = focus.querySelector('#bfChartTitle');
    const months = (state && state.months) || [];
    const h = r.h;
    let hs = h && h.s ? h.s : null;
    let hr = h && h.r ? h.r : null;
    if (h && h.m) {
      hs = new Array(months.length).fill(0);
      hr = new Array(months.length).fill(0);
      h.m.forEach((mi, k) => { hs[mi] = h.s[k] || 0; hr[mi] = h.r[k] || 0; });
    }
    let drawn = false;
    if (h && months.length && hs && hr) {
      const yvals = months.map((_, j) => (hs[j] > 0 && hr[j] > 0) ? hr[j] * 12 / hs[j] * 100 : null);
      if (yvals.filter((v) => v != null).length >= 2) {
        titleEl.textContent = '该栋年化毛回报率走势（全历史 · 近 3 个月滚动中位）';
        drawTrend(chartEl, months, yvals, {
          color: '#0f4c81', gid: 'bfy', axis: AXIS.yield, fmt: (v) => v.toFixed(1) + '%',
          tip: (j, v, m) => m + ' · ' + v.toFixed(2) + '%',
        });
        drawn = true;
      } else {
        const svals = months.map((_, j) => hs[j] > 0 ? hs[j] : null);
        if (svals.filter((v) => v != null).length >= 2) {
          titleEl.textContent = '该栋售价走势（全历史 · 近 3 个月滚动中位 · HK$/呎）';
          drawTrend(chartEl, months, svals, {
            color: '#c2410c', gid: 'bfs', axis: AXIS.sale, fmt: (v) => 'HK$' + NF.format(Math.round(v)),
            tip: (j, v, m) => m + ' · ' + hkdPsf(v),
          });
          drawn = true;
        }
      }
    }
    if (!drawn) {
      titleEl.textContent = '该栋走势';
      chartEl.innerHTML = '<div class="chart-empty">该栋暂无可发布的月度序列（售/租各 ≥1 宗才发布）</div>';
    }
    focus.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function renderDetail() {
    if (!state) return;
    const { b, rec, months } = state;
    const n = months.length;
    const ruleEl = document.getElementById('drRule');
    if (ruleEl && state.details && state.details.rule) {
      ruleEl.textContent = state.details.rule
        + (b.age_mixed ? ' · 按栋中位（每月可配对栋数见悬停）' : '')
        + ' · 纵轴固定 0–6%';
    }
    if (!rec) {
      ['drChartYield', 'drChartSale', 'drChartRent'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '<div class="chart-empty">暂无该楼宇组的明细序列</div>';
      });
      const bldEl0 = document.getElementById('drBld');
      if (bldEl0) bldEl0.innerHTML = '';
      const galEl0 = document.getElementById('drGallery');
      if (galEl0) galEl0.innerHTML = '';
      const sEl = document.getElementById('drDealsSale');
      const rEl = document.getElementById('drDealsRent');
      if (sEl) sEl.innerHTML = dealsTable(null, 'sale', 0);
      if (rEl) rEl.innerHTML = dealsTable(null, 'rent', 0);
      return;
    }
    const h = rec.h || null;
    const empty = (k) => new Array(n).fill(null);
    const sVals = h ? h.s.map((v) => v > 0 ? v : null) : empty();
    const rVals = h ? h.r.map((v) => v > 0 ? v : null) : empty();
    const yVals = months.map((_, i) => (sVals[i] != null && rVals[i] != null) ? rVals[i] * 12 / sVals[i] * 100 : null);
    const yEl = document.getElementById('drChartYield');
    const hb = rec.hb;
    if (yEl && b.age_mixed) {
      if (hb && hb.y && hb.y.filter((v) => v != null).length >= 2) {
        drawTrend(yEl, months, hb.y, {
          color: '#0f4c81', gid: 'yield', axis: AXIS.yield, fmt: (v) => v.toFixed(1) + '%',
          tip: (i, v, m) => m + ' · ' + v.toFixed(2) + '% · ' + ((hb.n && hb.n[i]) || 0) + ' 栋可配对',
          empty: '混合楼龄组：暂无按栋中位走势',
        });
      } else {
        yEl.innerHTML = '<div class="chart-empty">混合楼龄组：暂无按栋中位走势（各栋售/租样本不足）</div>';
      }
    } else if (yEl) drawTrend(yEl, months, yVals, {
      color: '#0f4c81', gid: 'yield', axis: AXIS.yield, fmt: (v) => v.toFixed(1) + '%',
      tip: (i, v, m) => m + ' · ' + v.toFixed(2) + '% · 售 ' + (h ? h.sn[i] : 0) + ' / 租 ' + (h ? h.rn[i] : 0) + ' 宗(滚动)',
      empty: '售/租任一侧样本不足，暂无回报率走势',
    });
    const sEl2 = document.getElementById('drChartSale');
    const sAxis = saleAxisFor(sVals);
    const sNote = document.getElementById('drSaleAxisNote');
    if (sNote) sNote.textContent = '近 3 个月滚动中位 · 纵轴固定 HK$0–' + NF.format(Math.round(sAxis.max)) + '/呎';
    if (sEl2) drawTrend(sEl2, months, sVals, {
      color: '#c2410c', gid: 'sale', axis: sAxis, fmt: (v) => 'HK$' + NF.format(Math.round(v)),
      tip: (i, v, m) => m + ' · ' + hkdPsf(v) + ' · ' + (h ? h.sn[i] : 0) + ' 宗(滚动)',
      empty: '该楼宇组全历史暂无足够售价样本',
    });
    const rEl2 = document.getElementById('drChartRent');
    if (rEl2) drawTrend(rEl2, months, rVals, {
      color: '#0f766e', gid: 'rent', axis: AXIS.rent, fmt: (v) => 'HK$' + Number(v).toFixed(1),
      tip: (i, v, m) => m + ' · ' + rentPsf(v) + ' · ' + (h ? h.rn[i] : 0) + ' 宗(滚动)',
      empty: '该楼宇组全历史暂无足够租金样本',
    });
    renderDealPanel('sale');
    renderDealPanel('rent');
    const rentTab = document.getElementById('drRentTab');
    if (rentTab && rec.rent28) rentTab.textContent = '租 · 成交 ' + (b.rent_n || 0) + ' 宗 / 租盘 ' + rec.rent28.n + ' 个';
    const rentSources = document.getElementById('drRentSources');
    if (rentSources) {
      const r28 = rec.rent28;
      const r28h = rec.rent28h;
      let html = '';
      if (r28) html += '<div class="dr-mini" style="border-color:#99f6e4;background:#f0fdfa">28Hse 当前租盘：<b>' + intFmt(r28.n) + '</b> 个 · 叫租中位 <b>' + hkd(r28.rent) + '</b>' + (r28.psf == null ? '' : ' · 呎租中位 <b>HK$' + Number(r28.psf).toFixed(1) + '</b>/呎/月') + ' · <a href="' + esc(r28.url) + '" target="_blank" rel="noopener">查看租盘</a><br>28Hse 为挂牌叫租，不是成交；不并入成交租金中位。</div>';
      if (r28h) html += '<div class="dr-mini" style="border-color:#fde68a;background:#fffbeb">28Hse 屋苑租务成交（试点）：<b>' + intFmt(r28h.n) + '</b> 宗 · 中位月租 <b>' + hkd(r28h.rent) + '</b>' + (r28h.psf == null ? '' : ' · 呎租中位 <b>HK$' + Number(r28h.psf).toFixed(1) + '</b>/呎/月') + ' · ' + esc(r28h.start) + ' ~ ' + esc(r28h.end) + '<br>屋苑级试点，仅作第二来源校验；不并入本栋成交中位。</div>';
      rentSources.innerHTML = html;
    }
    const galEl = document.getElementById('drGallery');
    if (galEl) renderGallery(galEl, rec.img, b);
    const bldEl = document.getElementById('drBld');
    if (bldEl) {
      const wasOpen = !!(bldEl.querySelector('details') || {}).open;
      if (rec.bld && rec.bld.length) {
        bldEl.innerHTML = '<details class="dr-bld"' + (wasOpen ? ' open' : '') + '>' +
          '<summary>组内各栋明细（' + rec.bld.length + ' 栋）</summary>' +
          '<table class="deals bld"><thead><tr><th>栋</th><th>入伙</th>' +
          '<th style="text-align:right">售/租</th><th style="text-align:right">售呎价</th>' +
          '<th style="text-align:right">租呎价</th><th style="text-align:right">回报率</th></tr></thead><tbody>' +
          rec.bld.map((r, i) => '<tr class="brow" data-i="' + i + '">' +
            '<td class="bname" title="' + esc(r.a || '') + '">' + esc(r.n || '—') + '</td>' +
            '<td>' + (r.y == null ? '—' : r.y) + '</td>' +
            '<td class="r">' + (r.s || 0) + '/' + (r.r || 0) + '</td>' +
            '<td class="r">' + (r.sp == null ? '—' : NF.format(r.sp)) + '</td>' +
            '<td class="r">' + (r.rp == null ? '—' : Number(r.rp).toFixed(1)) + '</td>' +
            '<td class="r">' + (r.yld == null ? '—' : r.yld.toFixed(2) + '%') + '</td></tr>').join('') +
          '</tbody></table>' +
          '<div class="dr-mini">按栋配对：该栋售 / 租各 ≥1 宗才计算回报率；「—」= 样本不足或单侧无成交。呎价单位 HK$/呎（租为 HK$/呎/月）。点击任意一行查看该栋成交与走势。</div></details>';
        bldEl.querySelectorAll('tr.brow').forEach((tr) => {
          tr.addEventListener('click', () => showBldFocus(rec, +tr.dataset.i, bldEl));
        });
      } else if (b.layer === 'estate') {
        bldEl.innerHTML = '<div class="dr-mini">该屋苑在源数据中没有楼宇组 ID，暂按屋苑级聚合展示；下方租/售明细仍为原始逐笔记录。</div>';
      } else {
        bldEl.innerHTML = '';
      }
    }
    const areaEl = document.getElementById('drRentArea');
    if (areaEl) {
      const m = median((rec.rent || []).map((d) => d.a));
      areaEl.textContent = m == null ? '—' : NF.format(Math.round(m)) + ' 呎';
    }
    const footEl = document.getElementById('drFoot');
    if (footEl && state.details && state.details.window) {
      const w = state.details.window;
      if (w.start) footEl.innerHTML += '<br>走势窗口：' + esc(w.start) + ' ~ ' + esc(w.end);
    }
  }

  function renderCharts() {
    if (state) renderDetail();
  }

  // ---------- drawer resize grip ----------
  function initGrip() {
    const grip = drawer.querySelector('.drawer-grip');
    let dragging = false;
    grip.addEventListener('pointerdown', (e) => {
      dragging = true;
      try { grip.setPointerCapture(e.pointerId); } catch (err) { /* noop */ }
      document.body.style.userSelect = 'none';
    });
    grip.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      const maxW = Math.min(880, window.innerWidth * 0.86);
      const w = Math.min(Math.max(window.innerWidth - e.clientX, 340), maxW);
      drawer.style.width = w + 'px';
    });
    const stop = () => {
      if (!dragging) return;
      dragging = false;
      document.body.style.userSelect = '';
      renderCharts();
    };
    grip.addEventListener('pointerup', stop);
    grip.addEventListener('pointercancel', stop);
  }

  let resizeTimer = null;
  window.addEventListener('resize', () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => { if (current) renderCharts(); }, 160);
  });

  // ---------- yield filter (minimum gross yield) ----------
  function applyYieldFilter(v) {
    minYield = Math.max(0, Math.min(6, isFinite(v) ? v : 0));
    const out = document.getElementById('lgFilterVal');
    if (out) out.textContent = minYield <= 0 ? '全部显示' : '> ' + minYield.toFixed(1) + '%';
    const input = document.getElementById('lgFilter');
    if (input && parseFloat(input.value) !== minYield) input.value = String(minYield);
    updateFilterCount();
    rebuildAll();
  }
  function updateFilterCount() {
    const el = document.getElementById('lgCount');
    if (!el) return;
    let total = 0, shown = 0, et = 0, es = 0;
    for (const b of data.buildings) {
      if (b.yield == null) continue;
      total++;
      if (b.yield > minYield && passesAge(b) && passesSchools(b)) shown++;
    }
    if (overlays.estateFallback) {
      for (const e of data.estates || []) {
        if (!e.fallback || e.yield == null) continue;
        et++;
        if (e.yield > minYield && passesAge(e) && passesSchools(e)) es++;
      }
    }
    el.textContent = '匹配 ' + shown + ' / ' + total + ' 栋有回报率' +
      (et ? ' · 屋苑级 ' + es + ' / ' + et : '') +
      (minSchools > 0 ? ' · 学校 ≥' + minSchools + ' 所' : '');
  }

  function passesAge(b) { return maxAge == null || (b.age_years != null && b.age_years <= maxAge); }
  function passesSchools(b) { return minSchools <= 0 || ((b.schools_1km || 0) >= minSchools); }

  function applyAgeFilter(v) {
    const n = Math.round(isFinite(v) ? v : 70);
    maxAge = n >= 70 ? null : n;
    const out = document.getElementById('lgAgeVal');
    if (out) out.textContent = maxAge == null ? '不限' : '≤ ' + maxAge + ' 年';
    const input = document.getElementById('lgAge');
    if (input && Math.round(parseFloat(input.value)) !== n) input.value = String(n);
    updateFilterCount();
    rebuildAll();
  }

  function applySchoolFilter(v) {
    minSchools = Math.max(0, Math.min(120, Math.round(isFinite(v) ? v : 0)));
    const out = document.getElementById('lgSchoolVal');
    if (out) out.textContent = minSchools <= 0 ? '不限' : '≥ ' + minSchools + ' 所';
    const input = document.getElementById('lgSchool');
    if (input && Math.round(parseFloat(input.value)) !== minSchools) input.value = String(minSchools);
    updateFilterCount();
    rebuildAll();
  }

  function setShowNoYield(v) {
    showNoYield = !!v;
    const cb = document.getElementById('lgNoYield');
    if (cb) cb.checked = showNoYield;
    rebuildAll();
  }

  // ---------- layers ----------
  function loadSchools() {
    if (window.HK_SCHOOLS) { schoolData = window.HK_SCHOOLS; return Promise.resolve(schoolData); }
    if (!schoolPromise) {
      schoolPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'schools.js' + (data.build ? '?v=' + data.build : '');
        s.onload = () => { schoolData = window.HK_SCHOOLS || { points: [], campuses: [] }; resolve(schoolData); };
        s.onerror = () => reject(new Error('schools.js 加载失败'));
        document.head.appendChild(s);
      });
    }
    return schoolPromise;
  }

  function drawSchoolLayer() {
    if (schoolLayer) { map.removeLayer(schoolLayer); schoolLayer = null; }
    if (!schoolData) return;
    const group = L.layerGroup();
    const catName = { kindergarten: '幼稚园', primary: '小学', secondary: '中学', special: '特殊学校', tertiary: '大专/大学' };
    for (const p of schoolData.points || []) {
      const ll = wgs2gcj(p.lon, p.lat);
      const mk = L.circleMarker(ll, {
        radius: 2.2, color: '#1d4ed8', weight: 0.5, fillColor: '#2563eb',
        fillOpacity: 0.8, className: 'hk-school-dot',
      });
      const html = '<b>' + esc(p.n) + '</b><br>' + esc(catName[p.c] || p.c || '学校') + (p.a ? '<br>' + esc(p.a) : '');
      mk.bindTooltip(html, { direction: 'top', sticky: true, className: 'hk-school-tip' });
      mk.on('click', () => mk.bindPopup(html).openPopup());
      group.addLayer(mk);
    }
    if (schoolData.campuses && schoolData.campuses.length) {
      const gj = JSON.parse(JSON.stringify({ type: 'FeatureCollection', features: schoolData.campuses }));
      for (const f of gj.features) {
        const conv = (ring) => ring.map((c) => { const ll = wgs2gcj(c[0], c[1]); return [ll[1], ll[0]]; });
        if (f.geometry.type === 'Polygon') f.geometry.coordinates = f.geometry.coordinates.map(conv);
        else if (f.geometry.type === 'MultiPolygon') f.geometry.coordinates = f.geometry.coordinates.map((p) => p.map(conv));
      }
      L.geoJSON(gj, {
        style: { color: '#1d4ed8', weight: 1.2, fillColor: '#60a5fa', fillOpacity: 0.08 },
        onEachFeature: (f, layer) => {
          const name = (f.properties && (f.properties.name || f.properties.id)) || '大学校园';
          layer.bindTooltip('<b>' + esc(name) + '</b><br>大学校园', { sticky: true, className: 'hk-school-tip' });
          layer.on('click', () => layer.bindPopup('<b>' + esc(name) + '</b><br>大学校园').openPopup());
        },
      }).addTo(group);
    }
    schoolLayer = group.addTo(map);
  }

  function loadFootprints() {
    if (window.HK_FOOTPRINTS) { footData = window.HK_FOOTPRINTS; return Promise.resolve(footData); }
    if (!footPromise) {
      footPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'footprints.js' + (data.build ? '?v=' + data.build : '');
        s.onload = () => resolve(window.HK_FOOTPRINTS || { features: [] });
        s.onerror = () => reject(new Error('footprints.js 加载失败'));
        document.head.appendChild(s);
      });
    }
    return footPromise;
  }
  function ensureFootprints() {
    if (!overlays.footprints || map.getZoom() < FOOTPRINT_MIN_ZOOM || footData) return;
    loadFootprints().then((fd) => { footData = fd; rebuildAll(); }).catch(() => {
      overlays.footprints = false;
      const cb = document.querySelector('input[data-layer="footprints"]');
      if (cb) cb.checked = false;
      rebuildAll();
    });
  }

  function rebuildAll() {
    if (map.getSize().x === 0 || map.getSize().y === 0) return;
    if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
    if (barsLayer) { map.removeLayer(barsLayer); barsLayer = null; }
    if (bubbleLayer) { map.removeLayer(bubbleLayer); bubbleLayer = null; }
    if (schoolLayer) { map.removeLayer(schoolLayer); schoolLayer = null; }

    if (overlays.heat) {
      const pts = [];
      for (const b of data.buildings) {
        if (b.yield == null || b.yield <= minYield || !passesAge(b) || !passesSchools(b)) continue;
        const ll = disp(b.lon, b.lat);
        pts.push([ll[0], ll[1], Math.max(0.2, Math.min(1.6, b.yield / 3))]);
      }
      try {
        heatLayer = L.heatLayer(pts, { radius: 22, blur: 18, maxZoom: 15,
          gradient: { 0.2: '#c6dbef', 0.45: '#6baed6', 0.7: '#2171b5', 1: '#08306b' } }).addTo(map);
      } catch (e) { console.warn('heat layer skipped:', e); heatLayer = null; }
    }

    if (overlays.bars) {
      barsLayer = new Bars(); barsLayer.addTo(map);
    }

    if (overlays.bubbles) {
      const group = L.layerGroup();
      const footIds = (overlays.footprints && footData && map.getZoom() >= FOOTPRINT_MIN_ZOOM)
        ? new Set((footData.features || []).filter((f) => f.properties.match !== 'near' && f.properties.match !== 'osm').map((f) => f.properties.id)) : null;
      for (const b of data.buildings) {
        if (b.yield == null) { if (!showNoYield || !passesAge(b) || !passesSchools(b)) continue; }
        else if (b.yield <= minYield || !passesAge(b) || !passesSchools(b)) continue;
        if (footIds && footIds.has(b.id)) continue;   // matched group is drawn as outline
        const ll = disp(b.lon, b.lat);
        const mk = L.circleMarker(ll, {
          radius: bubbleRadius(b), color: '#26364a', weight: 1,
          fillColor: yieldColor(b.yield), fillOpacity: 0.92, className: 'hk-dot',
        });
        mk.on('mouseover', (e) => { mk.setStyle({ weight: 2.5, color: '#0f172a' }); mk.bringToFront(); showTip(e, b, b.geo === 'synthetic' ? '合成组' : ''); });
        mk.on('mousemove', (e) => moveTip(e));
        mk.on('mouseout', () => { mk.setStyle({ weight: 1, color: '#26364a' }); hideTip(); });
        mk.on('click', () => openDrawer(b));
        group.addLayer(mk);
      }
      for (const e of data.estates || []) {
        if (!overlays.estateFallback || !e.fallback) continue;
        if (e.yield == null) { if (!showNoYield || !passesAge(e) || !passesSchools(e)) continue; }
        else if (e.yield <= minYield || !passesAge(e) || !passesSchools(e)) continue;
        const ll = disp(e.lon, e.lat);
        const total = (e.sale_n || 0) + (e.rent_n || 0);
        const sz = Math.max(8, Math.min(15, 8 + Math.sqrt(total) * 0.4));
        const html = '<span class="hk-estate-diamond" style="width:' + sz.toFixed(0) + 'px;height:' + sz.toFixed(0) + 'px;background:' + yieldColor(e.yield) + '"></span>';
        const mk = L.marker(ll, {
          icon: L.divIcon({ className: 'hk-estate-icon', html: html, iconSize: [sz, sz], iconAnchor: [sz / 2, sz / 2] }),
          keyboard: false,
          title: e.name + ' · 屋苑级',
        });
        const eb = estateAsBuilding(e);
        mk.on('mouseover', (ev) => showTip(ev, eb, '屋苑级'));
        mk.on('mousemove', (ev) => moveTip(ev));
        mk.on('mouseout', () => hideTip());
        mk.on('click', () => openDrawer(eb));
        group.addLayer(mk);
      }
      bubbleLayer = group.addTo(map);
    }
    // school points / university campuses (blue)
    if (overlays.schools && map.getZoom() >= SCHOOL_MIN_ZOOM) {
      if (schoolData) drawSchoolLayer();
      else loadSchools().then((sd) => {
        schoolData = sd;
        if (overlays.schools && map.getZoom() >= SCHOOL_MIN_ZOOM) drawSchoolLayer();
      }).catch(() => {});
    }
    // official CSDI building footprints (Phase 1; WGS84 → GCJ-02)
    if (footLayer) { map.removeLayer(footLayer); footLayer = null; }
    if (overlays.footprints) ensureFootprints();
    if (overlays.footprints && footData && map.getZoom() >= FOOTPRINT_MIN_ZOOM) {
      const byId = {};
      for (const b of data.buildings) byId[b.id] = b;
      const gj = JSON.parse(JSON.stringify(footData));
      for (const f of gj.features) {
        const conv = (ring) => ring.map((c) => { const ll = wgs2gcj(c[0], c[1]); return [ll[1], ll[0]]; });
        if (f.geometry.type === 'MultiPolygon') f.geometry.coordinates = f.geometry.coordinates.map((p) => p.map(conv));
        else if (f.geometry.type === 'Polygon') f.geometry.coordinates = f.geometry.coordinates.map(conv);
      }
      footLayer = L.geoJSON(gj, {
        filter: (f) => {
          const b = byId[f.properties.id];
          if (!b) return false;
          if (b.yield == null) return showNoYield && passesAge(b) && passesSchools(b);
          return b.yield > minYield && passesAge(b) && passesSchools(b);
        },
        style: (f) => {
          const b = byId[f.properties.id] || {};
          const near = f.properties.match === 'near';
          const osm = f.properties.match === 'osm';
          const low = near || osm;
          return { color: osm ? '#64748b' : near ? '#b45309' : '#0b4f9e', weight: low ? 1.2 : 1.6, opacity: low ? 0.85 : 0.95,
                   dashArray: low ? '4 3' : null,
                   fillColor: yieldColor(b.yield), fillOpacity: b.yield == null ? 0.42 : (low ? 0.25 : 0.55) };
        },
        onEachFeature: (f, layer) => {
          const b = byId[f.properties.id];
          if (!b) return;
          const near = f.properties.match === 'near';
          const osm = f.properties.match === 'osm';
          const low = near || osm;
          layer.on('mouseover', (e) => { layer.setStyle({ weight: low ? 2.2 : 2.8 }); showTip(e, b, osm ? 'OSM 补充轮廓' : near ? '低置信候选' : ''); });
          layer.on('mousemove', (e) => moveTip(e));
          layer.on('mouseout', () => { layer.setStyle({ weight: low ? 1.2 : 1.6 }); hideTip(); });
          layer.on('click', () => openDrawer(b));
        },
      }).addTo(map);
    }
    refreshLabels();
  }

  function refreshLabels() {
    if (labelLayer) { map.removeLayer(labelLayer); labelLayer = null; }
    if (!overlays.labels || map.getZoom() < 15) return;
    const group = L.layerGroup();
    for (const b of data.buildings) {
      if (b.yield == null || b.yield <= minYield || !passesAge(b) || !passesSchools(b)) continue;
      const ll = disp(b.lon, b.lat);
      const icon = L.divIcon({ className: 'map-label-wrap', html: '<span class="map-label">' + esc(shortLabel(b)) + '</span>', iconSize: [0, 0], iconAnchor: [0, 0] });
      group.addLayer(L.marker(ll, { icon, interactive: false, keyboard: false }));
    }
    labelLayer = group.addTo(map);
  }
  map.on('zoomend', () => { if (overlays.labels) refreshLabels(); if (overlays.footprints) { ensureFootprints(); rebuildAll(); } });

  function buildDistrictLayer() {
    if (districtLayer) { map.removeLayer(districtLayer); districtLayer = null; }
    if (!overlays.districts || !data.districts) return;
    const gj = JSON.parse(JSON.stringify(data.districts));
    for (const f of gj.features) {
      const conv = (ring) => ring.map((c) => { const ll = wgs2gcj(c[0], c[1]); return [ll[1], ll[0]]; });
      if (f.geometry.type === 'Polygon') f.geometry.coordinates = f.geometry.coordinates.map(conv);
      else if (f.geometry.type === 'MultiPolygon') f.geometry.coordinates = f.geometry.coordinates.map((p) => p.map(conv));
    }
    districtLayer = L.geoJSON(gj, {
      interactive: false,
      style: { color: '#4b5563', weight: 1.2, dashArray: '5 5', fill: false, opacity: 0.85 },
    }).addTo(map);
    districtLayer.bringToBack();
  }

  // ---------- controls ----------
  (function makeControls() {
    const ctrl = document.createElement('div');
    ctrl.className = 'map-fx hk-controls';
    ctrl.innerHTML = '<div class="mp-title">图层</div>' +
      [['bubbles', '气泡点（低缩放 / 未匹配）'], ['estateFallback', '屋苑级回退点（紫色菱形）'], ['schools', '学校（蓝点，z≥11）'], ['labels', '短名标注（悬停看全名）'], ['districts', '18 区边界'], ['footprints', '官方建筑轮廓（z≥17 自动）']]
        .map(([k, label]) => '<label class="mp-switch"><input type="checkbox" data-layer="' + k + '"' + (overlays[k] ? ' checked' : '') + '><span class="mp-track"><span class="mp-thumb"></span></span><span class="mp-txt">' + label + '</span></label>').join('') +
      '<div class="mp-bubblekey"><div class="bk-title">气泡大小 = 近 12 个月售 + 租成交宗数</div><div class="bk-row">' +
        [10, 50, 150].map((n) => {
          const d = Math.round(bubbleRadius({ sale_n: n, rent_n: 0 }) * 2);
          return '<span><i style="width:' + d + 'px;height:' + d + 'px"></i>' + n + (n === 150 ? '+' : '') + '</span>';
        }).join('') + '</div></div>' +
      '<div class="mp-note">数据窗口 ' + esc(data.window || '') + '<br>轮廓开启时：name/pip 组用官方轮廓；near 低置信组保留气泡 + 橙色虚线；未匹配组保留气泡<br>紫色菱形 = 源数据无楼宇组 ID 的屋苑级回退点<br>蓝色小点 = 学校/大学（z≥11；大学校园同时显示轮廓）<br>轮廓来源：© 地政總署 / DATA.GOV.HK · CSDI Building</div>';
    L.DomEvent.disableClickPropagation(ctrl);
    L.DomEvent.disableScrollPropagation(ctrl);
    map.getContainer().appendChild(ctrl);

    const greyN = data.buildings.filter((b) => b.yield == null).length;
    const legend = document.createElement('div');
    legend.className = 'map-fx hk-legend';
    legend.innerHTML = '<div class="lg-title">年化毛回报率</div>' +
      '<div class="lg-bar"><span>0</span><div class="lg-gradient"></div><span>6%+</span></div>' +
      '<div class="lg-scale"><span>0</span><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span><span>6+</span></div>' +
      '<div class="lg-filter"><div class="lg-filter-head"><span>最低回报率筛选（>）</span><output id="lgFilterVal">> 3.0%</output></div>' +
      '<input type="range" id="lgFilter" min="0" max="6" step="0.1" value="3" aria-label="最低年化毛回报率筛选（低于或等于阈值的楼盘在地图上隐藏，最高 6%）">' +
      '<div class="lg-filter-head lg-gap"><span>最高楼龄</span><output id="lgAgeVal">≤ 30 年</output></div>' +
      '<input type="range" id="lgAge" class="lg-age" min="0" max="70" step="1" value="30" aria-label="最高楼龄筛选（高于阈值的楼栋在地图上隐藏）" title="无入伙年份记录的楼栋在楼龄筛选时一并隐藏">' +
      '<div class="lg-filter-head lg-gap"><span>1 公里内学校</span><output id="lgSchoolVal">不限</output></div>' +
      '<input type="range" id="lgSchool" class="lg-school" min="0" max="120" step="1" value="0" aria-label="1 公里内学校数下限（含边界触达）">' +
      '<div class="lg-count" id="lgCount"></div>' +
      '<label class="lg-check"><input type="checkbox" id="lgNoYield"><span>样本不足楼栋（' + greyN + ' 栋，灰色）</span></label></div>';
    L.DomEvent.disableClickPropagation(legend);
    L.DomEvent.disableScrollPropagation(legend);
    map.getContainer().appendChild(legend);
    legend.querySelector('#lgFilter').addEventListener('input', (e) => {
      const v = parseFloat(e.target.value) || 0;
      if (yieldRaf) cancelAnimationFrame(yieldRaf);
      yieldRaf = requestAnimationFrame(() => applyYieldFilter(v));
    });
    legend.querySelector('#lgAge').addEventListener('input', (e) => {
      const v = parseFloat(e.target.value) || 0;
      if (ageRaf) cancelAnimationFrame(ageRaf);
      ageRaf = requestAnimationFrame(() => applyAgeFilter(v));
    });
    legend.querySelector('#lgSchool').addEventListener('input', (e) => {
      const v = parseInt(e.target.value, 10) || 0;
      if (schoolRaf) cancelAnimationFrame(schoolRaf);
      schoolRaf = requestAnimationFrame(() => applySchoolFilter(v));
    });
    legend.querySelector('#lgNoYield').addEventListener('change', (e) => setShowNoYield(e.target.checked));
    updateFilterCount();
  })();

  document.addEventListener('change', (e) => {
    const lname = e.target.dataset && e.target.dataset.layer;
    if (!lname) return;
    overlays[lname] = e.target.checked;
    if (lname === 'estateFallback') { updateFilterCount(); rebuildAll(); return; }
    if (lname === 'districts') { buildDistrictLayer(); return; }
    if (lname === 'footprints' && e.target.checked && !footData && map.getZoom() >= FOOTPRINT_MIN_ZOOM) {
      ensureFootprints();
      return;
    }
    rebuildAll();
  });

  // ---------- init ----------
  const fitPts = data.buildings.filter((b) => b.yield != null).map((b) => disp(b.lon, b.lat));
  if (fitPts.length) map.fitBounds(L.latLngBounds(fitPts).pad(0.12));

  let initialized = false;
  function tryInit() {
    if (initialized) return;
    if (map.getSize().x === 0 || map.getSize().y === 0) return;
    initialized = true;
    rebuildAll();
    buildDistrictLayer();
  }
  const initTimer = setInterval(() => { tryInit(); if (initialized) clearInterval(initTimer); }, 250);
  map.whenReady(tryInit);
  window.addEventListener('resize', () => { setTimeout(tryInit, 150); });

  window.__hkDebug = {
    get overlays() { return overlays; },
    get bubbleLayer() { return bubbleLayer; },
    get drawer() { return drawer; },
    get currentBuilding() { return current; },
    rebuildAll, openDrawer, closeDrawer, drawTrend, applyYieldFilter, applyAgeFilter, setShowNoYield,
    disp, get footLayer() { return footLayer; },
    get minYield() { return minYield; },
    get maxAge() { return maxAge; },
    get minSchools() { return minSchools; },
    get showNoYield() { return showNoYield; },
    get side() { return drawer ? (drawer.querySelector('.dr-tab.active') || {}).dataset : null; },
  };
})();
