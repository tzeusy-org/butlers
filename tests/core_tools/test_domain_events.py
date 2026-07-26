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
    _is_retryable_route_error_text,
    _unwrap_route_result,
    fan_out_event,
    publish_domain_event_once,
    register_domain_event_tools,
    run_domain_event_reconciliation_sweep,
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
        mark_failed_mock = AsyncMock(return_value="failed")
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
        mark_failed_mock.assert_awaited_once_with(
            pool,
            "d1",
            "boom",
            retryable=True,
            max_attempts=_domain_events._MAX_DELIVERY_RETRY_ATTEMPTS,
        )

    async def test_permanent_route_error_marks_delivery_failed_permanent(self, monkeypatch):
        """A route error classified permanent (retryable=False) must land on the
        terminal `failed_permanent` status, not the retryable `failed` -- and
        the outcome must never claim `retryable` for a permanent failure."""
        pool = AsyncMock()
        monkeypatch.setattr(
            _domain_events, "get_active_subscribers", AsyncMock(return_value=["health"])
        )
        monkeypatch.setattr(
            _domain_events,
            "claim_delivery",
            AsyncMock(return_value={"id": "d2", "status": "pending"}),
        )
        monkeypatch.setattr(
            _domain_events,
            "_dispatch_receive_via_switchboard",
            AsyncMock(
                return_value=(None, "RuntimeError: Unknown tool: receive_domain_event", False)
            ),
        )
        mark_failed_mock = AsyncMock(return_value="failed_permanent")
        monkeypatch.setattr(_domain_events, "mark_delivery_failed", mark_failed_mock)

        result = await fan_out_event(
            pool,
            None,
            event_id="event-1",
            event_type="travel.trip_active",
            source_butler="travel",
            payload={},
        )

        assert result["deliveries"] == [
            {
                "subscriber_butler": "health",
                "status": "failed_permanent",
                "error": "RuntimeError: Unknown tool: receive_domain_event",
            }
        ]
        mark_failed_mock.assert_awaited_once_with(
            pool,
            "d2",
            "RuntimeError: Unknown tool: receive_domain_event",
            retryable=False,
            max_attempts=_domain_events._MAX_DELIVERY_RETRY_ATTEMPTS,
        )

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


class TestIsRetryableRouteErrorText:
    """route()'s except-block stamps every failure as f"{type(exc).__name__}:
    {exc}" -- these prefixes are the transient ones; everything else
    (notably an "unknown tool" RuntimeError, the shape a missing
    domain_events core group takes) is permanent."""

    @pytest.mark.parametrize(
        "error_text",
        [
            "ConnectionError: refused",
            "OSError: [Errno 111] Connection refused",
            "TimeoutError: deadline exceeded",
        ],
    )
    def test_transient_prefixes_are_retryable(self, error_text):
        assert _is_retryable_route_error_text(error_text) is True

    @pytest.mark.parametrize(
        "error_text",
        [
            "RuntimeError: Unknown tool: receive_domain_event",
            "LookupError: Butler 'health' not found in registry",
            "Butler 'health' not found in registry",
        ],
    )
    def test_other_errors_are_permanent(self, error_text):
        assert _is_retryable_route_error_text(error_text) is False


class TestUnwrapRouteResult:
    """route() (roster/switchboard/tools/routing/route.py) always returns
    {"error": ...} on a route-level failure or {"result": <target's own
    return>} on success -- never the target's dict unwrapped at the top
    level. _unwrap_route_result is the one place that envelope gets peeled
    back off before the caller inspects the target's own status/task_id/
    task_name (see _dispatch_receive_via_switchboard's docstring for why an
    earlier version of this code got this wrong)."""

    def test_success_envelope_unwraps_to_inner_result(self):
        data, error, retryable = _unwrap_route_result(
            {"result": {"status": "ok", "state": "task_created", "task_id": "t1"}}
        )
        assert data == {"status": "ok", "state": "task_created", "task_id": "t1"}
        assert error is None
        assert retryable is False

    def test_error_envelope_is_detected_and_classified_transient(self):
        data, error, retryable = _unwrap_route_result({"error": "ConnectionError: refused"})
        assert data is None
        assert error == "ConnectionError: refused"
        assert retryable is True

    def test_error_envelope_is_detected_and_classified_permanent(self):
        data, error, retryable = _unwrap_route_result(
            {"error": "RuntimeError: Unknown tool: receive_domain_event"}
        )
        assert data is None
        assert error == "RuntimeError: Unknown tool: receive_domain_event"
        assert retryable is False

    def test_inner_target_error_status_is_still_detected(self):
        data, error, retryable = _unwrap_route_result(
            {"result": {"status": "error", "error": "bad payload"}}
        )
        assert data is None
        assert error == "bad payload"
        assert retryable is False

    def test_non_dict_raw_is_a_non_retryable_error(self):
        data, error, retryable = _unwrap_route_result("not a dict")
        assert data is None
        assert error is not None
        assert retryable is False


class TestDispatchReceiveViaSwitchboardEnvelope:
    """Regression coverage for the route()-envelope unwrap bug: a real MCP
    client's CallToolResult.data is route()'s own {"result"/"error"} shape,
    not the target tool's dict directly."""

    async def test_client_branch_unwraps_successful_route_result(self):
        client = AsyncMock()
        client.call_tool = AsyncMock(
            return_value=SimpleNamespace(
                is_error=False,
                data={
                    "result": {
                        "status": "ok",
                        "state": "task_created",
                        "task_id": "t1",
                        "task_name": "domain-event-x-finance",
                    }
                },
            )
        )

        data, error, retryable = await _domain_events._dispatch_receive_via_switchboard(
            client,
            AsyncMock(),
            "travel",
            target_butler="finance",
            args={"event_id": "e1", "event_type": "travel.trip_booked", "payload": {}},
        )

        assert error is None
        assert retryable is False
        assert data == {
            "status": "ok",
            "state": "task_created",
            "task_id": "t1",
            "task_name": "domain-event-x-finance",
        }

    async def test_client_branch_surfaces_route_level_failure(self):
        client = AsyncMock()
        client.call_tool = AsyncMock(
            return_value=SimpleNamespace(
                is_error=False,
                data={"error": "RuntimeError: Unknown tool: receive_domain_event"},
            )
        )

        data, error, retryable = await _domain_events._dispatch_receive_via_switchboard(
            client,
            AsyncMock(),
            "travel",
            target_butler="health",
            args={"event_id": "e1", "event_type": "travel.trip_active", "payload": {}},
        )

        assert data is None
        assert error == "RuntimeError: Unknown tool: receive_domain_event"
        assert retryable is False

    async def test_switchboard_self_delivery_branch_unwraps_successful_result(self, monkeypatch):
        import importlib

        # butlers.tools.switchboard.routing's __init__ re-exports `route` (the
        # function) at the package level, shadowing the `route` *submodule*
        # attribute on the parent package -- `import ...routing.route as x`
        # would silently bind `x` to the function, not the module (a known
        # import-shadow gotcha). importlib.import_module always returns the
        # real sys.modules entry regardless of that shadowing.
        route_module = importlib.import_module("butlers.tools.switchboard.routing.route")

        switchboard_route_mock = AsyncMock(
            return_value={
                "result": {
                    "status": "ok",
                    "state": "task_created",
                    "task_id": "t2",
                    "task_name": "domain-event-y-finance",
                }
            }
        )
        monkeypatch.setattr(route_module, "route", switchboard_route_mock)

        data, error, retryable = await _domain_events._dispatch_receive_via_switchboard(
            None,
            AsyncMock(),
            "switchboard",
            target_butler="finance",
            args={"event_id": "e2", "event_type": "travel.trip_booked", "payload": {}},
        )

        assert error is None
        assert data["task_id"] == "t2"


class TestRunDomainEventReconciliationSweep:
    """Mocked-level orchestration coverage: the sweep selects candidates,
    re-observes each via claim_delivery (the idempotence boundary), and
    dispatches only those still in a redrivable state. Concurrency/real-DB
    behavior is covered by tests/integration/test_domain_event_bus_roundtrip.py.
    """

    async def test_stale_pending_candidate_is_redriven_and_delivered(self, monkeypatch):
        pool = AsyncMock()
        row = {
            "id": "d1",
            "event_id": "event-1",
            "subscriber_butler": "finance",
            "status": "pending",
            "attempt_count": 0,
            "event_type": "travel.trip_booked",
            "source_butler": "travel",
            "payload": {"trip_id": "t1"},
        }
        monkeypatch.setattr(
            _domain_events, "select_stale_pending_deliveries", AsyncMock(return_value=[row])
        )
        monkeypatch.setattr(
            _domain_events, "select_retryable_failed_deliveries", AsyncMock(return_value=[])
        )
        monkeypatch.setattr(_domain_events, "claim_delivery", AsyncMock(return_value=dict(row)))
        dispatch_mock = AsyncMock(
            return_value={"subscriber_butler": "finance", "status": "delivered"}
        )
        monkeypatch.setattr(_domain_events, "_dispatch_and_record_delivery", dispatch_mock)

        result = await run_domain_event_reconciliation_sweep(pool)

        assert result["stale_pending_candidates"] == 1
        assert result["stale_pending_redriven"] == 1
        assert result["stale_pending_delivered"] == 1
        dispatch_mock.assert_awaited_once()

    async def test_candidate_already_resolved_since_selection_is_skipped(self, monkeypatch):
        """A candidate that a concurrent live dispatch already resolved between
        selection and processing must be re-observed (via claim_delivery) and
        skipped, never blindly re-dispatched."""
        pool = AsyncMock()
        row = {
            "id": "d1",
            "event_id": "event-1",
            "subscriber_butler": "finance",
            "status": "pending",
            "attempt_count": 0,
            "event_type": "travel.trip_booked",
            "source_butler": "travel",
            "payload": {},
        }
        monkeypatch.setattr(
            _domain_events, "select_stale_pending_deliveries", AsyncMock(return_value=[row])
        )
        monkeypatch.setattr(
            _domain_events, "select_retryable_failed_deliveries", AsyncMock(return_value=[])
        )
        # Re-observe finds the row already delivered by someone else.
        monkeypatch.setattr(
            _domain_events,
            "claim_delivery",
            AsyncMock(return_value={**row, "status": "delivered"}),
        )
        dispatch_mock = AsyncMock()
        monkeypatch.setattr(_domain_events, "_dispatch_and_record_delivery", dispatch_mock)

        result = await run_domain_event_reconciliation_sweep(pool)

        assert result["stale_pending_redriven"] == 0
        dispatch_mock.assert_not_awaited()

    async def test_permanent_failure_is_counted_and_logged(self, monkeypatch, caplog):
        pool = AsyncMock()
        row = {
            "id": "d1",
            "event_id": "event-1",
            "subscriber_butler": "health",
            "status": "failed",
            "attempt_count": 1,
            "error_message": "RuntimeError: Unknown tool",
            "event_type": "travel.trip_active",
            "source_butler": "travel",
            "payload": {},
        }
        monkeypatch.setattr(
            _domain_events, "select_stale_pending_deliveries", AsyncMock(return_value=[])
        )
        monkeypatch.setattr(
            _domain_events, "select_retryable_failed_deliveries", AsyncMock(return_value=[row])
        )
        monkeypatch.setattr(_domain_events, "claim_delivery", AsyncMock(return_value=dict(row)))
        dispatch_mock = AsyncMock(
            return_value={
                "subscriber_butler": "health",
                "status": "failed_permanent",
                "error": "RuntimeError: Unknown tool",
            }
        )
        monkeypatch.setattr(_domain_events, "_dispatch_and_record_delivery", dispatch_mock)

        with caplog.at_level("ERROR"):
            result = await run_domain_event_reconciliation_sweep(pool)

        assert result["failed_retried"] == 1
        assert result["newly_permanently_failed"] == 1
        assert any("permanently failed" in message for message in caplog.messages)
