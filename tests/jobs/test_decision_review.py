"""Tests for butlers.jobs.decision_review (bu-ckkpz.4, "Owner Decision Desk").

Covers:
- compute_decision_digest: missing/stale/unreadable export -> available=False
  (never a fabricated all-clear); real fixture -> correct decision detection
  (title-marker heuristic), P1-bug/deploy escalation detection via `blocks`
  dependency edges, and the 48h age threshold.
- Message composition: weekly digest header + escalation message shape.
- _check_suppression: mirrors notify()'s quiet-hours + context-bus gate.
- run_decision_review_digest / run_decision_escalation_check: degraded-data
  path records a ledger event and sends nothing; a genuine zero is a real
  all-clear; delivery + debounce-via-audit_log branches.

No real database required -- pools are mocked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.jobs.decision_review import (
    _check_suppression,
    _compose_escalation_message,
    _compose_weekly_digest_message,
    _deliver,
    _is_decision_bead,
    _is_deploy_bead,
    compute_decision_digest,
    run_decision_escalation_check,
    run_decision_review_digest,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)


def _write_export(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _decision(id_, *, title="DECISION REQUIRED (owner): pick one", created_days_ago=10, **kw):
    return {
        "id": id_,
        "title": title,
        "status": "open",
        "priority": 1,
        "issue_type": "task",
        "created_at": _iso(_NOW - timedelta(days=created_days_ago)),
        "dependencies": [],
        **kw,
    }


def _blocker(id_, *, title, issue_type, priority, blocks_id, edge_age_hours, status="open"):
    return {
        "id": id_,
        "title": title,
        "status": status,
        "priority": priority,
        "issue_type": issue_type,
        "created_at": _iso(_NOW - timedelta(days=30)),
        "dependencies": [
            {
                "issue_id": id_,
                "depends_on_id": blocks_id,
                "type": "blocks",
                "created_at": _iso(_NOW - timedelta(hours=edge_age_hours)),
            }
        ],
    }


# ---------------------------------------------------------------------------
# compute_decision_digest -- degraded-data paths
# ---------------------------------------------------------------------------


def test_digest_unavailable_when_export_missing(tmp_path):
    digest = compute_decision_digest(tmp_path / "does-not-exist.jsonl", now=_NOW)
    assert digest.available is False
    assert digest.unavailable_reason == "export_missing"
    assert digest.open_decisions == ()
    assert digest.escalations == ()


def test_digest_unavailable_when_export_stale(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [_decision("bu-a")])
    import os

    stale_mtime = (_NOW - timedelta(days=30)).timestamp()
    os.utime(export, (stale_mtime, stale_mtime))

    digest = compute_decision_digest(export, now=_NOW)
    assert digest.available is False
    assert digest.unavailable_reason == "export_stale"


def test_digest_unavailable_on_unparseable_json(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    export.write_text("{not valid json\n")

    digest = compute_decision_digest(export, now=_NOW)
    assert digest.available is False
    assert digest.unavailable_reason is not None
    assert digest.unavailable_reason.startswith("export_read_error:")


def test_digest_genuine_zero_is_available_and_empty(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [{"id": "bu-x", "title": "Ordinary task", "status": "open"}])

    digest = compute_decision_digest(export, now=_NOW)
    assert digest.available is True
    assert digest.unavailable_reason is None
    assert digest.open_decisions == ()


def test_digest_skips_non_dict_jsonl_lines_instead_of_crashing(tmp_path):
    """A line that parses as valid JSON but isn't an object (e.g. a bare list)
    must be skipped, not crash the whole load with AttributeError on .get()."""
    export = tmp_path / "issues.export.jsonl"
    export.write_text(
        json.dumps([1, 2, 3])
        + "\n"
        + json.dumps(_decision("bu-v4ipc", created_days_ago=6))
        + "\n"
        + json.dumps("just a string")
        + "\n"
    )

    digest = compute_decision_digest(export, now=_NOW)
    assert digest.available is True
    assert digest.unavailable_reason is None
    assert {d.id for d in digest.open_decisions} == {"bu-v4ipc"}


def test_digest_tolerates_malformed_dependencies_field(tmp_path):
    """`dependencies` not being a list, or containing non-dict edges, must not
    raise -- this loop runs outside compute_decision_digest's own try/except,
    so an uncaught exception here would break the "never raises" contract."""
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=6),
            {
                "id": "bu-wzbu9",
                "title": "Silent message loss",
                "status": "open",
                "priority": 1,
                "issue_type": "bug",
                "created_at": _iso(_NOW - timedelta(days=30)),
                "dependencies": "not-a-list",
            },
            {
                "id": "bu-other",
                "title": "Another P1",
                "status": "open",
                "priority": 1,
                "issue_type": "bug",
                "created_at": _iso(_NOW - timedelta(days=30)),
                "dependencies": ["not-a-dict-edge"],
            },
        ],
    )

    digest = compute_decision_digest(export, now=_NOW)
    assert digest.available is True
    assert digest.escalations == ()


# ---------------------------------------------------------------------------
# compute_decision_digest -- real-shaped fixture
# ---------------------------------------------------------------------------


def test_digest_detects_decision_beads_by_title_marker(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision(
                "bu-v4ipc", title="DECISION REQUIRED (owner): identity model", created_days_ago=6
            ),
            _decision("bu-zhfd0", title="Deploy core migrations [OWNER-GATED]", created_days_ago=6),
            _decision("bu-closed", title="DECISION REQUIRED (owner): old", status="closed"),
            {
                "id": "bu-ordinary",
                "title": "Refactor the widget loader",
                "status": "open",
                "priority": 2,
                "issue_type": "task",
                "created_at": _iso(_NOW - timedelta(days=1)),
                "dependencies": [],
            },
        ],
    )

    digest = compute_decision_digest(export, now=_NOW)
    assert digest.available is True
    ids = {d.id for d in digest.open_decisions}
    assert ids == {"bu-v4ipc", "bu-zhfd0"}
    assert "bu-ordinary" not in ids, "non-decision-marked titles must not match"


def test_digest_detects_decision_beads_by_convention_label(tmp_path):
    """bu-ckkpz.1's `decision` label is the primary marker -- a convention-
    following bead needs no title-marker text at all to be detected."""
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision(
                "bu-labeled",
                title="Re-enable the api-haiku lane?",
                labels=["decision"],
                created_days_ago=3,
            ),
            {
                "id": "bu-other-label",
                "title": "Some unrelated task",
                "status": "open",
                "priority": 2,
                "issue_type": "task",
                "created_at": _iso(_NOW - timedelta(days=1)),
                "dependencies": [],
                "labels": ["backend"],
            },
        ],
    )

    digest = compute_decision_digest(export, now=_NOW)
    ids = {d.id for d in digest.open_decisions}
    assert ids == {"bu-labeled"}


def test_is_decision_bead_label_takes_precedence_over_missing_title_marker():
    assert _is_decision_bead(
        {
            "status": "open",
            "issue_type": "task",
            "title": "Plain title, no marker text",
            "labels": ["decision"],
        }
    )


def test_is_decision_bead_widened_title_markers_match_legacy_fallback():
    """bu-a9p6y's widened fallback shapes, incorporated directly here:
    'ARCHITECTURAL DECISION' and 'OWNER:' title prefixes must still be
    detected via the legacy regex fallback (no `decision` label present)."""
    assert _is_decision_bead(
        {
            "status": "open",
            "issue_type": "task",
            "title": "ARCHITECTURAL DECISION (owner): pick a queue backend",
        }
    )
    assert _is_decision_bead(
        {
            "status": "open",
            "issue_type": "task",
            "title": "OWNER: decide on the retention window",
        }
    )


def test_is_decision_bead_excludes_epics_even_with_label_or_title_marker():
    """An epic is a container for a body of work, not a single decision --
    exclude it regardless of whether it carries the `decision` label or a
    title-marker match (e.g. bu-ckkpz's own title)."""
    assert not _is_decision_bead(
        {
            "status": "open",
            "issue_type": "epic",
            "title": "Owner Decision Desk: decision beads become first-class attention citizens",
            "labels": ["decision"],
        }
    )
    assert not _is_decision_bead(
        {
            "status": "open",
            "issue_type": "epic",
            "title": "DECISION REQUIRED (owner): epic-level rollup",
        }
    )


def test_is_deploy_bead_excludes_epics():
    assert not _is_deploy_bead(
        {
            "status": "open",
            "issue_type": "epic",
            "title": "Deploy readiness epic",
        }
    )
    assert _is_deploy_bead(
        {
            "status": "open",
            "issue_type": "task",
            "title": "Deploy readiness epic",
        }
    )


def test_digest_orders_open_decisions_oldest_first(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-young", created_days_ago=2),
            _decision("bu-old", created_days_ago=20),
            _decision("bu-mid", created_days_ago=8),
        ],
    )
    digest = compute_decision_digest(export, now=_NOW)
    assert [d.id for d in digest.open_decisions] == ["bu-old", "bu-mid", "bu-young"]


def test_digest_escalates_p1_bug_blocked_over_48h(tmp_path):
    """Mirrors the real bu-wzbu9 (P1 bug) -> bu-v4ipc (decision) `blocks` edge."""
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=6),
            _blocker(
                "bu-wzbu9",
                title="Silent message loss",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=140,  # ~5.8 days, well over 48h
            ),
        ],
    )
    digest = compute_decision_digest(export, now=_NOW)
    assert len(digest.escalations) == 1
    hit = digest.escalations[0]
    assert hit.decision_id == "bu-v4ipc"
    assert hit.blocked_id == "bu-wzbu9"
    assert hit.blocked_kind == "p1_bug"
    assert hit.block_age >= timedelta(hours=140)


def test_digest_escalates_deploy_bead_blocked_over_48h(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=6),
            _blocker(
                "bu-zhfd0",
                title="Deploy pending core migrations",
                issue_type="task",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=72,
            ),
        ],
    )
    digest = compute_decision_digest(export, now=_NOW)
    assert len(digest.escalations) == 1
    assert digest.escalations[0].blocked_kind == "deploy"


def test_digest_does_not_escalate_under_48h(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=6),
            _blocker(
                "bu-wzbu9",
                title="Silent message loss",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=10,
            ),
        ],
    )
    digest = compute_decision_digest(export, now=_NOW)
    assert digest.escalations == ()


def test_digest_ignores_p2_bug_and_non_deploy_task_blocks(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=6),
            _blocker(
                "bu-p2",
                title="Minor bug",
                issue_type="bug",
                priority=2,
                blocks_id="bu-v4ipc",
                edge_age_hours=140,
            ),
            _blocker(
                "bu-task",
                title="Some unrelated task",
                issue_type="task",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=140,
            ),
        ],
    )
    digest = compute_decision_digest(export, now=_NOW)
    assert digest.escalations == (), "only P1 bugs and deploy-titled beads should escalate"


# ---------------------------------------------------------------------------
# Message composition
# ---------------------------------------------------------------------------


def test_compose_weekly_digest_message_includes_count_and_oldest_age(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export, [_decision("bu-a", created_days_ago=10), _decision("bu-b", created_days_ago=3)]
    )
    digest = compute_decision_digest(export, now=_NOW)
    message = _compose_weekly_digest_message(digest)
    assert "2 decisions waiting" in message
    assert "oldest 10d" in message
    assert "bu-a" in message and "bu-b" in message


def test_compose_escalation_message_names_decision_and_blocked():
    from butlers.jobs.decision_review import EscalationHit

    hit = EscalationHit(
        decision_id="bu-v4ipc",
        decision_title="DECISION REQUIRED (owner): identity model",
        blocked_id="bu-wzbu9",
        blocked_title="Silent message loss",
        blocked_kind="p1_bug",
        blocked_since=_NOW - timedelta(hours=140),
        block_age=timedelta(hours=140),
    )
    message = _compose_escalation_message(hit)
    assert "bu-v4ipc" in message
    assert "bu-wzbu9" in message
    assert "P1 bug" in message
    assert "140h" in message or "5d" in message


# ---------------------------------------------------------------------------
# _check_suppression
# ---------------------------------------------------------------------------


async def test_check_suppression_quiet_hours():
    pool = object()
    with (
        patch(
            "butlers.jobs.decision_review.get_approvals_policy_quiet_hours",
            new=AsyncMock(
                return_value={"quiet_start_hour": 0, "quiet_end_hour": 23, "timezone": "UTC"}
            ),
        ),
        patch(
            "butlers.jobs.decision_review.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
        patch("butlers.jobs.decision_review.should_suppress_by_policy", return_value=True),
    ):
        assert await _check_suppression(pool) == "quiet_hours"


async def test_check_suppression_context_bus():
    pool = object()
    with (
        patch(
            "butlers.jobs.decision_review.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.decision_review.get_suppressing_context_signal",
            new=AsyncMock(return_value="dnd"),
        ),
    ):
        assert await _check_suppression(pool) == "context_bus:dnd"


async def test_check_suppression_none_when_clear():
    pool = object()
    with (
        patch(
            "butlers.jobs.decision_review.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.decision_review.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
    ):
        assert await _check_suppression(pool) is None


# ---------------------------------------------------------------------------
# _deliver -- bu-hmdqz.3: genuine terminal failures record outcome='failed',
# not 'deferred' (reserved for a benign hold that resolves on its own).
# ---------------------------------------------------------------------------


async def test_deliver_no_recipient_records_failed_outcome():
    pool = object()
    with (
        patch("butlers.jobs.decision_review._check_suppression", new=AsyncMock(return_value=None)),
        patch(
            "butlers.jobs.decision_review.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.decision_review.record_attention_event", new=AsyncMock()
        ) as ledger_mock,
    ):
        outcome = await _deliver(pool, message="hi", dedup_key="k1", priority="medium")

    assert outcome == "failed"
    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "failed"
    assert ledger_mock.await_args.kwargs["reason"] == "no_recipient_configured"


async def test_deliver_transport_failure_records_failed_outcome():
    pool = object()
    with (
        patch("butlers.jobs.decision_review._check_suppression", new=AsyncMock(return_value=None)),
        patch(
            "butlers.jobs.decision_review.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "failed", "error": "boom"}),
        ),
        patch(
            "butlers.jobs.decision_review.record_attention_event", new=AsyncMock()
        ) as ledger_mock,
    ):
        outcome = await _deliver(pool, message="hi", dedup_key="k1", priority="medium")

    assert outcome == "failed"
    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "failed"
    assert ledger_mock.await_args.kwargs["reason"] == "delivery_error:boom"


# ---------------------------------------------------------------------------
# run_decision_review_digest
# ---------------------------------------------------------------------------


async def test_run_digest_unavailable_records_deferred_ledger_event_and_sends_nothing(tmp_path):
    pool = AsyncMock()
    missing = tmp_path / "missing.jsonl"

    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", missing),
        patch(
            "butlers.jobs.decision_review.record_attention_event", new=AsyncMock()
        ) as record_mock,
    ):
        result = await run_decision_review_digest(pool)

    assert result == {"available": False, "reason": "export_missing"}
    record_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["outcome"] == "deferred"
    assert record_mock.await_args.kwargs["reason"] == "data_unavailable:export_missing"


async def test_run_digest_genuine_zero_sends_nothing(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [{"id": "bu-x", "title": "Ordinary task", "status": "open"}])
    pool = AsyncMock()

    with patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export):
        result = await run_decision_review_digest(pool)

    assert result == {"available": True, "open_decisions": 0, "outcome": "no_decisions"}


async def test_run_digest_delivers_when_decisions_open(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [_decision("bu-v4ipc", created_days_ago=6)])
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export),
        patch("butlers.jobs.decision_review._check_suppression", new=AsyncMock(return_value=None)),
        patch(
            "butlers.jobs.decision_review.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "sent"}),
        ) as deliver_mock,
        patch("butlers.jobs.decision_review.record_attention_event", new=AsyncMock()),
    ):
        result = await run_decision_review_digest(pool)

    assert result == {"available": True, "open_decisions": 1, "outcome": "delivered"}
    deliver_mock.assert_awaited_once()
    assert "bu-v4ipc" in deliver_mock.await_args.kwargs["message"]


# ---------------------------------------------------------------------------
# run_decision_escalation_check
# ---------------------------------------------------------------------------


async def test_run_escalation_check_skips_already_escalated(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=6),
            _blocker(
                "bu-wzbu9",
                title="Silent message loss",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=140,
            ),
        ],
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"1": 1})  # already-escalated marker present

    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export),
        patch("butlers.jobs.decision_review.audit_router.append", new=AsyncMock()) as append_mock,
    ):
        result = await run_decision_escalation_check(pool)

    assert result == {
        "available": True,
        "escalations_found": 1,
        "escalated": 0,
        "skipped": 1,
    }
    append_mock.assert_not_awaited()


async def test_run_escalation_check_delivers_and_records_marker(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=6),
            _blocker(
                "bu-wzbu9",
                title="Silent message loss",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=140,
            ),
        ],
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)  # no prior escalation marker

    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export),
        patch("butlers.jobs.decision_review._check_suppression", new=AsyncMock(return_value=None)),
        patch(
            "butlers.jobs.decision_review.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "sent"}),
        ),
        patch("butlers.jobs.decision_review.record_attention_event", new=AsyncMock()),
        patch("butlers.jobs.decision_review.audit_router.append", new=AsyncMock()) as append_mock,
    ):
        result = await run_decision_escalation_check(pool)

    assert result == {
        "available": True,
        "escalations_found": 1,
        "escalated": 1,
        "skipped": 0,
    }
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[2] == "decision_escalation_notified"
    assert append_mock.await_args.kwargs["target"] == "bu-v4ipc:bu-wzbu9"


async def test_run_escalation_check_one_failure_does_not_sink_the_others(tmp_path):
    """A per-hit exception (e.g. a transient DB error) must be caught and
    logged so the remaining escalations in the same tick still get processed,
    instead of the first failure aborting the whole loop."""
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=6),
            _blocker(
                "bu-wzbu9",
                title="Silent message loss",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=140,
            ),
            _blocker(
                "bu-other",
                title="Another P1",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=140,
            ),
        ],
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[Exception("simulated transient DB error"), None])

    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export),
        patch("butlers.jobs.decision_review._check_suppression", new=AsyncMock(return_value=None)),
        patch(
            "butlers.jobs.decision_review.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "sent"}),
        ),
        patch("butlers.jobs.decision_review.record_attention_event", new=AsyncMock()),
        patch("butlers.jobs.decision_review.audit_router.append", new=AsyncMock()) as append_mock,
    ):
        result = await run_decision_escalation_check(pool)

    assert result["available"] is True
    assert result["escalations_found"] == 2
    assert result["escalated"] == 1
    append_mock.assert_awaited_once()


async def test_run_escalation_check_suppressed_does_not_write_marker(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=6),
            _blocker(
                "bu-wzbu9",
                title="Silent message loss",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=140,
            ),
        ],
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export),
        patch(
            "butlers.jobs.decision_review._check_suppression",
            new=AsyncMock(return_value="quiet_hours"),
        ),
        patch("butlers.jobs.decision_review.record_attention_event", new=AsyncMock()),
        patch("butlers.jobs.decision_review.audit_router.append", new=AsyncMock()) as append_mock,
    ):
        result = await run_decision_escalation_check(pool)

    assert result["escalated"] == 0
    append_mock.assert_not_awaited()
