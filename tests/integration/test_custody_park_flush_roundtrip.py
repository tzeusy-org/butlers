"""Real-Postgres: a custody-parked notice flushes once through the real Switchboard deliver path.

bu-s11n0s.1 S5b, REQ-endpoint-custody-holds-007 and REQ-endpoint-custody-holds-010. Drives the
real ``park_owner_default_notice`` and ``_tick_deferred_notification_pass`` on a migrated
Switchboard schema, the real ``public.custody_target_held`` / ``custody_target_episode`` reads,
a real ``public.custody_holds`` row, and the real ``deliver`` path as the notify function (the
production ``_scheduler_notify_fn`` calls this same ``deliver`` through the Switchboard client).
Only the Messenger transport call (``call_fn``) is captured. Docker; CI-only.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.core.custody_park import PARK_KEY, park_owner_default_notice
from butlers.core.scheduler.deferred_pass import _tick_deferred_notification_pass
from butlers.roster_imports import roster_module

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

CHAT = "424242"
EMAIL = "owner-custody-park@example.test"
TG = ("has-handle", f"telegram:{CHAT}")
MAIL = ("has-email", EMAIL)


@pytest.fixture
async def sb_pool(db):
    pool = await db(
        "core",
        "switchboard",
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        schema="switchboard",
    )
    owner = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('custody park owner', 'person', ARRAY['owner']) RETURNING id"
    )
    assert owner is not None
    for info_type, value in (("telegram_chat_id", CHAT), ("email", EMAIL)):
        await pool.execute(
            "INSERT INTO public.entity_info (entity_id, type, value, secured) "
            "VALUES ($1, $2, $3, false)",
            owner,
            info_type,
            value,
        )
    await pool.execute(
        "INSERT INTO butler_registry(name, endpoint_url, modules, last_seen_at, eligibility_state) "
        "VALUES ('messenger', 'http://localhost:41102/mcp', '[\"telegram\",\"email\"]'::jsonb, "
        "now(), 'active') ON CONFLICT (name) DO UPDATE SET last_seen_at = now(), "
        "eligibility_state = 'active'"
    )
    return pool


async def _hold(pool, kind: str, obj: str) -> tuple[uuid.UUID, uuid.UUID]:
    """An active hold row for the target; returns (hold id, episode)."""
    episode = uuid.uuid4()
    hold_id = await pool.fetchval(
        "INSERT INTO public.custody_holds (owner_entity_id, target_kind, target_digest, "
        "generation, episode_id, reason, source_command_id) "
        "VALUES ((SELECT id FROM public.entities WHERE canonical_name = 'custody park owner' "
        "AND 'owner' = ANY(roles) ORDER BY created_at DESC LIMIT 1), "
        "$1, public.custody_target_digest($1, $2), "
        "(SELECT COALESCE(max(generation), 0) + 1 FROM public.custody_holds "
        " WHERE target_kind = $1 AND target_digest = public.custody_target_digest($1, $2)), "
        "$3, 'lost', gen_random_uuid()) RETURNING id",
        kind,
        obj,
        episode,
    )
    return hold_id, episode


async def _release(pool, hold_id) -> None:
    await pool.execute("UPDATE public.custody_holds SET released_at = now() WHERE id = $1", hold_id)


def args_channel(args: dict[str, Any]) -> str:
    return args["input"]["context"]["notify_request"]["delivery"]["channel"]


def _deliver_fn(pool, calls: list[dict[str, Any]]):
    deliver = roster_module("switchboard", "tools/notification/deliver").deliver

    async def call_fn(endpoint_url: str, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {
            "status": "ok",
            "result": {
                "notify_response": {
                    "schema_version": "notify_response.v1",
                    "status": "ok",
                    "delivery": {"channel": args_channel(args), "delivery_id": "tg-42"},
                }
            },
        }

    async def notify_fn(envelope: dict[str, Any]) -> dict[str, Any]:
        result = await deliver(
            pool, source_butler="health", notify_request=envelope, call_fn=call_fn
        )
        if result.get("status") == "failed":
            raise RuntimeError(f"delivery failed: {result.get('error')}")
        return result

    return notify_fn


async def _park(pool) -> tuple[str, uuid.UUID]:
    _, episode = await _hold(pool, *TG)
    await _hold(pool, *MAIL)
    notif_id = await park_owner_default_notice(
        pool,
        butler_name="health",
        intent="send",
        priority="medium",
        message="Take your pills",
        targets=(TG, MAIL),
        now=datetime.now(UTC),
    )
    assert notif_id is not None
    return notif_id, episode


async def test_the_parked_row_keeps_its_episode_and_flushes_once_with_a_recipient(sb_pool) -> None:
    notif_id, episode = await _park(sb_pool)
    row = await sb_pool.fetchrow(
        "SELECT status, envelope FROM deferred_notifications WHERE id = $1::uuid", notif_id
    )
    assert row["status"] == "pending"
    assert row["envelope"][PARK_KEY]["episode"] == str(episode)

    calls: list[dict[str, Any]] = []
    notify_fn = _deliver_fn(sb_pool, calls)
    now = datetime.now(UTC)
    # Held: nothing is sent and the row stays parked.
    assert await _tick_deferred_notification_pass(sb_pool, now, notify_fn=notify_fn) == 0
    assert calls == []

    # Protected release of the Telegram target: the next flush sends once, to that chat.
    await _release(
        sb_pool,
        await sb_pool.fetchval(
            "SELECT id FROM public.custody_holds WHERE released_at IS NULL "
            "AND target_digest = public.custody_target_digest($1, $2)",
            *TG,
        ),
    )
    assert await _tick_deferred_notification_pass(sb_pool, now, notify_fn=notify_fn) == 1
    assert len(calls) == 1
    forwarded = calls[0]["input"]["context"]["notify_request"]["delivery"]
    assert forwarded["recipient"] == CHAT
    assert forwarded["channel"] == "telegram"
    assert forwarded["message"].endswith("Take your pills")
    assert forwarded["message"] != "Take your pills"  # the held-while-lost marker
    assert (
        await sb_pool.fetchval(
            "SELECT status FROM deferred_notifications WHERE id = $1::uuid", notif_id
        )
        == "delivered"
    )
    # A later tick never resends.
    assert await _tick_deferred_notification_pass(sb_pool, now, notify_fn=notify_fn) == 0
    assert len(calls) == 1


async def test_a_rehold_before_the_flush_sends_nothing(sb_pool) -> None:
    notif_id, _ = await _park(sb_pool)
    for kind, obj in (TG, MAIL):
        await _release(
            sb_pool,
            await sb_pool.fetchval(
                "SELECT id FROM public.custody_holds WHERE released_at IS NULL "
                "AND target_digest = public.custody_target_digest($1, $2)",
                kind,
                obj,
            ),
        )
        await _hold(sb_pool, kind, obj)  # held again before any flush ran
    calls: list[dict[str, Any]] = []
    assert (
        await _tick_deferred_notification_pass(
            sb_pool, datetime.now(UTC), notify_fn=_deliver_fn(sb_pool, calls)
        )
        == 0
    )
    assert calls == []
    assert (
        await sb_pool.fetchval(
            "SELECT status FROM deferred_notifications WHERE id = $1::uuid", notif_id
        )
        == "pending"
    )


async def test_two_concurrent_passes_send_once_and_a_failed_send_rolls_back(sb_pool) -> None:
    notif_id, _ = await _park(sb_pool)
    for kind, obj in (TG, MAIL):
        await _release(
            sb_pool,
            await sb_pool.fetchval(
                "SELECT id FROM public.custody_holds WHERE released_at IS NULL "
                "AND target_digest = public.custody_target_digest($1, $2)",
                kind,
                obj,
            ),
        )
    now = datetime.now(UTC)

    # A send that fails rolls the row lock back: the row stays pending and is retried.
    async def failing(envelope: dict[str, Any]) -> None:
        raise RuntimeError("transport down")

    assert await _tick_deferred_notification_pass(sb_pool, now, notify_fn=failing) == 0
    assert (
        await sb_pool.fetchval(
            "SELECT status FROM deferred_notifications WHERE id = $1::uuid", notif_id
        )
        == "pending"
    )

    sends: list[dict[str, Any]] = []
    gate = asyncio.Event()
    started = asyncio.Event()

    async def slow(envelope: dict[str, Any]) -> dict[str, str]:
        sends.append(envelope)
        started.set()
        await gate.wait()
        return {"status": "sent"}

    first = asyncio.create_task(_tick_deferred_notification_pass(sb_pool, now, notify_fn=slow))
    await asyncio.wait_for(started.wait(), timeout=30)
    # The row is locked by the first pass: the second skips it instead of sending again.
    assert await _tick_deferred_notification_pass(sb_pool, now, notify_fn=slow) == 0
    gate.set()
    assert await first == 1
    assert len(sends) == 1
    assert (
        await sb_pool.fetchval(
            "SELECT status FROM deferred_notifications WHERE id = $1::uuid", notif_id
        )
        == "delivered"
    )
