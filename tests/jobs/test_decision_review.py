"""Tests for butlers.jobs.decision_review (bu-ckkpz.4, "Owner Decision Desk").

Covers:
- compute_decision_digest: missing/stale/unreadable export -> available=False
  (never a fabricated all-clear); real fixture -> correct decision detection
  (by the `decision` label; the legacy title-marker fallback was retired in
  bu-uo37y), P1-bug/deploy escalation detection via `blocks` dependency edges,
  and the 48h age threshold.
- Message composition: weekly digest header + escalation message shape.
- _check_suppression: mirrors notify()'s quiet-hours + context-bus gate.
- run_decision_review_digest / run_decision_escalation_check: degraded-data
  path records a ledger event and sends nothing; a genuine zero is a real
  all-clear; delivery + debounce-via-audit_log branches.

No real database required -- pools are mocked.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.jobs.decision_review import (
    DecisionLintResult,
    _check_suppression,
    _compose_escalation_message,
    _compose_lint_violation_message,
    _compose_weekly_digest_message,
    _deliver,
    _is_decision_bead,
    _is_deploy_bead,
    _run_unlabeled_marker_lint,
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
    # Stamp the export's mtime to the tests' fixed reference clock (_NOW),
    # which every compute_decision_digest(..., now=_NOW) call site in this
    # file uses as `checked_at`. The staleness check compares checked_at
    # against the file's kernel mtime, and faketime offsets this process's
    # datetime.now() but not the kernel -- using the process clock here would
    # make freshness depend on how far real/faked wall-clock time has drifted
    # from the fixed _NOW anchor. Anchoring to _NOW directly keeps the export
    # "just written" relative to the tests' own clock regardless of when or
    # under what faketime offset the suite actually runs. Tests that want a
    # stale file re-utime it afterward.
    now_ts = _NOW.timestamp()
    os.utime(path, (now_ts, now_ts))


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _decision(id_, *, title="DECISION REQUIRED (owner): pick one", created_days_ago=10, **kw):
    # bu-uo37y: the runtime title-marker fallback is retired, so a decision bead
    # is classified solely by the `decision` label. Default fixtures to a labeled
    # decision (the fleet's current reality); tests that need an UNLABELED bead
    # pass labels=[] (or their own label list) via **kw to override.
    return {
        "id": id_,
        "title": title,
        "status": "open",
        "priority": 1,
        "issue_type": "task",
        "created_at": _iso(_NOW - timedelta(days=created_days_ago)),
        "dependencies": [],
        "labels": ["decision"],
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


def _lint_success(violations: list[dict] | None = None) -> DecisionLintResult:
    """A successful lint boundary result for digest-call-site tests."""
    return DecisionLintResult(True, None, tuple(violations or ()))


# ---------------------------------------------------------------------------
# compute_decision_digest -- degraded-data paths
# ---------------------------------------------------------------------------


def test_digest_unavailable_when_export_missing(tmp_path):
    digest = compute_decision_digest(tmp_path / "does-not-exist.jsonl", now=_NOW)
    assert digest.available is False
    assert digest.unavailable_reason == "export_missing"
    assert digest.open_decisions == ()
    assert digest.escalations == ()
    assert digest.export_as_of is None


def test_digest_unavailable_when_export_stale(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [_decision("bu-a")])
    import os

    stale_mtime = (_NOW - timedelta(days=30)).timestamp()
    os.utime(export, (stale_mtime, stale_mtime))

    digest = compute_decision_digest(export, now=_NOW)
    assert digest.available is False
    assert digest.unavailable_reason == "export_stale"
    # bu-hmdqz.6: export_as_of is still populated on the stale branch -- a
    # caller needs the true age precisely when the data is untrustworthy.
    assert digest.export_as_of is not None
    assert digest.export_as_of.timestamp() == pytest.approx(stale_mtime, abs=1)


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
    assert digest.export_as_of is not None


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


def test_digest_ignores_unlabeled_title_marker_beads(tmp_path):
    """bu-uo37y: the legacy runtime title-marker fallback is retired. A bead
    whose TITLE carries a legacy marker but which lacks the `decision` label is
    NOT classified as a decision at runtime -- only the label counts. (Such a
    bead is instead surfaced to the owner by the digest's unlabeled-marker lint,
    covered separately.)"""
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            # Labeled decision -> detected.
            _decision("bu-labeled", title="Re-enable the api-haiku lane?", created_days_ago=6),
            # Title marker but NO `decision` label -> NOT detected (fallback retired).
            {
                "id": "bu-v4ipc",
                "title": "DECISION REQUIRED (owner): identity model",
                "status": "open",
                "priority": 1,
                "issue_type": "task",
                "created_at": _iso(_NOW - timedelta(days=6)),
                "dependencies": [],
            },
            {
                "id": "bu-zhfd0",
                "title": "Deploy core migrations [OWNER-GATED]",
                "status": "open",
                "priority": 1,
                "issue_type": "task",
                "created_at": _iso(_NOW - timedelta(days=6)),
                "dependencies": [],
            },
        ],
    )

    digest = compute_decision_digest(export, now=_NOW)
    assert digest.available is True
    ids = {d.id for d in digest.open_decisions}
    assert ids == {"bu-labeled"}
    assert "bu-v4ipc" not in ids, "unlabeled title-marker beads must not match at runtime"
    assert "bu-zhfd0" not in ids


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


def test_is_decision_bead_detected_by_label():
    """The `decision` label alone classifies a decision bead -- a plain,
    non-marker title is fine (bu-ckkpz.1)."""
    assert _is_decision_bead(
        {
            "status": "open",
            "issue_type": "task",
            "title": "Plain title, no marker text",
            "labels": ["decision"],
        }
    )


def test_is_decision_bead_ignores_title_markers_without_label():
    """bu-uo37y: the legacy title-marker fallback is retired, so beads whose
    titles carry a marker ('ARCHITECTURAL DECISION', 'OWNER:', 'DECISION
    REQUIRED', ...) but which lack the `decision` label are NOT classified as
    decisions at runtime."""
    for title in (
        "ARCHITECTURAL DECISION (owner): pick a queue backend",
        "OWNER: decide on the retention window",
        "DECISION REQUIRED (owner): identity model",
    ):
        assert not _is_decision_bead({"status": "open", "issue_type": "task", "title": title}), (
            title
        )
        # A meta/dev bead that merely QUOTES the markers is likewise inert now
        # (moots the runtime half of bu-d4lkb's self-match concern).
    assert not _is_decision_bead(
        {
            "status": "open",
            "issue_type": "task",
            "title": 'Doc the "DECISION REQUIRED" / "OWNER:" marker strings',
        }
    )


def test_is_decision_bead_excludes_epics_even_with_label():
    """An epic is a container for a body of work, not a single decision --
    exclude it even when it carries the `decision` label (e.g. bu-ckkpz's own
    container epic)."""
    assert not _is_decision_bead(
        {
            "status": "open",
            "issue_type": "epic",
            "title": "Owner Decision Desk: decision beads become first-class attention citizens",
            "labels": ["decision"],
        }
    )


def test_is_deploy_bead_includes_epics():
    # bu-pnofc: unlike _is_decision_bead, a deploy-blocked epic ("Epic: ship v2
    # [deploy pending decision]") IS a valid deploy bead. Silently dropping a
    # >48h deploy-blocked escalation is strictly worse than an occasional extra
    # escalation on a container, so the epic exclusion was reverted here (only
    # here — _is_decision_bead still excludes epics, guarded by the test above).
    assert _is_deploy_bead(
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
    # The status gate is unchanged: a closed epic is still not a deploy bead.
    assert not _is_deploy_bead(
        {
            "status": "closed",
            "issue_type": "epic",
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


def test_digest_escalates_deploy_blocked_epic_over_48h(tmp_path):
    # bu-pnofc: a container epic on the deploy-blocked side of a blocks edge now
    # escalates like any other deploy bead (the epic exclusion was reverted in
    # _is_deploy_bead). Before, this >48h deploy-blocked escalation was silently
    # dropped.
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=6),
            _blocker(
                "bu-epicdeploy",
                title="Epic: ship v2 [deploy pending decision]",
                issue_type="epic",
                priority=2,  # deploy detection is title-based, not P1-priority-based
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
        patch("butlers.jobs.decision_review.is_policy_quiet_now", return_value=True),
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


async def test_run_digest_unavailable_records_failed_ledger_event_and_sends_nothing(tmp_path):
    # bu-xnusv: a beads-export-unavailable skip is a terminal FAILED run, not a
    # "deferred" hold — "deferred" is reserved for a notification queued for
    # redelivery. The distinct cause lives in the queryable ``reason`` column.
    pool = AsyncMock()
    missing = tmp_path / "missing.jsonl"

    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", missing),
        patch(
            "butlers.jobs.decision_review.record_attention_event", new=AsyncMock()
        ) as record_mock,
    ):
        result = await run_decision_review_digest(pool, _now=_NOW)

    assert result == {"available": False, "reason": "export_missing"}
    record_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["outcome"] == "failed"
    assert record_mock.await_args.kwargs["reason"] == "data_unavailable:export_missing"


async def test_run_digest_genuine_zero_sends_nothing(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [{"id": "bu-x", "title": "Ordinary task", "status": "open"}])
    pool = AsyncMock()

    with patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export):
        result = await run_decision_review_digest(pool, _now=_NOW)

    assert result == {
        "available": True,
        "open_decisions": 0,
        "outcome": "no_decisions",
        "lint_violations": 0,
    }


async def test_run_digest_delivers_when_decisions_open(tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [_decision("bu-v4ipc", created_days_ago=6)])
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export),
        patch(
            "butlers.jobs.decision_review._run_unlabeled_marker_lint",
            return_value=_lint_success(),
        ),
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
        result = await run_decision_review_digest(pool, _now=_NOW)

    assert result == {
        "available": True,
        "open_decisions": 1,
        "outcome": "delivered",
        "lint_violations": 0,
    }
    deliver_mock.assert_awaited_once()
    assert "bu-v4ipc" in deliver_mock.await_args.kwargs["message"]


async def test_run_digest_lint_violations_deliver_separate_low_priority_message(tmp_path):
    """bu-hmdqz.6: a genuine zero for open_decisions must not suppress the
    convention-lint nudge -- unlabeled, marker-matched beads still need a
    migration reminder even in an otherwise-quiet week."""
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [{"id": "bu-x", "title": "Ordinary task", "status": "open"}])
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    fake_violations = [{"id": "bu-w6jca", "title": "ARCHITECTURAL DECISION (owner): ..."}]

    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export),
        patch(
            "butlers.jobs.decision_review._run_unlabeled_marker_lint",
            return_value=_lint_success(fake_violations),
        ),
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
        result = await run_decision_review_digest(pool, _now=_NOW)

    assert result == {
        "available": True,
        "open_decisions": 0,
        "outcome": "no_decisions",
        "lint_violations": 1,
    }
    deliver_mock.assert_awaited_once()
    assert "bu-w6jca" in deliver_mock.await_args.kwargs["message"]


def test_run_unlabeled_marker_lint_real_subprocess_wiring(tmp_path):
    """Exercises the real subprocess call into scripts/lint_decision_beads.py
    (no mocking) so the wiring itself -- script path resolution, --json
    parsing, and live-status selection -- is verified, not just the mocked
    call sites above."""
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            # Both records are malformed labeled decisions. The scheduled
            # full-export marker path must report only the live one, never
            # resurrect the closed historical record as a weekly owner alert.
            _decision(
                "bu-open-malformed",
                title="ARCHITECTURAL DECISION (owner): pick a schema",
            ),
            _decision(
                "bu-closed-malformed",
                title="ARCHITECTURAL DECISION (owner): historical schema",
                status="closed",
            ),
            {"id": "bu-ordinary", "title": "fix a typo", "status": "open"},
        ],
    )

    lint_result = _run_unlabeled_marker_lint(export)

    assert lint_result.available is True
    assert lint_result.unavailable_reason is None
    assert [v["id"] for v in lint_result.violations] == ["bu-open-malformed"]
    assert any("metadata.decision" in v for v in lint_result.violations[0]["violations"])


def test_run_unlabeled_marker_lint_missing_script_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.decision_review._LINT_SCRIPT_PATH", tmp_path / "does-not-exist.py"
    )
    lint_result = _run_unlabeled_marker_lint(tmp_path / "export.jsonl")
    assert lint_result.available is False
    assert lint_result.unavailable_reason == "lint_script_missing"
    assert lint_result.violations == ()


def test_run_unlabeled_marker_lint_missing_input_is_unavailable(tmp_path):
    lint_result = _run_unlabeled_marker_lint(tmp_path / "does-not-exist.jsonl")

    assert lint_result.available is False
    assert lint_result.unavailable_reason == "lint_input_unavailable"
    assert lint_result.violations == ()


def test_run_unlabeled_marker_lint_unreadable_input_is_unavailable(tmp_path):
    """Exercise the real linter subprocess's unreadable-input exit path."""
    unreadable_export = tmp_path / "unreadable.jsonl"
    unreadable_export.write_text("{not valid json", encoding="utf-8")

    lint_result = _run_unlabeled_marker_lint(unreadable_export)

    assert lint_result.available is False
    assert lint_result.unavailable_reason == "lint_input_unavailable"
    assert lint_result.violations == ()


def test_run_unlabeled_marker_lint_unreadable_script_is_unavailable(monkeypatch, tmp_path):
    def _raise_permission_error(*_args, **_kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr("butlers.jobs.decision_review.subprocess.run", _raise_permission_error)

    lint_result = _run_unlabeled_marker_lint(tmp_path / "issues.export.jsonl")

    assert lint_result.available is False
    assert lint_result.unavailable_reason == "lint_subprocess_error:PermissionError"
    assert lint_result.violations == ()


def test_run_unlabeled_marker_lint_undecodable_output_is_unavailable(monkeypatch, tmp_path):
    def _raise_decode_error(*_args, **_kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr("butlers.jobs.decision_review.subprocess.run", _raise_decode_error)

    lint_result = _run_unlabeled_marker_lint(tmp_path / "issues.export.jsonl")

    assert lint_result.available is False
    assert lint_result.unavailable_reason == "lint_subprocess_output_decode_error"
    assert lint_result.violations == ()


def test_run_unlabeled_marker_lint_unexpected_nonzero_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "butlers.jobs.decision_review.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=9, stdout="", stderr="broken runtime"
        ),
    )

    lint_result = _run_unlabeled_marker_lint(tmp_path / "issues.export.jsonl")

    assert lint_result.available is False
    assert lint_result.unavailable_reason == "lint_subprocess_exit:9"
    assert lint_result.violations == ()


def test_run_unlabeled_marker_lint_non_json_stdout_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "butlers.jobs.decision_review.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        ),
    )

    lint_result = _run_unlabeled_marker_lint(tmp_path / "issues.export.jsonl")

    assert lint_result.available is False
    assert lint_result.unavailable_reason == "lint_output_non_json"
    assert lint_result.violations == ()


def test_run_unlabeled_marker_lint_wrong_json_shape_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "butlers.jobs.decision_review.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"not": "a list"}', stderr=""
        ),
    )

    lint_result = _run_unlabeled_marker_lint(tmp_path / "issues.export.jsonl")

    assert lint_result.available is False
    assert lint_result.unavailable_reason == "lint_output_invalid_shape"
    assert lint_result.violations == ()


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        pytest.param(1, "[]", id="exit_one_with_zero_results"),
        pytest.param(
            0,
            '[{"id": "bu-x", "title": "Decision", "ok": true, "violations": ["missing label"]}]',
            id="ok_true_with_string_violation",
        ),
        pytest.param(
            0,
            '[{"id": "bu-x", "title": "Decision", "ok": true, "violations": [123]}]',
            id="non_string_violation",
        ),
        pytest.param(
            0,
            '[{"id": "bu-x", "title": "Decision", "ok": false, "violations": ["missing label"]}]',
            id="exit_zero_with_failing_result",
        ),
    ],
)
def test_run_unlabeled_marker_lint_rejects_semantically_invalid_payload(
    monkeypatch, tmp_path, returncode, stdout
):
    monkeypatch.setattr(
        "butlers.jobs.decision_review.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=""
        ),
    )

    lint_result = _run_unlabeled_marker_lint(tmp_path / "issues.export.jsonl")

    assert lint_result.available is False
    assert lint_result.unavailable_reason == "lint_output_invalid_shape"
    assert lint_result.violations == ()


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        pytest.param(1, "[]", id="exit_one_with_zero_results"),
        pytest.param(
            0,
            '[{"id": "bu-x", "title": "Decision", "ok": true, "violations": [123]}]',
            id="non_string_violation",
        ),
    ],
)
async def test_run_digest_semantically_invalid_lint_payload_is_unavailable_and_records_failed_ledger(
    tmp_path, monkeypatch, returncode, stdout
):
    """Syntactically JSON lint output cannot fabricate a genuine-zero calm."""
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [{"id": "bu-x", "title": "Ordinary task", "status": "open"}])
    pool = AsyncMock()

    monkeypatch.setattr(
        "butlers.jobs.decision_review.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=""
        ),
    )
    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export),
        patch(
            "butlers.jobs.decision_review.record_attention_event", new=AsyncMock()
        ) as record_mock,
    ):
        result = await run_decision_review_digest(pool, _now=_NOW)

    assert result == {"available": False, "reason": "lint_output_invalid_shape"}
    record_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["outcome"] == "failed"
    assert record_mock.await_args.kwargs["reason"] == "data_unavailable:lint_output_invalid_shape"


async def test_run_digest_missing_lint_input_is_unavailable_not_no_decisions(tmp_path, monkeypatch):
    """A readable genuine-zero export cannot hide an unavailable lint script."""
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [{"id": "bu-x", "title": "Ordinary task", "status": "open"}])
    missing_script = tmp_path / "does-not-exist.py"
    pool = AsyncMock()

    monkeypatch.setattr("butlers.jobs.decision_review._LINT_SCRIPT_PATH", missing_script)
    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export),
        patch(
            "butlers.jobs.decision_review.record_attention_event", new=AsyncMock()
        ) as record_mock,
    ):
        result = await run_decision_review_digest(pool, _now=_NOW)

    assert result == {"available": False, "reason": "lint_script_missing"}
    record_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["outcome"] == "failed"
    assert record_mock.await_args.kwargs["reason"] == "data_unavailable:lint_script_missing"


async def test_run_digest_lint_script_preflight_oserror_records_failed_ledger(
    tmp_path, monkeypatch
):
    """A filesystem failure checking the script must not fabricate a calm digest."""
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [{"id": "bu-x", "title": "Ordinary task", "status": "open"}])
    pool = AsyncMock()

    class _UnreadableLintScriptPath:
        def is_file(self) -> bool:
            raise OSError("filesystem unavailable")

    monkeypatch.setattr(
        "butlers.jobs.decision_review._LINT_SCRIPT_PATH", _UnreadableLintScriptPath()
    )
    with (
        patch("butlers.jobs.decision_review._DEFAULT_EXPORT_PATH", export),
        patch(
            "butlers.jobs.decision_review.record_attention_event", new=AsyncMock()
        ) as record_mock,
    ):
        result = await run_decision_review_digest(pool, _now=_NOW)

    assert result == {"available": False, "reason": "lint_script_unavailable:OSError"}
    record_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["outcome"] == "failed"
    assert (
        record_mock.await_args.kwargs["reason"]
        == "data_unavailable:lint_script_unavailable:OSError"
    )


def test_compose_lint_violation_message_lists_ids_and_titles():
    message = _compose_lint_violation_message(
        [{"id": "bu-w6jca", "title": "ARCHITECTURAL DECISION (owner): pick a schema"}]
    )
    assert "bu-w6jca" in message
    assert "1 bead" in message


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
        result = await run_decision_escalation_check(pool, _now=_NOW)

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
        result = await run_decision_escalation_check(pool, _now=_NOW)

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
        result = await run_decision_escalation_check(pool, _now=_NOW)

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
        result = await run_decision_escalation_check(pool, _now=_NOW)

    assert result["escalated"] == 0
    append_mock.assert_not_awaited()
