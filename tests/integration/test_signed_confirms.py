"""Migrated PostgreSQL authority and crash-safe informational answer resolution."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest

from butlers.core.confirm_models import ConfirmOption, ConfirmPrompt, Recipient
from butlers.core.confirm_service import ConfirmService, SinkRegistration
from butlers.core.confirm_tokens import mint_confirm_token
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_ledger_fixtures import codec

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def confirm_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard", "relationship"],
        schemas={
            "core": "switchboard",
            "switchboard": "switchboard",
            "relationship": "relationship",
        },
    )


@pytest.fixture
async def confirms(confirm_url):
    import asyncpg

    async with asyncpg.create_pool(confirm_url, init=codec, min_size=1, max_size=4) as pool:
        await pool.execute("TRUNCATE public.pending_confirms")
        await pool.execute("DROP TABLE IF EXISTS sink_receipts")
        await pool.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='connector_writer') THEN CREATE ROLE connector_writer NOLOGIN; END IF; END $$"
        )

        class RolePool:
            @asynccontextmanager
            async def acquire(self):
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
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
        Recipient(uuid4(), "42", "42", False),
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
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
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
        Recipient(uuid4(), "42", "42", False),
    )
    a = await service.create("chronicler", "a", prompt, sink="test", sink_ref="a")
    b = await service.create("chronicler", "b", prompt, sink="test", sink_ref="b")
    async with admin.acquire() as conn, conn.transaction():
        # Backdate via controlled migration fixture; application cannot edit immutable expiry.
        await conn.execute(
            "ALTER TABLE public.pending_confirms DISABLE TRIGGER confirm_identity_guard"
        )
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        assert (
            await conn.execute(
                "UPDATE public.pending_confirms SET created_at=now()-interval '2 hours', "
                "expires_at=now()-interval '1 hour' WHERE id=$1",
                a["id"],
            )
            == "UPDATE 1"
        )
        await conn.execute("RESET ROLE")
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


async def test_deadline_is_rechecked_after_row_lock_wait(confirms):
    from tests.polling import poll_until

    admin, runtime = confirms
    service = ConfirmService(
        runtime,
        secret="synthetic",
        sinks={"test": SinkRegistration("test", frozenset({"a", "b"}), lambda *args: None)},
    )
    row = await service.create(
        "test",
        "lock-wait",
        ConfirmPrompt(
            "Which?",
            (ConfirmOption("a", "A"), ConfirmOption("b", "B")),
            Recipient(uuid4(), "42", "100", False),
        ),
        sink="test",
        sink_ref="day",
    )
    token = service.keyboard(row)["inline_keyboard"][0][0]["callback_data"]
    async with admin.acquire() as conn, conn.transaction():
        await conn.execute(
            "ALTER TABLE public.pending_confirms DISABLE TRIGGER confirm_identity_guard"
        )
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        assert (
            await conn.execute(
                "UPDATE public.pending_confirms SET expires_at=clock_timestamp()+interval '1 second' WHERE id=$1",
                row["id"],
            )
            == "UPDATE 1"
        )
        await conn.execute("RESET ROLE")
        await conn.execute(
            "ALTER TABLE public.pending_confirms ENABLE TRIGGER confirm_identity_guard"
        )
    async with admin.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        assert await conn.fetchrow(
            "SELECT id FROM public.pending_confirms WHERE id=$1 FOR UPDATE", row["id"]
        )
        task = asyncio.create_task(service.answer_callback(token, sender_id="42", chat_id="100"))
        try:

            async def blocked():
                return await admin.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE 'SELECT%, expires_at%')"
                )

            async with asyncio.timeout(3):
                await poll_until(blocked, interval_s=0.01)
            await conn.execute("SELECT pg_sleep(1.1)")
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
    assert (await task)["status"] == "expired"
    assert (await service.get(row["id"]))["answer_index"] is None


async def test_real_callback_mount_preserves_auth_and_applies_once(confirms):
    from unittest.mock import MagicMock

    import httpx

    from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
    from tests.api.test_confirms import app_with_confirms

    admin, role_pool = confirms
    await admin.execute("CREATE TABLE sink_receipts (id UUID PRIMARY KEY,value TEXT)")

    async def sink(key, ref, value):
        await admin.execute(
            "INSERT INTO sink_receipts VALUES ($1,$2) ON CONFLICT DO NOTHING", key, value
        )
        return {"status": "applied"}

    service = ConfirmService(
        role_pool,
        secret="synthetic",
        sinks={"test": SinkRegistration("test", frozenset({"a", "b"}), sink)},
    )
    row = await service.create(
        "test",
        "callback",
        ConfirmPrompt(
            "Which?",
            (ConfirmOption("a", "A"), ConfirmOption("b", "B")),
            Recipient(uuid4(), "42", "100", False),
        ),
        sink="test",
        sink_ref="day",
    )
    app, _ = app_with_confirms()
    app.state.confirm_service = service
    acks = []

    def provider(request):
        import json

        assert request.url.path.endswith("/answerCallbackQuery")
        acks.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": True})

    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost/sse",
            endpoint_identity="telegram:bot:synthetic",
            telegram_token="synthetic",
            internal_api_url="http://dashboard",
            approval_callback_secret="synthetic",
            approval_callback_connector_token="connector-key",
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )
    async with httpx.AsyncClient(
        mounts={
            "http://dashboard": httpx.ASGITransport(app=app),
            "https://api.telegram.org": httpx.MockTransport(provider),
        }
    ) as client:
        connector._http_client = client
        update = {
            "update_id": 1,
            "callback_query": {
                "id": "tap",
                "from": {"id": 42},
                "message": {"chat": {"id": 100}},
                "data": service.keyboard(row)["inline_keyboard"][1][0]["callback_data"],
            },
        }
        await connector._process_update(update)
        assert (await service.get(row["id"]))["answer_index"] == 1
        assert "Applying" in acks[-1]["text"]
        await connector._process_update(update)
        assert "already" in acks[-1]["text"].lower()
        assert await service.dispatch_one() == row["id"]
        assert await service.dispatch_one() is None
        assert await admin.fetchval("SELECT count(*) FROM sink_receipts") == 1
        # Reusing the owner dashboard credential is not how callback ingress works.
        response = await client.post(
            "http://dashboard/api/confirms/callback",
            json={"token": update["callback_query"]["data"], "sender_id": "42", "chat_id": "100"},
            headers={"X-API-Key": "owner-key"},
        )
        assert response.status_code == 403


async def test_delivery_restart_ambiguity_rendering_and_error_fairness(confirms):
    from unittest.mock import AsyncMock

    from butlers.core.confirm_delivery import ConfirmDelivery

    admin, role_pool = confirms

    async def sink(*args):
        raise TimeoutError("synthetic")

    service = ConfirmService(
        role_pool,
        secret="synthetic",
        sinks={"test": SinkRegistration("test", frozenset({"a", "b"}), sink)},
    )
    prompt = ConfirmPrompt(
        "Which?",
        (ConfirmOption("a", "A"), ConfirmOption("b", "B")),
        Recipient(uuid4(), "42", "100", False),
    )
    a = await service.create("test", "a", prompt, sink="test", sink_ref="a")
    b = await service.create("test", "b", prompt, sink="test", sink_ref="b")
    send = AsyncMock(side_effect=TimeoutError("lost provider reply"))
    delivery = ConfirmDelivery(
        service, send=send, edit=AsyncMock(return_value=True), allowed=AsyncMock(return_value=False)
    )
    assert not await delivery.deliver(a["id"])
    deferred = await service.get(a["id"])
    assert deferred["delivery_status"] == "new" and deferred["updated_at"] > a["updated_at"]
    send.assert_not_awaited()
    delivery.allowed.return_value = True
    assert not await delivery.deliver(a["id"])
    assert (await service.get(a["id"]))["delivery_status"] == "ambiguous"
    restarted = ConfirmDelivery(service, send=send, edit=delivery.edit, allowed=delivery.allowed)
    assert not await restarted.deliver(a["id"])
    send.assert_awaited_once()
    send.side_effect = None
    send.return_value = {"status": "confirmed", "reference": "99"}
    assert await restarted.deliver(b["id"])
    for row in (a, b):
        token = service.keyboard(row)["inline_keyboard"][0][0]["callback_data"]
        await service.answer_callback(token, sender_id="42", chat_id="100")
    await restarted.cycle()
    assert (await service.get(a["id"]))["application_status"] == "error"
    assert (await service.get(b["id"]))["application_status"] == "error"
    assert send.await_count == 2
    assert delivery.edit.await_count == 1
    assert (await service.get(b["id"]))["rendered_status"] == "answered:error"
    # Rendering alone cannot send or apply again, even concurrently after restart.
    await asyncio.gather(restarted.render(b["id"]), delivery.render(b["id"]))
    assert delivery.edit.await_count == 1 and send.await_count == 2
    unauthorized = ConfirmService(admin, secret="synthetic", sinks={})
    with pytest.raises(ValueError, match="restricted confirm service role"):
        await unauthorized.get(a["id"])


async def test_owner_pages_retain_older_actionable_questions(confirms):
    admin, runtime = confirms
    service = ConfirmService(
        runtime,
        secret="synthetic",
        sinks={"test": SinkRegistration("test", frozenset({"a", "b"}), lambda *args: None)},
    )
    prompt = ConfirmPrompt(
        "Which?",
        (ConfirmOption("a", "A"), ConfirmOption("b", "B")),
        Recipient(uuid4(), "42", "100", True),
    )
    ids = []
    for index in range(52):
        row = await service.create("test", str(index), prompt, sink="test", sink_ref=str(index))
        ids.append(str(row["id"]))
    first = await service.list_owner()
    assert len(first["rows"]) == 50 and first["next_cursor"]
    second = await service.list_owner(before=UUID(first["next_cursor"]))
    assert second["next_cursor"] is None
    found = [str(row["id"]) for row in first["rows"] + second["rows"]]
    assert found == ids[::-1] and len(set(found)) == 52
    assert all(row["status"] == "pending" for row in second["rows"])
    with pytest.raises(ValueError, match="invalid_confirm_cursor"):
        await service.list_owner(before=uuid4())


@pytest.mark.parametrize("failure", ["policy", "render"])
async def test_failed_batch_does_not_strand_later_prompt(confirms, failure):
    from unittest.mock import AsyncMock

    from butlers.core.confirm_delivery import ConfirmDelivery

    admin, runtime = confirms
    service = ConfirmService(
        runtime,
        secret="synthetic",
        sinks={"test": SinkRegistration("test", frozenset({"a", "b"}), lambda *args: None)},
    )
    prompt = ConfirmPrompt(
        "Which?",
        (ConfirmOption("a", "A"), ConfirmOption("b", "B")),
        Recipient(uuid4(), "42", "100", False),
    )
    for index in range(3):
        row = await service.create("test", f"poison-{index}", prompt, sink="test", sink_ref="day")
        if failure == "render":
            async with runtime.acquire() as conn:
                await conn.execute(
                    "UPDATE public.pending_confirms SET status='answered',answer_index=0,answered_at=clock_timestamp(),answer_channel='telegram',application_status='applied',delivery_status='confirmed',message_ref='1' WHERE id=$1",
                    row["id"],
                )
    healthy = await service.create("test", "healthy", prompt, sink="test", sink_ref="day")

    async def allowed(row):
        if failure == "policy" and row["producer_key"].startswith("poison"):
            raise RuntimeError("unavailable source policy")
        return True

    send = AsyncMock(return_value={"status": "confirmed", "reference": "99"})
    delivery = ConfirmDelivery(
        service, send=send, edit=AsyncMock(return_value=False), allowed=allowed
    )
    await delivery.cycle(limit=2)
    send.assert_not_awaited()
    await delivery.cycle(limit=2)
    assert (await service.get(healthy["id"]))["delivery_status"] == "confirmed"
    send.assert_awaited_once()
    assert send.await_args.args[0]["id"] == healthy["id"]
    await delivery.cycle(limit=2)
    send.assert_awaited_once()
