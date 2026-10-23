"""Actual cron/deadline dark-source consumers; software SQL, not PG proof.

REQ-goal-orchestration-007; REQ-capability-authorization-013.
No fixture grants catalog/creator authority or invokes a model/provider.
"""

import copy
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.core.test_obligation_schedule_sources import RecordingConnection, RecordingPool

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

NOW = datetime(2026, 6, 15, 12, tzinfo=UTC)


class OccurrenceConnection(RecordingConnection):
    """Rollback-aware SQL collaborator, never locking/durability evidence."""

    async def fetch(self, sql, *args):
        if "FROM obligation_definitions" in sql:
            return [
                {"id": key}
                for key, value in self.definitions.items()
                if value["origin"] == "git" and value["state"] != "deleted"
            ]
        if "information_schema.columns" in sql:
            return [{"column_name": name} for name in args[1]]
        if "scheduled_tasks" in sql and "FROM" in sql:
            if "source='toml'" in sql:
                return [copy.deepcopy(row) for row in self.rows.values() if row["source"] == "toml"]
            if "task_type = 'deadline'" in sql:
                return [
                    copy.deepcopy(row)
                    for row in self.rows.values()
                    if row["enabled"] and row["task_type"] == "deadline"
                ]
            if "next_run_at <= $1" in sql:
                return [
                    copy.deepcopy(row)
                    for row in self.rows.values()
                    if row["enabled"]
                    and row["task_type"] == "cron"
                    and row["next_run_at"] is not None
                    and row["next_run_at"] <= args[0]
                ]
        if "UPDATE obligation_occurrences" in sql:
            return await super().fetch(sql, *args)
        return []

    async def fetchval(self, sql, *args):
        if "information_schema.columns" in sql:
            return True
        if "information_schema.tables" in sql:
            return False
        return None

    async def fetchrow(self, sql, *args):
        if "INSERT INTO obligation_occurrences" in sql:
            self.calls.append(sql)
            for row in self.occurrences.values():
                if row["source_key"] == args[2]:
                    return None
            key = uuid4()
            self.occurrences[key] = dict(
                zip(
                    [
                        "source_id",
                        "revision",
                        "source_key",
                        "source_kind",
                        "source_ref",
                        "occurrence",
                    ],
                    args,
                    strict=True,
                )
            ) | {"id": key, "state": "pending", "claim_token": None}
            return {"id": key}
        if "FROM obligation_occurrences" in sql:
            self.calls.append(sql)
            if "source_key=" in sql:
                return next(
                    (
                        copy.deepcopy(row)
                        for row in self.occurrences.values()
                        if row["source_key"] == args[0]
                    ),
                    None,
                )
            row = self.occurrences.get(args[0])
            return copy.deepcopy(row) if row and row["source_id"] == args[1] else None
        if "scheduled_tasks" in sql and sql.lstrip().startswith("UPDATE"):
            self.calls.append(sql)
            row = self.rows[args[0]]
            if "IS NOT DISTINCT FROM" in sql and "next_run_at" in sql:
                if row["next_run_at"] != args[-1]:
                    return None
                row["next_run_at"] = args[1] if len(args) == 3 else None
                if len(args) == 2:
                    row["enabled"] = False
                return {"id": args[0]}
            if "enabled=false" in sql:
                if (row["target_date"], row["deadline_status"]) != args[2:]:
                    return None
                row.update(deadline_status=args[1], enabled=False)
                return {"id": args[0]}
            if "fired_thresholds" in sql:
                if (row["target_date"], row["fired_thresholds"], row["deadline_status"]) != args[
                    4:
                ]:
                    return None
                row.update(
                    deadline_status=args[1],
                    fired_thresholds=copy.deepcopy(args[2]),
                    last_run_at=args[3],
                )
                return {"id": args[0]}
        if "provider_allowance" in sql or "model_catalog" in sql:
            return None
        return await super().fetchrow(sql, *args)

    async def execute(self, sql, *args):
        if "SET state='claimed'" in sql:
            self.calls.append(sql)
            self.occurrences[args[0]].update(state="claimed", claim_token=args[1])
            return "UPDATE 1"
        if "INSERT INTO obligation_source_outbox" in sql and getattr(self, "fail_outbox", False):
            raise OSError("closed software fault")
        if "UPDATE scheduled_tasks" in sql and "last_result" in sql:
            return "UPDATE 1"
        if "UPDATE scheduled_tasks" in sql and "fired_thresholds" in sql:
            self.rows[args[0]].update(
                deadline_status=args[1],
                fired_thresholds=copy.deepcopy(args[2]),
                last_run_at=args[3],
            )
            return "UPDATE 1"
        return await super().execute(sql, *args)


async def runtime_fixture(tmp_path, *, deadline=False, target=None):
    from butlers.config import load_config
    from butlers.core.obligation_git_sources import (
        capture_loaded_git_sources,
        start_schedule_sources,
    )
    from butlers.core.runtime_config import RuntimeConfig
    from tests.config.test_obligation_declarations import _loaded_config
    from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog

    _loaded_config(tmp_path)
    path = tmp_path / "butler.toml"
    if deadline:
        text = path.read_text().replace(
            "[[butler.schedule]]",
            '[butler.scheduler]\nobligation_source_mode="dark"\n\n[[butler.schedule]]',
        )
        text = text.replace(
            "[butler.schedule.obligation]",
            'task_type="deadline"\ntarget_date="' + (target or NOW.date()).isoformat() + '"\n'
            'lead_time_days=7\nalert_thresholds=[{days_before=0,severity="info"}]\n'
            "[butler.schedule.obligation]",
        )
        path.write_text(text)
    else:
        path.write_text(
            path.read_text().replace(
                "[[butler.schedule]]",
                '[butler.scheduler]\nobligation_source_mode="dark"\n\n[[butler.schedule]]',
            )
        )
    config = load_config(tmp_path)
    snapshot = capture_loaded_git_sources(
        config, RuntimeConfig("health"), await _complete_core_catalog("health")
    )

    class ReleasePool(RecordingPool):
        fail_release = False
        after_release = None

        @asynccontextmanager
        async def acquire(self):
            yield self.connection
            if self.fail_release:
                raise OSError("closed release fault after software commit")
            if self.after_release is not None:
                callback, self.after_release = self.after_release, None
                callback()

    pool = ReleasePool()
    pool.connection = OccurrenceConnection()
    runtime = await start_schedule_sources(
        pool, snapshot, mode=config.scheduler.obligation_source_mode
    )
    (source,) = pool.connection.rows
    row = pool.connection.rows[source]
    row.update(
        next_run_at=NOW - timedelta(minutes=1),
        fired_thresholds=[],
        deadline_status="pending",
        depends_on=[],
    )
    return pool, runtime, source


async def test_real_tick_commits_nominal_occurrence_before_legacy_dispatch(tmp_path):
    from butlers.core.scheduler import tick

    pool, runtime, source = await runtime_fixture(tmp_path)
    # tick's actual query/legacy CAS and source SQL share this owning state.
    pool.fetch = pool.connection.fetch
    pool.fetchval = pool.connection.fetchval
    pool.fetchrow = pool.connection.fetchrow
    pool.execute = pool.connection.execute
    nominal = pool.connection.rows[source]["next_run_at"]

    async def dispatch(**kwargs):
        assert pool.connection.depth == 0
        assert len(pool.connection.occurrences) == 1
        occurrence = next(iter(pool.connection.occurrences.values()))
        assert occurrence["state"] == "claimed" and occurrence["claim_token"] is not None
        assert pool.connection.rows[source]["next_run_at"] != nominal
        assert kwargs == {
            "prompt": "Inspect the fixture",
            "trigger_source": "schedule:fixture",
            "complexity": "workhorse",
        }
        return {"status": "ok"}

    dispatched = AsyncMock(side_effect=dispatch)
    assert await tick(pool, dispatched, source_runtime=runtime) == 1
    assert await tick(pool, dispatched, source_runtime=runtime) == 0
    assert dispatched.await_count == 1 and len(pool.connection.occurrences) == 1
    assert any(args[3] == "occurrence_captured" for args in pool.connection.outbox)
    # Source slot is the selected nominal instant, never advanced next_run or wall clock.
    from butlers.core.obligation_sources import DefinitionRevision

    revision = DefinitionRevision.decode(
        pool.connection.revisions[pool.connection.definitions[source]["revision"]]
    )
    occurrence = next(iter(pool.connection.occurrences.values()))
    assert (
        occurrence["occurrence"]
        == revision.source.occurrence("cron:" + nominal.isoformat()).occurrence
    )


async def test_deadline_failed_dispatch_stays_unfired_then_commits_same_threshold(tmp_path):
    from butlers.core.scheduler import _tick_deadline_pass

    pool, runtime, source = await runtime_fixture(tmp_path, deadline=True)
    pool.fetch = pool.connection.fetch
    pool.fetchval = pool.connection.fetchval
    pool.execute = pool.connection.execute
    row = pool.connection.rows[source]
    failed = AsyncMock(side_effect=RuntimeError("closed dispatch fault"))
    assert await _tick_deadline_pass(pool, failed, now=NOW, source_runtime=runtime) == (1, 0)
    assert row["fired_thresholds"] == [] and pool.connection.occurrences == {}
    successful = AsyncMock(return_value={"status": "ok"})
    assert await _tick_deadline_pass(pool, successful, now=NOW, source_runtime=runtime) == (1, 1)
    assert row["fired_thresholds"] == row["alert_thresholds"]
    assert len(pool.connection.occurrences) == 1 and pool.connection.depth == 0
    assert await _tick_deadline_pass(pool, successful, now=NOW, source_runtime=runtime) == (1, 0)
    assert successful.await_count == 1
    # The same pass clock governs threshold evaluation and durable expiry, even
    # across midnight; these calls use the real temporal helpers without patches.
    assert await _tick_deadline_pass(
        pool, successful, now=NOW.replace(hour=23, minute=59), source_runtime=runtime
    ) == (1, 0)
    assert row["enabled"] and row["deadline_status"] != "expired"
    assert await _tick_deadline_pass(
        pool, successful, now=NOW.replace(hour=0) + timedelta(days=1), source_runtime=runtime
    ) == (1, 0)
    assert row["enabled"] is False and row["deadline_status"] == "expired"
    assert successful.await_count == 1


async def test_source_cas_failure_rolls_back_and_changed_material_never_claims(tmp_path):
    from butlers.core.obligation_admission import SourceUnavailable
    from butlers.core.obligation_sources import SourceConflict

    pool, runtime, source = await runtime_fixture(tmp_path)
    selected = copy.deepcopy(pool.connection.rows[source])
    before = copy.deepcopy(
        (pool.connection.rows, pool.connection.occurrences, pool.connection.outbox)
    )
    pool.connection.rows[source]["next_run_at"] += timedelta(days=1)
    assert (
        await runtime.claim_cron(
            pool, selected, next_run_at=selected["next_run_at"] + timedelta(days=1), disable=False
        )
        is None
    )
    pool.connection.rows[source]["next_run_at"] = selected["next_run_at"]
    assert pool.connection.occurrences == {}
    pool.connection.fail_outbox = True
    with pytest.raises(SourceUnavailable, match="source_transaction_unconfirmed"):
        await runtime.claim_cron(
            pool, selected, next_run_at=selected["next_run_at"] + timedelta(days=1), disable=False
        )
    assert (pool.connection.rows, pool.connection.occurrences, pool.connection.outbox) == before
    pool.connection.fail_outbox = False
    for field, value, reason in [
        ("role", "butler_other_rw", "owning_role_required"),
        ("schedule_shadow", True, "owning_schedule_relation_required"),
    ]:
        original = getattr(pool.connection, field)
        setattr(pool.connection, field, value)
        with pytest.raises(SourceConflict, match=reason):
            await runtime.claim_cron(
                pool,
                selected,
                next_run_at=selected["next_run_at"] + timedelta(days=1),
                disable=False,
            )
        setattr(pool.connection, field, original)
    pool.connection.rows[source]["prompt"] = "newer owning material"
    with pytest.raises(SourceConflict, match="schedule_material_changed"):
        await runtime.claim_cron(
            pool, selected, next_run_at=selected["next_run_at"] + timedelta(days=1), disable=False
        )
    assert pool.connection.rows[source]["next_run_at"] == selected["next_run_at"]
    assert pool.connection.occurrences == {}
    pool.connection.rows[source]["prompt"] = selected["prompt"]
    pool.fail_release = True
    with pytest.raises(SourceUnavailable, match="source_transaction_unconfirmed"):
        await runtime.claim_cron(
            pool, selected, next_run_at=selected["next_run_at"] + timedelta(days=1), disable=False
        )
    assert len(pool.connection.occurrences) == 1 and pool.connection.depth == 0
    # Commit/release uncertainty is an explicit non-success; do not invoke a second CAS.


async def test_off_factory_has_no_source_io_and_rejects_untyped_selector(tmp_path):
    from butlers.config import SchedulerConfig
    from butlers.core.obligation_git_sources import start_schedule_sources
    from butlers.core.obligation_sources import SourceConflict

    assert SchedulerConfig().obligation_source_mode == "off"
    poison = object()
    assert await start_schedule_sources(poison, None, mode="off") is None
    for mode in (True, "enabled", None):
        with pytest.raises(SourceConflict, match="invalid_source_mode"):
            await start_schedule_sources(poison, None, mode=mode)


async def test_deadline_expiry_cancels_prior_claimed_occurrence_in_same_transaction(tmp_path):
    from butlers.core.scheduler import _tick_deadline_pass

    pool, runtime, source = await runtime_fixture(
        tmp_path, deadline=True, target=NOW.date() - timedelta(days=1)
    )
    pool.fetch = pool.connection.fetch
    pool.fetchval = pool.connection.fetchval
    old = uuid4()
    revision = pool.connection.definitions[source]["revision"]
    pool.connection.occurrences[old] = {
        "id": old,
        "source_id": source,
        "revision": revision,
        "state": "claimed",
        "claim_token": uuid4(),
    }
    dispatch = AsyncMock()
    assert await _tick_deadline_pass(pool, dispatch, now=NOW, source_runtime=runtime) == (1, 0)
    dispatch.assert_not_awaited()
    assert pool.connection.rows[source]["enabled"] is False
    assert pool.connection.rows[source]["deadline_status"] == "expired"
    assert pool.connection.definitions[source]["state"] == "disabled"
    assert pool.connection.occurrences[old]["state"] == "cancelled"
    assert any(row[3] == "occurrence_cancelled" for row in pool.connection.outbox)
    assert pool.connection.depth == 0


async def test_actual_daemon_and_background_loop_forward_only_registered_runtime(
    tmp_path, monkeypatch
):
    import asyncio
    from types import SimpleNamespace

    from butlers import background
    from butlers import daemon as daemon_module
    from butlers.config import load_config
    from butlers.daemon import ButlerDaemon

    pool, runtime, _ = await runtime_fixture(tmp_path)
    daemon = ButlerDaemon(tmp_path)
    daemon.config = load_config(tmp_path)
    daemon.db = SimpleNamespace(pool=pool)
    daemon.spawner = object()
    daemon.obligation_schedule_runtime = runtime
    daemon._build_scheduler_runtime_context = AsyncMock(
        return_value=SimpleNamespace(prompt_hooks={}, completion_hooks={}, default_timezone="UTC")
    )
    calls = []

    async def observed_tick(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["source_runtime"] is runtime
        raise asyncio.CancelledError

    monkeypatch.setattr(daemon_module, "_tick", observed_tick)
    monkeypatch.setattr(background.asyncio, "sleep", AsyncMock())
    await daemon._scheduler_loop()
    assert len(calls) == 1

    # Manual MCP tick has no source/context argument for a model to assert;
    # the closure must use exactly the same lifecycle-owned runtime.
    from butlers.core_tools._base import ToolContext
    from butlers.core_tools._infra import register_infra_tools

    registered = {}

    def core_tool(group, **kwargs):
        def register(fn):
            registered[fn.__name__] = fn
            return fn

        return register

    async def manual_tick(*args, **kwargs):
        assert kwargs["source_runtime"] is runtime
        return 1

    monkeypatch.setattr("butlers.core_tools._infra._tick", manual_tick)
    context = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=None,
        butler_name="health",
        butler_type=None,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_infra_tools(context, SimpleNamespace(), core_tool)
    import inspect

    assert not inspect.signature(registered["tick"]).parameters
    assert await registered["tick"]() == {"dispatched": 1}


async def test_actual_startup_registers_after_sync_and_final_catalog(tmp_path, monkeypatch):
    from contextlib import ExitStack
    from unittest.mock import patch

    from butlers import lifecycle
    from butlers.config import load_config
    from butlers.core.obligation_git_sources import GitScheduleRuntime
    from butlers.core.runtime_config import RuntimeConfig
    from butlers.daemon import ButlerDaemon
    from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog
    from tests.daemon.test_daemon import _make_registry, _patch_infra

    pool, previous, source = await runtime_fixture(tmp_path)
    nominal = pool.connection.rows[source]["next_run_at"]
    config = load_config(tmp_path)
    registry = await _complete_core_catalog("health")
    order = []
    patches = _patch_infra()

    class Observed(Exception):
        pass

    async def finalized(self):
        order.append("finalized")
        self._tool_catalog = registry
        # Earlier startup infrastructure is isolated. Source reconciliation now
        # executes real repository SQL on the same pool argument/owning state.
        patches["mock_pool"].acquire = pool.acquire
        return registry

    def diagnosed(*args, **kwargs):
        assert order == ["sync", "finalized"]
        assert type(daemon.obligation_schedule_runtime) is GitScheduleRuntime
        assert daemon.obligation_schedule_runtime.bindings == previous.bindings
        assert pool.connection.rows[source]["next_run_at"] == nominal
        assert pool.connection.depth == 0
        raise Observed

    monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "1")
    with ExitStack() as stack:
        for name, value in patches.items():
            if not name.startswith("mock_"):
                stack.enter_context(value)
        stack.enter_context(patch.object(ButlerDaemon, "_finalize_tool_catalog", finalized))
        stack.enter_context(
            patch(
                "butlers.core.runtime_config.RuntimeConfigAccessor.seed_if_empty",
                new=AsyncMock(return_value=RuntimeConfig("health")),
            )
        )
        monkeypatch.setattr(lifecycle, "diagnose_loaded_schedules", diagnosed)
        lifecycle.sync_schedules.side_effect = lambda *args, **kwargs: order.append("sync")
        daemon = ButlerDaemon(tmp_path, registry=_make_registry())
        daemon.config = config
        with pytest.raises(Observed):
            await daemon.start()


@pytest.mark.parametrize("deadline", [False, True], ids=["cron", "deadline"])
async def test_committed_source_payload_is_read_back_from_registered_owning_material(
    tmp_path, deadline
):
    from butlers.core.goal_admission import Obligation

    pool, runtime, source = await runtime_fixture(tmp_path, deadline=deadline)
    selected = copy.deepcopy(pool.connection.rows[source])
    if deadline:
        threshold = selected["alert_thresholds"][0]
        submission = await runtime.record_deadline(
            pool,
            selected,
            threshold=threshold,
            status="alerted",
            fired=[threshold],
            run_at=NOW,
        )
    else:
        claim = await runtime.claim_cron(
            pool,
            selected,
            next_run_at=selected["next_run_at"] + timedelta(days=1),
            disable=False,
        )
        submission = claim.submission
    # This is the real source consumer's returned payload after software commit,
    # not Evidence, a catalog publication, or an authenticated control receipt.
    assert pool.connection.depth == 0
    assert submission.obligation == Obligation(
        "health",
        submission.definition.revision,
        submission.definition.declaration.capabilities,
        created_exposed=False,
    )
    assert (
        submission.claim_token
        == pool.connection.occurrences[submission.occurrence_id]["claim_token"]
    )
    readback = await runtime.read_committed_submission(pool, submission)
    assert readback == submission and pool.connection.depth == 0
    assert (
        submission.source.key == pool.connection.occurrences[submission.occurrence_id]["source_key"]
    )


async def test_source_payload_readback_refuses_forgery_stale_claim_and_unconfirmed_release(
    tmp_path,
):
    from dataclasses import replace

    from butlers.core.obligation_admission import SourceUnavailable
    from butlers.core.obligation_sources import SourceConflict

    pool, runtime, source = await runtime_fixture(tmp_path)
    selected = copy.deepcopy(pool.connection.rows[source])
    claim = await runtime.claim_cron(
        pool, selected, next_run_at=selected["next_run_at"] + timedelta(days=1), disable=False
    )
    submission = claim.submission
    # A consumer must reread the owning state, not trust an otherwise valid
    # packet or promote a historical command result to current authority.
    for forged in (
        replace(submission, claim_token=uuid4()),
        replace(submission, obligation=replace(submission.obligation, created_exposed=True)),
        replace(submission, source=replace(submission.source, occurrence="0" * 64)),
    ):
        with pytest.raises(SourceConflict):
            await runtime.read_committed_submission(pool, forged)
    row = pool.connection.occurrences[submission.occurrence_id]
    baseline = copy.deepcopy(row)
    for fields in (
        {"state": "cancelled"},
        {"claim_token": uuid4()},
        {"source_ref": "another:source"},
        {"source_key": "0" * 64},
    ):
        row.update(fields)
        with pytest.raises(SourceConflict):
            await runtime.read_committed_submission(pool, submission)
        pool.connection.occurrences[submission.occurrence_id] = copy.deepcopy(baseline)
        row = pool.connection.occurrences[submission.occurrence_id]
    pool.connection.rows[source]["prompt"] = "changed actual owning prompt"
    with pytest.raises(SourceConflict, match="schedule_material_changed"):
        await runtime.read_committed_submission(pool, submission)
    pool.connection.rows[source]["prompt"] = selected["prompt"]
    pool.connection.role = "butler_other_rw"
    with pytest.raises(SourceConflict, match="owning_role_required"):
        await runtime.read_committed_submission(pool, submission)
    pool.connection.role = "butler_health_rw"
    async with pool.connection.transaction():
        with pytest.raises(SourceConflict, match="fresh_source_transaction_required"):
            await runtime.read_committed_submission(pool, submission)
    pool.fail_release = True
    with pytest.raises(SourceUnavailable, match="source_transaction_unconfirmed"):
        await runtime.read_committed_submission(pool, submission)
    assert pool.connection.depth == 0


async def test_actual_source_consumer_preserves_dispatch_when_later_readback_refuses(tmp_path):
    from butlers.core.obligation_sources import SourceConflict
    from butlers.core.scheduler import tick

    pool, runtime, source = await runtime_fixture(tmp_path)
    selected = copy.deepcopy(pool.connection.rows[source])
    pool.fetch = pool.connection.fetch
    pool.fetchval = pool.connection.fetchval
    pool.fetchrow = pool.connection.fetchrow
    pool.execute = pool.connection.execute
    captured = []

    async def legacy_dispatch(**kwargs):
        assert pool.connection.depth == 0
        assert kwargs["prompt"] == selected["prompt"]
        assert kwargs["trigger_source"] == "schedule:fixture"
        return {"status": "ok"}

    dispatch = AsyncMock(side_effect=legacy_dispatch)

    def invalidate_committed_claim():
        occurrence = next(iter(pool.connection.occurrences.values()))
        captured.append(copy.deepcopy(occurrence))
        occurrence["state"] = "cancelled"

    pool.after_release = invalidate_committed_claim
    assert await tick(pool, dispatch, source_runtime=runtime) == 1
    assert dispatch.await_count == 1
    # A later state change cannot retroactively suppress the committed legacy
    # CAS. It *does* prevent using its former payload as a current handoff.
    from butlers.core.goal_admission import Obligation, SourceRef
    from butlers.core.obligation_admission import DarkSubmission

    definition = runtime.bindings[0][0]
    row = captured[0]
    submitted = DarkSubmission(
        row["id"],
        SourceRef(row["source_kind"], row["source_ref"], row["occurrence"]),
        definition,
        row["claim_token"],
        Obligation(
            "health",
            definition.revision,
            definition.declaration.capabilities,
            created_exposed=False,
        ),
    )
    with pytest.raises(SourceConflict, match="source_not_current"):
        await runtime.read_committed_submission(pool, submitted)
    assert pool.connection.rows[source]["next_run_at"] != selected["next_run_at"]
    assert len(pool.connection.occurrences) == 1 and pool.connection.depth == 0


@pytest.mark.parametrize("job_name", ["insight_delivery_cycle", "insight_urgent_subcycle"])
async def test_actual_insight_job_registration_stages_nominal_model_obligation(
    job_name, monkeypatch
):
    """Real roster/handler/typed source/CAS; collaborators do not prove PG authority."""

    from butlers.background import _deterministic_jobs_for_butler
    from butlers.config import load_config
    from butlers.core.obligation_git_sources import (
        capture_loaded_git_sources,
        start_schedule_sources,
    )
    from butlers.core.runtime_config import RuntimeConfig
    from butlers.core.scheduler import tick
    from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog

    config = load_config(_REPO_ROOT / "roster" / "switchboard")
    snapshot = capture_loaded_git_sources(
        config, RuntimeConfig("switchboard"), await _complete_core_catalog("switchboard")
    )
    material = next(item for item in snapshot.definitions if item.schedule["job_name"] == job_name)
    assert material.available, material.unavailable_reason
    assert material.job_handler is _deterministic_jobs_for_butler("switchboard")[job_name]
    assert {g.capability for g in material.declaration.capabilities} == {"switchboard__notify"}

    class SwitchboardConnection(OccurrenceConnection):
        def __init__(self):
            super().__init__()
            self.role = "butler_switchboard_rw"

        async def fetchrow(self, sql, *args):
            if "current_schema()" in sql:
                return {"schema": "switchboard", "role": self.role, "shadowed": self.shadow}
            return await super().fetchrow(sql, *args)

    pool = RecordingPool()
    pool.connection = SwitchboardConnection()
    runtime = await start_schedule_sources(pool, snapshot, mode="dark")
    source = next(key for key, row in pool.connection.rows.items() if row["job_name"] == job_name)
    row = pool.connection.rows[source]
    nominal = NOW - timedelta(minutes=1)
    row.update(next_run_at=nominal, fired_thresholds=[], deadline_status="pending", depends_on=[])
    pool.fetch = pool.connection.fetch
    pool.fetchval = pool.connection.fetchval
    pool.fetchrow = pool.connection.fetchrow
    pool.execute = pool.connection.execute
    dispatch = AsyncMock(return_value={"status": "unchanged legacy delivery"})
    assert await tick(pool, dispatch, source_runtime=runtime) == 1
    dispatch.assert_awaited_once_with(
        job_name=job_name, job_args=None, trigger_source="schedule:" + row["name"]
    )
    assert pool.connection.depth == 0 and len(pool.connection.occurrences) == 1
    occurrence = next(iter(pool.connection.occurrences.values()))
    from butlers.core.obligation_admission import prepare_dark_submission, run_source_transaction

    async def read(repository):
        return await prepare_dark_submission(
            repository, source, occurrence["id"], occurrence["claim_token"]
        )

    payload = await run_source_transaction(pool, "switchboard", read, require_fresh=True)
    assert payload.definition.family == "model_job"
    assert payload.obligation.created_exposed is True  # candidate lineage is not a trusted stamp
    assert payload.source == payload.definition.source.occurrence("cron:" + nominal.isoformat())
    assert await runtime.read_committed_submission(pool, payload) == payload
    assert await tick(pool, dispatch, source_runtime=runtime) == 0
    assert dispatch.await_count == 1

    # A copied registry name never blesses a different callback. Preserve the
    # existing job dispatcher but refuse stale source consumption independently.
    from butlers.core.obligation_sources import SourceConflict

    monkeypatch.setitem(_deterministic_jobs_for_butler("switchboard"), job_name, AsyncMock())
    assert not runtime.supports(row)
    with pytest.raises(SourceConflict, match="model_job_registration_changed"):
        await runtime.read_committed_submission(pool, payload)
