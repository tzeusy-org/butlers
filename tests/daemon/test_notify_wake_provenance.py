"""Wake-recovery provenance on parked owner-attention holds (bu-kqnum.3.6.1).

Slice A of strict-owner-telegram-wake-recovery (core-notify "Wake-Recovery Hold Provenance",
task 3.1). A new routine owner-default quiet-hours hold carries provenance in the same INSERT
as the row; explicit-target, priority, retry, context-only and unprovable holds carry none and
park exactly as before.
"""

# ruff: noqa: F811  (butler_dir is the shared fixture imported from the ledger tests)

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.temporal.delivery_db import WAKE_HOLD_KIND_OWNER_QUIET_HOURS, envelope_digest
from butlers.core_tools._notifications import _wake_hold_provenance
from tests.daemon.test_notify_attention_ledger import (  # noqa: F401
    _IN_WINDOW_QUIET_POLICY,
    _NO_QUIET_POLICY,
    _configured_owner_default_recipient,
    _deferred_notification_insert_calls,
    _make_mock_client,
    _make_mock_pool,
    _patch_infra,
    _start_daemon_with_notify,
    butler_dir,
)

pytestmark = pytest.mark.unit

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
    daemon, notify_fn = await _start_daemon_with_notify(butler_dir, _patch_infra(mock_pool))
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
    daemon, notify_fn = await _start_daemon_with_notify(butler_dir, _patch_infra(mock_pool))
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
    daemon, notify_fn = await _start_daemon_with_notify(butler_dir, _patch_infra(mock_pool))
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
async def test_a_high_priority_notification_is_not_parked_at_all(butler_dir: Path) -> None:
    mock_pool = _make_mock_pool(approvals_policy_row=_IN_WINDOW_QUIET_POLICY)
    daemon, notify_fn = await _start_daemon_with_notify(butler_dir, _patch_infra(mock_pool))
    daemon.switchboard_client = _make_mock_client()

    with (
        _configured_owner_default_recipient(daemon),
        patch("butlers.context_bus.get_active_context", new=AsyncMock(return_value=[])),
        patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(return_value=_OWNER_CHAT),
        ),
    ):
        await notify_fn(channel="telegram", message="urgent", priority="high")

    assert _deferred_notification_insert_calls(mock_pool) == []


_POLICY_END = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)


@pytest.mark.usefixtures("_fixed_quiet_clock")
@pytest.mark.parametrize(
    ("overrides", "owner_chat"),
    [
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
    daemon, notify_fn = await _start_daemon_with_notify(butler_dir, _patch_infra(mock_pool))
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
