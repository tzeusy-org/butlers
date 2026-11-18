"""Endpoint custody egress fence (bu-s11n0s.1 slice 4).

openspec change ``endpoint-custody-holds``: REQ-endpoint-custody-holds-007 (held recipient or
sending account refuses before provider start; lookup failure fails closed; owner-default
fallback) and the egress half of REQ-endpoint-custody-holds-006. The real
``public.custody_target_held`` read is exercised in
``tests/migrations/test_custody_egress_fence.py``; these tests fake only the pool.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.custody_egress import (
    CustodyEgressRefused,
    choose_owner_default_target,
    custody_targets_held,
    email_targets,
    require_custody_clear,
    telegram_chat_target,
)
from butlers.modules.email import EmailConfig, EmailModule
from butlers.modules.telegram import TelegramConfig, TelegramModule

pytestmark = pytest.mark.unit

HELD_TG = ("has-handle", "telegram:111")
HELD_EMAIL = ("has-email", "lost@example.test")


class FakeCustodyPool:
    """Answers the fence query from a set of held targets, recording every lookup."""

    def __init__(self, held: set[tuple[str, str]] | None = None, *, error: bool = False):
        self.held = held or set()
        self.error = error
        self.lookups: list[list[tuple[str, str]]] = []

    async def fetchval(self, sql: str, predicates: list[str], objects: list[str]) -> Any:
        assert "public.custody_target_held" in sql
        targets = list(zip(predicates, objects, strict=True))
        self.lookups.append(targets)
        if self.error:
            raise ConnectionError("database unavailable")
        normalized = {(p, o.strip().lower()) for p, o in self.held}
        return any((p, o.strip().lower()) in normalized for p, o in targets)


# --------------------------------------------------------------------------- core helper


def test_targets_name_the_owner_fact_forms() -> None:
    assert telegram_chat_target(" 111 ") == HELD_TG
    assert telegram_chat_target("") is None
    assert telegram_chat_target(None) is None
    assert email_targets(["Lost <lost@example.test>"]) == [HELD_EMAIL]
    assert email_targets(["a@x.test, b@x.test"]) == [
        ("has-email", "a@x.test"),
        ("has-email", "b@x.test"),
    ]
    # A recipient that names no mailbox is unknown, never treated as unheld.
    assert email_targets(["not an address"]) is None
    assert email_targets([None]) is None
    assert email_targets([""]) is None


async def test_held_target_refuses_and_unheld_passes() -> None:
    pool = FakeCustodyPool({HELD_TG})
    with pytest.raises(CustodyEgressRefused) as refused:
        await require_custody_clear(pool, [HELD_TG])
    assert refused.value.reason == "endpoint_held"
    assert refused.value.retryable is False
    assert "telegram:111" not in str(refused.value)
    await require_custody_clear(pool, [("has-handle", "telegram:222")])


@pytest.mark.parametrize(
    ("pool", "targets"),
    [
        (None, [HELD_TG]),
        (FakeCustodyPool(error=True), [HELD_TG]),
        (FakeCustodyPool(), None),
        (FakeCustodyPool(), []),
    ],
    ids=["no-pool", "lookup-error", "unnamed-target", "no-targets"],
)
async def test_unreadable_custody_fails_closed(pool, targets) -> None:
    with pytest.raises(CustodyEgressRefused) as refused:
        await require_custody_clear(pool, targets)
    assert refused.value.reason == "eligibility_unknown"
    assert refused.value.retryable is True


async def test_a_non_boolean_answer_is_unknown() -> None:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=None)
    with pytest.raises(CustodyEgressRefused, match="eligibility_unknown"):
        await custody_targets_held(pool, [HELD_TG])


async def test_owner_default_keeps_telegram_when_unheld() -> None:
    choice = await choose_owner_default_target(
        FakeCustodyPool({HELD_EMAIL}), telegram_chat_id="111", owner_email="lost@example.test"
    )
    assert (choice.channel, choice.recipient, choice.refusal) == ("telegram", "111", None)


async def test_owner_default_falls_back_to_unheld_email() -> None:
    """'Owner fallback uses real unheld account'."""
    choice = await choose_owner_default_target(
        FakeCustodyPool({HELD_TG}), telegram_chat_id="111", owner_email="owner@example.test"
    )
    assert (choice.channel, choice.recipient, choice.refusal) == (
        "email",
        "owner@example.test",
        None,
    )


async def test_owner_default_with_no_unheld_destination_refuses() -> None:
    """'No unheld destination': all held is endpoint_held; any unreadable is unknown."""
    held = await choose_owner_default_target(
        FakeCustodyPool({HELD_TG, HELD_EMAIL}),
        telegram_chat_id="111",
        owner_email="lost@example.test",
    )
    assert (held.channel, held.refusal) == (None, "endpoint_held")
    unknown = await choose_owner_default_target(
        FakeCustodyPool(error=True), telegram_chat_id="111", owner_email="owner@example.test"
    )
    assert (unknown.channel, unknown.refusal) == (None, "eligibility_unknown")
    no_email = await choose_owner_default_target(
        FakeCustodyPool({HELD_TG}), telegram_chat_id="111", owner_email=None
    )
    assert (no_email.channel, no_email.refusal) == (None, "endpoint_held")


# --------------------------------------------------------------------------- adapters


def _email_module(pool: Any, *, sender: str = "bot@example.test") -> EmailModule:
    module = EmailModule()
    module._config = EmailConfig(send_tools=True)
    module._pool = pool
    module._resolved_credentials = {
        module._config.bot.address_env: sender,
        module._config.bot.password_env: "unused",
    }
    return module


@pytest.fixture
def allow_permissions():
    with (
        patch("butlers.modules.email.require_permission", AsyncMock(return_value=None)),
        patch("butlers.modules.telegram.require_permission", AsyncMock(return_value=None)),
        patch("butlers.modules.email.write_audit_entry", AsyncMock()) as email_audit,
        patch("butlers.modules.telegram.write_audit_entry", AsyncMock()) as telegram_audit,
    ):
        yield {"email": email_audit, "telegram": telegram_audit}


async def test_email_refuses_held_recipient_before_smtp(allow_permissions) -> None:
    module = _email_module(FakeCustodyPool({HELD_EMAIL}))
    with patch.object(module, "_smtp_send") as smtp:
        with pytest.raises(CustodyEgressRefused, match="endpoint_held"):
            await module._send_email("Lost <LOST@example.test>", "s", "b")
        with pytest.raises(CustodyEgressRefused, match="endpoint_held"):
            await module._reply_to_thread("lost@example.test", "thread-1", "b")
    smtp.assert_not_called()
    allow_permissions["email"].assert_not_awaited()


async def test_email_refuses_held_sending_account_before_smtp(allow_permissions) -> None:
    """'All paths refuse held account': the cached SMTP account, not the recipient, is held."""
    pool = FakeCustodyPool({HELD_EMAIL})
    module = _email_module(pool, sender="lost@example.test")
    with patch.object(module, "_smtp_send") as smtp:
        with pytest.raises(CustodyEgressRefused, match="endpoint_held"):
            await module._send_email("friend@example.test", "s", "b")
    smtp.assert_not_called()
    assert pool.lookups == [[("has-email", "friend@example.test"), HELD_EMAIL]]


async def test_email_lookup_error_refuses_before_smtp(allow_permissions) -> None:
    module = _email_module(FakeCustodyPool(error=True))
    with patch.object(module, "_smtp_send") as smtp:
        with pytest.raises(CustodyEgressRefused, match="eligibility_unknown"):
            await module._send_email("friend@example.test", "s", "b")
    smtp.assert_not_called()


async def test_unheld_email_send_keeps_audit(allow_permissions) -> None:
    module = _email_module(FakeCustodyPool({HELD_EMAIL}))
    with patch.object(module, "_smtp_send", return_value={"status": "sent"}) as smtp:
        result = await module._send_email("friend@example.test", "s", "b")
    assert result == {"status": "sent"}
    smtp.assert_called_once()
    allow_permissions["email"].assert_awaited_once()


def _telegram_module(pool: Any) -> TelegramModule:
    module = TelegramModule()
    module._config = TelegramConfig()
    module._audit_pool = pool
    module._resolved_credentials = {module._config.bot.token_env: "unused"}
    return module


async def test_telegram_refuses_held_chat_before_provider(allow_permissions) -> None:
    module = _telegram_module(FakeCustodyPool({HELD_TG}))
    with patch.object(module, "_send_chunk", AsyncMock()) as chunk:
        with pytest.raises(CustodyEgressRefused, match="endpoint_held"):
            await module._send_message("111", "hello")
        with pytest.raises(CustodyEgressRefused, match="endpoint_held"):
            await module._reply_to_message("111", 7, "hello")
    chunk.assert_not_awaited()
    audit = allow_permissions["telegram"]
    assert audit.await_args.kwargs["error"] == "custody_endpoint_held"


async def test_telegram_refuses_held_chat_edit_and_files(allow_permissions) -> None:
    module = _telegram_module(FakeCustodyPool({HELD_TG}))
    with patch("butlers.modules.telegram.edit_telegram_message_text", AsyncMock()) as edit:
        with pytest.raises(CustodyEgressRefused, match="endpoint_held"):
            await module._edit_message_text("111", 7, "new text")
    edit.assert_not_awaited()
    with (
        patch("butlers.modules.telegram.validate_attachment_specs", return_value=[object()]),
        patch("butlers.modules.telegram.resolve_attachments", AsyncMock()) as resolve,
    ):
        with pytest.raises(CustodyEgressRefused, match="endpoint_held"):
            await module._send_files("111", "t", [{}], origin_butler="general")
    resolve.assert_not_awaited()


async def test_telegram_lookup_error_refuses_without_audit_retry(allow_permissions) -> None:
    module = _telegram_module(FakeCustodyPool(error=True))
    with patch.object(module, "_send_chunk", AsyncMock()) as chunk:
        with pytest.raises(CustodyEgressRefused, match="eligibility_unknown"):
            await module._send_message("111", "hello")
    chunk.assert_not_awaited()
    allow_permissions["telegram"].assert_not_awaited()


async def test_unheld_telegram_chat_sends(allow_permissions) -> None:
    module = _telegram_module(FakeCustodyPool({HELD_TG}))
    sent = {"ok": True, "result": {"message_id": 9}}
    with patch.object(module, "_send_chunk", AsyncMock(return_value=sent)) as chunk:
        result = await module._send_message("222", "hello")
    chunk.assert_awaited_once()
    assert result["result"]["message_id"] == 9


# --------------------------------------------------------------------------- notify default


def _daemon(pool: Any, chat_id: str | None) -> Any:
    daemon = MagicMock()
    daemon.db.pool = pool
    daemon._resolve_default_notify_recipient = AsyncMock(return_value=chat_id)
    return daemon


async def test_notify_owner_default_moves_to_unheld_email() -> None:
    from butlers.core_tools._notifications import _owner_default_custody_target

    daemon = _daemon(FakeCustodyPool({HELD_TG}), "111")
    with patch(
        "butlers.credential_store.resolve_owner_entity_info",
        AsyncMock(return_value="owner@example.test"),
    ):
        choice = await _owner_default_custody_target(daemon, None)
    assert choice is not None
    assert (choice.channel, choice.recipient) == ("email", "owner@example.test")


async def test_notify_owner_default_keeps_today_when_unheld_or_unconfigured() -> None:
    from butlers.core_tools._notifications import _owner_default_custody_target

    with patch(
        "butlers.credential_store.resolve_owner_entity_info",
        AsyncMock(return_value="owner@example.test"),
    ):
        assert await _owner_default_custody_target(_daemon(FakeCustodyPool(), "111"), None) is None
        # No Telegram target: the existing missing-chat error path stays in charge.
        assert await _owner_default_custody_target(_daemon(FakeCustodyPool(), None), None) is None


async def test_notify_owner_default_refuses_when_nothing_is_unheld() -> None:
    from butlers.core_tools._notifications import _owner_default_custody_target

    with patch(
        "butlers.credential_store.resolve_owner_entity_info",
        AsyncMock(return_value="lost@example.test"),
    ):
        held = await _owner_default_custody_target(
            _daemon(FakeCustodyPool({HELD_TG, HELD_EMAIL}), "111"), None
        )
        unknown = await _owner_default_custody_target(
            _daemon(FakeCustodyPool(error=True), "111"), None
        )
    assert held is not None and held.refusal == "endpoint_held"
    assert unknown is not None and unknown.refusal == "eligibility_unknown"


# --------------------------------------------------------------------------- approval handoff


async def test_approval_card_custody_refusal_is_pre_provider_safe_retry() -> None:
    """REQ-endpoint-custody-holds-006: a fenced card is never recorded as possibly sent."""
    from butlers.core.approval_delivery_transport import MessengerApprovalHandoffRepository

    repo = MessengerApprovalHandoffRepository(pool=None)
    context = MagicMock()
    context.operation = "handoff"
    stored: list[Any] = []

    async def store(_context: Any, result: Any) -> Any:
        stored.append(result)
        return result

    async def refused_provider() -> Any:
        raise CustodyEgressRefused("endpoint_held")

    with (
        patch.object(repo, "_admit_or_classify", AsyncMock(return_value="provider")),
        patch.object(repo, "_store_result", side_effect=store),
    ):
        result = await repo.process(context, provider_call=refused_provider)
    assert (result.classification, result.reason_code) == (
        "safe_retry",
        "provider_preflight_failed",
    )
    assert stored == [result]
