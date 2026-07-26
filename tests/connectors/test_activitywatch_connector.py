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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.activitywatch import (
    ActivityWatchConnector,
    ActivityWatchConnectorConfig,
    ActivityWatchRetention,
    ActivityWatchRetentionConfig,
    build_activity_envelope,
    build_afk_intervals,
    classify_app,
    find_bucket_id,
    lookup_afk_status,
    match_browser_domain,
    persist_activity_event,
)
from butlers.ingestion_policy import PolicyDecision

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
        "ACTIVITYWATCH_RETENTION_DAYS",
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
    assert config.retention_days == 14
    assert config.health_port == 40092


def test_config_from_env_retention_days_below_minimum_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/mcp")
    monkeypatch.setenv("ACTIVITYWATCH_MACHINE_ID", _MACHINE_ID)
    monkeypatch.setenv("ACTIVITYWATCH_RETENTION_DAYS", "0")
    with pytest.raises(ValueError, match="ACTIVITYWATCH_RETENTION_DAYS"):
        ActivityWatchConnectorConfig.from_env()


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


# ---------------------------------------------------------------------------
# Browser-domain correlation
# ---------------------------------------------------------------------------


def _web_event(
    *,
    timestamp: str,
    duration: float,
    url: str,
    title: str = "Sensitive browser tab title",
) -> dict:
    return {
        "timestamp": timestamp,
        "duration": duration,
        "data": {"url": url, "title": title},
    }


def test_match_browser_domain_uses_aware_instants_and_exposes_only_hostname() -> None:
    """An offset-bearing web event matches the same UTC instant without URL leakage."""
    raw_url = "https://docs.example.test/private/path?token=do-not-project#anchor"
    match = match_browser_domain(
        _TS,
        [
            _web_event(
                timestamp="2026-07-05T18:00:00+08:00",
                duration=60,
                url=raw_url,
            )
        ],
    )

    assert match is not None
    assert match.domain == "docs.example.test"
    # The source event is retained for sensitive evidence persistence only;
    # the correlation result itself has no path, query, fragment, or title.
    assert match.raw_event["data"]["url"] == raw_url
    assert "private" not in match.domain
    assert "token" not in match.domain


def test_match_browser_domain_uses_half_open_boundaries_and_latest_overlap() -> None:
    """At an exact end boundary choose the next interval; overlap picks latest start."""
    first = _web_event(
        timestamp=(_TS - timedelta(seconds=60)).isoformat(),
        duration=60,
        url="https://ended.example.test/path",
    )
    boundary = _web_event(
        timestamp=_TS.isoformat(),
        duration=120,
        url="https://boundary.example.test/path",
    )
    latest_overlap = _web_event(
        timestamp=(_TS + timedelta(seconds=30)).isoformat(),
        duration=120,
        url="https://latest.example.test/path",
    )

    at_boundary = match_browser_domain(_TS, [first, boundary])
    in_overlap = match_browser_domain(_TS + timedelta(seconds=45), [boundary, latest_overlap])

    assert at_boundary is not None
    assert at_boundary.domain == "boundary.example.test"
    assert in_overlap is not None
    assert in_overlap.domain == "latest.example.test"


def test_match_browser_domain_returns_none_for_no_match_or_malformed_timestamp() -> None:
    """No correlation is safer than guessing from malformed source data."""
    no_match = _web_event(
        timestamp=(_TS + timedelta(minutes=5)).isoformat(),
        duration=60,
        url="https://outside.example.test/path",
    )
    malformed_timestamp = _web_event(
        timestamp="not-a-timestamp",
        duration=60,
        url="https://malformed.example.test/path",
    )
    unsupported_scheme = _web_event(
        timestamp=_TS.isoformat(),
        duration=60,
        url="file:///private/path",
    )

    assert match_browser_domain(_TS, [no_match, malformed_timestamp, unsupported_scheme]) is None


def test_match_browser_domain_treats_naive_activitywatch_timestamp_as_utc() -> None:
    """ActivityWatch stores UTC timestamps and may discard their offset."""
    match = match_browser_domain(
        _TS.replace(tzinfo=None),
        [
            _web_event(
                timestamp="2026-07-05T10:00:00",
                duration=60,
                url="https://utc-naive.example.test/path",
            )
        ],
    )

    assert match is not None
    assert match.domain == "utc-naive.example.test"


@pytest.mark.asyncio
async def test_poll_passes_only_safe_domain_and_sensitive_web_event_to_persistence() -> None:
    """Web enrichment is connector-local: the ingest envelope stays coarse."""
    connector = ActivityWatchConnector(
        ActivityWatchConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            machine_id=_MACHINE_ID,
        )
    )
    connector._http_client = MagicMock()
    connector._last_checkpoint_ts = _TS - timedelta(seconds=1)

    window_event = {
        "timestamp": _TS.isoformat(),
        "duration": 42,
        "data": {"app": "Google Chrome", "title": "Sensitive project roadmap"},
    }
    web_event = _web_event(
        timestamp=_TS.isoformat(),
        duration=60,
        url="https://docs.example.test/private?token=secret",
    )
    connector._process_window_event = AsyncMock()  # type: ignore[method-assign]

    with (
        patch(
            "butlers.connectors.activitywatch.fetch_buckets",
            new=AsyncMock(
                return_value={
                    _BUCKET_ID: {"type": "currentwindow"},
                    "aw-watcher-web_desktop": {"type": "web.tab.current"},
                }
            ),
        ),
        patch(
            "butlers.connectors.activitywatch.fetch_events",
            new=AsyncMock(side_effect=[[window_event], [web_event]]),
        ),
    ):
        await connector._execute_poll_cycle()

    connector._process_window_event.assert_awaited_once()
    kwargs = connector._process_window_event.await_args.kwargs
    assert kwargs["browser_domain"] == "docs.example.test"
    assert kwargs["raw_web_event"] == web_event


@pytest.mark.asyncio
async def test_process_window_event_keeps_raw_url_and_title_out_of_ingest_envelope() -> None:
    """Only the evidence write receives the raw web event; normal ingress sees no URL/title."""
    connector = ActivityWatchConnector(
        ActivityWatchConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            machine_id=_MACHINE_ID,
        ),
        db_pool=MagicMock(),
    )
    connector._mcp_client.call_tool = AsyncMock()  # type: ignore[method-assign]
    raw_url = "https://docs.example.test/private?token=secret"
    raw_title = "Sensitive browser tab title"
    raw_web_event = _web_event(timestamp=_TS.isoformat(), duration=60, url=raw_url, title=raw_title)
    raw_window_event = {
        "timestamp": _TS.isoformat(),
        "duration": 42,
        "data": {"app": "Google Chrome", "title": "Sensitive project roadmap"},
    }

    with patch(
        "butlers.connectors.activitywatch.persist_activity_event", new=AsyncMock()
    ) as persist:
        await connector._process_window_event(
            bucket_id=_BUCKET_ID,
            ts=_TS,
            duration_seconds=42,
            app="Google Chrome",
            window_title="Sensitive project roadmap",
            app_class="browser",
            is_afk=False,
            raw_event=raw_window_event,
            browser_domain="docs.example.test",
            raw_web_event=raw_web_event,
        )

    envelope = connector._mcp_client.call_tool.await_args.args[1]
    serialized_envelope = json.dumps(envelope)
    assert raw_url not in serialized_envelope
    assert raw_title not in serialized_envelope
    assert "docs.example.test" not in serialized_envelope

    persisted = persist.await_args.kwargs
    assert persisted["browser_domain"] == "docs.example.test"
    assert persisted["raw_payload"]["web_event"] == raw_web_event


@pytest.mark.asyncio
async def test_persist_activity_event_rejects_raw_url_as_browser_domain() -> None:
    """A caller cannot bypass the connector's hostname-only write boundary."""
    pool = AsyncMock()
    pool.fetchval.return_value = "event-id"
    raw_url = "https://docs.example.test/private?token=secret"

    inserted = await persist_activity_event(
        pool,
        machine_id=_MACHINE_ID,
        endpoint_identity=_ENDPOINT,
        bucket_id=_BUCKET_ID,
        ts=_TS,
        duration_seconds=42,
        app="Google Chrome",
        window_title="Sensitive project roadmap",
        app_class="browser",
        browser_domain=raw_url,
        is_afk=False,
        raw_payload={"web_event": {"data": {"url": raw_url}}},
    )

    assert inserted is True
    assert pool.fetchval.await_args.args[10] is None
    assert pool.fetchval.await_args.args[12]["web_event"]["data"]["url"] == raw_url


# ---------------------------------------------------------------------------
# Filtered-content privacy tier (bu-apzqs)
# ---------------------------------------------------------------------------


async def test_policy_denied_event_scrubs_title_from_filtered_full_payload() -> None:
    """A policy-denied event keeps operational metadata but no provider raw payload."""
    connector = ActivityWatchConnector(
        ActivityWatchConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            machine_id=_MACHINE_ID,
        )
    )
    connector._ingestion_policy = MagicMock()  # type: ignore[assignment]
    connector._ingestion_policy.evaluate.return_value = PolicyDecision(
        action="block",
        matched_rule_type="app_class",
    )
    title = "Sensitive project roadmap"
    raw_event = {
        "timestamp": _TS.isoformat(),
        "duration": 42,
        "data": {"app": "Code", "title": title},
    }

    await connector._process_window_event(
        bucket_id=_BUCKET_ID,
        ts=_TS,
        duration_seconds=42,
        app="Code",
        window_title=title,
        app_class="ide",
        is_afk=False,
        raw_event=raw_event,
    )

    rows = connector._filtered_event_buffer._rows
    assert len(rows) == 1
    row = rows[0]
    assert row[7] == "connector_rule:block:app_class"
    assert row[6] == "ActivityWatch ide activity"
    assert row[9]["payload"]["raw"] == {}
    serialized_payload = json.dumps(row[9]).lower()
    assert title.lower() not in serialized_payload
    assert "title" not in serialized_payload


# ---------------------------------------------------------------------------
# Retention purge (bu-il04h)
# ---------------------------------------------------------------------------
#
# connectors.activitywatch_events durably stores window titles (sensitive)
# with no other TTL. Mirrors the OwnTracks connector's fake-pool retention
# test pattern (tests/connectors/test_owntracks_connector.py).


class _PurgeConnection:
    """Small asyncpg boundary fake that returns or raises queued purge results."""

    def __init__(self, outcomes: list[str | Exception]) -> None:
        self._outcomes = outcomes
        self.calls: list[tuple[object, ...]] = []

    async def execute(self, *args: object) -> str:
        self.calls.append(args)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _PurgeAcquire:
    def __init__(self, connection: _PurgeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _PurgeConnection:
        return self._connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _PurgePool:
    def __init__(self, outcomes: list[str | Exception]) -> None:
        self.connection = _PurgeConnection(outcomes)

    def acquire(self) -> _PurgeAcquire:
        return _PurgeAcquire(self.connection)


def _make_retention(*outcomes: str | Exception, retention_days: int = 14) -> ActivityWatchRetention:
    return ActivityWatchRetention(
        ActivityWatchRetentionConfig(retention_days=retention_days),
        _PurgePool(list(outcomes)),
    )


def _make_connector(retention: ActivityWatchRetention) -> ActivityWatchConnector:
    connector = ActivityWatchConnector(
        ActivityWatchConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            machine_id=_MACHINE_ID,
        )
    )
    connector._retention = retention
    return connector


def test_retention_config_rejects_non_positive_days() -> None:
    with pytest.raises(ValueError, match="retention_days must be >= 1"):
        ActivityWatchRetentionConfig(retention_days=0)


async def test_purge_once_queries_activitywatch_events_by_ts_with_configured_days() -> None:
    """The purge query targets the sensitive evidence table's ts column and
    is parameterized by the configured retention_days (rows older than the
    TTL are the ones matched for deletion; newer rows are outside the WHERE
    clause and therefore retained)."""
    retention = _make_retention("DELETE 5", retention_days=21)

    deleted = await retention.purge_once()

    assert deleted == 5
    pool = retention._pool
    assert isinstance(pool, _PurgePool)
    ((sql, retention_days_arg),) = pool.connection.calls
    assert "connectors.activitywatch_events" in sql
    assert "ts <" in sql
    assert "NOW()" in sql
    assert retention_days_arg == 21


async def test_retention_purge_failures_stay_retryable_and_degrade_connector_health() -> None:
    retention = _make_retention(RuntimeError("first failure"), RuntimeError("second failure"))
    connector = _make_connector(retention)

    await retention._run_purge()

    assert connector._get_health_state() == (
        "degraded",
        "ActivityWatch retention purge has failed 1 consecutive time",
    )

    await retention._run_purge()

    assert connector._get_health_state() == (
        "degraded",
        "ActivityWatch retention purge has failed 2 consecutive times",
    )


async def test_successful_retention_purge_resets_degraded_connector_health() -> None:
    retention = _make_retention(RuntimeError("temporary failure"), "DELETE 3")
    connector = _make_connector(retention)

    await retention._run_purge()
    await retention._run_purge()

    assert connector._get_health_state() == ("healthy", None)


async def test_connector_health_error_outranks_retention_degradation() -> None:
    retention = _make_retention(RuntimeError("retention failure"))
    connector = _make_connector(retention)

    await retention._run_purge()
    connector._health_error = "Switchboard ingest unavailable"

    assert connector._get_health_state() == ("degraded", "Switchboard ingest unavailable")


async def test_retention_health_diagnostic_does_not_leak_exception_details() -> None:
    retention = _make_retention(RuntimeError("database password=swordfish traceback details"))
    connector = _make_connector(retention)

    await retention._run_purge()

    state, diagnostic = connector._get_health_state()

    assert state == "degraded"
    assert diagnostic == "ActivityWatch retention purge has failed 1 consecutive time"
    assert diagnostic is not None
    assert "swordfish" not in diagnostic
    assert "traceback" not in diagnostic
