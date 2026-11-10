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
    assert is_resolved({"status": "ok", "address_match": True})
    assert not is_resolved({"status": "ok", "address_match": False})
    assert not is_resolved({"status": "error", "address_match": True})
    assert not is_resolved(None)


def test_all_cached_unresolved_entries_count_toward_budget():
    cache = {
        "ok": {"status": "ok", "address_match": True},
        "mismatch": {"status": "ok", "address_match": False},
        "error": {"status": "error"},
        "not_found": {"status": "not_found"},
    }
    keys = set(cache) | {"missing"}

    assert unresolved_coordinates(cache, keys) == ["error", "mismatch", "missing", "not_found"]


def test_coordinate_budget_and_errors_are_enforced():
    assert coordinate_exit_code(0, 20) == 0
    assert coordinate_exit_code(20, 20) == 0
    assert coordinate_exit_code(21, 20) == 2


def test_cached_error_uses_same_total_unresolved_budget():
    cache = {"address": {"status": "error", "error": "HTTP 503"}}

    unresolved = unresolved_coordinates(cache, {"address"})

    assert coordinate_exit_code(len(unresolved), 20) == 0
