"""Private registered feed consumers. SQL collaborators are software evidence only.

REQ-capability-authorization-003; REQ-goal-orchestration-007.
No provider, creator, owner stamp, Home writer or runner is exercised.
"""

import asyncio

# The production SQL runs against this isolated stateful collaborator. It is
# not evidence of PG roles, lock enforcement, migration or durable commit.
import copy
import importlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from asyncpg.pgproto.pgproto import UUID as NativeUUID

from butlers.config import SchedulerConfig, load_config
from butlers.core.catalog_publication import CatalogPublisher
from butlers.core.catalog_reader import DOMAINS
from butlers.core.goal_admission import SourceRef
from butlers.core.obligation_feed_admission import _AcceptedFeed, start_feed_admission
from tests.contracts.test_catalog_publication import Pool as CatalogPool
from tests.contracts.test_catalog_publication import Store, registered_catalog
from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog, _roster_catalog
from tests.core.test_git_obligation_admission import AdmissionConnection
from tests.core.test_obligation_schedule_sources import RecordingPool
from tests.core_tools.test_switchboard_ingest_policy_tier import (
    _envelope_kwargs,
    _FakeBuffer,
    _register_and_grab_ingest,
)

pytestmark = pytest.mark.unit


async def test_registered_ingest_offers_only_canonical_id_after_legacy_enqueue(monkeypatch):
    """The existing registered tool, rather than a test-only receiver, owns the hook."""
    from butlers.core_tools._base import ToolContext
    from butlers.core_tools._switchboard import register_switchboard_tools
    from butlers.tools.switchboard.ingestion.ingest import IngestAcceptedResponse

    canonical = uuid4()
    buffer = _FakeBuffer()
    observed = []

    class Held:
        def offer(self, request_id):
            assert len(buffer.enqueue_calls) == 1
            observed.append(request_id)

    async def accepted(*args, **kwargs):
        return IngestAcceptedResponse(duplicate=bool(observed), request_id=canonical)

    # Construct the same registered tool and its existing policy collaborator.
    _register_and_grab_ingest(monkeypatch, buffer)
    monkeypatch.setattr(
        importlib.import_module("butlers.tools.switchboard.ingestion.ingest"), "ingest_v1", accepted
    )
    daemon = SimpleNamespace(_pipeline=object(), _buffer=buffer, obligation_feed_runtime=Held())
    tools = {}

    def register(group, **kwargs):
        def keep(fn):
            tools[fn.__name__] = fn
            return fn

        return keep

    register_switchboard_tools(
        ToolContext(daemon, None, None, "switchboard", None, True, False, None),
        SimpleNamespace(),
        register,
    )
    for _ in range(2):
        result = await tools["ingest"](**_envelope_kwargs("default"))
        assert result["request_id"] == str(canonical)
    assert observed == [canonical, canonical]
    assert type(observed[0]) is UUID
    assert len(buffer.enqueue_calls) == 1
    await asyncio.sleep(0)


ROOT = Path(__file__).resolve().parents[2]


class FeedConnection(AdmissionConnection):
    def __init__(self, store):
        super().__init__(store)
        self.inbox, self.accepted, self.registry = {}, {}, {}
        self.trace = []

    @asynccontextmanager
    async def transaction(self):
        before = copy.deepcopy((self.inbox, self.accepted))
        try:
            async with super().transaction():
                yield
        except BaseException:
            self.inbox, self.accepted = before
            raise

    async def fetchrow(self, sql, *args):
        if "request_context ->> 'request_id'" in sql:
            for row in self.inbox.values():
                if args[0] in (
                    row["request_context"].get("dedupe_key"),
                    row["request_context"].get("content_hash_key"),
                ):
                    return {"request_id": row["id"]}
            return None
        if "FROM public.ingestion_events WHERE id=" in sql:
            assert self.depth and "FOR SHARE" in sql
            self.trace.append("event")
            return copy.deepcopy(self.accepted.get(args[0]))
        if "FROM switchboard.connector_registry" in sql:
            assert self.depth and "FOR SHARE" in sql
            self.trace.append("registry")
            row = copy.deepcopy(self.registry.get(args))
            return (
                row | {"deleted_at": row.get("deleted_at"), "archived_at": row.get("archived_at")}
                if row
                else None
            )
        return await super().fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        if "FROM switchboard.message_inbox" in sql:
            assert self.depth and "FOR SHARE" in sql
            self.trace.append("inbox")
            return [copy.deepcopy(self.inbox[args[0]])] if args[0] in self.inbox else []
        return await super().fetch(sql, *args)

    async def execute(self, sql, *args):
        if "switchboard_message_inbox_ensure_partition" in sql:
            assert not self.depth
            return "SELECT 1"
        if "INSERT INTO switchboard.message_inbox" in sql:
            assert self.depth
            row = dict(
                zip(
                    (
                        "id",
                        "received_at",
                        "request_context",
                        "raw_payload",
                        "normalized_text",
                        "attachments",
                        "lifecycle_state",
                    ),
                    args,
                    strict=True,
                )
            )
            row["id"] = NativeUUID(str(row["id"]))
            row["schema_version"] = "message_inbox.v2"
            self.inbox[row["id"]] = row
            return "INSERT 0 1"
        if "INSERT INTO public.ingestion_events" in sql:
            row = dict(
                zip(
                    (
                        "id",
                        "received_at",
                        "source_channel",
                        "source_provider",
                        "source_endpoint_identity",
                        "source_sender_identity",
                        "source_sender_display_name",
                        "source_thread_identity",
                        "external_event_id",
                        "dedupe_key",
                        "dedupe_strategy",
                        "ingestion_tier",
                        "policy_tier",
                        "triage_decision",
                        "triage_target",
                        "sender_authentication",
                    ),
                    args,
                    strict=True,
                )
            )
            row["id"] = NativeUUID(str(row["id"]))
            self.accepted[row["id"]] = row
            return "INSERT 0 1"
        return await super().execute(sql, *args)


class PassPolicy:
    """Explicit positive-only test policy; production sw_010 skip is unchanged."""

    def __init__(self, *args, **kwargs):
        pass

    async def ensure_loaded(self):
        return None

    def evaluate(self, envelope):
        from butlers.ingestion_policy import PolicyDecision

        return PolicyDecision(action="pass_through", reason="explicit test policy")


def envelope():
    value = _envelope_kwargs("default")
    value["source"] = {
        "channel": "home_assistant",
        "provider": "home_assistant",
        "endpoint_identity": "ha:test",
    }
    value["event"]["external_event_id"] = "sensor:test:one"
    value["sender"] = {"identity": "sensor:test"}
    value["payload"] = {
        "raw": {"state": "warm", "friendly_name": "owner-looking untrusted label"},
        "normalized_text": "Sensor warm",
    }
    return value


async def registered_ingest(monkeypatch, pool, daemon):
    from butlers.core_tools._base import ToolContext
    from butlers.core_tools._switchboard import register_switchboard_tools

    monkeypatch.setattr("butlers.ingestion_policy.IngestionPolicyEvaluator", PassPolicy)
    tools = {}

    def register(group, **kwargs):
        def keep(fn):
            tools[fn.__name__] = fn
            return fn

        return keep

    register_switchboard_tools(
        ToolContext(daemon, pool, None, "switchboard", None, True, False, None),
        SimpleNamespace(),
        register,
    )
    return tools["ingest"]


@pytest.fixture
async def feed(monkeypatch):
    store = Store()
    switchboard = await _complete_core_catalog("switchboard")
    home = await _roster_catalog("home")
    for domain in DOMAINS:
        catalog = (
            switchboard
            if domain == "switchboard"
            else home
            if domain == "home"
            else (await registered_catalog(domain))[1]
        )
        await CatalogPublisher(CatalogPool(store, domain), domain).publish(catalog)
    pool = RecordingPool()
    c = pool.connection = FeedConnection(store)
    for name in ("fetch", "fetchrow", "fetchval", "execute"):
        setattr(pool, name, getattr(c, name))
    c.registry[("home_assistant", "ha:test")] = {
        "connector_type": "home_assistant",
        "endpoint_identity": "ha:test",
        "capabilities": {"channel_kind": "feed"},
        "deleted_at": None,
        "archived_at": None,
    }
    daemon = SimpleNamespace(
        config=load_config(ROOT / "roster/switchboard"),
        _tool_catalog=switchboard,
        _pipeline=object(),
        _buffer=_FakeBuffer(),
    )
    daemon.obligation_feed_runtime = start_feed_admission(pool, daemon, switchboard, mode="dark")
    ingest = await registered_ingest(monkeypatch, pool, daemon)
    try:
        yield pool, daemon, ingest, store
    finally:
        await daemon.obligation_feed_runtime.aclose()
        await asyncio.sleep(0)


async def settle(runtime):
    async with asyncio.timeout(2):
        while tasks := set(runtime._pending) | set(runtime._watchers):
            await asyncio.wait(tasks)


async def test_actual_ingest_loaded_home_publisher_admission_and_replay(feed):
    pool, daemon, ingest, _ = feed
    c = pool.connection
    response = await ingest(**envelope())
    await settle(daemon.obligation_feed_runtime)
    assert response["status"] == "accepted" and not response["duplicate"]
    assert len(daemon._buffer.enqueue_calls) == 1
    assert len(c.goals) == len(c.budgets) == len(c.decisions) == 1
    goal = next(iter(c.goals.values()))
    assert goal[5] == "obligation" and goal[6] is True
    assert goal[3] == response["request_id"] and goal[4] is None
    assert {g["capability"] for g in goal[7]} == {
        g.capability
        for g in daemon.obligation_feed_runtime.declaration().obligation.declaration.capabilities
    }
    assert c.catalog_reads == 1 and c.trace == ["inbox", "event", "registry"]
    assert c.events.index("admission_key_lock") < c.events.index("goal_insert") < len(c.events) - 1
    assert goal[10] and goal[12:14] == (None, None)
    assert isinstance(next(iter(c.inbox.values()))["id"], NativeUUID)
    prior = copy.deepcopy((c.goals, c.budgets, c.decisions))
    # Kind/material drift after a committed decision cannot reclassify the same source.
    c.registry[("home_assistant", "ha:test")]["capabilities"] = {"channel_kind": "message"}
    duplicate = await ingest(**envelope())
    await settle(daemon.obligation_feed_runtime)
    assert duplicate["duplicate"] and duplicate["request_id"] == response["request_id"]
    assert (c.goals, c.budgets, c.decisions) == prior and c.catalog_reads == 1
    assert len(daemon._buffer.enqueue_calls) == 1


async def accepted_without_hook(feed):
    pool, daemon, ingest, _ = feed
    runtime = daemon.obligation_feed_runtime
    daemon.obligation_feed_runtime = None
    result = await ingest(**envelope())
    daemon.obligation_feed_runtime = runtime
    return pool.connection, UUID(result["request_id"]), runtime


@pytest.mark.parametrize(
    "mutation",
    [
        "skip",
        "metadata",
        "quarantine",
        "unknown_feed",
        "no_declaration",
        "registry_missing",
        "registry_malformed",
        "identity",
        "content",
        "file_drift",
        "ceiling_actuate",
        "ceiling_resource",
        "notify",
    ],
)
async def test_persisted_feed_refusals_never_fall_back_to_message(feed, monkeypatch, mutation):
    c, request, runtime = await accepted_without_hook(feed)
    row, event = c.inbox[request], c.accepted[request]
    if mutation == "skip":
        event["triage_decision"] = row["request_context"]["triage_decision"] = "skip"
    elif mutation == "metadata":
        event["ingestion_tier"] = row["request_context"]["ingestion_tier"] = row["raw_payload"][
            "control"
        ]["ingestion_tier"] = "metadata"
        row["lifecycle_state"] = "metadata_ref"
    elif mutation == "quarantine":
        row["lifecycle_state"] = "quarantined"
    elif mutation == "unknown_feed":
        event["source_channel"] = event["source_provider"] = "wellness"
        row["raw_payload"]["source"].update(channel="wellness", provider="wellness")
        row["request_context"]["source_channel"] = "wellness"
        c.registry[("wellness", "ha:test")] = {
            "connector_type": "wellness",
            "endpoint_identity": "ha:test",
            "capabilities": {"channel_kind": "feed"},
            "deleted_at": None,
            "archived_at": None,
        }
    elif mutation == "no_declaration":
        runtime.home.subscriptions = []
        from butlers.config import obligation_config_material_digest

        runtime.home_digest = runtime.home.source_material_sha256 = (
            obligation_config_material_digest(runtime.home)
        )
    elif mutation.startswith("registry"):
        c.registry.clear()
        if mutation == "registry_malformed":
            c.registry[("home_assistant", "ha:test")] = {
                "connector_type": "home_assistant",
                "endpoint_identity": "ha:test",
                "capabilities": {},
                "deleted_at": None,
                "archived_at": None,
            }
    elif mutation == "identity":
        event["source_sender_identity"] = "another"
    elif mutation == "content":
        row["normalized_text"] = "mismatch"
    elif mutation == "file_drift":
        runtime.home.source_sha256 = "changed"
    elif mutation in {"ceiling_actuate", "ceiling_resource"}:
        # Test-local loaded declaration successor: no production file/activation.
        from butlers.config import SubscriptionSpec, obligation_config_material_digest

        grant = (
            {"capability": "home__ha_call_service"}
            if mutation == "ceiling_actuate"
            else {"capability": "home__ha_get_entity_state", "resources": {"invented": ["x"]}}
        )
        runtime.home.subscriptions = [
            SubscriptionSpec(
                name="test",
                kind="feed",
                source="home_assistant",
                obligation={"version": 1, "capabilities": [grant]},
            )
        ]
        runtime.home_digest = runtime.home.source_material_sha256 = (
            obligation_config_material_digest(runtime.home)
        )
    elif mutation == "notify":
        from butlers.core.catalog_publication import decode_publication, generation, publication

        store = feed[3]
        home = await _roster_catalog("home")
        body = publication(home, "home")
        for item in body["descriptors"]:
            if item["canonical_name"] == "notify":
                item["recipient_params"].pop("owner_notify_branch")
        body["generation_digest"] = generation(body["descriptors"])
        home = decode_publication(body, "home")
        await CatalogPublisher(CatalogPool(store, "home"), "home").publish(home)
    runtime.offer(request)
    await settle(runtime)
    assert not c.goals and not c.budgets
    assert len(c.decisions) == 1
    decision = next(iter(c.decisions.values()))
    assert decision["result"] == "refused"
    if mutation in {"skip", "metadata", "quarantine", "unknown_feed", "no_declaration"}:
        assert decision["reason"] == "recorded_without_goal"
        # A no-goal attempt is durable under the same source even if its policy changes.
        c.accepted[request]["triage_decision"] = c.inbox[request]["request_context"][
            "triage_decision"
        ] = "pass_through"
        runtime.offer(request)
        await settle(runtime)
        assert len(c.decisions) == 1 and not c.goals


async def test_full_material_digest_and_commit_uncertainty_do_not_invent_success(feed):
    c, request, runtime = await accepted_without_hook(feed)
    reader = _AcceptedFeed(runtime)
    async with c.transaction():
        facts = await reader.load(c, SourceRef("message", str(request)))
        assert (
            (await reader.load(c)).digest
            and facts.outside_content
            and facts.untainted_owner_stamp is None
        )
    c.inbox[request]["raw_payload"]["payload"]["raw"]["state"] = "cool"
    async with c.transaction():
        changed = await _AcceptedFeed(runtime).load(c, SourceRef("message", str(request)))
    assert changed.obligation.digest != facts.obligation.digest
    c.lose_reply = True
    runtime.offer(request)
    await settle(runtime)
    assert len(c.goals) == len(c.decisions) == 1
    runtime.offer(request)
    await settle(runtime)
    assert len(c.goals) == len(c.decisions) == 1 and c.catalog_reads == 3


def test_strict_off_and_factory_binding_have_no_source_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("OFF read source")

    monkeypatch.setattr("butlers.core.obligation_feed_admission.load_config", forbidden)
    assert start_feed_admission(None, None, None, mode="off") is None
    with pytest.raises(ValueError):
        start_feed_admission(
            None, SimpleNamespace(config=SimpleNamespace(name="home")), None, mode="dark"
        )
    assert SchedulerConfig().obligation_feed_admission_mode == "off"
    for bad in (True, False, "on", "enabled", 1, None):
        with pytest.raises(ValueError):
            SchedulerConfig(obligation_feed_admission_mode=bad)


@pytest.mark.parametrize("fault", ["role", "shadow", "schema", "fleet", "UUID_string", "UUID_duck"])
async def test_actual_source_and_fleet_refusals_preserve_legacy_acceptance(feed, fault):
    c, request, runtime = await accepted_without_hook(feed)
    if fault == "role":
        c.role = "butler_health_rw"
    elif fault == "shadow":
        c.shadow = True
    elif fault == "schema":
        original = c.fetchrow

        async def wrong(sql, *args):
            row = await original(sql, *args)
            return row | {"schema": "home"} if "current_schema()" in sql else row

        c.fetchrow = wrong
    elif fault == "fleet":
        feed[3].current.pop("home")
    elif fault == "UUID_string":
        c.inbox[request]["id"] = str(request)
    elif fault == "UUID_duck":
        c.inbox[request]["id"] = SimpleNamespace(bytes=request.bytes)
    runtime.offer(request)
    await settle(runtime)
    assert not c.goals and not c.budgets
    assert len(feed[1]._buffer.enqueue_calls) == 1 and len(c.inbox) == 1


@pytest.mark.parametrize("resistant", [False, True])
async def test_owned_deadline_capacity_and_repeated_close_cancellation(
    feed, monkeypatch, resistant
):
    import butlers.core.obligation_feed_admission as module
    from butlers.core.goal_admission import GoalAdmission

    c, request, runtime = await accepted_without_hook(feed)
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocking(self, *args):
        started.set()
        while True:
            try:
                await release.wait()
                return None
            except asyncio.CancelledError:
                cancelled.set()
                if not resistant:
                    raise

    monkeypatch.setattr(GoalAdmission, "admit_submission", blocking)
    monkeypatch.setattr(module, "ADMISSION_SECONDS", 0.04)
    runtime.offer(request)
    await started.wait()
    pending = dict(runtime._pending)
    runtime.offer(uuid4())
    assert runtime._pending == pending
    closer = asyncio.create_task(runtime.aclose())
    try:
        await cancelled.wait()
        if resistant:
            closer.cancel()
            await asyncio.sleep(0)
            closer.cancel()
            with pytest.raises(RuntimeError, match="cleanup_incomplete"):
                await closer
            assert runtime._pending == pending
            with pytest.raises(RuntimeError, match="cleanup_incomplete"):
                await runtime.aclose()
        else:
            await closer
            assert not runtime._pending
        assert not c.goals and not c.decisions
    finally:
        release.set()
        await asyncio.wait(set(pending), timeout=1)
        assert all(t.done() for t in pending)
        await runtime.aclose()
    runtime.offer(request)
    assert not runtime._pending


@pytest.mark.parametrize("outcome", ["healthy", "expired", "cancel"])
async def test_real_daemon_release_continues_after_feed_incomplete(
    feed, monkeypatch, tmp_path, outcome
):
    import butlers.core.obligation_feed_admission as module
    from butlers.core.goal_admission import GoalAdmission
    from butlers.daemon import ButlerDaemon

    c, request, runtime = await accepted_without_hook(feed)
    daemon = ButlerDaemon(tmp_path)
    daemon.config = feed[1].config
    daemon.config.shutdown_timeout_s = 0.05
    daemon.obligation_feed_runtime = runtime
    calls = []

    async def close(name):
        calls.append(name)

    daemon._modules = [SimpleNamespace(name="native", on_shutdown=lambda: close("module"))]
    daemon.blob_store = SimpleNamespace(close=lambda: close("blob"))
    daemon._audit_db = SimpleNamespace(close=lambda: close("audit"))
    daemon._shared_credentials_db = SimpleNamespace(close=lambda: close("credentials"))
    daemon.db = SimpleNamespace(close=lambda: close("db"))
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def resistant(self, *args):
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()

    owned = set()
    if outcome != "healthy":
        monkeypatch.setattr(GoalAdmission, "admit_submission", resistant)
        monkeypatch.setattr(module, "ADMISSION_SECONDS", 0.04 if outcome == "expired" else 1)
        runtime.offer(request)
        await entered.wait()
        owned = set(runtime._pending) | set(runtime._watchers)
        if outcome == "expired":
            await cancelled.wait()
    shutdown = asyncio.create_task(daemon.shutdown())
    try:
        if outcome == "cancel":
            await cancelled.wait()
            shutdown.cancel()
            await asyncio.sleep(0)
            shutdown.cancel()
            release.set()
        if outcome == "expired":
            with pytest.raises(RuntimeError, match="cleanup_incomplete"):
                await shutdown
        elif outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await shutdown
        else:
            await shutdown
        assert calls == ["module", "blob", "audit", "credentials", "db"]
        retained = daemon._catalog_shutdown_task
        assert retained.done()
        if outcome == "expired":
            with pytest.raises(RuntimeError, match="cleanup_incomplete"):
                await daemon.shutdown()
        else:
            await daemon.shutdown()
        assert daemon._catalog_shutdown_task is retained
        assert calls == ["module", "blob", "audit", "credentials", "db"]
    finally:
        release.set()
        if owned:
            done, pending = await asyncio.wait(owned, timeout=1)
            assert not pending
            await asyncio.gather(*done, return_exceptions=True)
        await runtime.aclose()


async def test_actual_module_startup_binds_selected_factory_and_off_reads_nothing(
    feed, monkeypatch, tmp_path
):
    from unittest.mock import AsyncMock

    from butlers import lifecycle
    from butlers.core.runtime_config import RuntimeConfig
    from butlers.daemon import ButlerDaemon

    pool, source, _, _ = feed
    daemon = ButlerDaemon(tmp_path)
    daemon.config = source.config
    daemon._tool_catalog = source._tool_catalog
    daemon._register_module_tools = AsyncMock()
    daemon._apply_approval_gates = AsyncMock(return_value={})
    daemon._finalize_tool_catalog = AsyncMock(return_value=source._tool_catalog)
    daemon._init_module_runtime_states = AsyncMock()
    state = lifecycle.StartupState(pool=pool, effective_runtime=RuntimeConfig("switchboard"))
    await lifecycle._startup_module_tools(daemon, state)
    assert daemon.obligation_feed_runtime is None and not pool.connection.trace
    daemon.config.scheduler = SchedulerConfig(obligation_feed_admission_mode="dark")
    await lifecycle._startup_module_tools(daemon, state)
    assert daemon.obligation_feed_runtime.daemon is daemon
    assert daemon.obligation_feed_runtime.registry is source._tool_catalog
    assert daemon.obligation_feed_runtime.home.name == "home"
    assert not pool.connection.trace
    await daemon.obligation_feed_runtime.aclose()
