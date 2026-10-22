"""Dormant schedule writer software controls, NOT PostgreSQL/authority proof.

REQ-goal-orchestration-007; REQ-capability-authorization-013.
The context fixture below is supplied test material, not a verified producer.
"""

import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
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
