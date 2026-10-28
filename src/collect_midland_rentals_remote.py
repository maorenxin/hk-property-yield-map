#!/usr/bin/env python3
"""Collect Midland / HKP rental transactions through the GPU host.

The Mac cannot reach data.midland.com.hk (CloudFront 403).  The GPU host can.
This script performs both steps through one SSH/Node bridge:

1. map every map building group to a Midland estate using autocomplete;
2. fetch all 3-year rental transactions for mapped estates, with pagination.

The token is read from .env locally and passed to the remote process over
stdin.  It never appears in an SSH command line or in a generated file.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import subprocess
import time
import unicodedata
from pathlib import Path

import pandas as pd

from rental_group_matching import detect_estate, match_midland_row

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "data/derived/midland_group_map.csv"
OUT = ROOT / "data/derived/midland_rentals_2026-10-18.csv"
RAW = ROOT / "data/raw/midland/2026-10-18/remote"
AUTO = RAW / "pages_autocomplete"
BASE = "https://data.midland.com.hk"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/155.0 Safari/537.36"
SSH_HOST = "gpu"

REMOTE_JS = r"""
const https = require('https');
const token = process.env.TOKEN;
const base = 'https://data.midland.com.hk';
const headers = {
  'Authorization': 'Bearer ' + token,
  'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/155.0 Safari/537.36',
  'Origin': 'https://www.midland.com.hk',
  'Referer': 'https://www.midland.com.hk/'
};
function getJson(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, {headers}, res => {
      let body = '';
      res.on('data', c => body += c);
      res.on('end', () => {
        if (res.statusCode !== 200) return reject(new Error('http ' + res.statusCode + ': ' + body.slice(0, 160)));
        try { resolve(JSON.parse(body)); } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.setTimeout(45000, () => req.destroy(new Error('timeout')));
  });
}
async function retry(fn, n = 3) {
  let last;
  for (let i = 0; i < n; i++) {
    try { return await fn(); } catch (e) {
      last = e;
      if (i + 1 < n) await new Promise(r => setTimeout(r, 800 + i * 1200));
    }
  }
  throw last;
}
async function autocomplete(q) {
  const url = base + '/search/v2/autocomplete/estates?lang=zh-hk&text=' + encodeURIComponent(q) + '&map=false';
  const d = await retry(() => getJson(url));
  const rows = [];
  for (const group of (d || [])) rows.push(...(group.result || []));
  return rows;
}
async function transactions(ids, page) {
  const params = new URLSearchParams({
    chart: 'false', lang: 'zh-hk', currency: 'HKD', unit: 'feet',
    est_ids: ids.join(','), tx_type: 'L', limit: '1000', page: String(page)
  });
  return await retry(() => getJson(base + '/search/v2/transactions?' + params.toString()));
}
async function mapLimit(items, limit, fn) {
  const out = new Array(items.length);
  let next = 0;
  async function worker() {
    while (true) {
      const i = next++;
      if (i >= items.length) return;
      out[i] = await fn(items[i], i);
    }
  }
  await Promise.all(Array.from({length: Math.min(limit, items.length)}, worker));
  return out;
}
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', c => input += c);
process.stdin.on('end', async () => {
  try {
    const p = JSON.parse(input);
    if (p.mode === 'autocomplete') {
      const pairs = await mapLimit(p.queries, p.concurrency || 4, async q => [q, await autocomplete(q)]);
      console.log(JSON.stringify(Object.fromEntries(pairs)));
    } else if (p.mode === 'transactions') {
      const first = await transactions(p.ids, 1);
      const count = Number(first.count || 0);
      const pages = [first];
      const n = Math.min(Number(p.max_pages || 20), Math.max(1, Math.ceil(count / 1000)));
      for (let page = 2; page <= n; page++) pages.push(await transactions(p.ids, page));
      console.log(JSON.stringify({count, pages}));
    } else {
      throw new Error('unknown mode: ' + p.mode);
    }
  } catch (e) {
    console.error(e && e.stack ? e.stack : String(e));
    process.exit(1);
  }
});
"""


def token() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("MIDLAND_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("MIDLAND_TOKEN missing from .env")


def remote(payload: dict, timeout: int = 600) -> dict:
    encoded = base64.b64encode(REMOTE_JS.encode()).decode()
    cmd = f"read TOKEN; TOKEN=\"$TOKEN\" node -e \"$(echo '{encoded}' | base64 -d)\""
    p = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", SSH_HOST, cmd],
        input=token() + "\n" + json.dumps(payload, ensure_ascii=False),
        text=True, capture_output=True, timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr[:500] or "remote call failed")
    return json.loads(p.stdout)


def norm(s) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)


def dist_m(a, b):
    dx = (a[0] - b[0]) * 111320 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (a[1] - b[1]) * 110540
    return math.hypot(dx, dy)


def clean_query(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).strip()
    s = re.sub(r"^\([^)]*\)\s*", "", s)
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)
    s = re.sub(r"\s*(?:住宅物業|別墅洋房|私人住宅|屋苑)\s*$", "", s)
    s = re.sub(r"\s*(?:戰前[-－至]?\d{0,4}年代|\d{2,4}年代)\s*$", "", s)
    if "/" in s:
        s = s.split("/", 1)[0]
    return re.sub(r"\s+", " ", s).strip(" ·‧-")


def load_groups() -> list[dict]:
    js = (ROOT / "site/map_data.js").read_text()
    data = json.loads(js[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    bb = pd.read_csv(ROOT / "data/derived/building_metrics_by_building_12m.csv")
    out = []
    for b in data.get("buildings") or []:
        if b.get("lon") is None or b.get("lat") is None:
            continue
        h, g = str(b["id"]).split(":", 1)
        names = []
        for r in bb[(bb.hma_id == h) & (bb.bldg_grp_id == g)].itertuples():
            n = str(getattr(r, "building_name", "") or "").strip()
            if len(norm(n)) >= 3 and not re.fullmatch(r"(?:第)?\d+座|[a-z]座|第?\d+期", norm(n)):
                names.append(n)
        out.append({
            "gid": b["id"], "name": b.get("name") or "", "bldg": b.get("bldg") or "",
            "lon": float(b["lon"]), "lat": float(b["lat"]),
            "names": list(dict.fromkeys(names)),
        })
    return out


def queries_for(g: dict) -> list[tuple[str, int]]:
    qs: list[tuple[str, int]] = []
    def add(q: str, priority: int):
        q = clean_query(q)
        if q and (q, priority) not in qs:
            qs.append((q, priority))
    # Prefer the actual Chinese estate name; long English RVD group names are
    # only used when no Chinese name is available.
    if re.search(r"[\u4e00-\u9fff]", str(g["name"])):
        add(g["name"], 0)
    add(g["bldg"], 1)
    if not qs:
        add(g["name"], 0)
    for n in g["names"][:3]:
        add(n, 2)
    return qs


def map_groups(groups: list[dict], workers: int) -> pd.DataFrame:
    AUTO.mkdir(parents=True, exist_ok=True)
    query_set = {}
    for g in groups:
        for q, prio in queries_for(g):
            query_set.setdefault(q, []).append((g["gid"], prio))
    cached = {}
    todo = []
    for q in sorted(query_set):
        fp = AUTO / (hashlib.sha1(q.encode()).hexdigest()[:16] + ".json")
        if fp.exists():
            try:
                cached[q] = json.loads(fp.read_text())
                continue
            except Exception:
                pass
        todo.append((q, fp))
    print(f"autocomplete queries: {len(query_set)} total, {len(cached)} cached, {len(todo)} to fetch", flush=True)
    for i in range(0, len(todo), 80):
        chunk = todo[i:i + 80]
        print(f"autocomplete {i+1}-{i+len(chunk)}/{len(todo)}", flush=True)
        got = remote({"mode": "autocomplete", "queries": [q for q, _ in chunk],
                      "concurrency": max(1, min(6, workers))}, timeout=600)
        for q, fp in chunk:
            rows = got.get(q, [])
            fp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
            cached[q] = rows
    rows = []
    for g in groups:
        best = None
        for q, prio in queries_for(g):
            for x in cached.get(q, []):
                typ = (x.get("search") or {}).get("type")
                if typ != "estate":
                    continue
                nm = ((x.get("name") or {}).get("zh-hk") or (x.get("name") or {}).get("en") or "")
                if norm(nm) != norm(q):
                    continue
                loc = x.get("location") or {}
                if loc.get("lon") is None or loc.get("lat") is None:
                    continue
                d = dist_m((g["lon"], g["lat"]), (float(loc["lon"]), float(loc["lat"])))
                if d > 700:
                    continue
                # Query priority + exact-name match; nearest wins ties.
                score = (1 if prio == 0 else 0, -prio, -d)
                if best is None or score > best[0]:
                    best = (score, {
                        "gid": g["gid"], "query": q, "midland_id": (x.get("search") or {}).get("id"),
                        "midland_type": typ, "midland_name": nm,
                        "parent_estate_id": (x.get("search") or {}).get("id"),
                        "dist_m": round(d, 1),
                    })
        if best:
            rows.append(best[1])
        else:
            rows.append({"gid": g["gid"], "query": queries_for(g)[0][0] if queries_for(g) else "",
                         "midland_id": None, "midland_type": None, "midland_name": None,
                         "parent_estate_id": None, "dist_m": None})
    df = pd.DataFrame(rows)
    # Never lose a previously verified mapping when a new query misses.
    if MAP.exists():
        old = pd.read_csv(MAP)
        old = old[old.midland_id.notna()]
        known = set(df.loc[df.midland_id.notna(), "gid"])
        fill = old[~old.gid.isin(known)]
        if len(fill):
            df = pd.concat([df, fill], ignore_index=True).drop_duplicates("gid", keep="first")
    df.to_csv(MAP, index=False)
    print(f"mapped groups: {df.midland_id.notna().sum()}/{len(df)}; unique estates: {df.parent_estate_id.nunique()}", flush=True)
    return df


def fetch_rentals(mapping: pd.DataFrame, max_pages: int, refresh: bool) -> pd.DataFrame:
    hit = mapping[mapping.parent_estate_id.notna()].copy()
    ids = sorted({str(x) for x in hit.parent_estate_id.dropna() if str(x).startswith("E")})
    batches = [ids[i:i + 20] for i in range(0, len(ids), 20)]
    all_pages = []
    for bi, batch in enumerate(batches, 1):
        fp = RAW / f"batch_{bi:03d}.json"
        d = None
        if fp.exists() and not refresh:
            try:
                cached = json.loads(fp.read_text())
                if cached.get("ids") == batch:
                    d = cached
            except Exception:
                pass
        if d is None:
            print(f"rent batch {bi}/{len(batches)} ids={len(batch)}", flush=True)
            last = None
            for attempt in range(7):
                try:
                    d = remote({"mode": "transactions", "ids": batch, "max_pages": max_pages}, timeout=600)
                    break
                except Exception as e:  # noqa: BLE001
                    last = e
                    msg = str(e)
                    if "401" in msg:
                        raise
                    wait = min(120, 15 + attempt * 15)
                    print(f"batch {bi} transient error; sleep {wait}s: {msg[:120]}", flush=True)
                    time.sleep(wait)
            if d is None:
                raise RuntimeError(f"batch {bi} failed: {last}")
            d["ids"] = batch
            fp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
            time.sleep(1.0)
        all_pages.extend(d.get("pages") or [])
    by_estate = {}
    for r in hit.itertuples():
        by_estate.setdefault(str(r.parent_estate_id), []).append(r)
    rows = []
    for page in all_pages:
        for x in page.get("result") or []:
            est = str((x.get("estate") or {}).get("id") or "")
            phase = str((x.get("phase") or {}).get("id") or "")
            bname = norm((x.get("building") or {}).get("name"))
            pname = norm((x.get("phase") or {}).get("name"))
            cands = by_estate.get(est) or []
            chosen = None
            if phase:
                ph = [r for r in cands if str(r.midland_id) == phase]
                if len(ph) == 1:
                    chosen = ph[0]
            if chosen is None:
                scored = []
                for r in cands:
                    cn = norm(getattr(r, "midland_name", ""))
                    cg = norm(getattr(r, "query", ""))
                    score = 0
                    if pname and (pname == cg or pname in cg or cg in pname):
                        score = 90
                    elif bname and bname == cn:
                        score = 80
                    elif bname and (bname in cn or cn in bname):
                        score = 60
                    elif pname and (pname in cn or cn in pname):
                        score = 55
                    elif cg and (cg in bname or bname in cg):
                        score = 40
                    elif len(cands) == 1:
                        score = 10
                    if score:
                        scored.append((score, r))
                scored.sort(key=lambda z: z[0], reverse=True)
                if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                    chosen = scored[0][1]
                elif len(cands) == 1:
                    chosen = cands[0]
            if not chosen:
                continue
            m_estate = str((x.get("estate") or {}).get("name") or "")
            m_phase = str((x.get("phase") or {}).get("name") or "")
            m_bld = str((x.get("building") or {}).get("name") or "")
            target = detect_estate(" ".join([m_estate, m_phase, m_bld]), m_estate)
            if target in {"太古城", "匯璽", "日出康城", "映灣園", "珀麗灣", "沙田第一城"}:
                mm = match_midland_row({"estate_name": m_estate, "phase_name": m_phase,
                                        "building_name": m_bld,
                                        "floor": (x.get("floor_level") or {}).get("name") or "",
                                        "unit": x.get("flat") or ""})
                if mm.group_id is None:
                    continue
                chosen = type("Mapped", (), {"gid": mm.group_id})()
            dt = str(x.get("tx_date") or "")[:10]
            area = x.get("net_area") or x.get("area")
            price = x.get("price")
            rows.append({
                "source": "midland", "source_record_id": x.get("id"),
                "source_url": x.get("url_desc"), "transaction_date": dt, "deal_date": dt,
                "group_id": chosen.gid, "midland_estate_id": est, "midland_phase_id": phase,
                "estate_name": (x.get("estate") or {}).get("name"),
                "phase_name": (x.get("phase") or {}).get("name"),
                "building_name": (x.get("building") or {}).get("name"),
                "floor": (x.get("floor_level") or {}).get("name"), "unit": x.get("flat"),
                "area_sqft": area, "rent_hkd": price, "psf": x.get("unit_price_net"),
                "retrieved_at": "2026-10-18",
            })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.drop_duplicates("source_record_id")
    out.to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(ROOT)} rows={len(out)} groups={out.group_id.nunique() if len(out) else 0}", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map-only", action="store_true")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-fetch transaction batches")
    ap.add_argument("--max-pages", type=int, default=20, help="max 1000-row pages per estate batch")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    if args.fetch_only:
        if not MAP.exists():
            raise SystemExit("mapping missing; run without --fetch-only")
        mapping = pd.read_csv(MAP)
    else:
        mapping = map_groups(load_groups(), args.workers)
    if not args.map_only:
        fetch_rentals(mapping, args.max_pages, args.refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
