"""Park and flush of custody-refused owner notices (bu-s11n0s.1 S5b).

openspec change ``endpoint-custody-holds``: REQ-endpoint-custody-holds-007 (park instead of only
refusing) and REQ-endpoint-custody-holds-010 (automatic flush once on release, marker, no replay
of anything not exactly generic). The custody read and the deferred table are faked; the real
``public.custody_target_held`` is covered in ``tests/migrations``.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.config import ButlerType
from butlers.core.custody_egress import OwnerDefaultTarget, choose_owner_default_target
from butlers.core.custody_park import (
    HELD_WHILE_LOST_MARKER,
    PARK_KEY,
    build_parked_envelope,
    flush_custody_parked,
    park_owner_default_notice,
    parked_targets,
)
from butlers.core.scheduler.deferred_pass import _tick_deferred_notification_pass
from butlers.core_tools._base import ToolContext
from butlers.core_tools._notifications import register_notification_tools

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
TG = ("has-handle", "telegram:111")
EMAIL = ("has-email", "owner@example.test")


class FakeStore:
    """A deferred_notifications table and the custody read, with row locks and transactions."""

    def __init__(self, held: set[tuple[str, str]]) -> None:
        self.held = held
        self.rows: dict[uuid.UUID, dict[str, Any]] = {}
        self.locks: set[uuid.UUID] = set()
        self.lookups = 0

    def add(self, envelope: dict[str, Any], *, age: timedelta = timedelta(0)) -> uuid.UUID:
        row_id = uuid.uuid4()
        self.rows[row_id] = {
            "id": row_id,
            "butler_name": "health",
            "channel": "telegram",
            "message": "m",
            "priority": "medium",
            "envelope": envelope,
            "deliver_at": NOW - age,
            "status": "pending",
        }
        return row_id

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "custody_target_held" in sql:
            self.lookups += 1
            await asyncio.sleep(0)
            return any((p, o) in self.held for p, o in zip(args[0], args[1], strict=True))
        row = self.rows.get(args[0])
        if row is None or row["status"] != "pending" or row["deliver_at"] > args[1]:
            return None
        if row["id"] in self.locks:
            return None  # SKIP LOCKED
        self.locks.add(row["id"])
        return row["id"]

    async def execute(self, sql: str, *args: Any) -> None:
        if "SET status = $3" in sql:
            row = self.rows[args[0]]
            if row["status"] == "pending":
                row["status"] = args[2]
        elif "SET status = 'cancelled'" in sql:
            row = self.rows[args[0]]
            if row["status"] == "pending":
                row["status"] = "cancelled"

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        before = set(self.locks)
        try:
            yield
        finally:
            self.locks = before


def _envelope(targets=(TG, EMAIL)) -> dict[str, Any]:
    return build_parked_envelope(
        origin_butler="health", intent="send", message="Take your pills", targets=list(targets)
    )


@pytest.fixture(autouse=True)
def _ledger(monkeypatch):
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr("butlers.core.attention_ledger.record_attention_event", mock)
    owner = {"telegram_chat_id": "111", "email": "owner@example.test"}
    monkeypatch.setattr(
        "butlers.credential_store.resolve_owner_entity_info",
        AsyncMock(side_effect=lambda pool, info_type: owner.get(info_type)),
    )
    return mock


# park (REQ-endpoint-custody-holds-007)


async def test_a_held_refusal_carries_every_held_target() -> None:
    class Pool:
        async def fetchval(self, sql, predicates, objects):
            return True

    choice = await choose_owner_default_target(
        Pool(), telegram_chat_id="111", owner_email="owner@example.test"
    )
    assert choice.refusal == "endpoint_held"
    assert choice.held_targets == (TG, EMAIL)


async def test_an_unreadable_refusal_carries_no_targets() -> None:
    class Pool:
        async def fetchval(self, sql, predicates, objects):
            raise ConnectionError("down")

    choice = await choose_owner_default_target(
        Pool(), telegram_chat_id="111", owner_email="owner@example.test"
    )
    assert choice.refusal == "eligibility_unknown"
    assert choice.held_targets == ()


@pytest.fixture
def notification(monkeypatch):
    for module, name, value in (
        ("butlers.core_tools._notifications", "check_permission", SimpleNamespace(allowed=True)),
        ("butlers.core.temporal.delivery_db", "get_delivery_preferences", None),
        ("butlers.core_tools._notifications", "get_suppressing_context", None),
        ("butlers.fleet_events", "publish_fleet_event", None),
    ):
        monkeypatch.setattr(f"{module}.{name}", AsyncMock(return_value=value))
    ledger = AsyncMock(return_value=None)
    monkeypatch.setattr("butlers.core_tools._notifications.record_attention_event", ledger)
    monkeypatch.setattr(
        "butlers.core.custody_park._hold_episode", AsyncMock(return_value="episode-1")
    )
    insert = AsyncMock(return_value="parked-id")
    monkeypatch.setattr("butlers.core.temporal.delivery_db.insert_deferred_notification", insert)
    target = AsyncMock()
    monkeypatch.setattr("butlers.core_tools._notifications._owner_default_custody_target", target)
    monkeypatch.setattr(
        "butlers.core_tools._notifications.get_current_runtime_session_id", lambda: "s"
    )
    daemon = SimpleNamespace(
        db=SimpleNamespace(pool=object()),
        switchboard_client=SimpleNamespace(call_tool=AsyncMock()),
        _approval_push_runtime=None,
        _resolve_default_notify_recipient=AsyncMock(return_value="111"),
    )
    tools: dict[str, Any] = {}

    def register(group, **kwargs):
        def decorate(fn):
            tools[fn.__name__] = fn
            return fn

        return decorate

    register_notification_tools(
        ToolContext(daemon, daemon.db.pool, None, "health", ButlerType.BUTLER, False, False, None),
        None,
        register,
    )
    return tools["notify"], daemon, insert, target, ledger


async def test_notify_parks_an_owner_default_notice_when_every_target_is_held(notification):
    notify, daemon, insert, target, ledger = notification
    target.return_value = OwnerDefaultTarget(None, None, "endpoint_held", (TG, EMAIL))
    result = await notify(intent="send", message="Take your pills")
    assert result["status"] == "deferred"
    assert result["code"] == "endpoint_held"
    assert result["notification_id"] == "parked-id"
    envelope = insert.call_args.kwargs["envelope"]
    assert parked_targets(envelope) == [TG, EMAIL]
    assert envelope[PARK_KEY]["episode"] == "episode-1"
    assert envelope["delivery"] == {
        "intent": "send",
        "channel": "telegram",
        "message": "Take your pills",
    }
    assert insert.call_args.kwargs["deliver_at"] == insert.call_args.kwargs["deferred_at"]
    assert ledger.call_args.kwargs["outcome"] == "deferred"
    daemon.switchboard_client.call_tool.assert_not_awaited()


@pytest.mark.parametrize(
    "extra",
    [
        {"recipient": "42"},
        {"channel": "telegram"},
        {"attachments": [{"type": "document", "path": "/x"}]},
        {"awaits_reply_seconds": 60},
        {"intent": "reply"},
    ],
)
async def test_non_generic_notices_are_not_parked(notification, extra):
    notify, daemon, insert, target, ledger = notification
    target.return_value = OwnerDefaultTarget(None, None, "endpoint_held", (TG, EMAIL))
    await notify(**({"intent": "send", "message": "Take your pills"} | extra))
    insert.assert_not_awaited()


async def test_an_unreadable_custody_refusal_is_not_parked(notification):
    notify, daemon, insert, target, ledger = notification
    target.return_value = OwnerDefaultTarget(None, None, "eligibility_unknown", ())
    result = await notify(intent="send", message="Take your pills")
    assert result["status"] == "error" and result["code"] == "eligibility_unknown"
    insert.assert_not_awaited()


async def test_park_stores_the_episode_of_the_first_held_target() -> None:
    class Pool:
        calls: list = []

        async def fetchval(self, sql, predicate, obj):
            self.calls.append((sql, predicate, obj))
            return None if predicate == "has-handle" else uuid.UUID(int=7)

    pool = Pool()
    from butlers.core.custody_park import _hold_episode

    assert await _hold_episode(pool, (TG, EMAIL)) == str(uuid.UUID(int=7))
    assert "public.custody_target_episode" in pool.calls[0][0]


async def test_an_episode_lookup_failure_refuses_instead_of_parking(monkeypatch) -> None:
    monkeypatch.setattr(
        "butlers.core.custody_park._hold_episode", AsyncMock(side_effect=ConnectionError("down"))
    )
    insert = AsyncMock()
    monkeypatch.setattr("butlers.core.temporal.delivery_db.insert_deferred_notification", insert)
    parked = await park_owner_default_notice(
        SimpleNamespace(),
        butler_name="health",
        intent="send",
        priority="medium",
        message="hi",
        targets=(TG,),
        now=NOW,
    )
    assert parked is None
    insert.assert_not_awaited()


async def test_only_the_exact_generic_shape_is_parkable() -> None:
    ok = _envelope()
    assert parked_targets(ok) == [TG, EMAIL]
    for mutate in (
        lambda e: e["delivery"].update(recipient="42"),
        lambda e: e["delivery"].update(intent="approval_request"),
        lambda e: e["delivery"].update(attachments=[1]),
        lambda e: e.update(decision_dossier={"x": 1}),
        lambda e: e[PARK_KEY].update(targets=[]),
        lambda e: e[PARK_KEY].update(targets=[["has-email"]]),
    ):
        bad = _envelope()
        mutate(bad)
        assert parked_targets(bad) is None
    pool = SimpleNamespace()
    assert (
        await park_owner_default_notice(
            pool,
            butler_name="health",
            intent="send",
            priority="medium",
            message="  ",
            targets=(TG,),
            now=NOW,
        )
        is None
    )


# flush (REQ-endpoint-custody-holds-010)


async def test_release_makes_the_row_flush_once_with_the_marker() -> None:
    store = FakeStore(held={TG, EMAIL})
    row_id = store.add(_envelope())
    notify = AsyncMock(return_value={"status": "sent"})

    assert await _tick_deferred_notification_pass(store_pool(store), NOW, notify_fn=notify) == 0
    notify.assert_not_awaited()
    assert store.rows[row_id]["status"] == "pending"

    store.held.discard(TG)  # protected release of the Telegram target
    assert await _tick_deferred_notification_pass(store_pool(store), NOW, notify_fn=notify) == 1
    sent = notify.call_args.args[0]
    assert sent["delivery"]["message"] == HELD_WHILE_LOST_MARKER + "Take your pills"
    assert sent["delivery"]["channel"] == "telegram"
    assert sent["delivery"]["recipient"] == "111" and PARK_KEY not in sent
    assert store.rows[row_id]["status"] == "delivered"

    assert await _tick_deferred_notification_pass(store_pool(store), NOW, notify_fn=notify) == 0
    assert notify.await_count == 1


async def test_a_released_email_target_sends_by_email(_ledger) -> None:
    """Telegram still held, email clear: the owning pipeline is asked for email."""
    store = FakeStore(held={TG})
    store.add(_envelope())
    notify = AsyncMock(return_value={"status": "sent"})
    assert await flush_custody_parked(store, NOW, list(store.rows.values()), notify) == 1
    delivery = notify.call_args.args[0]["delivery"]
    assert (delivery["channel"], delivery["recipient"]) == ("email", "owner@example.test")
    assert _ledger.call_args.kwargs["channel"] == "email"  # the channel actually used


async def test_a_rehold_before_the_flush_does_not_send() -> None:
    store = FakeStore(held=set())
    row_id = store.add(_envelope())
    store.held = {TG, EMAIL}  # released, then held again before the tick reads custody
    notify = AsyncMock()
    assert await flush_custody_parked(store, NOW, list(store.rows.values()), notify) == 0
    notify.assert_not_awaited()
    assert store.rows[row_id]["status"] == "pending"


async def test_unreadable_custody_does_not_send() -> None:
    store = FakeStore(held=set())
    row_id = store.add(_envelope())

    async def broken(sql: str, *args: Any) -> Any:
        if "custody_target_held" in sql:
            raise ConnectionError("down")
        return await FakeStore.fetchval(store, sql, *args)

    store.fetchval = broken  # type: ignore[method-assign]
    notify = AsyncMock()
    assert await flush_custody_parked(store, NOW, list(store.rows.values()), notify) == 0
    notify.assert_not_awaited()
    assert store.rows[row_id]["status"] == "pending"


async def test_an_expired_row_never_sends_and_leaves_an_expired_ledger_row(_ledger) -> None:
    store = FakeStore(held=set())
    row_id = store.add(_envelope(), age=timedelta(hours=30))
    notify = AsyncMock()
    assert await _tick_deferred_notification_pass(store_pool(store), NOW, notify_fn=notify) == 0
    notify.assert_not_awaited()
    assert store.rows[row_id]["status"] == "expired"
    assert _ledger.call_args.kwargs["outcome"] == "expired"
    assert _ledger.call_args.kwargs["notification_ref"] == str(row_id)


async def test_a_non_generic_row_is_cancelled_not_sent() -> None:
    store = FakeStore(held=set())
    envelope = _envelope()
    envelope["delivery"]["recipient"] = "42"
    row_id = store.add(envelope)
    notify = AsyncMock()
    assert await flush_custody_parked(store, NOW, list(store.rows.values()), notify) == 0
    notify.assert_not_awaited()
    assert store.rows[row_id]["status"] == "cancelled"


async def test_a_failed_send_stays_pending_for_the_next_tick() -> None:
    store = FakeStore(held=set())
    row_id = store.add(_envelope())
    notify = AsyncMock(side_effect=RuntimeError("provider refused"))
    assert await flush_custody_parked(store, NOW, list(store.rows.values()), notify) == 0
    assert store.rows[row_id]["status"] == "pending"
    assert not store.locks


async def test_concurrent_flushes_send_once() -> None:
    store = FakeStore(held=set())
    store.add(_envelope())
    gate = asyncio.Event()
    started = asyncio.Event()

    async def slow_send(envelope: dict[str, Any]) -> dict[str, str]:
        started.set()
        await gate.wait()
        return {"status": "sent"}

    notify = AsyncMock(side_effect=slow_send)
    rows = list(store.rows.values())
    first = asyncio.create_task(flush_custody_parked(store, NOW, rows, notify))
    await started.wait()
    second = await flush_custody_parked(store, NOW, rows, notify)  # row locked: skipped
    gate.set()
    assert (await first, second) == (1, 0)
    assert notify.await_count == 1


def store_pool(store: FakeStore) -> Any:
    """The store as the pass's pool: the pass's own selects and expiry writes."""

    class Pool:
        def __getattr__(self, name: str) -> Any:
            return getattr(store, name)

        def acquire(self):
            return store.acquire()

        async def execute(self, sql: str, *args: Any) -> None:
            if "SET status = 'expired'" in sql:
                for row in store.rows.values():
                    if row["status"] == "pending" and row["deliver_at"] < args[0]:
                        row["status"] = "expired"
                return
            await store.execute(sql, *args)

        async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            if "RETURNING id, butler_name" in sql:
                expired = [
                    r
                    for r in store.rows.values()
                    if r["status"] == "pending"
                    and r["deliver_at"] < args[0]
                    and "custody_park" in r["envelope"]
                ]
                for r in expired:
                    r["status"] = "expired"
                return expired
            if "SELECT id, butler_name, channel, message" in sql:
                return [
                    r
                    for r in store.rows.values()
                    if r["status"] == "pending" and r["deliver_at"] <= args[0]
                ]
            return []

        async def fetchval(self, sql: str, *args: Any) -> Any:
            return await store.fetchval(sql, *args)

    return Pool()
