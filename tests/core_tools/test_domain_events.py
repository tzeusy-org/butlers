"""Unit tests for the domain-event bus core tools.

Mirrors the fake-``_core_tool``-registry harness from
``tests/core_tools/test_delegation.py`` -- the tool logic here only touches
``butlers.core.domain_events``/``butlers.core.domain_event_wake`` and
``daemon.switchboard_client``, both cleanly monkeypatchable at this level.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.config import ButlerType
from butlers.core_tools import _domain_events
from butlers.core_tools._base import ToolContext
from butlers.core_tools._domain_events import (
    fan_out_event,
    publish_domain_event_once,
    register_domain_event_tools,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.unit

docker_available = shutil.which("docker") is not None
_integration = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


def _register(butler_name: str = "finance", butler_type=ButlerType.BUTLER, switchboard_client=None):
    registered: dict[str, callable] = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    mcp = SimpleNamespace()
    daemon = SimpleNamespace(switchboard_client=switchboard_client)
    ctx = ToolContext(
        daemon=daemon,
        pool=AsyncMock(),
        spawner=None,
        butler_name=butler_name,
        butler_type=butler_type,
        is_switchboard=(butler_name == "switchboard"),
        is_messenger=False,
        route_metrics=None,
    )
    register_domain_event_tools(ctx, mcp, _core_tool)
    return registered


# ---------------------------------------------------------------------------
# Real-Postgres fixtures for TestPublishDomainEventOnce's concurrency
# regression -- mocking asyncpg's acquire/transaction chaining would test the
# mock, not the atomic-claim contract (see tests/core/test_infra_conditions.py
# for the same rationale), so those specific tests run against a real,
# migrated database instead.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _dedup_migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def real_pool(_dedup_migrated_db_url: str):
    p = await asyncpg.create_pool(
        _dedup_migrated_db_url, min_size=2, max_size=5, init=register_jsonb_codec
    )
    yield p
    await p.close()


def test_staffer_gets_no_domain_event_tools():
    registered = _register(butler_type=ButlerType.STAFFER)
    assert registered == {}


def test_all_five_tools_registered():
    registered = _register()
    assert set(registered) == {
        "publish_event",
        "subscribe_to_event",
        "unsubscribe_from_event",
        "list_my_subscriptions",
        "receive_domain_event",
    }


class TestPublishEvent:
    async def test_invalid_event_type_rejected(self):
        registered = _register()
        result = await registered["publish_event"](event_type="not valid", payload={})
        assert result["status"] == "error"

    async def test_valid_publish_records_and_fans_out(self, monkeypatch):
        registered = _register(butler_name="travel")
        monkeypatch.setattr(_domain_events, "record_event", AsyncMock(return_value="event-1"))
        fanout_mock = AsyncMock(return_value={"event_id": "event-1", "deliveries": []})
        monkeypatch.setattr(_domain_events, "fan_out_event", fanout_mock)

        result = await registered["publish_event"](
            event_type="travel.trip_booked", payload={"trip_id": "t1"}
        )

        assert result == {"status": "ok", "event_id": "event-1", "deliveries": []}
        fanout_mock.assert_awaited_once()
        assert fanout_mock.await_args.kwargs["event_id"] == "event-1"
        assert fanout_mock.await_args.kwargs["event_type"] == "travel.trip_booked"
        assert fanout_mock.await_args.kwargs["source_butler"] == "travel"


class TestSubscribeUnsubscribeList:
    async def test_subscribe_rejects_invalid_event_type(self):
        registered = _register()
        result = await registered["subscribe_to_event"](event_type="bogus")
        assert result["status"] == "error"

    async def test_subscribe_upserts(self, monkeypatch):
        registered = _register(butler_name="finance")
        upsert_mock = AsyncMock(return_value={"subscriber_butler": "finance", "active": True})
        monkeypatch.setattr(_domain_events, "upsert_subscription", upsert_mock)

        result = await registered["subscribe_to_event"](event_type="travel.trip_booked")

        assert result["status"] == "ok"
        upsert_mock.assert_awaited_once()
        assert upsert_mock.await_args.kwargs == {
            "subscriber_butler": "finance",
            "event_type": "travel.trip_booked",
        }

    async def test_unsubscribe_removes(self, monkeypatch):
        registered = _register(butler_name="finance")
        remove_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(_domain_events, "remove_subscription", remove_mock)

        result = await registered["unsubscribe_from_event"](event_type="travel.trip_booked")

        assert result == {"status": "ok", "existed": True}

    async def test_list_my_subscriptions(self, monkeypatch):
        registered = _register(butler_name="finance")
        list_mock = AsyncMock(return_value=[{"event_type": "travel.trip_booked"}])
        monkeypatch.setattr(_domain_events, "list_subscriptions", list_mock)

        result = await registered["list_my_subscriptions"]()

        assert result == {"status": "ok", "subscriptions": [{"event_type": "travel.trip_booked"}]}
        assert list_mock.await_args.kwargs == {"subscriber_butler": "finance"}


class TestReceiveDomainEvent:
    async def test_delegates_to_handler(self, monkeypatch):
        registered = _register(butler_name="finance")
        handler_mock = AsyncMock(return_value={"status": "ok", "state": "task_created"})
        monkeypatch.setattr(_domain_events, "handle_receive_domain_event", handler_mock)

        result = await registered["receive_domain_event"](
            event_id="event-1",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "t1"},
        )

        assert result == {"status": "ok", "state": "task_created"}
        assert handler_mock.await_args.kwargs["subscriber_butler"] == "finance"
        assert handler_mock.await_args.kwargs["event_id"] == "event-1"


class TestPublishDomainEventOnce:
    """publish_domain_event_once's dedup gate is now an atomic claim
    (``_claim_and_record_event`` / ``state_claim_if_changed``), not a
    check-then-act ``state_get`` + ``state_set`` pair -- see that function's
    docstring for why. These tests mock ``_claim_and_record_event`` and
    ``fan_out_event`` as units (mocking asyncpg's acquire/transaction
    chaining here would test the mock, not the atomicity contract); the
    atomicity itself is verified against a real Postgres pool below.
    """

    async def test_publishes_when_no_prior_key_recorded(self, monkeypatch):
        pool = AsyncMock()
        claim_mock = AsyncMock(return_value="e1")
        monkeypatch.setattr(_domain_events, "_claim_and_record_event", claim_mock)
        fanout_mock = AsyncMock(return_value={"deliveries": []})
        monkeypatch.setattr(_domain_events, "fan_out_event", fanout_mock)

        result = await publish_domain_event_once(
            pool,
            None,
            event_type="travel.trip_active",
            source_butler="travel",
            dedup_namespace="travel.trip_active",
            dedup_key="trip-1",
            payload={"trip_id": "trip-1"},
        )

        assert result == {"status": "ok", "event_id": "e1", "deliveries": []}
        claim_mock.assert_awaited_once_with(
            pool,
            state_key="domain_event_once:travel.trip_active:travel.trip_active",
            dedup_key="trip-1",
            event_type="travel.trip_active",
            source_butler="travel",
            payload={"trip_id": "trip-1"},
        )
        fanout_mock.assert_awaited_once()

    async def test_skips_when_key_unchanged(self, monkeypatch):
        pool = AsyncMock()
        claim_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(_domain_events, "_claim_and_record_event", claim_mock)
        fanout_mock = AsyncMock()
        monkeypatch.setattr(_domain_events, "fan_out_event", fanout_mock)

        result = await publish_domain_event_once(
            pool,
            None,
            event_type="travel.trip_active",
            source_butler="travel",
            dedup_namespace="travel.trip_active",
            dedup_key="trip-1",
            payload={"trip_id": "trip-1"},
        )

        assert result is None
        claim_mock.assert_awaited_once()
        fanout_mock.assert_not_awaited()

    async def test_publishes_again_when_key_changes(self, monkeypatch):
        pool = AsyncMock()
        claim_mock = AsyncMock(return_value="e2")
        monkeypatch.setattr(_domain_events, "_claim_and_record_event", claim_mock)
        fanout_mock = AsyncMock(return_value={"deliveries": []})
        monkeypatch.setattr(_domain_events, "fan_out_event", fanout_mock)

        result = await publish_domain_event_once(
            pool,
            None,
            event_type="travel.trip_active",
            source_butler="travel",
            dedup_namespace="travel.trip_active",
            dedup_key="trip-2",
            payload={"trip_id": "trip-2"},
        )

        assert result == {"status": "ok", "event_id": "e2", "deliveries": []}
        claim_mock.assert_awaited_once_with(
            pool,
            state_key="domain_event_once:travel.trip_active:travel.trip_active",
            dedup_key="trip-2",
            event_type="travel.trip_active",
            source_butler="travel",
            payload={"trip_id": "trip-2"},
        )

    async def test_invalid_event_type_short_circuits_before_claim(self, monkeypatch):
        pool = AsyncMock()
        claim_mock = AsyncMock()
        monkeypatch.setattr(_domain_events, "_claim_and_record_event", claim_mock)
        fanout_mock = AsyncMock()
        monkeypatch.setattr(_domain_events, "fan_out_event", fanout_mock)

        result = await publish_domain_event_once(
            pool,
            None,
            event_type="not valid",
            source_butler="travel",
            dedup_namespace="travel.trip_active",
            dedup_key="trip-1",
        )

        assert result == {
            "status": "error",
            "error": (
                "event_type='not valid' must match '<namespace>.<event>' "
                "(lowercase, e.g. 'travel.trip_booked')."
            ),
        }
        # Never attempts to claim the dedup slot for a publish that was
        # never going to happen -- otherwise a bad event_type would burn the
        # dedup_key and silently mask a subsequent, valid publish attempt.
        claim_mock.assert_not_awaited()
        fanout_mock.assert_not_awaited()

    # -----------------------------------------------------------------
    # Real-Postgres: the atomic-claim regression (bu-317s5 review
    # remediation). Two overlapping publish_domain_event_once calls for the
    # same dedup_namespace/dedup_key (overlapping consecutive cron
    # occurrences, or a dashboard run-now racing cron) must have exactly one
    # publish; a distinct dedup_key must still publish independently.
    # -----------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.skipif(not docker_available, reason="Docker not available")
    @pytest.mark.asyncio(loop_scope="session")
    async def test_concurrent_same_key_publishes_exactly_once(self, real_pool):
        results = await asyncio.gather(
            publish_domain_event_once(
                real_pool,
                None,
                event_type="travel.dedup_concurrency_test",
                source_butler="travel",
                dedup_namespace="travel.dedup_concurrency_test",
                dedup_key="trip-race",
                payload={"n": 1},
            ),
            publish_domain_event_once(
                real_pool,
                None,
                event_type="travel.dedup_concurrency_test",
                source_butler="travel",
                dedup_namespace="travel.dedup_concurrency_test",
                dedup_key="trip-race",
                payload={"n": 1},
            ),
        )

        published = [r for r in results if r is not None]
        assert len(published) == 1
        assert published[0]["status"] == "ok"

        count = await real_pool.fetchval(
            "SELECT count(*) FROM public.domain_events WHERE event_type = $1",
            "travel.dedup_concurrency_test",
        )
        assert count == 1

        state_row = await real_pool.fetchval(
            "SELECT value FROM state WHERE key = $1",
            "domain_event_once:travel.dedup_concurrency_test:travel.dedup_concurrency_test",
        )
        assert state_row == "trip-race"

    @pytest.mark.integration
    @pytest.mark.skipif(not docker_available, reason="Docker not available")
    @pytest.mark.asyncio(loop_scope="session")
    async def test_sequential_distinct_keys_both_publish(self, real_pool):
        first = await publish_domain_event_once(
            real_pool,
            None,
            event_type="travel.dedup_sequential_test",
            source_butler="travel",
            dedup_namespace="travel.dedup_sequential_test",
            dedup_key="key-1",
            payload={"n": 1},
        )
        second = await publish_domain_event_once(
            real_pool,
            None,
            event_type="travel.dedup_sequential_test",
            source_butler="travel",
            dedup_namespace="travel.dedup_sequential_test",
            dedup_key="key-2",
            payload={"n": 2},
        )
        # A repeat of the second key must not publish again.
        third = await publish_domain_event_once(
            real_pool,
            None,
            event_type="travel.dedup_sequential_test",
            source_butler="travel",
            dedup_namespace="travel.dedup_sequential_test",
            dedup_key="key-2",
            payload={"n": 2},
        )

        assert first is not None and first["status"] == "ok"
        assert second is not None and second["status"] == "ok"
        assert first["event_id"] != second["event_id"]
        assert third is None

        count = await real_pool.fetchval(
            "SELECT count(*) FROM public.domain_events WHERE event_type = $1",
            "travel.dedup_sequential_test",
        )
        assert count == 2


class TestFanOutEvent:
    async def test_skips_source_butler_as_its_own_subscriber(self, monkeypatch):
        pool = AsyncMock()
        monkeypatch.setattr(
            _domain_events, "get_active_subscribers", AsyncMock(return_value=["travel"])
        )
        claim_mock = AsyncMock()
        monkeypatch.setattr(_domain_events, "claim_delivery", claim_mock)

        result = await fan_out_event(
            pool,
            None,
            event_id="event-1",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={},
        )

        assert result == {"event_id": "event-1", "deliveries": []}
        claim_mock.assert_not_awaited()

    async def test_already_delivered_is_not_redispatched(self, monkeypatch):
        pool = AsyncMock()
        monkeypatch.setattr(
            _domain_events, "get_active_subscribers", AsyncMock(return_value=["finance"])
        )
        monkeypatch.setattr(
            _domain_events,
            "claim_delivery",
            AsyncMock(return_value={"id": "d1", "status": "delivered"}),
        )
        dispatch_mock = AsyncMock()
        monkeypatch.setattr(_domain_events, "_dispatch_receive_via_switchboard", dispatch_mock)

        result = await fan_out_event(
            pool,
            None,
            event_id="event-1",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={},
        )

        assert result == {
            "event_id": "event-1",
            "deliveries": [{"subscriber_butler": "finance", "status": "delivered"}],
        }
        dispatch_mock.assert_not_awaited()

    async def test_dispatch_failure_marks_delivery_failed(self, monkeypatch):
        pool = AsyncMock()
        monkeypatch.setattr(
            _domain_events, "get_active_subscribers", AsyncMock(return_value=["finance"])
        )
        monkeypatch.setattr(
            _domain_events,
            "claim_delivery",
            AsyncMock(return_value={"id": "d1", "status": "pending"}),
        )
        monkeypatch.setattr(
            _domain_events,
            "_dispatch_receive_via_switchboard",
            AsyncMock(return_value=(None, "boom", True)),
        )
        mark_failed_mock = AsyncMock()
        monkeypatch.setattr(_domain_events, "mark_delivery_failed", mark_failed_mock)

        result = await fan_out_event(
            pool,
            None,
            event_id="event-1",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={},
        )

        assert result["deliveries"] == [
            {
                "subscriber_butler": "finance",
                "status": "failed",
                "error": "boom",
                "retryable": True,
            }
        ]
        mark_failed_mock.assert_awaited_once_with(pool, "d1", "boom")

    async def test_task_conflict_marks_delivery_conflict(self, monkeypatch):
        pool = AsyncMock()
        monkeypatch.setattr(
            _domain_events, "get_active_subscribers", AsyncMock(return_value=["finance"])
        )
        monkeypatch.setattr(
            _domain_events,
            "claim_delivery",
            AsyncMock(return_value={"id": "d1", "status": "pending"}),
        )
        monkeypatch.setattr(
            _domain_events,
            "_dispatch_receive_via_switchboard",
            AsyncMock(return_value=({"state": "task_conflict"}, None, False)),
        )
        mark_conflict_mock = AsyncMock()
        monkeypatch.setattr(_domain_events, "mark_delivery_conflict", mark_conflict_mock)

        result = await fan_out_event(
            pool,
            None,
            event_id="event-1",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={},
        )

        assert result["deliveries"] == [{"subscriber_butler": "finance", "status": "conflict"}]
        mark_conflict_mock.assert_awaited_once_with(pool, "d1")

    async def test_successful_dispatch_marks_delivered(self, monkeypatch):
        pool = AsyncMock()
        monkeypatch.setattr(
            _domain_events, "get_active_subscribers", AsyncMock(return_value=["finance"])
        )
        monkeypatch.setattr(
            _domain_events,
            "claim_delivery",
            AsyncMock(return_value={"id": "d1", "status": "pending"}),
        )
        task_id = str(uuid.uuid4())
        monkeypatch.setattr(
            _domain_events,
            "_dispatch_receive_via_switchboard",
            AsyncMock(
                return_value=(
                    {"state": "task_created", "task_id": task_id, "task_name": "domain-event-x"},
                    None,
                    False,
                )
            ),
        )
        mark_delivered_mock = AsyncMock()
        monkeypatch.setattr(_domain_events, "mark_delivery_delivered", mark_delivered_mock)

        result = await fan_out_event(
            pool,
            None,
            event_id="event-1",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={},
        )

        assert result["deliveries"] == [{"subscriber_butler": "finance", "status": "delivered"}]
        mark_delivered_mock.assert_awaited_once_with(
            pool, "d1", task_id=task_id, task_name="domain-event-x"
        )
