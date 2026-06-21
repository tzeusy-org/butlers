"""Tests for run_interaction_sync() in roster/relationship/jobs/relationship_jobs.py.

Covers:
- Group-aware pre-grouping by (source_thread_identity, source_channel, date)
- interaction_eligible=false filter (messages skipped before grouping)
- Participant count gate (>20 skips the group)
- Owner-presence direction detection (incoming vs outgoing)
- Outgoing hour offsets (+12 relative to incoming)
- group_size injection into fact metadata
- Unresolved sender handling
- DM group_size=1 logic
- Backward compatibility: no regression on existing stats keys
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

# The roster job modules are loaded dynamically via the root conftest using
# _load_roster_jobs(); they are registered in sys.modules under the key
# "butlers.jobs._roster.relationship_jobs" but are NOT accessible as
# butlers.jobs._roster.<attribute> since _roster is not a real subpackage.
# We retrieve the module object directly from sys.modules.
_MODULE_KEY = "butlers.jobs._roster.relationship_jobs"


def _get_rjobs() -> ModuleType:
    """Return the dynamically-loaded relationship_jobs module."""
    mod = sys.modules.get(_MODULE_KEY)
    if mod is None:
        from butlers.jobs._roster_loader import load_roster_jobs

        mod = load_roster_jobs("relationship")
    return mod


# Convenience aliases evaluated at import time (safe once conftest has run).
def _rjobs_attr(name: str) -> Any:
    return getattr(_get_rjobs(), name)


pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 16, 10, 0, 0, tzinfo=UTC)
_TODAY = date(2026, 4, 16)

_ENTITY_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_ENTITY_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_ENTITY_OWNER = uuid.UUID("00000000-0000-0000-0000-000000000001")

_THREAD_1 = "thread-1"
_CHANNEL = "telegram_user_client"
_CI_TYPE = "telegram_chat_id"


# These constants are looked up lazily at test execution time via _rjobs_attr().
# Using a helper avoids accessing sys.modules before the root conftest has run.
def _incoming_hour() -> int:
    return _rjobs_attr("_INTERACTION_SYNC_CHANNEL_HOUR_OFFSET")[_CHANNEL]


def _outgoing_hour() -> int:
    return _rjobs_attr("_INTERACTION_SYNC_CHANNEL_HOUR_OFFSET_OUTGOING")[_CHANNEL]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool() -> MagicMock:
    """Return a pool mock suitable for most test scenarios."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


def _make_inbox_row(
    *,
    thread_identity: str = _THREAD_1,
    source_channel: str = _CHANNEL,
    interaction_date: date = _TODAY,
    sender_identities: list[str] | None = None,
    message_count: int = 1,
    participant_count: int | None = None,
) -> dict[str, Any]:
    """Build a dict mimicking a row returned by the group-by inbox query."""
    row: dict[str, Any] = {
        "thread_identity": thread_identity,
        "source_channel": source_channel,
        "interaction_date": interaction_date,
        "sender_identities": sender_identities or ["sender-alice"],
        "message_count": message_count,
        "participant_count": participant_count,
    }
    # Wrap in a MagicMock so row["key"] and row.get() both work.
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: row[k]
    mock_row.get = lambda k, default=None: row.get(k, default)
    return mock_row


def _make_entity_row(
    *,
    ci_type: str = _CI_TYPE,
    ci_value: str,
    entity_id: uuid.UUID,
    roles: list[str] | None = None,
) -> dict[str, Any]:
    """Build a dict mimicking a row from the entity_facts batch-resolve query.

    The query now returns entity_id (= ef.subject) instead of contact_id.
    """
    row: dict[str, Any] = {
        "ci_type": ci_type,
        "ci_value": ci_value,
        "entity_id": entity_id,
        "roles": roles or [],
    }
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: row[k]
    mock_row.get = lambda k, default=None: row.get(k, default)
    return mock_row


def _make_state_get_none(pool: MagicMock) -> None:
    """Patch state_get to return None (first-run checkpoint)."""
    pool.fetchval = AsyncMock(return_value=None)


async def _run_with_mocked_deps(
    pool: MagicMock,
    *,
    inbox_rows: list | None = None,
    contact_rows: list | None = None,
    calendar_rows: list | None = None,
    interaction_log_return: dict | None = None,
) -> tuple[dict[str, Any], MagicMock]:
    """Run interaction_sync with all external dependencies mocked.

    The pool.fetch side_effect is set up to return different row sets for
    each call in order:
      1st call  → inbox groups (switchboard.message_inbox)
      2nd call  → contact resolution (public.contact_info)
      3rd call  → calendar events (public.calendar_events)
      4th call  → calendar email resolution (public.contact_info)
    """
    fetch_returns = [
        inbox_rows if inbox_rows is not None else [],
        contact_rows if contact_rows is not None else [],
        calendar_rows if calendar_rows is not None else [],
        [],  # calendar email resolution
        [],  # knows-count query (Step 6)
    ]
    pool.fetch = AsyncMock(side_effect=fetch_returns)

    log_return = interaction_log_return or {"id": str(uuid.uuid4()), "logged": True}

    mod = _get_rjobs()
    run_fn = mod.run_interaction_sync

    mock_log = AsyncMock(return_value=log_return)
    mock_state_get = AsyncMock(return_value=None)
    mock_state_set = AsyncMock()

    # state_get / state_set are module-level names in relationship_jobs.py.
    # interaction_log is a local import inside run_interaction_sync; patch
    # it at the source module so all callers see the mock.
    with (
        patch.object(mod, "state_get", mock_state_get),
        patch.object(mod, "state_set", mock_state_set),
        patch(
            "butlers.tools.relationship.interactions.interaction_log",
            mock_log,
        ),
    ):
        # Patch datetime.now in the module namespace to return a fixed time
        # so the scan window calculation is deterministic.
        real_datetime = datetime

        class _FixedDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return _NOW.replace(tzinfo=tz) if tz else _NOW

        with patch.object(mod, "datetime", _FixedDatetime):
            stats = await run_fn(pool)

    return stats, mock_log


# ---------------------------------------------------------------------------
# Test: stats keys are present (backward compat)
# ---------------------------------------------------------------------------


async def test_stats_contains_all_required_keys():
    """Return dict must include both old and new stat keys."""
    pool = _make_pool()
    stats, _ = await _run_with_mocked_deps(pool)
    required_keys = {
        "scan_window_start",
        "scan_window_end",
        "processed",
        "logged",
        "skipped_unresolved",
        "skipped_owner",
        "skipped_ineligible",
        "skipped_group_too_large",
        "calendar_events_scanned",
        "co_attended_edges_minted",
        "knows_edges_minted",
        "errors",
    }
    assert required_keys.issubset(set(stats.keys()))


# ---------------------------------------------------------------------------
# Test: empty inbox → no interactions logged
# ---------------------------------------------------------------------------


async def test_empty_inbox_logs_nothing():
    """No inbox rows → interaction_log is never called."""
    pool = _make_pool()
    stats, mock_log = await _run_with_mocked_deps(pool, inbox_rows=[])
    mock_log.assert_not_called()
    assert stats["logged"] == 0
    assert stats["processed"] == 0


async def test_missing_calendar_table_is_skipped_without_error():
    """Missing public.calendar_events should not increment errors."""
    pool = _make_pool()
    pool.fetch = AsyncMock(
        side_effect=[
            [],
            asyncpg.exceptions.UndefinedTableError(
                'relation "public.calendar_events" does not exist'
            ),
            [],  # knows-count query (Step 6) — empty, no edges minted
        ]
    )

    mod = _get_rjobs()
    run_fn = mod.run_interaction_sync

    mock_log = AsyncMock(return_value={"id": str(uuid.uuid4()), "logged": True})
    mock_state_get = AsyncMock(return_value=None)
    mock_state_set = AsyncMock()

    with (
        patch.object(mod, "state_get", mock_state_get),
        patch.object(mod, "state_set", mock_state_set),
        patch(
            "butlers.tools.relationship.interactions.interaction_log",
            mock_log,
        ),
    ):
        real_datetime = datetime

        class _FixedDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return _NOW.replace(tzinfo=tz) if tz else _NOW

        with patch.object(mod, "datetime", _FixedDatetime):
            stats = await run_fn(pool)

    assert stats["calendar_events_scanned"] == 0
    assert stats["errors"] == 0
    assert pool.fetch.await_count == 3
    mock_log.assert_not_called()


async def test_checkpoint_read_failure_uses_default_window_without_raising(caplog):
    """Checkpoint read failures are reported in stats instead of escaping to scheduler."""
    pool = _make_pool()
    pool.fetch = AsyncMock(side_effect=[[], [], []])

    mod = _get_rjobs()
    run_fn = mod.run_interaction_sync

    mock_log = AsyncMock(return_value={"id": str(uuid.uuid4()), "logged": True})
    mock_state_get = AsyncMock(side_effect=RuntimeError("state unavailable"))
    mock_state_set = AsyncMock()

    with (
        patch.object(mod, "state_get", mock_state_get),
        patch.object(mod, "state_set", mock_state_set),
        patch(
            "butlers.tools.relationship.interactions.interaction_log",
            mock_log,
        ),
    ):
        real_datetime = datetime

        class _FixedDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return _NOW.replace(tzinfo=tz) if tz else _NOW

        with patch.object(mod, "datetime", _FixedDatetime):
            with caplog.at_level(logging.WARNING, logger=mod.logger.name):
                stats = await run_fn(pool)

    # On read failure the job falls back to the module's max-lookback window,
    # so derive the expected start from _NOW and the constant rather than
    # hard-coding a date string that will rot if the window changes.
    expected_start = _NOW - timedelta(days=_rjobs_attr("_INTERACTION_SYNC_MAX_WINDOW_DAYS"))
    assert stats["errors"] == 1
    assert stats["scan_window_start"] == expected_start.isoformat()
    assert any("failed to read checkpoint" in record.message for record in caplog.records)
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)
    mock_state_set.assert_awaited_once_with(
        pool, _rjobs_attr("_INTERACTION_SYNC_STATE_KEY"), _NOW.isoformat()
    )
    mock_log.assert_not_called()


async def test_checkpoint_write_failure_returns_error_stats_without_raising(caplog):
    """Checkpoint write failures do not make deterministic dispatch fail."""
    pool = _make_pool()
    pool.fetch = AsyncMock(side_effect=[[], [], []])

    mod = _get_rjobs()
    run_fn = mod.run_interaction_sync

    mock_log = AsyncMock(return_value={"id": str(uuid.uuid4()), "logged": True})
    mock_state_get = AsyncMock(return_value=None)
    mock_state_set = AsyncMock(side_effect=RuntimeError("state unavailable"))

    with (
        patch.object(mod, "state_get", mock_state_get),
        patch.object(mod, "state_set", mock_state_set),
        patch(
            "butlers.tools.relationship.interactions.interaction_log",
            mock_log,
        ),
    ):
        real_datetime = datetime

        class _FixedDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return _NOW.replace(tzinfo=tz) if tz else _NOW

        with patch.object(mod, "datetime", _FixedDatetime):
            with caplog.at_level(logging.WARNING, logger=mod.logger.name):
                stats = await run_fn(pool)

    assert stats["errors"] == 1
    assert any("failed to write checkpoint" in record.message for record in caplog.records)
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)
    mock_state_set.assert_awaited_once_with(
        pool, _rjobs_attr("_INTERACTION_SYNC_STATE_KEY"), _NOW.isoformat()
    )
    mock_log.assert_not_called()


# ---------------------------------------------------------------------------
# Test: interaction_eligible=false is excluded BEFORE grouping
#
# The SQL WHERE clause filters these out, so by the time the job processes
# rows none of them represent ineligible messages.  We verify that if only
# ineligible messages existed (inbox_rows=[]) the job logs nothing.
# ---------------------------------------------------------------------------


async def test_interaction_eligible_false_excluded():
    """Rows filtered by interaction_eligible=false produce no interactions."""
    pool = _make_pool()
    # The SQL excludes interaction_eligible=false before aggregation; we
    # simulate this by providing an empty inbox result (as the DB would).
    stats, mock_log = await _run_with_mocked_deps(pool, inbox_rows=[])
    mock_log.assert_not_called()
    assert stats["logged"] == 0
    assert stats["skipped_ineligible"] == 0  # field exists and is zero


# ---------------------------------------------------------------------------
# Test: participant count gate — groups with >20 participants are skipped
# ---------------------------------------------------------------------------


async def test_participant_count_gate_skips_large_groups():
    """Groups with participant_count > 20 are skipped; skipped_group_too_large is incremented."""
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["alice", "bob"],
            participant_count=21,
        )
    ]
    contact_rows = [
        _make_entity_row(ci_value="alice", entity_id=_ENTITY_A),
        _make_entity_row(ci_value="bob", entity_id=_ENTITY_B),
    ]
    stats, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    mock_log.assert_not_called()
    assert stats["skipped_group_too_large"] == 1
    assert stats["logged"] == 0


async def test_participant_count_gate_boundary_20_is_allowed():
    """Groups with exactly 20 participants are NOT skipped (threshold is > 20)."""
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["alice"],
            participant_count=20,
        )
    ]
    contact_rows = [_make_entity_row(ci_value="alice", entity_id=_ENTITY_A)]
    stats, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    assert stats["skipped_group_too_large"] == 0
    mock_log.assert_called()


async def test_participant_count_falls_back_to_distinct_sender_count():
    """When participant_count is None, fall back to distinct sender count."""
    pool = _make_pool()
    # participant_count=None → fallback to len(sender_identities)
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["alice"],
            participant_count=None,
        )
    ]
    contact_rows = [_make_entity_row(ci_value="alice", entity_id=_ENTITY_A)]
    stats, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    # 1 distinct sender → no gate fire
    assert stats["skipped_group_too_large"] == 0
    mock_log.assert_called()


async def test_participant_count_fallback_triggers_gate():
    """Fallback sender count > 20 still triggers the gate."""
    pool = _make_pool()
    # 21 distinct senders, no participant_count in context
    senders = [f"sender-{i}" for i in range(21)]
    inbox_rows = [_make_inbox_row(sender_identities=senders, participant_count=None)]
    contact_rows = [_make_entity_row(ci_value=s, entity_id=uuid.uuid4()) for s in senders]
    stats, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    assert stats["skipped_group_too_large"] == 1
    mock_log.assert_not_called()


# ---------------------------------------------------------------------------
# Test: DM — only one non-owner sender → group_size=1
# ---------------------------------------------------------------------------


async def test_dm_group_size_is_1():
    """DM (single non-owner sender) must pass group_size=1 in metadata."""
    pool = _make_pool()
    inbox_rows = [_make_inbox_row(sender_identities=["alice"], participant_count=1)]
    contact_rows = [_make_entity_row(ci_value="alice", entity_id=_ENTITY_A)]
    _, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    mock_log.assert_called_once()
    kwargs = mock_log.call_args.kwargs
    assert kwargs["metadata"]["group_size"] == 1


# ---------------------------------------------------------------------------
# Test: incoming-only interaction when owner did NOT send
# ---------------------------------------------------------------------------


async def test_incoming_only_when_owner_not_present():
    """When no owner sender is in the group, only incoming facts are logged."""
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["alice"],
            participant_count=1,
        )
    ]
    contact_rows = [_make_entity_row(ci_value="alice", entity_id=_ENTITY_A)]
    _, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    # Only one call, direction=incoming
    mock_log.assert_called_once()
    kwargs = mock_log.call_args.kwargs
    assert kwargs["direction"] == "incoming"
    assert kwargs["occurred_at"].hour == _incoming_hour()


# ---------------------------------------------------------------------------
# Test: incoming + outgoing when owner sent in the same chat
# ---------------------------------------------------------------------------


async def test_incoming_and_outgoing_when_owner_sent():
    """When the owner is among the senders, each non-owner gets incoming AND outgoing."""
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["owner-handle", "alice"],
            participant_count=2,
        )
    ]
    contact_rows = [
        _make_entity_row(
            ci_value="owner-handle",
            entity_id=_ENTITY_OWNER,
            roles=["owner"],
        ),
        _make_entity_row(ci_value="alice", entity_id=_ENTITY_A),
    ]
    _, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    # alice → incoming + outgoing (owner skipped as contact)
    assert mock_log.call_count == 2
    calls_kwargs = [c.kwargs for c in mock_log.call_args_list]
    call_directions = {kw["direction"] for kw in calls_kwargs}
    assert call_directions == {"incoming", "outgoing"}


async def test_outgoing_uses_plus12_hour_offset():
    """Outgoing facts use hour = incoming_hour + 12 for dedup separation."""
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["owner-handle", "alice"],
            participant_count=2,
        )
    ]
    contact_rows = [
        _make_entity_row(
            ci_value="owner-handle",
            entity_id=_ENTITY_OWNER,
            roles=["owner"],
        ),
        _make_entity_row(ci_value="alice", entity_id=_ENTITY_A),
    ]
    _, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    calls_kwargs = [c.kwargs for c in mock_log.call_args_list]
    hour_by_dir = {kw["direction"]: kw["occurred_at"].hour for kw in calls_kwargs}
    assert hour_by_dir["incoming"] == _incoming_hour()
    assert hour_by_dir["outgoing"] == _outgoing_hour()
    assert hour_by_dir["outgoing"] == hour_by_dir["incoming"] + 12


# ---------------------------------------------------------------------------
# Test: group_size is injected into both incoming and outgoing metadata
# ---------------------------------------------------------------------------


async def test_group_size_in_metadata_for_group_chat():
    """group_size from participant_count is present in metadata for both fact directions."""
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["owner-handle", "alice"],
            participant_count=5,
        )
    ]
    contact_rows = [
        _make_entity_row(
            ci_value="owner-handle",
            entity_id=_ENTITY_OWNER,
            roles=["owner"],
        ),
        _make_entity_row(ci_value="alice", entity_id=_ENTITY_A),
    ]
    _, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    for c in mock_log.call_args_list:
        assert c.kwargs["metadata"]["group_size"] == 5


# ---------------------------------------------------------------------------
# Test: owner contact is excluded from resolution (no self-interaction)
# ---------------------------------------------------------------------------


async def test_owner_not_logged_as_contact():
    """Owner contact is skipped during contact resolution; skipped_owner incremented."""
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["owner-handle"],
            participant_count=1,
        )
    ]
    contact_rows = [
        _make_entity_row(
            ci_value="owner-handle",
            entity_id=_ENTITY_OWNER,
            roles=["owner"],
        ),
    ]
    stats, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    mock_log.assert_not_called()
    assert stats["skipped_owner"] == 1
    assert stats["logged"] == 0


# ---------------------------------------------------------------------------
# Test: unresolved sender is skipped gracefully
# ---------------------------------------------------------------------------


async def test_unresolved_sender_skipped():
    """Senders with no contact_info match are counted in skipped_unresolved."""
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["unknown-handle"],
            participant_count=1,
        )
    ]
    # contact_rows is empty → no resolution
    stats, mock_log = await _run_with_mocked_deps(pool, inbox_rows=inbox_rows, contact_rows=[])
    mock_log.assert_not_called()
    assert stats["skipped_unresolved"] == 1


# ---------------------------------------------------------------------------
# Test: multiple non-owner senders in a group chat each get their facts
# ---------------------------------------------------------------------------


async def test_multiple_non_owner_senders_each_get_facts():
    """Each non-owner sender in a group gets at least one incoming fact."""
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["alice", "bob"],
            participant_count=2,
        )
    ]
    contact_rows = [
        _make_entity_row(ci_value="alice", entity_id=_ENTITY_A),
        _make_entity_row(ci_value="bob", entity_id=_ENTITY_B),
    ]
    stats, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    # 2 senders × 1 direction (no owner) = 2 calls
    assert mock_log.call_count == 2
    assert stats["logged"] == 2


async def test_multiple_non_owner_senders_with_owner():
    """Each non-owner in a group with owner gets incoming + outgoing = 2 facts each."""
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["owner-handle", "alice", "bob"],
            participant_count=3,
        )
    ]
    contact_rows = [
        _make_entity_row(ci_value="owner-handle", entity_id=_ENTITY_OWNER, roles=["owner"]),
        _make_entity_row(ci_value="alice", entity_id=_ENTITY_A),
        _make_entity_row(ci_value="bob", entity_id=_ENTITY_B),
    ]
    _, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    # alice + bob, each get incoming + outgoing = 4 calls
    assert mock_log.call_count == 4


# ---------------------------------------------------------------------------
# Test: duplicate returned from interaction_log does not increment logged
# ---------------------------------------------------------------------------


async def test_duplicate_does_not_increment_logged():
    """When interaction_log returns skipped=duplicate, logged is NOT incremented."""
    pool = _make_pool()
    inbox_rows = [_make_inbox_row(sender_identities=["alice"], participant_count=1)]
    contact_rows = [_make_entity_row(ci_value="alice", entity_id=_ENTITY_A)]
    stats, _ = await _run_with_mocked_deps(
        pool,
        inbox_rows=inbox_rows,
        contact_rows=contact_rows,
        interaction_log_return={"skipped": "duplicate", "existing_id": str(uuid.uuid4())},
    )
    assert stats["logged"] == 0


# ---------------------------------------------------------------------------
# Test: MAX_INTERACTION_GROUP_SIZE constant is 20
# ---------------------------------------------------------------------------


async def test_group_size_uses_participant_count_when_only_one_sender_resolves():
    """Group chats preserve participant_count-based group_size even if only one sender resolves.

    Regression guard for the bug where len(non_owner_contacts) was used instead of
    participant_count: a group with participant_count=5 but only one resolving sender
    must still yield group_size=5, not 1.
    """
    pool = _make_pool()
    # 5 senders in the chat, but only alice resolves to a contact.
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["alice", "ghost-1", "ghost-2", "ghost-3", "ghost-4"],
            participant_count=5,
        )
    ]
    contact_rows = [_make_entity_row(ci_value="alice", entity_id=_ENTITY_A)]
    _, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    mock_log.assert_called_once()
    kwargs = mock_log.call_args.kwargs
    assert kwargs["metadata"]["group_size"] == 5


async def test_bidirectional_dm_group_size_is_1():
    """A 1-on-1 DM where both owner and contact sent must still have group_size=1.

    Regression guard: participant_count=2 (owner + 1 contact) should yield
    group_size=1, not 2.
    """
    pool = _make_pool()
    inbox_rows = [
        _make_inbox_row(
            sender_identities=["owner-handle", "alice"],
            participant_count=2,
        )
    ]
    contact_rows = [
        _make_entity_row(
            ci_value="owner-handle",
            entity_id=_ENTITY_OWNER,
            roles=["owner"],
        ),
        _make_entity_row(ci_value="alice", entity_id=_ENTITY_A),
    ]
    _, mock_log = await _run_with_mocked_deps(
        pool, inbox_rows=inbox_rows, contact_rows=contact_rows
    )
    # alice gets incoming + outgoing (owner sent), both must have group_size=1
    assert mock_log.call_count == 2
    for c in mock_log.call_args_list:
        assert c.kwargs["metadata"]["group_size"] == 1, (
            f"Expected group_size=1 for DM, got {c.kwargs['metadata']['group_size']}"
        )


# ---------------------------------------------------------------------------
# Test: channel hour offset table coverage
# ---------------------------------------------------------------------------


def test_channel_hour_offsets_cover_all_sync_channels():
    """Incoming and outgoing offset tables cover all three sync channels."""
    channel_map = _rjobs_attr("_INTERACTION_SYNC_CHANNEL_MAP")
    incoming_offsets = _rjobs_attr("_INTERACTION_SYNC_CHANNEL_HOUR_OFFSET")
    outgoing_offsets = _rjobs_attr("_INTERACTION_SYNC_CHANNEL_HOUR_OFFSET_OUTGOING")

    for channel in channel_map:
        assert channel in incoming_offsets
        assert channel in outgoing_offsets
        outgoing = outgoing_offsets[channel]
        incoming = incoming_offsets[channel]
        assert outgoing == incoming + 12, (
            f"Channel {channel}: expected outgoing={incoming + 12}, got {outgoing}"
        )


# ---------------------------------------------------------------------------
# Test: 'knows' edge derivation (Step 6)
# ---------------------------------------------------------------------------


def _make_knows_count_row(entity_id: uuid.UUID, count: int) -> MagicMock:
    """Build a mock row mimicking a result from the knows-count query."""
    row: dict[str, Any] = {"entity_id": entity_id, "interaction_count": count}
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: row[k]
    mock_row.get = lambda k, default=None: row.get(k, default)
    return mock_row


def _make_owner_entity_row(entity_id: uuid.UUID) -> MagicMock:
    """Build a mock row mimicking a public.entities lookup result."""
    row: dict[str, Any] = {"id": entity_id}
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: row[k]
    mock_row.get = lambda k, default=None: row.get(k, default)
    return mock_row


async def _run_with_knows_scenario(
    *,
    knows_count_rows: list,
    assert_outcome: str = "inserted",
) -> tuple[dict[str, Any], MagicMock]:
    """Run interaction_sync with mocked knows-count query and relationship_assert_fact.

    Uses empty inbox and empty calendar so only Step 6 (knows derivation) is
    exercised.  Returns (stats, mock_assert_fact).

    Pool.fetch side_effect order with empty inbox:
      1. inbox rows  → []
      2. calendar rows → []  (contact resolution step is skipped — no lookup_pairs)
      3. knows-count rows → knows_count_rows
    """
    pool = _make_pool()
    pool.fetch = AsyncMock(side_effect=[[], [], knows_count_rows])
    pool.fetchrow = AsyncMock(return_value=_make_owner_entity_row(_ENTITY_OWNER))

    mod = _get_rjobs()
    run_fn = mod.run_interaction_sync

    from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult

    mock_result = MagicMock(spec=AssertResult)
    mock_result.outcome = getattr(AssertOutcome, assert_outcome)
    mock_assert = AsyncMock(return_value=mock_result)

    with (
        patch.object(mod, "state_get", AsyncMock(return_value=None)),
        patch.object(mod, "state_set", AsyncMock()),
        patch(
            "butlers.tools.relationship.interactions.interaction_log",
            AsyncMock(return_value={"id": str(uuid.uuid4()), "logged": True}),
        ),
        patch(
            "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
            mock_assert,
        ),
    ):
        real_datetime = datetime

        class _FixedDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return _NOW.replace(tzinfo=tz) if tz else _NOW

        with patch.object(mod, "datetime", _FixedDatetime):
            stats = await run_fn(pool)

    return stats, mock_assert


async def test_knows_edge_minted_when_threshold_reached():
    """'knows' edges (both directions) are minted when count >= threshold."""
    threshold = _rjobs_attr("_KNOWS_THRESHOLD")
    knows_rows = [_make_knows_count_row(_ENTITY_A, threshold)]

    stats, mock_assert = await _run_with_knows_scenario(knows_count_rows=knows_rows)

    # Two edges per contact: contact→owner and owner→contact
    assert mock_assert.call_count == 2
    called_predicates = {c.args[2] for c in mock_assert.call_args_list}
    assert called_predicates == {"knows"}
    called_subjects = {c.args[1] for c in mock_assert.call_args_list}
    assert called_subjects == {_ENTITY_A, _ENTITY_OWNER}
    assert stats["knows_edges_minted"] == 2
    assert stats["errors"] == 0


async def test_knows_edge_not_minted_below_threshold():
    """'knows' edges are NOT minted when interaction count is below threshold."""
    # Return empty knows-count rows (HAVING filters out sub-threshold entities),
    # simulating that the DB returned no qualifying rows.
    stats, mock_assert = await _run_with_knows_scenario(knows_count_rows=[])

    mock_assert.assert_not_called()
    assert stats["knows_edges_minted"] == 0
    assert stats["errors"] == 0


async def test_knows_edge_idempotent_on_rerun():
    """Re-running with unchanged outcome returns 'unchanged' and does not increment minted."""
    threshold = _rjobs_attr("_KNOWS_THRESHOLD")
    knows_rows = [_make_knows_count_row(_ENTITY_A, threshold)]

    stats, mock_assert = await _run_with_knows_scenario(
        knows_count_rows=knows_rows,
        assert_outcome="unchanged",
    )

    # relationship_assert_fact is still called (idempotency is in that layer),
    # but since outcome is 'unchanged', knows_edges_minted stays at 0.
    assert mock_assert.call_count == 2
    assert stats["knows_edges_minted"] == 0
    assert stats["errors"] == 0
