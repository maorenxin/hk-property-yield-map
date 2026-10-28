#!/usr/bin/env python3
"""Single low-cost Centaline quota probe. Exit 0 only on HTTP 200."""
import sys
import requests

URL = "https://hk-api.centanet.com/centanetapi/api/Transaction/Search"
BODY = {"day": "", "sort": "InsOrRegDate", "order": "Ascending", "offset": 0, "size": 1,
        "estateUsages": ["RE", "HOS"], "postType": "Rent",
        "insOrRegDateTimeRange": {"start": "2026-09-01", "end": "2026-10-01"}}
HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0",
           "Origin": "https://hk.centanet.com", "Referer": "https://hk.centanet.com/"}
try:
    r = requests.post(URL, json=BODY, headers=HEADERS, timeout=30)
    print(f"probe {r.status_code} {r.text[:120]}", flush=True)
    sys.exit(0 if r.status_code == 200 else 2)
except Exception as exc:
    print(f"probe error {type(exc).__name__}: {exc}", flush=True)
    sys.exit(2)
