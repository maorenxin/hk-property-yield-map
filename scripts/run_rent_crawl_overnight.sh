#!/bin/bash
set -u
cd /Users/mao/code/hongkong
mkdir -p logs
LOG="logs/rent_full_overnight.log"
PY="/Library/Frameworks/Python.framework/Versions/3.10/bin/python3"
DONE="data/raw/centaline/rent_full/2026-10-18/DONE"
if [ -f "$DONE" ]; then
  echo "already complete: $DONE" >> "$LOG"
  exit 0
fi
echo "=== overnight rental crawl start $(date) ===" >> "$LOG"
crawl_attempt=0
for attempt in $(seq 1 24); do
  h=$(date +%H)
  if [ "$h" -ge 8 ] && [ "$h" -lt 23 ]; then
    echo "outside 23:00-08:00 window, stop $(date)" >> "$LOG"
    break
  fi
  if ! "$PY" -u scripts/centaline_quota_probe.py >> "$LOG" 2>&1; then
    echo "quota not ready; sleep 30m $(date)" >> "$LOG"
    sleep 1800
    continue
  fi
  crawl_attempt=$((crawl_attempt + 1))
  pace=8
  if [ "$crawl_attempt" -ge 2 ]; then pace=15; fi
  if [ "$crawl_attempt" -ge 4 ]; then pace=25; fi
  echo "quota ok; crawl attempt $crawl_attempt pace=${pace}s $(date)" >> "$LOG"
  "$PY" -u src/collect_centaline_rent_full.py --pace "$pace" >> "$LOG" 2>&1
  code=$?
  echo "crawl attempt $crawl_attempt exit=$code $(date)" >> "$LOG"
  if [ "$code" -eq 0 ]; then
    failed_pages=$("$PY" - <<'PYEOF'
import json
from pathlib import Path
p = Path('data/raw/centaline/rent_full/2026-10-18/summary.json')
try:
    print(int(json.loads(p.read_text()).get('pages_failed') or 0))
except Exception:
    print(0)
PYEOF
)
    if [ "${failed_pages:-0}" -gt 0 ]; then
      echo "crawl summary has pages_failed=$failed_pages; retry after 30m" >> "$LOG"
      code=3
    fi
  fi
  if [ "$code" -eq 0 ]; then
    echo "crawl complete; normalizing and rebuilding $(date)" >> "$LOG"
    "$PY" -u src/normalize_centaline_full.py >> "$LOG" 2>&1 || exit 1
    "$PY" -u src/build_building_metrics.py >> "$LOG" 2>&1 || exit 1
    "$PY" -u src/build_artifacts.py >> "$LOG" 2>&1 || exit 1
    touch "$DONE"
    echo "=== pipeline complete $(date); marker=$DONE ===" >> "$LOG"
    break
  fi
  echo "crawl stopped; sleep 30m then retry $(date)" >> "$LOG"
  sleep 1800
done
