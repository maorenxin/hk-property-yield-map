from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from collect_orphan_coordinates import (  # noqa: E402
    coordinate_exit_code,
    is_resolved,
    request_autocomplete_coordinate,
    request_coordinate,
    unresolved_coordinates,
)


class _Response:
    status_code = 204
    text = ""

    def json(self) -> dict:
        raise AssertionError("HTTP 204 must not be parsed as JSON")


class _Session:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, *args, **kwargs) -> _Response:
        self.calls += 1
        return _Response()


def test_http_204_coordinate_miss_is_not_retried():
    session = _Session()

    result = request_coordinate(session, "1-TEST", "測試地址1號", retries=5)

    assert session.calls == 1
    assert result == {
        "status": "not_found",
        "type_code": "1-TEST",
        "error": "HTTP 204: no coordinate match",
    }


class _PostSession:
    def __init__(self) -> None:
        self.calls = 0

    def post(self, *args, **kwargs) -> _Response:
        self.calls += 1
        return _Response()


def test_http_204_autocomplete_miss_is_not_retried():
    session = _PostSession()

    result = request_autocomplete_coordinate(session, "測試地址1號", retries=3)

    assert session.calls == 1
    assert result == {
        "status": "not_found",
        "type_code": None,
        "error": "HTTP 204: no autocomplete match",
    }


def test_resolved_requires_matching_address():
    coords = {"lat": 22.3, "lon": 114.2}
    assert is_resolved({"status": "ok", "address_match": True, **coords})
    assert not is_resolved({"status": "ok", "address_match": False, **coords})
    assert not is_resolved({"status": "error", "address_match": True, **coords})
    assert not is_resolved(None)


def test_stale_address_match_flag_is_recomputed():
    item = {
        "status": "ok",
        "address": "小瀝源路62-78號",
        "address_match": False,
        "lat": 22.3,
        "lon": 114.2,
    }

    assert is_resolved(item, "小瀝源路62號")


def test_all_cached_unresolved_entries_count_toward_budget():
    cache = {
        "ok": {"status": "ok", "address_match": True, "lat": 22.3, "lon": 114.2},
        "stale-ok": {
            "status": "ok",
            "address": "小瀝源路62-78號",
            "address_match": False,
            "lat": 22.3,
            "lon": 114.2,
        },
        "mismatch": {
            "status": "ok",
            "address": "測試路9號",
            "address_match": False,
            "lat": 22.3,
            "lon": 114.2,
        },
        "error": {"status": "error"},
        "not_found": {"status": "not_found"},
    }
    expected = {
        "ok": "測試路1號",
        "stale-ok": "小瀝源路62號",
        "mismatch": "測試路1號",
        "error": "測試路1號",
        "not_found": "測試路1號",
        "missing": "測試路1號",
    }

    assert unresolved_coordinates(cache, expected) == ["error", "mismatch", "missing", "not_found"]


def test_coordinate_budget_and_errors_are_enforced():
    assert coordinate_exit_code(0, 20) == 0
    assert coordinate_exit_code(20, 20) == 0
    assert coordinate_exit_code(21, 20) == 2


def test_cached_error_uses_same_total_unresolved_budget():
    cache = {"address": {"status": "error", "error": "HTTP 503"}}

    unresolved = unresolved_coordinates(cache, {"address": "測試路1號"})

    assert coordinate_exit_code(len(unresolved), 20) == 0
