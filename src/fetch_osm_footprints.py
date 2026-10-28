#!/usr/bin/env python3
"""Fetch OSM building footprints for the Phase-1 footprint prototype.

Target groups = groups with per-building breakdown AND at least one RVD age match
(the 616 high-confidence set). Points are clustered into ~2km cells; each cell is
fetched from Overpass once and cached (resume-safe).
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import math
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/osm/2026-10-18/cells"
UA = "hk-yield-footprint-research/1.0 (contact: local)"
CELL = 0.025  # ~2.8km


def main() -> int:
    data = json.loads((ROOT / "site/map_data.js").read_text()[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    builds = {b["id"]: b for b in data["buildings"]}
    rvd = pd.read_csv(ROOT / "data/derived/building_age_rvd.csv")
    hi = rvd[rvd.match.isin(["name+addr", "estate+phase", "name"])]
    gids_hi = {f"{r.hma_id}:{r.bldg_grp_id}" for r in hi.itertuples()}
    bb = pd.read_csv(ROOT / "data/derived/building_metrics_by_building_12m.csv")
    gids_bld = {f"{r.hma_id}:{r.bldg_grp_id}" for r in bb.itertuples()}
    targets = sorted(gids_hi & gids_bld & set(builds))
    print(f"Phase-1 target groups: {len(targets)}")

    cells = {}
    for gid in targets:
        b = builds[gid]
        cx = math.floor(b["lon"] / CELL)
        cy = math.floor(b["lat"] / CELL)
        cells.setdefault((cx, cy), []).append(gid)
    print(f"cells: {len(cells)}")
    OUT.mkdir(parents=True, exist_ok=True)

    def fetch(cell):
        cx, cy = cell
        fp = OUT / f"{cx}_{cy}.json"
        if fp.exists() and fp.stat().st_size > 100:
            return cell, "cached"
        lon0, lat0 = cx * CELL - 0.004, cy * CELL - 0.004
        lon1, lat1 = (cx + 1) * CELL + 0.004, (cy + 1) * CELL + 0.004
        q = f'[out:json][timeout:180];way["building"]({lat0:.5f},{lon0:.5f},{lat1:.5f},{lon1:.5f});out geom;'
        for attempt in range(3):
            try:
                r = requests.post("https://overpass-api.de/api/interpreter",
                                  data={"data": q}, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=260)
                if r.status_code == 200 and len(r.content) > 50:
                    fp.write_bytes(r.content)
                    time.sleep(1.5)
                    return cell, f"ok {len(r.content)//1024}KB"
                print(f"    cell {cell} http {r.status_code}", flush=True)
                time.sleep(6 + attempt * 12)
            except Exception as e:  # noqa: BLE001
                time.sleep(3 + attempt * 5)
        return cell, "fail"

    ok = cached = fail = 0
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        for cell, status in ex.map(fetch, sorted(cells)):
            if status.startswith("ok"):
                ok += 1
            elif status == "cached":
                cached += 1
            else:
                fail += 1
            print(f"  cell {cell}: {status} | ok={ok} cached={cached} fail={fail}", flush=True)
    meta = {"target_groups": len(targets), "cells": len(cells), "cell_size": CELL,
            "source": "OpenStreetMap via Overpass API", "license": "ODbL 1.0",
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (OUT.parent / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"done: ok={ok} cached={cached} fail={fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
