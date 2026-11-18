"""Endpoint custody egress fence on migrated PostgreSQL (bu-s11n0s.1 slice 4).

openspec change ``endpoint-custody-holds``: REQ-endpoint-custody-holds-007 and the egress half of
REQ-endpoint-custody-holds-006. Holds are written by the database owner (the trusted
maintenance login); every fence lookup runs through the real Messenger runtime role, which reads
holds only through ``public.custody_target_held``.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import asyncpg
import pytest

from butlers.core.custody_egress import (
    CustodyEgressRefused,
    choose_owner_default_target,
    require_custody_clear,
)
from butlers.db import Database
from butlers.modules.email import EmailConfig, EmailModule
from butlers.modules.telegram import TelegramConfig, TelegramModule

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_HOLD_INSERT = """
INSERT INTO public.custody_holds
    (owner_entity_id, target_kind, target_digest, generation, episode_id, reason,
     source_command_id)
VALUES ($1, $2, public.custody_target_digest($2, $3), 1, gen_random_uuid(), 'lost',
        gen_random_uuid())
RETURNING id
"""


@pytest.fixture
def custody_db_url(fresh_db) -> str:
    return fresh_db("core", "relationship", schemas={"relationship": "relationship"})


@pytest.fixture
async def owner_pool(custody_db_url: str) -> AsyncIterator[asyncpg.Pool]:
    """The migration login, which owns the database and so maintains holds."""
    pool = await asyncpg.create_pool(custody_db_url, min_size=1, max_size=2)
    yield pool
    await pool.close()


@pytest.fixture
async def messenger_pool(custody_db_url: str) -> AsyncIterator[asyncpg.Pool]:
    """Production Database pool constrained by the real Messenger runtime role."""
    parsed = urlparse(custody_db_url)
    database = Database(
        db_name=parsed.path.lstrip("/"),
        schema="messenger",
        role="butler_messenger_rw",
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "",
        min_pool_size=1,
        max_pool_size=2,
    )
    pool = await database.connect()
    yield pool
    await database.close()


def _unique(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


async def _hold(owner_pool: asyncpg.Pool, predicate: str, obj: str) -> uuid.UUID:
    owner = await owner_pool.fetchval(
        "INSERT INTO public.entities (canonical_name, roles) "
        "VALUES ($1, ARRAY['owner']) RETURNING id",
        _unique("egress owner "),
    )
    return await owner_pool.fetchval(_HOLD_INSERT, owner, predicate, obj)


async def _release(owner_pool: asyncpg.Pool, hold_id: uuid.UUID) -> None:
    await owner_pool.execute(
        "UPDATE public.custody_holds SET released_at = now() WHERE id = $1", hold_id
    )


async def test_runtime_role_fence_refuses_held_targets_until_release(
    owner_pool: asyncpg.Pool, messenger_pool: asyncpg.Pool
) -> None:
    chat = f"telegram:{uuid.uuid4().int % 10**9}"
    mailbox = _unique("lost-") + "@example.test"
    tg_hold = await _hold(owner_pool, "has-handle", chat)
    email_hold = await _hold(owner_pool, "has-email", mailbox)

    for target in (("has-handle", chat), ("has-email", mailbox.upper())):
        with pytest.raises(CustodyEgressRefused) as refused:
            await require_custody_clear(messenger_pool, [target])
        assert refused.value.reason == "endpoint_held"
    # An unheld target passes, and a held one anywhere in the set refuses the whole send.
    await require_custody_clear(messenger_pool, [("has-email", "friend@example.test")])
    with pytest.raises(CustodyEgressRefused, match="endpoint_held"):
        await require_custody_clear(
            messenger_pool, [("has-email", "friend@example.test"), ("has-email", mailbox)]
        )

    await _release(owner_pool, tg_hold)
    await _release(owner_pool, email_hold)
    await require_custody_clear(messenger_pool, [("has-handle", chat), ("has-email", mailbox)])


async def test_closed_pool_fails_closed(custody_db_url: str) -> None:
    pool = await asyncpg.create_pool(custody_db_url, min_size=1, max_size=1)
    await pool.close()
    with pytest.raises(CustodyEgressRefused, match="eligibility_unknown"):
        await require_custody_clear(pool, [("has-email", "friend@example.test")])


async def test_adapters_refuse_before_provider_through_the_runtime_role(
    owner_pool: asyncpg.Pool, messenger_pool: asyncpg.Pool
) -> None:
    """'All paths refuse held account' and 'Prior approval does not reopen custody'.

    The SMTP and Telegram chokepoints are what registered tools, route.execute, approved
    execution and deferred flush all call, so a refusal here is a refusal on every path.
    """
    chat_id = str(uuid.uuid4().int % 10**9)
    sender = _unique("held-bot-") + "@example.test"
    await _hold(owner_pool, "has-handle", f"telegram:{chat_id}")
    await _hold(owner_pool, "has-email", sender)

    email = EmailModule()
    email._config = EmailConfig(send_tools=True)
    email._pool = messenger_pool
    email._resolved_credentials = {
        email._config.bot.address_env: sender,
        email._config.bot.password_env: "unused",
    }
    telegram = TelegramModule()
    telegram._config = TelegramConfig()
    telegram._audit_pool = messenger_pool
    telegram._resolved_credentials = {telegram._config.bot.token_env: "unused"}

    with (
        patch("butlers.modules.email.require_permission", AsyncMock(return_value=None)),
        patch("butlers.modules.telegram.require_permission", AsyncMock(return_value=None)),
        patch.object(email, "_smtp_send") as smtp,
        patch.object(telegram, "_send_chunk", AsyncMock()) as chunk,
    ):
        with pytest.raises(CustodyEgressRefused, match="endpoint_held"):
            await email._send_email("friend@example.test", "s", "b")
        with pytest.raises(CustodyEgressRefused, match="endpoint_held"):
            await telegram._send_message(chat_id, "hello")
        smtp.assert_not_called()
        chunk.assert_not_awaited()

        chunk.return_value = {"ok": True, "result": {"message_id": 1}}
        await telegram._send_message(str(int(chat_id) + 1), "hello")
        chunk.assert_awaited_once()


async def test_owner_default_fallback_reads_real_holds(
    owner_pool: asyncpg.Pool, messenger_pool: asyncpg.Pool
) -> None:
    """'Owner fallback uses real unheld account' and 'No unheld destination'."""
    chat_id = str(uuid.uuid4().int % 10**9)
    mailbox = _unique("owner-") + "@example.test"
    await _hold(owner_pool, "has-handle", f"telegram:{chat_id}")

    choice = await choose_owner_default_target(
        messenger_pool, telegram_chat_id=chat_id, owner_email=mailbox
    )
    assert (choice.channel, choice.recipient) == ("email", mailbox)

    await _hold(owner_pool, "has-email", mailbox)
    none_left = await choose_owner_default_target(
        messenger_pool, telegram_chat_id=chat_id, owner_email=mailbox
    )
    assert (none_left.channel, none_left.refusal) == (None, "endpoint_held")
