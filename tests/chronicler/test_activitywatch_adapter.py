"""Tests for the ActivityWatch window-focus Chronicler projection adapter.

Covers:
- Per-point event projection correctness (one app_focus event per active row).
- AFK rows (is_afk=True) excluded from point events and episode duration.
- is_afk=NULL rows (no AFK watcher installed) treated as active.
- Screen episode rollup: contiguous active rows collapse; gap starts a new episode.
- Per-app-class duration breakdown + dominant_app_class in episode payload.
- Privacy: point event / episode payloads never carry raw app name, window title,
  or raw browser URL; a validated browser hostname is the only browser detail.
- Malformed app_class handling (defaults to "other" with a warning).
- Missing evidence surface graceful degradation.
- Carryover continuation logic (``_resolve_carryover``).
- Source-scan guardrail: no LLM imports in adapters/activitywatch.py.

[bu-whhll.6]
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.adapters.activitywatch import (
    EPISODE_TYPE_SCREEN,
    EVENT_TYPE_APP_FOCUS,
    SCREEN_GAP_MINUTES,
    SOURCE_NAME,
    ActivityWatchWindowAdapter,
)
from butlers.chronicler.models import PointEvent, Precision, Privacy

_NOW = datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC)
_ENDPOINT = "activitywatch:desktop"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    ts: datetime = _NOW,
    duration_seconds: float = 30.0,
    app_class: str = "ide",
    is_afk: bool | None = False,
    endpoint_identity: str = _ENDPOINT,
    idempotency_key: str | None = None,
    browser_domain: str | None = None,
) -> dict:
    ikey = idempotency_key or f"activitywatch:{endpoint_identity}:bucket:{ts.isoformat()}"
    return {
        "id": "some-uuid",
        "idempotency_key": ikey,
        "ts": ts,
        "duration_seconds": duration_seconds,
        "app_class": app_class,
        "is_afk": is_afk,
        "endpoint_identity": endpoint_identity,
        "browser_domain": browser_domain,
    }


def _make_mock_row(r: dict) -> MagicMock:
    return MagicMock(**r, **{"__getitem__": lambda s, k, _r=r: _r[k]})


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _pool_returning(*rows: dict) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table-exists check
    conn.fetch = AsyncMock(return_value=[_make_mock_row(r) for r in rows])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _pool_table_missing() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _chronicler_pool() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


# ---------------------------------------------------------------------------
# Source-scan guardrail
# ---------------------------------------------------------------------------


def test_no_llm_imports_in_activitywatch_adapter() -> None:
    import butlers.chronicler.adapters.activitywatch as mod

    source_path = mod.__file__
    assert source_path is not None

    with open(source_path) as fh:
        tree = ast.parse(fh.read(), filename=source_path)

    forbidden_prefixes = ("anthropic", "openai", "langchain", "litellm", "llm")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden_prefixes:
                    assert not alias.name.startswith(prefix)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for prefix in forbidden_prefixes:
                    assert not node.module.startswith(prefix)


# ---------------------------------------------------------------------------
# Per-point event projection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_single_active_row_produces_one_point_event() -> None:
    row = _make_row()
    adapter = ActivityWatchWindowAdapter()

    upserted_events: list[PointEvent] = []

    async def _fake_upsert_event(conn: object, event: PointEvent) -> PointEvent:
        upserted_events.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch(
            "butlers.chronicler.adapters.activitywatch.upsert_point_event",
            side_effect=_fake_upsert_event,
        ),
        patch("butlers.chronicler.adapters.activitywatch.upsert_episode") as mock_ep,
    ):
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.point_events == 1
    assert len(upserted_events) == 1
    ev = upserted_events[0]
    assert ev.source_name == SOURCE_NAME
    assert ev.event_type == EVENT_TYPE_APP_FOCUS
    assert ev.occurred_at == _NOW
    assert ev.precision == Precision.EXACT
    assert ev.privacy == Privacy.NORMAL
    assert ev.payload == {"app_class": "ide", "duration_seconds": 30.0}


@pytest.mark.asyncio
async def test_afk_row_excluded_from_point_events_but_advances_watermark() -> None:
    row = _make_row(is_afk=True)
    adapter = ActivityWatchWindowAdapter()
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.activitywatch.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.activitywatch.upsert_episode") as mock_ep,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    assert result.point_events == 0
    assert result.episodes_closed == 0
    assert result.watermark == _NOW
    mock_pe.assert_not_called()
    mock_ep.assert_not_called()


@pytest.mark.asyncio
async def test_null_afk_row_treated_as_active() -> None:
    """No AFK bucket on this machine (is_afk=NULL) — must not silently drop activity."""
    row = _make_row(is_afk=None)
    adapter = ActivityWatchWindowAdapter()
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.activitywatch.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.activitywatch.upsert_episode") as mock_ep,
    ):
        mock_pe.side_effect = AsyncMock(return_value=MagicMock())
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.point_events == 1


@pytest.mark.asyncio
async def test_point_event_payload_never_contains_app_name_or_title() -> None:
    """Privacy: only app_class + duration reach the point event payload."""
    row = _make_row()
    adapter = ActivityWatchWindowAdapter()
    upserted_events: list[PointEvent] = []

    async def _fake_upsert_event(conn: object, event: PointEvent) -> PointEvent:
        upserted_events.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch(
            "butlers.chronicler.adapters.activitywatch.upsert_point_event",
            side_effect=_fake_upsert_event,
        ),
        patch("butlers.chronicler.adapters.activitywatch.upsert_episode") as mock_ep,
    ):
        mock_ep.return_value = MagicMock()
        await adapter.project(pool, chronicler_pool=cp, since=None)

    ev = upserted_events[0]
    assert set(ev.payload.keys()) == {"app_class", "duration_seconds"}


@pytest.mark.asyncio
async def test_browser_point_event_projects_validated_hostname_only() -> None:
    row = _make_row(app_class="browser", browser_domain="docs.example.test")
    adapter = ActivityWatchWindowAdapter()
    upserted_events: list[PointEvent] = []

    async def _fake_upsert_event(conn: object, event: PointEvent) -> PointEvent:
        upserted_events.append(event)
        return event

    with patch(
        "butlers.chronicler.adapters.activitywatch.upsert_point_event",
        side_effect=_fake_upsert_event,
    ):
        await adapter._project_point_event(_chronicler_pool(), row)

    event = upserted_events[0]
    assert event.payload == {
        "app_class": "browser",
        "duration_seconds": 30.0,
        "browser_domain": "docs.example.test",
    }
    assert "docs.example.test" in event.title


@pytest.mark.asyncio
async def test_browser_point_event_rejects_raw_url_shaped_domain() -> None:
    """Projection fails closed if an evidence row contains a raw URL by mistake."""
    raw_url = "https://docs.example.test/private?token=secret"
    row = _make_row(app_class="browser", browser_domain=raw_url)
    adapter = ActivityWatchWindowAdapter()
    upserted_events: list[PointEvent] = []

    async def _fake_upsert_event(conn: object, event: PointEvent) -> PointEvent:
        upserted_events.append(event)
        return event

    with patch(
        "butlers.chronicler.adapters.activitywatch.upsert_point_event",
        side_effect=_fake_upsert_event,
    ):
        await adapter._project_point_event(_chronicler_pool(), row)

    event = upserted_events[0]
    serialized = str({"title": event.title, "payload": event.payload})
    assert event.payload == {"app_class": "browser", "duration_seconds": 30.0}
    assert raw_url not in serialized
    assert "token=secret" not in serialized


@pytest.mark.asyncio
async def test_malformed_app_class_defaults_to_other_with_warning() -> None:
    row = _make_row(app_class="not-a-real-class")
    adapter = ActivityWatchWindowAdapter()
    upserted_events: list[PointEvent] = []

    async def _fake_upsert_event(conn: object, event: PointEvent) -> PointEvent:
        upserted_events.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch(
            "butlers.chronicler.adapters.activitywatch.upsert_point_event",
            side_effect=_fake_upsert_event,
        ),
        patch("butlers.chronicler.adapters.activitywatch.upsert_episode") as mock_ep,
    ):
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert upserted_events[0].payload["app_class"] == "other"
    assert len(result.warnings) == 1
    assert "unrecognized app_class" in result.warnings[0]


@pytest.mark.asyncio
async def test_missing_evidence_table_skips_gracefully() -> None:
    adapter = ActivityWatchWindowAdapter()
    pool = _pool_table_missing()
    cp = _chronicler_pool()

    result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert "not found" in (result.skipped_reason or "")


# ---------------------------------------------------------------------------
# Screen episode rollup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_active_row_produces_one_screen_episode() -> None:
    row = _make_row()
    adapter = ActivityWatchWindowAdapter()
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.activitywatch.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.activitywatch.upsert_episode") as mock_ep,
    ):
        mock_pe.side_effect = AsyncMock(return_value=MagicMock())
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    assert mock_ep.await_count == 1
    episode_arg = mock_ep.await_args.args[1]
    assert episode_arg.episode_type == EPISODE_TYPE_SCREEN
    assert episode_arg.payload["dominant_app_class"] == "ide"
    assert episode_arg.payload["ide_seconds"] == 30.0
    assert episode_arg.payload["point_count"] == 1


@pytest.mark.asyncio
async def test_contiguous_rows_collapse_into_one_episode_with_breakdown() -> None:
    row1 = _make_row(ts=_NOW, app_class="ide", duration_seconds=60.0, idempotency_key="k1")
    row2 = _make_row(
        ts=_NOW + timedelta(minutes=2),
        app_class="browser",
        duration_seconds=120.0,
        idempotency_key="k2",
    )
    adapter = ActivityWatchWindowAdapter()
    pool = _pool_returning(row1, row2)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.activitywatch.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.activitywatch.upsert_episode") as mock_ep,
    ):
        mock_pe.side_effect = AsyncMock(return_value=MagicMock())
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    episode_arg = mock_ep.await_args.args[1]
    assert episode_arg.payload["ide_seconds"] == 60.0
    assert episode_arg.payload["browser_seconds"] == 120.0
    assert episode_arg.payload["dominant_app_class"] == "browser"
    assert episode_arg.payload["point_count"] == 2


@pytest.mark.asyncio
async def test_browser_domain_seconds_are_aggregated_in_screen_episode() -> None:
    row1 = _make_row(
        ts=_NOW,
        app_class="browser",
        browser_domain="docs.example.test",
        duration_seconds=60.0,
        idempotency_key="k1",
    )
    row2 = _make_row(
        ts=_NOW + timedelta(minutes=2),
        app_class="browser",
        browser_domain="search.example.test",
        duration_seconds=30.0,
        idempotency_key="k2",
    )
    row3 = _make_row(
        ts=_NOW + timedelta(minutes=3),
        app_class="browser",
        browser_domain="docs.example.test",
        duration_seconds=45.0,
        idempotency_key="k3",
    )
    adapter = ActivityWatchWindowAdapter()
    pool = _pool_returning(row1, row2, row3)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.activitywatch.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.activitywatch.upsert_episode") as mock_ep,
    ):
        mock_pe.side_effect = AsyncMock(return_value=MagicMock())
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    episode_arg = mock_ep.await_args.args[1]
    assert episode_arg.payload["browser_domain_seconds"] == {
        "docs.example.test": 105.0,
        "search.example.test": 30.0,
    }


@pytest.mark.asyncio
async def test_fetch_events_reads_safe_domain_without_selecting_sensitive_evidence() -> None:
    adapter = ActivityWatchWindowAdapter()
    pool = _pool_returning()

    await adapter._fetch_events(pool, since=None)

    sql = str(pool.acquire.return_value._obj.fetch.await_args.args[0])
    assert "browser_domain" in sql
    assert "window_title" not in sql
    assert "raw_payload" not in sql


@pytest.mark.asyncio
async def test_gap_beyond_threshold_starts_new_episode() -> None:
    row1 = _make_row(ts=_NOW, idempotency_key="k1")
    row2 = _make_row(
        ts=_NOW + timedelta(minutes=SCREEN_GAP_MINUTES + 5),
        idempotency_key="k2",
    )
    adapter = ActivityWatchWindowAdapter()
    pool = _pool_returning(row1, row2)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.activitywatch.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.activitywatch.upsert_episode") as mock_ep,
    ):
        mock_pe.side_effect = AsyncMock(return_value=MagicMock())
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 2


@pytest.mark.asyncio
async def test_gap_measured_from_prior_row_end_not_start() -> None:
    """A long-duration row must not fragment the episode on its own duration.

    row1 is a 1-hour continuous session (duration_seconds=3600). row2 starts
    61 minutes after row1's *start* -- but only 60 seconds after row1's true
    *end* (ts + duration_seconds). Since 60s is well within
    SCREEN_GAP_MINUTES, this must collapse into a single episode. Gating the
    gap on start-to-start instead of end-to-start would incorrectly split
    this into two episodes.
    """
    row1 = _make_row(ts=_NOW, app_class="ide", duration_seconds=3600.0, idempotency_key="k1")
    row2 = _make_row(
        ts=_NOW + timedelta(minutes=61),
        app_class="ide",
        duration_seconds=30.0,
        idempotency_key="k2",
    )
    adapter = ActivityWatchWindowAdapter()
    pool = _pool_returning(row1, row2)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.activitywatch.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.activitywatch.upsert_episode") as mock_ep,
    ):
        mock_pe.side_effect = AsyncMock(return_value=MagicMock())
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    episode_arg = mock_ep.await_args.args[1]
    assert episode_arg.payload["ide_seconds"] == 3630.0


# ---------------------------------------------------------------------------
# Carryover continuation
# ---------------------------------------------------------------------------


def test_resolve_carryover_continues_within_gap() -> None:
    prior_start = _NOW - timedelta(minutes=5)
    prior_end = _NOW - timedelta(minutes=1)
    carry = {
        "start_at": prior_start.isoformat(),
        "end_at": prior_end.isoformat(),
        "class_seconds": {"ide": 100.0},
    }
    start_at, class_seconds = ActivityWatchWindowAdapter._resolve_carryover(
        carry=carry, row_ts=_NOW, gap=timedelta(minutes=SCREEN_GAP_MINUTES)
    )
    assert start_at == prior_start
    assert class_seconds == {"ide": 100.0}


def test_resolve_carryover_rejects_gap_exceeded() -> None:
    prior_start = _NOW - timedelta(hours=2)
    prior_end = _NOW - timedelta(hours=1)
    carry = {"start_at": prior_start.isoformat(), "end_at": prior_end.isoformat()}
    start_at, class_seconds = ActivityWatchWindowAdapter._resolve_carryover(
        carry=carry, row_ts=_NOW, gap=timedelta(minutes=SCREEN_GAP_MINUTES)
    )
    assert start_at is None
    assert class_seconds == {}


def test_resolve_carryover_rejects_naive_timestamps() -> None:
    carry = {"start_at": "2026-07-05T09:00:00", "end_at": "2026-07-05T09:05:00"}
    start_at, class_seconds = ActivityWatchWindowAdapter._resolve_carryover(
        carry=carry, row_ts=_NOW, gap=timedelta(minutes=SCREEN_GAP_MINUTES)
    )
    assert start_at is None
    assert class_seconds == {}


def test_resolve_carryover_rejects_malformed_dict() -> None:
    start_at, class_seconds = ActivityWatchWindowAdapter._resolve_carryover(
        carry={"start_at": "not-a-date", "end_at": "also-not"},
        row_ts=_NOW,
        gap=timedelta(minutes=SCREEN_GAP_MINUTES),
    )
    assert start_at is None
    assert class_seconds == {}

    start_at, class_seconds = ActivityWatchWindowAdapter._resolve_carryover(
        carry="not-a-dict", row_ts=_NOW, gap=timedelta(minutes=SCREEN_GAP_MINUTES)
    )
    assert start_at is None
    assert class_seconds == {}


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_screen_gap_minutes_must_be_positive() -> None:
    with pytest.raises(ValueError, match="screen_gap_minutes"):
        ActivityWatchWindowAdapter(screen_gap_minutes=0)
