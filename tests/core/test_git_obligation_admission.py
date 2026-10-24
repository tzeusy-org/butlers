"""Private Git submitter consumers; SQL collaborators are software proof only.

REQ-goal-orchestration-007; REQ-capability-authorization-003.
No collaborator establishes production role, source, catalog or D7 authority.
"""

import asyncio
import copy
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.config import SchedulerConfig, load_config
from butlers.core.catalog_publication import CatalogPublisher
from butlers.core.catalog_reader import DOMAINS
from butlers.core.goal_admission import EvidenceRefused
from butlers.core.obligation_git_admission import (
    bind_private_git_admission,
)
from butlers.core.obligation_git_sources import (
    GitScheduleRuntime,
    capture_loaded_git_sources,
    start_schedule_sources,
)
from butlers.core.runtime_config import RuntimeConfig
from butlers.core.scheduler import _tick_deadline_pass, tick
from tests.contracts.test_catalog_publication import Connection as CatalogConnection
from tests.contracts.test_catalog_publication import Pool as CatalogPool
from tests.contracts.test_catalog_publication import Store, registered_catalog
from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog
from tests.core.test_obligation_schedule_dispatch import NOW, OccurrenceConnection, runtime_fixture
from tests.core.test_obligation_schedule_sources import RecordingPool
from tests.switchboard.goal_admission_fixtures import limits

_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("deadline", [False, True], ids=["cron", "deadline"])
async def test_scheduler_delivers_committed_candidate_after_legacy_dispatch(
    tmp_path, monkeypatch, deadline
):
    pool, runtime, _ = await runtime_fixture(tmp_path, deadline=deadline)
    pool.fetch = pool.connection.fetch
    pool.fetchrow = pool.connection.fetchrow
    pool.fetchval = pool.connection.fetchval
    pool.execute = pool.connection.execute
    dispatch = AsyncMock(return_value={"legacy": "unchanged"})
    delivered = []

    async def observed(self, submitted):
        # Forwarding control only: this is not a positive authority adapter.
        assert pool.connection.depth == 0
        assert dispatch.await_count == 1
        assert await self.read_committed_submission(pool, submitted) == submitted
        delivered.append(submitted)

    monkeypatch.setattr(GitScheduleRuntime, "submit_committed", observed, raising=False)

    async def run():
        if deadline:
            _, dispatched = await _tick_deadline_pass(
                pool, dispatch, now=NOW, source_runtime=runtime
            )
            return dispatched
        return await tick(pool, dispatch, source_runtime=runtime)

    assert await run() == 1
    assert len(delivered) == 1
    assert await run() == 0
    assert len(delivered) == 1 and dispatch.await_count == 1


# These collaborators execute production SQL categories but cannot prove PG
# locking, role enforcement or durability. The integration owner is separate.


class AdmissionConnection(OccurrenceConnection):
    def __init__(self, store):
        super().__init__()
        self.role = "butler_switchboard_rw"
        self.catalog = CatalogConnection(store, "switchboard")
        self.decisions, self.attempts, self.goals, self.budgets = {}, {}, {}, {}
        self.catalog_reads = 0
        self.events = []
        self.policy = limits()
        self.lose_reply = False

    @asynccontextmanager
    async def transaction(self):
        old = copy.deepcopy((self.decisions, self.attempts, self.goals, self.budgets))
        async with super().transaction():
            try:
                yield
            except BaseException:
                self.decisions, self.attempts, self.goals, self.budgets = old
                raise
        self.events.append("commit")
        if self.lose_reply:
            self.lose_reply = False
            raise OSError("controlled reply loss")

    async def fetchrow(self, sql, *args):
        if "FROM pg_roles" in sql:
            return {"name": self.role, "rolsuper": False, "rolbypassrls": False}
        if "current_schema()" in sql:
            return {"schema": "switchboard", "role": self.role, "shadowed": self.shadow}
        if "AS registrations" in sql:
            self.catalog_reads += 1
            return await self.catalog.fetchrow(sql, *args)
        if "FROM switchboard.admission_attempt_bindings" in sql:
            return self.decisions.get(self.attempts.get(args[0]))
        if "FROM switchboard.admission_decisions" in sql:
            if "count(*)" in sql:
                return {"origin_count": len(self.goals), "source_count": len(self.goals)}
            return next(
                (
                    v
                    for v in self.decisions.values()
                    if v["attempt_id" if "attempt_id=$1" in sql else "source_key"] == args[0]
                ),
                None,
            )
        if "FOR UPDATE" in sql:
            self.events.append("source_row_lock")
        return await super().fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        if "goal_admission_config" in sql:
            return self.policy
        if "clock_timestamp()" in sql:
            return NOW
        if "pg_has_role" in sql:
            return False
        return await super().fetchval(sql, *args)

    async def fetch(self, sql, *args):
        if "FROM pg_class" in sql:
            return await self.catalog.fetch(sql, *args)
        if "FROM switchboard.goals" in sql:
            assert not args[0]
            return []
        return await super().fetch(sql, *args)

    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock($1::bigint)" in sql:
            self.events.append("admission_key_lock")
        if "INSERT INTO switchboard.admission_attempt_bindings" in sql:
            self.attempts.setdefault(args[0], args[2])
        elif "INSERT INTO switchboard.admission_decisions" in sql:
            self.decisions[args[0]] = dict(
                zip(
                    (
                        "id",
                        "attempt_id",
                        "source_key",
                        "source_ref",
                        "source_kind",
                        "goal_id",
                        "result",
                        "reason",
                    ),
                    args[:8],
                    strict=True,
                )
            )
        elif "INSERT INTO switchboard.goals" in sql:
            self.events.append("goal_insert")
            self.goals[args[0]] = args
        elif "INSERT INTO switchboard.budgets" in sql:
            self.budgets[args[0]] = args
        else:
            return await super().execute(sql, *args)
        return "INSERT 0 1"


async def owning_runtime(*, actual_roster=False):
    registry = await _complete_core_catalog("switchboard")
    config = load_config(_ROOT / "roster" / "switchboard")
    if not actual_roster:
        # A real loaded Git fixture selecting an ACTUALLY registered read tool.
        # The delivered Insight graph still requests absent staffer notify; it
        # has its own refusal control, never an invented registration here.
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "butler.toml").write_text("""
[butler]
name="switchboard"
port=41100
type="staffer"
[[butler.schedule]]
name="private-git-read"
cron="* * * * *"
prompt="Read the registered entity graph."
[butler.schedule.obligation]
version=1
capabilities=[{capability="switchboard__entity_graph_walk"}]
""")
            config = load_config(path)
    loaded = capture_loaded_git_sources(config, RuntimeConfig("switchboard"), registry)
    store = Store()
    for domain in DOMAINS:
        catalog = (
            registry
            if domain == "switchboard"
            else await _complete_core_catalog("health")
            if domain == "health"
            else (await registered_catalog(domain))[1]
        )
        await CatalogPublisher(CatalogPool(store, domain), domain).publish(catalog)
    pool = RecordingPool()
    pool.startup_config = config
    pool.connection = AdmissionConnection(store)
    runtime = await start_schedule_sources(pool, loaded, mode="dark")
    runtime = bind_private_git_admission(pool, runtime, loaded, registry, mode="dark")
    material = (
        next(m for _, m in runtime.bindings if m.schedule["job_name"] == "insight_delivery_cycle")
        if actual_roster
        else runtime.bindings[0][1]
    )
    source = next(d.source_id for d, m in runtime.bindings if m == material)
    # Only the actual registered Insight closure is due; use its original slot.
    for row in pool.connection.rows.values():
        row.update(next_run_at=None, fired_thresholds=[], deadline_status="pending", depends_on=[])
    pool.connection.rows[source]["next_run_at"] = NOW - timedelta(minutes=1)
    for name in ("fetch", "fetchrow", "fetchval", "execute"):
        setattr(pool, name, getattr(pool.connection, name))
    return pool, runtime, source, loaded, registry


async def staged_candidate(pool, runtime, source):
    selected = copy.deepcopy(pool.connection.rows[source])
    claim = await runtime.claim_cron(
        pool, selected, next_run_at=NOW + timedelta(days=1), disable=False
    )
    assert pool.connection.depth == 0
    return claim.submission


async def test_real_factory_registered_scheduler_and_admission_use_one_owning_transaction():
    pool, runtime, source, _, _ = await owning_runtime()
    c = pool.connection
    dispatch = AsyncMock(return_value={"status": "legacy"})
    assert await tick(pool, dispatch, source_runtime=runtime) == 1
    assert dispatch.await_count == 1 and c.depth == 0
    assert [v["reason"] for v in c.decisions.values()] == ["new_goal"]
    assert len(c.goals) == len(c.budgets) == len(c.decisions) == len(c.attempts) == 1
    goal = next(iter(c.goals.values()))
    assert goal[5] == "obligation" and goal[6] is True
    assert {g["capability"] for g in goal[7]} == {"switchboard__entity_graph_walk"}
    assert c.catalog_reads == 1
    # Actual admission keys precede owning source locks, and commit follows goal.
    admission = c.events.index("admission_key_lock")
    assert admission < c.events.index("source_row_lock", admission) < c.events.index("goal_insert")
    assert c.events[-1] == "commit"
    submitted = await staged_read(pool, runtime, source)
    result = await runtime.submit_committed(submitted)
    assert result.goal_id == goal[0] and len(c.goals) == 1 and c.catalog_reads == 1
    await runtime.close_admission()


async def staged_read(pool, runtime, source):
    from butlers.core.obligation_admission import prepare_dark_submission, run_source_transaction

    occurrence = next(v for v in pool.connection.occurrences.values() if v["source_id"] == source)

    async def read(repo):
        return await prepare_dark_submission(
            repo, source, occurrence["id"], occurrence["claim_token"]
        )

    return await run_source_transaction(pool, "switchboard", read, require_fresh=True)


@pytest.mark.parametrize(
    "fault",
    ["token", "payload", "row", "cancelled", "shadow", "role", "fleet", "held_registry", "limits"],
)
async def test_actual_admission_refuses_stale_or_unavailable_inputs_without_goal(fault):
    pool, runtime, source, _, _ = await owning_runtime()
    submitted = await staged_candidate(pool, runtime, source)
    c = pool.connection
    if fault == "token":
        submitted = replace(submitted, claim_token=uuid4())
    elif fault == "payload":
        submitted = replace(submitted, obligation=replace(submitted.obligation, digest="forged"))
    elif fault == "row":
        c.rows[source]["prompt"] = "changed held material"
    elif fault == "cancelled":
        c.occurrences[submitted.occurrence_id]["state"] = "cancelled"
    elif fault == "shadow":
        c.shadow = True
    elif fault == "role":
        c.role = "butler_health_rw"
    elif fault == "fleet":
        c.catalog.store.current.pop("health")
    elif fault == "held_registry":
        _, catalog = await registered_catalog("switchboard")
        await CatalogPublisher(CatalogPool(c.catalog.store, "switchboard"), "switchboard").publish(
            catalog
        )
    elif fault == "limits":
        c.policy = None
    result = await runtime.submit_committed(submitted)
    assert result is None or result.result == "refused"
    assert not c.goals and not c.budgets and c.depth == 0
    await runtime.close_admission()


async def test_actual_commit_reply_loss_is_unknown_then_same_occurrence_resolves_history():
    pool, runtime, source, _, _ = await owning_runtime()
    submitted = await staged_candidate(pool, runtime, source)
    pool.connection.lose_reply = True
    assert await runtime.submit_committed(submitted) is None
    assert len(pool.connection.goals) == 1
    result = await runtime.submit_committed(submitted)
    assert result.result == "admitted" and len(pool.connection.goals) == 1
    assert pool.connection.attempts.keys() == {submitted.occurrence_id}
    await runtime.close_admission()


def test_strict_separate_switch_and_off_zero_io():
    from pydantic import ValidationError

    assert SchedulerConfig().obligation_admission_mode == "off"
    assert bind_private_git_admission(None, None, None, None) is None
    for values in (
        {"obligation_admission_mode": True},
        {"obligation_admission_mode": "enabled"},
        {"obligation_admission_mode": "dark"},
        {"extra_authority": True},
    ):
        with pytest.raises(ValidationError):
            SchedulerConfig(**values)
    assert SchedulerConfig(obligation_source_mode="dark", obligation_admission_mode="dark")
    with pytest.raises(ValueError, match="binding_unavailable"):
        bind_private_git_admission(None, None, None, None, mode="dark")


async def test_cancellation_and_repeated_cancel_retain_one_absolute_cleanup_bound(monkeypatch):
    from butlers.core import obligation_git_admission as module

    pool, runtime, source, _, _ = await owning_runtime()
    submitted = await staged_candidate(pool, runtime, source)
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def resistant(self, *args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    # Cleanup control only, never evidence of admitted authority or SQL success.
    monkeypatch.setattr(module.GoalAdmission, "admit_submission", resistant)
    task = asyncio.create_task(runtime.submit_committed(submitted))
    await entered.wait()
    task.cancel()
    await cancelled.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert not runtime.admission._pending
    await runtime.close_admission()


async def test_actual_switchboard_insight_graph_cannot_invent_staffer_notify():
    pool, runtime, source, _, registry = await owning_runtime(actual_roster=True)
    assert "notify" not in registry
    submitted = await staged_candidate(pool, runtime, source)
    assert {g.capability for g in submitted.obligation.capabilities} == {"switchboard__notify"}
    result = await runtime.submit_committed(submitted)
    assert result.result == "refused" and result.reason == "source_evidence_unavailable"
    assert not pool.connection.goals and not pool.connection.budgets
    await runtime.close_admission()


async def test_one_current_snapshot_preserves_notify_eligibility_and_exact_resource_keys():
    from butlers.core.goal_admission import Grant, Obligation, derive_admission
    from butlers.core.obligation_git_admission import RegisteredGitCatalog

    pool, runtime, _, _, registry = await owning_runtime()
    async with pool.acquire() as connection, connection.transaction():
        view = await RegisteredGitCatalog(registry).load(connection)
    notify = next(c for c in view.capabilities if c.id == "health__notify")
    assert notify.owner_default and set(notify.resource_params) == {
        "recipient",
        "entity_id",
        "request_context",
    }
    # This is ceiling eligibility, never recipient ownership or a call grant.
    assert pool.connection.catalog_reads == 1
    valid = Obligation("health", "fixed", (Grant("health__notify", (("entity_id", ("a",)),)),))
    from butlers.core.goal_admission import Evidence

    assert derive_admission(Evidence("occurrence", "own", obligation=valid), view).ceiling
    with pytest.raises(EvidenceRefused, match="invalid_contract_resources"):
        derive_admission(
            Evidence(
                "occurrence",
                "own",
                obligation=replace(
                    valid, capabilities=(Grant("health__notify", (("forged_key", ("a",)),)),)
                ),
            ),
            view,
        )
    await runtime.close_admission()


async def test_timeout_refuses_unsettled_cleanup_without_fresh_grace_or_success(monkeypatch):
    from butlers.core import obligation_git_admission as module

    pool, runtime, source, _, _ = await owning_runtime()
    submitted = await staged_candidate(pool, runtime, source)
    release, interrupted = asyncio.Event(), asyncio.Event()

    async def resistant(self, *args):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            interrupted.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass

    monkeypatch.setattr(module.GoalAdmission, "admit_submission", resistant)
    start = asyncio.get_running_loop().time()
    try:
        assert await runtime.submit_committed(submitted) is None
        # cancel() schedules delivery; allow the synthetic worker to observe it.
        # This observation does not grant product cleanup a new deadline.
        async with asyncio.timeout(1):
            await interrupted.wait()
        assert runtime.admission._pending
        elapsed = asyncio.get_running_loop().time() - start
        assert module.SUBMISSION_SECONDS <= elapsed < module.SUBMISSION_SECONDS + 1
        assert await runtime.submit_committed(submitted) is None
        with pytest.raises(RuntimeError, match="cleanup_incomplete"):
            await runtime.close_admission()
    finally:
        # Always release this test's deliberate resistant worker, even if an
        # assertion fails. No product result is inferred from fixture cleanup.
        pending = set(runtime.admission._pending)
        release.set()
        if pending:
            done, unsettled = await asyncio.wait(pending, timeout=1)
            assert not unsettled and done == pending
        await runtime.close_admission()


@pytest.mark.parametrize(
    "outcome", ["expired", "expired_later_error", "cancel", "admission_cancel", "healthy"]
)
async def test_actual_daemon_shutdown_continues_after_private_admission_close(
    tmp_path, monkeypatch, outcome
):
    import logging

    from butlers.core import obligation_git_admission as module
    from butlers.daemon import ButlerDaemon

    pool, runtime, source, _, _ = await owning_runtime()
    daemon = ButlerDaemon(tmp_path)
    daemon.config = pool.startup_config
    # Existing downstream-owner budget; the admission's real five seconds are
    # unchanged. A stuck pool release must not extend either absolute bound.
    daemon.config.shutdown_timeout_s = 0.05
    daemon.obligation_schedule_runtime = runtime
    entered, interrupted, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    admission_released, pool_release = asyncio.Event(), asyncio.Event()
    events, calls = [], []
    submitted_task = None

    async def resistant(self, *args):
        async with self.pool.acquire():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                interrupted.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass
            finally:
                admission_released.set()

    if outcome != "healthy":
        monkeypatch.setattr(module.GoalAdmission, "admit_submission", resistant)
        submitted = await staged_candidate(pool, runtime, source)
        submitted_task = asyncio.create_task(runtime.submit_committed(submitted))
        await entered.wait()
        original_pending = dict(runtime.admission._pending)
        assert len(original_pending) == 1
        if outcome.startswith("expired"):
            assert await submitted_task is None
            await interrupted.wait()
            assert runtime.admission._pending == original_pending
    else:
        assert await runtime.submit_committed(await staged_candidate(pool, runtime, source))
        original_pending = {}
        admission_released.set()

    async def loop_owner(name):
        try:
            await asyncio.Event().wait()
        finally:
            events.append(name)

    for attr, name in (
        ("_scheduler_loop_task", "scheduler"),
        ("_route_inbox_recovery_task", "recovery"),
        ("_liveness_reporter_task", "liveness"),
        ("_approval_delivery_task", "approval_delivery"),
    ):
        setattr(daemon, attr, asyncio.create_task(loop_owner(name)))
    daemon._approval_delivery_stop = asyncio.Event()

    async def close(name):
        calls.append(name)
        if name == "blob" and outcome == "expired_later_error":
            raise ConnectionError("controlled downstream close failure")
        if name == "db":
            # A release may itself resist cancellation while a connection is
            # owned. This is an explicit software settlement control only.
            while not admission_released.is_set():
                try:
                    await admission_released.wait()
                except asyncio.CancelledError:
                    pass
            if outcome == "expired_later_error":
                while not pool_release.is_set():
                    try:
                        await pool_release.wait()
                    except asyncio.CancelledError:
                        pass
        events.append(name)

    daemon._modules = [SimpleNamespace(name="native", on_shutdown=lambda: close("module"))]
    daemon.blob_store = SimpleNamespace(close=lambda: close("blob"))
    daemon._audit_db = SimpleNamespace(close=lambda: close("audit"))
    daemon._shared_credentials_db = SimpleNamespace(close=lambda: close("credentials"))
    daemon.db = SimpleNamespace(pool=pool, close=lambda: close("db"))
    handler = logging.NullHandler()
    logging.getLogger().addHandler(handler)
    daemon._db_log_handler = handler
    await asyncio.sleep(0)
    started = asyncio.get_running_loop().time()
    shutdown = asyncio.create_task(daemon.shutdown())
    try:
        if outcome in {"cancel", "admission_cancel"}:
            await interrupted.wait()
            cancellation_target = shutdown if outcome == "cancel" else daemon._catalog_shutdown_task
            cancellation_target.cancel()
            await asyncio.sleep(0)
            cancellation_target.cancel()
            await asyncio.sleep(0)
            assert not shutdown.done()
            assert runtime.admission._pending == original_pending
            release.set()
        if outcome.startswith("expired"):
            # Observe the retained lifetime without cancelling it on a failed
            # bound. Fixture finalization releases and joins every owned task.
            done, pending = await asyncio.wait({shutdown}, timeout=1)
            assert done == {shutdown} and not pending
            with pytest.raises(RuntimeError, match="private_git_admission_cleanup_incomplete"):
                shutdown.result()
            assert runtime.admission._pending == original_pending
            assert asyncio.get_running_loop().time() - started < 1
        elif outcome in {"cancel", "admission_cancel"}:
            with pytest.raises(asyncio.CancelledError):
                await shutdown
        else:
            await shutdown
        assert calls == ["module", "blob", "audit", "credentials", "db"]
        assert {"scheduler", "recovery", "liveness", "approval_delivery"} <= set(events)
        assert events.index("scheduler") < events.index("recovery")
        assert handler not in logging.getLogger().handlers
        retained = daemon._catalog_shutdown_task
        assert retained.done()
        repeated_error = (
            RuntimeError
            if outcome.startswith("expired")
            else asyncio.CancelledError
            if outcome == "admission_cancel"
            else None
        )
        with pytest.raises(repeated_error) if repeated_error else nullcontext():
            await daemon.shutdown()
        assert daemon._catalog_shutdown_task is retained
        assert calls == ["module", "blob", "audit", "credentials", "db"]
        if outcome.startswith("expired"):
            assert daemon._admission_cleanup_tasks
            assert "db" not in events
            assert daemon.db is not None
            if outcome == "expired_later_error":
                failures = retained.exception().__cause__
                assert isinstance(failures, BaseExceptionGroup)
                assert any(isinstance(exc, ConnectionError) for exc in failures.exceptions)
    finally:
        release.set()
        pool_release.set()
        logging.getLogger().removeHandler(handler)
        owned = set(runtime.admission._pending)
        owned.add(shutdown)
        if (lifetime := getattr(daemon, "_catalog_shutdown_task", None)) is not None:
            owned.add(lifetime)
        owned.update(getattr(daemon, "_admission_cleanup_tasks", ()))
        owned.update(
            task
            for attr in (
                "_scheduler_loop_task",
                "_route_inbox_recovery_task",
                "_liveness_reporter_task",
                "_approval_delivery_task",
            )
            if (task := getattr(daemon, attr, None)) is not None
        )
        if submitted_task is not None:
            owned.add(submitted_task)
        for task in owned:
            if not task.done():
                task.cancel()
        if owned:
            done, pending = await asyncio.wait(owned, timeout=1)
            assert not pending and done == owned
            await asyncio.gather(*done, return_exceptions=True)
        await runtime.close_admission()


async def test_full_git_discovery_verifies_selected_insight_source_and_removal(
    tmp_path, monkeypatch
):
    """Actual full-inventory consumer; SQL doubles are software-only evidence."""
    import json
    from uuid import uuid4

    from butlers.core.goal_admission import Grant, digest
    from butlers.core.obligation_git_sources import reconcile_loaded_git_sources
    from butlers.core.obligation_insight_sources import configured_insight_preparer
    from butlers.core.obligation_sources import DefinitionRevision, SourceConflict
    from butlers.core.obligations import ObligationDeclaration
    from butlers.tools.switchboard.insight.broker import _cluster_synthesis_prompt
    from tests.core.test_dark_obligation_producers import insight_source_fixture

    pool, raw = insight_source_fixture()
    monkeypatch.setenv("BUTLERS_INSIGHT_DARK_PREPARATION", "1")
    context = {
        "clusters": [
            {"ids": [str(c["id"]) for c in raw], "prompt": _cluster_synthesis_prompt(raw)}
        ],
        "settings": {"verbosity": "normal"},
        "effective_budget": 2,
        "selection_reference": NOW.replace(year=2026, month=1, day=15, hour=12),
        "urgent_only": False,
        "daily_hold_mode": True,
        "category_budget_reasons": {},
        "premise_verdicts": {},
        "prepared_action_statuses": {},
    }
    await configured_insight_preparer(pool, "insight_delivery_cycle")(raw, raw, context)
    config = load_config(_ROOT / "roster" / "switchboard")
    registry = await _complete_core_catalog("switchboard")
    snapshot = capture_loaded_git_sources(config, RuntimeConfig("switchboard"), registry)
    conn = pool.connection
    source = next(iter(conn.definitions))
    original = DefinitionRevision.decode(next(iter(conn.revisions.values())))
    await reconcile_loaded_git_sources(pool, snapshot)
    assert conn.definitions[source]["state"] == "active"
    assert DefinitionRevision.decode(conn.revisions[original.revision]) == original
    assert not any(row["id"] == source for row in conn.rows.values())
    baseline = copy.deepcopy(
        (conn.rows, conn.definitions, conn.revisions, conn.commands, conn.outbox, conn.occurrences)
    )
    for fault in (
        "ordinary_dangling",
        "unknown_subtype",
        "wrong_identity",
        "wrong_family",
        "wrong_declaration",
    ):
        # Plant a fully parseable, owning-repository-shaped wrong source. A
        # marker-only exclusion must not hide any of these inventory failures.
        body = original.encode()
        if fault == "ordinary_dangling":
            wrong = replace(original, source_id=uuid4(), family="schedule", payload_json=None)
        elif fault == "wrong_identity":
            wrong = replace(original, source_id=uuid4())
        elif fault == "wrong_family":
            wrong = replace(original, family="schedule")
        elif fault == "wrong_declaration":
            wrong = replace(
                original,
                declaration=ObligationDeclaration((Grant("switchboard__entity_graph_walk"),)),
            )
        else:
            body["payload"]["kind"] = "caller_says_insight"
            wrong = replace(
                original,
                material_digest=digest(body["payload"]),
                payload_json=json.dumps(body["payload"], sort_keys=True, separators=(",", ":")),
            )
        conn.definitions[wrong.source_id] = {
            "id": wrong.source_id,
            "owner_domain": wrong.owner_domain,
            "family": wrong.family,
            "origin": wrong.origin,
            "creator": wrong.encode()["creator"],
            "revision": wrong.revision,
            "generation": wrong.generation,
            "state": wrong.state,
        }
        conn.revisions[wrong.revision] = wrong.encode()
        with pytest.raises(SourceConflict):
            await reconcile_loaded_git_sources(pool, snapshot)
        (
            conn.rows,
            conn.definitions,
            conn.revisions,
            conn.commands,
            conn.outbox,
            conn.occurrences,
        ) = copy.deepcopy(baseline)
    # A real complete loaded config authorizes removal; no fake selective
    # snapshot or runtime creator is introduced to hide the extra model job.
    (tmp_path / "butler.toml").write_text(
        '[butler]\nname="switchboard"\nport=41100\ntype="staffer"\n'
    )
    removed = capture_loaded_git_sources(
        load_config(tmp_path), RuntimeConfig("switchboard"), registry
    )
    await reconcile_loaded_git_sources(pool, removed)
    assert conn.definitions[source]["state"] == "deleted"
    assert all(row["state"] == "cancelled" for row in conn.occurrences.values())
    assert await reconcile_loaded_git_sources(pool, removed) == ()
    with pytest.raises(SourceConflict):
        await configured_insight_preparer(pool, "insight_delivery_cycle")(raw, raw, context)
    assert conn.definitions[source]["state"] == "deleted"
