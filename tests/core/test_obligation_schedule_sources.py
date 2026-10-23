"""Dormant schedule writer software controls, NOT PostgreSQL/authority proof.

REQ-goal-orchestration-007; REQ-capability-authorization-013.
The context fixture below is supplied test material, not a verified producer.
"""

import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from butlers.core.goal_admission import Grant
from butlers.core.obligation_admission import SourceUnavailable, run_source_transaction
from butlers.core.obligation_sources import RuntimeCreationSnapshot, SourceConflict
from butlers.core.obligations import DeclarationError, ObligationDeclaration

pytestmark = pytest.mark.unit


def schedule(**changes):
    """Complete already-normalized row material; no implicit source defaults."""
    value = {
        "name": "fixture",
        "cron": "0 9 * * *",
        "dispatch_mode": "prompt",
        "prompt": "fixture prompt",
        "job_name": None,
        "job_args": None,
        "complexity": "workhorse",
        "enabled": True,
        "timezone": "UTC",
        "continuity": False,
        "max_token_budget": None,
        "task_type": "cron",
        "target_date": None,
        "lead_time_days": None,
        "alert_thresholds": None,
        "start_at": None,
        "end_at": None,
        "until_at": None,
        "display_title": None,
        "description": None,
        "location": None,
        "calendar_event_id": None,
    }
    return value | changes


def definition(**changes):
    from butlers.core.obligation_schedule_sources import compile_git_schedule_definition

    return compile_git_schedule_definition(
        owner_domain="health",
        normalized_schedule=schedule(**changes),
        declaration=ObligationDeclaration((Grant("health__read"),)),
        context_material={"fixture_only": ["actual producer remains unregistered"]},
    )


class RecordingConnection:
    """Execute actual adapter/repository SQL against isolated recording state.

    Transactions emulate rollback ONLY for software flow controls. They cannot
    establish PostgreSQL locking, role enforcement or durability.
    """

    def __init__(self):
        self.rows, self.definitions, self.revisions, self.commands = {}, {}, {}, {}
        self.outbox, self.occurrences, self.calls = [], {}, []
        self.depth, self.role, self.shadow = 0, "butler_health_rw", False
        self.schedule_shadow = False

    def is_in_transaction(self):
        return self.depth > 0

    @asynccontextmanager
    async def transaction(self):
        state = copy.deepcopy(
            (
                self.rows,
                self.definitions,
                self.revisions,
                self.commands,
                self.outbox,
                self.occurrences,
            )
        )
        self.depth += 1
        try:
            yield
        except BaseException:
            (
                self.rows,
                self.definitions,
                self.revisions,
                self.commands,
                self.outbox,
                self.occurrences,
            ) = state
            raise
        finally:
            self.depth -= 1

    async def fetchrow(self, sql, *args):
        self.calls.append(sql)
        if "current_schema()" in sql:
            return {"schema": "health", "role": self.role, "shadowed": self.shadow}
        if "AS scheduled_relation" in sql:
            return {"scheduled_relation": not self.schedule_shadow}
        if "FROM obligation_source_commands" in sql:
            return self.commands.get(args[0])
        if "FROM obligation_definitions" in sql:
            value = self.definitions.get(args[0])
            if value is None:
                return None
            return value | {
                "revision_generation": value["generation"],
                "body": self.revisions[value["revision"]],
            }
        if "scheduled_tasks" in sql:
            if sql.lstrip().startswith("INSERT"):
                from butlers.core.obligation_schedule_sources import SCHEDULE_FIELDS

                row = dict(zip(SCHEDULE_FIELDS, args, strict=True))
                row |= {"id": uuid4(), "source": "toml", "next_run_at": None}
                self.rows[row["id"]] = row
                return copy.deepcopy(row)
            if sql.lstrip().startswith("UPDATE"):
                from butlers.core.obligation_schedule_sources import SCHEDULE_FIELDS

                row = self.rows[args[0]]
                row.update(zip(SCHEDULE_FIELDS, args[1:], strict=True))
                return copy.deepcopy(row)
            return copy.deepcopy(self.rows.get(args[0]))
        raise AssertionError("unhandled software SQL category")

    async def execute(self, sql, *args):
        self.calls.append(sql)
        if "pg_advisory_xact_lock" in sql:
            return "SELECT 1"
        if "INSERT INTO obligation_definitions" in sql:
            self.definitions[args[0]] = dict(
                zip(
                    [
                        "id",
                        "owner_domain",
                        "family",
                        "origin",
                        "creator",
                        "revision",
                        "generation",
                        "state",
                    ],
                    args,
                    strict=True,
                )
            )
        elif "UPDATE obligation_definitions" in sql:
            self.definitions[args[0]].update(revision=args[1], generation=args[2], state=args[3])
        elif "INSERT INTO obligation_revisions" in sql:
            self.revisions[args[1]] = copy.deepcopy(args[3])
        elif "INSERT INTO obligation_source_commands" in sql:
            self.commands[args[0]] = {"request_digest": args[2], "body": copy.deepcopy(args[3])}
        elif "INSERT INTO obligation_source_outbox" in sql:
            self.outbox.append(args)
        else:
            raise AssertionError("unhandled software SQL category")

    async def fetch(self, sql, *args):
        assert "UPDATE obligation_occurrences" in sql
        cancelled = []
        for key, row in self.occurrences.items():
            if row["source_id"] == args[0] and row["state"] in {"pending", "claimed"}:
                row["state"] = "cancelled"
                cancelled.append({"id": key, "revision": row["revision"]})
        return cancelled


class RecordingPool:
    def __init__(self):
        self.connection = RecordingConnection()

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


@pytest.mark.asyncio
async def test_typed_sync_prepared_sources_bind_input_replay_and_atomic_batch(tmp_path):
    from butlers.config import ScheduleSpec
    from butlers.core import scheduler
    from butlers.core.obligation_schedule_sources import GitScheduleSyncCommand
    from tests.config.test_obligation_declarations import _loaded_config

    spec = _loaded_config(tmp_path).schedules[0]
    # This fixture supplies context, NOT a production policy/Git verifier.
    prepared = replace(
        definition(name=spec.name, cron=spec.cron, prompt=spec.prompt),
        declaration=spec.obligation.declaration,
    )
    command = GitScheduleSyncCommand(uuid4(), prepared)
    pool = RecordingPool()
    (first,) = await scheduler.sync_schedules(pool, [spec], prepared_git_sources=(command,))
    source = first.definition.source_id
    assert pool.connection.rows[source]["prompt"] == spec.prompt
    assert pool.connection.rows[source]["next_run_at"] is None
    assert first.definition.declaration is spec.obligation.declaration
    assert len(pool.connection.outbox) == 1 and pool.connection.depth == 0

    # Bind submitted typed material BEFORE a historical command can return.
    before = copy.deepcopy(pool.connection.calls)
    for changed in (
        spec.model_copy(update={"prompt": "contradictory submitted prompt"}),
        spec.model_copy(
            update={
                "obligation": ScheduleSpec(
                    name=spec.name, cron=spec.cron, prompt=spec.prompt
                ).obligation
            }
        ),
    ):
        with pytest.raises(SourceConflict, match="git_sync_material_mismatch"):
            await scheduler.sync_schedules(pool, [changed], prepared_git_sources=(command,))
    assert pool.connection.calls == before
    with pytest.raises(SourceConflict, match="git_sync_set_mismatch"):
        await scheduler.sync_schedules(pool, [], prepared_git_sources=(command,))
    assert pool.connection.calls == before

    slot = datetime(2026, 1, 2, tzinfo=UTC)
    pool.connection.rows[source]["next_run_at"] = slot
    revised_spec = spec.model_copy(update={"prompt": "revised prompt"})
    revised = replace(
        prepared,
        row_json=definition(name=spec.name, cron=spec.cron, prompt="revised prompt").row_json,
    )
    revision = GitScheduleSyncCommand(uuid4(), revised, source, first.definition.revision)
    (current,) = await scheduler.sync_schedules(
        pool, [revised_spec], prepared_git_sources=(revision,)
    )
    (replay,) = await scheduler.sync_schedules(pool, [spec], prepared_git_sources=(command,))
    assert replay == first and current.definition.generation == 2
    assert pool.connection.rows[source]["prompt"] == "revised prompt"
    assert pool.connection.rows[source]["next_run_at"] == slot

    missing = ScheduleSpec(name=spec.name, cron=spec.cron, prompt="revised prompt")
    without_declaration = replace(revised, declaration=None)
    pool.connection.occurrences[UUID(int=999)] = {
        "source_id": source,
        "revision": current.definition.revision,
        "state": "claimed",
    }
    (disabled,) = await scheduler.sync_schedules(
        pool,
        [missing],
        prepared_git_sources=(
            GitScheduleSyncCommand(
                uuid4(), without_declaration, source, current.definition.revision
            ),
        ),
    )
    assert disabled.definition.state == "disabled"
    assert pool.connection.occurrences[UUID(int=999)]["state"] == "cancelled"
    assert pool.connection.rows[source]["next_run_at"] == slot

    # A real second repository refusal rolls back the first row/revision too.
    other_spec = spec.model_copy(update={"name": "second"})
    other = replace(
        prepared,
        row_json=definition(name="second", cron=spec.cron, prompt=spec.prompt).row_json,
        context_json=None,
    )
    failed = RecordingPool()
    with pytest.raises(SourceConflict, match="source_definition_unavailable"):
        await scheduler.sync_schedules(
            failed,
            [spec, other_spec],
            prepared_git_sources=(command, GitScheduleSyncCommand(uuid4(), other)),
        )
    assert not failed.connection.rows and not failed.connection.definitions
    assert not failed.connection.commands and not failed.connection.outbox
    assert failed.connection.depth == 0


def test_material_is_closed_immutable_complete_and_missing_context_unavailable():
    from butlers.core.obligation_schedule_sources import compile_git_schedule_definition

    raw = schedule()
    context = {"fixture_only": ["before"]}
    declaration = ObligationDeclaration((Grant("health__read"),))
    value = compile_git_schedule_definition(
        owner_domain="health",
        normalized_schedule=raw,
        declaration=declaration,
        context_material=context,
    )
    original = value.material_digest
    raw["prompt"], context["fixture_only"][0] = "changed", "changed"
    assert value.material_digest == original
    assert value.schedule["prompt"] == "fixture prompt"
    variants = [
        compile_git_schedule_definition(
            owner_domain="health",
            normalized_schedule=schedule(**fields),
            declaration=declaration,
            context_material={"fixture_only": ["before"]},
        ).material_digest
        for fields in (
            {"prompt": "new prompt"},
            {"timezone": "Asia/Singapore"},
            {"continuity": True},
        )
    ]
    assert len({original, *variants}) == 4
    missing = compile_git_schedule_definition(
        owner_domain="health",
        normalized_schedule=schedule(),
        declaration=declaration,
        context_material=None,
    )
    assert not missing.available and missing.unavailable_reason == "context_unavailable"
    for fields in (
        raw | {"creator": "forged"},
        raw | {"enabled": 1},
        raw | {"cron": "invalid"},
        raw | {"job_args": float("nan")},
    ):
        with pytest.raises(DeclarationError):
            compile_git_schedule_definition(
                owner_domain="health",
                normalized_schedule=fields,
                declaration=declaration,
                context_material={"fixture_only": ["before"]},
            )


@pytest.mark.asyncio
async def test_actual_adapter_sql_replay_revision_cancel_and_rollback_are_one_boundary():
    from butlers.core.obligation_schedule_sources import stage_git_schedule_definition

    pool, command = RecordingPool(), uuid4()
    value = definition()

    async def write(repo, item=value, key=command, expected=None, source_id=None):
        return await stage_git_schedule_definition(
            repo,
            command_id=key,
            expected_revision=expected,
            definition=item,
            source_id=source_id,
        )

    first = await run_source_transaction(pool, "health", write)
    conn = pool.connection
    assert first.definition.generation == 1 and first.definition.origin == "git"
    assert first.definition.source_id in conn.rows and len(conn.outbox) == 1
    assert await run_source_transaction(pool, "health", write) == first
    assert len(conn.rows) == len(conn.outbox) == 1
    # Real production nominal slot is preserved on edits; it is not a new clock.
    source_id = first.definition.source_id
    nominal = datetime(2026, 1, 1, tzinfo=UTC)
    conn.rows[source_id]["next_run_at"] = nominal
    for state in ("pending", "claimed"):
        conn.occurrences[uuid4()] = {
            "source_id": source_id,
            "revision": first.definition.revision,
            "state": state,
        }
    second = await run_source_transaction(
        pool,
        "health",
        lambda r: write(
            r, definition(prompt="changed"), uuid4(), first.definition.revision, source_id
        ),
    )
    assert second.definition.generation == 2
    assert conn.rows[source_id]["next_run_at"] == nominal
    assert all(row["state"] == "cancelled" for row in conn.occurrences.values())
    # Context/declaration-only edits revise even when every legacy row field is
    # identical. Neither test fixture is authenticated production authority.
    unchanged_row = copy.deepcopy(conn.rows[source_id])
    for item in (
        replace(definition(prompt="changed"), context_json='{"fixture_only":["changed"]}'),
        replace(
            definition(prompt="changed"),
            declaration=ObligationDeclaration((Grant("health__other"),)),
        ),
    ):
        old = second
        second = await run_source_transaction(
            pool,
            "health",
            lambda r, item=item, old=old: write(
                r, item, uuid4(), old.definition.revision, source_id
            ),
        )
        assert second.definition.generation == old.definition.generation + 1
        assert conn.rows[source_id] == unchanged_row
    # Late replay returns its original receipt without reverting today's row.
    assert await run_source_transaction(pool, "health", write) == first
    assert conn.rows[source_id]["prompt"] == "changed"
    before = copy.deepcopy((conn.rows, conn.definitions, conn.outbox))

    async def fail(repo):
        await write(
            repo, definition(prompt="rollback"), uuid4(), second.definition.revision, source_id
        )
        raise RuntimeError("fixture-after-staged-write")

    with pytest.raises(SourceUnavailable):
        await run_source_transaction(pool, "health", fail)
    assert (conn.rows, conn.definitions, conn.outbox) == before

    async def caught_refusal(repo):
        async def refuse_after_row_write(*args, **kwargs):
            raise SourceConflict("fixture-after-row-write")

        repo.revise = refuse_after_row_write
        with pytest.raises(SourceConflict):
            await write(
                repo, definition(prompt="savepoint"), uuid4(), second.definition.revision, source_id
            )

    await run_source_transaction(pool, "health", caught_refusal)
    assert (conn.rows, conn.definitions, conn.outbox) == before


@pytest.mark.asyncio
async def test_noop_disable_and_source_authority_refusals_preserve_actual_rows():
    from butlers.core.obligation_schedule_sources import stage_git_schedule_definition

    pool = RecordingPool()

    async def write(repo, value, source_id=None, expected=None, command=None):
        return await stage_git_schedule_definition(
            repo,
            command_id=command or uuid4(),
            expected_revision=expected,
            source_id=source_id,
            definition=value,
        )

    first = await run_source_transaction(pool, "health", lambda r: write(r, definition()))
    source_id = first.definition.source_id
    same = await run_source_transaction(
        pool, "health", lambda r: write(r, definition(), source_id, first.definition.revision)
    )
    assert same.definition == first.definition and len(pool.connection.outbox) == 1
    # No raw new authority is inferred when a formerly valid declaration vanishes.
    invalid = replace(definition(), declaration=None)
    stopped = await run_source_transaction(
        pool, "health", lambda r: write(r, invalid, source_id, first.definition.revision)
    )
    assert stopped.definition.state == "disabled"
    with pytest.raises(SourceConflict, match="source_definition_unavailable"):
        await run_source_transaction(pool, "health", lambda r: write(r, invalid))
    no_context = replace(definition(), context_json=None)
    stopped = await run_source_transaction(
        pool, "health", lambda r: write(r, no_context, source_id, stopped.definition.revision)
    )
    assert (
        stopped.definition.state == "disabled"
        and no_context.unavailable_reason == "context_unavailable"
    )
    job = definition(dispatch_mode="job", prompt=None, job_name="fixture_job", job_args={})
    stopped = await run_source_transaction(
        pool, "health", lambda r: write(r, job, source_id, stopped.definition.revision)
    )
    assert stopped.definition.state == "disabled"
    with pytest.raises(SourceConflict, match="source_definition_unavailable"):
        await run_source_transaction(pool, "health", lambda r: write(r, job))
    for mutation in ("runtime", "deleted", "row-source"):
        conn = pool.connection
        old = copy.deepcopy((conn.definitions, conn.revisions, conn.rows))
        stored = conn.revisions[stopped.definition.revision]
        if mutation == "runtime":
            creator = RuntimeCreationSnapshot(UUID(int=1), (Grant("health__read"),), True)
            changed = replace(stopped.definition, origin="runtime", creator=creator)
        elif mutation == "deleted":
            changed = replace(stopped.definition, state="deleted")
        else:
            changed = stopped.definition
            conn.rows[source_id]["source"] = "db"
        conn.definitions[source_id].update(
            revision=changed.revision,
            origin=changed.origin,
            creator=changed.encode()["creator"],
            state=changed.state,
        )
        conn.revisions[changed.revision] = changed.encode()
        reason = {
            "runtime": "creation_binding_immutable",
            "deleted": "source_revision_conflict",
            "row-source": "git_schedule_required",
        }[mutation]
        with pytest.raises(SourceConflict, match=reason):
            await run_source_transaction(
                pool, "health", lambda r: write(r, definition(), source_id, changed.revision)
            )
        conn.definitions, conn.revisions, conn.rows = old
        assert stored


@pytest.mark.asyncio
async def test_role_shadow_wrong_command_and_stale_generation_refuse_before_effect():
    from butlers.core.obligation_schedule_sources import stage_git_schedule_definition

    pool, command = RecordingPool(), uuid4()

    async def write(repo, item=None, expected=None):
        return await stage_git_schedule_definition(
            repo,
            command_id=command,
            expected_revision=expected,
            definition=item or definition(),
        )

    for field, value in (
        ("role", "butler_general_rw"),
        ("shadow", True),
        ("schedule_shadow", True),
    ):
        conn = pool.connection
        old = getattr(conn, field)
        setattr(conn, field, value)
        with pytest.raises(SourceConflict):
            await run_source_transaction(pool, "health", write)
        assert not conn.rows and not conn.outbox
        setattr(conn, field, old)
    first = await run_source_transaction(pool, "health", write)
    with pytest.raises(SourceConflict, match="command_binding_conflict"):
        await run_source_transaction(
            pool, "health", lambda r: write(r, definition(prompt="rebound"))
        )
    with pytest.raises(SourceConflict):
        await run_source_transaction(pool, "general", write)
    with pytest.raises(SourceConflict, match="source_revision_conflict"):
        await run_source_transaction(
            pool,
            "health",
            lambda r: stage_git_schedule_definition(
                r,
                command_id=uuid4(),
                source_id=first.definition.source_id,
                expected_revision="a" * 64,
                definition=definition(prompt="stale attempt"),
            ),
        )
    assert len(pool.connection.rows) == len(pool.connection.outbox) == 1
    assert first.definition.generation == 1


async def test_actual_typed_sync_consumes_same_carrier_without_new_io(tmp_path, monkeypatch):
    from pathlib import Path

    from butlers.config import load_config
    from butlers.core import scheduler
    from tests.config.test_obligation_declarations import _loaded_config
    from tests.core.test_sync_schedules_timezone_and_quiet_lint import _FakePool

    config = _loaded_config(tmp_path)
    declared = config.schedules[0]
    plain = type(declared)(name=declared.name, cron=declared.cron, prompt=declared.prompt)
    education = load_config(Path(__file__).resolve().parents[2] / "roster/education")
    education_plain = [
        type(spec).model_validate(spec.model_dump(exclude={"obligation"}))
        for spec in education.schedules
    ]
    rosters = {
        domain: load_config(Path(__file__).resolve().parents[2] / "roster" / domain)
        for domain in ("chronicler", "general", "health", "lifestyle", "travel")
    }
    plain_rosters = {
        domain: [
            type(spec).model_validate(spec.model_dump(exclude={"obligation"}))
            for spec in config.schedules
        ]
        for domain, config in rosters.items()
    }
    projections = []
    actual = scheduler.project_loaded_schedule
    from butlers.core import obligations

    monkeypatch.setattr(
        obligations, "parse_declaration", lambda _: pytest.fail("sync must not reparse config")
    )

    def projected(spec):
        value = actual(spec)
        assert value.declaration is spec.obligation
        projections.append(value)
        return value

    class Pool(_FakePool):
        async def fetchval(self, sql, *args):
            assert "obligation" not in sql.lower()
            value = await super().fetchval(sql, *args)
            return value if "information_schema" in sql else UUID(int=1)

        async def fetch(self, sql, *args):
            assert "obligation" not in sql.lower()
            return await super().fetch(sql, *args)

        async def execute(self, sql, *args):
            assert "obligation" not in sql.lower()
            return await super().execute(sql, *args)

    monkeypatch.setattr(scheduler, "project_loaded_schedule", projected)
    monkeypatch.setattr(scheduler, "_next_run", lambda *a, **k: datetime(2026, 1, 1, tzinfo=UTC))
    a, b = Pool(), Pool()
    assert await scheduler.sync_schedules(a, [plain], stagger_key="health") is None
    assert await scheduler.sync_schedules(b, [declared], stagger_key="health") is None
    assert a.inserted == b.inserted and a.executed == b.executed
    assert [p.declaration.status for p in projections] == ["missing", "valid"]
    # The same production projection has a real declaration change, without
    # falsely promoting either value to row UUID/context/catalog authority.
    assert projections[0].digest != projections[1].digest
    assert projections[1].declaration.declaration is declared.obligation.declaration

    # Real roster input reaches the same sync consumer. Removing only the new
    # declarations must leave every old scheduled-row SQL value unchanged,
    # including deterministic jobs, timezone, delivery prompts and cron slots.
    before_education = len(projections)
    c, d = Pool(), Pool()
    assert await scheduler.sync_schedules(c, education_plain, stagger_key="education") is None
    assert await scheduler.sync_schedules(d, education.schedules, stagger_key="education") is None
    assert c.inserted == d.inserted and c.executed == d.executed
    normalized = projections[before_education:]
    assert len(normalized) == 2 * len(education.schedules)
    for original, candidate in zip(
        normalized[: len(education.schedules)], normalized[len(education.schedules) :], strict=True
    ):
        assert original.fields == candidate.fields
        if candidate["dispatch_mode"] == "prompt":
            assert original.declaration.status == "missing"
            assert candidate.declaration.status == "valid"
            assert original.digest != candidate.digest
        else:
            assert original.digest == candidate.digest

    # The five actual roster inputs use the same production sync path. Keep
    # every old SQL field and job row, and consume the exact parsed object.
    class ObservedPool(Pool):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def fetchval(self, sql, *args):
            self.calls.append(("fetchval", sql, args))
            return await super().fetchval(sql, *args)

        async def fetch(self, sql, *args):
            self.calls.append(("fetch", sql, args))
            return await super().fetch(sql, *args)

        async def execute(self, sql, *args):
            self.calls.append(("execute", sql, args))
            return await super().execute(sql, *args)

    for domain, config in rosters.items():
        before_roster = len(projections)
        original_pool, declared_pool = ObservedPool(), ObservedPool()
        assert (
            await scheduler.sync_schedules(original_pool, plain_rosters[domain], stagger_key=domain)
            is None
        )
        assert (
            await scheduler.sync_schedules(declared_pool, config.schedules, stagger_key=domain)
            is None
        )
        assert original_pool.inserted == declared_pool.inserted
        assert original_pool.executed == declared_pool.executed
        assert original_pool.calls
        assert original_pool.calls == declared_pool.calls
        normalized = projections[before_roster:]
        count = len(config.schedules)
        assert len(normalized) == 2 * count
        for spec, original, candidate in zip(
            config.schedules, normalized[:count], normalized[count:], strict=True
        ):
            assert original.fields == candidate.fields
            assert candidate.declaration is spec.obligation
            if spec.obligation.status == "valid":
                assert original.declaration.status == "missing"
                assert original.digest != candidate.digest
            else:
                assert original.digest == candidate.digest


class RuntimeConnection(RecordingConnection):
    async def execute(self, sql, *args):
        if sql.startswith("DELETE FROM scheduled_tasks"):
            self.calls.append(sql)
            assert self.depth >= 2
            self.rows.pop(args[0])
            return "DELETE 1"
        if sql.startswith("UPDATE scheduled_tasks SET"):
            import re

            self.calls.append(sql)
            assert self.depth >= 2
            row = self.rows[args[0]]
            for name, index in re.findall(r"(\w+) = \$(\d+)", sql.split("WHERE")[0]):
                row[name] = args[int(index) - 1]
            return "UPDATE 1"
        return await super().execute(sql, *args)

    async def fetchval(self, sql, *args):
        assert "UPDATE scheduled_tasks" in sql
        await self.execute(" ".join(sql.split()), *args)
        return self.rows[args[0]]["next_run_at"]

    async def fetchrow(self, sql, *args):
        if sql.lstrip().startswith("INSERT") and "scheduled_tasks" in sql and "'db'" in sql:
            from butlers.core.obligation_schedule_sources import SCHEDULE_FIELDS

            row = dict(zip(SCHEDULE_FIELDS, args[: len(SCHEDULE_FIELDS)], strict=True))
            slot = args[len(SCHEDULE_FIELDS)]
            assert slot.utcoffset() is not None
            row |= {"id": uuid4(), "source": "db", "next_run_at": slot}
            if row["task_type"] == "deadline":
                assert "deadline_status,fired_thresholds,depends_on" in sql
                row |= {
                    "deadline_status": "pending",
                    "fired_thresholds": [],
                    "depends_on": args[-1],
                }
            self.rows[row["id"]] = row
            return copy.deepcopy(row)
        return await super().fetchrow(sql, *args)


async def test_runtime_create_binds_actual_row_creator_and_command_without_changing_off_calls(
    monkeypatch,
):
    """Actual schedule_create path stages one row/source TX, not a creator verifier."""
    from butlers.core import obligation_admission, scheduler
    from butlers.core.obligation_schedule_sources import compile_runtime_schedule_definition

    # Ordinary legacy creation does not consult or write source infrastructure.

    old_transaction = obligation_admission.run_source_transaction
    monkeypatch.setattr(
        obligation_admission,
        "run_source_transaction",
        AsyncMock(side_effect=AssertionError),
    )
    ordinary_pool = AsyncMock()
    ordinary_pool.fetchval.return_value = UUID(int=899)
    assert await scheduler.schedule_create(
        ordinary_pool,
        name="legacy",
        cron="0 9 * * *",
        prompt="legacy prompt",
    ) == UUID(int=899)
    assert ordinary_pool.fetchval.await_count == 1
    assert "INSERT INTO scheduled_tasks" in ordinary_pool.fetchval.call_args.args[0]
    monkeypatch.setattr(obligation_admission, "run_source_transaction", old_transaction)

    creator = RuntimeCreationSnapshot(UUID(int=900), (Grant("health__read"),), True)
    prepared = compile_runtime_schedule_definition(
        owner_domain="health",
        normalized_schedule=schedule(name="runtime-source", timezone="UTC"),
        declaration=ObligationDeclaration((Grant("health__read"),)),
        context_material={"server_selected_policy": "software fixture, not authentication"},
        creator=creator,
    )

    pool = RecordingPool()
    pool.connection = RuntimeConnection()
    task_id = await scheduler.schedule_create(
        pool,
        name="runtime-source",
        cron="0 9 * * *",
        prompt="fixture prompt",
        obligation_definition=prepared,
        obligation_command_id=UUID(int=901),
    )
    assert len(pool.connection.rows) == len(pool.connection.definitions) == 1
    assert task_id in pool.connection.rows
    body = pool.connection.definitions[task_id]
    assert body["origin"] == "runtime"
    assert body["creator"]["exposed"] is True
    assert body["creator"]["invocation_id"] == str(creator.invocation_id)
    assert pool.connection.depth == 0
    # Exact command replay returns its old result, without duplicating the row.
    assert (
        await scheduler.schedule_create(
            pool,
            name="runtime-source",
            cron="0 9 * * *",
            prompt="fixture prompt",
            obligation_definition=prepared,
            obligation_command_id=UUID(int=901),
        )
        == task_id
    )
    assert len(pool.connection.rows) == len(pool.connection.definitions) == 1
    before = copy.deepcopy(pool.connection.rows)
    with pytest.raises(SourceConflict, match="schedule_material_mismatch"):
        await scheduler.schedule_create(
            pool,
            name="runtime-source",
            cron="0 9 * * *",
            prompt="changed payload",
            obligation_definition=prepared,
            obligation_command_id=UUID(int=902),
        )
    assert pool.connection.rows == before
    # No trusted creation context is invented from a valid declaration.
    unavailable = replace(prepared, context_json=None)
    with pytest.raises(SourceConflict, match="source_definition_unavailable"):
        await scheduler.schedule_create(
            pool,
            name="runtime-source",
            cron="0 9 * * *",
            prompt="fixture prompt",
            obligation_definition=unavailable,
            obligation_command_id=UUID(int=903),
        )
    assert pool.connection.rows == before
    # Actual connection identity, not owner_domain in the value, controls SQL.
    pool.connection.role = "butler_general_rw"
    with pytest.raises(SourceConflict, match="owning_role_required"):
        await scheduler.schedule_create(
            pool,
            name="runtime-source",
            cron="0 9 * * *",
            prompt="fixture prompt",
            obligation_definition=prepared,
            obligation_command_id=UUID(int=904),
        )
    assert pool.connection.rows == before
    pool.connection.role = "butler_health_rw"
    # A source/revision write failure rolls back the *real adapter's* row too.
    actual_execute = pool.connection.execute

    async def fail_revision(sql, *args):
        if "INSERT INTO obligation_revisions" in sql:
            raise RuntimeError("controlled revision failure")
        return await actual_execute(sql, *args)

    pool.connection.execute = fail_revision
    with pytest.raises(SourceUnavailable, match="source_transaction_unconfirmed"):
        await scheduler.schedule_create(
            pool,
            name="runtime-source",
            cron="0 9 * * *",
            prompt="fixture prompt",
            obligation_definition=prepared,
            obligation_command_id=UUID(int=905),
        )
    assert pool.connection.rows == before and pool.connection.depth == 0
    pool.connection.execute = actual_execute
    with pytest.raises(DeclarationError, match="creator_ceiling_exceeded"):
        compile_runtime_schedule_definition(
            owner_domain="health",
            normalized_schedule=schedule(),
            declaration=ObligationDeclaration((Grant("health__write"),)),
            context_material={"selected": "context"},
            creator=creator,
        )


async def test_runtime_deadline_actual_create_preserves_dependencies_and_creation_exposure():
    """The real deadline branch retains dependency material in its source identity."""
    from datetime import date

    from butlers.core import scheduler
    from butlers.core.obligation_schedule_sources import compile_runtime_schedule_definition

    dependency = str(UUID(int=920))
    creator = RuntimeCreationSnapshot(UUID(int=921), (Grant("health__read"),), True)
    material = schedule(
        name="deadline-source",
        cron="0 0 * * *",
        task_type="deadline",
        target_date=date(2099, 1, 1),
        lead_time_days=7,
        alert_thresholds=[{"days_before": 3, "severity": "info"}],
    )
    prepared = compile_runtime_schedule_definition(
        owner_domain="health",
        normalized_schedule=material,
        declaration=ObligationDeclaration((Grant("health__read"),)),
        context_material={"selected": "fixture context, not authentication"},
        creator=creator,
        depends_on=(dependency,),
    )
    pool = RecordingPool()
    pool.connection = RuntimeConnection()
    kwargs = {
        "name": material["name"],
        "task_type": "deadline",
        "prompt": material["prompt"],
        "target_date": material["target_date"],
        "lead_time_days": material["lead_time_days"],
        "alert_thresholds": material["alert_thresholds"],
        "depends_on": [dependency],
        "obligation_definition": prepared,
        "obligation_command_id": UUID(int=922),
    }
    task_id = await scheduler.schedule_create(pool, **kwargs)
    row = pool.connection.rows[task_id]
    assert row["depends_on"] == [dependency]
    assert row["deadline_status"] == "pending" and row["fired_thresholds"] == []
    assert row["target_date"] == date(2099, 1, 1)
    body = pool.connection.definitions[task_id]
    assert body["family"] == "deadline" and body["creator"]["exposed"] is True
    assert await scheduler.schedule_create(pool, **kwargs) == task_id
    assert len(pool.connection.rows) == 1
    before = copy.deepcopy(pool.connection.rows)
    for changes in ({"depends_on": []}, {"prompt": "another deadline"}):
        with pytest.raises(SourceConflict, match="schedule_material_mismatch"):
            await scheduler.schedule_create(pool, **(kwargs | changes))
        assert pool.connection.rows == before
    different_dependency = compile_runtime_schedule_definition(
        owner_domain="health",
        normalized_schedule=material,
        declaration=prepared.declaration,
        context_material={"selected": "fixture context, not authentication"},
        creator=creator,
    )
    assert different_dependency.material_digest != prepared.material_digest


async def test_runtime_update_revises_actual_locked_row_and_invalidates_only_unsettled_work():
    """Actual scheduler normalizers execute inside the same owning source TX."""
    from butlers.core import scheduler
    from butlers.core.obligation_schedule_sources import compile_runtime_schedule_definition

    # OFF value validation stays before any row/source IO after extraction.
    ordinary = AsyncMock()
    with pytest.raises(ValueError, match="Invalid cron"):
        await scheduler.schedule_update(ordinary, UUID(int=939), cron="invalid cron")
    ordinary.fetchrow.assert_not_awaited()
    ordinary.execute.assert_not_awaited()

    creator = RuntimeCreationSnapshot(UUID(int=940), (Grant("health__read"),), True)
    initial = compile_runtime_schedule_definition(
        owner_domain="health",
        normalized_schedule=schedule(name="editable"),
        declaration=ObligationDeclaration((Grant("health__read"),)),
        context_material={"selected": "fixture context, not authentication"},
        creator=creator,
    )
    pool = RecordingPool()
    pool.connection = RuntimeConnection()
    task_id = await scheduler.schedule_create(
        pool,
        name="editable",
        cron="0 9 * * *",
        prompt="fixture prompt",
        obligation_definition=initial,
        obligation_command_id=UUID(int=941),
    )
    revision = pool.connection.definitions[task_id]["revision"]
    for index, state in enumerate(("pending", "claimed", "cancelled"), 942):
        pool.connection.occurrences[UUID(int=index)] = {
            "id": UUID(int=index),
            "source_id": task_id,
            "revision": revision,
            "source_key": f"{index:064x}",
            "source_kind": "occurrence",
            "source_ref": str(task_id),
            "occurrence": f"fixture-slot-{index}",
            "state": state,
            "claim_token": UUID(int=index + 1000) if state == "claimed" else None,
            "created_at": datetime(2026, 10, 1, tzinfo=UTC),
        }
    history = copy.deepcopy(pool.connection.occurrences[UUID(int=944)])
    prior_evidence = copy.deepcopy(
        (pool.connection.revisions, pool.connection.commands, pool.connection.outbox)
    )
    changed = replace(
        initial, row_json=initial.row_json.replace("fixture prompt", "revised prompt")
    )
    kwargs = dict(
        prompt="revised prompt",
        obligation_definition=changed,
        obligation_command_id=UUID(int=945),
        obligation_expected_revision=revision,
    )
    assert await scheduler.schedule_update(pool, task_id, **kwargs) is None
    assert pool.connection.rows[task_id]["prompt"] == "revised prompt"
    body = pool.connection.definitions[task_id]
    assert body["generation"] == 2 and body["revision"] != revision
    assert body["creator"]["exposed"] is True
    assert body["creator"]["invocation_id"] == str(creator.invocation_id)
    assert [row["state"] for row in pool.connection.occurrences.values()] == [
        "cancelled",
        "cancelled",
        "cancelled",
    ]
    assert pool.connection.occurrences[UUID(int=944)] == history
    assert all(pool.connection.revisions[k] == v for k, v in prior_evidence[0].items())
    assert all(pool.connection.commands[k] == v for k, v in prior_evidence[1].items())
    assert pool.connection.outbox[: len(prior_evidence[2])] == prior_evidence[2]
    # Historical command reconciliation must never execute its mutation again.
    pool.connection.rows[task_id]["prompt"] = "later independent write"
    assert await scheduler.schedule_update(pool, task_id, **kwargs) is None
    assert pool.connection.rows[task_id]["prompt"] == "later independent write"
    # Both actual submitted requests must be refused before historical success.
    # Accumulate both outcomes so a RED receipt exercises both bypasses.
    before_replay = copy.deepcopy(
        (pool.connection.rows, pool.connection.definitions, pool.connection.commands)
    )
    refused = []
    for replay_fields, error in (
        ({"prompt": "different payload"}, SourceConflict),
        ({"cron": "invalid cron"}, ValueError),
        ({"dispatch_mode": "invalid mode"}, ValueError),
        ({"complexity": "invalid complexity"}, ValueError),
        ({"timezone": None}, ValueError),
        ({"description": "different metadata"}, SourceConflict),
    ):
        try:
            await scheduler.schedule_update(pool, task_id, **(kwargs | replay_fields))
        except error:
            refused.append(True)
        else:
            refused.append(False)
        assert (
            pool.connection.rows,
            pool.connection.definitions,
            pool.connection.commands,
        ) == before_replay
    assert refused == [True] * 6
    # Equivalent normalized partial semantics still reconcile without SQL writes.
    assert (
        await scheduler.schedule_update(pool, task_id, **(kwargs | {"timezone": " UTC "})) is None
    )
    assert (
        pool.connection.rows,
        pool.connection.definitions,
        pool.connection.commands,
    ) == before_replay
    pool.connection.rows[task_id]["prompt"] = "revised prompt"
    before = copy.deepcopy((pool.connection.rows, pool.connection.definitions))
    with pytest.raises(SourceConflict, match="creation_binding_immutable"):
        await scheduler.schedule_update(
            pool, task_id, **(kwargs | {"obligation_command_id": UUID(int=946)})
        )
    assert (pool.connection.rows, pool.connection.definitions) == before
    # Validation happens on the real normalizer's output; mismatched intent rolls
    # back its SQL row update, not merely a source metadata write.
    writes_before = sum(
        sql.startswith("UPDATE scheduled_tasks SET") for sql in pool.connection.calls
    )
    with pytest.raises(SourceConflict, match="schedule_material_mismatch"):
        await scheduler.schedule_update(
            pool,
            task_id,
            **(
                kwargs
                | {
                    "prompt": "not the selected source material",
                    "obligation_command_id": UUID(int=947),
                    "obligation_expected_revision": body["revision"],
                }
            ),
        )
    assert (pool.connection.rows, pool.connection.definitions) == before
    assert (
        sum(sql.startswith("UPDATE scheduled_tasks SET") for sql in pool.connection.calls)
        == writes_before + 1
    )
    assert pool.connection.depth == 0


async def test_runtime_delete_tombstones_real_row_and_reconciles_only_original_delete():
    """Deletion cancels live attempts atomically; absence alone is not a receipt."""
    from butlers.core import scheduler
    from butlers.core.obligation_schedule_sources import compile_runtime_schedule_definition

    creator = RuntimeCreationSnapshot(UUID(int=960), (Grant("health__read"),), True)
    selected = compile_runtime_schedule_definition(
        owner_domain="health",
        normalized_schedule=schedule(name="deletable"),
        declaration=ObligationDeclaration((Grant("health__read"),)),
        context_material={"selected": "fixture context, not authentication"},
        creator=creator,
    )
    pool = RecordingPool()
    pool.connection = RuntimeConnection()
    task_id = await scheduler.schedule_create(
        pool,
        name="deletable",
        cron="0 9 * * *",
        prompt="fixture prompt",
        obligation_definition=selected,
        obligation_command_id=UUID(int=961),
    )
    revision = pool.connection.definitions[task_id]["revision"]
    for index, state in enumerate(("pending", "claimed", "cancelled"), 962):
        pool.connection.occurrences[UUID(int=index)] = {
            "id": UUID(int=index),
            "source_id": task_id,
            "revision": revision,
            "source_key": f"{index:064x}",
            "source_kind": "occurrence",
            "source_ref": str(task_id),
            "occurrence": f"fixture-slot-{index}",
            "state": state,
            "claim_token": UUID(int=index + 1000) if state == "claimed" else None,
            "created_at": datetime(2026, 10, 1, tzinfo=UTC),
        }
    history = copy.deepcopy(pool.connection.occurrences[UUID(int=964)])
    prior_evidence = copy.deepcopy(
        (pool.connection.revisions, pool.connection.commands, pool.connection.outbox)
    )
    kwargs = dict(
        obligation_definition=selected,
        obligation_command_id=UUID(int=965),
        obligation_expected_revision=revision,
    )
    # A source write failure after the actual DELETE restores that row too.
    before = copy.deepcopy((pool.connection.rows, pool.connection.definitions))
    execute = pool.connection.execute

    async def reject_revision(sql, *args):
        if "INSERT INTO obligation_revisions" in sql:
            raise RuntimeError("controlled revision failure")
        return await execute(sql, *args)

    pool.connection.execute = reject_revision
    with pytest.raises(SourceUnavailable, match="source_transaction_unconfirmed"):
        await scheduler.schedule_delete(pool, task_id, **kwargs)
    assert (pool.connection.rows, pool.connection.definitions) == before
    pool.connection.execute = execute
    assert await scheduler.schedule_delete(pool, task_id, **kwargs) is None
    assert task_id not in pool.connection.rows
    body = pool.connection.definitions[task_id]
    assert body["state"] == "deleted" and body["generation"] == 2
    assert body["creator"]["exposed"] is True
    assert [row["state"] for row in pool.connection.occurrences.values()] == [
        "cancelled",
        "cancelled",
        "cancelled",
    ]
    assert pool.connection.occurrences[UUID(int=964)] == history
    assert all(pool.connection.revisions[k] == v for k, v in prior_evidence[0].items())
    assert all(pool.connection.commands[k] == v for k, v in prior_evidence[1].items())
    assert pool.connection.outbox[: len(prior_evidence[2])] == prior_evidence[2]
    assert await scheduler.schedule_delete(pool, task_id, **kwargs) is None
    with pytest.raises(SourceConflict, match="source_revision_conflict"):
        await scheduler.schedule_delete(
            pool, task_id, **(kwargs | {"obligation_command_id": UUID(int=966)})
        )
    # A creation replay is historical, not resurrection of the deleted row.
    assert (
        await scheduler.schedule_create(
            pool,
            name="deletable",
            cron="0 9 * * *",
            prompt="fixture prompt",
            obligation_definition=selected,
            obligation_command_id=UUID(int=961),
        )
        == task_id
    )
    assert task_id not in pool.connection.rows and body["state"] == "deleted"
    assert pool.connection.depth == 0


async def test_deadline_revision_reset_terminal_and_delete_share_owning_transaction():
    """Actual deadline mutators retain progress rules while cancelling old work."""
    from datetime import date

    from butlers.core import scheduler
    from butlers.core.obligation_schedule_sources import compile_runtime_schedule_definition
    from butlers.core.temporal.deadlines_db import deadline_delete, deadline_update

    selected = compile_runtime_schedule_definition(
        owner_domain="health",
        normalized_schedule=schedule(
            name="deadline-revision",
            cron="0 0 * * *",
            task_type="deadline",
            target_date=date(2099, 1, 1),
            lead_time_days=7,
            alert_thresholds=[{"days_before": 3, "severity": "info"}],
        ),
        declaration=ObligationDeclaration((Grant("health__read"),)),
        context_material={"fixture_only": "no invocation authority"},
        creator=RuntimeCreationSnapshot(UUID(int=980), (Grant("health__read"),), True),
    )
    pool = RecordingPool()
    pool.connection = RuntimeConnection()
    row = selected.schedule
    task = await scheduler.schedule_create(
        pool,
        name=row["name"],
        task_type="deadline",
        prompt=row["prompt"],
        target_date=row["target_date"],
        lead_time_days=7,
        alert_thresholds=row["alert_thresholds"],
        obligation_definition=selected,
        obligation_command_id=UUID(int=981),
    )
    revision = pool.connection.definitions[task]["revision"]
    pool.connection.occurrences[UUID(int=987)] = {
        "id": UUID(int=987),
        "source_id": task,
        "revision": revision,
        "source_key": "f" * 64,
        "source_kind": "occurrence",
        "source_ref": str(task),
        "occurrence": "deadline-threshold",
        "state": "pending",
        "claim_token": None,
        "created_at": datetime(2026, 10, 1, tzinfo=UTC),
    }
    pool.connection.rows[task]["fired_thresholds"] = [{"days_before": 3}]
    pool.connection.rows[task]["deadline_status"] = "alerted"
    changed = replace(
        selected,
        row_json=selected.row_json.replace("2099-01-01", "2099-02-01"),
        dependencies=(str(UUID(int=982)),),
    )
    kwargs = dict(
        target_date=date(2099, 2, 1),
        depends_on=list(changed.dependencies),
        obligation_definition=changed,
        obligation_command_id=UUID(int=983),
        obligation_expected_revision=revision,
    )
    await deadline_update(pool, task, **kwargs)
    actual = pool.connection.rows[task]
    assert actual["target_date"] == date(2099, 2, 1)
    assert actual["fired_thresholds"] == [] and actual["deadline_status"] == "pending"
    assert actual["depends_on"] == list(changed.dependencies)
    assert pool.connection.definitions[task]["generation"] == 2
    assert pool.connection.occurrences[UUID(int=987)]["state"] == "cancelled"
    before = copy.deepcopy((pool.connection.rows, pool.connection.definitions))
    await deadline_update(pool, task, **kwargs)
    assert (pool.connection.rows, pool.connection.definitions) == before
    with pytest.raises(SourceConflict):
        await deadline_update(pool, task, **(kwargs | {"depends_on": []}))
    with pytest.raises(ValueError):
        await deadline_update(pool, task, **(kwargs | {"deadline_status": "invented"}))
    assert (pool.connection.rows, pool.connection.definitions) == before
    # A mismatched requested prompt executes the ordinary update, then the same
    # owning transaction rolls back both row and source state on readback.
    with pytest.raises(SourceConflict, match="schedule_material_mismatch"):
        await deadline_update(
            pool,
            task,
            **(
                kwargs
                | {
                    "prompt": "different request",
                    "obligation_command_id": UUID(int=984),
                    "obligation_expected_revision": pool.connection.definitions[task]["revision"],
                }
            ),
        )
    assert (pool.connection.rows, pool.connection.definitions) == before
    completed = replace(
        changed,
        row_json=changed.row_json.replace('"enabled":true', '"enabled":false'),
        deadline_status="completed",
    )
    terminal_kwargs = dict(
        deadline_status="completed",
        obligation_definition=completed,
        obligation_command_id=UUID(int=986),
        obligation_expected_revision=pool.connection.definitions[task]["revision"],
    )
    await deadline_update(pool, task, **terminal_kwargs)
    assert pool.connection.rows[task]["deadline_status"] == "completed"
    assert pool.connection.rows[task]["enabled"] is False
    assert pool.connection.definitions[task]["state"] == "disabled"
    with pytest.raises(SourceConflict, match="schedule_material_mismatch"):
        await deadline_update(pool, task, **(terminal_kwargs | {"deadline_status": "expired"}))
    changed = completed
    deletion = dict(
        obligation_definition=changed,
        obligation_command_id=UUID(int=985),
        obligation_expected_revision=pool.connection.definitions[task]["revision"],
    )
    await deadline_delete(pool, task, **deletion)
    assert task not in pool.connection.rows
    assert pool.connection.definitions[task]["state"] == "deleted"
    await deadline_delete(pool, task, **deletion)
    assert pool.connection.depth == 0


async def test_runtime_toggle_binds_enabled_and_reconciles_without_replaying_newer_row():
    from butlers.core import scheduler
    from butlers.core.obligation_schedule_sources import compile_runtime_schedule_definition

    selected = compile_runtime_schedule_definition(
        owner_domain="health",
        normalized_schedule=schedule(),
        declaration=ObligationDeclaration((Grant("health__read"),)),
        context_material={"fixture_only": "not caller authority"},
        creator=RuntimeCreationSnapshot(UUID(int=990), (Grant("health__read"),), True),
    )
    pool = RecordingPool()
    pool.connection = RuntimeConnection()
    task = await scheduler.schedule_create(
        pool,
        "fixture",
        "0 9 * * *",
        "fixture prompt",
        obligation_definition=selected,
        obligation_command_id=UUID(int=991),
    )
    disabled = replace(
        selected, row_json=selected.row_json.replace('"enabled":true', '"enabled":false')
    )
    kwargs = dict(
        enabled=False,
        obligation_definition=disabled,
        obligation_command_id=UUID(int=992),
        obligation_expected_revision=pool.connection.definitions[task]["revision"],
    )
    receipt = await scheduler.schedule_toggle(pool, task, **kwargs)
    assert receipt["observed_enabled"] is False and receipt["changed"] is True
    assert pool.connection.rows[task]["enabled"] is False
    assert pool.connection.rows[task]["next_run_at"] is None
    assert pool.connection.definitions[task]["state"] == "disabled"
    assert pool.connection.definitions[task]["generation"] == 2
    # Reconciliation returns a source-command observation, not a fabricated
    # current-row observation or a repeated mutation against newer row state.
    pool.connection.rows[task]["enabled"] = True
    before = copy.deepcopy((pool.connection.rows, pool.connection.definitions))
    reconciled = await scheduler.schedule_toggle(pool, task, **kwargs)
    assert reconciled["status"] == "reconciled"
    assert reconciled["source_revision"] == receipt["source_revision"]
    assert "observed_enabled" not in reconciled
    with pytest.raises(SourceConflict, match="schedule_material_mismatch"):
        await scheduler.schedule_toggle(pool, task, **(kwargs | {"enabled": True}))
    assert (pool.connection.rows, pool.connection.definitions) == before


@pytest.mark.asyncio
async def test_full_loaded_git_reconciliation_uses_actual_inventory_and_tombstones(tmp_path):
    from butlers.config import load_config
    from butlers.core.obligation_git_sources import (
        capture_loaded_git_sources,
        reconcile_loaded_git_sources,
    )
    from butlers.core.runtime_config import RuntimeConfig
    from tests.config.test_obligation_declarations import _loaded_config
    from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog

    class InventoryConnection(RecordingConnection):
        async def fetch(self, sql, *args):
            if "FROM obligation_definitions" in sql:
                self.calls.append(sql)
                return [
                    {"id": key}
                    for key, value in self.definitions.items()
                    if value["origin"] == "git" and value["state"] != "deleted"
                ]
            if "FROM" in sql and "scheduled_tasks" in sql:
                self.calls.append(sql)
                return [copy.deepcopy(row) for row in self.rows.values() if row["source"] == "toml"]
            return await super().fetch(sql, *args)

        async def execute(self, sql, *args):
            if sql.lstrip().startswith("DELETE FROM") and "scheduled_tasks" in sql:
                self.calls.append(sql)
                del self.rows[args[0]]
                return "DELETE 1"
            if "INSERT INTO obligation_revisions" in sql and getattr(self, "fail_revision", False):
                raise OSError("software commit-path failure")
            return await super().execute(sql, *args)

    pool = RecordingPool()
    pool.connection = InventoryConnection()
    registry = await _complete_core_catalog("health")
    runtime = RuntimeConfig("health")
    config = _loaded_config(tmp_path)
    captured = capture_loaded_git_sources(config, runtime, registry)
    first = await reconcile_loaded_git_sources(pool, captured)
    (created,) = first
    source = created.source_id
    assert created.origin == "git" and created.creator is None
    assert pool.connection.rows[source]["next_run_at"] is None
    nominal = datetime(2027, 1, 2, tzinfo=UTC)
    pool.connection.rows[source]["next_run_at"] = nominal
    stable = copy.deepcopy((pool.connection.definitions, pool.connection.outbox))
    assert await reconcile_loaded_git_sources(pool, captured) == first
    assert (pool.connection.definitions, pool.connection.outbox) == stable
    assert pool.connection.rows[source]["next_run_at"] == nominal
    # Actual writer/repository SQL path rolls back row + revision together;
    # this software collaborator is not PostgreSQL durability or lock proof.
    path = tmp_path / "butler.toml"
    path.write_text(path.read_text().replace("Inspect the fixture", "Updated fixture"))
    updated = capture_loaded_git_sources(load_config(tmp_path), runtime, registry)
    before = copy.deepcopy(
        (pool.connection.rows, pool.connection.definitions, pool.connection.outbox)
    )
    pool.connection.fail_revision = True
    with pytest.raises(SourceUnavailable, match="source_transaction_unconfirmed"):
        await reconcile_loaded_git_sources(pool, updated)
    assert (pool.connection.rows, pool.connection.definitions, pool.connection.outbox) == before
    pool.connection.fail_revision = False
    pool.connection.role = "butler_other_rw"
    with pytest.raises(SourceConflict, match="owning_role_required"):
        await reconcile_loaded_git_sources(pool, updated)
    assert (pool.connection.rows, pool.connection.definitions, pool.connection.outbox) == before
    pool.connection.role = "butler_health_rw"
    pool.connection.schedule_shadow = True
    with pytest.raises(SourceConflict, match="owning_schedule_relation_required"):
        await reconcile_loaded_git_sources(pool, updated)
    assert (pool.connection.rows, pool.connection.definitions, pool.connection.outbox) == before
    pool.connection.schedule_shadow = False
    revised = await reconcile_loaded_git_sources(pool, updated)
    assert revised[0].generation == 2
    assert pool.connection.rows[source]["next_run_at"] == nominal

    # The same strict load cannot lend its provenance to later mutable input.
    config.schedules[0].prompt = "post-load change"
    with pytest.raises(DeclarationError, match="loaded_source_changed"):
        capture_loaded_git_sources(config, runtime, registry)

    # Full, successfully loaded empty config authorizes Git removal only. It
    # never sweeps runtime rows or treats an arbitrary selected subset as full.
    runtime_row = uuid4()
    pool.connection.rows[runtime_row] = schedule(name="manual") | {
        "id": runtime_row,
        "source": "db",
    }
    occurrence = uuid4()
    pool.connection.occurrences[occurrence] = {
        "source_id": source,
        "revision": created.revision,
        "state": "claimed",
    }
    (tmp_path / "butler.toml").write_text('[butler]\nname="health"\nport=9100\n')
    empty = capture_loaded_git_sources(load_config(tmp_path), runtime, registry)
    (deleted,) = await reconcile_loaded_git_sources(pool, empty)
    assert deleted.state == "deleted" and deleted.source_id == source
    assert source not in pool.connection.rows and runtime_row in pool.connection.rows
    assert pool.connection.occurrences[occurrence]["state"] == "cancelled"
    assert len(pool.connection.outbox) > len(stable[1])
    assert await reconcile_loaded_git_sources(pool, empty) == ()
    assert pool.connection.depth == 0
