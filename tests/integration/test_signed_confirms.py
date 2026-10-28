"""Migrated PostgreSQL authority and crash-safe informational answer resolution."""

from __future__ import annotations

import asyncio
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from butlers.core.confirm_models import ConfirmOption, ConfirmPrompt, Recipient
from butlers.core.confirm_service import ConfirmService, SinkRegistration
from butlers.core.confirm_tokens import mint_confirm_token

pytestmark = pytest.mark.integration
_MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic/versions/core/core_282_signed_confirms.py"
)


@pytest.fixture
async def confirms(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        path = _MIGRATION
        spec = importlib.util.spec_from_file_location("confirm_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        sql = []
        migration.op = SimpleNamespace(execute=sql.append)
        migration.upgrade()
        async with pool.acquire() as conn:
            await conn.execute("""DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='butler_messenger_rw') THEN
                    CREATE ROLE butler_messenger_rw NOLOGIN;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='connector_writer') THEN
                    CREATE ROLE connector_writer NOLOGIN;
                END IF;
                END $$""")
            for statement in sql:
                await conn.execute(statement)

        class RolePool:
            @asynccontextmanager
            async def acquire(self):
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute("SET LOCAL ROLE butler_messenger_rw")
                        yield conn

        yield pool, RolePool()


async def test_recipient_expiry_race_and_restart_application(confirms):
    admin, role_pool = confirms
    await admin.execute("CREATE TABLE sink_receipts (id UUID PRIMARY KEY, ref TEXT, value TEXT)")
    calls = []
    lose_reply = True

    async def sink(key, ref, value):
        nonlocal lose_reply
        calls.append((key, ref, value))
        await admin.execute(
            "INSERT INTO sink_receipts VALUES ($1,$2,$3) ON CONFLICT (id) DO NOTHING",
            key,
            ref,
            value,
        )
        if lose_reply:
            lose_reply = False
            raise TimeoutError("synthetic lost response")
        return {"status": "applied", "key": str(key)}

    registry = {"test": SinkRegistration("chronicler", frozenset({"confirm", "correct"}), sink)}
    service = ConfirmService(role_pool, secret="synthetic-only", sinks=registry)
    prompt = ConfirmPrompt(
        "Work?",
        (ConfirmOption("confirm", "Work"), ConfirmOption("correct", "Not work")),
        Recipient(uuid4(), "42", "42", True),
    )
    card = await service.create("chronicler", "day", prompt, sink="test", sink_ref="day")
    token = mint_confirm_token(card["id"], 1, chat_id="42", secret="synthetic-only")
    assert (await service.answer_callback(token, sender_id="43", chat_id="42"))[
        "status"
    ] == "refused"
    answers = await asyncio.gather(
        *(service.answer_callback(token, sender_id="42", chat_id="42") for _ in range(2))
    )
    assert [a["status"] for a in answers].count("answered") == 1
    await service.dispatch_one()
    assert (await service.get(card["id"]))["application_status"] == "error"
    restarted = ConfirmService(role_pool, secret="synthetic-only", sinks=registry)
    await restarted.dispatch_one()
    result = await restarted.get(card["id"])
    assert result["application_status"] == "applied"
    assert len(calls) == 2 and await admin.fetchval("SELECT count(*) FROM sink_receipts") == 1
    assert calls[0] == calls[1]
    await restarted.dispatch_one()
    assert len(calls) == 2
    # Duplicate producer requests retrieve the immutable prompt, not a second send.
    assert (await service.create("chronicler", "day", prompt, sink="test", sink_ref="day"))[
        "id"
    ] == card["id"]
    async with admin.acquire() as conn:
        # Replay bootstrap's deliberately broad GRANT; forced RLS still denies connector writes.
        await conn.execute("GRANT ALL ON public.pending_confirms TO connector_writer")
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE connector_writer")
            assert await conn.fetchval("SELECT count(*) FROM public.pending_confirms") == 0
            assert (
                await conn.execute("UPDATE public.pending_confirms SET status='expired'")
                == "UPDATE 0"
            )
        with pytest.raises(Exception, match="immutable"):
            await conn.execute("UPDATE public.pending_confirms SET prompt='{}'::jsonb")


async def test_late_tap_and_sweep_independent_prompts(confirms):
    admin, role_pool = confirms

    async def sink(*args):
        pytest.fail("expired prompt must not apply a choice")

    service = ConfirmService(
        role_pool,
        secret="synthetic-only",
        sinks={"test": SinkRegistration("chronicler", frozenset({"a", "b"}), sink)},
    )
    prompt = ConfirmPrompt(
        "Which?",
        (ConfirmOption("a", "A"), ConfirmOption("b", "B")),
        Recipient(uuid4(), "42", "42", True),
    )
    a = await service.create("chronicler", "a", prompt, sink="test", sink_ref="a")
    b = await service.create("chronicler", "b", prompt, sink="test", sink_ref="b")
    async with admin.acquire() as conn:
        # Backdate via controlled migration fixture; application cannot edit immutable expiry.
        await conn.execute(
            "ALTER TABLE public.pending_confirms DISABLE TRIGGER confirm_identity_guard"
        )
        await conn.execute(
            "UPDATE public.pending_confirms SET created_at=now()-interval '2 hours', "
            "expires_at=now()-interval '1 hour' WHERE id=$1",
            a["id"],
        )
        await conn.execute(
            "ALTER TABLE public.pending_confirms ENABLE TRIGGER confirm_identity_guard"
        )
    token = mint_confirm_token(a["id"], 0, chat_id="42", secret="synthetic-only")
    assert (await service.answer_callback(token, sender_id="42", chat_id="42"))[
        "status"
    ] == "expired"
    await service.expire()
    await service.dispatch_one()
    assert (await service.get(a["id"]))["application_status"] == "applied"
    assert (await service.get(b["id"]))["status"] == "pending"
