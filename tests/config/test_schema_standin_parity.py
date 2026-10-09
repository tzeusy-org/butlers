"""Real-chain clone/fresh catalog guards replacing independent-table stand-ins.

Legacy node and parameter identities remain traceable. Independent creatability,
FK exclusions and copied trigger definitions are retired mechanisms; complete
real relationships, actual append-only behavior and literal catalog mutation
controls replace them. REQ-testing-053 / REQ-testing-055.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migrated_templates import MigrationStage, template_cache
from butlers.testing.migration import create_migrated_test_db, migration_db_name

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXEMPTION_MARKER = "schema-standin-exempt:"
_EXEMPTION_LOOKBACK_LINES = 8

docker_available = shutil.which("docker") is not None


@dataclass(frozen=True)
class _Relation:
    table: str
    real_schema: str
    stages: tuple[MigrationStage, ...]


_CORE = (MigrationStage("core"),)
_APPROVALS = (*_CORE, MigrationStage("approvals"))
_RELATIONSHIP = (
    *_CORE,
    MigrationStage("memory", schema="relationship"),
    MigrationStage("relationship", schema="relationship"),
)
RELATIONS = {
    relation.table: relation
    for relation in (
        _Relation("connector_registry", "public", (*_CORE, MigrationStage("switchboard"))),
        *(
            _Relation(name, "public", _APPROVALS)
            for name in (
                "pending_actions",
                "autonomy_approval_history",
                "autonomy_suggestions",
                "approval_rules",
                "approval_events",
            )
        ),
        *(
            _Relation(name, "relationship", _RELATIONSHIP)
            for name in (
                "entity_predicate_registry",
                "contact_entity_map",
            )
        ),
        *(
            _Relation(name, "public", _CORE)
            for name in (
                "entity_graph_edges",
                "entity_rebind_log",
            )
        ),
    )
}


@pytest.fixture(scope="module")
def parity_db_url(postgres_container):
    """New per-case clone plus a genuine fresh reference under the SAME principal."""
    cache = template_cache(postgres_container)
    owned = []

    def pair(relation):
        clone = create_migrated_test_db(
            postgres_container, migration_db_name(), stages=relation.stages, fresh=False
        )
        owned.append(clone)
        # This reads clone/global catalogs BEFORE fresh bootstrap can repair a
        # planted defect, then rebuilds all ordered real stages as the same key
        # principal. It never projects ownership or grants catalog equivalence.
        reference = cache.fresh_reference(clone)
        owned.append(reference)
        cache.assert_pristine_clone(clone)
        return clone, reference

    try:
        yield pair
    finally:
        for url in reversed(owned):
            cache.discard_clone(url)


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
            "WHERE n.nspname = :s AND t.relname = :t"
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


def _surface(conn, relation: _Relation) -> dict:
    schema, table = relation.real_schema, relation.table
    columns = _columns(conn, schema, table)
    assert columns, f"{schema}.{table} was not created by chains"
    attributes = conn.execute(
        text(
            "SELECT pg_get_userbyid(c.relowner),c.relacl,c.relrowsecurity,c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=:s AND c.relname=:t"
        ),
        {"s": schema, "t": table},
    ).one()
    policies = tuple(
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT policyname,permissive,roles,cmd,qual,with_check FROM pg_policies "
                "WHERE schemaname=:s AND tablename=:t ORDER BY policyname"
            ),
            {"s": schema, "t": table},
        )
    )
    return {
        "column": columns,
        "constraint": _constraints(conn, schema, table),
        "index": _indexes(conn, schema, table),
        "trigger": _triggers(conn, schema, table),
        "owner-acl-rls": tuple(attributes),
        "policy": policies,
    }


def _differences(real: dict, clone: dict) -> list[str]:
    # Closed relation/field labels only; executable function bodies and role or
    # ACL values stay in memory rather than entering diagnostic messages.
    problems = []
    for kind in sorted(real):
        left, right = real[kind], clone[kind]
        if isinstance(left, dict) and isinstance(right, dict):
            problems.extend(f"MISSING {kind}: {key}" for key in sorted(left.keys() - right.keys()))
            problems.extend(f"EXTRA {kind}: {key}" for key in sorted(right.keys() - left.keys()))
            for key in sorted(left.keys() & right.keys()):
                if left[key] != right[key]:
                    problems.append(f"MISMATCHED {kind}: {key}")
        elif left != right:
            problems.append(f"MISMATCHED {kind}")
    return problems


def _read(url: str, relation: _Relation) -> dict:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            return _surface(connection, relation)
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.parametrize("standin", list(RELATIONS.values()), ids=list(RELATIONS))
def test_standin_matches_the_real_migration_chain(parity_db_url, standin: _Relation):
    """Legacy ten identities now compare full real migrated relationships/security."""
    clone, reference = parity_db_url(standin)
    real, actual = _read(reference, standin), _read(clone, standin)
    assert not _differences(real, actual)
    assert _differences(real, actual) == _differences(actual, real) == []


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.parametrize(
    "standin",
    tuple(
        RELATIONS[name]
        for name in ("autonomy_approval_history", "autonomy_suggestions", "approval_events")
    ),
    ids=("autonomy_approval_history", "autonomy_suggestions", "approval_events"),
)
async def test_standin_ddl_is_independently_creatable(parity_db_url, standin: _Relation):
    """Retired independence is replaced by actual sibling/FK/trigger behavior.

    Existing identities stay, but no independent copied DDL is constructed.
    The append-only binding remains immune to a first-in-path shadow function;
    replay uses the actual migration chain, not a hand-written trigger copy.
    """
    clone, reference = parity_db_url(standin)
    assert not _differences(_read(reference, standin), _read(clone, standin))
    connection = await asyncpg.connect(clone)
    try:
        if standin.table != "approval_events":
            assert await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='approval_rules')"
            )
            return
        await connection.execute("CREATE SCHEMA standin_trigger_shadow")
        await connection.execute("""
            CREATE FUNCTION standin_trigger_shadow.prevent_approval_events_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$
        """)
        await connection.execute("SET search_path TO standin_trigger_shadow, public")
        mirror_function_schema = await connection.fetchval("""
            SELECT function_namespace.nspname FROM pg_trigger trigger_row
            JOIN pg_class table_row ON table_row.oid=trigger_row.tgrelid
            JOIN pg_namespace table_namespace ON table_namespace.oid=table_row.relnamespace
            JOIN pg_proc function_row ON function_row.oid=trigger_row.tgfoid
            JOIN pg_namespace function_namespace ON function_namespace.oid=function_row.pronamespace
            WHERE table_namespace.nspname='public' AND table_row.relname='approval_events'
              AND trigger_row.tgname='trg_approval_events_immutable'
        """)
        mirror_schema = "public"
        assert mirror_function_schema == mirror_schema
        public_function_before = await connection.fetchval(
            "SELECT pg_get_functiondef('public.prevent_approval_events_mutation()'::regprocedure)"
        )
        public_trigger_count_before = await connection.fetchval("""
            SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname='approval_events' AND NOT t.tgisinternal
        """)
        # Actual siblings are present. A valid event uses a real pending parent;
        # the removed independent-table fixture allowed orphan action IDs.
        action_id, event_id = uuid4(), uuid4()
        await connection.execute(
            """
            INSERT INTO public.pending_actions(id,tool_name,tool_args,expires_at)
            VALUES($1,'parity_control','{}'::jsonb,now()+interval '1 hour')
        """,
            action_id,
        )
        await connection.execute(
            """
            INSERT INTO public.approval_events(event_id,action_id,event_type,actor)
            VALUES($1,$2,'action_queued','owner')
        """,
            event_id,
            action_id,
        )
        with pytest.raises(asyncpg.RaiseError, match="append-only: UPDATE"):
            await connection.execute(
                "UPDATE public.approval_events SET actor='other' WHERE event_id=$1", event_id
            )
        with pytest.raises(asyncpg.RaiseError, match="append-only: DELETE"):
            await connection.execute(
                "DELETE FROM public.approval_events WHERE event_id=$1", event_id
            )
        # The qualified bound function and all existing trigger metadata survive
        # a hostile search_path and actual chain replay.
        from butlers.migrations import run_migrations

        await run_migrations(clone, chain="approvals")
        assert (
            await connection.fetchval(
                "SELECT pg_get_functiondef('public.prevent_approval_events_mutation()'::regprocedure)"
            )
            == public_function_before
        )
        assert (
            await connection.fetchval("""
            SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname='approval_events' AND NOT t.tgisinternal
        """)
            == public_trigger_count_before
        )
        # A separately acquired connection proves the allowed insert committed.
        other = await asyncpg.connect(clone)
        try:
            assert (
                await other.fetchval(
                    "SELECT count(*) FROM public.approval_events WHERE event_id=$1", event_id
                )
                == 1
            )
        finally:
            await other.close()
        # The real deferrable cycle admits both related rows in one transaction;
        # a missing sibling still refuses commit, rather than being omitted to
        # make an independent-table stand-in constructible.
        rule_id, cyclic_action_id = uuid4(), uuid4()
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO public.approval_rules(id,tool_name,arg_constraints,created_from)
                VALUES($1,'parity_cycle','{}'::jsonb,$2)
            """,
                rule_id,
                cyclic_action_id,
            )
            await connection.execute(
                """
                INSERT INTO public.pending_actions(id,tool_name,tool_args,approval_rule_id)
                VALUES($1,'parity_cycle','{}'::jsonb,$2)
            """,
                cyclic_action_id,
                rule_id,
            )
        assert (
            await connection.fetchval(
                "SELECT created_from=$2 FROM public.approval_rules WHERE id=$1",
                rule_id,
                cyclic_action_id,
            )
            is True
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO public.approval_rules(tool_name,arg_constraints,created_from)
                    VALUES('parity_orphan','{}'::jsonb,$1)
                """,
                    uuid4(),
                )
        # Orphan writes remain forbidden by the REAL sibling-table trigger.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO public.approval_events(event_id,action_id,event_type,actor)
                VALUES($1,$2,'action_queued','owner')
            """,
                uuid4(),
                uuid4(),
            )
    finally:
        await connection.close()


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_the_index_diff_can_fail(parity_db_url):
    relation = RELATIONS["pending_actions"]
    clone, reference = parity_db_url(relation)
    real = _read(reference, relation)
    engine = create_engine(clone, isolation_level="AUTOCOMMIT")
    name = "ux_pending_actions_active_deduplication_key"
    try:
        with engine.connect() as connection:
            definition = connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname=:name"
                ),
                {"name": name},
            ).scalar_one()
            connection.execute(text(f"DROP INDEX public.{name}"))
            try:
                problems = _differences(real, _surface(connection, relation))
                assert any(
                    "MISSING index: ux_pending_actions_active_deduplication_key" in p
                    for p in problems
                )
                assert any(
                    "EXTRA index: ux_pending_actions_active_deduplication_key" in p
                    for p in _differences(_surface(connection, relation), real)
                )
            finally:
                connection.execute(text(definition))
            # A same-name index with lost UNIQUE authority must also fail;
            # counting index names would miss this exact semantic drift.
            assert "CREATE UNIQUE INDEX" in definition
            connection.execute(text(f"DROP INDEX public.{name}"))
            try:
                connection.execute(
                    text(definition.replace("CREATE UNIQUE INDEX", "CREATE INDEX", 1))
                )
                assert f"MISMATCHED index: {name}" in _differences(
                    real, _surface(connection, relation)
                )
            finally:
                connection.execute(text(f"DROP INDEX public.{name}"))
                connection.execute(text(definition))
            connection.execute(
                text("CREATE INDEX parity_extra_index ON public.pending_actions(id)")
            )
            try:
                assert "EXTRA index: parity_extra_index" in _differences(
                    real, _surface(connection, relation)
                )
            finally:
                connection.execute(text("DROP INDEX public.parity_extra_index"))
            assert not _differences(real, _surface(connection, relation))
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.parametrize(
    "label",
    (
        "missing-declaration",
        "changed-function-body",
        "changed-timing",
        "unclassified-trigger",
        "stale-exclusion",
    ),
)
def test_the_trigger_diff_can_fail(parity_db_url, label):
    """Five existing identities now plant actual full-catalog differences.

    The old named-exclusion mechanism is retired: no trigger is excluded from a
    complete clone/fresh surface. The stale-exclusion identity specifically
    proves a formerly ignorable extra trigger is now a material mismatch.
    """
    relation = RELATIONS["approval_events"]
    clone, reference = parity_db_url(relation)
    real = _read(reference, relation)
    engine = create_engine(clone, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            function = connection.execute(
                text(
                    "SELECT pg_get_functiondef('public.prevent_approval_events_mutation()'::regprocedure)"
                )
            ).scalar_one()
            trigger = connection.execute(
                text("""
                SELECT pg_get_triggerdef(t.oid,true) FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname='approval_events'
                  AND t.tgname='trg_approval_events_immutable'
            """)
            ).scalar_one()
            try:
                if label == "missing-declaration":
                    connection.execute(
                        text("DROP TRIGGER trg_approval_events_immutable ON public.approval_events")
                    )
                    needle = "MISSING trigger: trg_approval_events_immutable"
                elif label == "changed-function-body":
                    assert "TG_OP" in function
                    connection.execute(text(function.replace("TG_OP", "TG_OP || ''")))
                    needle = "MISMATCHED trigger: trg_approval_events_immutable"
                elif label == "changed-timing":
                    assert "BEFORE" in trigger
                    connection.execute(
                        text("DROP TRIGGER trg_approval_events_immutable ON public.approval_events")
                    )
                    connection.execute(text(trigger.replace("BEFORE", "AFTER", 1)))
                    needle = "MISMATCHED trigger: trg_approval_events_immutable"
                else:
                    connection.execute(
                        text("""
                        CREATE FUNCTION public.parity_unclassified_trigger()
                        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$
                    """)
                    )
                    name = (
                        "trg_approval_events_unclassified"
                        if label == "unclassified-trigger"
                        else "trg_approval_events_removed"
                    )
                    connection.execute(
                        text(f"""
                        CREATE TRIGGER {name} BEFORE INSERT ON public.approval_events
                        FOR EACH ROW EXECUTE FUNCTION public.parity_unclassified_trigger()
                    """)
                    )
                    needle = f"EXTRA trigger: {name}"
                problems = _differences(real, _surface(connection, relation))
                assert any(needle in problem for problem in problems)
            finally:
                connection.execute(
                    text(
                        "DROP TRIGGER IF EXISTS trg_approval_events_immutable ON public.approval_events"
                    )
                )
                connection.execute(text(function))
                connection.execute(text(trigger))
                for name in ("trg_approval_events_unclassified", "trg_approval_events_removed"):
                    connection.execute(
                        text(f"DROP TRIGGER IF EXISTS {name} ON public.approval_events")
                    )
                connection.execute(
                    text("DROP FUNCTION IF EXISTS public.parity_unclassified_trigger()")
                )
            assert not _differences(real, _surface(connection, relation))
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_chain_schema_metadata_can_fail(parity_db_url):
    relation = RELATIONS["contact_entity_map"]
    clone, reference = parity_db_url(relation)
    from dataclasses import replace

    engine = create_engine(clone)
    try:
        with engine.connect() as connection:
            with pytest.raises(
                AssertionError, match=r"public\.contact_entity_map was not created by chains"
            ):
                _surface(connection, replace(relation, real_schema="public"))
            assert _surface(connection, relation)["column"]
    finally:
        engine.dispose()
    assert not _differences(_read(reference, relation), _read(clone, relation))


def _exempted(lines: list[str], match_line_index: int) -> bool:
    start = max(0, match_line_index - _EXEMPTION_LOOKBACK_LINES)
    window = lines[start : match_line_index + 1]
    return any(
        line.split(_EXEMPTION_MARKER, 1)[1].strip() for line in window if _EXEMPTION_MARKER in line
    )


@pytest.mark.unit
def test_no_test_hand_rolls_a_standin_table(tmp_path):
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
    for standin in RELATIONS.values():
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
        "definition:\n" + "\n".join(offenders) + "\nRun the actual migration chain rather than "
        "copying chain-owned DDL so a migration can never leave "
        "one copy stale (bu-r8opr) -- or, for a fixture that genuinely is not a "
        f"query stand-in, put '# {_EXEMPTION_MARKER} <why>' within the "
        f"{_EXEMPTION_LOOKBACK_LINES} lines above it."
    )

    # The broader detector follows actual migration inputs and both test roots,
    # including quoted/multiline/dynamic/concatenated identities. This miniature
    # is parser/source evidence, never a migrated database or runtime catalog.
    import json
    from dataclasses import asdict

    from butlers.testing.schema_guard import copied_table_creations, unreviewed_creations

    migration = tmp_path / "alembic/versions/core/current.py"
    migration.parent.mkdir(parents=True)
    migration.write_text("op.create_table('owned_table')\n")
    test = tmp_path / "tests/test_copy.py"
    test.parent.mkdir()
    test.write_text("sql = " + repr('CREATE TABLE "public"."owned_table" (id integer)') + "\n")
    roster_test = tmp_path / "roster/example/tests/test_copy.py"
    roster_test.parent.mkdir(parents=True)
    roster_test.write_text('sql = "CREATE TABLE " + unknown_table + " (id integer)"\n')
    declarations = tmp_path / "exceptions.json"
    declarations.write_text(json.dumps({"schema": 1, "exceptions": []}))
    copies = copied_table_creations(tmp_path)
    assert len(copies) == 2 and {copy.table for copy in copies} == {"owned_table", None}
    assert len(unreviewed_creations(tmp_path, declarations)) == 2
    # A bounded historical declaration binds BOTH complete source and exact
    # literal bytes. A reason comment alone cannot authorize a changed fixture.
    row = asdict(next(copy for copy in copies if copy.path == "tests/test_copy.py"))
    row.pop("line")
    row.update(
        kind="historical", reason="Planted pre-migration parser control", proof="local-control"
    )
    declarations.write_text(json.dumps({"schema": 1, "exceptions": [row]}))
    assert len(unreviewed_creations(tmp_path, declarations)) == 1
    declarations.write_text(json.dumps({"schema": True, "exceptions": [row]}))
    with pytest.raises(ValueError, match="invalid schema fixture exception declaration"):
        unreviewed_creations(tmp_path, declarations)
    declarations.write_text(json.dumps({"schema": 1, "exceptions": [row]}))
    test.write_text(test.read_text() + "changed_fixture = True\n")
    with pytest.raises(ValueError, match="stale schema fixture exception declaration"):
        unreviewed_creations(tmp_path, declarations)
    test.write_text("setup = run_actual_migrations()\n")
    roster_test.write_text("setup = run_actual_migrations()\n")
    declarations.write_text(json.dumps({"schema": 1, "exceptions": []}))
    assert copied_table_creations(tmp_path) == ()
    assert unreviewed_creations(tmp_path, declarations) == ()
