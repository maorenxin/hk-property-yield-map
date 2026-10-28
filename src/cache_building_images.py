#!/usr/bin/env python3
"""Cache gallery images locally as WebP (2 per group, width<=640) so the drawer
survives CDN link rot. Files are bucketed by 18-district folder. Idempotent.

Output: site/img/buildings/<district>/<sha1>.webp ; site/building_images_local.json
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
import time
import urllib.parse
from pathlib import Path

import io

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site/img/buildings"
LOCAL_MAP = ROOT / "site/building_images_local.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
MAX_PER_GROUP = 6
WIDTH = 640


def sized(url: str) -> str:
    if "hkvrdata.centanet.com" in url:
        return re.sub(r"x-oss-process=[^&]+", f"x-oss-process=image/resize,w_{WIDTH}", url)
    if "hkais.centanet.com" in url:
        sep = "&" if "?" in url else "?"
        return url if "width=" in url else f"{url}{sep}width={WIDTH}"
    return url


def main() -> int:
    imgs = json.loads((ROOT / "data/derived/building_images.json").read_text())
    js = (ROOT / "site/map_data.js").read_text()
    data = json.loads(js[len("window.HK_MAP_DATA="):].rstrip().rstrip(";"))
    district_of = {b["id"]: (b.get("district") or "未知") for b in data["buildings"]}
    OUT.mkdir(parents=True, exist_ok=True)
    local = json.loads(LOCAL_MAP.read_text()) if LOCAL_MAP.exists() else {}

    tasks = []
    for gid in district_of:
        for item in (imgs.get(gid) or [])[:MAX_PER_GROUP]:
            u = item.get("u") or ""
            if not any(h in u for h in ("centanet.com", "centadata.com")):
                continue
            tasks.append((gid, district_of.get(gid, "未知"), u))

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})

    def to_webp(raw: bytes, dst: Path) -> bool:
        try:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            if im.width > WIDTH:
                im = im.resize((WIDTH, round(im.height * WIDTH / im.width)), Image.LANCZOS)
            dst.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst, "WEBP", quality=78, method=4)
            return True
        except Exception:  # noqa: BLE001
            return False

    def fetch(task):
        gid, district, u = task
        h = hashlib.sha1(u.encode()).hexdigest()[:16]
        fp = OUT / district / f"{h}.webp"
        rel = f"img/buildings/{district}/{h}.webp"
        if fp.exists() and fp.stat().st_size > 1500:
            return u, rel, "cached"
        legacy = OUT / f"{h}.jpg"
        if legacy.exists() and legacy.stat().st_size > 2000:
            if to_webp(legacy.read_bytes(), fp):
                legacy.unlink(missing_ok=True)
                return u, rel, "converted"
        url = sized(u)
        candidates = [url]
        # A handful of upstream records use a truncated 6-digit date path
        # (Images/0240316/...): the real object is Images/20240316/...
        m = re.search(r"/Images/(0\d{6})/", url)
        if m:
            candidates.append(url.replace(f"/Images/{m.group(1)}/", f"/Images/2{m.group(1)}/", 1))
        last = "fail"
        for url2 in candidates:
            for attempt in range(3):
                try:
                    r = sess.get(url2, timeout=25)
                    if r.status_code == 200 and len(r.content) > 2000:
                        if to_webp(r.content, fp):
                            return u, rel, "ok"
                        return u, None, "webp fail"
                    last = f"http {r.status_code}"
                    if r.status_code in (403, 404):
                        break
                except Exception as e:  # noqa: BLE001
                    last = f"err {type(e).__name__}"
                time.sleep(0.4 + attempt * 0.8)
        return u, None, last

    ok = skipped = failed = 0
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for u, rel, status in ex.map(fetch, tasks, chunksize=8):
            if rel:
                local[u] = rel
                if status in ("cached", "converted"):
                    skipped += 1
                else:
                    ok += 1
            else:
                failed += 1
            if (ok + skipped + failed) % 200 == 0:
                LOCAL_MAP.write_text(json.dumps(local, ensure_ascii=False, indent=0))
                print(f"progress {ok+skipped+failed}/{len(tasks)} ok={ok} cached={skipped} failed={failed}", flush=True)
    LOCAL_MAP.write_text(json.dumps(local, ensure_ascii=False, indent=0))
    print(f"done: tasks {len(tasks)}, downloaded {ok}, cached/converted {skipped}, failed {failed}, map entries {len(local)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
