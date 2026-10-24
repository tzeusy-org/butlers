"""Unit tests for butlers.core.domain_event_wake (bu-ac4yc).

Mocked-pool style mirroring tests/core/test_delegation_wake.py. These pin the
*descriptive-only* validity contract the module docstring states: a derived
advisory's ``valid_until`` is a producer convention inside an open JSONB
payload, never a bus-level delivery predicate. See
tests/integration/test_domain_event_bus_roundtrip.py for the real-Postgres
fan-out coverage.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.core import domain_event_wake
from butlers.core.domain_event_wake import (
    _build_wake_task_prompt,
    handle_receive_domain_event,
)

pytestmark = pytest.mark.unit

_EXPIRED_ADVISORY = {
    "state": "depleted",
    "max_severity": 8,
    "valid_until": "2020-01-01T00:00:00+00:00",
}


def _assert_fixture_is_genuinely_expired(payload: dict) -> datetime:
    """Guard the fixture itself: a TTL test on a non-expired advisory proves nothing."""
    valid_until = datetime.fromisoformat(payload["valid_until"])
    now = datetime.now(UTC)
    assert valid_until < now, (
        f"fixture is not expired: valid_until={valid_until.isoformat()} is not "
        f"before the delivery clock {now.isoformat()}"
    )
    return now


class TestDescriptiveOnlyValiditySemantics:
    """`valid_until` is descriptive; the wake path must never act on it."""

    async def test_expired_advisory_is_still_delivered(self, monkeypatch):
        now = _assert_fixture_is_genuinely_expired(_EXPIRED_ADVISORY)
        task_id = uuid.uuid4()
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        schedule_create = AsyncMock(return_value=task_id)
        monkeypatch.setattr(domain_event_wake, "schedule_create", schedule_create)

        result = await handle_receive_domain_event(
            pool,
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="health.recovery_state",
            source_butler="health",
            payload=_EXPIRED_ADVISORY,
            subscriber_butler="lifestyle",
        )

        assert result["status"] == "ok"
        assert result["state"] == "task_created"
        assert result["task_id"] == str(task_id)
        assert schedule_create.await_count == 1, (
            "an advisory past its own valid_until must still schedule a wake -- "
            "the bus makes no delivery decision from payload content"
        )
        # The wake is scheduled forward from the delivery clock, never clamped to
        # the (already lapsed) validity window carried in the payload.
        until_at = schedule_create.await_args.kwargs["until_at"]
        assert until_at > now

    async def test_expired_valid_until_is_embedded_verbatim(self):
        _assert_fixture_is_genuinely_expired(_EXPIRED_ADVISORY)

        prompt = _build_wake_task_prompt(
            event_id="22222222-2222-2222-2222-222222222222",
            event_type="health.recovery_state",
            source_butler="health",
            subscriber_butler="lifestyle",
            payload=_EXPIRED_ADVISORY,
        )

        # Delivered verbatim: the bus neither strips nor rewrites the window it
        # declines to enforce, so the subscriber can judge freshness itself.
        assert json.dumps(_EXPIRED_ADVISORY, sort_keys=True) in prompt

    def test_prompt_tells_the_session_to_recheck_the_validity_window(self):
        prompt = _build_wake_task_prompt(
            event_id="33333333-3333-3333-3333-333333333333",
            event_type="finance.budget_pressure",
            source_butler="finance",
            subscriber_butler="general",
            payload={"category": "groceries", "valid_until": "2020-01-01T00:00:00+00:00"},
        )

        assert "valid_until" in prompt
        assert "compare it against the current time first" in prompt

        # The caveat is trusted bus text, so it must sit OUTSIDE the fence --
        # inside it the prompt itself forbids reading it as an instruction.
        fence_end = prompt.index("</domain_event>")
        assert prompt.index("compare it against the current time first") > fence_end


class TestWakeSchedulingIgnoresPayloadContent:
    async def test_scheduling_is_derived_from_the_delivery_clock_only(self, monkeypatch):
        """Two payloads with opposite validity windows schedule the identical wake."""
        frozen_now = datetime(2026, 8, 24, 12, 30, 0, tzinfo=UTC)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is None else frozen_now.astimezone(tz)

        monkeypatch.setattr(domain_event_wake, "datetime", _FrozenDatetime)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        schedule_create = AsyncMock(return_value=uuid.uuid4())
        monkeypatch.setattr(domain_event_wake, "schedule_create", schedule_create)

        far_future = (frozen_now + timedelta(days=365)).isoformat()
        payloads = (
            {"valid_until": "2020-01-01T00:00:00+00:00"},
            {"valid_until": far_future},
        )
        for index, payload in enumerate(payloads):
            await handle_receive_domain_event(
                pool,
                event_id=f"4444444{index}-4444-4444-4444-444444444444",
                event_type="health.recovery_state",
                source_butler="health",
                payload=payload,
                subscriber_butler="lifestyle",
            )

        assert schedule_create.await_count == 2, (
            "both events must schedule a wake regardless of their validity windows"
        )
        expired_call, fresh_call = schedule_create.await_args_list
        # cron is the 3rd positional arg of schedule_create(pool, name, cron, prompt).
        assert expired_call.args[2] == "31 12 24 8 *"
        assert expired_call.kwargs["until_at"] == frozen_now + timedelta(minutes=2)
        assert (expired_call.args[2], expired_call.kwargs["until_at"]) == (
            fresh_call.args[2],
            fresh_call.kwargs["until_at"],
        ), "payload validity must not steer the wake's cron or expiry"


class TestReactionLifecycleIsOpened:
    """A wake that is scheduled must show up in the reaction ledger (bu-6jv4m.8)."""

    async def test_a_fresh_wake_opens_a_scheduled_receipt(self, monkeypatch):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(
            domain_event_wake, "schedule_create", AsyncMock(return_value=uuid.uuid4())
        )
        recorded: list[dict] = []

        async def _record(_pool, **kwargs):
            recorded.append(kwargs)
            return "reaction-id"

        monkeypatch.setattr(domain_event_wake, "record_reaction", _record)

        result = await handle_receive_domain_event(
            pool,
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "t-1"},
            subscriber_butler="finance",
        )

        assert result["status"] == "ok"
        assert len(recorded) == 1
        assert recorded[0]["status"] == "scheduled"
        assert recorded[0]["subscriber_butler"] == "finance"
        assert recorded[0]["task_name"] == result["task_name"]

    async def test_a_duplicate_delivery_does_not_reopen_the_lifecycle(self, monkeypatch):
        """Positive control: a re-delivered wake must not stack a second 'scheduled'."""
        task_name = domain_event_wake.task_name_for(
            "11111111-1111-1111-1111-111111111111", "finance"
        )
        prompt = domain_event_wake._build_wake_task_prompt(
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            subscriber_butler="finance",
            payload={"trip_id": "t-1"},
        )
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            return_value={"id": uuid.uuid4(), "name": task_name, "prompt": prompt}
        )
        recorded: list[dict] = []

        async def _record(_pool, **kwargs):
            recorded.append(kwargs)
            return "reaction-id"

        monkeypatch.setattr(domain_event_wake, "record_reaction", _record)

        result = await handle_receive_domain_event(
            pool,
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "t-1"},
            subscriber_butler="finance",
        )

        assert result["reconciled"] is True
        assert recorded == []

    async def test_a_ledger_write_failure_never_loses_the_wake(self, monkeypatch):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(
            domain_event_wake, "schedule_create", AsyncMock(return_value=uuid.uuid4())
        )

        async def _boom(_pool, **_kwargs):
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(domain_event_wake, "record_reaction", _boom)

        result = await handle_receive_domain_event(
            pool,
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            payload={"trip_id": "t-1"},
            subscriber_butler="finance",
        )
        assert result["status"] == "ok"


class TestWakePromptClosesTheLoop:
    def test_the_prompt_asks_the_session_to_file_a_receipt(self):
        prompt = domain_event_wake._build_wake_task_prompt(
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            subscriber_butler="finance",
            payload={"trip_id": "t-1"},
        )
        assert "report_event_reaction" in prompt
        assert "ignored" in prompt

    def test_the_receipt_instruction_stays_outside_the_untrusted_fence(self):
        prompt = domain_event_wake._build_wake_task_prompt(
            event_id="11111111-1111-1111-1111-111111111111",
            event_type="travel.trip_booked",
            source_butler="travel",
            subscriber_butler="finance",
            payload={"trip_id": "t-1"},
        )
        fence = prompt[prompt.index("<domain_event>") : prompt.index("</domain_event>")]
        assert "report_event_reaction" not in fence


async def domain_source_consumer(tmp_path, monkeypatch, *, mode="dark", owning_pool=None):
    """Real startup/registrar/receive/wake/source SQL; software, never PG proof."""
    import copy
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from fastmcp import FastMCP

    from butlers.config import load_config
    from butlers.core.runtime_config import RuntimeConfig
    from butlers.daemon import ButlerDaemon
    from butlers.lifecycle import _startup_module_tools
    from tests.core.test_obligation_schedule_dispatch import OccurrenceConnection
    from tests.core.test_obligation_schedule_sources import RecordingPool

    class Connection(OccurrenceConnection):
        def __init__(self):
            super().__init__()
            self.event = {
                "id": uuid.UUID(int=901),
                "event_type": "travel.trip_booked",
                "source_butler": "travel",
                "payload": {"trip": "reference"},
            }
            self.subscription = {
                "id": uuid.UUID(int=902),
                "subscriber_butler": "health",
                "event_type": "travel.trip_booked",
                "active": True,
            }
            self.delivery = {
                "id": uuid.UUID(int=903),
                "event_id": self.event["id"],
                "subscriber_butler": "health",
                "status": "pending",
                "task_id": None,
                "task_name": None,
            }
            self.bus_shadow = False

        async def fetchval(self, sql, *args):
            if "INSERT INTO scheduled_tasks" in sql:
                key = uuid.uuid4()
                keys = [
                    "name",
                    "cron",
                    "dispatch_mode",
                    "prompt",
                    "job_name",
                    "job_args",
                    "complexity",
                    "timezone",
                    "start_at",
                    "end_at",
                    "until_at",
                    "display_title",
                    "description",
                    "location",
                    "calendar_event_id",
                    "next_run_at",
                ]
                from butlers.core.obligation_schedule_sources import SCHEDULE_FIELDS

                self.rows[key] = (
                    dict.fromkeys(SCHEDULE_FIELDS)
                    | dict(zip(keys, args, strict=True))
                    | {
                        "id": key,
                        "source": "db",
                        "enabled": True,
                        "task_type": "cron",
                        "continuity": False,
                        "max_token_budget": None,
                    }
                )
                return key
            return await super().fetchval(sql, *args)

        async def fetchrow(self, sql, *args):
            if "AS domain_relations" in sql:
                return {"domain_relations": not self.bus_shadow}
            for name, value in (
                ("public.domain_events", self.event),
                ("public.butler_subscriptions", self.subscription),
                ("public.domain_event_deliveries", self.delivery),
            ):
                if f"FROM {name}" in sql:
                    assert self.depth == 1 and "FOR SHARE" in sql
                    self.calls.append(sql)
                    return copy.deepcopy(value)
            if "SELECT id, name, prompt FROM scheduled_tasks" in sql:
                return next(
                    (copy.deepcopy(v) for v in self.rows.values() if v["name"] == args[0]), None
                )
            return await super().fetchrow(sql, *args)

        async def fetch(self, sql, *args):
            if "FROM obligation_source_outbox" in sql:
                return [
                    {"reference": copy.deepcopy(v[4])}
                    for v in self.outbox
                    if v[1] == args[0]
                    and v[2] == args[1]
                    and v[3] == "occurrence_captured"
                    and v[4]["occurrence_id"] == args[2]
                ]
            return await super().fetch(sql, *args)

    (tmp_path / "butler.toml").write_text(f'''
[butler]
name="health"
port=41100
[butler.scheduler]
obligation_source_mode="{mode}"
[[butler.subscription]]
name="trip-review"
kind="domain"
source="travel.trip_booked"
[butler.subscription.obligation]
version=1
capabilities=[{{capability="health__entity_graph_walk"}}]
''')
    pool = owning_pool
    if pool is None:
        pool = RecordingPool()
        pool.connection = Connection()
        for method in ("fetch", "fetchrow", "fetchval", "execute"):
            setattr(pool, method, getattr(pool.connection, method))
    daemon = ButlerDaemon(tmp_path, db=SimpleNamespace(pool=pool))
    daemon.config = load_config(tmp_path)
    daemon.config.runtime_seed = daemon.config.runtime_seed.model_copy(update={"core_groups": None})
    # The loaded source digest tracks material; effective policy is separately held.
    from butlers.config import obligation_config_material_digest

    daemon.config.source_material_sha256 = obligation_config_material_digest(daemon.config)
    daemon.mcp = FastMCP("domain-source-consumer")
    daemon.spawner = MagicMock()
    daemon._register_core_tools()
    monkeypatch.setattr(daemon, "_init_module_runtime_states", AsyncMock())
    monkeypatch.setattr(
        "butlers.core.approval_delivery_worker.start_approval_delivery_worker", AsyncMock()
    )
    monkeypatch.setattr(domain_event_wake, "record_reaction", AsyncMock())
    await _startup_module_tools(
        daemon, SimpleNamespace(pool=pool, effective_runtime=RuntimeConfig("health"))
    )
    receive = (await daemon.mcp.get_tool("receive_domain_event")).fn
    return daemon, pool, receive


async def test_registered_receive_commits_full_domain_material_then_revalidates(
    tmp_path, monkeypatch
):
    daemon, pool, receive = await domain_source_consumer(tmp_path, monkeypatch)
    c = pool.connection
    args = {
        "event_id": str(c.event["id"]),
        "event_type": c.event["event_type"],
        "source_butler": "travel",
        "payload": c.event["payload"],
    }
    result = await receive(**args)
    assert result["state"] == "task_created" and len(c.rows) == 1
    assert len(c.occurrences) == 1, "successful registered receive must commit its dark source"
    runtime = daemon.obligation_domain_runtime
    submitted = await runtime.read_committed(uuid.UUID(result["task_id"]), **args)
    assert submitted.dark.obligation.created_exposed is True
    assert submitted.dark.source.kind == "domain" and c.depth == 0
    assert json.loads(submitted.material_json)["event"]["payload"] == c.event["payload"]
    assert "reference" not in json.dumps(c.outbox, default=str)
    assert await receive(**args) == result | {"reconciled": True}
    assert len(c.occurrences) == 1
    assert await runtime.read_committed(uuid.UUID(result["task_id"]), **args) == submitted
    # Exact IDs/footer cannot substitute for full current prompt/event material.
    c.rows[uuid.UUID(result["task_id"])]["prompt"] += " altered"
    from butlers.core.obligation_sources import SourceConflict

    with pytest.raises(SourceConflict, match="domain_task_mismatch"):
        await runtime.read_committed(uuid.UUID(result["task_id"]), **args)
    await runtime.aclose()


async def test_domain_durable_refusals_replay_and_legacy_success(tmp_path, monkeypatch):
    import copy

    from butlers.core.obligation_sources import SourceConflict

    daemon, pool, receive = await domain_source_consumer(tmp_path, monkeypatch)
    c, runtime = pool.connection, daemon.obligation_domain_runtime
    args = {
        "event_id": str(c.event["id"]),
        "event_type": c.event["event_type"],
        "source_butler": "travel",
        "payload": copy.deepcopy(c.event["payload"]),
    }
    # Durable activity, not the transport's assertion, authorizes dark staging.
    c.subscription["active"] = False
    result = await receive(**args)
    assert result["status"] == "ok" and len(c.rows) == 1 and not c.occurrences
    c.subscription["active"] = True
    task_id = uuid.UUID(result["task_id"])
    result = await receive(**args)
    submitted = await runtime.read_committed(task_id, **args)
    original = copy.deepcopy((c.definitions, c.revisions, c.occurrences, c.outbox))
    event, subscription, delivery, row = copy.deepcopy(
        (c.event, c.subscription, c.delivery, c.rows[task_id])
    )
    for fault in (
        "absent_event",
        "event_identity",
        "publisher",
        "payload",
        "inactive",
        "absent_subscription",
        "subscriber",
        "absent_delivery",
        "delivery",
        "delivery_task",
        "role",
        "shadow",
        "task",
        "registry",
        "declaration",
        "cancelled",
        "claim",
    ):
        c.event, c.subscription, c.delivery, c.rows[task_id] = copy.deepcopy(
            (event, subscription, delivery, row)
        )
        if fault == "absent_event":
            c.event = None
        if fault == "event_identity":
            c.event["id"] = uuid.uuid4()
        if fault == "publisher":
            c.event["source_butler"] = "other"
        if fault == "payload":
            c.event["payload"] = {"trip": "changed"}
        if fault == "inactive":
            c.subscription["active"] = False
        if fault == "absent_subscription":
            c.subscription = None
        if fault == "subscriber":
            c.subscription["subscriber_butler"] = "other"
        if fault == "absent_delivery":
            c.delivery = None
        if fault == "delivery":
            c.delivery["event_id"] = uuid.uuid4()
        if fault == "delivery_task":
            c.delivery["task_id"] = uuid.uuid4()
        if fault == "role":
            c.role = "butler_other_rw"
        if fault == "shadow":
            c.schedule_shadow = True
        if fault == "task":
            c.rows[task_id]["job_name"] = "other"
        if fault == "registry":
            daemon._tool_catalog = None
        if fault == "declaration":
            daemon.config.subscriptions[0].source = "other.event"
        if fault == "cancelled":
            c.occurrences[submitted.dark.occurrence_id]["state"] = "cancelled"
        if fault == "claim":
            c.occurrences[submitted.dark.occurrence_id]["claim_token"] = uuid.uuid4()
        with pytest.raises(SourceConflict):
            await runtime.read_committed(task_id, **args)
        c.role, c.schedule_shadow, daemon._tool_catalog = (
            "butler_health_rw",
            False,
            runtime.registry,
        )
        daemon.config.subscriptions[0].source = "travel.trip_booked"
        c.definitions, c.revisions, c.occurrences, c.outbox = copy.deepcopy(original)
    c.event, c.subscription, c.delivery, c.rows[task_id] = copy.deepcopy(
        (event, subscription, delivery, row)
    )
    # Matching new event AND prompt still cannot rewrite the first journal.
    c.event["payload"] = args["payload"] = {
        "trip": "new",
        "exposed": False,
        "owner_authority": True,
        "caused_by_goal": "forged",
    }
    c.rows[task_id]["prompt"] = _build_wake_task_prompt(
        **{k: v for k, v in args.items() if k != "event_id"},
        event_id=c.event["id"],
        subscriber_butler="health",
    )
    with pytest.raises(SourceConflict, match="domain_capture_binding_changed"):
        await runtime.read_committed(task_id, **args)
    assert (c.definitions, c.revisions, c.occurrences, c.outbox) == original
    assert len(c.rows) == 1
    await runtime.aclose()


async def test_domain_off_refusal_and_unknown_commit_keep_legacy_task(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from butlers.core.obligation_domain_sources import start_domain_sources

    class NoAccess:
        def __getattribute__(self, key):
            raise AssertionError("OFF must not inspect material")

    assert start_domain_sources(NoAccess(), NoAccess(), NoAccess(), NoAccess()) is None
    daemon, pool, receive = await domain_source_consumer(tmp_path, monkeypatch, mode="off")
    c = pool.connection
    args = {
        "event_id": str(c.event["id"]),
        "event_type": c.event["event_type"],
        "source_butler": "travel",
        "payload": c.event["payload"],
    }
    assert (await receive(**args))["status"] == "ok"
    assert not c.definitions and not c.occurrences and daemon.obligation_domain_runtime is None
    # Same loaded declaration, actual registration and private factory; codec
    # compatibility follows the bus writer's existing serialized JSON object.
    runtime = start_domain_sources(
        pool,
        daemon,
        __import__("butlers.core.runtime_config", fromlist=["RuntimeConfig"]).RuntimeConfig(
            "health"
        ),
        daemon._tool_catalog,
        mode="dark",
    )
    daemon.obligation_domain_runtime = runtime
    c.event["payload"] = json.dumps(args["payload"])
    acquire = pool.acquire
    lose_reply = True

    @asynccontextmanager
    async def lost_commit():
        nonlocal lose_reply
        async with acquire() as connection:
            yield connection
        if lose_reply:
            lose_reply = False
            raise OSError("synthetic lost commit acknowledgement")

    pool.acquire = lost_commit
    assert (await receive(**args))["status"] == "ok"
    assert len(c.occurrences) == 1  # staged TX committed but acknowledgement lost
    first = next(iter(c.occurrences))
    assert (await receive(**args))["reconciled"] is True
    assert list(c.occurrences) == [first] and len(c.rows) == 1
    submitted = await runtime.read_committed(next(iter(c.rows)), **args)
    assert submitted.dark.obligation.created_exposed is True
    await runtime.aclose()
