"""Drift guards for the hand-provisioned table stand-ins in ``butlers.testing``.

An integration test that needs one table from *another* butler's migration
chain sometimes provisions it by hand rather than running that whole chain.
``connector_registry`` is the recurring case: three integration tests each
stood up their own copy of that table, with a column list covering only what
their own endpoint queried.

That is a silent-breakage machine.  When ``sw_031`` widened the registry, the
stale stand-ins produced five of the nine failures on PR #3853 -- and none of
them pointed at the DDL.  The endpoint's ``SELECT`` raised, the route returned
its DEGRADED envelope, and the test died much later on ``KeyError:
'hourly_events_available'`` or an ``IndexError`` on an empty device list.  Each
file also passed in isolation against its own stand-in, so the cost was only
paid ~35 minutes into a full run, by whoever changed the schema next.

These tests move the failure back to the point of breakage:

- :func:`test_standin_matches_the_real_migration_chain` diffs every registered
  stand-in against the table the real chain builds, naming the exact columns,
  constraints, indexes and non-internal triggers that drifted.  Trigger
  function bodies and execution metadata are read from ``pg_proc`` and
  ``pg_trigger``; only the declared self-contained append-only trigger is
  mirrored, while sibling-table FK substitutes are explicitly classified as
  exclusions in :mod:`butlers.testing.schema_standins`.
- :func:`test_the_index_diff_can_fail` keeps that index arm honest: a guard
  nobody has watched go red is indistinguishable from one that cannot.
- :func:`test_the_trigger_diff_can_fail` keeps trigger declaration/body and
  classification arms honest, including absent, altered and unclassified
  definitions.
- :func:`test_no_test_hand_rolls_a_standin_table` stops the class from
  recurring by refusing any further hand-written copy of a registered table.

The append-only trigger is deliberately covered here rather than by the
stored-function drift probe: the latter validates deployed ``init-db.sql``
functions, while this contract validates the stand-in's schema-qualified
function/table binding.  FK-substitute triggers from ``approvals_008``,
``approvals_009`` and ``approvals_011`` remain real-chain-only because their
referential semantics belong to ``tests/modules/test_approvals_retention.py``.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.testing.schema_standins import (
    APPROVAL_EVENTS,
    AUTONOMY_APPROVAL_HISTORY,
    AUTONOMY_SUGGESTIONS,
    CONTACT_ENTITY_MAP,
    PENDING_ACTIONS,
    STANDINS,
    TableStandin,
    TriggerExclusion,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PARITY_SCHEMA = "standin_parity"
_BLINDED_SCHEMA = "standin_parity_blinded"
_EXEMPTION_MARKER = "schema-standin-exempt:"
_EXEMPTION_LOOKBACK_LINES = 8

docker_available = shutil.which("docker") is not None


@pytest.fixture(scope="module")
def parity_db_url(postgres_container) -> str:
    """Provision every chain the registered stand-ins claim to mirror.

    A chain that owns a schema is migrated under it, so its tables land where
    the declaring stand-in says they do rather than in ``public``.
    """
    chains: list[str] = []
    schemas: dict[str, str] = {}
    for standin in STANDINS.values():
        chains.extend(chain for chain in standin.chains if chain not in chains)
        schemas.update(standin.chain_schemas)
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=chains, schemas=schemas
    )


def _columns(conn, schema: str, table: str) -> dict[str, tuple[str, str, str | None]]:
    rows = conn.execute(
        text(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t"
        ),
        {"s": schema, "t": table},
    )
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


def _constraints(conn, schema: str, table: str) -> dict[str, str]:
    rows = conn.execute(
        text(
            "SELECT c.conname, pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = :s AND t.relname = :t AND c.contype IN ('p', 'c')"
        ),
        {"s": schema, "t": table},
    )
    return {row[0]: row[1] for row in rows}


def _indexes(conn, schema: str, table: str) -> dict[str, str]:
    """Return ``{index name: schema-independent definition}``.

    Postgres canonicalises ``indexdef`` (``USING btree``, parenthesised and
    cast predicates), so reading both sides out of the catalogue compares the
    *materialised* index rather than two spellings of the same intent.  Only
    the schema qualification has to be normalised away.
    """
    rows = conn.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :s AND tablename = :t"),
        {"s": schema, "t": table},
    )
    return {row[0]: row[1].replace(f" ON {schema}.", " ON ") for row in rows}


def _normalize_function_body(body: str) -> str:
    """Normalize only line endings and outer whitespace in ``pg_proc.prosrc``.

    A function body is executable SQL and may contain schema-looking text in
    literals.  Do not dedent, collapse whitespace, or replace identifiers here:
    those transformations could make a materially different body look equal.
    """
    return body.replace("\r\n", "\n").replace("\r", "\n").strip()


def _trigger_execution_shape(trigger_type: int) -> tuple[tuple[str, ...], str, str]:
    """Decode PostgreSQL's ``tgtype`` into event, timing and level metadata."""
    events = tuple(
        event
        for bit, event in ((4, "INSERT"), (8, "DELETE"), (16, "UPDATE"), (32, "TRUNCATE"))
        if trigger_type & bit
    )
    if trigger_type & 64:
        timing = "INSTEAD OF"
    elif trigger_type & 2:
        timing = "BEFORE"
    else:
        timing = "AFTER"
    level = "ROW" if trigger_type & 1 else "STATEMENT"
    return events, timing, level


def _triggers(conn, schema: str, table: str) -> dict[str, dict[str, object]]:
    """Return non-internal trigger metadata and the bound function attributes.

    Trigger/function OIDs are database-local and therefore deliberately absent
    from the result.  The relation-local constraint flag, execution metadata,
    function identity, body, language, security and configuration are stable
    contract values and are compared by :func:`_trigger_drift`.
    """
    rows = conn.execute(
        text(
            """
            SELECT
                trigger_row.tgname,
                trigger_row.tgenabled,
                trigger_row.tgtype,
                pg_get_expr(trigger_row.tgqual, trigger_row.tgrelid) AS when_condition,
                trigger_row.tgnargs,
                encode(trigger_row.tgargs, 'hex') AS trigger_arguments,
                trigger_row.tgattr::text AS trigger_columns,
                (trigger_row.tgconstraint <> 0) AS is_constraint,
                (trigger_row.tgconstrrelid <> 0) AS has_constraint_relation,
                trigger_row.tgdeferrable,
                trigger_row.tginitdeferred,
                trigger_row.tgoldtable,
                trigger_row.tgnewtable,
                function_namespace.nspname AS function_schema,
                function_row.proname AS function_name,
                pg_get_function_identity_arguments(function_row.oid)
                    AS function_identity_arguments,
                function_row.prosrc AS function_body,
                language_row.lanname AS function_language,
                function_row.prosecdef AS function_security_definer,
                COALESCE(function_row.proconfig, ARRAY[]::text[]) AS function_config
            FROM pg_trigger AS trigger_row
            JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
            JOIN pg_namespace AS table_namespace
                ON table_namespace.oid = table_row.relnamespace
            JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
            JOIN pg_namespace AS function_namespace
                ON function_namespace.oid = function_row.pronamespace
            JOIN pg_language AS language_row ON language_row.oid = function_row.prolang
            WHERE table_namespace.nspname = :schema
              AND table_row.relname = :table
              AND NOT trigger_row.tgisinternal
            ORDER BY trigger_row.tgname
            """
        ),
        {"schema": schema, "table": table},
    )
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        values = row._mapping
        trigger_type = int(values["tgtype"])
        events, timing, level = _trigger_execution_shape(trigger_type)
        result[str(values["tgname"])] = {
            "tgenabled": str(values["tgenabled"]),
            "tgtype": trigger_type,
            "event_timing_level": (events, timing, level),
            "when_condition": values["when_condition"],
            "trigger_arguments": (int(values["tgnargs"]), values["trigger_arguments"]),
            "trigger_columns": values["trigger_columns"],
            "is_constraint": bool(values["is_constraint"]),
            "has_constraint_relation": bool(values["has_constraint_relation"]),
            "tgdeferrable": bool(values["tgdeferrable"]),
            "tginitdeferred": bool(values["tginitdeferred"]),
            "transition_tables": (values["tgoldtable"], values["tgnewtable"]),
            "function_binding": (
                str(values["function_schema"]),
                str(values["function_name"]),
                str(values["function_identity_arguments"]),
            ),
            "function_body": _normalize_function_body(str(values["function_body"])),
            "function_language": str(values["function_language"]),
            "function_security_definer": bool(values["function_security_definer"]),
            "function_config": tuple(values["function_config"] or ()),
        }
    return result


def _trigger_drift(
    standin: TableStandin,
    real: dict[str, dict[str, object]],
    mirror: dict[str, dict[str, object]],
    *,
    real_schema: str,
    mirror_schema: str,
) -> list[str]:
    """Classify every non-internal trigger and compare mirrored definitions."""
    problems: list[str] = []
    expected = {trigger.name: trigger for trigger in standin.triggers}
    exclusions = {exclusion.name: exclusion for exclusion in standin.excluded_triggers}

    for name in sorted(expected.keys() & exclusions.keys()):
        problems.append(
            f"  INVALID trigger classification: {name} is both mirrored and excluded "
            f"for {standin.constant_path}"
        )

    for name in sorted(set(real) & set(expected)):
        if name not in mirror:
            problems.append(
                f"  MISSING trigger: {name} -- the migration chain has it, "
                f"{standin.constant_path} does not render it"
            )
    for name in sorted(set(expected) - set(real)):
        problems.append(
            f"  MISSING trigger: {name} -- {standin.constant_path} declares it, "
            f"but {real_schema}.{standin.table} has no such non-internal trigger"
        )
    for name in sorted(set(expected) & set(mirror) - set(real)):
        problems.append(
            f"  EXTRA trigger: {name} -- {standin.constant_path} renders it, "
            f"but the real {real_schema}.{standin.table} does not"
        )

    for name, exclusion in sorted(exclusions.items()):
        if name not in real:
            problems.append(
                f"  STALE trigger exclusion: {name} ({exclusion.migration}) -- {exclusion.reason}"
            )
        if name in mirror:
            problems.append(
                f"  EXCLUDED trigger rendered: {name} -- {exclusion.migration} is a "
                "sibling-table guard and must remain absent from an independent stand-in"
            )

    unknown_real = set(real) - set(expected) - set(exclusions)
    for name in sorted(unknown_real):
        problems.append(
            f"  UNCLASSIFIED trigger: {name} -- {real_schema}.{standin.table} has a "
            f"non-internal trigger with no mirrored definition or named exclusion in "
            f"{standin.constant_path}"
        )
    unknown_mirror = set(mirror) - set(expected)
    for name in sorted(unknown_mirror):
        problems.append(
            f"  UNCLASSIFIED trigger: {name} -- {mirror_schema}.{standin.table} has a "
            f"trigger that {standin.constant_path} does not declare"
        )

    comparable_fields = (
        ("tgenabled", "enabled state"),
        ("tgtype", "trigger type bits"),
        ("event_timing_level", "event/timing/level"),
        ("when_condition", "WHEN condition"),
        ("trigger_arguments", "arguments"),
        ("trigger_columns", "UPDATE OF columns"),
        ("tgdeferrable", "deferrability"),
        ("tginitdeferred", "initially-deferred flag"),
        ("is_constraint", "constraint-trigger flag"),
        ("has_constraint_relation", "constraint relation flag"),
        ("transition_tables", "transition-table names"),
        ("function_body", "function body"),
        ("function_language", "function language"),
        ("function_security_definer", "function security"),
        ("function_config", "function configuration"),
    )
    for name in sorted(set(real) & set(mirror) & set(expected)):
        real_row = real[name]
        mirror_row = mirror[name]
        real_function = real_row["function_binding"]
        mirror_function = mirror_row["function_binding"]
        assert isinstance(real_function, tuple) and isinstance(mirror_function, tuple)
        if real_function[0] != real_schema:
            problems.append(
                f"  MISMATCHED trigger function binding: {name} -- chain binds "
                f"{real_function[0]}.{real_function[1]} instead of {real_schema}."
                f"{real_function[1]}"
            )
        if mirror_function[0] != mirror_schema:
            problems.append(
                f"  MISMATCHED trigger function binding: {name} -- mirror binds "
                f"{mirror_function[0]}.{mirror_function[1]} instead of "
                f"{mirror_schema}.{mirror_function[1]}"
            )
        if real_function[1:] != mirror_function[1:]:
            problems.append(
                f"  MISMATCHED trigger function identity: {name} -- chain says "
                f"{real_function[1:]!r}, mirror says {mirror_function[1:]!r}"
            )
        for field, label in comparable_fields:
            if real_row[field] != mirror_row[field]:
                problems.append(
                    f"  MISMATCHED trigger {label}: {name} -- chain says "
                    f"{real_row[field]!r}, mirror says {mirror_row[field]!r}"
                )
    return problems


def _describe_drift(standin: TableStandin, real: dict, mirror: dict, kind: str) -> list[str]:
    """Return one human-readable line per drifted item, or an empty list."""
    problems: list[str] = []
    for name in sorted(set(real) - set(mirror)):
        problems.append(
            f"  MISSING {kind}: {name} {real[name]} -- the migration chain has it, "
            f"{standin.constant_path} does not"
        )
    for name in sorted(set(mirror) - set(real)):
        problems.append(
            f"  EXTRA {kind}: {name} {mirror[name]} -- {standin.constant_path} has it, "
            "the migration chain does not"
        )
    for name in sorted(set(real) & set(mirror)):
        if real[name] != mirror[name]:
            problems.append(
                f"  MISMATCHED {kind}: {name} -- chain says {real[name]}, "
                f"{standin.constant_path} says {mirror[name]}"
            )
    return problems


def _drift(
    conn,
    standin: TableStandin,
    mirror_schema: str,
    *,
    after_mirror_ddl: Callable[[object, str], None] | None = None,
) -> list[str]:
    """Build the stand-in and diff tables, indexes and triggers against reality.

    ``after_mirror_ddl`` is a narrow falsification seam used only by the
    parameterized trigger guard to install an intentionally unclassified
    mirror trigger before catalog inspection.
    """
    conn.execute(text(f"DROP SCHEMA IF EXISTS {mirror_schema} CASCADE"))
    conn.execute(text(f"CREATE SCHEMA {mirror_schema}"))
    conn.execute(text(standin.ddl(schema=mirror_schema)))
    if after_mirror_ddl is not None:
        after_mirror_ddl(conn, mirror_schema)

    real_columns = _columns(conn, standin.real_schema, standin.table)
    assert real_columns, (
        f"{standin.real_schema}.{standin.table} was not created by chains "
        f"{list(standin.chains)} -- the stand-in's chain/schema metadata is wrong"
    )

    problems = _describe_drift(
        standin, real_columns, _columns(conn, mirror_schema, standin.table), "column"
    )
    for reader, kind in ((_constraints, "constraint"), (_indexes, "index")):
        problems += _describe_drift(
            standin,
            reader(conn, standin.real_schema, standin.table),
            reader(conn, mirror_schema, standin.table),
            kind,
        )
    problems += _trigger_drift(
        standin,
        _triggers(conn, standin.real_schema, standin.table),
        _triggers(conn, mirror_schema, standin.table),
        real_schema=standin.real_schema,
        mirror_schema=mirror_schema,
    )
    return problems


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.parametrize("standin", list(STANDINS.values()), ids=list(STANDINS))
def test_standin_matches_the_real_migration_chain(parity_db_url: str, standin: TableStandin):
    """Every stand-in table surface, including classified trigger metadata, matches."""
    engine = create_engine(parity_db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            problems = _drift(conn, standin, _PARITY_SCHEMA)
    finally:
        engine.dispose()

    assert not problems, (
        f"The {standin.table} test stand-in has drifted from the "
        f"{'/'.join(standin.chains)} migration chain:\n" + "\n".join(problems) + "\n"
        f"Reconcile {standin.constant_path} with the chain. A stale stand-in does not "
        "fail here in CI -- it fails as a DEGRADED envelope and a downstream KeyError "
        "in whichever integration test uses it (PR #3853)."
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.parametrize(
    "standin",
    (AUTONOMY_APPROVAL_HISTORY, AUTONOMY_SUGGESTIONS, APPROVAL_EVENTS),
    ids=("autonomy_approval_history", "autonomy_suggestions", "approval_events"),
)
async def test_standin_ddl_is_independently_creatable(
    provisioned_postgres_pool, standin: TableStandin
) -> None:
    """A stand-in must not rely on sibling tables absent from its fresh test DB.

    The ``approval_events`` case also exercises its self-contained append-only
    trigger.  Both a public function/table and a first-in-path shadow function
    exist before the schema-qualified mirror is provisioned; this proves an
    altered ``search_path`` cannot hijack the trigger binding.  Repeating the
    same DDL proves the fixture's idempotent drop/recreate semantics.
    """
    async with provisioned_postgres_pool() as pool:
        if standin is not APPROVAL_EVENTS:
            await pool.execute(standin.ddl())
            return

        mirror_schema = "standin_trigger_independent"
        async with pool.acquire() as conn:
            await conn.execute(f"CREATE SCHEMA {mirror_schema}")

            # schema-standin-exempt: conflicting public relation for search_path binding test
            await conn.execute(
                """
                CREATE TABLE public.approval_events (
                    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    action_id UUID,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL
                )
                """
            )
            for schema in ("public", "standin_trigger_shadow"):
                if schema != "public":
                    await conn.execute(f"CREATE SCHEMA {schema}")
                await conn.execute(
                    f"""
                    CREATE OR REPLACE FUNCTION {schema}.prevent_approval_events_mutation()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    AS $$
                    BEGIN
                        RETURN NEW;
                    END;
                    $$
                    """
                )

            public_function_before = await conn.fetchval(
                "SELECT pg_get_functiondef("
                "'public.prevent_approval_events_mutation()'::regprocedure)"
            )
            public_trigger_count_before = await conn.fetchval(
                """
                SELECT count(*)
                FROM pg_trigger trigger_row
                JOIN pg_class table_row ON table_row.oid = trigger_row.tgrelid
                JOIN pg_namespace table_namespace
                    ON table_namespace.oid = table_row.relnamespace
                WHERE table_namespace.nspname = 'public'
                  AND table_row.relname = 'approval_events'
                  AND NOT trigger_row.tgisinternal
                """
            )

            await conn.execute("SET search_path TO standin_trigger_shadow, public")
            await conn.execute(standin.ddl(schema=mirror_schema))

            mirror_function_schema = await conn.fetchval(
                """
                SELECT function_namespace.nspname
                FROM pg_trigger trigger_row
                JOIN pg_class table_row ON table_row.oid = trigger_row.tgrelid
                JOIN pg_namespace table_namespace
                    ON table_namespace.oid = table_row.relnamespace
                JOIN pg_proc function_row ON function_row.oid = trigger_row.tgfoid
                JOIN pg_namespace function_namespace
                    ON function_namespace.oid = function_row.pronamespace
                WHERE table_namespace.nspname = $1
                  AND table_row.relname = 'approval_events'
                  AND trigger_row.tgname = 'trg_approval_events_immutable'
                """,
                mirror_schema,
            )
            assert mirror_function_schema == mirror_schema

            action_id = uuid4()
            event_id = uuid4()
            await conn.execute(
                f"""
                INSERT INTO {mirror_schema}.approval_events
                    (event_id, action_id, event_type, actor)
                VALUES ($1, $2, 'action_queued', 'owner')
                """,
                event_id,
                action_id,
            )
            with pytest.raises(asyncpg.RaiseError, match="append-only: UPDATE"):
                await conn.execute(
                    f"UPDATE {mirror_schema}.approval_events SET actor = 'other' WHERE event_id = $1",
                    event_id,
                )
            with pytest.raises(asyncpg.RaiseError, match="append-only: DELETE"):
                await conn.execute(
                    f"DELETE FROM {mirror_schema}.approval_events WHERE event_id = $1", event_id
                )

            # CREATE OR REPLACE FUNCTION plus DROP/CREATE TRIGGER is the same
            # repeat-safe path used by the approvals fixture.
            await conn.execute(standin.ddl(schema=mirror_schema))

            public_id = uuid4()
            await conn.execute(
                "INSERT INTO public.approval_events (event_id, action_id, event_type, actor) "
                "VALUES ($1, $2, 'action_queued', 'owner')",
                public_id,
                uuid4(),
            )
            await conn.execute(
                "UPDATE public.approval_events SET actor = 'other' WHERE event_id = $1", public_id
            )
            await conn.execute("DELETE FROM public.approval_events WHERE event_id = $1", public_id)

            assert (
                await conn.fetchval(
                    "SELECT pg_get_functiondef("
                    "'public.prevent_approval_events_mutation()'::regprocedure)"
                )
                == public_function_before
            )
            assert (
                await conn.fetchval(
                    """
                    SELECT count(*)
                    FROM pg_trigger trigger_row
                    JOIN pg_class table_row ON table_row.oid = trigger_row.tgrelid
                    JOIN pg_namespace table_namespace
                        ON table_namespace.oid = table_row.relnamespace
                    WHERE table_namespace.nspname = 'public'
                      AND table_row.relname = 'approval_events'
                      AND NOT trigger_row.tgisinternal
                    """
                )
                == public_trigger_count_before
            )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_the_index_diff_can_fail(parity_db_url: str):
    """Dropping a real index from a stand-in must be reported, not tolerated.

    ``ux_pending_actions_active_deduplication_key`` (``approvals_013``) is the
    concrete case: it is a *unique* partial index, so it decides which rows the
    real table accepts.  A stand-in without it accepts writes production
    rejects.  Diffing a deliberately blinded copy proves the index arm of
    :func:`test_standin_matches_the_real_migration_chain` reports that, rather
    than passing the way it did while indexes went unread (bu-cwv9l).
    """
    engine = create_engine(parity_db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            problems = _drift(conn, replace(PENDING_ACTIONS, indexes=()), _BLINDED_SCHEMA)
    finally:
        engine.dispose()

    assert any(
        "MISSING index: ux_pending_actions_active_deduplication_key" in problem
        for problem in problems
    ), (
        "The parity guard did not notice a missing unique partial index. "
        f"It reported: {problems or 'no drift at all'}"
    )


def _without_approval_events_trigger(standin: TableStandin) -> TableStandin:
    return replace(standin, triggers=())


def _with_changed_approval_events_body(standin: TableStandin) -> TableStandin:
    trigger = standin.triggers[0]
    return replace(
        standin,
        triggers=(
            replace(trigger, function_body=trigger.function_body.replace("TG_OP", "TG_OP || ''")),
        ),
    )


def _with_changed_approval_events_timing(standin: TableStandin) -> TableStandin:
    trigger = standin.triggers[0]
    return replace(standin, triggers=(replace(trigger, timing="AFTER"),))


def _with_stale_trigger_exclusion(standin: TableStandin) -> TableStandin:
    return replace(
        standin,
        excluded_triggers=standin.excluded_triggers
        + (
            TriggerExclusion(
                name="trg_approval_events_removed",
                migration="approvals_999",
                reason="falsification-only stale exclusion",
            ),
        ),
    )


def _install_unclassified_trigger(conn: object, schema: str) -> None:
    """Install a mirror-only trigger that has no stand-in classification."""
    assert hasattr(conn, "execute")
    conn.execute(
        text(
            f"""
            CREATE OR REPLACE FUNCTION {schema}.unclassified_approval_event_trigger()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RETURN NEW;
            END;
            $$
            """
        )
    )
    conn.execute(
        text(
            f"""
            CREATE TRIGGER trg_approval_events_unclassified
            BEFORE INSERT ON {schema}.approval_events
            FOR EACH ROW
            EXECUTE FUNCTION {schema}.unclassified_approval_event_trigger()
            """
        )
    )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.parametrize(
    "label,mutate_standin,after_mirror_ddl,needle",
    (
        pytest.param(
            "missing-declaration",
            _without_approval_events_trigger,
            None,
            "UNCLASSIFIED trigger: trg_approval_events_immutable",
            id="missing-declaration",
        ),
        pytest.param(
            "changed-function-body",
            _with_changed_approval_events_body,
            None,
            "MISMATCHED trigger function body: trg_approval_events_immutable",
            id="changed-function-body",
        ),
        pytest.param(
            "changed-timing",
            _with_changed_approval_events_timing,
            None,
            "MISMATCHED trigger event/timing/level: trg_approval_events_immutable",
            id="changed-timing",
        ),
        pytest.param(
            "unclassified-trigger",
            lambda standin: standin,
            _install_unclassified_trigger,
            "UNCLASSIFIED trigger: trg_approval_events_unclassified",
            id="unclassified-trigger",
        ),
        pytest.param(
            "stale-exclusion",
            _with_stale_trigger_exclusion,
            None,
            "STALE trigger exclusion: trg_approval_events_removed",
            id="stale-exclusion",
        ),
    ),
)
def test_the_trigger_diff_can_fail(
    parity_db_url: str,
    label: str,
    mutate_standin: Callable[[TableStandin], TableStandin],
    after_mirror_ddl: Callable[[object, str], None] | None,
    needle: str,
):
    """Every trigger parity/classification arm must fail when deliberately blinded.

    One parameterized falsification test covers the trigger declaration, body,
    execution metadata and named-exclusion contract.  The body mutation is
    intentionally inside ``prosrc``; the guard must report it rather than
    normalizing arbitrary function SQL away.
    """
    assert label
    engine = create_engine(parity_db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            problems = _drift(
                conn,
                mutate_standin(APPROVAL_EVENTS),
                _BLINDED_SCHEMA,
                after_mirror_ddl=after_mirror_ddl,
            )
    finally:
        engine.dispose()

    assert any(needle in problem for problem in problems), (
        f"The trigger parity guard did not report {label}: {needle}. "
        f"It reported: {problems or 'no drift at all'}"
    )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_chain_schema_metadata_can_fail(parity_db_url: str):
    """A wrong real schema must fail loudly instead of making parity vacuous."""
    engine = create_engine(parity_db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            with pytest.raises(
                AssertionError,
                match=r"public\.contact_entity_map was not created by chains",
            ):
                _drift(
                    conn,
                    replace(CONTACT_ENTITY_MAP, real_schema="public"),
                    _BLINDED_SCHEMA,
                )
    finally:
        engine.dispose()


def _exempted(lines: list[str], match_line_index: int) -> bool:
    start = max(0, match_line_index - _EXEMPTION_LOOKBACK_LINES)
    window = lines[start : match_line_index + 1]
    return any(
        line.split(_EXEMPTION_MARKER, 1)[1].strip() for line in window if _EXEMPTION_MARKER in line
    )


@pytest.mark.unit
def test_no_test_hand_rolls_a_standin_table():
    """A further hand-written copy of a stand-in table is refused at source level.

    The three original ``connector_registry`` stand-ins each looked reasonable
    in isolation; the defect only existed across them -- and the twenty-odd
    ``entity_predicate_registry`` copies proved it scales (bu-1ehh1): no two
    agreed on whether the table has a ``kind`` CHECK, so one fixture seeded a
    kind the real chain has never allowed.  Import the shared constant instead,
    or annotate a genuinely different use with
    ``# schema-standin-exempt: <why>`` (as
    ``tests/config/test_init_db_bootstrap.py`` does for its two-column
    GRANT target, which is a privilege fixture rather than a query stand-in).
    """
    search_roots = [_REPO_ROOT / "tests", *sorted(_REPO_ROOT.glob("roster/*/tests"))]
    offenders: list[str] = []
    for standin in STANDINS.values():
        pattern = re.compile(
            rf"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?(\w+\.)?{standin.table}\b",
            re.IGNORECASE,
        )
        for root in search_roots:
            for path in sorted(root.rglob("*.py")):
                lines = path.read_text(encoding="utf-8").splitlines()
                for index, line in enumerate(lines):
                    if pattern.search(line) and not _exempted(lines, index):
                        rel = path.relative_to(_REPO_ROOT)
                        offenders.append(f"  {rel}:{index + 1}: {line.strip()}")

    assert not offenders, (
        "These tests hand-roll a table that already has a single shared "
        "definition:\n" + "\n".join(offenders) + "\nUse the constant in "
        "src/butlers/testing/schema_standins.py so a migration can never leave "
        "one copy stale (bu-r8opr) -- or, for a fixture that genuinely is not a "
        f"query stand-in, put '# {_EXEMPTION_MARKER} <why>' within the "
        f"{_EXEMPTION_LOOKBACK_LINES} lines above it."
    )
