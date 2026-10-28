from datetime import date
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from update_hk_daily import date_slices, merge_frames  # noqa: E402


def test_date_slices_are_inclusive_and_contiguous():
    assert date_slices(date(2026, 10, 28), 7, 3) == [
        (date(2026, 10, 22), date(2026, 10, 24)),
        (date(2026, 10, 25), date(2026, 10, 27)),
        (date(2026, 10, 28), date(2026, 10, 28)),
    ]


def test_merge_frames_refreshes_same_id_and_keeps_history():
    base = pd.DataFrame(
        [
            {"source_record_id": "a", "analysis_date": pd.Timestamp("2026-01-01"), "price_hkd": 1},
            {"source_record_id": "b", "analysis_date": pd.Timestamp("2026-01-02"), "price_hkd": 2},
        ]
    )
    fresh = pd.DataFrame(
        [
            {"source_record_id": "a", "analysis_date": pd.Timestamp("2026-01-01"), "price_hkd": 10},
            {"source_record_id": "c", "analysis_date": pd.Timestamp("2026-01-03"), "price_hkd": 3},
        ]
    )
    merged, added = merge_frames(base, fresh)
    assert added == 1
    assert merged.source_record_id.tolist() == ["a", "b", "c"]
    assert merged.loc[merged.source_record_id == "a", "price_hkd"].item() == 10
