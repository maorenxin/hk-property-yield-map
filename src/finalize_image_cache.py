#!/usr/bin/env python3
"""Finalize the local gallery cache: audit district buckets and prune obsolete files.

The live cache is WebP-only under site/img/buildings/<district>/ and is looked up
through site/building_images_local.json.  Older pre-WebP files may remain at the
cache root; they are not referenced by the site.  This script reports them and,
with --apply, removes only unreferenced legacy .jpg/.jpeg/.png files.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "site/img/buildings"
MAP = ROOT / "site/building_images_local.json"
MANIFEST = ROOT / "reports/image_cache_manifest.csv"
REPORT = ROOT / "reports/image_cache.md"


def human_mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MiB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="delete unreferenced legacy raster files")
    args = ap.parse_args()

    mapping = json.loads(MAP.read_text())
    refs = {(ROOT / "site" / rel).resolve() for rel in mapping.values()}
    missing = [rel for rel in mapping.values() if not (ROOT / "site" / rel).exists()]
    files = [p for p in IMG.rglob("*") if p.is_file()]
    legacy = [p for p in files if p.suffix.lower() in {".jpg", ".jpeg", ".png"} and p.resolve() not in refs]
    webp = [p for p in files if p.suffix.lower() == ".webp"]
    orphan_webp = [p for p in webp if p.resolve() not in refs]

    by = defaultdict(lambda: {"files": 0, "bytes": 0})
    for p in webp:
        district = p.parent.name if p.parent != IMG else "根目录"
        by[district]["files"] += 1
        by[district]["bytes"] += p.stat().st_size
    rows = [{"district": k, **v} for k, v in sorted(by.items(), key=lambda kv: (-kv[1]["bytes"], kv[0]))]
    pd.DataFrame(rows).to_csv(MANIFEST, index=False)

    legacy_bytes = sum(p.stat().st_size for p in legacy)
    webp_bytes = sum(p.stat().st_size for p in webp)
    print(f"mapped URLs: {len(mapping)} | missing mapped files: {len(missing)}")
    print(f"WebP: {len(webp)} files / {human_mb(webp_bytes)} | orphan WebP: {len(orphan_webp)}")
    print(f"unreferenced legacy raster: {len(legacy)} files / {human_mb(legacy_bytes)}")
    if args.apply and legacy:
        for p in legacy:
            p.unlink()
        print(f"deleted {len(legacy)} legacy files, reclaimed {human_mb(legacy_bytes)}")
    elif legacy:
        print("dry-run only; pass --apply to delete these legacy files")

    lines = [
        "# 楼盘图片本地缓存（WebP，按区）",
        "",
        f"- 映射：`site/building_images_local.json`，{len(mapping)} 个原始 URL；缺失映射文件 **{len(missing)}**。",
        f"- 本地 WebP：**{len(webp)} 张 / {human_mb(webp_bytes)}**，按 18 区/片区目录存放，平均 {webp_bytes/max(1,len(webp))/1024:.1f} KB。",
        f"- 未引用的旧版栅格：**{len(legacy)} 个 / {human_mb(legacy_bytes)}**；{'已清理' if args.apply else '尚未清理（dry-run）'}。",
        f"- 分区分桶：{len(rows)} 个目录；明细见 `reports/image_cache_manifest.csv`。",
        "",
        "| 区域目录 | WebP 张数 | 体积 |",
        "|---|---:|---:|",
    ]
    for r in rows:
        lines.append(f"| {r['district']} | {r['files']} | {human_mb(r['bytes'])} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST.relative_to(ROOT)} and {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
