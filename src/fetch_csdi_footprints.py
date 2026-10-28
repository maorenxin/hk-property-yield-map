#!/usr/bin/env python3
"""Fetch official CSDI/LandsD building footprints for Phase-1 groups.

The local machine cannot complete TLS to *.gov.hk (fake-IP / TLS reset).  This
collector can either use the DeepSeek Fetch backend or, preferably, fetch the
official FeatureServer through an SSH host with working egress (for example
`--ssh-host japan`).  It caches the decoded raw ArcGIS JSON per group and is
resumable.

Output:
  data/raw/csdi/2026-10-18/pages/<hma>_<grp>.json
  data/raw/csdi/2026-10-18/manifest.csv
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-10-18"
RAW = ROOT / "data/raw/csdi" / DATE
PAGES = RAW / "pages"
MANIFEST = RAW / "manifest.csv"
DEEPSEEK_ENV = Path.home() / ".codex/mcp/deepseek-search/.env"
SERVICE = ("https://portal.csdi.gov.hk/server/rest/services/common/"
           "landsd_rcd_1637211194312_35158/FeatureServer/0/query")
RADIUS_M = 110
FIELDS = "OBJECTID,BuildingNameEN,BuildingNameTC,BuildingBlockType,Status,Storeys,TopHeight,Shape__Area"


class RateLimiter:
    def __init__(self, qps: float = 4.5):
        self.interval = 1.0 / qps
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                time.sleep(self.next_at - now)
                now = time.monotonic()
            self.next_at = max(now, self.next_at) + self.interval


def load_key() -> str:
    import os

    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    if not DEEPSEEK_ENV.exists():
        raise SystemExit("DEEPSEEK_API_KEY is not set and MCP .env is missing")
    for line in DEEPSEEK_ENV.read_text().splitlines():
        m = re.match(r"\s*DEEPSEEK_API_KEY\s*=\s*(.*?)\s*$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    raise SystemExit("DEEPSEEK_API_KEY not found")


def decode_fetch_content(content: str) -> dict:
    """DeepSeek Fetch wraps JSON with escaped punctuation; undo that wrapper."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = text.replace('\\"', '"')
    text = re.sub(r'\\(?=[^\\"/bfnrtu])', "", text)
    return json.loads(text)


def make_url(lon: float, lat: float) -> str:
    params = {
        "where": "Status='Active'",
        "geometry": f"{lon:.7f},{lat:.7f}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": str(RADIUS_M),
        "units": "esriSRUnit_Meter",
        "outFields": FIELDS,
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": "500",
        "f": "json",
    }
    return SERVICE + "?" + urlencode(params)


def target_groups() -> list[dict]:
    data = json.loads((ROOT / "site/map_data.js").read_text()[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    builds = {b["id"]: b for b in data["buildings"]}
    rvd = pd.read_csv(ROOT / "data/derived/building_age_rvd.csv")
    hi = rvd[rvd.match.isin(["name+addr", "estate+phase", "name"])]
    gids_hi = {f"{r.hma_id}:{r.bldg_grp_id}" for r in hi.itertuples()}
    bb = pd.read_csv(ROOT / "data/derived/building_metrics_by_building_12m.csv")
    gids_bld = {f"{r.hma_id}:{r.bldg_grp_id}" for r in bb.itertuples()}

    def is_street(b: dict) -> bool:
        s = str(b.get("bldg") or "")
        return bool(b.get("age_mixed")) or ("住宅物業" in s and " / " in s)

    return [builds[gid] for gid in sorted(builds) if gid in gids_hi and gid in gids_bld and is_street(builds[gid])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssh-host", default="", help="fetch through `ssh <host> curl` (e.g. japan)")
    ap.add_argument("--force", action="store_true", help="ignore cached pages and re-fetch every target")
    ap.add_argument("--workers", type=int, default=4, help="concurrent fetches")
    args = ap.parse_args()
    key = "" if args.ssh_host else load_key()
    groups = target_groups()
    print(f"CSDI Phase-1 target groups: {len(groups)}; radius={RADIUS_M}m")
    PAGES.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    limiter = RateLimiter(4.5)
    existing = pd.read_csv(MANIFEST) if MANIFEST.exists() else pd.DataFrame()
    rows = [] if existing.empty else existing.to_dict("records")

    def one(b: dict):
        gid = b["id"]
        safe = gid.replace(":", "_")
        fp = PAGES / f"{safe}.json"
        if fp.exists() and fp.stat().st_size > 20 and not args.force:
            try:
                d = json.loads(fp.read_text())
                return {"gid": gid, "status": "cached", "features": len(d.get("features", [])),
                        "bytes": fp.stat().st_size, "error": "", "fetch_method": "cache"}
            except Exception:
                pass
        url = make_url(float(b["lon"]), float(b["lat"]))
        if args.ssh_host:
            last = ""
            for attempt in range(3):
                try:
                    cmd = "curl -sS --compressed -H 'User-Agent: hk-yield-research/1.0' '" + url + "'"
                    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", args.ssh_host, cmd],
                                       capture_output=True, text=True, timeout=120)
                    if r.returncode == 0 and r.stdout.strip():
                        d = json.loads(r.stdout)
                        fp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                        return {"gid": gid, "status": "ok", "features": len(d.get("features", [])),
                                "bytes": fp.stat().st_size, "error": "", "url": url,
                                "sha256": hashlib.sha256(fp.read_bytes()).hexdigest(),
                                "fetch_method": f"ssh:{args.ssh_host}"}
                    last = r.stderr[:160].strip() if "r" in locals() else "ssh/curl failed"
                except Exception as e:  # noqa: BLE001
                    last = f"{type(e).__name__}: {e}"
                time.sleep(2 + attempt * 4)
            return {"gid": gid, "status": "failed", "features": 0, "bytes": 0, "error": last, "url": url}
        last = ""
        for attempt in range(3):
            limiter.wait()
            try:
                r = sess.post("https://api.deepseek.com/v1/tools/fetch", json={"url": url}, timeout=120)
                if r.status_code != 200:
                    last = f"api {r.status_code}: {r.text[:160]}"
                    time.sleep(2 + attempt * 4)
                    continue
                content = r.json().get("content", "")
                d = decode_fetch_content(content)
                if not isinstance(d, dict) or "features" not in d:
                    last = "unexpected fetch content"
                    time.sleep(2 + attempt * 4)
                    continue
                fp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                return {"gid": gid, "status": "ok", "features": len(d.get("features", [])),
                        "bytes": fp.stat().st_size, "error": "", "url": url,
                        "sha256": hashlib.sha256(fp.read_bytes()).hexdigest(),
                        "fetch_method": "deepseek"}
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                time.sleep(2 + attempt * 4)
        return {"gid": gid, "status": "failed", "features": 0, "bytes": 0, "error": last, "url": url}

    ok = cached = failed = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for i, res in enumerate(ex.map(one, groups), 1):
            rows.append(res)
            if res["status"] == "ok":
                ok += 1
            elif res["status"] == "cached":
                cached += 1
            else:
                failed += 1
            if i % 25 == 0 or i == len(groups):
                print(f"progress {i}/{len(groups)} ok={ok} cached={cached} failed={failed}", flush=True)
                tmp = pd.DataFrame(rows).drop_duplicates("gid", keep="last")
                tmp[tmp.gid.isin({g["id"] for g in groups})].to_csv(MANIFEST, index=False)
    out = pd.DataFrame(rows).drop_duplicates("gid", keep="last")
    out = out[out.gid.isin({g["id"] for g in groups})]
    out.to_csv(MANIFEST, index=False)
    print(f"done: ok={ok} cached={cached} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
