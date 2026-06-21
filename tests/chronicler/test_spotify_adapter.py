"""Tests for the Spotify session-summary Chronicler projection adapter.

Covers:
- Per-episode projection correctness (one listening episode per session row).
- Title fallback hierarchy (context_name > context_uri > track_names > endpoint).
- Stable source_ref keyed to idempotency_key.
- Missing evidence surface graceful degradation.
- Checkpoint advance / resume (watermark advances by recorded_at).
- Source-scan guardrail: no LLM imports in adapters/spotify.py.
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
    track_names: list[str] | None = None,
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
        "track_names": track_names if track_names is not None else ["Song A", "Song B"],
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
    assert ep.privacy == Privacy.NORMAL
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
async def test_episode_title_falls_back_to_track_names_when_no_context() -> None:
    """When neither context_name nor context_uri is set, prefer track_names."""
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

    title = upserted[0].title
    assert title.startswith("Listened to ")
    assert "Song A" in title
    assert "Song B" in title


@pytest.mark.asyncio
async def test_episode_title_falls_back_to_endpoint_when_no_context_or_tracks() -> None:
    """When context AND track_names are missing, surface endpoint identity."""
    row = _make_row(context_name=None, context_uri=None, track_names=[])
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


@pytest.mark.asyncio
async def test_episode_title_lists_first_two_tracks_with_remainder_count() -> None:
    """When track_names has >2 entries, the title enumerates the first two
    inline and appends a "+N more" suffix to keep tooltips compact."""
    row = _make_row(
        context_name=None,
        context_uri=None,
        track_names=["Song A", "Song B", "Song C", "Song D"],
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

    title = upserted[0].title
    assert title == "Listened to Song A, Song B (+2 more)"


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
    """When the DB raises UndefinedTableError the adapter returns skipped=True."""
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
async def test_watermark_advances_to_latest_recorded_at() -> None:
    """Watermark tracks ``recorded_at`` so updated rows get re-projected."""
    t1 = _NOW
    t2 = _NOW + timedelta(hours=1)
    rows = [
        {**_make_row(started_at=t1, idempotency_key="k1"), "recorded_at": t1},
        {**_make_row(started_at=t1, idempotency_key="k2"), "recorded_at": t2},
    ]
    adapter = SpotifySessionAdapter()

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t2


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
