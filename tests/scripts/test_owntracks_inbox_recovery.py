"""The OwnTracks inbox-only recovery dry run: classification, summary and its read-only shape.

The query runs against Postgres in the owner-gated one-shot; nothing here touches a database
(bu-uhbhzq.4 slice 5).
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from butlers.roster_imports import roster_module

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recovery = _load("owntracks_inbox_recovery", "scripts/owntracks_inbox_recovery.py")

_TST = 1_750_000_000
_KEY = f"owntracks:owntracks:synthetic:{_TST}:location"
_RECEIVED = datetime(2026, 6, 20, 12, tzinfo=UTC)


def _row(key=_KEY, text="", raw=None, received=_RECEIVED):
    return {"key": key, "normalized_text": text, "raw": raw, "received_at": received}


def test_a_full_tier_row_is_recovered_from_its_raw_body() -> None:
    raw = {"_type": "location", "lat": 1.5, "lon": 103.5, "tst": _TST, "acc": 5}
    assert recovery.classify(_KEY, "", raw) == ("from_raw", _TST)


def test_a_raw_body_without_tst_takes_the_key_time_and_a_json_string_is_decoded() -> None:
    assert recovery.classify(_KEY, "", json.dumps({"lat": 1.5, "lon": 103.5}))[0] == "from_raw"


def test_a_raw_body_for_another_time_is_a_key_mismatch() -> None:
    raw = {"lat": 1.5, "lon": 103.5, "tst": _TST + 1}
    assert recovery.classify(_KEY, "", raw) == ("unrecoverable_key_mismatch", None)


@pytest.mark.parametrize(
    "text",
    [
        "Location update: 1.5N, 103.5E",
        "Location update: 1.5S, 103.5W, acc 12.0m, 30 km/h (in: home)",
        "Location update: 0.0N, 0.0E, acc 5m",
    ],
)
def test_a_metadata_tier_row_is_recovered_from_its_text(text: str) -> None:
    assert recovery.classify(_KEY, text, None) == ("from_text", _TST)


def test_the_text_parse_keeps_the_hemisphere_signs() -> None:
    assert recovery._from_text("Location update: 1.5S, 103.5W, acc 12.0m") == {
        "lat": -1.5,
        "lon": -103.5,
        "acc": 12.0,
    }


@pytest.mark.parametrize(
    ("text", "raw", "expected"),
    [
        (recovery.WITHHELD_TEXT, None, "unrecoverable_withheld"),
        ("something else entirely", None, "unrecoverable_unreadable"),
        ("", None, "unrecoverable_unreadable"),
        ("Location update: 95.0N, 103.5E", None, "rejected"),
        ("", {"lat": 95.0, "lon": 0.0}, "rejected"),
    ],
)
def test_unusable_rows_are_counted_not_recovered(text, raw, expected) -> None:
    assert recovery.classify(_KEY, text, raw)[0] == expected


def test_a_malformed_key_is_unrecoverable() -> None:
    assert recovery.classify("owntracks:x:location", "", {"lat": 1, "lon": 1})[0] == (
        "unrecoverable_bad_key"
    )


def test_the_summary_is_counts_and_ranges_only() -> None:
    other_key = f"owntracks:owntracks:synthetic:{_TST + 60}:location"
    report = recovery.summarize(
        [
            _row(raw={"lat": 1.5, "lon": 103.5, "tst": _TST}),
            _row(key=other_key, text="Location update: 1.5N, 103.5E"),
            _row(key=f"owntracks:e:{_TST + 120}:location", text=recovery.WITHHELD_TEXT),
        ]
    )
    assert report["mode"] == "dry-run"
    assert report["candidates"] == 3
    assert report["recoverable"] == {"from_raw": 1, "from_text": 1}
    assert report["not_recoverable"] == {"unrecoverable_withheld": 1}
    assert report["fix_time_range"] == [
        datetime.fromtimestamp(_TST, tz=UTC).isoformat(),
        datetime.fromtimestamp(_TST + 60, tz=UTC).isoformat(),
    ]
    printed = json.dumps(report)
    assert "synthetic" not in printed and "103.5" not in printed


def test_an_empty_candidate_set_reports_zeroes_and_null_ranges() -> None:
    report = recovery.summarize([])
    assert report["candidates"] == 0
    assert report["fix_time_range"] == [None, None]
    assert report["received_at_range"] == [None, None]


def test_the_only_sql_is_a_select_over_the_expected_exclusions() -> None:
    sql = recovery.CANDIDATES_SQL
    assert sql.lstrip().upper().startswith("SELECT")
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP|CREATE)\b", sql, re.I)
    assert "connectors.owntracks_points" in sql
    assert "switchboard.location_retention_forgotten_keys" in sql


def test_the_withheld_text_is_the_switchboard_tool_s() -> None:
    tool = roster_module("switchboard", "tools/location_retention")
    assert recovery.WITHHELD_TEXT == tool.WITHHELD_TEXT


def test_there_is_no_apply_mode() -> None:
    with pytest.raises(SystemExit):
        recovery.main(["--apply"])


def test_a_missing_connection_is_refused_without_connecting(capsys) -> None:
    assert recovery.main([], stdin=io.StringIO("")) == 2
    assert "connection_input_missing" in capsys.readouterr().out


def test_a_driver_failure_never_echoes_the_connection_string(monkeypatch, capsys) -> None:
    secret = "postgresql://user:hunter2@host/db"

    async def boom(dsn: str):
        raise RuntimeError(f"cannot connect to {dsn}")

    monkeypatch.setattr(recovery, "run", boom)
    assert recovery.main([], stdin=io.StringIO(secret)) == 1
    out = capsys.readouterr().out
    assert "hunter2" not in out and "operation_failed" in out
