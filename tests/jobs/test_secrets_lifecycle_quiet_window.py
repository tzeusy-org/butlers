"""bu-kqnum.3.8: owner-attention policy holds park ONE aggregate envelope per quiet window.

The scan's collaborators are replaced at the module boundary and the deferred queue is a small
in-memory model of ``replace_pending_deferred_notification``. The real SQL (atomic supersede under
concurrency) is covered by ``test_secrets_lifecycle_quiet_window_pg.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.temporal import delivery_db
from butlers.jobs import secrets_lifecycle as lifecycle
from butlers.jobs.secrets_lifecycle import (
    CredentialSnapshot,
    _compose_window_message,
    _focus_fragment,
    _QuietWindow,
    _window_marker,
    run_secrets_lifecycle_check,
)

pytestmark = pytest.mark.unit

_WINDOW = _QuietWindow(
    start=datetime(2026, 10, 18, 22, 0, tzinfo=UTC),
    deliver_at=datetime(2026, 10, 19, 7, 0, tzinfo=UTC),
)


def _cred(state):
    return CredentialSnapshot(key="s:A", family="system", label="Alpha", state=state)


_A = CredentialSnapshot(key="s:A", family="system", label="Alpha", state="expired")
_B = CredentialSnapshot(key="s:B", family="system", label="Beta", state="expiring")


class _Db:
    def __init__(self):
        self.shared = MagicMock(name="shared_pool")
        self.switchboard = MagicMock(name="switchboard_pool")

    def credential_shared_pool(self):
        return self.shared

    def pool(self, name):
        assert name == "switchboard"
        return self.switchboard

    butler_names: list[str] = []


class _Queue:
    """In-memory model of the deferred queue's replace-pending semantics."""

    def __init__(self):
        self.rows: list[dict] = []

    async def replace(self, pool, *, line_token, message, deliver_at, **_):
        for row in self.rows:
            if row["status"] == "pending" and (
                line_token + "\n" in row["message"] or row["message"].endswith(line_token)
            ):
                row["status"] = "cancelled"
        self.rows.append(
            {
                "id": f"n{len(self.rows)}",
                "message": message,
                "deliver_at": deliver_at,
                "status": "pending",
            }
        )
        return self.rows[-1]["id"]

    def status_of(self, envelope_id):
        return next((r["status"] for r in self.rows if r["id"] == envelope_id), None)

    def flush(self):
        """The ordinary deferred flush: every pending envelope is delivered."""
        for row in self.pending:
            row["status"] = "delivered"

    @property
    def pending(self):
        return [row for row in self.rows if row["status"] == "pending"]


class _Harness:
    def __init__(self, *, suppression="quiet_hours", window=_WINDOW, recipient="chat-1"):
        self.db = _Db()
        self.db.shared.fetchrow = AsyncMock(side_effect=self._audit_row)
        self.queue = _Queue()
        self.ledger = AsyncMock()
        self.audit = AsyncMock(side_effect=self._append_audit)
        self.snapshots: list[CredentialSnapshot] = [_A, _B]
        self.suppression = suppression
        self.window = window
        self.recipient = recipient
        self.audit_log: list[tuple[str, str, str]] = []  # (action, key, note), oldest first
        self.deliver = AsyncMock(return_value={"status": "ok", "notification_id": "d1"})

    async def scan(self):
        async def collect(*_a, **_k):
            return list(self.snapshots)

        patches = [
            patch.object(lifecycle, "_collect_snapshots", collect),
            patch.object(lifecycle, "_delivery_preferences_deferral", AsyncMock(return_value=None)),
            patch.object(lifecycle, "_check_suppression", AsyncMock(return_value=self.suppression)),
            patch.object(lifecycle, "_policy_quiet_window", AsyncMock(return_value=self.window)),
            patch.object(
                lifecycle,
                "resolve_owner_telegram_recipient",
                AsyncMock(return_value=self.recipient),
            ),
            patch.object(lifecycle, "record_attention_event", self.ledger),
            patch.object(lifecycle.audit_router, "append", self.audit),
            patch.object(lifecycle, "replace_pending_deferred_notification", self.queue.replace),
            patch.object(lifecycle, "_parked_envelope_status", AsyncMock(side_effect=self._status)),
            patch("butlers.tools.switchboard.notification.deliver.deliver", self.deliver),
        ]
        for p in patches:
            p.start()
        try:
            return await run_secrets_lifecycle_check(self.db)
        finally:
            for p in patches:
                p.stop()

    async def _audit_row(self, _sql, key, actions):
        """The newest ``public.audit_log`` row for ``key`` among ``actions`` (str or list)."""
        wanted = [actions] if isinstance(actions, str) else actions
        rows = [(a, n) for a, k, n in self.audit_log if k == key and a in wanted]
        return {"action": rows[-1][0], "note": rows[-1][1]} if rows else None

    async def _append_audit(self, _pool, _actor, action, *, target, note, **_):
        self.audit_log.append((action, target, note))

    async def _status(self, _db, envelope_id):
        return self.queue.status_of(envelope_id)

    def outcomes(self):
        return [call.kwargs["outcome"] for call in self.ledger.await_args_list]


async def test_a_night_of_scans_leaves_one_envelope_naming_both_credentials():
    harness = _Harness()

    summaries = [await harness.scan() for _ in range(3)]

    (pending,) = harness.queue.pending
    assert "Alpha" in pending["message"] and "Beta" in pending["message"]
    assert pending["deliver_at"] == _WINDOW.deliver_at
    assert [s["deferred"] for s in summaries] == [2, 2, 2]
    assert all(s["suppressed"] == 0 and s["errors"] == 0 for s in summaries)
    harness.deliver.assert_not_awaited()


async def test_every_scan_records_deferred_never_suppressed_with_the_envelope_ref():
    harness = _Harness()

    await harness.scan()
    await harness.scan()

    assert harness.outcomes() == ["deferred"] * 4
    reasons = {call.kwargs["reason"] for call in harness.ledger.await_args_list}
    assert reasons == {"owner_attention_quiet_hours"}
    assert all(call.kwargs["notification_ref"] for call in harness.ledger.await_args_list)


async def test_a_later_scan_with_an_extra_credential_supersedes_with_both():
    harness = _Harness()
    harness.snapshots = [_A]
    await harness.scan()
    (first,) = harness.queue.pending
    assert "Beta" not in first["message"]

    harness.snapshots = [_A, _B]
    await harness.scan()

    (latest,) = harness.queue.pending
    assert latest["id"] != first["id"]
    assert "Alpha" in latest["message"] and "Beta" in latest["message"]


async def test_the_next_window_starts_a_new_envelope():
    harness = _Harness()
    await harness.scan()
    harness.window = _QuietWindow(
        start=datetime(2026, 10, 19, 22, 0, tzinfo=UTC),
        deliver_at=datetime(2026, 10, 20, 7, 0, tzinfo=UTC),
    )

    await harness.scan()

    assert len(harness.queue.pending) == 2


async def test_the_parked_state_is_recorded_for_every_held_credential():
    harness = _Harness()

    await harness.scan()

    (envelope,) = harness.queue.pending
    notes = {
        call.kwargs["target"]: call.kwargs["note"]
        for call in harness.audit.await_args_list
        if call.args[2] == lifecycle._LIFECYCLE_PARKED_ACTION
    }
    assert notes == {"s:A": f"expired|{envelope['id']}", "s:B": f"expiring|{envelope['id']}"}
    # Parking never advances the delivery debounce marker.
    assert all(
        call.args[2] != lifecycle._LIFECYCLE_NOTIFIED_ACTION
        for call in harness.audit.await_args_list
    )


@pytest.mark.parametrize(
    ("suppression", "window"),
    [
        ("context_bus:dnd", _WINDOW),  # context-only suppression
        ("quiet_hours", None),  # policy window could not be resolved: fail open to the drop
    ],
)
async def test_context_only_or_unresolved_suppression_still_records_and_drops(suppression, window):
    harness = _Harness(suppression=suppression, window=window)

    summary = await harness.scan()

    assert harness.queue.rows == []
    assert harness.outcomes() == ["suppressed", "suppressed"]
    assert (summary["suppressed"], summary["deferred"]) == (2, 0)


@pytest.mark.parametrize("flush", [False, True])
async def test_a_parked_state_is_not_re_alerted_after_the_window(flush):
    harness = _Harness()
    await harness.scan()
    if flush:
        harness.queue.flush()
    harness.suppression = None

    summary = await harness.scan()

    harness.deliver.assert_not_awaited()
    assert (summary["delivered"], summary["errors"]) == (0, 0)


async def test_a_cancelled_parked_envelope_falls_back_to_direct_delivery():
    harness = _Harness()
    harness.snapshots = [_A]
    await harness.scan()
    harness.queue.rows[0]["status"] = "cancelled"
    harness.suppression = None

    summary = await harness.scan()

    harness.deliver.assert_awaited_once()
    assert summary["delivered"] == 1


async def test_a_different_state_than_the_parked_one_is_delivered():
    harness = _Harness()
    harness.snapshots = [
        CredentialSnapshot(key="s:A", family="system", label="Alpha", state="expiring")
    ]
    await harness.scan()
    harness.queue.flush()
    harness.suppression = None
    harness.snapshots = [_A]  # parked as expiring, now expired

    await harness.scan()

    harness.deliver.assert_awaited_once()


async def test_a_credential_dropped_from_a_later_aggregate_is_not_masked():
    harness = _Harness()
    await harness.scan()  # aggregate {A, B}
    harness.snapshots = [_B]  # A drops out (e.g. a degraded per-butler fetch)
    await harness.scan()  # aggregate {B} supersedes {A, B}
    harness.queue.flush()  # the flush delivers {B}
    harness.suppression = None
    harness.snapshots = [_A, _B]

    summary = await harness.scan()

    # B rides the delivered aggregate; A never did, so it reaches the owner directly.
    assert harness.deliver.await_count == 1
    assert "Alpha" in harness.deliver.await_args.kwargs["message"]
    assert summary["delivered"] == 1


async def test_a_direct_delivery_after_the_park_supersedes_the_parked_marker():
    expiring = CredentialSnapshot(key="s:A", family="system", label="Alpha", state="expiring")
    harness = _Harness()
    harness.snapshots = [expiring]
    await harness.scan()  # expiring parked in the window
    harness.queue.flush()
    harness.suppression = None
    harness.snapshots = [_A]
    await harness.scan()  # expired is a different state: delivered directly
    assert harness.deliver.await_count == 1

    harness.snapshots = [expiring]  # renewed, then expiring again outside any window
    await harness.scan()

    assert harness.deliver.await_count == 2


async def test_a_newer_park_supersedes_an_older_notified_marker():
    harness = _Harness(suppression=None)
    harness.snapshots = [_cred("expiring")]
    await harness.scan()  # expiring delivered directly
    assert harness.deliver.await_count == 1

    harness.suppression = "quiet_hours"
    harness.snapshots = [_cred("expired")]
    await harness.scan()  # expired parked overnight, then flushed
    harness.queue.flush()
    harness.suppression = None
    await harness.scan()  # the parked expired state is not re-alerted
    assert harness.deliver.await_count == 1

    harness.snapshots = [_cred("expiring")]  # renewed; the next cycle must not be debounced
    await harness.scan()
    assert harness.deliver.await_count == 2

    harness.snapshots = [_cred("expired")]
    await harness.scan()
    assert harness.deliver.await_count == 3


async def test_a_failing_parked_marker_write_does_not_stop_the_other_credentials():
    harness = _Harness()
    harness.audit.side_effect = [RuntimeError("audit down"), None]

    summary = await harness.scan()

    assert summary["deferred"] == 2
    assert harness.audit.await_count == 2


async def test_no_recipient_records_failed_and_parks_nothing():
    harness = _Harness(recipient=None)

    summary = await harness.scan()

    assert harness.queue.rows == []
    assert harness.outcomes() == ["failed", "failed"]
    assert {c.kwargs["reason"] for c in harness.ledger.await_args_list} == {
        "no_recipient_configured"
    }
    assert (summary["errors"], summary["deferred"]) == (2, 0)


async def test_a_queue_failure_is_failed_and_retryable_not_deferred():
    harness = _Harness()
    harness.queue.replace = AsyncMock(side_effect=RuntimeError("queue down"))

    summary = await harness.scan()

    assert harness.outcomes() == ["failed", "failed"]
    assert {c.kwargs["reason"] for c in harness.ledger.await_args_list} == {
        "quiet_window_queue_failure_retryable"
    }
    assert (summary["errors"], summary["deferred"]) == (2, 0)
    assert harness.audit.await_args_list == []  # no parked marker without an envelope


def test_the_aggregate_is_deterministic_and_never_carries_a_per_credential_focus_link():
    marker = _window_marker("http://dash", _WINDOW)

    forward = _compose_window_message([_A, _B], marker)
    reverse = _compose_window_message([_B, _A], marker)

    assert forward == reverse
    assert forward.endswith("\n" + marker)
    # A credential's own retry supersede matches its focus fragment as a substring, so the
    # aggregate must not contain one.
    assert "focus=" not in forward
    assert _focus_fragment("s:A") not in forward


async def test_replace_takes_the_advisory_lock_before_cancel_and_insert():
    calls: list[str] = []

    class Conn:
        async def execute(self, sql, *args):
            calls.append("lock" if "pg_advisory_xact_lock" in sql else "cancel")
            return "UPDATE 0"

        async def fetchval(self, sql, *args):
            calls.append("insert")
            return "00000000-0000-0000-0000-000000000001"

        def transaction(self):
            outer = calls

            class Tx:
                async def __aenter__(self):
                    outer.append("begin")

                async def __aexit__(self, *exc):
                    outer.append("commit")

            return Tx()

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    await delivery_db.replace_pending_deferred_notification(
        Pool(),
        butler_name="switchboard",
        line_token="tok",
        channel="telegram",
        message="m\ntok",
        priority="medium",
        envelope={},
        deliver_at=_WINDOW.deliver_at,
    )

    assert calls == ["begin", "lock", "cancel", "insert", "commit"]
