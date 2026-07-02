"""Tests for the comms -> Social Chronicler projection adapter.

Covers:
- Burst grouping/segmentation: same (channel, sender) rows within the gap
  collapse; a gap beyond the threshold (or a different sender) starts a new
  burst.
- Participant match-object encoding per channel (email normalization,
  telegram prefixing, verbatim discord/whatsapp).
- Participant resolution SQL shape (relationship.entity_facts, not a
  chronicler-local contact store) and resolved/unresolved mapping.
- End-to-end projection: message burst -> social_episode; resolved
  participant -> high confidence + participant episode_entities row;
  unresolved participant -> low confidence + unattributed (fail-soft, no
  participant row, episode still emitted).
- Missing evidence surface graceful degradation.
- Source-scan guardrail: no LLM imports in adapters/comms.py.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from butlers.chronicler.adapters.comms import (
    BURST_GAP_MINUTES,
    EPISODE_TYPE_SOCIAL,
    SOURCE_NAME,
    CommsSocialAdapter,
    _BurstGroupKey,
    _match_object_for,
    _normalize_email_sender,
    _predicate_for,
)
from butlers.chronicler.models import Confidence, Episode, Layer, PointEvent, Precision, Privacy

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 20, 9, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


class _NullCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        pass


def _make_row(**kwargs: object) -> MagicMock:
    return MagicMock(**kwargs, **{"__getitem__": lambda s, k, _kwargs=kwargs: _kwargs[k]})


def _event_row(
    *,
    event_id: object | None = None,
    received_at: datetime = _NOW,
    source_channel: str = "telegram_bot",
    source_sender_identity: str = "12345",
) -> MagicMock:
    return _make_row(
        id=event_id or uuid4(),
        received_at=received_at,
        source_channel=source_channel,
        source_sender_identity=source_sender_identity,
    )


def _pool_returning(
    *,
    events: list[MagicMock],
    participant_rows: list[MagicMock] | None = None,
    table_exists: bool = True,
    owner_row: MagicMock | None = None,
) -> AsyncMock:
    """Mock cross-butler read pool serving (in order): exists-check, events
    fetch, owner-entity fetchrow, participant-resolution fetch."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=table_exists)
    conn.fetch = AsyncMock(side_effect=[events, participant_rows or []])
    conn.fetchrow = AsyncMock(return_value=owner_row)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _chronicler_pool() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_NullCtx())
    conn.execute = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool, conn


# ---------------------------------------------------------------------------
# Source-scan guardrail
# ---------------------------------------------------------------------------


def test_no_llm_imports_in_comms_adapter() -> None:
    import butlers.chronicler.adapters.comms as mod

    source = ast.parse(open(mod.__file__).read())
    forbidden = {"anthropic", "openai", "claude_agent_sdk"}
    for node in ast.walk(source):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in forbidden


# ---------------------------------------------------------------------------
# Match-object encoding
# ---------------------------------------------------------------------------


def test_normalize_email_sender_extracts_bare_address() -> None:
    assert _normalize_email_sender("John Doe <John@Example.com>") == "john@example.com"


def test_normalize_email_sender_falls_back_to_raw_when_no_address() -> None:
    assert _normalize_email_sender("not-an-address") == "not-an-address"


@pytest.mark.parametrize(
    "channel,sender,expected",
    [
        ("email", "Jane <jane@example.com>", "jane@example.com"),
        ("telegram_bot", "555", "telegram:555"),
        ("telegram_user_client", "telegram:555", "telegram:555"),
        ("discord", "user#1234", "user#1234"),
        ("whatsapp_user_client", "6591234567@s.whatsapp.net", "6591234567@s.whatsapp.net"),
    ],
)
def test_match_object_for_channel(channel: str, sender: str, expected: str) -> None:
    assert _match_object_for(channel, sender) == expected


def test_predicate_for_channel() -> None:
    assert _predicate_for("email") == "has-email"
    assert _predicate_for("telegram_bot") == "has-handle"
    assert _predicate_for("discord") == "has-handle"
    assert _predicate_for("whatsapp_user_client") == "has-handle"


# ---------------------------------------------------------------------------
# Burst grouping / segmentation
# ---------------------------------------------------------------------------


def test_group_into_bursts_groups_by_channel_and_sender() -> None:
    rows = [
        _event_row(source_channel="telegram_bot", source_sender_identity="1"),
        _event_row(source_channel="telegram_bot", source_sender_identity="2"),
        _event_row(source_channel="discord", source_sender_identity="1"),
    ]
    groups = CommsSocialAdapter._group_into_bursts(rows)
    assert len(groups) == 3
    assert _BurstGroupKey("telegram_bot", "1") in groups
    assert _BurstGroupKey("telegram_bot", "2") in groups
    assert _BurstGroupKey("discord", "1") in groups


def test_segment_burst_collapses_within_gap() -> None:
    adapter = CommsSocialAdapter()
    rows = [
        _event_row(received_at=_NOW),
        _event_row(received_at=_NOW + timedelta(minutes=5)),
        _event_row(received_at=_NOW + timedelta(minutes=10)),
    ]
    segments = adapter._segment_burst(rows)
    assert len(segments) == 1
    assert len(segments[0]) == 3


def test_segment_burst_splits_beyond_gap_threshold() -> None:
    adapter = CommsSocialAdapter()
    t2 = _NOW + timedelta(minutes=BURST_GAP_MINUTES + 1)
    rows = [_event_row(received_at=_NOW), _event_row(received_at=t2)]
    segments = adapter._segment_burst(rows)
    assert len(segments) == 2
    assert len(segments[0]) == 1
    assert len(segments[1]) == 1


# ---------------------------------------------------------------------------
# Participant resolution SQL shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_participants_reads_entity_facts_not_contact_info() -> None:
    """Architectural invariant: participant resolution reads
    relationship.entity_facts (+ public.entities for display_name) and NEVER a
    chronicler-local contact store."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    await CommsSocialAdapter()._resolve_participants(pool, [_BurstGroupKey("discord", "u1")])

    sql: str = conn.fetch.call_args[0][0]
    assert "relationship.entity_facts" in sql
    assert "public.entities" in sql
    assert "contact_info" not in sql


@pytest.mark.asyncio
async def test_resolve_participants_postgres_error_degrades_to_empty() -> None:
    import asyncpg as _asyncpg

    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=_asyncpg.PostgresError())
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    result = await CommsSocialAdapter()._resolve_participants(
        pool, [_BurstGroupKey("discord", "u1")]
    )
    assert result == {}


# ---------------------------------------------------------------------------
# End-to-end projection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_burst_projects_social_episode() -> None:
    """A burst of ingestion_events -> exactly one social_episode candidate."""
    rows = [
        _event_row(received_at=_NOW, source_sender_identity="42"),
        _event_row(received_at=_NOW + timedelta(minutes=3), source_sender_identity="42"),
    ]
    pool = _pool_returning(events=rows)
    cp, conn = _chronicler_pool()

    upserted_episodes: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted_episodes.append(episode)
        return episode

    async def _fake_upsert_pe(conn: object, event: PointEvent) -> PointEvent:
        event.id = uuid4()
        return event

    with (
        patch(
            "butlers.chronicler.adapters.comms.upsert_point_event",
            side_effect=_fake_upsert_pe,
        ),
        patch(
            "butlers.chronicler.adapters.comms.upsert_episode",
            side_effect=_fake_upsert_ep,
        ),
    ):
        result = await CommsSocialAdapter().project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    assert result.point_events == 2
    assert len(upserted_episodes) == 1
    episode = upserted_episodes[0]
    assert episode.source_name == SOURCE_NAME
    assert episode.episode_type == EPISODE_TYPE_SOCIAL
    assert episode.start_at == _NOW
    assert episode.end_at == _NOW + timedelta(minutes=3)
    assert episode.layer == Layer.ACTIVITY
    assert episode.precision == Precision.EXACT
    assert episode.privacy == Privacy.NORMAL
    assert episode.payload["message_count"] == 2


@pytest.mark.asyncio
async def test_source_ref_disambiguates_concurrent_senders_same_channel_same_second() -> None:
    """Two different senders starting a burst at the same second on the same
    channel must not collide on ``source_ref`` (would silently overwrite one
    episode with the other via the ``ON CONFLICT (source_name, source_ref)``
    upsert)."""
    rows = [
        _event_row(received_at=_NOW, source_sender_identity="alice"),
        _event_row(received_at=_NOW, source_sender_identity="bob"),
    ]
    pool = _pool_returning(events=rows)
    cp, conn = _chronicler_pool()

    upserted_episodes: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted_episodes.append(episode)
        return episode

    async def _fake_upsert_pe(conn: object, event: PointEvent) -> PointEvent:
        event.id = uuid4()
        return event

    with (
        patch(
            "butlers.chronicler.adapters.comms.upsert_point_event",
            side_effect=_fake_upsert_pe,
        ),
        patch(
            "butlers.chronicler.adapters.comms.upsert_episode",
            side_effect=_fake_upsert_ep,
        ),
    ):
        result = await CommsSocialAdapter().project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 2
    assert len(upserted_episodes) == 2
    source_refs = {ep.source_ref for ep in upserted_episodes}
    assert len(source_refs) == 2, "distinct senders must not share a source_ref"
    assert all("alice" in ref or "bob" in ref for ref in source_refs)


@pytest.mark.asyncio
async def test_resolved_participant_yields_high_confidence_and_participant_row() -> None:
    entity_id = uuid4()
    rows = [_event_row(received_at=_NOW, source_channel="discord", source_sender_identity="u1")]
    participant_rows = [
        _make_row(
            channel="discord", sender_identity="u1", entity_id=entity_id, display_name="Alex"
        ),
    ]
    pool = _pool_returning(events=rows, participant_rows=participant_rows)
    cp, conn = _chronicler_pool()

    episode_id = uuid4()
    upserted_episodes: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        episode.id = episode_id
        upserted_episodes.append(episode)
        return episode

    async def _fake_upsert_pe(conn: object, event: PointEvent) -> PointEvent:
        event.id = uuid4()
        return event

    with (
        patch(
            "butlers.chronicler.adapters.comms.upsert_point_event",
            side_effect=_fake_upsert_pe,
        ),
        patch(
            "butlers.chronicler.adapters.comms.upsert_episode",
            side_effect=_fake_upsert_ep,
        ),
    ):
        await CommsSocialAdapter().project(pool, chronicler_pool=cp, since=None)

    # Resolved participant -> two independent evidence kinds -> high confidence,
    # and the episode names the participant (spec: "Message burst becomes a
    # Social activity" -> "a social activity is emitted naming that participant").
    assert upserted_episodes[0].confidence == Confidence.HIGH
    assert upserted_episodes[0].payload["participant_status"] == "resolved"
    assert upserted_episodes[0].title == "Messages with Alex"

    insert_calls = [
        call for call in conn.execute.call_args_list if "episode_entities" in call.args[0]
    ]
    participant_calls = [
        c for c in insert_calls if c.args[-1] == "participant" or entity_id in c.args
    ]
    assert participant_calls, "expected an episode_entities INSERT for the resolved participant"


@pytest.mark.asyncio
async def test_unresolved_participant_degrades_to_unattributed_low_confidence() -> None:
    """Unresolved participant: episode still emitted, low confidence, unattributed."""
    rows = [_event_row(received_at=_NOW, source_channel="discord", source_sender_identity="ghost")]
    # No participant_rows -> resolution returns empty mapping.
    pool = _pool_returning(events=rows, participant_rows=[])
    cp, conn = _chronicler_pool()

    upserted_episodes: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted_episodes.append(episode)
        episode.id = uuid4()
        return episode

    async def _fake_upsert_pe(conn: object, event: PointEvent) -> PointEvent:
        event.id = uuid4()
        return event

    with (
        patch(
            "butlers.chronicler.adapters.comms.upsert_point_event",
            side_effect=_fake_upsert_pe,
        ),
        patch(
            "butlers.chronicler.adapters.comms.upsert_episode",
            side_effect=_fake_upsert_ep,
        ),
    ):
        result = await CommsSocialAdapter().project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    episode = upserted_episodes[0]
    assert episode.confidence == Confidence.LOW
    assert episode.payload["participant_status"] == "unattributed"

    # No episode_entities INSERT should carry role='participant'.
    insert_calls = [
        call for call in conn.execute.call_args_list if "episode_entities" in call.args[0]
    ]
    participant_calls = [c for c in insert_calls if "participant" in c.args]
    assert not participant_calls


@pytest.mark.asyncio
async def test_missing_evidence_table_returns_skipped_result() -> None:
    pool = _pool_returning(events=[], table_exists=False)
    cp, _conn = _chronicler_pool()

    result = await CommsSocialAdapter().project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert "ingestion_events" in (result.skipped_reason or "")


@pytest.mark.asyncio
async def test_no_new_rows_preserves_watermark() -> None:
    pool = _pool_returning(events=[])
    cp, _conn = _chronicler_pool()

    since = _NOW
    result = await CommsSocialAdapter().project(pool, chronicler_pool=cp, since=since)

    assert result.watermark == since
    assert result.episodes_closed == 0


@pytest.mark.asyncio
async def test_watermark_advances_to_latest_received_at() -> None:
    t2 = _NOW + timedelta(minutes=10)
    rows = [_event_row(received_at=_NOW), _event_row(received_at=t2)]
    pool = _pool_returning(events=rows)
    cp, _conn = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.comms.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.comms.upsert_episode") as mock_ep,
    ):
        mock_pe.side_effect = lambda conn, ev: ev
        mock_ep.side_effect = lambda conn, ep: ep
        result = await CommsSocialAdapter().project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t2


def test_comms_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import CommsSocialAdapter as ExportedAdapter

    assert ExportedAdapter is CommsSocialAdapter
