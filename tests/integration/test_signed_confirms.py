"""Migrated PostgreSQL authority and crash-safe informational answer resolution."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from butlers.core.confirm_models import ConfirmOption, ConfirmPrompt, Recipient
from butlers.core.confirm_service import ConfirmService, SinkRegistration
from butlers.core.confirm_tokens import mint_confirm_token

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
async def confirms(db):
    pool = await db(
        "core",
        "switchboard",
        "relationship",
        schemas={
            "core": "switchboard",
            "switchboard": "switchboard",
            "relationship": "relationship",
        },
        max_size=4,
    )
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

    return pool, RolePool()


async def test_recipient_expiry_race_and_restart_application(confirms):
    admin, role_pool = confirms
    # The sink's own idempotent store: one receipt per application key.
    sink_receipts: dict = {}
    calls = []
    lose_reply = True

    async def sink(key, ref, value):
        nonlocal lose_reply
        calls.append((key, ref, value))
        sink_receipts.setdefault(key, (ref, value))
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
    assert len(calls) == 2 and len(sink_receipts) == 1
    assert calls[0] == calls[1]
    await restarted.dispatch_one()
    assert len(calls) == 2
    # Duplicate producer requests retrieve the immutable prompt, not a second send.
    assert (await service.create("chronicler", "day", prompt, sink="test", sink_ref="day"))[
        "id"
    ] == card["id"]
    # Prompt copy outlives settlement by the retention horizon only (bu-jv1moa.46).
    assert await restarted.redact_settled() == 0
    async with admin.acquire() as conn, conn.transaction():
        await conn.execute(
            "ALTER TABLE public.pending_confirms DISABLE TRIGGER confirm_identity_guard"
        )
        # The owner is under FORCE RLS with no UPDATE policy; backdate as the service role.
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        assert (
            await conn.execute(
                "UPDATE public.pending_confirms SET answered_at=now()-interval '15 days' "
                "WHERE id=$1",
                card["id"],
            )
            == "UPDATE 1"
        )
        await conn.execute("RESET ROLE")
        await conn.execute(
            "ALTER TABLE public.pending_confirms ENABLE TRIGGER confirm_identity_guard"
        )
    with pytest.raises(Exception, match="re-enter application"):
        async with role_pool.acquire() as conn:
            # One statement cannot both redact and reopen application.
            await conn.execute(
                "UPDATE public.pending_confirms SET "
                "prompt=public.confirm_redacted_prompt(prompt), application_status='pending' "
                "WHERE id=$1",
                card["id"],
            )
    assert await restarted.redact_settled() == 1
    redacted = await restarted.get(card["id"])
    assert set(redacted["prompt"]) == {"redacted", "digest"} and "Work" not in str(redacted)
    assert (redacted["answer_index"], redacted["application_status"]) == (1, "applied")
    # The tombstone digest still binds the producer key to its original question.
    assert (await service.create("chronicler", "day", prompt, sink="test", sink_ref="day"))[
        "id"
    ] == card["id"]
    other = ConfirmPrompt("Rest?", prompt.options, prompt.recipient)
    with pytest.raises(ValueError, match="different immutable prompt"):
        await service.create("chronicler", "day", other, sink="test", sink_ref="day")
    assert (await service.answer_callback(token, sender_id="42", chat_id="42"))[
        "status"
    ] == "already_handled"
    with pytest.raises(Exception, match="re-enter application"):
        async with role_pool.acquire() as conn:
            await conn.execute(
                "UPDATE public.pending_confirms SET application_status='pending' WHERE id=$1",
                card["id"],
            )
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
    # The sink's own idempotent store: one receipt per application key.
    sink_receipts: dict = {}

    async def sink(key, ref, value):
        sink_receipts.setdefault(key, value)
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
        assert len(sink_receipts) == 1
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


# ---------------------------------------------------------------------------
# relationship.commitment_candidate: owner-chat candidate cards and their sink
# (REQ-commitment-lifecycle-013, -014; bu-jv1moa.11)
# ---------------------------------------------------------------------------

_OWNER = UUID("00000000-0000-4000-8000-0000000000aa")
_CC_SOURCE = "relationship:commitment"


def _relationship(module: str):
    from butlers.roster_imports import roster_module

    return roster_module("relationship", f"tools/{module}")


async def _owner_recipient():
    return Recipient(_OWNER, "42", "42", True)


async def _person(admin, name: str = "Sam Rivera") -> UUID:
    return await admin.fetchval(
        "INSERT INTO public.entities(canonical_name,entity_type) VALUES($1,'person') RETURNING id",
        f"{name} {uuid4().hex[:6]}",
    )


async def _candidate(admin, person: UUID, action: str, *, event_at=None, lapse_in=None) -> UUID:
    from butlers.core.commitments import commitment_fingerprint

    fingerprint = commitment_fingerprint(
        source=_CC_SOURCE, counterparty_entity_id=str(person), action_description=action
    )
    return await admin.fetchval(
        """
        INSERT INTO relationship.commitment_candidates (
            source_key, state, kind, direction, counterparty_entity_id, action_description,
            confidence, marker, fingerprint, channel, evidence_ref, taint, event_time, lapse_at)
        VALUES ($1, 'pending', 'promise', 'owner_to_other', $2, $3, 0.9, 'i''ll', $4,
                'whatsapp_user_client',
                '{"ingestion_event_id": "ing-1", "provider_message_id": "m-1"}'::jsonb,
                'untrusted_origin', COALESCE($5::timestamptz, now() - interval '1 hour'),
                COALESCE(now() + $6::interval,
                         COALESCE($5::timestamptz, now() - interval '1 hour') + interval '14 days'))
        RETURNING id
        """,
        uuid4().hex + uuid4().hex,
        person,
        action,
        fingerprint,
        event_at,
        lapse_in,
    )


def _candidate_harness(admin, role_pool, owner_recipient=_owner_recipient):
    """The topology's producer and sink wiring, without the kernel-peer sockets."""
    cards = _relationship("commitment_candidate_cards")
    source = cards.build_source(admin, owner_recipient=owner_recipient)
    service = ConfirmService(
        role_pool,
        secret="synthetic-only",
        sinks={cards.SOURCE_NAME: SinkRegistration(cards.SOURCE_NAME, source.values, source.apply)},
    )

    async def request(key, data):
        prompt, ref = await source.prepare(key, data)
        row = await service.create(
            cards.SOURCE_NAME, key, prompt, sink=cards.SOURCE_NAME, sink_ref=ref
        )
        return {"status": "accepted", "confirm_id": str(row["id"])}

    async def step():
        return await cards.card_step(admin, request=request, owner_recipient=owner_recipient)

    return source, service, step


async def _owner_answers(service, confirm_id, option: int):
    from butlers.core.owner_auth_context import verified_http_principal

    token = verified_http_principal.set("owner")
    try:
        return await service.answer_owner(confirm_id, option)
    finally:
        verified_http_principal.reset(token)


async def _row(admin, candidate: UUID):
    return await admin.fetchrow(
        "SELECT * FROM relationship.commitment_candidates WHERE id = $1", candidate
    )


async def _active(admin, fingerprint: str):
    from butlers.core.owner_conditions import get_active_condition

    return await get_active_condition(admin, source=_CC_SOURCE, fingerprint=fingerprint)


async def _cards_sent(admin) -> int:
    return await admin.fetchval(
        "SELECT count(*) FROM public.pending_confirms WHERE producer=$1",
        "relationship.commitment_candidate",
    )


async def _drain(service) -> None:
    while await service.dispatch_one() is not None:
        pass


async def test_owner_chat_track_done_and_ignore_apply_once_with_receipts(confirms):
    admin, role_pool = confirms
    source, service, step = _candidate_harness(admin, role_pool)
    sam = await _person(admin)
    track = await _candidate(admin, sam, "send you the deck")
    done = await _candidate(admin, sam, "call you tonight")
    ignore = await _candidate(admin, sam, "bring the book")
    fingerprints = {c: (await _row(admin, c))["fingerprint"] for c in (track, done, ignore)}

    assert await step() == {"carded": 3}
    rows = {c: await _row(admin, c) for c in (track, done, ignore)}
    assert all(row["state"] == "carded" and row["confirm_id"] for row in rows.values())
    prompt = (await service.get(rows[track]["confirm_id"]))["prompt"]
    assert [o["value"] for o in prompt["options"]] == ["track", "done", "ignore"]
    assert prompt["quotes"] == ["send you the deck"]

    for candidate, option in ((track, 0), (done, 1), (ignore, 2)):
        answered = await _owner_answers(service, rows[candidate]["confirm_id"], option)
        assert answered["status"] == "answered"
    await _drain(service)

    tracked = await _active(admin, fingerprints[track])
    evidence = tracked["metadata"]["evidence_opened"]
    assert evidence["source"] == "owner_chat_candidate"
    assert evidence["confirm_id"] == str(rows[track]["confirm_id"])
    assert evidence["candidate_id"] == str(track)
    assert not {"utterance", "excerpt", "text"} & set(evidence)
    assert await _active(admin, fingerprints[done]) is None
    assert await _active(admin, fingerprints[ignore]) is None
    after = {c: await _row(admin, c) for c in (track, done, ignore)}
    assert [after[c]["state"] for c in (track, done, ignore)] == ["tracked", "done", "ignored"]
    assert after[track]["commitment_fingerprint"] == fingerprints[track]
    assert all(row["action_description"] is None for row in after.values())

    # A retried confirm_id returns the stored receipt and writes nothing again,
    # whatever value the retry carries.
    first = await source.apply(rows[track]["confirm_id"], str(track), "track")
    again = await source.apply(rows[track]["confirm_id"], str(track), "ignore")
    assert (
        first
        == again
        == {
            "status": "applied",
            "outcome": "tracked",
            "fingerprint": fingerprints[track],
        }
    )
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM public.owner_conditions WHERE source = $1", _CC_SOURCE
        )
        == 1
    )
    assert (
        await admin.fetchval("SELECT count(*) FROM relationship.commitment_candidate_receipts") == 3
    )


async def test_owner_chat_forged_wrong_chat_replayed_and_expired_taps_change_nothing(confirms):
    admin, role_pool = confirms
    _, service, step = _candidate_harness(admin, role_pool)
    sam = await _person(admin)
    candidate = await _candidate(admin, sam, "send you the deck")
    await step()
    confirm_id = (await _row(admin, candidate))["confirm_id"]

    forged = mint_confirm_token(confirm_id, 0, chat_id="42", secret="not-the-secret")
    assert (await service.answer_callback(forged, sender_id="42", chat_id="42"))[
        "status"
    ] == "refused"
    wrong_chat = mint_confirm_token(confirm_id, 0, chat_id="43", secret="synthetic-only")
    assert (await service.answer_callback(wrong_chat, sender_id="42", chat_id="43"))[
        "status"
    ] == "refused"
    async with admin.acquire() as conn, conn.transaction():
        await conn.execute(
            "ALTER TABLE public.pending_confirms DISABLE TRIGGER confirm_identity_guard"
        )
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        await conn.execute(
            "UPDATE public.pending_confirms SET expires_at=now()-interval '1 minute' WHERE id=$1",
            confirm_id,
        )
        await conn.execute("RESET ROLE")
        await conn.execute(
            "ALTER TABLE public.pending_confirms ENABLE TRIGGER confirm_identity_guard"
        )
    assert (await _owner_answers(service, confirm_id, 0))["status"] == "expired"
    assert (await _owner_answers(service, confirm_id, 0))["status"] == "already_handled"
    await _drain(service)

    row = await _row(admin, candidate)
    assert (row["state"], row["confirm_id"]) == ("carded", confirm_id)
    assert await _active(admin, row["fingerprint"]) is None
    assert (
        await admin.fetchval("SELECT count(*) FROM relationship.commitment_candidate_receipts") == 0
    )
    # The expired card is never re-sent: the candidate stays carded until it lapses.
    assert await step() == {}
    assert await _cards_sent(admin) == 1


async def test_owner_chat_lapse_and_track_race_first_terminal_wins(confirms):
    admin, role_pool = confirms
    cc = _relationship("commitment_candidates")
    source, _, step = _candidate_harness(admin, role_pool)
    sam = await _person(admin)
    lapsed_first = await _candidate(admin, sam, "send you the deck")
    raced = await _candidate(admin, sam, "call you tonight")
    await step()
    first_id = (await _row(admin, lapsed_first))["confirm_id"]
    raced_row = await _row(admin, raced)
    await admin.execute(
        "UPDATE relationship.commitment_candidates SET lapse_at = now() WHERE id = $1",
        lapsed_first,
    )

    await cc.lapse_and_purge(admin)
    late = await source.apply(first_id, str(lapsed_first), "track")
    assert late == {"status": "applied", "outcome": "already_lapsed"}
    assert await _active(admin, raced_row["fingerprint"]) is None

    # Concurrent: the candidate row lock serializes them; whichever commits first wins.
    await admin.execute(
        "UPDATE relationship.commitment_candidates SET lapse_at = now() WHERE id = $1", raced
    )
    receipt, _ = await asyncio.gather(
        source.apply(raced_row["confirm_id"], str(raced), "track"), cc.lapse_and_purge(admin)
    )
    final = await _row(admin, raced)
    if receipt["outcome"] == "tracked":
        assert final["state"] == "tracked"
        assert await _active(admin, raced_row["fingerprint"]) is not None
    else:
        assert (receipt["outcome"], final["state"]) == ("already_lapsed", "lapsed")
        assert await _active(admin, raced_row["fingerprint"]) is None


async def test_owner_chat_card_step_repeated_returns_the_same_confirm(confirms):
    admin, role_pool = confirms
    _, service, step = _candidate_harness(admin, role_pool)
    sam = await _person(admin)
    # Inside the final 24 hours before lapse the timeout is bounded by lapse_at.
    candidate = await _candidate(admin, sam, "send you the deck", lapse_in=timedelta(hours=20))
    await step()
    first = await _row(admin, candidate)
    snapshot = first["card_snapshot"]
    snapshot = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
    assert 0 < snapshot["timeout_seconds"] <= 20 * 3600

    # A crash after the confirm was created but before the candidate was marked carded.
    await admin.execute(
        "UPDATE relationship.commitment_candidates SET state='pending', confirm_id=NULL, "
        "card_expires_at=NULL WHERE id=$1",
        candidate,
    )
    await asyncio.sleep(1.1)  # a recomputed timeout would now differ and be refused
    assert await step() == {"carded": 1}
    again = await _row(admin, candidate)
    assert again["confirm_id"] == first["confirm_id"]
    assert await _cards_sent(admin) == 1
    stored = (await service.get(first["confirm_id"]))["prompt"]
    assert stored["timeout_seconds"] == snapshot["timeout_seconds"]


async def test_owner_chat_track_on_an_active_fingerprint_keeps_its_evidence(confirms):
    from butlers.core.commitments import create_commitment

    admin, role_pool = confirms
    _, service, step = _candidate_harness(admin, role_pool)
    sam = await _person(admin)
    candidate = await _candidate(admin, sam, "send you the deck")
    superseded = await _candidate(admin, sam, "call you tonight")
    routed = {"source": "routed_message", "utterance": "routed opening"}

    async def _routed(action):
        await create_commitment(
            admin,
            source=_CC_SOURCE,
            summary=action,
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=str(sam),
            confidence=0.9,
            evidence_opened=routed,
            action_description=action,
        )

    # Active at card time: superseded, no card.
    await _routed("call you tonight")
    assert await step() == {"carded": 1, "superseded": 1}
    assert (await _row(admin, superseded))["state"] == "superseded"
    # Active after carding (the routed REQ-commitment-lifecycle-007 path won): Track writes nothing.
    await _routed("send you the deck")
    row = await _row(admin, candidate)
    await _owner_answers(service, row["confirm_id"], 0)
    await _drain(service)
    condition = await _active(admin, row["fingerprint"])
    assert condition["metadata"]["evidence_opened"] == routed
    assert (await _row(admin, candidate))["state"] == "tracked"
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM public.owner_conditions WHERE source=$1 AND fingerprint=$2",
            _CC_SOURCE,
            row["fingerprint"],
        )
        == 1
    )


async def test_owner_chat_posture_defers_the_card(confirms):
    admin, role_pool = confirms
    _, _, step = _candidate_harness(admin, role_pool)
    sam = await _person(admin)
    candidate = await _candidate(admin, sam, "send you the deck")
    await admin.execute("UPDATE public.entities SET posture='quiet' WHERE id=$1", sam)

    assert await step() == {"posture_deferred": 1}
    assert (await _row(admin, candidate))["state"] == "pending"
    await admin.execute("UPDATE public.entities SET posture='active' WHERE id=$1", sam)
    assert await step() == {"carded": 1}


async def test_owner_chat_unavailable_owner_recipient_leaves_the_candidate_pending(confirms):
    admin, role_pool = confirms

    async def no_recipient():
        return None

    _, _, step = _candidate_harness(admin, role_pool, owner_recipient=no_recipient)
    sam = await _person(admin)
    candidate = await _candidate(admin, sam, "send you the deck")

    assert await step() == {"recipient_unavailable": 1}
    row = await _row(admin, candidate)
    assert (row["state"], row["card_snapshot"]) == ("pending", None)
    assert await _cards_sent(admin) == 0


async def test_owner_chat_four_promises_across_owner_midnight_each_get_one_card(confirms):
    """No per-counterparty cap in S1 (REQ-commitment-lifecycle-017)."""
    admin, role_pool = confirms
    _, _, step = _candidate_harness(admin, role_pool)
    sam = await _person(admin)
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    for index, minutes in enumerate((-90, -10, 10, 90)):
        await _candidate(
            admin,
            sam,
            f"send you part {index}",
            event_at=midnight - timedelta(days=1) + timedelta(minutes=minutes),
        )

    assert await step() == {"carded": 4}
    assert await _cards_sent(admin) == 4
