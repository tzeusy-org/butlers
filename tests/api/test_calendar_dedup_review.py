"""Tests for the calendar read-model cross-source dedup (bu-tjo2m1).

The dashboard's duplicate-review routes are gone (bu-q11586.7); the workspace read
still collapses cross-source copies, so this covers the pure-function dedup/cluster
logic (including keep-separate pins already stored) and the fail-open rule/override
loaders.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from butlers.api.db import DatabaseManager
from butlers.api.read_models import calendar_workspace as rm
from butlers.api.read_models.calendar_workspace import _cluster_key, _dedup_workspace_rows

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _row(*, origin_ref: str, title: str, start: datetime, instance_id=None) -> dict:
    """A full workspace row dict (the shape ``_normalize_entry`` consumes)."""
    iid = instance_id or uuid4()
    return {
        "instance_id": iid,
        "source_id": uuid4(),
        "source_key": "provider:google:primary",
        "source_kind": "provider_event",
        "lane": "user",
        "provider": "google",
        "calendar_id": "primary",
        "butler_name": None,
        "display_name": "Primary",
        "writable": True,
        "source_metadata": {},
        "event_id": uuid4(),
        "origin_ref": origin_ref,
        "origin_instance_ref": str(uuid4()),
        "title": title,
        "description": "",
        "location": "",
        "event_timezone": "UTC",
        "all_day": False,
        "event_status": "confirmed",
        "visibility": "default",
        "recurrence_rule": None,
        "event_metadata": {"source_type": "provider_event"},
        "source_butler": None,
        "source_session_id": None,
        "instance_timezone": "UTC",
        "instance_starts_at": start,
        "instance_ends_at": start + timedelta(hours=1),
        "instance_status": "confirmed",
        "instance_metadata": {},
        "cursor_name": "provider_sync",
        "last_synced_at": start,
        "last_success_at": start,
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
    }


# ---------------------------------------------------------------------------
# Pure dedup / cluster logic
# ---------------------------------------------------------------------------


def test_dedup_collapses_cross_source_copies_and_exposes_cluster():
    start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    # Two cross-calendar copies: different origin_ref, same title+start → pass2.
    a = _row(origin_ref="ref-a", title="Standup", start=start)
    b = _row(origin_ref="ref-b", title="Standup", start=start)
    deduped, clusters = _dedup_workspace_rows([a, b], strategy="balanced")

    assert len(deduped) == 1  # collapsed to the lowest-keyset survivor
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.match_pass == "title"
    assert len(cluster.members) == 2
    assert cluster.keep_separate is False


def test_exact_strategy_does_not_run_title_pass():
    start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    a = _row(origin_ref="ref-a", title="Standup", start=start)
    b = _row(origin_ref="ref-b", title="Standup", start=start)
    deduped, clusters = _dedup_workspace_rows([a, b], strategy="exact")

    # Distinct origin_refs ⇒ the title pass never runs ⇒ nothing collapses.
    assert len(deduped) == 2
    assert clusters == []


def test_keep_separate_prevents_collapse_but_still_reports_cluster():
    start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    a = _row(origin_ref="ref-a", title="Standup", start=start)
    b = _row(origin_ref="ref-b", title="Standup", start=start)
    ck = _cluster_key(a, "title", aggressive=False)

    deduped, clusters = _dedup_workspace_rows([a, b], strategy="balanced", keep_separate={ck})

    assert len(deduped) == 2  # not collapsed — both survive
    assert len(clusters) == 1
    assert clusters[0].keep_separate is True


def test_origin_ref_pass_collapses_time_drifted_resync_keeping_freshest():
    # Regression (bu-ssp91): the 'Lou Shang farewell' event rendered twice with
    # one window 8h off. Two rows share one origin_ref but differ in start; keying
    # the origin_ref pass on origin_ref ALONE must collapse them to a single row,
    # and the most-recently-synced copy (higher instance_updated_at) must survive.
    t0 = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    stale = _row(origin_ref="lou", title="Lou Shang farewell", start=t0)
    stale["instance_updated_at"] = t0  # projected on the original (wrong) sync
    fresh = _row(origin_ref="lou", title="Lou Shang farewell", start=t0 + timedelta(hours=8))
    fresh["instance_updated_at"] = t0 + timedelta(days=100)  # corrected re-sync

    # Pre-sorted by (start, id): the stale/earlier copy sorts first, so a survivor
    # of `fresh` proves freshness beats keyset order.
    deduped, clusters = _dedup_workspace_rows([stale, fresh], strategy="balanced")

    assert len(deduped) == 1
    survivor = deduped[0]
    assert survivor["instance_id"] == fresh["instance_id"]
    assert survivor["instance_starts_at"] == t0 + timedelta(hours=8)
    assert len(clusters) == 1
    assert clusters[0].match_pass == "origin_ref"
    # The cluster reports the survivor first (members[0]).
    assert clusters[0].members[0]["instance_id"] == fresh["instance_id"]


def test_origin_ref_pass_keeps_recurring_occurrences_separate():
    # A recurring event's occurrences legitimately share one origin_ref at
    # different starts — keying origin_ref alone would wrongly collapse them, so
    # the pass keeps the start in the key when recurrence_rule is set.
    t0 = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    wk1 = _row(origin_ref="weekly", title="Standup", start=t0)
    wk1["recurrence_rule"] = "RRULE:FREQ=WEEKLY"
    wk2 = _row(origin_ref="weekly", title="Standup", start=t0 + timedelta(days=7))
    wk2["recurrence_rule"] = "RRULE:FREQ=WEEKLY"

    deduped, clusters = _dedup_workspace_rows([wk1, wk2], strategy="balanced")

    assert len(deduped) == 2  # both occurrences survive
    assert clusters == []


def test_origin_ref_pass_does_not_over_collapse_refless_rows():
    # Rows without an origin_ref (reminders/scheduled tasks) must not all collapse
    # into one: keying the empty string alone would fuse unrelated rows, so the
    # pass keeps the start in the key when origin_ref is empty.
    t0 = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    a = _row(origin_ref="", title="Reminder A", start=t0)
    b = _row(origin_ref="", title="Reminder B", start=t0 + timedelta(hours=3))

    deduped, clusters = _dedup_workspace_rows([a, b], strategy="exact")

    assert len(deduped) == 2
    assert clusters == []


# ---------------------------------------------------------------------------
# Store-level fail-open
# ---------------------------------------------------------------------------


async def test_load_dedup_rules_defaults_when_no_pool():
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butlers_with_module = MagicMock(return_value=[])
    mock_db.butler_names = []
    rules = await rm.load_dedup_rules(mock_db)
    assert rules.match_strategy == "balanced"
    assert rules.noisy_threshold == 2


async def test_load_keep_separate_keys_fail_open_on_query_error():
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("missing table"))
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butlers_with_module = MagicMock(return_value=["general"])
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=pool)
    keys = await rm.load_keep_separate_keys(mock_db)
    assert keys == set()
