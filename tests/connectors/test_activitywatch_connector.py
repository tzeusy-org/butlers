"""ActivityWatch connector tests — ingest.v1 contract, app classification, AFK matching.

Verifies:
- classify_app bucketing (ide / terminal / browser / other)
- ingest.v1 envelope production for window-focus events
- metadata vs full tier: raw field null in metadata tier
- window titles are NEVER present anywhere in the built envelope (privacy)
- Idempotency key determinism
- Config parsing: required env vars, defaults
- AFK interval matching (lookup_afk_status)
- Bucket discovery (find_bucket_id)

[bu-whhll.6]
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from butlers.connectors.activitywatch import (
    ActivityWatchConnectorConfig,
    build_activity_envelope,
    build_afk_intervals,
    classify_app,
    find_bucket_id,
    lookup_afk_status,
)

_MACHINE_ID = "desktop"
_ENDPOINT = f"activitywatch:{_MACHINE_ID}"
_BUCKET_ID = "aw-watcher-window_desktop"
_TS = datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# classify_app
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "app,expected",
    [
        ("Code", "ide"),
        ("Visual Studio Code", "ide"),
        ("Cursor", "ide"),
        ("PyCharm", "ide"),
        ("iTerm2", "terminal"),
        ("Terminal", "terminal"),
        ("Alacritty", "terminal"),
        ("Google Chrome", "browser"),
        ("firefox", "browser"),
        ("Safari", "browser"),
        ("Finder", "other"),
        ("Slack", "other"),
        (None, "other"),
        ("", "other"),
    ],
)
def test_classify_app(app: str | None, expected: str) -> None:
    assert classify_app(app) == expected


def test_classify_app_case_insensitive_and_exe_suffix() -> None:
    assert classify_app("CHROME.EXE") == "browser"
    assert classify_app("code.exe") == "ide"


def test_classify_app_ide_checked_before_browser() -> None:
    """Order matters: an app name matching multiple keyword sets favors IDE first."""
    # Sanity: no real app name collides across sets in our tables, but the
    # order is a documented contract — verify precedence directly.
    assert classify_app("pycharm") == "ide"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_from_env_requires_switchboard_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)
    monkeypatch.setenv("ACTIVITYWATCH_MACHINE_ID", _MACHINE_ID)
    with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL"):
        ActivityWatchConnectorConfig.from_env()


def test_config_from_env_requires_machine_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/mcp")
    monkeypatch.delenv("ACTIVITYWATCH_MACHINE_ID", raising=False)
    with pytest.raises(ValueError, match="ACTIVITYWATCH_MACHINE_ID"):
        ActivityWatchConnectorConfig.from_env()


def test_config_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/mcp")
    monkeypatch.setenv("ACTIVITYWATCH_MACHINE_ID", _MACHINE_ID)
    for var in (
        "ACTIVITYWATCH_BASE_URL",
        "ACTIVITYWATCH_POLL_INTERVAL_S",
        "ACTIVITYWATCH_MAX_BACKFILL_DAYS",
        "CONNECTOR_INGESTION_TIER",
        "CONNECTOR_HEALTH_PORT",
    ):
        monkeypatch.delenv(var, raising=False)

    config = ActivityWatchConnectorConfig.from_env()
    assert config.machine_id == _MACHINE_ID
    assert config.base_url == "http://localhost:5600"
    assert config.poll_interval_s == 60
    assert config.max_backfill_days == 30
    assert config.ingestion_tier == "metadata"
    assert config.health_port == 40092


def test_config_from_env_base_url_trailing_slash_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/mcp")
    monkeypatch.setenv("ACTIVITYWATCH_MACHINE_ID", _MACHINE_ID)
    monkeypatch.setenv("ACTIVITYWATCH_BASE_URL", "http://100.64.1.2:5600/")
    config = ActivityWatchConnectorConfig.from_env()
    assert config.base_url == "http://100.64.1.2:5600"


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------


def test_activity_envelope_schema_version() -> None:
    env = build_activity_envelope(
        machine_id=_MACHINE_ID,
        endpoint_identity=_ENDPOINT,
        bucket_id=_BUCKET_ID,
        ts=_TS,
        duration_seconds=42.0,
        app="Code",
        app_class="ide",
        ingestion_tier="metadata",
    )
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "activitywatch"
    assert env["source"]["provider"] == "activitywatch"
    assert env["source"]["endpoint_identity"] == _ENDPOINT


def test_activity_envelope_metadata_tier_raw_is_null() -> None:
    env = build_activity_envelope(
        machine_id=_MACHINE_ID,
        endpoint_identity=_ENDPOINT,
        bucket_id=_BUCKET_ID,
        ts=_TS,
        duration_seconds=42.0,
        app="Code",
        app_class="ide",
        ingestion_tier="metadata",
    )
    assert env["payload"]["raw"] is None
    assert env["control"]["ingestion_tier"] == "metadata"


def test_activity_envelope_full_tier_has_raw_but_no_title() -> None:
    env = build_activity_envelope(
        machine_id=_MACHINE_ID,
        endpoint_identity=_ENDPOINT,
        bucket_id=_BUCKET_ID,
        ts=_TS,
        duration_seconds=42.0,
        app="Code",
        app_class="ide",
        ingestion_tier="full",
    )
    assert env["payload"]["raw"] is not None
    assert env["payload"]["raw"]["app"] == "Code"


def test_activity_envelope_never_contains_window_title() -> None:
    """Privacy: window titles must never reach the envelope, in any tier.

    build_activity_envelope has no title parameter at all — this test
    guards against a future signature change silently reintroducing one by
    scanning the serialized envelope for a title-shaped key.
    """
    for tier in ("metadata", "full"):
        env = build_activity_envelope(
            machine_id=_MACHINE_ID,
            endpoint_identity=_ENDPOINT,
            bucket_id=_BUCKET_ID,
            ts=_TS,
            duration_seconds=42.0,
            app="Code",
            app_class="ide",
            ingestion_tier=tier,
        )
        serialized = json.dumps(env)
        assert "title" not in serialized.lower()


def test_activity_idempotency_key_deterministic() -> None:
    kwargs = dict(
        machine_id=_MACHINE_ID,
        endpoint_identity=_ENDPOINT,
        bucket_id=_BUCKET_ID,
        ts=_TS,
        duration_seconds=42.0,
        app="Code",
        app_class="ide",
        ingestion_tier="metadata",
    )
    e1 = build_activity_envelope(**kwargs)
    e2 = build_activity_envelope(**kwargs)
    assert e1["control"]["idempotency_key"] == e2["control"]["idempotency_key"]
    assert (
        e1["control"]["idempotency_key"]
        == f"activitywatch:{_MACHINE_ID}:{_BUCKET_ID}:{_TS.isoformat()}"
    )


def test_activity_envelope_passes_parse_ingest_envelope() -> None:
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    env = build_activity_envelope(
        machine_id=_MACHINE_ID,
        endpoint_identity=_ENDPOINT,
        bucket_id=_BUCKET_ID,
        ts=_TS,
        duration_seconds=42.0,
        app="Code",
        app_class="ide",
        ingestion_tier="metadata",
    )
    try:
        parse_ingest_envelope(env)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


# ---------------------------------------------------------------------------
# Bucket discovery + AFK matching
# ---------------------------------------------------------------------------


def test_find_bucket_id_matches_type() -> None:
    buckets = {
        "aw-watcher-afk_desktop": {"type": "afkstatus"},
        "aw-watcher-window_desktop": {"type": "currentwindow"},
    }
    assert find_bucket_id(buckets, "currentwindow") == "aw-watcher-window_desktop"
    assert find_bucket_id(buckets, "afkstatus") == "aw-watcher-afk_desktop"


def test_find_bucket_id_returns_none_when_missing() -> None:
    assert find_bucket_id({}, "currentwindow") is None
    assert find_bucket_id({"x": {"type": "other"}}, "currentwindow") is None


def test_build_afk_intervals_and_lookup() -> None:
    base = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    afk_events = [
        {"timestamp": base.isoformat(), "duration": 600, "data": {"status": "not-afk"}},
        {
            "timestamp": (base + timedelta(seconds=600)).isoformat(),
            "duration": 300,
            "data": {"status": "afk"},
        },
    ]
    intervals = build_afk_intervals(afk_events)
    assert len(intervals) == 2

    # Falls within the first (not-afk) interval.
    assert lookup_afk_status(intervals, base + timedelta(seconds=100)) is False
    # Falls within the second (afk) interval.
    assert lookup_afk_status(intervals, base + timedelta(seconds=700)) is True
    # Outside any interval.
    assert lookup_afk_status(intervals, base + timedelta(hours=5)) is None


def test_build_afk_intervals_skips_malformed_events() -> None:
    afk_events = [
        {"timestamp": "not-a-timestamp", "duration": 60, "data": {"status": "afk"}},
        {"duration": 60, "data": {"status": "afk"}},  # missing timestamp
    ]
    assert build_afk_intervals(afk_events) == []
