"""Tests for butlers.jobs.secrets_lifecycle — proactive credential lifecycle scan.

Covers:
- _collect_snapshots: aggregates system/cli/user families into canonical keys,
  excludes cli/cli-auth rows from the system pass (they're the cli family),
  and excludes provider-managed Spotify rows from lifecycle notifications.
- _last_notified_state: reads the debounce marker back from public.audit_log.
- _check_suppression: mirrors notify()'s quiet-hours + context-bus gate.
- _compose_message: deep link always present; re-authorize URL only for OAuth
  providers.
- run_secrets_lifecycle_check: debounce (no repeat notify for the same
  state), a genuinely new transition delivers + records the ledger + writes
  the debounce marker, a suppressed attempt does NOT write the debounce
  marker (so it retries next scan), and graceful no-ops when there's no
  shared pool / no owner recipient.

No real database required — DatabaseManager and its pools are faked/mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from butlers.api.routers.secrets_v2 import CliRuntime, SystemSecret, UserSecret
from butlers.jobs.secrets_lifecycle import (
    CredentialSnapshot,
    _check_suppression,
    _collect_snapshots,
    _compose_message,
    _delivery_preferences_deferral,
    _focus_fragment,
    _last_notified_state,
    run_secrets_lifecycle_check,
)

pytestmark = pytest.mark.unit


class _FakeDatabaseManager:
    """Minimal stand-in for DatabaseManager: butler_names / pool / credential_shared_pool."""

    def __init__(self, *, butler_pools: dict[str, object], shared_pool: object | None):
        self._butler_pools = butler_pools
        self._shared_pool = shared_pool

    @property
    def butler_names(self) -> list[str]:
        return list(self._butler_pools)

    def pool(self, name: str):
        try:
            return self._butler_pools[name]
        except KeyError:
            raise KeyError(name) from None

    def credential_shared_pool(self):
        if self._shared_pool is None:
            raise KeyError("shared_pool")
        return self._shared_pool


# ---------------------------------------------------------------------------
# _collect_snapshots
# ---------------------------------------------------------------------------


async def test_collect_snapshots_aggregates_all_families_with_canonical_keys():
    finance_pool = object()
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={"finance": finance_pool}, shared_pool=shared_pool)

    async def fake_fetch_system_secrets(pool, butler_name, **kwargs):
        if pool is finance_pool:
            return [SystemSecret(key="FINANCE_KEY", state="ok", butler="finance")]
        if pool is shared_pool:
            return [
                SystemSecret(key="BUTLER_TELEGRAM_TOKEN", state="expiring", butler="shared-public"),
                SystemSecret(
                    key="cli-should-be-excluded",
                    state="ok",
                    butler="shared-public",
                    category="cli",
                ),
            ]
        return []

    async def fake_fetch_cli_secrets(pool):
        return [CliRuntime(key="claude-cli-token", state="failing")]

    async def fake_fetch_user_secrets(pool, *, identity):
        assert identity is None
        return [
            UserSecret(id="u1", entity_id="e1", type="google_oauth_refresh", state="expiring"),
        ]

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._fetch_system_secrets",
            side_effect=fake_fetch_system_secrets,
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._fetch_cli_secrets", side_effect=fake_fetch_cli_secrets
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._fetch_user_secrets",
            side_effect=fake_fetch_user_secrets,
        ),
    ):
        snapshots = await _collect_snapshots(db)

    keys = {s.key: s for s in snapshots}
    assert keys["s:FINANCE_KEY"].family == "system"
    assert keys["s:BUTLER_TELEGRAM_TOKEN"].state == "expiring"
    assert "s:cli-should-be-excluded" not in keys, (
        "cli/cli-auth rows must not double-count in system"
    )
    assert keys["c:claude-cli-token"].family == "cli"
    assert keys["c:claude-cli-token"].state == "failing"
    assert keys["u:google"].family == "user"
    assert keys["u:google"].provider == "google"
    assert keys["u:google"].state == "expiring"


async def test_collect_snapshots_excludes_spotify_system_credentials():
    lifestyle_pool = object()
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={"lifestyle": lifestyle_pool}, shared_pool=shared_pool)

    async def fake_fetch_system_secrets(pool, butler_name, **kwargs):
        if pool is lifestyle_pool:
            return [
                SystemSecret(
                    key="SPOTIFY_ACCESS_TOKEN",
                    state="expired",
                    butler="lifestyle",
                    category="spotify",
                )
            ]
        if pool is shared_pool:
            return [
                SystemSecret(
                    key="SPOTIFY_REFRESH_TOKEN",
                    state="failing",
                    butler="shared-public",
                    category="spotify",
                ),
                SystemSecret(
                    key="BUTLER_TELEGRAM_TOKEN",
                    state="expiring",
                    butler="shared-public",
                    category="messaging",
                ),
            ]
        return []

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._fetch_system_secrets",
            side_effect=fake_fetch_system_secrets,
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._fetch_cli_secrets",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._fetch_user_secrets",
            new=AsyncMock(return_value=[]),
        ),
    ):
        snapshots = await _collect_snapshots(db)

    assert [snapshot.key for snapshot in snapshots] == ["s:BUTLER_TELEGRAM_TOKEN"]


async def test_collect_snapshots_returns_system_only_when_no_shared_pool():
    finance_pool = object()
    db = _FakeDatabaseManager(butler_pools={"finance": finance_pool}, shared_pool=None)

    async def fake_fetch_system_secrets(pool, butler_name, **kwargs):
        return [SystemSecret(key="FINANCE_KEY", state="ok", butler="finance")]

    with patch(
        "butlers.jobs.secrets_lifecycle._fetch_system_secrets",
        side_effect=fake_fetch_system_secrets,
    ):
        snapshots = await _collect_snapshots(db)

    assert [s.key for s in snapshots] == ["s:FINANCE_KEY"]


# ---------------------------------------------------------------------------
# _last_notified_state
# ---------------------------------------------------------------------------


async def test_last_notified_state_returns_note_from_audit_log():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"note": "expiring"})
    result = await _last_notified_state(pool, "u:google")
    assert result == "expiring"


async def test_last_notified_state_none_when_no_row():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    assert await _last_notified_state(pool, "u:google") is None


async def test_last_notified_state_none_on_error():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))
    assert await _last_notified_state(pool, "u:google") is None


# ---------------------------------------------------------------------------
# _check_suppression
# ---------------------------------------------------------------------------


async def test_check_suppression_quiet_hours():
    pool = object()
    with (
        patch(
            "butlers.jobs.secrets_lifecycle.get_approvals_policy_quiet_hours",
            new=AsyncMock(
                return_value={"quiet_start_hour": 0, "quiet_end_hour": 23, "timezone": "UTC"}
            ),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
    ):
        reason = await _check_suppression(pool)
    assert reason == "quiet_hours"


async def test_check_suppression_context_bus():
    pool = object()
    with (
        patch(
            "butlers.jobs.secrets_lifecycle.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.get_suppressing_context_signal",
            new=AsyncMock(return_value="dnd"),
        ),
    ):
        reason = await _check_suppression(pool)
    assert reason == "context_bus:dnd"


async def test_check_suppression_none_when_clear():
    pool = object()
    with (
        patch(
            "butlers.jobs.secrets_lifecycle.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
    ):
        reason = await _check_suppression(pool)
    assert reason is None


# ---------------------------------------------------------------------------
# _compose_message
# ---------------------------------------------------------------------------


def test_compose_message_includes_deep_link_and_reauthorize_for_oauth():
    snapshot = CredentialSnapshot(
        key="u:google", family="user", label="Google", state="expiring", provider="google"
    )
    message = _compose_message(snapshot, "http://localhost:41200")
    assert "http://localhost:41200/secrets?focus=u%3Agoogle" in message
    assert "http://localhost:41200/api/oauth/google/start" in message
    assert "expiring soon" in message


def test_compose_message_omits_reauthorize_for_non_oauth():
    snapshot = CredentialSnapshot(
        key="c:claude-cli-token", family="cli", label="claude-cli-token", state="failing"
    )
    message = _compose_message(snapshot, "http://localhost:41200")
    assert "/api/oauth/" not in message
    assert "failing its health probe" in message


# ---------------------------------------------------------------------------
# run_secrets_lifecycle_check
# ---------------------------------------------------------------------------


async def test_run_secrets_lifecycle_check_debounces_same_state():
    """No delivery and no ledger write when the current state matches the last-notified one."""
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={}, shared_pool=shared_pool)
    snapshot = CredentialSnapshot(key="u:google", family="user", label="Google", state="expiring")

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state",
            new=AsyncMock(return_value="expiring"),
        ) as last_notified,
        patch("butlers.jobs.secrets_lifecycle._check_suppression") as suppression,
    ):
        summary = await run_secrets_lifecycle_check(db)

    last_notified.assert_awaited_once()
    suppression.assert_not_called()
    assert summary == {
        "scanned": 1,
        "attention": 1,
        "delivered": 0,
        "deferred": 0,
        "suppressed": 0,
        "errors": 0,
    }


async def test_run_secrets_lifecycle_check_delivers_on_new_transition():
    """A genuinely new transition delivers, records the ledger, and writes the debounce marker."""
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={}, shared_pool=shared_pool)
    snapshot = CredentialSnapshot(
        key="u:google", family="user", label="Google", state="expiring", provider="google"
    )

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state",
            new=AsyncMock(return_value="ok"),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._check_suppression", new=AsyncMock(return_value=None)
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "sent", "notification_id": "n-1"}),
        ) as deliver_mock,
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    deliver_mock.assert_awaited_once()
    call_kwargs = deliver_mock.await_args.kwargs
    assert call_kwargs["channel"] == "telegram"
    assert call_kwargs["recipient"] == "12345"
    assert "u:google" in call_kwargs["message"] or "focus=u%3Agoogle" in call_kwargs["message"]

    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "delivered"

    audit_append.assert_awaited_once()
    assert audit_append.await_args.args[2] == "lifecycle_state_notified"
    assert audit_append.await_args.kwargs["target"] == "u:google"
    assert audit_append.await_args.kwargs["note"] == "expiring"

    assert summary == {
        "scanned": 1,
        "attention": 1,
        "delivered": 1,
        "deferred": 0,
        "suppressed": 0,
        "errors": 0,
    }


async def test_run_secrets_lifecycle_check_suppressed_does_not_write_debounce_marker():
    """A quiet-hours-suppressed attempt records 'suppressed' but leaves no debounce marker,
    so the next scan retries once quiet hours end."""
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={}, shared_pool=shared_pool)
    snapshot = CredentialSnapshot(
        key="s:SOME_KEY", family="system", label="SOME_KEY", state="failing"
    )

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._check_suppression",
            new=AsyncMock(return_value="quiet_hours"),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "suppressed"
    assert ledger_mock.await_args.kwargs["reason"] == "quiet_hours"
    audit_append.assert_not_called()
    assert summary == {
        "scanned": 1,
        "attention": 1,
        "delivered": 0,
        "deferred": 0,
        "suppressed": 1,
        "errors": 0,
    }


async def test_run_secrets_lifecycle_check_no_recipient_counts_as_error_no_marker():
    """An unresolvable recipient is a genuine terminal failure: it must write an
    attention-ledger row (not go silent, or the outage reads as quiet-hours
    discipline) while still leaving no debounce marker so the next scan retries."""
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={}, shared_pool=shared_pool)
    snapshot = CredentialSnapshot(
        key="s:SOME_KEY", family="system", label="SOME_KEY", state="expired"
    )

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._check_suppression", new=AsyncMock(return_value=None)
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "failed"
    assert ledger_mock.await_args.kwargs["reason"] == "no_recipient_configured"
    assert ledger_mock.await_args.kwargs["dedup_key"] == "s:SOME_KEY"
    audit_append.assert_not_called()
    assert summary == {
        "scanned": 1,
        "attention": 1,
        "delivered": 0,
        "deferred": 0,
        "suppressed": 0,
        "errors": 1,
    }


async def test_run_secrets_lifecycle_check_delivery_error_records_failed_and_enqueues_retry():
    """bu-hmdqz.3: a transport-failed delivery is recorded as 'failed' (not
    'deferred' -- that's reserved for a benign hold) and a retry envelope is
    enqueued on switchboard's own deferred_notifications table so it actually
    gets redelivered instead of silently expiring."""
    shared_pool = object()
    switchboard_pool = object()
    db = _FakeDatabaseManager(
        butler_pools={"switchboard": switchboard_pool}, shared_pool=shared_pool
    )
    snapshot = CredentialSnapshot(
        key="s:SPOTIFY_ACCESS_TOKEN", family="system", label="SPOTIFY_ACCESS_TOKEN", state="failing"
    )

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._check_suppression", new=AsyncMock(return_value=None)
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(
                return_value={"status": "failed", "error": "connection refused: localhost:41104"}
            ),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.insert_deferred_notification",
            new=AsyncMock(return_value="deferred-notif-1"),
        ) as insert_mock,
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    insert_mock.assert_awaited_once()
    insert_kwargs = insert_mock.await_args.kwargs
    assert insert_kwargs["butler_name"] == "switchboard"
    assert insert_kwargs["channel"] == "telegram"
    assert insert_kwargs["envelope"]["origin_butler"] == "switchboard"
    assert insert_kwargs["envelope"]["delivery"]["recipient"] == "12345"

    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "failed"
    assert ledger_mock.await_args.kwargs["reason"].startswith("delivery_error:")
    assert ledger_mock.await_args.kwargs["notification_ref"] == "deferred-notif-1"
    audit_append.assert_not_called()
    assert summary == {
        "scanned": 1,
        "attention": 1,
        "delivered": 0,
        "deferred": 0,
        "suppressed": 0,
        "errors": 1,
    }


async def test_run_secrets_lifecycle_check_delivery_error_supersedes_prior_pending():
    """bu-id0fh: a transport-failed tick cancels prior pending retry envelopes
    for the same credential BEFORE enqueueing the latest one, so a persistent
    multi-tick outage cannot accumulate N pending envelopes (N+1 duplicates on
    recovery). The enqueued envelope carries the CURRENT state's message
    (latest-state-wins supersede)."""
    shared_pool = object()
    switchboard_pool = object()
    db = _FakeDatabaseManager(
        butler_pools={"switchboard": switchboard_pool}, shared_pool=shared_pool
    )
    snapshot = CredentialSnapshot(
        key="s:SPOTIFY_ACCESS_TOKEN", family="system", label="SPOTIFY_ACCESS_TOKEN", state="failing"
    )
    call_order: list = []

    async def fake_cancel(pool, *, butler_name, line_token):
        call_order.append(("cancel", butler_name, line_token))
        return 3

    async def fake_insert(*args, **kwargs):
        call_order.append(("insert", kwargs["envelope"]))
        return "deferred-notif-1"

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._check_suppression", new=AsyncMock(return_value=None)
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "failed", "error": "connection refused"}),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.cancel_pending_notifications_matching_line",
            side_effect=fake_cancel,
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.insert_deferred_notification",
            side_effect=fake_insert,
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ),
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)),
    ):
        summary = await run_secrets_lifecycle_check(db)

    # supersede ran, on switchboard's queue, keyed by the state-independent
    # focus fragment, and strictly BEFORE the insert.
    assert call_order[0][0] == "cancel"
    assert call_order[0][1] == "switchboard"
    assert call_order[0][2] == _focus_fragment("s:SPOTIFY_ACCESS_TOKEN")
    assert call_order[1][0] == "insert"
    # the newly enqueued envelope reflects the CURRENT state.
    assert "failing its health probe" in call_order[1][1]["delivery"]["message"]
    assert summary["errors"] == 1


async def test_run_secrets_lifecycle_check_direct_delivery_cancels_leftover_pending():
    """bu-id0fh: once a direct delivery finally succeeds, any retry envelope left
    over from a prior failed tick is cancelled so switchboard's flusher does not
    ALSO redeliver it — the common recovery path, giving exactly one delivery."""
    shared_pool = object()
    switchboard_pool = object()
    db = _FakeDatabaseManager(
        butler_pools={"switchboard": switchboard_pool}, shared_pool=shared_pool
    )
    snapshot = CredentialSnapshot(
        key="u:google", family="user", label="Google", state="expiring", provider="google"
    )

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state",
            new=AsyncMock(return_value="ok"),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._check_suppression", new=AsyncMock(return_value=None)
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "sent", "notification_id": "n-1"}),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.cancel_pending_notifications_matching_line",
            new=AsyncMock(return_value=1),
        ) as cancel_mock,
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ),
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    cancel_mock.assert_awaited_once()
    assert cancel_mock.await_args.kwargs["butler_name"] == "switchboard"
    assert cancel_mock.await_args.kwargs["line_token"] == _focus_fragment("u:google")
    # the debounce marker still advances only on genuine delivery.
    audit_append.assert_awaited_once()
    assert summary == {
        "scanned": 1,
        "attention": 1,
        "delivered": 1,
        "deferred": 0,
        "suppressed": 0,
        "errors": 0,
    }


async def test_run_secrets_lifecycle_check_unexpected_error_writes_ledger_row():
    """An unexpected exception mid-dispatch (e.g. a DB error deep in deliver())
    must not go silent — it is a genuine delivery failure and has to be
    recorded, or an outage impersonates quiet-hours discipline in the ledger."""
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={}, shared_pool=shared_pool)
    snapshot = CredentialSnapshot(
        key="s:SOME_KEY", family="system", label="SOME_KEY", state="expired"
    )

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._check_suppression",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "failed"
    assert ledger_mock.await_args.kwargs["reason"] == "unexpected_error:RuntimeError"
    assert ledger_mock.await_args.kwargs["dedup_key"] == "s:SOME_KEY"
    audit_append.assert_not_called()
    assert summary == {
        "scanned": 1,
        "attention": 1,
        "delivered": 0,
        "deferred": 0,
        "suppressed": 0,
        "errors": 1,
    }


async def test_run_secrets_lifecycle_check_no_shared_pool_is_a_clean_noop():
    db = _FakeDatabaseManager(butler_pools={}, shared_pool=None)
    summary = await run_secrets_lifecycle_check(db)
    assert summary == {
        "scanned": 0,
        "attention": 0,
        "delivered": 0,
        "deferred": 0,
        "suppressed": 0,
        "errors": 0,
    }


async def test_run_secrets_lifecycle_check_ignores_non_attention_states():
    """A credential in 'ok'/'warn' never reaches the debounce/notify path at all."""
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={}, shared_pool=shared_pool)
    snapshots = [
        CredentialSnapshot(key="s:A", family="system", label="A", state="ok"),
        CredentialSnapshot(key="s:B", family="system", label="B", state="warn"),
    ]

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=snapshots),
        ),
        patch("butlers.jobs.secrets_lifecycle._last_notified_state") as last_notified,
    ):
        summary = await run_secrets_lifecycle_check(db)

    last_notified.assert_not_called()
    assert summary == {
        "scanned": 2,
        "attention": 0,
        "delivered": 0,
        "deferred": 0,
        "suppressed": 0,
        "errors": 0,
    }


# ---------------------------------------------------------------------------
# _delivery_preferences_deferral (notify()'s first quiet-hours gate, bu-178v1)
# ---------------------------------------------------------------------------

_PREFS = {"timezone": "America/New_York", "quiet_hours_start": "22:00", "quiet_hours_end": "07:00"}
_DELIVER_AT = datetime(2026, 7, 14, 11, 0, tzinfo=UTC)


async def test_delivery_preferences_deferral_defers_in_quiet_hours():
    """Prefs present + should_defer True -> returns the computed batch deliver_at,
    keyed on the 'switchboard' identity."""
    switchboard_pool = object()
    db = _FakeDatabaseManager(butler_pools={"switchboard": switchboard_pool}, shared_pool=object())

    with (
        patch(
            "butlers.jobs.secrets_lifecycle.get_delivery_preferences",
            new=AsyncMock(return_value=_PREFS),
        ) as prefs_mock,
        patch(
            "butlers.jobs.secrets_lifecycle.should_defer_notification", return_value=True
        ) as defer_mock,
        patch(
            "butlers.jobs.secrets_lifecycle.compute_deliver_at", return_value=_DELIVER_AT
        ) as compute_mock,
    ):
        result = await _delivery_preferences_deferral(db, channel="telegram", priority="medium")

    assert result == _DELIVER_AT
    # Keyed on the switchboard identity + its own pool (per-schema prefs table).
    assert prefs_mock.await_args.args == (switchboard_pool, "switchboard")
    assert defer_mock.call_args.kwargs["priority"] == "medium"
    assert defer_mock.call_args.kwargs["channel"] == "telegram"
    compute_mock.assert_called_once()


async def test_delivery_preferences_deferral_none_outside_quiet_hours():
    """Prefs present but should_defer False -> None (deliver now)."""
    db = _FakeDatabaseManager(butler_pools={"switchboard": object()}, shared_pool=object())
    with (
        patch(
            "butlers.jobs.secrets_lifecycle.get_delivery_preferences",
            new=AsyncMock(return_value=_PREFS),
        ),
        patch("butlers.jobs.secrets_lifecycle.should_defer_notification", return_value=False),
    ):
        result = await _delivery_preferences_deferral(db, channel="telegram", priority="medium")
    assert result is None


async def test_delivery_preferences_deferral_none_when_no_prefs_row():
    """No delivery_preferences row for switchboard -> None (passthrough)."""
    db = _FakeDatabaseManager(butler_pools={"switchboard": object()}, shared_pool=object())
    with patch(
        "butlers.jobs.secrets_lifecycle.get_delivery_preferences",
        new=AsyncMock(return_value=None),
    ):
        result = await _delivery_preferences_deferral(db, channel="telegram", priority="medium")
    assert result is None


async def test_delivery_preferences_deferral_none_when_no_switchboard_pool():
    """No switchboard pool -> None (fail-open), never raises."""
    db = _FakeDatabaseManager(butler_pools={}, shared_pool=object())
    result = await _delivery_preferences_deferral(db, channel="telegram", priority="medium")
    assert result is None


# ---------------------------------------------------------------------------
# run_secrets_lifecycle_check: delivery_preferences defer gate outcomes
# ---------------------------------------------------------------------------


async def test_run_secrets_lifecycle_check_deferred_enqueues_envelope_not_delivered():
    """Defer window: the notify.v1 envelope is enqueued (single deferral path)
    with the batch deliver_at, deliver() is NOT called, the ledger records
    'deferred', and the debounce marker is NOT advanced (so the transition still
    resolves)."""
    shared_pool = object()
    switchboard_pool = object()
    db = _FakeDatabaseManager(
        butler_pools={"switchboard": switchboard_pool}, shared_pool=shared_pool
    )
    snapshot = CredentialSnapshot(key="u:google", family="user", label="Google", state="expiring")

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state", new=AsyncMock(return_value=None)
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._delivery_preferences_deferral",
            new=AsyncMock(return_value=_DELIVER_AT),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._supersede_pending_retries",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.insert_deferred_notification",
            new=AsyncMock(return_value="deferred-env-1"),
        ) as insert_mock,
        patch(
            "butlers.jobs.secrets_lifecycle._check_suppression", new=AsyncMock()
        ) as suppression_mock,
        patch("butlers.tools.switchboard.notification.deliver.deliver") as deliver_mock,
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    # Enqueued on switchboard's table with the batch deliver_at; NOT delivered.
    insert_mock.assert_awaited_once()
    assert insert_mock.await_args.kwargs["butler_name"] == "switchboard"
    assert insert_mock.await_args.kwargs["deliver_at"] == _DELIVER_AT
    assert insert_mock.await_args.kwargs["envelope"]["delivery"]["recipient"] == "12345"
    deliver_mock.assert_not_called()
    # Gate 1 short-circuits before the approvals/context suppression gate.
    suppression_mock.assert_not_called()
    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "deferred"
    assert ledger_mock.await_args.kwargs["reason"] == "delivery_preferences_quiet_hours"
    assert ledger_mock.await_args.kwargs["notification_ref"] == "deferred-env-1"
    # Debounce marker NOT advanced — a deferred hold is not a confirmed delivery.
    audit_append.assert_not_called()
    assert summary == {
        "scanned": 1,
        "attention": 1,
        "delivered": 0,
        "deferred": 1,
        "suppressed": 0,
        "errors": 0,
    }


async def test_run_secrets_lifecycle_check_delivers_when_delivery_preferences_pass():
    """Gate 1 returns None (no prefs row / outside quiet hours) -> the credential
    is delivered directly through the normal path."""
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={"switchboard": object()}, shared_pool=shared_pool)
    snapshot = CredentialSnapshot(key="u:google", family="user", label="Google", state="expiring")

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state", new=AsyncMock(return_value=None)
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._delivery_preferences_deferral",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._check_suppression", new=AsyncMock(return_value=None)
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "sent", "notification_id": "n-1"}),
        ) as deliver_mock,
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    deliver_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "delivered"
    audit_append.assert_awaited_once()
    assert summary == {
        "scanned": 1,
        "attention": 1,
        "delivered": 1,
        "deferred": 0,
        "suppressed": 0,
        "errors": 0,
    }


async def test_run_secrets_lifecycle_check_defer_without_recipient_records_failed():
    """Defer window but no owner recipient: the deferred envelope would be
    undeliverable, so this is a genuine failure (recorded, not enqueued)."""
    shared_pool = object()
    db = _FakeDatabaseManager(butler_pools={"switchboard": object()}, shared_pool=shared_pool)
    snapshot = CredentialSnapshot(key="s:K", family="system", label="K", state="expired")

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state", new=AsyncMock(return_value=None)
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._delivery_preferences_deferral",
            new=AsyncMock(return_value=_DELIVER_AT),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.insert_deferred_notification",
            new=AsyncMock(return_value="unused"),
        ) as insert_mock,
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    insert_mock.assert_not_called()
    assert ledger_mock.await_args.kwargs["outcome"] == "failed"
    assert ledger_mock.await_args.kwargs["reason"] == "no_recipient_configured"
    audit_append.assert_not_called()
    assert summary == {
        "scanned": 1,
        "attention": 1,
        "delivered": 0,
        "deferred": 0,
        "suppressed": 0,
        "errors": 1,
    }


async def test_run_secrets_lifecycle_check_unexpected_error_after_resolution_enqueues_retry():
    """bu-ziuye: when an unexpected exception raises AFTER message + recipient are
    resolved (e.g. deliver() itself raises instead of returning a failed result),
    the failure is retryable — a retry envelope is enqueued on the SAME
    _enqueue_deferred_envelope path as delivery_error, the ledger reason is
    distinguished (unexpected_error_retry:*) with the envelope ref, and the
    debounce marker is NOT advanced."""
    shared_pool = object()
    switchboard_pool = object()
    db = _FakeDatabaseManager(
        butler_pools={"switchboard": switchboard_pool}, shared_pool=shared_pool
    )
    snapshot = CredentialSnapshot(
        key="s:SPOTIFY_ACCESS_TOKEN", family="system", label="SPOTIFY_ACCESS_TOKEN", state="failing"
    )

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._check_suppression", new=AsyncMock(return_value=None)
        ),
        patch(
            "butlers.jobs.secrets_lifecycle._delivery_preferences_deferral",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        # deliver() RAISES (not returns failed) — the exact gap bu-ziuye closes.
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(side_effect=RuntimeError("boom deep in deliver")),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.insert_deferred_notification",
            new=AsyncMock(return_value="retry-notif-1"),
        ) as insert_mock,
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    # A retry envelope was enqueued on switchboard's deferred_notifications with
    # the resolved message + recipient.
    insert_mock.assert_awaited_once()
    insert_kwargs = insert_mock.await_args.kwargs
    assert insert_kwargs["butler_name"] == "switchboard"
    assert insert_kwargs["envelope"]["delivery"]["recipient"] == "12345"

    # Ledger row: failed, retry reason distinguished from pre-resolution failure,
    # carries the envelope ref.
    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "failed"
    assert ledger_mock.await_args.kwargs["reason"] == "unexpected_error_retry:RuntimeError"
    assert ledger_mock.await_args.kwargs["notification_ref"] == "retry-notif-1"

    # Debounce marker NOT advanced on the retry path.
    audit_append.assert_not_called()
    assert summary["errors"] == 1
    assert summary["delivered"] == 0


async def test_run_secrets_lifecycle_check_unexpected_error_before_resolution_no_envelope():
    """bu-ziuye: an exception BEFORE the message/recipient are resolvable (here,
    inside _last_notified_state) has nothing safe to enqueue — it stamps a plain
    failed row (unexpected_error:*, no envelope ref) rather than a half-built
    envelope."""
    shared_pool = object()
    switchboard_pool = object()
    db = _FakeDatabaseManager(
        butler_pools={"switchboard": switchboard_pool}, shared_pool=shared_pool
    )
    snapshot = CredentialSnapshot(
        key="s:SOME_KEY", family="system", label="SOME_KEY", state="expired"
    )

    with (
        patch(
            "butlers.jobs.secrets_lifecycle._collect_snapshots",
            new=AsyncMock(return_value=[snapshot]),
        ),
        # Raise at the very first step — before message / recipient exist.
        patch(
            "butlers.jobs.secrets_lifecycle._last_notified_state",
            side_effect=RuntimeError("db down"),
        ),
        patch(
            "butlers.jobs.secrets_lifecycle.insert_deferred_notification",
            new=AsyncMock(return_value="should-not-be-used"),
        ) as insert_mock,
        patch(
            "butlers.jobs.secrets_lifecycle.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
    ):
        summary = await run_secrets_lifecycle_check(db)

    # Nothing safe to enqueue → no retry envelope.
    insert_mock.assert_not_called()
    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "failed"
    assert ledger_mock.await_args.kwargs["reason"] == "unexpected_error:RuntimeError"
    assert ledger_mock.await_args.kwargs["notification_ref"] is None
    audit_append.assert_not_called()
    assert summary["errors"] == 1
