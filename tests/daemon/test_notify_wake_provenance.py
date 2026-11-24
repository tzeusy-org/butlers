"""Wake-recovery provenance on parked owner-attention holds (bu-kqnum.3.6.1).

Slice A of strict-owner-telegram-wake-recovery (core-notify "Wake-Recovery Hold Provenance",
task 3.1). A new routine owner-default quiet-hours hold carries provenance in the same INSERT
as the row; explicit-target, priority, retry, context-only and unprovable holds carry none and
park exactly as before.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.temporal.delivery_db import WAKE_HOLD_KIND_OWNER_QUIET_HOURS, envelope_digest
from butlers.core_tools._notifications import _wake_hold_provenance
from butlers.daemon import ButlerDaemon
from butlers.testing.tool_host import host_tools

pytestmark = pytest.mark.unit

# DB-free harness: the daemon's real notify on a mock pool whose INSERT args the
# tests read (bu-rvgzmf.14).


@contextmanager
def _registered_approval_hooks(pool: Any):
    """Install the real approvals runtime for exactly one test pool."""
    import butlers.core.approvals_hooks as approval_hooks
    from butlers.modules.approvals.email_guard import (
        check_email_recipient,
        check_recipient,
    )
    from butlers.modules.approvals.park import park_pending_action

    runtime = approval_hooks.register_approval_hooks(
        pool,
        email_guard=check_email_recipient,
        recipient_guard=check_recipient,
        park_pending_action=park_pending_action,
    )
    try:
        yield
    finally:
        approval_hooks.unregister_approval_hooks(pool, runtime)


@pytest.fixture
def butler_dir(tmp_path: Path) -> Path:
    """Create a minimal butler directory for testing."""
    butler_path = tmp_path / "test-butler"
    butler_path.mkdir()
    (butler_path / "butler.toml").write_text(
        """
[butler]
name = "test-butler"
port = 9100
description = "Test butler"

[butler.db]
name = "butlers"
schema = "test_butler"

[[butler.schedule]]
name = "daily-check"
cron = "0 9 * * *"
prompt = "Do the daily check"
"""
    )
    (butler_path / "MANIFESTO.md").write_text("# Test Butler")
    (butler_path / "CLAUDE.md").write_text("Test butler instructions.")
    return butler_path


def _make_runtime_config_row(butler_name: str = "test-butler") -> dict:
    return {
        "butler_name": butler_name,
        "core_groups": None,
        "catalog_read_sensitivity": "normal",
        "max_concurrent": 3,
        "max_queued": 10,
        "seeded_at": None,
        "updated_at": None,
    }


def _make_fetchrow_side_effect(*, approvals_policy_row: dict | None):
    """Build an async side_effect for pool.fetchrow.

    - ``runtime_config`` queries → a valid runtime_config row (daemon boot).
    - ``contact_info`` + ``is_primary`` queries → is_primary=True (email guard).
    - ``approvals_policy`` queries → *approvals_policy_row* (the owner-default gate).
    - ``delivery_preferences`` queries → None (per-butler defer path not configured;
      this test suite targets the owner-level approvals_policy + context-bus gate).
    - everything else → None.
    """

    async def _fetchrow(query: str, *args, **kwargs):
        if "runtime_config" in query:
            return _make_runtime_config_row()
        if "contact_info" in query and "is_primary" in query:
            return {"is_primary": True}
        if "approvals_policy" in query:
            return approvals_policy_row
        return None

    return _fetchrow


async def _host_notify(butler_dir: Path, mock_pool: Any) -> tuple[ButlerDaemon, Any]:
    """The daemon's real notify, registered on a DB-free host over *mock_pool*.

    The subject is the INSERT notify builds, not the boot, so no lifecycle runs
    (bu-rvgzmf.2, bu-rvgzmf.14).
    """
    host = await host_tools(butler_dir, pool=mock_pool)
    return host.daemon, await host.tool("notify")


def _make_mock_pool(*, approvals_policy_row: dict | None) -> AsyncMock:
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    # Git defaults and the runtime row agree: no conditional update or audit append.
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])

    transaction_depth = 0

    async def enter_transaction() -> None:
        nonlocal transaction_depth
        transaction_depth += 1

    async def exit_transaction(*_exc: object) -> bool:
        nonlocal transaction_depth
        transaction_depth -= 1
        return False

    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(side_effect=enter_transaction)
    transaction.__aexit__ = AsyncMock(side_effect=exit_transaction)
    mock_conn.transaction = MagicMock(return_value=transaction)
    mock_conn.is_in_transaction = MagicMock(side_effect=lambda: transaction_depth > 0)

    @asynccontextmanager
    async def mock_acquire():
        yield mock_conn

    mock_pool = AsyncMock()
    mock_pool.acquire = mock_acquire
    mock_pool.fetchval = AsyncMock(return_value=str(uuid.uuid4()))
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(
        side_effect=_make_fetchrow_side_effect(approvals_policy_row=approvals_policy_row)
    )
    mock_pool.fetch = AsyncMock(return_value=[])
    return mock_pool


def _make_mock_client() -> Any:
    mock_call_result = MagicMock()
    mock_call_result.is_error = False
    mock_call_result.data = {"status": "ok", "notification_id": "notif-123"}
    mock_call_result.content = [MagicMock(text='{"status":"ok"}')]

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    return mock_client


@contextmanager
def _configured_owner_default_recipient(daemon: ButlerDaemon):
    """Model a configured owner channel for default-recipient notify tests."""
    owner_contact = MagicMock(roles=["owner"])
    with (
        patch.object(
            daemon,
            "_resolve_default_notify_recipient",
            new=AsyncMock(return_value="123456789"),
        ),
        patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=owner_contact),
        ),
    ):
        yield


def _deferred_notification_insert_calls(mock_pool: AsyncMock) -> list[tuple[Any, ...]]:
    """Return fetchval call args whose query persists deferred notifications."""
    return [
        call.args
        for call in mock_pool.fetchval.await_args_list
        if call.args and "INSERT INTO deferred_notifications" in call.args[0]
    ]


_IN_WINDOW_QUIET_POLICY = {"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "UTC"}


_NO_QUIET_POLICY = {"quiet_start_hour": None, "quiet_end_hour": None, "timezone": "UTC"}


_OWNER_CHAT = "123456789"
_NOW = datetime(2026, 10, 19, 23, 30, tzinfo=UTC)
_POLICY = {"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "UTC"}
_ENVELOPE = {"delivery": {"channel": "telegram", "recipient": _OWNER_CHAT, "message": "m"}}


@pytest.fixture
def _fixed_quiet_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluate the policy hold, and its window start, at a fixed in-window instant."""
    import butlers.core.approvals_policy as approvals_policy

    real_deliver_at = approvals_policy.policy_quiet_hours_deliver_at
    real_window_start = approvals_policy.policy_quiet_hours_window_start

    def _fixed(real):
        def call(policy: dict[str, Any] | None, *, now: datetime) -> datetime | None:
            return real(policy, now=now.replace(hour=23, minute=30, second=0, microsecond=0))

        return call

    monkeypatch.setattr(approvals_policy, "policy_quiet_hours_deliver_at", _fixed(real_deliver_at))
    monkeypatch.setattr(
        approvals_policy, "policy_quiet_hours_window_start", _fixed(real_window_start)
    )


def _wake_args(call_args: tuple[Any, ...]) -> tuple[Any, ...]:
    """The provenance bind values of a deferred_notifications INSERT (after deferred_at)."""
    return call_args[8:]


@pytest.mark.usefixtures("_fixed_quiet_clock")
async def test_a_new_policy_hold_writes_its_provenance_in_the_same_insert(
    butler_dir: Path,
) -> None:
    mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
    daemon, notify_fn = await _host_notify(butler_dir, mock_pool)
    daemon.switchboard_client = _make_mock_client()

    with (
        _configured_owner_default_recipient(daemon),
        patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(return_value=_OWNER_CHAT),
        ),
    ):
        result = await notify_fn(channel="telegram", message="Weekly report")

    assert result["status"] == "deferred"
    calls = _deferred_notification_insert_calls(mock_pool)
    assert len(calls) == 1
    sql, envelope = calls[0][0], calls[0][5]
    assert "wake_hold_kind" in sql and "nextval('deferred_notifications_admission_seq')" in sql
    kind, window_key, tz, target, digest, original = _wake_args(calls[0])
    assert kind == WAKE_HOLD_KIND_OWNER_QUIET_HOURS
    start, end = window_key.split("/")
    assert end == result["deliver_at"]
    assert datetime.fromisoformat(end) - datetime.fromisoformat(start) == timedelta(hours=9)
    assert tz == "UTC"
    assert target == {
        "provider": "telegram",
        "chat_id": _OWNER_CHAT,
        "thread_id": None,
        "recipient_endpoint": f"telegram:{_OWNER_CHAT}",
    }
    assert (
        digest
        == hashlib.sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert original.isoformat() == result["deliver_at"]


@pytest.mark.usefixtures("_fixed_quiet_clock")
async def test_a_hold_for_an_unprovable_owner_target_parks_without_provenance(
    butler_dir: Path,
) -> None:
    mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
    daemon, notify_fn = await _host_notify(butler_dir, mock_pool)
    daemon.switchboard_client = _make_mock_client()

    with (
        _configured_owner_default_recipient(daemon),
        patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(return_value="someone-else"),
        ),
    ):
        result = await notify_fn(channel="telegram", message="Weekly report")

    assert result["status"] == "deferred"
    (call,) = _deferred_notification_insert_calls(mock_pool)
    assert "wake_hold_kind" not in call[0] and len(call) == 8


@pytest.mark.usefixtures("_fixed_quiet_clock")
async def test_a_context_only_hold_carries_no_provenance(butler_dir: Path) -> None:
    from butlers.context_bus import ContextEntry

    mock_pool = _make_mock_pool(approvals_policy_row=_NO_QUIET_POLICY)
    daemon, notify_fn = await _host_notify(butler_dir, mock_pool)
    daemon.switchboard_client = _make_mock_client()
    dnd = ContextEntry(
        signal_type="dnd",
        value=None,
        set_by_butler="general",
        set_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        confidence=1.0,
    )

    with (
        _configured_owner_default_recipient(daemon),
        patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[dnd])),
        patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(return_value=_OWNER_CHAT),
        ),
    ):
        result = await notify_fn(channel="telegram", message="Weekly report")

    assert result["reason"] == "context_bus:dnd"
    (call,) = _deferred_notification_insert_calls(mock_pool)
    assert "wake_hold_kind" not in call[0] and len(call) == 8


@pytest.mark.usefixtures("_fixed_quiet_clock")
@pytest.mark.parametrize(
    "send_kwargs",
    [
        pytest.param({"priority": "high"}, id="unverified-high-priority"),
        pytest.param(
            {"recipient": _OWNER_CHAT, "_why": "The owner picked this chat.", "_evidence": []},
            id="explicit-owner-recipient",
        ),
    ],
)
async def test_a_priority_or_explicit_target_hold_parks_without_provenance(
    butler_dir: Path, send_kwargs: dict[str, Any]
) -> None:
    mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
    daemon, notify_fn = await _host_notify(butler_dir, mock_pool)
    daemon.switchboard_client = _make_mock_client()
    owner_contact = MagicMock(roles=["owner"])

    with (
        _configured_owner_default_recipient(daemon),
        _registered_approval_hooks(mock_pool),
        patch("butlers.identity.resolve_contact_by_channel", AsyncMock(return_value=owner_contact)),
        patch(
            "butlers.identity.resolve_owner_channel_via_definer",
            AsyncMock(return_value=(owner_contact, False)),
        ),
        patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(return_value=_OWNER_CHAT),
        ),
    ):
        result = await notify_fn(channel="telegram", message="urgent", **send_kwargs)

    assert result["status"] == "deferred"
    (call,) = _deferred_notification_insert_calls(mock_pool)
    assert "wake_hold_kind" not in call[0] and len(call) == 8


_POLICY_END = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)


@pytest.mark.usefixtures("_fixed_quiet_clock")
@pytest.mark.parametrize(
    ("overrides", "owner_chat"),
    [
        pytest.param({"routine_owner_default": False}, _OWNER_CHAT, id="not-routine-default"),
        pytest.param({"channel": "email"}, _OWNER_CHAT, id="not-telegram"),
        pytest.param({"policy": None}, _OWNER_CHAT, id="no-policy"),
        pytest.param({"policy_deliver_at": None}, _OWNER_CHAT, id="context-only"),
        pytest.param(
            {"notify_request": {"delivery": {"channel": "telegram"}}},
            _OWNER_CHAT,
            id="no-recipient",
        ),
        pytest.param({}, "someone-else", id="recipient-is-not-the-owner-chat"),
        pytest.param({}, None, id="owner-chat-unresolvable"),
    ],
)
async def test_provenance_is_refused_unless_every_input_is_provable(
    overrides: dict[str, Any], owner_chat: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    kwargs: dict[str, Any] = {
        "routine_owner_default": True,
        "channel": "telegram",
        "policy": _POLICY,
        "policy_deliver_at": _POLICY_END,
        "deferred_at": _NOW,
        "deliver_at": _POLICY_END,
        "notify_request": _ENVELOPE,
    }
    kwargs.update(overrides)

    with patch(
        "butlers.credential_store.resolve_owner_entity_info",
        new=AsyncMock(return_value=owner_chat),
    ):
        assert await _wake_hold_provenance(object(), **kwargs) is None
    # An ordinary refusal is a decision, not a swallowed error.
    assert "could not derive wake-recovery provenance" not in caplog.text


@pytest.mark.usefixtures("_fixed_quiet_clock")
async def test_a_resolution_error_fails_closed_to_no_provenance() -> None:
    with patch(
        "butlers.credential_store.resolve_owner_entity_info",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        assert (
            await _wake_hold_provenance(
                object(),
                routine_owner_default=True,
                channel="telegram",
                policy=_POLICY,
                policy_deliver_at=_POLICY_END,
                deferred_at=_NOW,
                deliver_at=_POLICY_END,
                notify_request=_ENVELOPE,
            )
            is None
        )


def test_the_envelope_digest_is_stable_across_key_order() -> None:
    assert envelope_digest({"a": 1, "b": {"c": 2}}) == envelope_digest({"b": {"c": 2}, "a": 1})
    assert envelope_digest({"a": 1}) != envelope_digest({"a": 2})


@pytest.mark.usefixtures("_fixed_quiet_clock")
async def test_a_policy_hold_extended_by_context_keeps_the_policy_window_and_the_stored_anchor(
    butler_dir: Path,
) -> None:
    from butlers.context_bus import ContextEntry

    mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
    daemon, notify_fn = await _host_notify(butler_dir, mock_pool)
    daemon.switchboard_client = _make_mock_client()
    dnd = ContextEntry(
        signal_type="dnd",
        value=None,
        set_by_butler="general",
        set_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=2),
        confidence=1.0,
    )

    with (
        _configured_owner_default_recipient(daemon),
        patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[dnd])),
        patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(return_value=_OWNER_CHAT),
        ),
    ):
        result = await notify_fn(channel="telegram", message="Weekly report")

    assert result["reason"] == "policy_quiet_hours+context_bus:dnd"
    (call,) = _deferred_notification_insert_calls(mock_pool)
    _kind, window_key, _tz, _target, _digest, original = _wake_args(call)
    assert window_key.split("/")[1] != result["deliver_at"]
    assert original == dnd.expires_at and call[6] == dnd.expires_at
