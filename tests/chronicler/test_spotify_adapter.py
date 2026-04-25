"""Tests for the Spotify session-summary Chronicler projection adapter.

Covers:
- Per-episode projection correctness (one listening episode per session row).
- Replay / idempotency (same source_ref on repeated runs).
- Missing evidence surface graceful degradation.
- Checkpoint advance / resume (watermark advances by started_at).
- Source-scan guardrail: no LLM imports in adapters/spotify.py.
- Deferred-tracks verification: per-track events NOT produced.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.chronicler.adapters.spotify import (
    EPISODE_TYPE_LISTENING,
    SOURCE_NAME,
    SpotifySessionAdapter,
)
from butlers.chronicler.models import Episode, Precision, Privacy

_NOW = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)
_ENDPOINT = "spotify_user_client:spotify:user123"
_SPOTIFY_USER_ID = "user123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    started_at: datetime = _NOW,
    ended_at: datetime | None = None,
    idempotency_key: str = "spotify:ep:session:1711447200000",
    endpoint_identity: str = _ENDPOINT,
    spotify_user_id: str = _SPOTIFY_USER_ID,
    track_count: int = 5,
    duration_seconds: int = 1800,
    context_uri: str | None = "spotify:playlist:abc",
    context_name: str | None = "Deep Focus",
) -> dict:
    return {
        "id": "some-uuid",
        "idempotency_key": idempotency_key,
        "endpoint_identity": endpoint_identity,
        "spotify_user_id": spotify_user_id,
        "started_at": started_at,
        "ended_at": ended_at or (started_at + timedelta(minutes=30)),
        "duration_seconds": duration_seconds,
        "track_count": track_count,
        "track_names": ["Song A", "Song B"],
        "context_uri": context_uri,
        "context_name": context_name,
        "recorded_at": started_at,
    }


def _pool_returning(*rows: dict) -> AsyncMock:
    """Build a mock asyncpg pool that returns the given row dicts for fetch()."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table-exists check
    conn.fetch = AsyncMock(
        return_value=[MagicMock(**r, **{"__getitem__": lambda s, k: r[k]}) for r in rows]
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _pool_table_missing() -> AsyncMock:
    """Build a pool whose table-existence check returns False."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


class _AsyncCtx:
    """Async context manager that yields ``obj``."""

    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _chronicler_pool() -> AsyncMock:
    """Build a minimal mock chronicler pool for upsert_episode calls."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_NullCtx())
    conn.fetchrow = AsyncMock(return_value=None)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


class _NullCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Source-scan guardrail: no LLM imports in adapters/spotify.py
# ---------------------------------------------------------------------------


def test_no_llm_imports_in_spotify_adapter() -> None:
    """The spotify adapter module must not import any LLM client packages.

    Parses the source AST rather than inspecting the live module so that
    transitive imports through other modules don't cause false negatives.
    """
    import butlers.chronicler.adapters.spotify as mod

    source_path = mod.__file__
    assert source_path is not None

    with open(source_path) as fh:
        tree = ast.parse(fh.read(), filename=source_path)

    forbidden_prefixes = ("anthropic", "openai", "langchain", "litellm", "llm")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden_prefixes:
                    assert not alias.name.startswith(prefix), (
                        f"LLM import detected in spotify adapter: {alias.name!r}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for prefix in forbidden_prefixes:
                    assert not node.module.startswith(prefix), (
                        f"LLM import detected in spotify adapter: {node.module!r}"
                    )


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_source_name() -> None:
    assert SOURCE_NAME == "spotify.session_summary"


def test_episode_type() -> None:
    assert EPISODE_TYPE_LISTENING == "listening_episode"


# ---------------------------------------------------------------------------
# Per-episode projection correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_single_row_produces_one_episode() -> None:
    row = _make_row()
    adapter = SpotifySessionAdapter()

    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.episodes_closed == 1
    assert len(upserted) == 1


@pytest.mark.asyncio
async def test_episode_fields_from_row() -> None:
    row = _make_row(
        started_at=_NOW,
        ended_at=_NOW + timedelta(minutes=45),
        context_name="Deep Focus",
        context_uri="spotify:playlist:abc",
    )
    adapter = SpotifySessionAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    ep = upserted[0]
    assert ep.source_name == SOURCE_NAME
    assert ep.episode_type == EPISODE_TYPE_LISTENING
    assert ep.start_at == _NOW
    assert ep.end_at == _NOW + timedelta(minutes=45)
    assert ep.precision == Precision.EXACT
    assert ep.privacy == Privacy.SENSITIVE
    assert "Deep Focus" in ep.title
    assert ep.payload["endpoint_identity"] == _ENDPOINT
    assert ep.payload["spotify_user_id"] == _SPOTIFY_USER_ID
    assert ep.payload["track_count"] == 5


@pytest.mark.asyncio
async def test_episode_title_falls_back_to_context_uri_fragment() -> None:
    row = _make_row(context_name=None, context_uri="spotify:playlist:myplaylist")
    adapter = SpotifySessionAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert "myplaylist" in upserted[0].title


@pytest.mark.asyncio
async def test_episode_title_falls_back_to_endpoint_when_no_context() -> None:
    row = _make_row(context_name=None, context_uri=None)
    adapter = SpotifySessionAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert _ENDPOINT in upserted[0].title


# ---------------------------------------------------------------------------
# Stable source_ref / idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_ref_uses_idempotency_key() -> None:
    ikey = "spotify:ep:session:1711447200000"
    row = _make_row(idempotency_key=ikey)
    adapter = SpotifySessionAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    expected_ref = f"connectors.spotify_listening_sessions:{ikey}"
    assert upserted[0].source_ref == expected_ref


@pytest.mark.asyncio
async def test_same_row_produces_same_source_ref_on_replay() -> None:
    ikey = "spotify:ep:session:111"
    row = _make_row(idempotency_key=ikey)
    adapter = SpotifySessionAdapter()
    refs: list[str] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        refs.append(episode.source_ref)
        return episode

    pool1 = _pool_returning(row)
    cp1 = _chronicler_pool()
    pool2 = _pool_returning(row)
    cp2 = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool1, chronicler_pool=cp1, since=None)
        await adapter.project(pool2, chronicler_pool=cp2, since=None)

    assert refs[0] == refs[1]


# ---------------------------------------------------------------------------
# Missing evidence surface graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_evidence_table_returns_skipped_result() -> None:
    adapter = SpotifySessionAdapter()
    pool = _pool_table_missing()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.skipped_reason is not None
    assert "not found" in result.skipped_reason
    assert result.rows_projected == 0
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_undefined_table_exception_returns_skipped_result() -> None:
    """When the DB raises UndefinedTableError (asyncpg.PostgresError subclass),
    the adapter must not crash — it returns skipped=True without advancing the
    watermark or upsetting any episode.

    This exercises the ``except asyncpg.PostgresError`` branch directly,
    distinct from the ``information_schema`` table-existence check.
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError(
            'relation "connectors.spotify_listening_sessions" does not exist'
        )
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SpotifySessionAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.rows_projected == 0
    assert result.watermark is None
    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Checkpoint advance / resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_advances_to_latest_started_at() -> None:
    t1 = _NOW
    t2 = _NOW + timedelta(hours=1)
    rows = [
        _make_row(started_at=t1, idempotency_key="k1"),
        _make_row(started_at=t2, idempotency_key="k2"),
    ]
    adapter = SpotifySessionAdapter()

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t2


@pytest.mark.asyncio
async def test_watermark_preserved_when_no_rows() -> None:
    """When the evidence table exists but no new rows, watermark stays at since."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table exists
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SpotifySessionAdapter()
    cp = _chronicler_pool()
    prior_watermark = _NOW - timedelta(days=1)

    with patch("butlers.chronicler.adapters.spotify.upsert_episode"):
        result = await adapter.project(pool, chronicler_pool=cp, since=prior_watermark)

    assert result.watermark == prior_watermark
    assert result.rows_projected == 0


@pytest.mark.asyncio
async def test_since_filter_passed_to_query() -> None:
    """When ``since`` is given, the fetch query uses it as a WHERE filter."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SpotifySessionAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=2)

    with patch("butlers.chronicler.adapters.spotify.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    # The second positional arg to conn.fetch should be the since timestamp.
    assert conn.fetch.await_count == 1
    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "started_at > $1" in query
    assert call_args.args[1] == since


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_order_by_includes_id_tiebreaker_without_since() -> None:
    """ORDER BY clause must include id ASC as a tie-breaker when since=None.

    Same-timestamp sessions in the evidence table have non-deterministic ordering
    without a secondary sort key, which can cause rows to be missed or duplicated
    at batch boundaries when paginating with a watermark.
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SpotifySessionAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    query: str = conn.fetch.call_args.args[0]
    assert "ORDER BY started_at ASC, id ASC" in query


@pytest.mark.asyncio
async def test_order_by_includes_id_tiebreaker_with_since() -> None:
    """ORDER BY clause must include id ASC as a tie-breaker when since is given."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SpotifySessionAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)

    with patch("butlers.chronicler.adapters.spotify.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    query: str = conn.fetch.call_args.args[0]
    assert "ORDER BY started_at ASC, id ASC" in query


# ---------------------------------------------------------------------------
# Deferred per-track events NOT produced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_per_track_point_events_produced() -> None:
    """Per-track PointEvents must NOT be emitted by this adapter."""
    row = _make_row(track_count=10)
    adapter = SpotifySessionAdapter()

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert),
        patch("butlers.chronicler.adapters.spotify.upsert_point_event", create=True) as mock_pe,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.point_events == 0
    mock_pe.assert_not_called()


# ---------------------------------------------------------------------------
# Contracts registration
# ---------------------------------------------------------------------------


def test_spotify_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import SpotifySessionAdapter as _Cls

    assert _Cls is SpotifySessionAdapter


def test_spotify_session_summary_supported_in_contracts() -> None:
    from butlers.chronicler.contracts import find_source
    from butlers.chronicler.models import Compatibility

    source = find_source("spotify.session_summary")
    assert source is not None
    assert source.chronicler_compatibility == Compatibility.SUPPORTED
    assert source.read_surface == "connectors.spotify_listening_sessions"


def test_spotify_session_summary_in_supported_names() -> None:
    from butlers.chronicler.contracts import supported_source_names

    assert "spotify.session_summary" in supported_source_names()


def test_spotify_session_summary_not_in_deferred_names() -> None:
    from butlers.chronicler.contracts import deferred_source_names

    assert "spotify.session_summary" not in deferred_source_names()
