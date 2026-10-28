"""Executing serving admission controls; driver boundaries only are doubled."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from butlers.db import Database
from butlers.db_authority import roles
from butlers.db_authority.roles import AuthorityError, load_runtime_profile

pytestmark = pytest.mark.unit


@contextmanager
def _recording_postgresql_connection(respond):
    """Real SQLAlchemy dispatch; mapping decoding is not a native PostgreSQL proof."""
    import psycopg2
    from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2
    from sqlalchemy.engine import URL, Engine
    from sqlalchemy.pool import StaticPool

    calls = []

    class Cursor:
        description = None
        rowcount = -1

        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=None):
            parameters = {} if parameters is None else parameters
            # Model the mapping-format boundary after the actual PG dialect has
            # supplied its parameters. Do not interpret SQL or emulate PG roles.
            decoded = statement % parameters
            calls.append(
                (
                    statement,
                    dict(parameters) if isinstance(parameters, dict) else tuple(parameters),
                    decoded,
                )
            )
            columns, self.rows = respond(statement, parameters, decoded)
            self.rows = list(self.rows)
            self.description = (
                [(column, None, None, None, None, None, None) for column in columns]
                if columns is not None
                else None
            )

        def fetchone(self):
            return self.rows.pop(0) if self.rows else None

        def fetchall(self):
            rows, self.rows = self.rows, []
            return rows

        def close(self):
            pass

    class Transport:
        notices = []

        def cursor(self):
            return Cursor(self)

        def rollback(self):
            pass

        def commit(self):
            pass

        def close(self):
            pass

    engine = Engine(
        StaticPool(creator=Transport),
        PGDialect_psycopg2(dbapi=psycopg2),
        URL.create("postgresql+psycopg2"),
    )
    try:
        with engine.connect() as connection:
            yield connection, calls
    finally:
        engine.dispose()


def test_actual_dashboard_staging_roundtrips_literal_sql_and_restores_identity():
    from butlers.db_authority import interfaces

    state = {"actor": "previous_actor", "profile": True, "maintenance": True, "fail": None}
    staged = []

    def respond(statement, parameters, decoded):
        if "FROM pg_catalog.pg_database d" in statement:
            return ("database", "owner", "valid"), [
                ("butlers", "normal_migrator", state["profile"])
            ]
        if "current_user=session_user AND rolsuper" in statement:
            return ("valid",), [(state["maintenance"],)]
        if statement.startswith("SELECT current_setting"):
            return ("actor",), [(state["actor"],)]
        if "pg_catalog.set_config" in statement:
            state["actor"] = parameters["owner"]
            return ("actor",), [(state["actor"],)]
        assert decoded == interfaces.DASHBOARD_PURPOSE_SQL
        assert dict(parameters) == {}
        assert state["actor"] == "normal_migrator"
        if state["fail"] is not None:
            raise state["fail"]
        staged.append(decoded)
        return None, []

    with _recording_postgresql_connection(respond) as (connection, calls):
        try:
            interfaces.stage_dashboard_interfaces(connection)
        finally:
            assert state["actor"] == "previous_actor"
        assert staged == [interfaces.DASHBOARD_PURPOSE_SQL]
        assert any(template != decoded for template, _, decoded in calls)
        for guard in ("profile", "maintenance"):
            state[guard] = False
            with pytest.raises(AuthorityError):
                interfaces.stage_dashboard_interfaces(connection)
            state[guard] = True
            assert staged == [interfaces.DASHBOARD_PURPOSE_SQL]
        for primary in (LookupError("recording transport failure"), asyncio.CancelledError()):
            state["fail"] = primary
            with pytest.raises(type(primary)) as error:
                interfaces.stage_dashboard_interfaces(connection)
            assert error.value is primary
            if isinstance(primary, asyncio.CancelledError):
                # SQLAlchemy invalidates a DBAPI connection on BaseException;
                # the real finally attempts restoration and preserves cancel.
                assert connection.invalidated
                assert primary.__notes__ == [
                    "Dashboard maintenance identity restoration was incomplete"
                ]
            else:
                assert state["actor"] == "previous_actor"


def test_actual_protected_installer_roundtrips_all_percent_bodies_and_catalog_checks():
    from butlers.db_authority import interfaces
    from butlers.db_authority.apply import quote_identifier

    names = ("mutate", "reissue_runtime_attention_episode", "append_runtime_attention_condition")
    definitions = tuple(
        next(row for row in interfaces.FUNCTIONS if row.name == name) for name in names
    )
    index = {interfaces._signature(row): row for row in definitions}
    created = {}
    owners = {}
    acls = {}
    installed = []
    valid = {"owners": True, "maintenance": True}

    def respond(statement, parameters, decoded):
        if "WITH owners AS" in statement:
            assert set(parameters["owners"]) == {row.owner for row in definitions}
            assert parameters["count"] == len(set(parameters["owners"]))
            return ("valid",), [(valid["owners"],)]
        if "pg_get_function_identity_arguments" in statement:
            key = parameters["signature"]
            row = index[key]
            return ("oid", "owner", "definer", "config", "body"), (
                [
                    (
                        key,
                        owners[key],
                        row.security_definer,
                        ["search_path=pg_catalog, pg_temp"],
                        row.body,
                    )
                ]
                if key in created
                else []
            )
        if "pg_catalog.aclexplode" in statement:
            return ("role", "privilege", "grantable"), [
                (role, "EXECUTE", False) for role in sorted(acls[parameters["oid"]])
            ]
        if "session_user=current_user AND rolsuper" in statement:
            return ("valid",), [(valid["maintenance"],)]
        for key, row in index.items():
            if decoded == row.sql:
                assert "%" in row.sql
                assert dict(parameters) == {}
                created[key] = row
                owners[key] = "maintenance"
                acls[key] = {"PUBLIC"}
                installed.append(decoded)
                return None, []
            if decoded == f"ALTER FUNCTION {key} OWNER TO {quote_identifier(row.owner)}":
                owners[key] = row.owner
                return None, []
            if decoded == f"REVOKE ALL ON FUNCTION {key} FROM PUBLIC":
                acls[key].remove("PUBLIC")
                return None, []
            for role in row.executors:
                if decoded == f"GRANT EXECUTE ON FUNCTION {key} TO {quote_identifier(role)}":
                    acls[key].add(role)
                    return None, []
        raise AssertionError("unexpected protected installer dispatch")

    with _recording_postgresql_connection(respond) as (connection, calls):
        for guard in ("owners", "maintenance"):
            valid[guard] = False
            with pytest.raises(AuthorityError):
                interfaces.install_functions(connection, definitions)
            valid[guard] = True
            assert installed == []
        interfaces.install_functions(connection, definitions)
        assert installed == [row.sql for row in definitions]
        assert all(owners[key] == row.owner for key, row in index.items())
        assert all(acls[key] == set(row.executors) for key, row in index.items())
        assert all(
            interfaces.function_matches(
                connection, row, interfaces.inspect_function(connection, row)
            )
            for row in definitions
        )
        interfaces.install_functions(connection, definitions)
        assert installed == [row.sql for row in definitions]
        assert len([template for template, _, decoded in calls if decoded in installed]) == 3


def test_actual_ordinary_convergence_roundtrips_percent_bodies_and_retains_admission():
    from butlers.db_authority import apply, interfaces
    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.roles import runtime_profiles

    definitions = tuple(
        row
        for row in compile_function_manifest(
            runtime_profiles("butlers"), "normal_migrator", (("core", None, None),)
        )
        if "%" in row.sql
    )
    assert definitions
    index = {interfaces._signature(row): row for row in definitions}
    catalog = {
        key: dict(oid=key, owner=row.owner, body="previous body") for key, row in index.items()
    }
    acls = {key: {"PUBLIC"} for key in index}
    installed = []
    valid = {"owners": True, "maintenance": True, "catalog": True}
    failure = {"primary": None}

    def respond(statement, parameters, decoded):
        if "WITH owners AS" in statement:
            owners = {row.owner for row in definitions if row.security_definer}
            assert set(parameters["owners"]) == owners
            assert parameters["count"] == len(owners)
            return ("valid",), [(valid["owners"],)]
        if "pg_get_function_identity_arguments" in statement:
            key = parameters["signature"]
            actual, row = catalog[key], index[key]
            return ("oid", "owner", "definer", "config", "body"), [
                (
                    actual["oid"] if valid["catalog"] else "changed_oid",
                    actual["owner"],
                    row.security_definer,
                    list(row.settings),
                    actual["body"],
                )
            ]
        if "pg_catalog.aclexplode" in statement:
            return ("role", "privilege", "grantable"), [
                (role, "EXECUTE", False) for role in sorted(acls[parameters["oid"]])
            ]
        if "current_user=session_user AND rolsuper" in statement:
            return ("valid",), [(valid["maintenance"],)]
        for key, row in index.items():
            if decoded == row.sql:
                assert dict(parameters) == {}
                if failure["primary"] is not None:
                    raise failure["primary"]
                catalog[key]["body"] = row.body
                installed.append(decoded)
                return None, []
            if decoded == f"ALTER FUNCTION {key} OWNER TO {apply.quote_identifier(row.owner)}":
                catalog[key]["owner"] = row.owner
                return None, []
            if decoded == f"REVOKE ALL ON FUNCTION {key} FROM PUBLIC":
                acls[key].remove("PUBLIC")
                return None, []
            for role in row.executors:
                if decoded == f"GRANT EXECUTE ON FUNCTION {key} TO {apply.quote_identifier(role)}":
                    acls[key].add(role)
                    return None, []
        raise AssertionError("unexpected ordinary convergence dispatch")

    population = tuple(
        dict(
            oid=key,
            schema=row.schema,
            name=row.name,
            arguments=row.signature,
            owner=row.owner,
        )
        for key, row in index.items()
    )
    bound = apply.validate_function_population(
        population, definitions, {row.identity for row in definitions}
    )
    with _recording_postgresql_connection(respond) as (connection, calls):
        for guard in valid:
            valid[guard] = False
            with pytest.raises(AuthorityError):
                apply.converge_ordinary_functions(connection, bound)
            valid[guard] = True
            assert installed == []
        primary = LookupError("recording transport failure")
        failure["primary"] = primary
        with pytest.raises(LookupError) as error:
            apply.converge_ordinary_functions(connection, bound)
        assert error.value is primary
        assert installed == []
        failure["primary"] = None
        apply.converge_ordinary_functions(connection, bound)
        assert installed == [row.sql for row in definitions]
        assert all(catalog[key]["owner"] == row.owner for key, row in index.items())
        assert all(
            acls[key] == {role for role in row.executors if role != row.owner}
            for key, row in index.items()
        )
        assert all(
            interfaces.function_matches(
                connection, row, interfaces.inspect_function(connection, row)
            )
            for row in definitions
        )
        apply.converge_ordinary_functions(connection, bound)
        assert installed == [row.sql for row in definitions]
        assert all(template != decoded for template, _, decoded in calls if decoded in installed)


@pytest.mark.parametrize(
    "fault", ["missing", "unlisted", "note_failure", "passed_relations", "cancel"]
)
def test_actual_convergence_relation_notes_preserve_validator_and_error(monkeypatch, fault):
    import json

    from butlers.db_authority import apply
    from butlers.db_authority.grants import compile_relation_manifest, select_migration_manifest
    from butlers.migrations import get_chain_head

    plans = (("chronicler", "chronicler", None),)
    manifest = select_migration_manifest(
        compile_relation_manifest(
            "butlers", "normal_owner", "maintenance", source_database="butlers"
        ),
        plans,
    )
    observed = []
    if fault == "unlisted":
        observed = [
            dict(
                schema="chronicler",
                name="synthetic_unlisted",
                kind="r",
                owner="normal_owner",
                rls=False,
                forced_rls=False,
                oid=1,
                partition=False,
            )
        ]
    if fault == "passed_relations":
        observed = [
            dict(
                schema=row.schema,
                name=row.name,
                kind={"table": "r", "view": "v", "sequence": "S"}[row.kind.value],
                owner=row.owner,
                rls=apply.relation_security_flags(row)[0],
                forced_rls=apply.relation_security_flags(row)[1],
                oid=index,
                partition=False,
            )
            for index, row in enumerate(manifest.relations, 1)
            if row.required
        ]
    primary = asyncio.CancelledError() if fault == "cancel" else TypeError("synthetic downstream")
    calls, originals, original_tracebacks = [], [], []

    def recording(original):
        def invoke(*args, **kwargs):
            calls.append(original.__name__)
            try:
                return original(*args, **kwargs)
            except AuthorityError as error:
                originals.append(error)
                original_tracebacks.append(error.__traceback__)
                raise

        return invoke

    monkeypatch.setattr(
        apply, "bind_partition_relations", recording(apply.bind_partition_relations)
    )
    monkeypatch.setattr(apply, "validate_relations", recording(apply.validate_relations))
    builder = Mock(wraps=apply._relation_failure_note)
    if fault == "note_failure":
        builder.side_effect = RuntimeError("synthetic observer failure")
    monkeypatch.setattr(apply, "_relation_failure_note", builder)

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def one(self):
            return self.rows[0]

        def scalar_one(self):
            return self.one()[0]

        def scalars(self):
            return (row[0] for row in self.rows)

        def mappings(self):
            return self.rows

        def __iter__(self):
            return iter(self.rows)

    purpose_owner_query = "\n        WITH owners AS (SELECT * FROM pg_catalog.pg_roles WHERE rolname=ANY(:owners))\n        SELECT (SELECT count(*)=:count AND bool_and(NOT rolcanlogin AND NOT rolinherit\n          AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb\n          AND NOT rolreplication AND NOT rolbypassrls) FROM owners)\n          AND NOT EXISTS(SELECT FROM pg_catalog.pg_auth_members\n            WHERE roleid IN (SELECT oid FROM owners) OR member IN (SELECT oid FROM owners))\n    "
    retired_audit_query = "\n        SELECT pg_catalog.oidvectortypes(p.proargtypes) AS signature\n        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace\n        WHERE n.nspname='public' AND p.proname='priority_contacts_cascade_audit'\n    "

    class Connection:
        info = {}
        # Explicit ledger fixture state; this is not native PostgreSQL evidence.
        ledgers = {row["oid"]: row for row in observed if row["name"] == "alembic_version"}
        source_ledgers = {
            (row.schema, row.name): row
            for row in manifest.relations
            if row.name == "alembic_version"
        }
        ledger_columns = (("version_num", "character varying(32)", True, "", ""),)
        ledger_defaults = ()
        ledger_constraints = (("p", False, False, True),)
        ledger_indexes = ((True, True, True, True, 1, 1, None, None, ("version_num",)),)
        revision_abi_query = """
            /* owner-lookup-replay:revision-abi */
            SELECT
              (SELECT count(*)=1 AND bool_and(a.attname='version_num'
                AND pg_catalog.format_type(a.atttypid,a.atttypmod)='character varying(32)'
                AND a.attnotnull AND a.attidentity='' AND a.attgenerated='')
               FROM pg_catalog.pg_attribute a
               WHERE a.attrelid=:oid AND a.attnum>0 AND NOT a.attisdropped)
              AND NOT EXISTS(SELECT FROM pg_catalog.pg_attrdef d WHERE d.adrelid=:oid)
              AND (SELECT count(*)=1 AND bool_and(c.contype='p' AND NOT c.condeferrable
                    AND NOT c.condeferred AND c.convalidated)
                   FROM pg_catalog.pg_constraint c WHERE c.conrelid=:oid)
              AND (SELECT count(*)=1 AND bool_and(i.indisprimary AND i.indisunique
                    AND i.indisvalid AND i.indisready AND i.indnkeyatts=1
                    AND i.indnatts=1 AND i.indexprs IS NULL AND i.indpred IS NULL
                    AND ARRAY(SELECT a.attname::text
                        FROM unnest(i.indkey::smallint[]) WITH ORDINALITY k(id,n)
                        JOIN pg_catalog.pg_attribute a
                          ON a.attrelid=i.indrelid AND a.attnum=k.id ORDER BY k.n)
                        =ARRAY['version_num']::text[])
                   FROM pg_catalog.pg_index i WHERE i.indrelid=:oid)
        """

        def exec_driver_sql(self, sql):
            if "FROM pg_catalog.pg_database d" in sql:
                return Result([("butlers", "normal_owner", True)])
            if sql == "SELECT current_database(), session_user":
                return Result([("butlers", "maintenance")])
            if sql == 'SELECT version_num FROM "chronicler"."alembic_version"':
                assert {row["schema"] for row in self.ledgers.values()} == {"chronicler"}
                return Result([(get_chain_head("chronicler"),)])
            assert sql == 'SELECT version_num FROM "chronicler".alembic_version'
            return Result([(get_chain_head("chronicler"),)])

        def execute(self, sql, params=None):
            sql = str(sql)
            if sql == purpose_owner_query:
                assert params == {"owners": ["priority_contact_audit_owner"], "count": 1}
                return Result([(True,)])
            if sql == retired_audit_query:
                assert params is None
                return Result([])
            if sql == self.revision_abi_query:
                assert type(params) is dict and set(params) == {"oid"}
                assert params["oid"] in self.ledgers
                valid = (
                    self.ledger_columns == (("version_num", "character varying(32)", True, "", ""),)
                    and not self.ledger_defaults
                    and self.ledger_constraints == (("p", False, False, True),)
                    and self.ledger_indexes
                    == ((True, True, True, True, 1, 1, None, None, ("version_num",)),)
                )
                return Result([(valid,)])
            if sql in (apply._RELATION_ACL, apply._COLUMN_ACL):
                assert type(params) is dict and set(params) == {"oid"}
                assert params["oid"] in self.ledgers
                ledger = self.ledgers[params["oid"]]
                declared = self.source_ledgers[(ledger["schema"], ledger["name"])]
                rows = []
                columns = sql == apply._COLUMN_ACL
                for permission in declared.permissions:
                    if permission.role == declared.owner or bool(permission.columns) != columns:
                        continue
                    for verb in permission.verbs:
                        if columns:
                            rows.extend(
                                dict(name=name, role=permission.role, verb=verb, grantable=False)
                                for name in permission.columns
                            )
                        else:
                            rows.append(dict(role=permission.role, verb=verb, grantable=False))
                return Result(rows)
            if sql == apply._FUNCTIONS:
                return Result([])
            if sql == apply._RELATIONS:
                return Result(observed)
            if sql == apply._PARTITION_LINKS:
                if fault == "cancel":
                    raise primary
                return Result([])
            assert sql == apply._SEQUENCE_LINKS
            raise primary

    with pytest.raises(BaseException) as caught:
        apply.converge_authority(Connection(), source_database="butlers", plans=plans)
    error = caught.value
    if fault in {"passed_relations", "cancel"}:
        assert error is primary
        assert not hasattr(error, "__notes__")
        builder.assert_not_called()
        assert calls == (
            ["bind_partition_relations", "validate_relations"]
            if fault == "passed_relations"
            else []
        )
        return
    assert error is originals[0]
    assert type(error) is AuthorityError
    traceback = error.__traceback__
    while traceback is not None and traceback is not original_tracebacks[0]:
        traceback = traceback.tb_next
    assert traceback is original_tracebacks[0]
    assert str(error) == (
        "Managed database object is not declared"
        if fault == "unlisted"
        else "Required database object is unavailable"
    )
    assert any(
        frame.name in {"bind_partition_relations", "validate_relations"}
        for frame in __import__("traceback").extract_tb(error.__traceback__)
    )
    if fault == "note_failure":
        assert not hasattr(error, "__notes__")
        return
    if fault == "unlisted":
        assert len(error.__notes__) == 2
        assert error.__notes__[0].startswith("DB_AUTHORITY_PARTITION_BINDING_DIAGNOSTIC ")
    else:
        assert len(error.__notes__) == 1
    relation_notes = [
        note for note in error.__notes__ if note.startswith("DB_AUTHORITY_RELATION_DIAGNOSTIC ")
    ]
    assert len(relation_notes) == 1
    note = relation_notes[0]
    assert all(
        json.dumps(value) not in note for value in ("normal_owner", "maintenance", "chronicler")
    )
    payload = json.loads(note.split(" ", 1)[1])
    assert payload["version"] == 2
    assert payload["phase"] == ("bind_partitions" if fault == "unlisted" else "validate_relations")
    assert payload["missing_required_count"] == sum(row.required for row in manifest.relations)
    assert all(
        set(row) == {"schema_sha256", "name_sha256", "identity_sha256", "declared_kind"}
        for row in payload["missing_required"]
    )
    assert payload["observed_count"] == len(observed)
    if fault == "unlisted":
        assert payload["rows"][0]["declared_identity"] is False


def test_relation_failure_note_bounds_types_and_typed_missing_are_closed(monkeypatch):
    import json

    from butlers.db_authority import apply
    from butlers.db_authority.grants import ObjectAuthority, ObjectKind

    table = ObjectAuthority("synthetic", "same", ObjectKind.TABLE, "normal", (), "synthetic")
    sequence = ObjectAuthority("synthetic", "same", ObjectKind.SEQUENCE, "normal", (), "synthetic")
    actual = dict(
        schema="synthetic", name="same", kind="r", owner="normal", rls=False, forced_rls=False
    )
    context = dict(
        phase="validate_relations", migration_owner="normal", maintenance_owner="maintenance"
    )
    note = apply._relation_failure_note((actual,), (table, sequence), **context)
    payload = json.loads(note.split(" ", 1)[1])
    assert payload["missing_required_count"] == 1
    assert payload["missing_required"][0]["declared_kind"] == "sequence"
    assert payload["rows"][0]["declared_identity"] is True
    for observed, declared in (
        ((actual,) * 4097, (table,)),
        ((actual, actual), (table,)),
        ((), (table, table)),
    ):
        assert apply._relation_failure_note(observed, declared, **context) is None
    error = AuthorityError("original")
    apply._add_relation_failure_note(error, (actual | {"schema": "x" * 1025},), (table,), **context)
    assert not hasattr(error, "__notes__")
    with monkeypatch.context() as scoped:
        scoped.setattr(json, "dumps", lambda *args, **kwargs: "x" * (1024 * 1024))
        assert apply._relation_failure_note((actual,), (table,), **context) is None

    class NoteFailure(AuthorityError):
        def add_note(self, note):
            raise ValueError("synthetic note transport failure")

    error = NoteFailure("original")
    apply._add_relation_failure_note(error, (actual,), (table,), **context)
    assert str(error) == "original" and not hasattr(error, "__notes__")


def test_actual_trigger_shape_query_binds_only_relation_and_preserves_location_normalization():
    from butlers.db_authority import interfaces

    expected = """
SELECT tgname, tgtype, tgenabled, tgfoid, tgargs, tgattr::smallint[] AS attributes,
 tgdeferrable, tginitdeferred, tgconstraint<>0 AS constraint_trigger,
 pg_catalog.regexp_replace(tgqual::text, ':location -?[0-9]+', ':location -1', 'g') AS expression
FROM pg_catalog.pg_trigger
WHERE tgrelid=pg_catalog.to_regclass(:relation) AND NOT tgisinternal
ORDER BY tgname
"""
    relation = "public.protocol_subject"

    def respond(statement, parameters, decoded):
        assert dict(parameters) == {"relation": relation}
        assert statement.replace("%(relation)s", ":relation") == expected
        return ("tgname", "expression"), [("fixed_trigger", "{EXPR :location -1}")]

    with _recording_postgresql_connection(respond) as (connection, calls):
        assert interfaces._shape_rows(connection, interfaces._SHAPE_TRIGGERS, relation) == (
            {"tgname": "fixed_trigger", "expression": "{EXPR :location -1}"},
        )
        assert len(calls) == 1


async def test_connect_rejects_hidden_connecting_login_owner_before_lazy_pool():
    connection = AsyncMock()
    connection.fetchval.return_value = True
    connection.fetchrow.return_value = {
        "identity_ok": True,
        "role_set_ok": False,
        "attributes_ok": True,
        "ownership_ok": False,
        "create_ok": True,
        "target_member": True,
    }
    connection.terminate = Mock()
    database = Database("butlers", schema="general", role="butler_general_rw", min_pool_size=0)
    with (
        patch("butlers.db.asyncpg.connect", AsyncMock(return_value=connection)),
        patch("butlers.db.asyncpg.create_pool", AsyncMock(return_value=Mock())) as create,
    ):
        with pytest.raises(RuntimeError):
            await database.connect()
        create.assert_not_called()
    assert database.pool is None
    connection.terminate.assert_called_once()


def _facts():
    return dict.fromkeys(
        (
            "identity_ok",
            "role_set_ok",
            "attributes_ok",
            "ownership_ok",
            "create_ok",
            "target_member",
        ),
        True,
    )


class _DriverConnection:
    """Driver protocol double, not proof that catalog SQL executed in PostgreSQL."""

    def __init__(self):
        self.facts = _facts()
        self.private_usage = True
        self.hidden_owner = False
        self.calls = []
        self.close = AsyncMock()
        self.terminate = Mock()
        self.set_type_codec = AsyncMock()

    async def fetchval(self, query, *args):
        return self.private_usage if "has_schema_privilege" in query else True

    async def execute(self, query, *args):
        self.calls.append(query)
        return "SET"

    async def fetchrow(self, query, *args):
        self.calls.append(query)
        result = self.facts.copy()
        if self.hidden_owner and "rolname = session_user" in query.split("UNION", 1)[0]:
            result["ownership_ok"] = False
        return result


class _DriverPool:
    def __init__(self, connection, setup):
        self.connection, self.setup = connection, setup
        self.close = AsyncMock()

    @asynccontextmanager
    async def acquire(self):
        await self.setup(self.connection)
        yield self.connection


@pytest.fixture
def driver(monkeypatch):
    connection = _DriverConnection()
    pools = []

    async def create(**kwargs):
        pool = _DriverPool(connection, kwargs.get("setup", AsyncMock()))
        pools.append(pool)
        return pool

    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr("butlers.db.asyncpg.connect", connect)
    monkeypatch.setattr("butlers.db.asyncpg.create_pool", AsyncMock(side_effect=create))
    return connection, pools, connect


def _runtime(schema="general"):
    return Database("butlers", schema=schema, role=f"butler_{schema}_rw", min_pool_size=0)


async def test_connect_and_each_handoff_observe_fresh_authority(driver):
    connection, pools, _ = driver
    database = _runtime()
    pool = await database.connect()
    assert connection.close.await_count == 1  # probe is released before pool creation
    assert pools == [pool]
    async with pool.acquire() as acquired:
        assert acquired is connection
    for fact in ("role_set_ok", "ownership_ok", "create_ok", "attributes_ok", "target_member"):
        connection.facts[fact] = False
        with pytest.raises(RuntimeError):
            async with pool.acquire():
                pytest.fail("unsafe handoff")
        connection.facts[fact] = True
    assert len([q for q in connection.calls if "WITH RECURSIVE" in q]) == 8
    # Probe + first real pool handoff + caller acquisition + five fresh denials.
    await database.close()
    pool.close.assert_awaited_once()


async def test_session_login_graph_not_post_set_role_graph(driver):
    connection, _, _ = driver
    connection.hidden_owner = True
    database = _runtime()
    with pytest.raises(RuntimeError):
        await database.connect()
    assert database.pool is None
    connection.terminate.assert_called_once()


@pytest.mark.parametrize("value", [False, None, 1, "true"])
async def test_nonexact_catalog_fact_refuses(driver, value):
    connection, _, _ = driver
    connection.facts["ownership_ok"] = value
    with pytest.raises(RuntimeError):
        await _runtime().connect()
    connection.terminate.assert_called_once()


async def test_unreadable_and_cancelled_catalog_preserve_cause_and_cleanup(driver):
    connection, _, _ = driver
    cause = ValueError("synthetic-private-driver-detail")
    connection.fetchrow = AsyncMock(side_effect=cause)
    with pytest.raises(RuntimeError) as error:
        await _runtime().connect()
    assert "synthetic-private-driver-detail" not in str(error.value)
    assert error.value.__cause__.__cause__ is cause
    connection.terminate.assert_called_once()
    connection.terminate.reset_mock()
    cancellation = asyncio.CancelledError()
    connection.fetchrow.side_effect = cancellation
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await _runtime().connect()
    assert cancelled.value is cancellation
    connection.terminate.assert_called_once()


@pytest.mark.parametrize("target_schema", ["chronicler_mem", "public"])
async def test_private_child_holds_primary_profile_and_lifetime(driver, monkeypatch, target_schema):
    connection, _, _ = driver
    primary = _runtime("chronicler")
    await primary.connect()
    child = primary.for_owned_schema(target_schema)
    assert child._runtime_profile is primary._runtime_profile
    monkeypatch.setattr(
        "butlers.db.load_runtime_profile", Mock(side_effect=AssertionError("reload"))
    )
    pool = await child.connect()  # actual held primary profile; no re-derived private role
    async with pool.acquire():
        pass
    connection.private_usage = False
    with pytest.raises(RuntimeError):
        async with pool.acquire():
            pytest.fail("missing private USAGE")
    connection.private_usage = True
    original_pool = primary.pool
    primary.pool = _DriverPool(connection, AsyncMock())
    with pytest.raises(RuntimeError):
        async with pool.acquire():
            pytest.fail("replaced primary pool")
    primary.pool = original_pool
    primary._runtime_profile = None
    with pytest.raises(RuntimeError):
        async with pool.acquire():
            pytest.fail("replaced primary profile")
    await primary.close()
    with pytest.raises(RuntimeError):
        async with pool.acquire():
            pytest.fail("closed primary")
    await child.close()


async def test_administrative_path_has_no_roster_or_role_io(driver, monkeypatch):
    _, pools, connect = driver
    monkeypatch.setattr(
        "butlers.db.load_runtime_profile", Mock(side_effect=AssertionError("roster"))
    )
    database = Database("administrative")
    assert await database.connect() is pools[0]
    connect.assert_not_awaited()
    await database.close()


def _write_roster(root, name="general", *, schema="general", database="butlers", private=None):
    child = root / name
    child.mkdir(parents=True, exist_ok=True)
    data = f'[butler]\nname="{name}"\n[butler.db]\nname="{database}"\nschema="{schema}"\n'
    if private is not None:
        data += f'[modules.memory]\nmemory_schema="{private}"\n'
    (child / "butler.toml").write_text(data)


def test_fixed_complete_source_profile_and_immutability():
    profile = load_runtime_profile("butlers", "general", "butler_general_rw")
    assert profile.role in profile.runtime_roles
    assert len(set(profile.runtime_roles)) == len(profile.runtime_roles) > 0
    assert "connector_writer" not in profile.runtime_roles
    with pytest.raises(FrozenInstanceError):
        profile.role = "other"
    private = load_runtime_profile("butlers", "chronicler", "butler_chronicler_rw")
    assert private.private_schemas == ("chronicler_mem",)


@pytest.mark.parametrize(
    "fault",
    ["missing", "empty", "missing_entry", "malformed", "invalid", "duplicate", "private_alias"],
)
def test_roster_errors_are_not_discovery_skips(tmp_path, monkeypatch, fault):
    root = tmp_path / "roster"
    if fault != "missing":
        root.mkdir()
    if fault not in ("missing", "empty"):
        _write_roster(root)
        if fault == "missing_entry":
            (root / "health").mkdir()
        elif fault == "malformed":
            (root / "general/butler.toml").write_text("[broken")
        elif fault == "invalid":
            (root / "general/butler.toml").write_text('[butler]\nname="general"')
        elif fault == "duplicate":
            _write_roster(root, "health")
        elif fault == "private_alias":
            _write_roster(root, "health", schema="health", private="general")
    monkeypatch.setattr(roles, "_ROSTER_ROOT", root)
    with pytest.raises(AuthorityError):
        load_runtime_profile("butlers", "general", "butler_general_rw")


@pytest.mark.parametrize(
    "database,schema,role",
    [
        ("other", "general", "butler_general_rw"),
        ("butlers", "health", "butler_general_rw"),
        ("butlers", "general", "butler_unlisted_rw"),
    ],
)
async def test_unlisted_identity_refuses_before_driver_io(driver, database, schema, role):
    _, _, connect = driver
    with pytest.raises(RuntimeError):
        await Database(database, schema=schema, role=role).connect()
    connect.assert_not_awaited()


def test_interpreter_cache_is_not_a_roster_identity(tmp_path, monkeypatch):
    _write_roster(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    monkeypatch.setattr(roles, "_ROSTER_ROOT", tmp_path)
    profile = load_runtime_profile("butlers", "general", "butler_general_rw")
    assert profile.role == "butler_general_rw"
    (tmp_path / "undeclared").mkdir()
    with pytest.raises(AuthorityError):
        load_runtime_profile("butlers", "general", "butler_general_rw")


@pytest.mark.parametrize(
    "purpose,role",
    [
        ("dashboard", "dashboard_api"),
        ("connector", "connector_writer"),
        ("credential", "credential_store_writer"),
        ("restore_executor", "restore_drill_executor"),
    ],
)
def test_service_purpose_has_only_its_fixed_capability(purpose, role):
    profile = roles.purpose_profile("butlers", purpose)
    assert profile.role == role
    assert profile.runtime_roles == (role,)
    with pytest.raises(FrozenInstanceError):
        profile.role = "butler_general_rw"
    with pytest.raises(AuthorityError):
        roles.purpose_profile("butlers", "butler_general_rw")


async def test_purpose_acquire_does_not_reuse_a_daemon_identity():
    from butlers.db_authority.catalog import setup_purpose

    connection = _DriverConnection()
    profile = roles.purpose_profile("butlers", "dashboard")
    await setup_purpose(connection, profile)
    assert connection.calls[0] == 'SET ROLE "dashboard_api"'
    assert "WITH RECURSIVE" in connection.calls[1]
    connection.facts["role_set_ok"] = False
    with pytest.raises(AuthorityError):
        await setup_purpose(connection, profile)


async def test_actual_connector_hook_refuses_hidden_connecting_owner():
    from butlers.connectors.db_role import connector_setup_role

    connection = _DriverConnection()
    driver_read = connection.fetchval

    async def selected_database_or_catalog(query, *args):
        if query == "SELECT current_database()":
            return "butlers"
        return await driver_read(query, *args)

    connection.fetchval = selected_database_or_catalog
    connection.facts["ownership_ok"] = False
    with pytest.raises(AuthorityError):
        await connector_setup_role(connection)
    connection.facts["ownership_ok"] = True
    await connector_setup_role(connection)
    assert connection.calls[-2] == 'SET ROLE "connector_writer"'


async def test_actual_dashboard_pool_proves_lazy_connection_before_publication(driver):
    from butlers.api.db import DatabaseManager

    connection, pools, _ = driver
    connection.facts["ownership_ok"] = False
    manager = DatabaseManager(min_pool_size=0, max_pool_size=1)
    with pytest.raises(AuthorityError):
        await manager._create_pool(database="butlers", log_name="synthetic", schema="general")
    assert len(pools) == 1
    pools[0].close.assert_awaited_once()
    assert manager._pools == {}


async def test_purpose_pool_rechecks_each_acquisition_and_preserves_cancellation(driver):
    from butlers.db_authority.catalog import create_purpose_pool

    connection, pools, _ = driver
    profile = roles.purpose_profile("butlers", "credential")
    pool = await create_purpose_pool(profile, database="butlers", min_size=0)
    async with pool.acquire() as acquired:
        assert acquired is connection
    connection.facts["attributes_ok"] = False
    with pytest.raises(AuthorityError):
        async with pool.acquire():
            pytest.fail("Unproved credential handoff escaped")
    connection.facts["attributes_ok"] = True
    cancel = asyncio.CancelledError()
    connection.fetchrow = AsyncMock(side_effect=cancel)
    with pytest.raises(asyncio.CancelledError) as caught:
        await create_purpose_pool(profile, database="butlers", min_size=0)
    assert caught.value is cancel
    pools[-1].close.assert_awaited_once()


async def test_actual_credential_factory_refuses_an_unproved_writer(driver, monkeypatch):
    from butlers.credential_store import create_system_global_credential_store_from_env

    connection, pools, _ = driver
    monkeypatch.setenv(
        "BUTLERS_CREDENTIAL_DATABASE_URL", "postgresql://credential:synthetic@localhost/butlers"
    )
    connection.facts["ownership_ok"] = False
    with pytest.raises(AuthorityError):
        await create_system_global_credential_store_from_env()
    pools[0].close.assert_awaited_once()


@pytest.mark.parametrize("cancel", [False, True])
async def test_unpublished_native_pool_is_terminated_after_failed_first_handoff(
    monkeypatch, cancel
):
    import asyncpg

    from butlers.db_authority.catalog import create_purpose_pool

    failure = asyncio.CancelledError() if cancel else AuthorityError("Synthetic admission refusal")
    calls = []

    async def refuse_connection(*args, **kwargs):
        calls.append("connect")
        raise failure

    native_create = asyncpg.create_pool
    pool = native_create(min_size=0, max_size=1, connect=refuse_connection)
    monkeypatch.setattr(asyncpg, "create_pool", lambda **options: pool)
    with pytest.raises(type(failure)) as caught:
        await create_purpose_pool(roles.purpose_profile("butlers", "dashboard"), min_size=0)
    assert caught.value is failure
    assert type(pool) is asyncpg.Pool
    assert calls == ["connect"]
    assert pool._closed is True
    assert all(holder._con is None for holder in pool._holders)


async def test_native_pool_failed_eager_initialization_owns_late_sibling_connection():
    import asyncpg

    from butlers.db_authority.catalog import create_purpose_pool

    third_entered = asyncio.Event()
    settle = asyncio.Event()
    connections = []
    count = 0
    failure = AuthorityError("Synthetic initialization failure")

    class DriverConnection(asyncpg.Connection):
        """Native class protocol boundary only; no initialized wire connection."""

        def __init__(self):
            self.close = AsyncMock()
            self.terminate = Mock()

        def __del__(self):
            pass

    async def connect(*args, **kwargs):
        nonlocal count
        count += 1
        ordinal = count
        if ordinal == 2:
            await third_entered.wait()
            raise failure
        if ordinal == 3:
            third_entered.set()
            while not settle.is_set():
                try:
                    await settle.wait()
                except asyncio.CancelledError:
                    # Faithfully exercise a driver boundary that settles late.
                    pass
        connection = DriverConnection()
        connections.append(connection)
        return connection

    try:
        with pytest.raises(AuthorityError) as caught:
            await asyncio.wait_for(
                create_purpose_pool(
                    roles.purpose_profile("butlers", "dashboard"),
                    min_size=3,
                    max_size=3,
                    max_inactive_connection_lifetime=0,
                    connect=connect,
                ),
                timeout=1,
            )
        assert caught.value is failure
    finally:
        settle.set()
    for _ in range(8):
        await asyncio.sleep(0)
    assert len(connections) == 2
    for connection in connections:
        connection.terminate.assert_called()


async def test_actual_database_does_not_publish_failed_lazy_native_pool(monkeypatch):
    import asyncpg

    failure = AuthorityError("Synthetic native initialization refusal")

    async def refuse_connection(*args, **kwargs):
        raise failure

    pool = asyncpg.create_pool(min_size=0, max_size=1, connect=refuse_connection)
    monkeypatch.setattr(asyncpg, "create_pool", lambda **options: pool)
    database = Database("synthetic", min_pool_size=0, max_pool_size=1)
    try:
        with pytest.raises(AuthorityError) as caught:
            await database.connect()
        assert caught.value is failure
        assert database.pool is None
        assert pool._closed is True
    finally:
        pool.terminate()


async def test_actual_dashboard_authority_never_certifies_a_connected_subset(driver):
    from butlers.api.db import DatabaseManager

    connection, _, _ = driver
    manager = DatabaseManager(min_pool_size=0, max_pool_size=1)
    assert await manager.authority_status() == "unavailable"
    await manager.add_butler("general", db_name="butlers", db_schema="general", modules=frozenset())
    await manager.set_credential_shared_pool("butlers", "public")
    assert await manager.authority_status() == "admitted"
    manager.register_configured_modules("health", frozenset())
    assert await manager.authority_status() == "unavailable"
    await manager.add_butler("health", db_name="butlers", db_schema="health", modules=frozenset())
    connection.facts["ownership_ok"] = False
    assert await manager.authority_status() == "unavailable"
    connection.facts["ownership_ok"] = True
    assert await manager.authority_status() == "admitted"
    await manager.close()


def test_privileged_disposable_compiler_does_not_relax_serving_identity():
    compiled = roles.compile_deployment_profiles("test_disposable", source_database="butlers")
    source = roles.runtime_profiles("butlers")
    assert len(compiled) == len(source) == 13
    for actual, original in zip(compiled, source, strict=True):
        assert actual.database == "test_disposable"
        assert (actual.role, actual.primary_schema, actual.private_schemas, actual.modules) == (
            original.role,
            original.primary_schema,
            original.private_schemas,
            original.modules,
        )
    with pytest.raises(AuthorityError):
        roles.compile_deployment_profiles("test_disposable", source_database="unlisted")
    with pytest.raises(AuthorityError):
        roles.load_runtime_profile("test_disposable", "general", "butler_general_rw")
    with pytest.raises(AuthorityError):
        roles.load_runtime_profile("butlers", "general", "unlisted_role")
    with pytest.raises(AuthorityError):
        roles.purpose_profile("unlisted", "dashboard")


def test_catalog_classification_refuses_missing_unknown_kind_owner_and_rls():
    from butlers.db_authority.apply import validate_relations
    from butlers.db_authority.grants import ObjectAuthority, ObjectKind

    expected = ObjectAuthority("general", "state", ObjectKind.TABLE, "migrator", (), "core")
    actual = dict(
        schema="general",
        name="state",
        kind="r",
        owner="migrator",
        rls=False,
        forced_rls=False,
        oid=1,
    )
    assert validate_relations([actual], (expected,)) == ((actual, expected),)
    with pytest.raises(AuthorityError):
        validate_relations([], (expected,))
    for change in (dict(name="undeclared"), dict(kind="f"), dict(owner="runtime")):
        with pytest.raises(AuthorityError):
            validate_relations([actual | change], (expected,))
    protected = ObjectAuthority(
        "general", "state", ObjectKind.TABLE, "migrator", (), "core", forced_rls=True
    )
    with pytest.raises(AuthorityError):
        validate_relations([actual | dict(rls=True)], (protected,))
    assert validate_relations([actual | dict(rls=True, forced_rls=True)], (protected,))


async def test_actual_credential_writer_cannot_fall_back_to_runtime_credentials(
    driver, monkeypatch
):
    from butlers.credential_store import create_system_global_credential_store_from_env

    _, pools, connect = driver
    monkeypatch.delenv("BUTLERS_CREDENTIAL_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime:synthetic@localhost/butlers")
    with pytest.raises(AuthorityError):
        await create_system_global_credential_store_from_env()
    assert pools == []
    connect.assert_not_called()
    monkeypatch.setenv(
        "BUTLERS_CREDENTIAL_DATABASE_URL", "postgresql://credential:synthetic@localhost/unlisted"
    )
    with pytest.raises(AuthorityError):
        await create_system_global_credential_store_from_env()
    assert pools == []


def test_protected_function_maintenance_refuses_without_existing_operator_authority():
    from butlers.db_authority.interfaces import FUNCTIONS, install_functions

    definition = FUNCTIONS[0]
    connection = Mock()
    result = Mock()
    result.scalar_one.return_value = True  # Existing isolated NOLOGIN owner profile.
    result.mappings.return_value.one_or_none.return_value = None
    connection.execute.return_value = result
    connection.exec_driver_sql.return_value.scalar_one.return_value = False
    with pytest.raises(AuthorityError, match="maintenance prerequisite"):
        install_functions(connection, (definition,))
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert statements == [
        "SELECT session_user=current_user AND rolsuper "
        "FROM pg_catalog.pg_roles WHERE rolname=session_user"
    ]
    result.scalar_one.return_value = False
    connection.exec_driver_sql.reset_mock()
    with pytest.raises(AuthorityError, match="owner profile"):
        install_functions(connection, (definition,))
    connection.exec_driver_sql.assert_not_called()


@pytest.mark.parametrize(
    "mismatch", [None, "columns", "constraints", "indexes", "policies", "triggers"]
)
def test_actual_protected_shape_comparison_refuses_drift_and_drops_only_owned_scratch(mismatch):
    from butlers.db_authority.interfaces import verify_protected_relation_shapes

    class Result:
        def __init__(self, rows=()):
            self.rows = rows

        def scalar_one(self):
            return True

        def mappings(self):
            return iter(self.rows)

    class Connection:
        def __init__(self):
            self.statements = []
            self.reads = []

        def exec_driver_sql(self, sql):
            self.statements.append(sql)
            return Result()

        def execute(self, sql, params):
            sql = str(sql)
            relation = params["relation"]
            self.reads.append((sql, relation))
            if "relforcerowsecurity AS forced" in sql:
                return Result(
                    (
                        {
                            "owner": "dnd_generation_owner",
                            "enabled": relation.endswith('"user_context"'),
                            "forced": relation.endswith('"user_context"'),
                        },
                    )
                )
            kind = (
                "policies"
                if "pg_catalog.pg_policy" in sql
                else "triggers"
                if "pg_catalog.pg_trigger" in sql
                else "indexes"
                if "pg_catalog.pg_index" in sql
                else "constraints"
                if "pg_catalog.pg_constraint" in sql
                else "columns"
            )
            # Catalog responses belong to the driver boundary. No installer,
            # comparison, source definitions or cleanup method is patched.
            value = "drift" if kind == mismatch and relation.startswith('"public".') else "source"
            return Result(({"attname": "synthetic_column", "catalog_shape": value},))

    connection = Connection()
    if mismatch is None:
        verify_protected_relation_shapes(connection, "dnd")
        assert len(connection.reads) == 33  # Three owners plus both sides of five catalogs.
    else:
        with pytest.raises(AuthorityError, match="differ[s]? from source"):
            verify_protected_relation_shapes(connection, "dnd")
    created = [sql for sql in connection.statements if sql.startswith("CREATE TEMPORARY TABLE")]
    dropped = [sql for sql in connection.statements if sql.startswith("DROP TABLE")]
    assert created and len(created) == len(dropped)
    for create, drop in zip(created, dropped, strict=True):
        name = create.split('"', 2)[1]
        assert name.startswith("_butlers_shape_")
        assert drop == f'DROP TABLE pg_temp."{name}"'
    assert not any(
        sql.startswith(("UPDATE ", "DELETE ", "ALTER TABLE public"))
        for sql in connection.statements
    )


def test_actual_protected_shape_cleanup_preserves_primary_failure():
    from butlers.db_authority.interfaces import verify_protected_relation_shapes

    primary = LookupError("synthetic catalog unavailable")
    cleanup = OSError("synthetic scratch cleanup unavailable")
    connection = Mock()
    connection.exec_driver_sql.return_value.scalar_one.return_value = True

    def read(sql, params):
        if "relforcerowsecurity AS forced" in str(sql):
            return Mock(
                mappings=lambda: (
                    {"owner": "dnd_generation_owner", "enabled": False, "forced": False},
                )
            )
        raise primary

    connection.execute.side_effect = read

    def execute(sql):
        if sql.startswith("DROP TABLE"):
            raise cleanup
        return Mock(scalar_one=lambda: True)

    connection.exec_driver_sql.side_effect = execute
    with pytest.raises(LookupError) as caught:
        verify_protected_relation_shapes(connection, "dnd")
    assert caught.value is primary
    assert primary.__notes__ == ["Protected source shape cleanup was incomplete"]
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert any(sql.startswith("CREATE TEMPORARY TABLE") for sql in statements)
    assert any(sql.startswith('DROP TABLE pg_temp."_butlers_shape_') for sql in statements)


def test_compiled_relation_manifest_preserves_real_private_and_narrow_reader_authority():
    from butlers.db_authority.grants import compile_relation_manifest

    manifest = compile_relation_manifest(
        "butlers", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    objects = {(row.schema, row.name): row for row in manifest.relations}
    assert len(manifest.profiles) == 13
    assert objects["chronicler_mem", "facts"].owner == "migration_selected"
    private = {permission.role for permission in objects["chronicler_mem", "facts"].permissions}
    assert private == {"butler_chronicler_rw", "dashboard_api"}
    inbox = {
        permission.role: permission.verbs
        for permission in objects["switchboard", "message_inbox"].permissions
    }
    for reader in ("relationship", "messenger", "travel"):
        assert inbox[f"butler_{reader}_rw"] == ("SELECT",)
    assert "butler_concierge_rw" not in inbox
    registry = {
        permission.role: permission.verbs
        for permission in objects["switchboard", "connector_registry"].permissions
    }
    assert registry["connector_writer"] == ("SELECT", "INSERT", "UPDATE")
    for identity in (
        ("education", "sessions"),
        ("general", "calendar_event_instances"),
        ("general", "calendar_sources"),
        ("general", "calendar_events"),
        ("general", "calendar_event_entities"),
    ):
        assert {permission.role: permission.verbs for permission in objects[identity].permissions}[
            "butler_chronicler_rw"
        ] == ("SELECT",)
    assert all(
        permission.role != "butler_chronicler_rw"
        for permission in objects["education", "state"].permissions
    )
    assert any(
        permission.role == "butler_concierge_rw"
        for permission in objects["concierge", "v_fleet_sessions"].permissions
    )
    assert objects["public", "user_context"].forced_rls is True
    assert objects["public", "user_context"].owner == "dnd_generation_owner"
    assert objects["dashboard_attention", "bootstrap_configuration"].owner == "maintenance_selected"
    assert ("public", "contacts") not in objects
    assert ("public", "facts") not in objects
    assert all(
        permission.role != "butler_calendar_rw"
        for row in manifest.relations
        for permission in row.permissions
    )
    for owner in ("butler_general_rw", "dnd_generation_owner", "dashboard_api"):
        with pytest.raises(AuthorityError):
            compile_relation_manifest(
                "butlers", owner, "maintenance_selected", source_database="butlers"
            )
    with pytest.raises(AuthorityError):
        compile_relation_manifest("butlers", "same", "same", source_database="butlers")
    with pytest.raises(AuthorityError):
        compile_relation_manifest(
            "butlers", "migration_selected", "maintenance_selected", source_database="unlisted"
        )


def test_source_partition_children_are_closed_and_sequences_bind_the_declared_parent():
    from butlers.db_authority.apply import (
        bind_partition_relations,
        validate_relations,
        validate_sequence_bindings,
    )
    from butlers.db_authority.grants import ObjectAuthority, ObjectKind, Permission

    parent = ObjectAuthority(
        "switchboard",
        "message_inbox",
        ObjectKind.TABLE,
        "migration_selected",
        (Permission("butler_switchboard_rw", ("SELECT", "INSERT")),),
        "registered ingest",
    )
    child = dict(
        schema="switchboard",
        name="message_inbox_209901",
        kind="r",
        owner="migration_selected",
        oid=20,
        partition=True,
        rls=False,
        forced_rls=False,
    )
    link = dict(oid=20, parent_schema="switchboard", parent_name="message_inbox")
    declared = bind_partition_relations((child,), (parent,), (link,))
    assert declared[0] is parent
    assert declared[1].permissions == ()
    assert validate_relations(
        (child,), tuple(__import__("dataclasses").replace(row, required=False) for row in declared)
    )
    for actual, links in (
        (child | dict(partition=False), (link,)),
        (child | dict(kind="v"), (link,)),
        (child | dict(owner="butler_switchboard_rw"), (link,)),
        (child | dict(schema="public"), (link,)),
        (child, ()),
        (child, (link, link)),
        (child, (link | dict(parent_name="unlisted_parent"),)),
    ):
        with pytest.raises(AuthorityError):
            bind_partition_relations((actual,), (parent,), links)
    sequence = ObjectAuthority(
        "public",
        "audit_log_id_seq",
        ObjectKind.SEQUENCE,
        "migration_selected",
        (),
        "core audit identity",
        sequence_parent=("public", "audit_log", "id"),
    )
    actual = dict(oid=21)
    binding = dict(
        oid=21,
        parent_schema="public",
        parent_name="audit_log",
        parent_column="id",
        parent_owner="migration_selected",
        deptype="a",
    )
    validate_sequence_bindings(((actual, sequence),), (binding,))
    for bindings in (
        (),
        (binding, binding),
        (binding | dict(parent_column="other"),),
        (binding | dict(parent_owner="foreign_owner"),),
    ):
        with pytest.raises(AuthorityError):
            validate_sequence_bindings(((actual, sequence),), bindings)

    # Bind the sequence emitted by actual core_096, not a sequence invented for
    # its separate TEXT-keyed retention-policy table.
    import runpy
    from pathlib import Path

    from butlers.db_authority.grants import compile_relation_manifest, select_migration_manifest

    emitted = []
    producer = runpy.run_path(
        str(
            Path(__file__).parents[2]
            / "alembic/versions/core/core_096_memory_retention_policies.py"
        )
    )
    with patch("alembic.op.execute", side_effect=emitted.append):
        producer["upgrade"]()
    statements = [" ".join(sql.split()) for sql in emitted]
    assert any(
        "CREATE TABLE IF NOT EXISTS public.memory_compaction_log ( id BIGSERIAL PRIMARY KEY," in sql
        for sql in statements
    )
    assert any(
        "CREATE TABLE IF NOT EXISTS public.memory_retention_policies ( kind TEXT PRIMARY KEY,"
        in sql
        for sql in statements
    )
    manifest = select_migration_manifest(
        compile_relation_manifest(
            "test_selected", "migration_selected", "maintenance_selected", source_database="butlers"
        ),
        (("core", None, None),),
    )
    rows = {row.identity: row for row in manifest.relations}
    compaction = rows["public", "memory_compaction_log_id_seq", "sequence", ""]
    assert compaction.owner == "migration_selected"
    assert compaction.sequence_parent == ("public", "memory_compaction_log", "id")
    assert compaction.required is False
    assert ("public", "memory_retention_policies_id_seq", "sequence", "") not in rows
    assert set(compaction.permissions) == {
        Permission(profile.role, ("USAGE",)) for profile in manifest.profiles
    }
    observed = dict(
        schema="public",
        name="memory_compaction_log_id_seq",
        kind="S",
        owner="migration_selected",
        oid=22,
        partition=False,
        rls=False,
        forced_rls=False,
    )
    assert bind_partition_relations((observed,), manifest.relations, ()) == manifest.relations
    bound = validate_relations((observed,), (compaction,))
    dependency = binding | dict(oid=22, parent_name="memory_compaction_log")
    validate_sequence_bindings(bound, (dependency,))
    for name in ("memory_retention_policies_id_seq", "unlisted_sequence"):
        with pytest.raises(AuthorityError):
            bind_partition_relations((observed | dict(name=name),), manifest.relations, ())
    for changed in (dict(kind="r"), dict(owner="foreign_owner"), dict(schema="foreign_schema")):
        with pytest.raises(AuthorityError):
            validate_relations((observed | changed,), (compaction,))
    for dependencies in (
        (),
        (dependency, dependency),
        (dependency | dict(parent_schema="foreign_schema"),),
        (dependency | dict(parent_name="memory_retention_policies"),),
        (dependency | dict(parent_column="kind"),),
        (dependency | dict(parent_owner="foreign_owner"),),
    ):
        with pytest.raises(AuthorityError):
            validate_sequence_bindings(bound, dependencies)


def test_selected_source_manifest_preserves_core_only_private_and_historical_boundaries():
    from butlers.db_authority.grants import compile_relation_manifest, select_migration_manifest

    full = compile_relation_manifest(
        "test_selected", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    private = select_migration_manifest(full, (("memory", "chronicler_mem", None),))
    assert private.schemas == ("chronicler_mem",)
    private_rows = {row.name: row for row in private.relations}
    assert private_rows["facts"].required is True
    assert private_rows["facts"].permissions == next(
        row.permissions
        for row in full.relations
        if (row.schema, row.name) == ("chronicler_mem", "facts")
    )
    for retired in ("embedding_versions", "rule_applications"):
        assert private_rows[retired].required is False
        assert private_rows[retired].permissions == ()
        assert not any(row.name == retired for row in full.relations)
    core = select_migration_manifest(full, (("core", None, None),))
    assert not ({"chronicler", "chronicler_mem", "switchboard"} & set(core.schemas))
    assert any(row.schema == "public" and row.name == "scheduled_tasks" for row in core.relations)
    assert any(
        row.schema == "public" and row.name == "audit_log" and row.required
        for row in core.relations
    )
    assert not any(
        row.schema == "public" and row.name == "scheduled_tasks" and row.permissions
        for row in core.relations
    )
    historical = select_migration_manifest(full, (("memory", "chronicler_mem", "mem_001"),))
    assert all(not row.required and not row.permissions for row in historical.relations)
    isolated = select_migration_manifest(full, (("memory", "isolated_admin", None),))
    assert isolated.schemas == ("isolated_admin",)
    assert all(not row.permissions for row in isolated.relations)
    assert any(row.name == "facts" and row.required for row in isolated.relations)
    production = compile_relation_manifest(
        "butlers", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    with pytest.raises(AuthorityError):
        select_migration_manifest(production, (("memory", "isolated_admin", None),))
    for plans in (
        (),
        (("core", "chronicler_mem", None),),
        (("memory", "dashboard_auth", None),),
        (("unlisted_chain", "chronicler", None),),
        (("health", "chronicler", None),),
        (("memory", "chronicler_mem", object()),),
    ):
        with pytest.raises(AuthorityError):
            select_migration_manifest(full, plans)


def test_selected_revision_proof_uses_actual_chain_graph_and_same_held_driver():
    from butlers.db_authority.apply import verify_selected_revisions
    from butlers.migrations import get_chain_head

    head = get_chain_head("memory")
    connection = Mock()
    connection.exec_driver_sql.return_value = [(head,), ("core_280",)]
    verify_selected_revisions(connection, (("memory", "chronicler_mem", None),))
    connection.exec_driver_sql.assert_called_once_with(
        'SELECT version_num FROM "chronicler_mem".alembic_version'
    )
    for versions in ((), (("mem_001",),), ((head,), ("mem_001",))):
        connection.exec_driver_sql.return_value = versions
        with pytest.raises(AuthorityError, match="not installed"):
            verify_selected_revisions(connection, (("memory", "chronicler_mem", None),))
    connection.reset_mock()
    with pytest.raises(AuthorityError, match="not source-owned"):
        verify_selected_revisions(connection, (("memory", "chronicler_mem", "core_280"),))
    connection.exec_driver_sql.assert_not_called()
    with pytest.raises(AuthorityError, match="plan is required"):
        verify_selected_revisions(connection, None)


def test_function_manifest_binds_fixed_overloads_to_actual_consumers_and_selected_chains():
    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.roles import PURPOSE_OWNERS, runtime_profiles

    profiles = runtime_profiles("butlers")
    plans = (
        ("core", "general", None),
        ("switchboard", "switchboard", None),
        ("memory", "chronicler_mem", None),
        ("approvals", "relationship", None),
        ("general", "general", None),
    )
    rows = compile_function_manifest(profiles, "migration_selected", plans)
    indexed = {row.identity: row for row in rows}
    assert len(indexed) == len(rows)
    assert indexed["public", "qa_local_schedule_policy", ""].executors == ("butler_qa_rw",)
    assert ("public", "butler_local_schedule_policy", "") in indexed
    own_policy = indexed["public", "butler_local_schedule_policy", ""]
    assert own_policy.executors == tuple(profile.role for profile in profiles)
    assert own_policy.owner == "registry_control_owner"
    assert own_policy.security_definer and own_policy.signature == ""
    assert own_policy.settings == ("search_path=pg_catalog, pg_temp",)
    assert (
        own_policy.producer == "roster/switchboard/migrations/053_butler_local_schedule_policy.py"
    )
    import runpy
    from pathlib import Path

    emitted = []
    producer = runpy.run_path(str(Path(__file__).parents[2] / own_policy.producer))
    driver = Mock()
    driver.exec_driver_sql.return_value.scalar_one.return_value = "switchboard"
    with (
        patch("alembic.op.get_bind", return_value=driver),
        patch("alembic.op.execute", side_effect=emitted.append),
    ):
        producer["upgrade"]()
    driver.exec_driver_sql.assert_called_once_with("SELECT current_schema()")
    header, rest = emitted[0].split("AS $fn$", 1)
    body, terminator = rest.split("$fn$;", 1)
    assert not terminator.strip()
    assert own_policy.header.strip() == header.strip()
    assert own_policy.body == body
    assert "current_setting('role', true)" in body
    assert "WHERE p.name = v_name" in body
    assert "current_user =" not in body and "session_user" not in body
    assert not any(word in body.upper().split() for word in ("INSERT", "UPDATE", "DELETE"))
    assert emitted[1].strip() == (
        "REVOKE ALL ON FUNCTION public.butler_local_schedule_policy() FROM PUBLIC"
    )
    assert "rolname ~ '^butler_[a-z][a-z0-9_]*_rw$'" in emitted[2]
    isolated = compile_function_manifest(
        profiles, "migration_selected", (("switchboard", "isolated_registry", None),)
    )
    isolated = next(row for row in isolated if row.name == "butler_local_schedule_policy")
    assert isolated.body == body.replace('"switchboard"', '"isolated_registry"')
    assert isolated.executors == own_policy.executors
    from butlers.db_authority.apply import validate_function_population
    from butlers.db_authority.interfaces import function_matches

    observed = dict(
        oid=713,
        schema="public",
        name="butler_local_schedule_policy",
        arguments="",
        owner=own_policy.owner,
    )
    assert validate_function_population((observed,), (own_policy,), (own_policy.identity,)) == (
        (observed, own_policy),
    )
    for wrong in (dict(name="unlisted_policy"), dict(arguments="text"), dict(owner="foreign")):
        with pytest.raises(AuthorityError):
            validate_function_population((observed | wrong,), (own_policy,), ())
    actual = dict(
        oid=713,
        owner=own_policy.owner,
        definer=True,
        config=own_policy.settings,
        body=own_policy.body,
    )
    acl = [dict(role=role, privilege="EXECUTE", grantable=False) for role in own_policy.executors]
    connection = Mock()
    connection.execute.return_value.mappings.return_value = acl
    assert function_matches(connection, own_policy, actual)
    for wrong in (
        dict(body=body.replace("current_setting('role', true)", "current_user")),
        dict(body=body.replace("WHERE p.name = v_name", "WHERE true")),
        dict(config=("search_path=public",)),
        dict(definer=False),
        dict(owner="migration_selected"),
    ):
        assert not function_matches(connection, own_policy, actual | wrong)
    for wrong_acl in (
        acl + [dict(role="PUBLIC", privilege="EXECUTE", grantable=False)],
        acl + [dict(role="dashboard_api", privilege="EXECUTE", grantable=False)],
        [dict(row, grantable=True) for row in acl],
    ):
        connection.execute.return_value.mappings.return_value = wrong_acl
        assert not function_matches(connection, own_policy, actual)
    assert indexed["public", "reserve_butler_probe", "text"].executors == (
        "butler_switchboard_rw",
        "dashboard_api",
    )
    for name in ("reserve_butler_probe", "record_butler_probe", "set_butler_registry_policy"):
        definition = next(row for row in rows if row.name == name)
        assert "session_user = current_user" not in definition.body
        assert "'dashboard_api'" in definition.body
    assert indexed[
        "public", "enqueue_premise_amendments", "text,text,timestamp with time zone,text"
    ].executors == tuple(p.role for p in profiles)
    assert indexed["chronicler_mem", "record_episode_tombstone", ""].executors == ()
    approval = indexed["relationship", "validate_approval_event_action_reference", ""]
    assert 'FROM "relationship".pending_actions' in approval.body
    assert (
        "reconciled row is final" in indexed["public", "qa_publication_operations_guard", ""].body
    )
    assert indexed["dashboard_auth", "cleanup", ""].executors == ("dashboard_auth_api",)
    allowed = (
        {p.role for p in profiles}
        | set(PURPOSE_OWNERS)
        | {
            "dashboard_api",
            "dashboard_auth_api",
            "connector_writer",
            "credential_store_writer",
            "migration_selected",
        }
    )
    assert all(set(row.executors) <= allowed for row in rows)
    assert all(
        row.owner in PURPOSE_OWNERS and row.owner != "migration_selected"
        for row in rows
        if row.security_definer
    )
    assert {row.name for row in rows if "migration_selected" in row.executors} == {
        "host",
        "cost_claim_restore_row",
        "ensure_registry_control",
        "switchboard_connector_heartbeat_log_drop_expired_partitions",
        "switchboard_message_inbox_drop_expired_partitions",
    }
    assert all(
        row.settings
        in {
            ("search_path=pg_catalog, pg_temp",),
            ("row_security=on", "search_path=pg_catalog, pg_temp"),
        }
        for row in rows
    )
    assert all(row.producer and row.signature is not None for row in rows)
    assert (
        compile_function_manifest(profiles, "migration_selected", (("core", None, "core_198"),))
        == ()
    )


def test_function_population_and_convergence_refuse_ambiguous_catalog_before_effects():
    from types import SimpleNamespace

    from butlers.db_authority.apply import (
        converge_ordinary_functions,
        validate_function_population,
    )
    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.interfaces import _FUNCTION_ACL, _FUNCTION_CATALOG
    from butlers.db_authority.roles import runtime_profiles

    definition = next(
        row
        for row in compile_function_manifest(
            runtime_profiles("butlers"), "migration_selected", (("core", None, None),)
        )
        if row.name == "resolve_relationship_prepared_action_status"
    )
    observed = dict(
        oid=71, schema="public", name=definition.name, arguments="uuid", owner=definition.owner
    )
    bound = validate_function_population((observed,), (definition,), {definition.identity})
    for population, required in (
        ((), {definition.identity}),
        ((observed, observed), {definition.identity}),
        ((observed | {"owner": "hostile_owner"},), {definition.identity}),
        ((observed | {"arguments": "text"},), {definition.identity}),
        ((observed | {"name": "unlisted_function"},), {definition.identity}),
    ):
        with pytest.raises(AuthorityError):
            validate_function_population(population, (definition,), required)

    class CatalogConnection:
        def __init__(self, maintenance, owner_profile=True):
            self.maintenance = maintenance
            self.owner_profile = owner_profile
            self.row = dict(
                oid=71,
                owner=definition.owner,
                definer=True,
                config=["search_path=public"],
                body="old body",
            )
            self.acl = [dict(role="PUBLIC", privilege="EXECUTE", grantable=False)]
            self.effects = []

        def execute(self, statement, parameters):
            if "WITH owners AS" in str(statement):
                assert parameters == {"owners": ["relationship_status_owner"], "count": 1}
                return SimpleNamespace(scalar_one=lambda: self.owner_profile)
            if str(statement) == _FUNCTION_CATALOG:
                return SimpleNamespace(
                    mappings=lambda: SimpleNamespace(one_or_none=lambda: self.row)
                )
            if str(statement) == _FUNCTION_ACL:
                return SimpleNamespace(mappings=lambda: tuple(self.acl))
            raise AssertionError("unexpected catalog read")

        def exec_driver_sql(self, statement):
            if statement.startswith("SELECT current_user=session_user AND rolsuper"):
                return SimpleNamespace(scalar_one=lambda: self.maintenance)
            self.effects.append(statement)
            if statement == definition.sql:
                self.row.update(body=definition.body, config=list(definition.settings))
            elif statement.startswith("ALTER FUNCTION"):
                assert statement.endswith('OWNER TO "relationship_status_owner"')
                self.row["owner"] = definition.owner
            elif statement.startswith("REVOKE ALL ON FUNCTION"):
                self.acl.clear()
            elif statement.startswith("GRANT EXECUTE ON FUNCTION"):
                self.acl.append(
                    dict(role="butler_switchboard_rw", privilege="EXECUTE", grantable=False)
                )
            else:
                raise AssertionError("unexpected catalog mutation")

    refused = CatalogConnection(False)
    with pytest.raises(AuthorityError):
        converge_ordinary_functions(refused, bound)
    assert refused.effects == []
    connection = CatalogConnection(True)
    converge_ordinary_functions(connection, bound)
    assert connection.effects[0] == definition.sql
    assert len(connection.effects) == 4  # Fixed owner transfer is an explicit effect.
    assert connection.acl == [
        dict(role="butler_switchboard_rw", privilege="EXECUTE", grantable=False)
    ]
    connection.effects.clear()
    converge_ordinary_functions(connection, bound)
    assert connection.effects == []
    unsafe_owner = CatalogConnection(True, owner_profile=False)
    with pytest.raises(AuthorityError, match="owner profile"):
        converge_ordinary_functions(unsafe_owner, bound)
    assert unsafe_owner.effects == []
    predecessor = observed | {"owner": "migration_selected"}
    assert validate_function_population(
        (predecessor,), (definition,), {definition.identity}, migration_owner="migration_selected"
    )
    with pytest.raises(AuthorityError):
        validate_function_population((predecessor,), (definition,), {definition.identity})
    previous_owner = CatalogConnection(True)
    previous_owner.row["owner"] = "migration_selected"
    previous_bound = validate_function_population(
        (predecessor,), (definition,), {definition.identity}, migration_owner="migration_selected"
    )
    converge_ordinary_functions(previous_owner, previous_bound)
    assert previous_owner.row["owner"] == "relationship_status_owner"
    assert any(
        statement.endswith('OWNER TO "relationship_status_owner"')
        for statement in previous_owner.effects
    )
    connection.row["oid"] = 72
    with pytest.raises(AuthorityError):
        converge_ordinary_functions(connection, bound)
    assert connection.effects == []


@pytest.mark.parametrize("catalog_ok", [False, None, 1, True])
async def test_real_runtime_handoff_rechecks_object_acl_after_successful_role_proof(catalog_ok):
    from butlers.db_authority.catalog import _CATALOG_OBJECT_SQL

    connection = AsyncMock()
    connection.fetchrow.return_value = dict.fromkeys(
        (
            "identity_ok",
            "role_set_ok",
            "attributes_ok",
            "ownership_ok",
            "create_ok",
            "target_member",
        ),
        True,
    )
    connection.fetchval.return_value = catalog_ok
    database = Database("butlers", schema="general", role="butler_general_rw", min_pool_size=0)
    database._runtime_profile = load_runtime_profile("butlers", "general", "butler_general_rw")
    database._role_verified = True
    database._verified_role = database.role
    if catalog_ok is True:
        await database._setup_connection(connection)
    else:
        with pytest.raises(RuntimeError, match="Runtime database role acquisition failed"):
            await database._setup_connection(connection)
    connection.execute.assert_awaited_once_with('SET ROLE "butler_general_rw"')
    assert connection.fetchrow.await_count == 1
    statement, bound = connection.fetchval.call_args.args
    assert statement == _CATALOG_OBJECT_SQL
    import json

    authority = json.loads(bound)
    assert all("body" not in row for row in authority["functions"])
    assert any(row["name"] == "context_dnd_mutate" for row in authority["functions"])
    assert all(
        not isinstance(executor, str) or not executor.startswith("manifest_")
        for row in authority["functions"]
        for executor in row["executors"]
    )
    # A prior successful handoff is not a cache: the next acquisition reads
    # fresh role and object facts and must refuse a revoked catalogue grant.
    if catalog_ok is True:
        connection.fetchval.return_value = False
        with pytest.raises(RuntimeError, match="Runtime database role acquisition failed"):
            await database._setup_connection(connection)
        assert connection.fetchval.await_count == 2


@pytest.mark.parametrize(
    ("name", "enabled", "forced"),
    [("task_continuity", True, True), ("insight_feedback", True, False), ("state", False, False)],
)
def test_actual_source_manifest_binds_both_row_security_flags(name, enabled, forced):
    """A grant convergence may neither disable nor silently add an RLS boundary."""
    from butlers.db_authority.apply import validate_relations
    from butlers.db_authority.grants import public_objects

    row = next(row for row in public_objects("butlers", "normal_owner") if row.name == name)
    actual = dict(
        schema="public",
        name=name,
        kind="r",
        owner="normal_owner",
        oid=42,
        rls=enabled,
        forced_rls=forced,
    )
    assert validate_relations([actual], (row,)) == ((actual, row),)
    for changed in (actual | {"rls": not enabled}, actual | {"forced_rls": not forced}):
        with pytest.raises(AuthorityError):
            validate_relations([changed], (row,))


def test_ha_mapping_manifest_preserves_actual_core246_rls_and_policy_identities(monkeypatch):
    """Execute the landed producer; catalog declarations preserve its exact boundary."""
    import runpy
    from pathlib import Path

    from butlers.db_authority.apply import validate_relations
    from butlers.db_authority.grants import (
        relation_security_flags,
        security_expression_tokens,
        serving_catalog_contract,
        shared_reader_objects,
    )

    producer = runpy.run_path(
        str(
            Path(__file__).resolve().parents[2] / "alembic/versions/core/"
            "core_246_ha_person_mapping_receipts.py"
        )
    )
    emitted = []
    monkeypatch.setattr(producer["op"], "execute", lambda sql: emitted.append(str(sql)))
    producer["upgrade"]()
    normalized = [" ".join(sql.split()) for sql in emitted]
    identity = ("connectors", "home_assistant_persons")
    assert producer["_MAPPINGS"] == ".".join(identity)
    assert f"ALTER TABLE {producer['_MAPPINGS']} ENABLE ROW LEVEL SECURITY" in normalized
    assert f"ALTER TABLE {producer['_MAPPINGS']} NO FORCE ROW LEVEL SECURITY" in normalized
    row = next(
        row for row in shared_reader_objects("normal_owner") if (row.schema, row.name) == identity
    )
    assert relation_security_flags(row) == (True, False)
    actual = dict(
        schema=identity[0],
        name=identity[1],
        kind="r",
        owner="normal_owner",
        oid=42,
        rls=True,
        forced_rls=False,
    )
    assert validate_relations([actual], (row,)) == ((actual, row),)
    for changed in (actual | {"rls": False}, actual | {"forced_rls": True}):
        with pytest.raises(AuthorityError):
            validate_relations([changed], (row,))
    policies = [
        policy
        for policy in serving_catalog_contract("butlers")["policies"]
        if policy["name"] in {"ha_person_mappings_select", "ha_person_mappings_insert"}
    ]
    assert {(policy["schema"], policy["table"]) for policy in policies} == {identity}
    assert len(policies) == 2
    by_name = {policy["name"]: policy for policy in policies}
    read = by_name["ha_person_mappings_select"]
    write = by_name["ha_person_mappings_insert"]
    assert (read["command"], read["roles"], read["permissive"], read["check"]) == (
        "r",
        ["PUBLIC"],
        True,
        None,
    )
    assert read["using"] == security_expression_tokens(
        "(CURRENT_USER = ANY (ARRAY['butler_switchboard_rw'::name, 'butler_chronicler_rw'::name]))"
    )
    assert (write["command"], write["roles"], write["permissive"], write["using"]) == (
        "a",
        ["PUBLIC"],
        True,
        None,
    )
    assert write["check"] == security_expression_tokens(
        "(CURRENT_USER = 'butler_switchboard_rw'::name)"
    )
    assert (
        "CREATE POLICY ha_person_mappings_select ON connectors.home_assistant_persons "
        "FOR SELECT TO PUBLIC USING (current_user IN ('butler_switchboard_rw', 'butler_chronicler_rw'))"
    ) in normalized
    assert (
        "CREATE POLICY ha_person_mappings_insert ON connectors.home_assistant_persons "
        "FOR INSERT TO PUBLIC WITH CHECK (current_user = 'butler_switchboard_rw')"
    ) in normalized


def test_pinned_predecessor_retirement_never_adopts_discovered_function_authority():
    from butlers.db_authority.interfaces import (
        _RETIRED_PREDECESSOR_FUNCTIONS,
        classify_retired_interfaces,
        retire_predecessor_interfaces,
    )

    observed = [
        dict(schema=r[0], name=r[1], signature=r[2], digest=r[3], owner=r[4] or "maintenance")
        for r in _RETIRED_PREDECESSOR_FUNCTIONS
    ]
    selected = classify_retired_interfaces(observed, "maintenance")
    assert len(selected) == 23
    for change in (
        dict(name="unlisted"),
        dict(signature="text,text"),
        dict(digest="0" * 64),
        dict(owner="untrusted"),
    ):
        with pytest.raises(AuthorityError):
            classify_retired_interfaces([observed[0] | change], "maintenance")
    with pytest.raises(AuthorityError):
        classify_retired_interfaces([observed[0], observed[0]], "maintenance")
    connection = Mock()
    retire_predecessor_interfaces(connection, (selected, ("dnd_generation_admin",), True))
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert statements[0].startswith("DROP TRIGGER runtime_attention_plant_legacy")
    assert sum(statement.startswith("DROP FUNCTION ") for statement in statements) == 23
    assert statements[-2:] == [
        'DROP TABLE "dnd_generation_admin".bootstrap_configuration RESTRICT',
        'DROP SCHEMA "dnd_generation_admin" RESTRICT',
    ]
    assert all("CASCADE" not in statement for statement in statements)
    assert not any("DELETE" in statement or "UPDATE" in statement for statement in statements)


def _predecessor_capsule():
    """Only authority metadata; no user rows, credential values or SQL input."""
    import hashlib
    import json

    from butlers.db_authority.apply import _PREDECESSOR_COMMIT, predecessor_revision_vectors
    from butlers.db_authority.grants import compile_relation_manifest

    manifest = compile_relation_manifest(
        "butlers", "normal_owner", "maintenance", source_database="butlers"
    )
    catalog = dict(
        database=dict(name="butlers", owner="maintenance", acl=[]),
        schemas=[],
        relations=[],
        functions=[],
        memberships=[],
        defaults=[],
        roles=[
            dict(
                name="normal_owner",
                login=True,
                inherit=False,
                super=False,
                createdb=False,
                createrole=False,
                replication=False,
                bypassrls=False,
            )
        ],
        versions=[
            dict(schema=schema, revisions=list(revisions))
            for schema, revisions in sorted(predecessor_revision_vectors(manifest).items())
        ],
        configurations=[],
    )
    capsule = dict(
        format="butlers-authority-rollback-v1",
        predecessor_commit=_PREDECESSOR_COMMIT,
        migration_role="normal_owner",
        actor="maintenance",
        release_references=dict(
            chart="chart-previous", image="image-previous", old_secret="key-v1"
        ),
        catalog=catalog,
        catalog_sha256=hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest(),
    )
    return manifest, capsule


@pytest.mark.parametrize("fault", ["foreign_role", "privileged_role", "duplicate", "configuration"])
def test_preserving_rollback_rejects_unbound_metadata_before_effects(fault):
    import hashlib
    import json

    from butlers.db_authority.apply import validate_rollback_capsule

    manifest, capsule = _predecessor_capsule()
    validate_rollback_capsule(capsule, manifest, "maintenance")
    catalog = capsule["catalog"]
    if fault == "foreign_role":
        catalog["roles"][0]["name"] = "unlisted_foreign_principal"
    elif fault == "privileged_role":
        catalog["roles"][0]["super"] = True
    elif fault == "duplicate":
        catalog["roles"].append(catalog["roles"][0].copy())
    else:
        catalog["configurations"].append(dict(schema="public", values=[dict(sql="untrusted")]))
    capsule["catalog_sha256"] = hashlib.sha256(
        json.dumps(catalog, sort_keys=True).encode()
    ).hexdigest()
    with pytest.raises(AuthorityError):
        validate_rollback_capsule(capsule, manifest, "maintenance")


@pytest.mark.parametrize("fault", [None, "changed-owner", "active-serving"])
def test_actual_r0_executes_pinned_initializer_and_requires_independent_catalog_readback(fault):
    """SQL protocol only: the genuine old-profile GRANT proof is a separate PG case."""
    import hashlib
    import json
    from types import SimpleNamespace

    from butlers.db_authority import apply

    _, capsule = _predecessor_capsule()
    catalog = capsule["catalog"]
    catalog["database"]["acl"] = [dict(role="normal_owner", verb="CONNECT", grantable=False)]
    capsule["catalog_sha256"] = hashlib.sha256(
        json.dumps(catalog, sort_keys=True).encode()
    ).hexdigest()

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def one(self):
            return self.value

        def mappings(self):
            return self

        def __iter__(self):
            return iter(self.value)

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            assert sql == apply.pinned_predecessor_initializer()
            assert "validated preserving operator rollback required" in sql
            assert "\\set ON_ERROR_STOP" not in sql
            db.effects.append("pinned-init")
            # A driver-bound response, not a simulated proof that SQL grants
            # work. Final readback MUST observe it rather than assuming success.
            db.database_acl = catalog["database"]["acl"].copy()

    class Connection:
        def __init__(self):
            self.info = {}
            self.effects = []
            self.database_acl = []
            self.connection = SimpleNamespace(driver_connection=SimpleNamespace(cursor=Cursor))

        def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            if "pg_locks" in sql:
                return Result(True)
            if "WITH RECURSIVE principals" in sql:
                return Result(fault != "active-serving")
            if "current_setting" in sql:
                return Result("normal_owner")
            if "set_config" in sql:
                return Result(None)
            if sql == apply._ROLLBACK_DATABASE_ACL:
                return Result(self.database_acl)
            if sql == apply._ROLLBACK_ROLES:
                return Result(catalog["roles"])
            if sql in {
                apply._NAMESPACE_CATALOG,
                apply._PARTITION_LINKS,
                apply._RELATIONS,
                apply._ROLLBACK_FUNCTIONS,
                apply._ROLLBACK_MEMBERSHIPS,
                apply._ROLLBACK_EFFECTIVE_DEFAULTS,
            }:
                return Result([])
            if "to_regclass(:target) IS NOT NULL" in sql:
                return Result(
                    any(
                        params["target"] == '"' + row["schema"] + '".alembic_version'
                        for row in catalog["versions"]
                    )
                )
            raise AssertionError("unexpected actual source query")

        def exec_driver_sql(self, sql):
            if sql.startswith("SELECT current_user=session_user AND rolsuper"):
                return Result(True)
            if sql == "SHOW search_path":
                return Result('"$user", public')
            if sql == "SELECT session_user":
                return Result("maintenance")
            if sql == "SELECT current_database(), session_user":
                return Result(("butlers", "maintenance"))
            for row in catalog["versions"]:
                if sql == 'SELECT version_num FROM "' + row["schema"] + '".alembic_version':
                    return Result([(revision,) for revision in row["revisions"]])
            if "FROM pg_catalog.pg_database d JOIN" in sql:
                owner = "foreign_owner" if fault == "changed-owner" else "maintenance"
                return Result(SimpleNamespace(one=lambda: dict(name="butlers", owner=owner)))
            if "set_config" in sql:
                return Result(None)
            raise AssertionError("unexpected actual source command")

    # The database query uses mappings().one(); retain that native result shape.
    old_mappings = Result.mappings

    def mappings(self):
        return self.value if isinstance(self.value, SimpleNamespace) else old_mappings(self)

    Result.mappings = mappings
    db = Connection()
    if fault is not None:
        with pytest.raises(AuthorityError):
            apply.replay_predecessor_r0(db, capsule)
        assert db.effects == []
    else:
        receipt = apply.replay_predecessor_r0(db, capsule)
        assert receipt["transition"] == "R0"
        assert db.effects == ["pinned-init"]
        assert db.database_acl == catalog["database"]["acl"]


def test_actual_selected_core_schemas_keep_source_wrappers_without_extra_serving_grants():
    from butlers.db_authority.apply import predecessor_function_sources
    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.roles import compile_deployment_profiles

    profiles = compile_deployment_profiles("butlers", source_database="butlers")
    rows = compile_function_manifest(
        profiles,
        "normal_owner",
        (("core", "general", None), ("core", "qa", None)),
    )
    local = [r for r in rows if r.name == "connectors_filtered_events_ensure_partition"]
    assert {r.schema for r in local} == {"general", "qa", "connectors"}
    assert all(r.executors == () for r in local if r.schema in {"general", "qa"})
    assert all(r.owner == "connector_partition_owner" for r in local)
    old = predecessor_function_sources(profiles)
    general = [
        sql
        for key, sql in old.items()
        if key[:2] == ("general", "connectors_filtered_events_ensure_partition")
    ]
    assert len(general) == 1
    assert "SET search_path = connectors, public, pg_temp" in general[0]
    assert "SET search_path = pg_catalog, pg_temp" not in general[0]
    assert not any(
        key[:2] == ("chronicler_mem", "connectors_filtered_events_ensure_partition") for key in old
    )


def test_preserving_reverse_detaches_foreign_reach_without_adopting_its_identity():
    from butlers.db_authority import apply

    manifest, capsule = _predecessor_capsule()
    commands = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return iter(self.rows)

        def scalar_one(self):
            return 160000

    class Connection:
        def execute(self, sql, params):
            assert str(sql) == apply._ROLLBACK_MEMBERSHIPS
            assert "maintenance" not in params["roles"]
            return Result(
                [
                    dict(role="butler_chronicler_rw", member="new_serving_login"),
                    dict(role="foreign_capability", member="butler_chronicler_rw"),
                ]
            )

        def exec_driver_sql(self, sql):
            commands.append(sql)
            return Result([])

    capsule["catalog"]["memberships"] = [
        dict(
            role="butler_chronicler_rw", member="normal_owner", admin=False, inherit=True, set=True
        )
    ]
    apply._restore_predecessor_role_state(Connection(), capsule["catalog"], manifest, "maintenance")
    assert commands[:2] == [
        'REVOKE "butler_chronicler_rw" FROM "new_serving_login"',
        'REVOKE "foreign_capability" FROM "butler_chronicler_rw"',
    ]
    assert commands[-1] == (
        'GRANT "butler_chronicler_rw" TO "normal_owner" WITH INHERIT TRUE, SET TRUE'
    )
    assert all(
        not sql.startswith(("DROP ", 'ALTER ROLE "new_serving_login"', 'GRANT "foreign'))
        for sql in commands
    )


@pytest.mark.parametrize("fault", [None, "login", "membership", "owner", "default"])
def test_reverse_readback_accepts_only_inert_new_source_roles(fault):
    from copy import deepcopy

    from butlers.db_authority import apply

    manifest, capsule = _predecessor_capsule()
    expected = capsule["catalog"]
    current = deepcopy(expected)
    role = "credential_store_writer"
    current["roles"].append(
        dict(
            name=role,
            login=False,
            inherit=False,
            super=False,
            createdb=False,
            createrole=False,
            replication=False,
            bypassrls=False,
        )
    )
    current["defaults"] = [
        dict(owner=role, schema=None, kind="f", role="PUBLIC", verb="EXECUTE", grantable=False)
    ]
    if fault == "login":
        current["roles"][-1]["login"] = True
    elif fault == "membership":
        current["memberships"].append(dict(role=role, member="new_login"))
    elif fault == "owner":
        current["schemas"].append(dict(name="public", owner=role, acl=[]))
    elif fault == "default":
        current["defaults"][0]["role"] = "butler_chronicler_rw"
    if fault:
        with pytest.raises(AuthorityError):
            apply._predecessor_catalog_projection(current, expected, manifest, "maintenance")
    else:
        projected = apply._predecessor_catalog_projection(
            current, expected, manifest, "maintenance"
        )
        assert apply._catalog_equivalent(projected, expected)
        assert len(current["roles"]) == 2  # no cluster-wide DROP/mutation hidden by comparison


@pytest.mark.parametrize("fault", [None, "body", "owner", "settings", "missing"])
def test_reverse_callable_preflight_is_bound_to_live_fixed_sources(fault):
    import hashlib

    from butlers.db_authority import apply
    from butlers.db_authority.grants import compile_function_manifest, deployment_function_plans
    from butlers.db_authority.interfaces import final_functions

    manifest, capsule = _predecessor_capsule()
    ordinary = compile_function_manifest(
        manifest.profiles, manifest.migration_owner, deployment_function_plans(manifest.profiles)
    )[0]
    projection = next(
        row
        for row in final_functions(manifest.profiles, manifest.migration_owner)
        if row.name == "write_audit_projection"
    )

    def row(definition):
        return dict(
            schema=definition.schema,
            name=definition.name,
            signature=definition.signature,
            owner=definition.owner,
            definer=definition.security_definer,
            settings=list(getattr(definition, "settings", ("search_path=pg_catalog, pg_temp",))),
            digest=hashlib.sha256(definition.body.encode()).hexdigest(),
            acl=[],
        )

    current = dict(functions=[row(ordinary), row(projection)])
    predecessor = dict(functions=[row(ordinary)])
    if fault == "body":
        current["functions"][0]["digest"] = "0" * 64
    elif fault == "owner":
        current["functions"][0]["owner"] = "foreign_owner"
    elif fault == "settings":
        current["functions"][0]["settings"] = ["search_path=public"]
    elif fault == "missing":
        current["functions"].pop()
    if fault:
        with pytest.raises(AuthorityError):
            apply._verify_r1_callable_sources(manifest, current, predecessor)
    else:
        apply._verify_r1_callable_sources(manifest, current, predecessor)


@pytest.mark.parametrize("stage", range(6))
def test_actual_attention_installer_advances_only_verified_source_states_without_reset(stage):
    """Driver protocol control; migrated old-stamp/data cases remain genuine PG work."""

    from butlers.db_authority import interfaces
    from butlers.db_authority.roles import compile_deployment_profiles

    profiles = compile_deployment_profiles("butlers", source_database="butlers")
    definitions = {
        f'"{row.schema}"."{row.name}"({row.signature})': row
        for row in interfaces.final_functions(profiles, "normal_owner")
    }

    class Result:
        def __init__(self, value=None):
            self.value = value

        def mappings(self):
            return self

        def one_or_none(self):
            return self.value

        def scalar_one(self):
            return self.value

        def __iter__(self):
            return iter(self.value or ())

    class Connection:
        def __init__(self):
            self.names = set(interfaces._ATTENTION_STAGE_NAMES.get(stage, ()))
            self.marker = stage == 5
            self.scratch_markers = set()
            self.commands = []
            self.owner_selects = []
            self.acls = {
                signature: set(row.executors) - {row.owner}
                for signature, row in definitions.items()
            }
            self.bodies = {signature: row.body for signature, row in definitions.items()}
            for signature, row in definitions.items():
                if row.name == "runtime_attention_outbox_guard":
                    self.bodies[signature] = row.body.replace(
                        "    IF TG_OP = 'DELETE' THEN\n        RETURN OLD;\n    END IF;\n", ""
                    )

        def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            if "count(*)=:count" in sql:
                return Result(True)
            if "WHERE p.oid=pg_catalog.to_regprocedure(:signature)" in sql:
                definition = definitions[params["signature"]]
                return Result(
                    dict(
                        oid=params["signature"],
                        owner=definition.owner,
                        definer=definition.security_definer,
                        config=["search_path=pg_catalog, pg_temp"],
                        body=self.bodies[params["signature"]],
                    )
                )
            if sql == interfaces._FUNCTION_ACL:
                definition = definitions[params["oid"]]
                return Result(
                    [
                        dict(role=role, privilege="EXECUTE", grantable=False)
                        for role in sorted(self.acls[params["oid"]])
                    ]
                )
            if "tgname='runtime_attention_condition_marker_sync_trigger'" in sql:
                return Result(self.marker)
            if "WHERE n.nspname=:schema AND c.relname=:name" in sql:
                self.owner_selects.append(params["name"])
                for group, rows in interfaces._PROTECTED_GROUPS.items():
                    for schema, name, owner in rows:
                        if (schema, name) == (params["schema"], params["name"]):
                            present = group != "attention" or name in self.names
                            return Result(dict(relkind="r", owner=owner) if present else None)
                raise AssertionError("unlisted protected relation lookup")
            target = params.get("relation", "")
            if sql == interfaces._SHAPE_SECURITY:
                for group, schema, name, _body, _indexes in interfaces._PROTECTED_TABLE_SHAPES:
                    if target == f'"{schema}"."{name}"':
                        owner = interfaces._PROTECTED_OWNERS[group]
                        enabled = name in interfaces._RELATION_POLICIES
                        return Result([dict(owner=owner, enabled=enabled, forced=enabled)])
                raise AssertionError("unlisted protected security lookup")
            if sql == interfaces._SHAPE_TRIGGERS:
                marker = (
                    target in self.scratch_markers
                    if target.startswith("pg_temp.")
                    else ("runtime_attention_outbox" in target and self.marker)
                )
                return Result([dict(marker=True)] if marker else [])
            if target:
                return Result([])
            raise AssertionError("unexpected installer driver query")

        def exec_driver_sql(self, sql):
            self.commands.append(sql)
            from butlers.db_authority.apply import quote_identifier

            for signature, definition in definitions.items():
                if sql == definition.sql.replace("%", "%%"):
                    self.bodies[signature] = definition.body
                    return Result(None)
                for role in set(definition.executors) | self.acls[signature]:
                    target = quote_identifier(role)
                    if sql == f"REVOKE ALL ON FUNCTION {signature} FROM {target}":
                        self.acls[signature].discard(role)
                        return Result(None)
                    if sql == f"GRANT EXECUTE ON FUNCTION {signature} TO {target}":
                        self.acls[signature].add(role)
                        return Result(None)
            if sql.startswith("SELECT"):
                return Result(True)
            if sql.startswith('CREATE TABLE "public".'):
                self.names.add(sql.split('"')[3])
            elif sql == interfaces._FRESH_ATTENTION_SQL:
                self.names.update(interfaces._ATTENTION_STAGE_NAMES[5])
            elif sql == interfaces._ATTENTION_POLICY_SQL:
                self.marker = True
            elif (
                sql.startswith("CREATE TRIGGER")
                and "runtime_attention_condition_marker_sync_trigger" in sql
            ):
                if " ON pg_temp." in sql:
                    target = sql.split(" ON ", 1)[1].split(" FOR EACH", 1)[0]
                    self.scratch_markers.add(target)
                elif " ON public.runtime_attention_outbox " in sql:
                    self.marker = True
            return Result(None)

    connection = Connection()
    interfaces.install_protected_state(connection, profiles, "normal_owner", group="attention")
    assert connection.names == interfaces._ATTENTION_STAGE_NAMES[5]
    assert connection.marker is True
    assert not any(
        sql.startswith(("UPDATE ", "DELETE ", "TRUNCATE ")) for sql in connection.commands
    )
    actual_inserts = [sql for sql in connection.commands if sql.startswith("INSERT INTO")]
    for name in interfaces._ATTENTION_STAGE_NAMES.get(stage, ()):
        assert not any(f'"{name}"' in sql for sql in actual_inserts)
    # The parser receives the actual old source shape before final DDL.
    if 0 < stage < 4:
        old_body = interfaces._ATTENTION_OLD_OUTBOX_SHAPES[1 if stage < 3 else 3]
        assert any(old_body in sql for sql in connection.commands if sql.startswith("CREATE TEMP"))
    assert all(name in connection.owner_selects for name in interfaces._ATTENTION_STAGE_NAMES[5])
    # Execute the real installer and its exact source/ACL comparison. The
    # catalog/DDL driver is doubled; this does not execute a PostgreSQL trigger.
    bounded = interfaces.selected_protected_functions(
        profiles, "normal_owner", (("core", None, "core_198"),)
    )
    attention = tuple(row for row in bounded if row.owner == "runtime_attention_outbox_owner")
    assert attention
    assert all(not row.executors for row in attention)
    assert all(
        interfaces.function_matches(connection, row, interfaces.inspect_function(connection, row))
        for row in attention
    )
    guard = next(row for row in attention if row.name == "runtime_attention_outbox_guard")
    assert (
        "    IF TG_OP = 'DELETE' THEN\n        RETURN OLD;\n    END IF;\n    RETURN NEW;"
        in guard.body
    )
    assert guard.sql.replace("%", "%%") in connection.commands
    # The current callable consumer still receives its exact live executors;
    # a bounded staging assertion cannot silently close the serving interface.
    current = tuple(
        row
        for row in interfaces.final_functions(profiles, "normal_owner")
        if row.owner == "runtime_attention_outbox_owner"
    )
    # Every explicit historical group closes its exact selected executors.
    for group, revision in (("restore", "core_196"), ("dnd", "core_197")):
        interfaces.install_protected_state(connection, profiles, "normal_owner", group=group)
        identities = {
            (row.schema, row.name, row.signature)
            for row in interfaces.protected_group_functions(profiles, "normal_owner", (group,))
        }
        expected = tuple(
            row
            for row in interfaces.selected_protected_functions(
                profiles, "normal_owner", (("core", None, revision),)
            )
            if (row.schema, row.name, row.signature) in identities
        )
        assert expected
        assert all(not row.executors for row in expected)
        assert all(
            interfaces.function_matches(
                connection, row, interfaces.inspect_function(connection, row)
            )
            for row in expected
        )
    # Real default full installer, not a direct call to the catalog writer.
    interfaces.install_protected_state(connection, profiles, "normal_owner")
    assert any(row.executors for row in current)
    assert all(
        interfaces.function_matches(connection, row, interfaces.inspect_function(connection, row))
        for row in current
    )
    assert all(
        interfaces.function_matches(connection, row, interfaces.inspect_function(connection, row))
        for row in interfaces.final_functions(profiles, "normal_owner")
    )


def test_external_capsule_timestamp_readback_preserves_native_configuration_value():
    import json
    from datetime import UTC, datetime

    from butlers.db_authority.apply import _catalog_equivalent

    stamp = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    catalog = dict(
        configurations=[
            dict(
                schema="runtime_attention_admin",
                values=[dict(producers_enabled=False, producer_activated_at=stamp)],
            )
        ]
    )
    external = json.loads(json.dumps(catalog, default=str))
    assert _catalog_equivalent(catalog, external)
    external["configurations"][0]["values"][0]["producers_enabled"] = True
    assert not _catalog_equivalent(catalog, external)


def test_genuine_catalog_fixture_selects_real_source_before_managed_migration(
    tmp_path, monkeypatch
):
    """The fixture calls the real compiler; only provisioning I/O is doubled."""
    import runpy
    from pathlib import Path

    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "integration" / "test_db_authority_catalog.py")
    )
    fixture = namespace["authority_database"].__wrapped__
    migrated = []

    def migrate(container, name, chains, schemas):
        profiles = roles.compile_deployment_profiles(name, source_database="butlers")
        assert len(profiles) == 1
        assert profiles[0].database == name
        assert profiles[0].role == "butler_chronicler_rw"
        assert profiles[0].private_schemas == ("chronicler_mem",)
        assert chains == ["core", "chronicler", "memory"]
        assert schemas == dict(core="chronicler", chronicler="chronicler", memory="chronicler_mem")
        migrated.append(name)
        return "synthetic-migrated-url"

    fixture.__globals__["create_migrated_test_db"] = migrate
    fixture.__globals__["migration_bootstrap_db_url"] = lambda container, name: (
        "synthetic-bootstrap-url"
    )
    assert fixture(object(), tmp_path, monkeypatch) == (
        "synthetic-migrated-url",
        "synthetic-bootstrap-url",
    )
    assert len(migrated) == 1
    serving = roles.load_runtime_profile(migrated[0], "chronicler", "butler_chronicler_rw")
    assert serving.private_schemas == ("chronicler_mem",)
    # A serving caller cannot reuse the administrative source-name mapping.
    with pytest.raises(AuthorityError):
        roles.load_runtime_profile("butlers", "chronicler", "butler_chronicler_rw")


def test_source_security_descriptors_bind_real_trigger_functions_and_preserve_sql_tokens():
    from butlers.db_authority.grants import (
        security_expression_tokens,
        serving_catalog_contract,
    )

    contract = serving_catalog_contract("butlers")
    functions = {(row["schema"], row["name"], row["signature"]) for row in contract["functions"]}
    assert contract["policies"] and contract["triggers"]
    assert len({(row["schema"], row["table"], row["name"]) for row in contract["policies"]}) == len(
        contract["policies"]
    )
    assert len({(row["schema"], row["table"], row["name"]) for row in contract["triggers"]}) == len(
        contract["triggers"]
    )
    assert all(
        (row["function_schema"], row["function_name"], row["signature"]) in functions
        for row in contract["triggers"]
    )
    assert security_expression_tokens("(CURRENT_USER = 'two  words'::name)") == (
        security_expression_tokens(" ( current_user\n= 'two  words' :: name ) ")
    )
    for altered in (
        "(current_user = 'two words'::name)",  # literal contents stay significant
        "(current_user = 'two  words'::text)",  # casts stay significant
        "current_user = 'two  words'::name",  # grouping is not discarded
        "(current_user <> 'two  words'::name)",
    ):
        assert security_expression_tokens(altered) != security_expression_tokens(
            "(CURRENT_USER = 'two  words'::name)"
        )
    for unsupported in ("true; SELECT 1", "true -- ignored", "true /* ignored */", ""):
        with pytest.raises(AuthorityError):
            security_expression_tokens(unsupported)


@pytest.mark.parametrize(
    "fault", ["unknown", "stale", "mixed", "missing_chain", "missing_schema", "private_foreign"]
)
def test_actual_rollback_refuses_unpinned_complete_revision_profile_before_initializer(
    monkeypatch, fault
):
    """Execute R0 with an otherwise intact capsule; no old-init on forged stamps."""
    import hashlib
    import json

    from butlers.db_authority import apply

    manifest, capsule = _predecessor_capsule()
    catalog = capsule["catalog"]
    selected = next(row for row in catalog["versions"] if row["schema"] == "chronicler")
    if fault == "unknown":
        selected["revisions"].append("unlisted_revision")
    elif fault == "stale":
        selected["revisions"].remove("core_280")
        selected["revisions"].append("core_279")
    elif fault == "mixed":
        selected["revisions"].append("core_279")
    elif fault == "missing_chain":
        selected["revisions"].remove("chronicler_026")
    elif fault == "missing_schema":
        catalog["versions"].remove(selected)
    else:
        next(row for row in catalog["versions"] if row["schema"] == "chronicler_mem")[
            "revisions"
        ] = ["core_280", "mem_015"]
    selected["revisions"].sort()
    capsule["catalog_sha256"] = hashlib.sha256(
        json.dumps(catalog, sort_keys=True).encode()
    ).hexdigest()
    effects = []
    connection = object()
    monkeypatch.setattr(apply, "_rollback_actor", lambda conn: "maintenance")
    monkeypatch.setattr(apply, "require_rollback_lock", lambda conn: None)
    monkeypatch.setattr(apply, "_rollback_source_manifest", lambda conn, role: manifest)
    monkeypatch.setattr(
        apply, "_replay_predecessor_initializer", lambda *args: effects.append("init")
    )
    with pytest.raises(AuthorityError, match="complete revision profile"):
        apply.replay_predecessor_r0(connection, capsule)
    assert effects == []


def test_actual_rollback_pinned_revision_profile_keeps_module_and_private_heads():
    from butlers.db_authority import apply

    manifest, capsule = _predecessor_capsule()
    apply.validate_rollback_capsule(capsule, manifest, "maintenance")
    vectors = {r["schema"]: r["revisions"] for r in capsule["catalog"]["versions"]}
    assert vectors["chronicler"] == ["chronicler_026", "core_280"]
    assert vectors["chronicler_mem"] == ["mem_015"]
    assert "home_004" in vectors["health"]
    assert "sw_051" in vectors["switchboard"]
    assert all("core_281" not in v for v in vectors.values())
    assert all("core_282" not in v for v in vectors.values())


@pytest.mark.parametrize(
    "fault", [None, "other_shape", "existing_function", "unbound_function", "other_relation"]
)
def test_actual_r1_marker_shape_preview_is_exact_and_always_rolled_back(fault):
    """Execute the actual source transition consumer; native PG proof is separate."""
    import hashlib

    from butlers.db_authority import apply

    manifest, expected = _predecessor_capsule()
    expected = expected["catalog"]
    sources = apply.predecessor_function_sources(manifest.profiles)
    marker = next(
        key
        for key in sources
        if key[:3] == ("public", "runtime_attention_plant_legacy_debounce_marker", "")
    )
    expected["functions"] = [
        dict(schema=marker[0], name=marker[1], signature=marker[2], digest=marker[3])
    ]
    if fault == "unbound_function":
        expected["functions"][0]["digest"] = "0" * 64
    original_shape = hashlib.sha256(
        b"complete pinned columns policies indexes and marker"
    ).hexdigest()
    before = dict(
        schema="public",
        name="model_dispatch_attempts",
        kind="r",
        rls=False,
        forced_rls=False,
        partition=False,
        parents=[],
        shape=original_shape,
    )
    if fault == "other_relation":
        before["name"] = "sessions"
    expected["relations"] = [before]
    current = dict(relations=[before | {"shape": "f" * 64}])

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

    class Preview:
        def __init__(self, connection):
            self.connection = connection

        def rollback(self):
            self.connection.events.append("rollback-savepoint")
            self.connection.function = self.connection.trigger = False

    class Connection:
        def __init__(self):
            self.events = []
            self.function = self.trigger = False

        def begin_nested(self):
            self.events.append("savepoint")
            return Preview(self)

        def exec_driver_sql(self, sql):
            if sql == "SET LOCAL search_path=pg_catalog":
                self.events.append("canonical-deparse-path")
                return Result(None)
            if "to_regprocedure" in sql:
                return Result(fault != "existing_function")
            if sql == sources[marker]:
                self.function = True
                self.events.append("create-pinned-function")
                return Result(None)
            if sql == apply._PREDECESSOR_MARKER_TRIGGER:
                assert self.function
                self.trigger = True
                self.events.append("create-pinned-trigger")
                return Result(None)
            if sql == "SELECT 'public.model_dispatch_attempts'::pg_catalog.regclass::oid":
                return Result(41)
            raise AssertionError("unexpected transition command")

        def execute(self, statement, params):
            assert str(statement) == apply._ROLLBACK_RELATION_SHAPE
            assert params == {"oid": 41}
            self.events.append("read-full-relation-shape")
            return Result(
                original_shape
                if self.function and self.trigger and fault != "other_shape"
                else "a" * 64
            )

    connection = Connection()
    if fault is None:
        assert apply._verify_r1_relation_shapes(connection, manifest, current, expected) is True
        assert connection.events == [
            "savepoint",
            "canonical-deparse-path",
            "create-pinned-function",
            "create-pinned-trigger",
            "read-full-relation-shape",
            "rollback-savepoint",
        ]
    else:
        with pytest.raises(AuthorityError):
            apply._verify_r1_relation_shapes(connection, manifest, current, expected)
        if fault == "other_shape":
            assert connection.events[-1] == "rollback-savepoint"
        else:
            assert connection.events == []
    assert connection.function is False
    assert connection.trigger is False
    assert not any("upgrade" in event for event in connection.events)


def test_actual_retirement_accepts_only_exact_present_legacy_marker():
    from butlers.db_authority import interfaces

    marker = (
        "runtime_attention_plant_legacy_debounce_marker_trigger",
        "public",
        "model_dispatch_attempts",
        "public",
        "runtime_attention_plant_legacy_debounce_marker",
        7,
        False,
        True,
        "O",
        0,
        "",
        "",
        0,
        False,
        False,
        0,
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def mappings(self):
            return self.value

        def all(self):
            return self.value

    class Connection:
        observed = [marker]
        effects = []

        def exec_driver_sql(self, sql):
            if sql == "SELECT session_user":
                return Result("maintenance")
            if sql.startswith("SELECT current_user=session_user AND rolsuper"):
                return Result(True)
            if "FROM pg_catalog.pg_trigger" in sql:
                return Result(self.observed)
            raise AssertionError("unexpected retirement command")

        def execute(self, statement, params):
            sql = str(statement)
            if "FROM pg_catalog.pg_proc" in sql or "FROM pg_catalog.pg_namespace n JOIN" in sql:
                return Result([])
            raise AssertionError("unexpected retirement query")

    connection = Connection()
    assert interfaces.inspect_retired_interfaces(connection, "normal_owner") == ((), (), True)
    for index, value in (
        (1, "foreign"),
        (5, 5),
        (6, True),
        (7, False),
        (8, "D"),
        (9, 1),
        (11, "00"),
        (13, True),
    ):
        changed = list(marker)
        changed[index] = value
        connection.observed = [tuple(changed)]
        with pytest.raises(AuthorityError):
            interfaces.inspect_retired_interfaces(connection, "normal_owner")
    assert connection.effects == []


@pytest.mark.parametrize("primary", ["chronicler", "qa"])
def test_actual_selected_deployment_installer_never_grants_absent_runtime(
    tmp_path, monkeypatch, primary
):
    """Real roster/compiler/installer, with administrative driver IO doubled only."""
    from butlers.db_authority import grants, interfaces

    root = tmp_path / "selected_roster"
    entry = root / primary
    entry.mkdir(parents=True)
    (entry / "butler.toml").write_text(
        f'[butler]\nname="{primary}"\n[butler.db]\nname="selected_db"\nschema="{primary}"\n[modules.{primary}]\n'
    )
    monkeypatch.setattr(roles, "_ROSTER_ROOT", root)
    manifest = grants.compile_relation_manifest(
        "selected_db", "normal_owner", "maintenance", source_database="selected_db"
    )
    bootstrap = roles.bootstrap_role_manifest(
        "selected_db", "normal_owner", source_database="selected_db"
    )
    allowed = set(bootstrap["roles"]) | {
        "normal_owner",
        "maintenance",
        bootstrap["restore_executor"],
    }
    assert not {"butler_general_rw", "butler_switchboard_rw"} & allowed
    for row in manifest.relations:
        assert all(permission.role in allowed for permission in row.permissions)
    compiled = grants.compile_function_manifest(
        manifest.profiles, "normal_owner", grants.deployment_function_plans(manifest.profiles)
    )
    assert all(set(function.executors) <= allowed for function in compiled)
    contract = grants.compiled_security_contract(manifest)
    assert all(set(policy["roles"]) <= allowed | {"PUBLIC"} for policy in contract["policies"])
    policy_sql = interfaces._attention_policy_sql(manifest.profiles)
    assert "TO butler_switchboard_rw" not in policy_sql
    assert "runtime_attention_outbox_owner" in policy_sql
    definitions = interfaces.final_functions(manifest.profiles, "maintenance")
    index = {interfaces._signature(row): row for row in definitions}

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def mappings(self):
            return self

        def one_or_none(self):
            return self.value

        def __iter__(self):
            return iter(self.value)

    class Connection:
        def __init__(self):
            self.created = set()
            self.acls = {key: set() for key in index}
            self.effects = []

        def execute(self, statement, params):
            sql = str(statement)
            if "count(*)=:count" in sql:
                return Result(True)
            if sql == interfaces._FUNCTION_CATALOG:
                key = params["signature"]
                row = index[key]
                return Result(
                    None
                    if key not in self.created
                    else dict(
                        oid=key,
                        owner=row.owner,
                        definer=row.security_definer,
                        config=["search_path=pg_catalog, pg_temp"],
                        body=row.body,
                    )
                )
            if sql == interfaces._FUNCTION_ACL:
                return Result(
                    [
                        dict(role=role, privilege="EXECUTE", grantable=False)
                        for role in sorted(self.acls[params["oid"]])
                    ]
                )
            raise AssertionError("unexpected installer query")

        def exec_driver_sql(self, sql):
            # Decode the actual unbound driver template before catalog modeling.
            sql = sql % {}
            if sql.startswith("SELECT session_user=current_user AND rolsuper"):
                return Result(True)
            for key, row in index.items():
                if sql == row.sql:
                    self.created.add(key)
                    self.effects.append("create-fixed-function")
                    return Result(None)
            if sql.startswith("ALTER FUNCTION "):
                return Result(None)
            if sql.startswith("GRANT EXECUTE ON FUNCTION "):
                key, role = sql.removeprefix("GRANT EXECUTE ON FUNCTION ").split(" TO ")
                assert role.startswith('"') and role.endswith('"')
                role = role[1:-1]
                if role not in allowed:
                    raise AssertionError("undeclared runtime grant attempted")
                self.acls[key].add(role)
                self.effects.append("grant-declared-executor")
                return Result(None)
            raise AssertionError("unexpected installer effect")

    connection = Connection()
    interfaces.install_functions(connection, definitions)
    assert connection.created == set(index)
    assert "grant-declared-executor" in connection.effects
    # The native policy parser likewise gets no absent TO principal.
    statements = []
    connection.exec_driver_sql = statements.append
    for name in ("runtime_attention_outbox", "runtime_attention_delivery_lease"):
        interfaces._parse_security_shape(
            connection, "pg_temp.selected", name, profiles=manifest.profiles
        )
    assert statements
    assert all(" TO butler_switchboard_rw " not in sql for sql in statements)
    assert sum(sql.startswith("CREATE POLICY") for sql in statements) == 2


def test_actual_home_module_chain_alias_keeps_health_owned_objects_and_sequence():
    import tomllib

    from butlers.db_authority import grants
    from butlers.migrations import get_module_migration_chains
    from butlers.modules.registry import default_registry

    with (roles._ROSTER_ROOT / "health" / "butler.toml").open("rb") as stream:
        source_modules = tomllib.load(stream)["modules"]
    modules = default_registry().load_from_config(source_modules)
    assert get_module_migration_chains(modules, "health")["home_assistant"] == "home"
    manifest = grants.compile_relation_manifest(
        "butlers", "normal_owner", "maintenance", source_database="butlers"
    )
    assert ("home", "health", None) in grants.deployment_function_plans(manifest.profiles)
    selected = grants.select_migration_manifest(manifest, (("home", "health", None),))
    rows = {row.identity: row for row in selected.relations}
    table = rows["health", "ha_command_log", "table", ""]
    sequence = rows["health", "ha_command_log_id_seq", "sequence", ""]
    assert table.required is True
    assert table.owner == sequence.owner == "normal_owner"
    assert sequence.sequence_parent == ("health", "ha_command_log", "id")
    assert any(
        permission.role == "butler_health_rw" and "INSERT" in permission.verbs
        for permission in table.permissions
    )
    assert any(
        permission.role == "butler_health_rw" and permission.verbs == ("USAGE",)
        for permission in sequence.permissions
    )
    with pytest.raises(AuthorityError):
        grants.select_migration_manifest(manifest, (("home", "chronicler", None),))


@pytest.mark.parametrize(
    ("chain", "target", "expected"),
    [
        ("memory", "chronicler_mem", "trg_episodes_preserve_provenance"),
        ("memory", "isolated_memory", "trg_episodes_preserve_provenance"),
        ("approvals", "isolated_approvals", "trg_approval_events_immutable"),
    ],
)
def test_selected_module_security_uses_actual_plan_without_fabricated_runtime_profile(
    chain, target, expected
):
    from butlers.db_authority.grants import (
        compile_relation_manifest,
        compiled_security_contract,
        select_migration_manifest,
    )

    full = compile_relation_manifest(
        "test_selected", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    selected = select_migration_manifest(full, ((chain, target, None),))
    contract = compiled_security_contract(selected)
    assert any(row["schema"] == target and row["name"] == expected for row in contract["triggers"])
    assert all(row["schema"] == target for row in contract["triggers"])
    assert selected.profiles == full.profiles
    if target.startswith("isolated_"):
        assert all(not row.permissions for row in selected.relations)
        assert not any(profile.primary_schema == target for profile in selected.profiles)


@pytest.mark.parametrize(
    ("revision", "present", "absent"),
    [
        ("core_100", (), ("restore_drill_executor", "dashboard_auth")),
        ("core_196", ("restore_drill_executor",), ("dashboard_auth",)),
        ("core_240", ("restore_drill_executor", "dashboard_auth"), ()),
    ],
)
def test_historical_core_namespace_selection_follows_real_source_graph(revision, present, absent):
    from butlers.db_authority.grants import compile_relation_manifest, select_migration_manifest

    full = compile_relation_manifest(
        "test_history", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    selected = select_migration_manifest(full, (("core", None, revision),))
    assert set(present) <= set(selected.schemas)
    assert not set(absent) & set(selected.schemas)
    # Historical plans grant no serving access. The admitted legacy trigger
    # still needs its exact non-serving purpose dependencies at core196.
    expected_dependencies = (
        {
            ("public", "audit_log", "table", ""): (
                (
                    "priority_contact_audit_owner",
                    ("INSERT",),
                    ("actor", "action", "target", "note"),
                ),
            ),
            ("public", "audit_log_id_seq", "sequence", ""): (
                ("priority_contact_audit_owner", ("USAGE",), ()),
            ),
        }
        if revision == "core_196"
        else {}
    )
    assert all(
        not row.permissions and not row.required
        for row in selected.relations
        if row.identity not in expected_dependencies
    )
    dependencies = {
        row.identity: row for row in selected.relations if row.identity in expected_dependencies
    }
    assert set(dependencies) == set(expected_dependencies)
    for identity, row in dependencies.items():
        assert row.owner == "migration_selected"
        assert row.required is True
        assert (
            tuple(
                (permission.role, permission.verbs, permission.columns)
                for permission in row.permissions
            )
            == expected_dependencies[identity]
        )


@pytest.mark.parametrize(
    ("revision", "owners"),
    [
        ("core_100", set()),
        ("core_196", {"restore_drill_executor_owner", "restore_drill_executor_audit_writer"}),
        (
            "core_197",
            {
                "restore_drill_executor_owner",
                "restore_drill_executor_audit_writer",
                "dnd_generation_owner",
            },
        ),
        (
            "core_198",
            {
                "restore_drill_executor_owner",
                "restore_drill_executor_audit_writer",
                "dnd_generation_owner",
                "runtime_attention_outbox_owner",
            },
        ),
    ],
)
def test_historical_protected_definitions_follow_real_edges_with_no_serving_grants(
    revision, owners
):
    from butlers.db_authority.grants import compile_relation_manifest
    from butlers.db_authority.interfaces import selected_protected_functions

    manifest = compile_relation_manifest(
        "test_history", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    definitions = selected_protected_functions(
        manifest.profiles, manifest.migration_owner, (("core", None, revision),)
    )
    assert {row.owner for row in definitions} == owners
    assert all(not row.executors for row in definitions)
    assert all(not row.schema.endswith("_admin") for row in definitions)


def test_historical_administration_closes_function_acl_without_rewriting_body():
    from types import SimpleNamespace

    from butlers.db_authority.apply import close_administrative_function_privileges
    from butlers.db_authority.grants import FunctionAuthority
    from butlers.db_authority.interfaces import _FUNCTION_ACL

    definition = FunctionAuthority(
        "public",
        "historical_owned",
        "",
        "normal_owner",
        (),
        False,
        "CREATE FUNCTION public.historical_owned() RETURNS void LANGUAGE plpgsql ",
        "BEGIN RETURN; END",
        "actual source declaration",
        ("search_path=pg_catalog, pg_temp",),
    )

    class Connection:
        def __init__(self):
            self.acls = [{"role": "PUBLIC", "privilege": "EXECUTE", "grantable": False}]
            self.effects = []

        def execute(self, statement, params):
            assert str(statement) == _FUNCTION_ACL
            assert params == {"oid": 51}
            return SimpleNamespace(mappings=lambda: iter(self.acls))

        def exec_driver_sql(self, sql):
            assert sql == 'REVOKE ALL ON FUNCTION "public"."historical_owned"() FROM PUBLIC'
            self.effects.append(sql)
            self.acls = []

    connection = Connection()
    close_administrative_function_privileges(connection, (({"oid": 51}, definition),))
    assert len(connection.effects) == 1
    assert connection.acls == []


def test_bounded_historical_administration_never_certifies_current_rls_or_serving_acl():
    from dataclasses import replace

    from butlers.db_authority.apply import validate_relations
    from butlers.db_authority.grants import ObjectAuthority, ObjectKind, Permission

    declared = ObjectAuthority(
        "public",
        "user_context",
        ObjectKind.TABLE,
        "normal_owner",
        (),
        "bounded source-owned history",
        required=False,
    )
    actual = dict(
        schema="public",
        name="user_context",
        kind="r",
        owner="normal_owner",
        oid=31,
        rls=False,
        forced_rls=False,
    )
    # A pre-core197 table is administrative history, never a current serving
    # catalog. The ordinary admission path retains its strict RLS check.
    with pytest.raises(AuthorityError, match="row security"):
        validate_relations((actual,), (declared,))
    assert validate_relations((actual,), (declared,), administrative_history=True) == (
        (actual, declared),
    )
    for invalid in (
        actual | {"owner": "foreign_owner"},
        actual | {"rls": None},
        actual | {"name": "unlisted_relation"},
    ):
        with pytest.raises(AuthorityError):
            validate_relations((invalid,), (declared,), administrative_history=True)
    serving = replace(declared, permissions=(Permission("butler_general_rw", ("SELECT",)),))
    with pytest.raises(AuthorityError, match="serving privilege"):
        validate_relations((actual,), (serving,), administrative_history=True)


async def test_actual_checkpoint_consumers_require_only_source_owned_connector_operations():
    """Actual production cursor/settings calls; controlled driver is not PG proof."""
    from butlers.connectors import cursor_store
    from butlers.db_authority.apply import namespace_permissions
    from butlers.db_authority.grants import compile_relation_manifest

    manifest = compile_relation_manifest(
        "butlers", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    row = next(
        row
        for row in manifest.relations
        if (row.schema, row.name) == ("switchboard", "connector_registry")
    )
    allowed = {
        verb
        for permission in row.permissions
        if permission.role == "connector_writer"
        for verb in permission.verbs
    }
    state = {"checkpoint_cursor": None, "settings": {}}

    class Driver:
        def require(self, verbs):
            if not set(verbs) <= allowed:
                raise PermissionError("Source manifest lacks a required checkpoint operation")

        async def execute(self, sql, *args):
            assert sql == cursor_store._UPSERT_SQL
            self.require(("SELECT", "INSERT", "UPDATE"))
            state["checkpoint_cursor"] = args[2]

        async def fetchrow(self, sql, *args):
            if sql == cursor_store._UPSERT_SETTINGS_SQL:
                self.require(("SELECT", "INSERT", "UPDATE"))
                state["settings"] |= args[2]
            else:
                assert sql in (cursor_store._SELECT_SQL, cursor_store._SELECT_SETTINGS_SQL)
                self.require(("SELECT",))
            return state.copy()

        @asynccontextmanager
        async def acquire(self):
            yield self

    driver = Driver()
    await cursor_store.save_cursor(
        driver, "synthetic", "endpoint", "first", parent_endpoint_identity=None
    )
    await cursor_store.save_cursor(
        driver, "synthetic", "endpoint", "second", parent_endpoint_identity=None
    )
    assert await cursor_store.load_cursor(driver, "synthetic", "endpoint") == "second"
    assert await cursor_store.save_connector_settings(
        driver, "synthetic", "endpoint", {"first": 1}
    ) == {"first": 1}
    assert await cursor_store.save_connector_settings(
        driver, "synthetic", "endpoint", {"second": 2}
    ) == {"first": 1, "second": 2}
    assert await cursor_store.load_connector_settings(driver, "synthetic", "endpoint") == {
        "first": 1,
        "second": 2,
    }
    assert allowed == {"SELECT", "INSERT", "UPDATE"}
    assert "connector_writer" in namespace_permissions(manifest.relations)["switchboard"]


async def test_actual_chronicler_readers_use_only_named_source_evidence():
    """Actual adapter SQL consumers with manifest-derived visibility, not PG execution."""
    import uuid
    from datetime import UTC, datetime

    from butlers.chronicler.adapters import CalendarCompletedAdapter, CoreSessionsAdapter
    from butlers.db_authority.grants import compile_relation_manifest

    manifest = compile_relation_manifest(
        "butlers", "migration_selected", "maintenance_selected", source_database="butlers"
    )
    readable = {
        (row.schema, row.name)
        for row in manifest.relations
        if any(
            permission.role == "butler_chronicler_rw" and "SELECT" in permission.verbs
            for permission in row.permissions
        )
    }
    observed = set()

    class Driver:
        @asynccontextmanager
        async def acquire(self):
            yield self

        async def fetchval(self, sql, *args):
            assert "information_schema.tables" in sql
            if "SELECT ARRAY" in sql:
                return [name for name in args[1] if (args[0], name) not in readable]
            name = "sessions" if "'sessions'" in sql else "calendar_event_entities"
            return (args[0], name) in readable

        async def fetch(self, sql, *args):
            identities = (
                (("education", "sessions"),)
                if '"education".sessions' in sql
                else (("general", "calendar_event_entities"),)
                if '"general".calendar_event_entities' in sql
                else (
                    ("general", "calendar_event_instances"),
                    ("general", "calendar_events"),
                    ("general", "calendar_sources"),
                )
            )
            assert set(identities) <= readable
            observed.update(identities)
            return []

    pool = Driver()
    sessions = CoreSessionsAdapter(("education",))
    calendar = CalendarCompletedAdapter(("general",))
    assert await sessions._fetch_sessions(pool, "education", None) == ([], None)
    assert await calendar._fetch_instances(pool, "general", None, datetime.now(UTC)) == []
    assert await calendar._fetch_event_entities(pool, "general", [uuid.uuid4()]) == {}
    assert observed == {
        ("education", "sessions"),
        ("general", "calendar_event_instances"),
        ("general", "calendar_sources"),
        ("general", "calendar_events"),
        ("general", "calendar_event_entities"),
    }
    assert ("education", "state") not in readable
    assert ("education", "state_store") not in readable


@pytest.mark.parametrize("predecessor", ["absent", "pinned", "forged", "foreign-owner", "unknown"])
def test_actual_historical_restore_edge_checks_retired_provenance_before_ddl(predecessor):
    """Execute both real preflights; only the physical catalog/DDL driver is doubled."""
    from butlers.db_authority import interfaces

    class ReachedOwnedDDL(Exception):
        pass

    class Result:
        def __init__(self, value):
            self.value = value

        def one(self):
            return self.value

        def scalar_one(self):
            return self.value

        def mappings(self):
            return self

        def one_or_none(self):
            return self.value

        def all(self):
            return self.value

        def __iter__(self):
            return iter(self.value)

    pinned = next(
        row
        for row in interfaces._RETIRED_PREDECESSOR_FUNCTIONS
        if row[0] == "restore_drill_executor_admin"
    )

    class Connection:
        info = {}

        def __init__(self):
            self.effects = []
            self.commands = []

        def exec_driver_sql(self, sql):
            self.commands.append(sql)
            if "actor.rolsuper AND actor.rolname=session_user" in sql:
                return Result(("butlers", "normal_owner", True))
            if sql == "SELECT current_database(), current_user":
                return Result(("butlers", "normal_owner"))
            if sql == "SELECT session_user":
                return Result("maintenance")
            if sql.startswith("SELECT current_user=session_user AND rolsuper"):
                return Result(True)
            if "FROM pg_catalog.pg_trigger" in sql:
                return Result(())
            if sql.startswith("SELECT singleton, migration_role::text"):
                return Result([(True, "normal_owner", "maintenance")])
            if sql in ("RESET ROLE", 'SET ROLE "normal_owner"'):
                return Result(None)
            if sql == interfaces._FRESH_RESTORE_SQL:
                self.effects.append("restore-ddl")
                raise ReachedOwnedDDL
            raise AssertionError("unexpected historical-edge driver command")

        def execute(self, statement, params):
            sql = str(statement)
            if "FROM pg_catalog.pg_proc p" in sql:
                if predecessor == "absent":
                    return Result([])
                return Result(
                    [
                        dict(
                            schema=pinned[0],
                            name=pinned[1],
                            signature=pinned[2],
                            owner=pinned[4] or "maintenance",
                            digest="0" * 64 if predecessor == "forged" else pinned[3],
                        )
                    ]
                )
            if "FROM pg_catalog.pg_namespace n JOIN" in sql:
                return Result(
                    []
                    if predecessor == "absent"
                    else [
                        dict(
                            name=pinned[0],
                            owner="foreign" if predecessor == "foreign-owner" else "maintenance",
                        )
                    ]
                )
            if "FROM pg_catalog.pg_class c JOIN" in sql and "relkind NOT IN" in sql:
                return Result(
                    [
                        dict(
                            name="unlisted"
                            if predecessor == "unknown"
                            else "bootstrap_configuration",
                            kind="r",
                            owner="maintenance",
                        )
                    ]
                )
            if "FROM pg_catalog.pg_attribute a" in sql:
                return Result(
                    [
                        ("singleton", "boolean", True),
                        ("migration_role", "name", True),
                        ("bootstrap_role", "name", True),
                    ]
                )
            if "WHERE n.nspname=:schema AND c.relname=:name" in sql:
                return Result(None)
            raise AssertionError("unexpected historical-edge catalog query")

    connection = Connection()
    if predecessor in ("absent", "pinned"):
        with pytest.raises(ReachedOwnedDDL):
            interfaces.install_revision_group(connection, "restore")
        assert connection.effects == ["restore-ddl"]
    else:
        with pytest.raises(AuthorityError):
            interfaces.install_revision_group(connection, "restore")
        assert connection.effects == []
    assert connection.commands[-1] == 'SET ROLE "normal_owner"'


@pytest.mark.parametrize(
    ("component", "enabled", "fault"),
    [
        ("producers", False, None),
        ("producers", True, None),
        ("reissue", False, None),
        ("reissue", True, None),
        ("conditions", False, None),
        ("conditions", True, None),
        ("marker", False, None),
        ("marker", True, None),
        ("reissue", False, "actor"),
        ("reissue", False, "lock"),
        ("reissue", False, "serving"),
        ("reissue", False, "body"),
        ("reissue", False, "owner"),
        ("marker", True, "partial"),
        ("producers", False, "write"),
    ],
)
def test_actual_operator_attention_transition_owns_source_and_atomic_state(
    component, enabled, fault
):
    """Real operator/installer predicates; physical SQL catalog/transaction protocol only is doubled.

    These controls do not prove PostgreSQL parsing, lock behavior or installed
    shape. Those remain the genuine migration/attention PG case obligations.
    """
    import copy

    from butlers.db_authority import apply, interfaces
    from butlers.db_authority.roles import compile_deployment_profiles

    profiles = compile_deployment_profiles("butlers", source_database="butlers")
    definitions = interfaces.protected_group_functions(profiles, "normal_owner", ("attention",))
    by_signature = {interfaces._signature(d): d for d in definitions}

    class Result:
        def __init__(self, value):
            self.value = value

        def one(self):
            return self.value

        def scalar_one(self):
            return self.value

        def mappings(self):
            return self

        def one_or_none(self):
            return self.value

        def all(self):
            return self.value

        def __iter__(self):
            return iter(self.value)

    class Transaction:
        def __init__(self, driver):
            self.driver = driver
            self.before = copy.deepcopy(driver.state)

        def rollback(self):
            self.driver.state = self.before
            self.driver.rollbacks += 1

        def commit(self):
            self.driver.commits += 1

    class Driver:
        info = {"butlers.source_database": "butlers"}

        def __init__(self):
            self.state = {"marker": component != "marker" or not enabled, "control": None}
            self.commands = []
            self.rollbacks = 0
            self.commits = 0
            self.effects = []

        def begin_nested(self):
            return Transaction(self)

        def exec_driver_sql(self, sql):
            self.commands.append(sql)
            if "actor.rolsuper AND actor.rolname=session_user" in sql:
                return Result(("butlers", "normal_owner", fault != "actor"))
            if (
                "SELECT current_user=session_user AND rolsuper" in sql
                or "SELECT session_user=current_user AND rolsuper" in sql
            ):
                return Result(True)
            if sql == "SELECT session_user":
                return Result("maintenance")
            if sql == "SELECT current_database(), session_user":
                return Result(("butlers", "maintenance"))
            if "p.proname='runtime_attention_plant_legacy_debounce_marker'" in sql:
                return Result(())
            if sql.startswith("SELECT to_regprocedure("):
                marker = self.state["marker"]
                return Result((marker, not marker if fault == "partial" else marker))
            if sql.startswith("LOCK TABLE "):
                return Result(None)
            if sql == apply._ATTENTION_MARKER_TRIGGER:
                self.state["marker"] = True
                self.effects.append("marker-create")
                return Result(None)
            if sql.startswith("DROP TRIGGER runtime_attention_condition_marker_sync_trigger"):
                self.state["marker"] = False
                self.effects.append("marker-drop")
                return Result(None)
            if sql.startswith("DROP FUNCTION public.runtime_attention_sync_condition_marker"):
                return Result(None)
            if sql.lstrip().startswith(
                "UPDATE public.runtime_attention_condition_episodes AS marker"
            ):
                self.effects.append("backfill")
                return Result(None)
            if sql.lstrip().startswith(
                "CREATE OR REPLACE FUNCTION public.runtime_attention_sync_condition_marker"
            ):
                self.state["marker"] = True
                return Result(None)
            if sql.startswith(("ALTER FUNCTION ", "REVOKE ", "GRANT ")):
                return Result(None)
            if sql.startswith(
                (
                    "CREATE TEMPORARY TABLE ",
                    "CREATE UNIQUE INDEX ",
                    "CREATE INDEX ",
                    "CREATE POLICY ",
                    "CREATE TRIGGER ",
                    "DROP TABLE ",
                )
            ):
                return Result(None)
            raise AssertionError("unexpected operator driver command")

        def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            if "pg_catalog.pg_locks" in sql:
                return Result(fault != "lock")
            if "pg_catalog.pg_stat_activity" in sql:
                return Result(fault != "serving")
            if "WHERE n.nspname=ANY(:schemas)" in sql:
                return Result([])
            if "WHERE n.nspname='public' AND p.proname=ANY(:names)" in sql:
                return Result(
                    [
                        (d.name, d.signature)
                        for d in definitions
                        if self.state["marker"]
                        or d.name != "runtime_attention_sync_condition_marker"
                    ]
                )
            if "WHERE p.oid=pg_catalog.to_regprocedure(:signature)" in sql:
                d = by_signature[params["signature"]]
                if d.name == "runtime_attention_sync_condition_marker" and not self.state["marker"]:
                    return Result(None)
                return Result(
                    dict(
                        oid=d.name,
                        owner=d.owner,
                        definer=d.security_definer,
                        config=["search_path=pg_catalog, pg_temp"],
                        body="foreign" if fault == "body" else d.body,
                    )
                )
            if "WHERE p.oid=:oid" in sql:
                d = next(d for d in definitions if d.name == params["oid"])
                return Result(
                    [
                        dict(role=r, privilege="EXECUTE", grantable=False)
                        for r in d.executors
                        if r != d.owner
                    ]
                )
            if "WITH owners AS" in sql:
                return Result(fault != "owner")
            if "WHERE n.nspname=:schema AND c.relname=:name" in sql:
                return Result(dict(relkind="r", owner="runtime_attention_outbox_owner"))
            if "c.relrowsecurity AS enabled" in sql:
                name = params["relation"].split(".")[-1].strip('"')
                rls = name in interfaces._RELATION_POLICIES
                return Result(
                    [dict(owner="runtime_attention_outbox_owner", enabled=rls, forced=rls)]
                )
            if "relation" in params:
                # Both parser/native result sets are driver facts here, not SQL
                # evidence. The genuine PG controls compare populated catalogs.
                return Result([])
            if sql.startswith("UPDATE public.runtime_attention_"):
                self.state["control"] = params["enabled"]
                self.effects.append("control-update")
                if fault == "write":
                    raise ValueError("driver write failed")
                return Result([(params["enabled"],)])
            raise AssertionError("unexpected operator driver query")

    driver = Driver()
    original = copy.deepcopy(driver.state)
    if fault:
        with pytest.raises(ValueError if fault == "write" else AuthorityError):
            apply.transition_runtime_attention(driver, component=component, enabled=enabled)
        assert driver.state == original
        assert driver.commits == 0
        if fault != "write":
            assert not driver.effects
    else:
        result = apply.transition_runtime_attention(driver, component=component, enabled=enabled)
        assert result == {"component": component, "enabled": enabled}
        assert driver.commits == 1
        if component == "marker":
            assert driver.state["marker"] is enabled
            assert ("backfill" in driver.effects) is enabled
        else:
            assert driver.state["control"] is enabled
        assert not any(sql.lstrip().startswith(("DELETE ", "TRUNCATE ")) for sql in driver.commands)
        assert driver.rollbacks == 2


@pytest.mark.parametrize(
    "name",
    (
        "observe_runtime_attention_models",
        "observe_runtime_attention_fleet_halt",
        "observe_runtime_attention_conditions",
        "reissue_runtime_attention_episode",
    ),
)
def test_actual_dashboard_attention_source_has_only_fixed_dashboard_authority(name):
    """Compile the real final producer; SQL execution remains genuine PG proof.

    The actual API's pool is dashboard_api, not the normal migration owner.
    These four function definitions are the exact installer/catalog inputs.
    """
    from butlers.db_authority import interfaces
    from butlers.db_authority.roles import runtime_profiles

    definition = next(
        row
        for row in interfaces.final_functions(runtime_profiles("butlers"), "normal_owner")
        if row.name == name
    )
    assert definition.executors == ("dashboard_api",)
    assert definition.owner == "runtime_attention_outbox_owner"
    assert definition.security_definer is True
    assert "IF current_setting('role', true) IS DISTINCT FROM 'dashboard_api' THEN" in (
        definition.body
    )
    assert "USING ERRCODE = '42501'" in definition.body
    assert "forbids SET ROLE" not in definition.body
    assert "normal_owner" not in definition.body
    # Preserve the actual producer's remaining query/state/CAS logic exactly;
    # independent PG controls exercise the guarded statements under real roles.
    assert "SET search_path = pg_catalog, pg_temp" in definition.header


@pytest.mark.parametrize("selected_schema", ("switchboard", "selected_registry"))
def test_finance_boot_witness_manifest_preserves_landed_selected_producer(selected_schema):
    """Current sw052 is a fixed Finance read, not a catalog-discovered grant."""
    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.roles import runtime_profiles

    profiles = runtime_profiles("butlers")
    plans = (("switchboard", selected_schema, None),)
    rows = compile_function_manifest(profiles, "ordinary_migration_owner", plans)
    matches = [row for row in rows if row.name == "finance_warning_boot_current"]
    assert len(matches) == 1
    row = matches[0]
    assert row.identity == ("public", "finance_warning_boot_current", "uuid,bigint")
    assert row.owner == "registry_control_owner"  # REQ021 fixed pure-reader purpose.
    assert row.executors == ("butler_finance_rw",)
    assert row.security_definer is True
    assert row.settings == ("search_path=pg_catalog, pg_temp",)
    assert "LANGUAGE plpgsql STABLE SECURITY DEFINER" in row.header
    assert row.producer == "roster/switchboard/migrations/052_finance_warning_boot_read.py"
    assert f'FROM "{selected_schema}".butler_registry_control_plane c' in row.body
    assert f'LEFT JOIN "{selected_schema}".butler_boot_registrations h' in row.body
    assert (
        "pg_catalog.current_setting('role',true) IS DISTINCT FROM 'butler_finance_rw'" in row.body
    )
    assert "WHERE c.name='finance'" in row.body
    assert "p_epoch<=0 THEN RETURN NULL" in row.body
    assert not any(token in row.body for token in ("INSERT ", "UPDATE ", "DELETE ", "GRANT "))
    for absent in ((), (("core", "general", None),), (("switchboard", selected_schema, "sw_051"),)):
        assert not any(
            item.name == row.name
            for item in compile_function_manifest(profiles, "ordinary_migration_owner", absent)
        )
    without_finance = tuple(profile for profile in profiles if profile.role != "butler_finance_rw")
    no_finance_row = next(
        item
        for item in compile_function_manifest(without_finance, "ordinary_migration_owner", plans)
        if item.name == row.name
    )
    assert no_finance_row.executors == ()


def test_actual_serving_catalog_requires_isolated_owners_for_every_managed_definer():
    """Exercise the real source-to-admission compiler, not a diagnostic manifest."""
    from butlers.db_authority.grants import serving_catalog_contract
    from butlers.db_authority.roles import PURPOSE_OWNERS, bootstrap_role_manifest

    contract = serving_catalog_contract("butlers")
    definers = [row for row in contract["functions"] if row["definer"]]
    assert definers
    assert all(row["owner"] in PURPOSE_OWNERS for row in definers)
    bootstrap = bootstrap_role_manifest(
        "butlers", "ordinary_migration_owner", source_database="butlers"
    )
    assert all(row["owner"] in bootstrap["roles"] for row in definers)
    assert all(row["owner"] not in row["executors"] for row in definers)
    relations = {(row["schema"], row["name"]): row for row in contract["relations"]}
    assert relations["connectors", "filtered_events"]["owner"] == "connector_partition_owner"
    assert relations["switchboard", "message_inbox"]["owner"] == "switchboard_partition_owner"
    schemas = {row["name"]: row for row in contract["schemas"]}
    assert schemas["connectors"]["creators"] == ["connector_partition_owner"]
    assert schemas["switchboard"]["creators"] == ["switchboard_partition_owner"]
    assert all(
        not row["creators"]
        for row in contract["schemas"]
        if row["name"] not in {"connectors", "switchboard"}
    )
    probe = [
        p
        for p in relations["public", "model_catalog"]["permissions"]
        if p["role"] == "model_probe_owner"
    ]
    assert {(p["verb"], tuple(p["columns"])) for p in probe} == {
        ("SELECT", ("id",)),
        (
            "UPDATE",
            (
                "last_verified_at",
                "last_verified_latency_ms",
                "last_verified_ok",
                "last_verified_error",
                "updated_at",
            ),
        ),
    }
    restore_sequence = [
        p
        for p in relations["public", "cost_claim_events_id_seq"]["permissions"]
        if p["role"] == "cost_claim_restore_owner"
    ]
    assert restore_sequence == [
        {"role": "cost_claim_restore_owner", "verb": "UPDATE", "columns": []}
    ]


def test_ordinary_purpose_policy_reverse_preserves_full_shape_and_savepoint_cleanup():
    """Execute the source reverse planner; native PostgreSQL hashes are separate proof."""
    from types import SimpleNamespace

    from butlers.db_authority import apply, grants, interfaces

    manifest = grants.compile_relation_manifest(
        "butlers", "normal_owner", "maintenance", source_database="butlers"
    )
    changes = interfaces.ordinary_security_policy_changes(manifest)
    assert {key[:2] for key in changes} == {
        ("public", "cost_claims"),
        ("public", "cost_claim_events"),
        ("public", "cost_claim_resolutions"),
        ("public", "insight_amendments"),
        ("switchboard", "butler_registry_control_plane"),
        ("switchboard", "butler_boot_registrations"),
    }
    before = dict(schema="public", name="cost_claims", shape="complete-captured-shape")

    class Connection:
        def __init__(self, wrong_shape=False):
            self.events = []
            self.wrong_shape = wrong_shape

        def begin_nested(self):
            self.events.append("SAVEPOINT")
            return SimpleNamespace(rollback=lambda: self.events.append("ROLLBACK SAVEPOINT"))

        def exec_driver_sql(self, sql):
            self.events.append(sql)
            if sql.startswith("SELECT current_user=session_user AND rolsuper"):
                return SimpleNamespace(scalar_one=lambda: True)

        def execute(self, sql, params):
            if str(sql) == "SELECT pg_catalog.to_regclass(:target)::oid":
                assert params == {"target": '"public"."cost_claims"'}
                return SimpleNamespace(scalar_one=lambda: 81)
            assert str(sql) == apply._ROLLBACK_RELATION_SHAPE and params == {"oid": 81}
            restored = any(
                s.startswith('CREATE POLICY "cost_claims_restore_owner"')
                and "cost_claim_restore_owner" not in s
                for s in self.events
            )
            return SimpleNamespace(
                scalar_one=lambda: (
                    before["shape"] if restored and not self.wrong_shape else "wrong-full-shape"
                )
            )

    successful = Connection()
    apply._preview_predecessor_purpose_policy_shape(successful, manifest, before)
    assert successful.events[0] == "SAVEPOINT"
    assert successful.events[-1] == "ROLLBACK SAVEPOINT"
    assert not any("INSERT INTO" in sql or "UPDATE " in sql for sql in successful.events)
    rejected = Connection(wrong_shape=True)
    with pytest.raises(AuthorityError, match="full shape"):
        apply._preview_predecessor_purpose_policy_shape(rejected, manifest, before)
    assert rejected.events[-1] == "ROLLBACK SAVEPOINT"


@pytest.mark.parametrize("finance_revision", [None, "finance_018"])
@pytest.mark.parametrize("include_connector", [False, True])
def test_disposable_serving_deployment_keeps_control_and_native_identities_separate(
    include_connector,
    finance_revision,
):
    """Execute producer orchestration; physical SQL is doubled, never PG credit."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers.testing import migration

    calls = []
    fail_create = [None]
    fail_revoke = [False]
    create_count = [0]
    held = object()
    container = SimpleNamespace(
        get_container_host_ip=lambda: "localhost",
        get_exposed_port=lambda _: 5432,
    )

    class PhysicalConnection:
        def execute(self, statement, params=None):
            calls.append((str(statement), params))
            if str(statement).startswith("CREATE ROLE"):
                assert finalize.called
                create_count[0] += 1
                if create_count[0] == fail_create[0]:
                    raise RuntimeError("Selected CREATE was not acknowledged")
            return SimpleNamespace(scalar_one=lambda: True)

        def exec_driver_sql(self, statement):
            calls.append((statement, None))
            if statement.startswith("REVOKE") and fail_revoke[0]:
                raise RuntimeError("Independent cleanup failure")

    @contextmanager
    def connect():
        yield PhysicalConnection()

    engine = SimpleNamespace(connect=connect, dispose=Mock())

    @contextmanager
    def maintenance(url, *, source_database):
        assert url == "postgresql://selected_control@localhost/butlers"
        assert source_database == "butlers"
        yield held

    with (
        patch.object(migration, "create_migration_db", return_value="normal-owner-url") as create,
        patch.object(
            migration,
            "migration_bootstrap_db_url",
            return_value="postgresql://selected_control@localhost/butlers",
        ),
        patch.object(migration, "create_engine", return_value=engine),
        patch.object(migration, "_upgrade_chain_to_revision") as stage,
        patch("butlers.migrations.migration_session", maintenance),
        patch("butlers.migrations.run_migrations", new_callable=AsyncMock) as upgrade,
        patch("butlers.migrations.finalize_authority") as finalize,
    ):
        with (
            migration.serving_test_deployment(
                container, include_connector=True, finance_revision=finance_revision
            )
            if include_connector
            else migration.serving_test_deployment(container, finance_revision=finance_revision)
        ) as deployment:
            create.assert_called_once_with(container, "butlers", _owned_creation=[])
            assert deployment.migration_url == "normal-owner-url"
            assert stage.call_count > 0
            assert upgrade.await_count > stage.call_count
            assert all(call.kwargs["connection"] is held for call in stage.call_args_list)
            assert all(call.kwargs["connection"] is held for call in upgrade.await_args_list)
            assert all(call.args[0] == "normal-owner-url" for call in upgrade.await_args_list)
            finalize.assert_called_once()
            assert finalize.call_args.args == (held,)
            assert finalize.call_args.kwargs["source_database"] == "butlers"
            selected_finance = [
                call for call in stage.call_args_list if call.kwargs.get("chain") == "finance"
            ]
            finance_heads = [
                call for call in upgrade.await_args_list if call.kwargs.get("chain") == "finance"
            ]
            finance_plans = [
                row for row in finalize.call_args.kwargs["plans"] if row[0] == "finance"
            ]
            if finance_revision is None:
                assert selected_finance == []
                assert len(finance_heads) == 1
                assert finance_plans == [("finance", "finance", None)]
            else:
                assert len(selected_finance) == 1
                assert selected_finance[0].kwargs == {
                    "chain": "finance",
                    "schema": "finance",
                    "revision": "finance_018",
                    "connection": held,
                }
                assert finance_heads == []
                assert finance_plans == [("finance", "finance", "finance_018")]
            runtime = deployment.runtime_database("general")
            assert runtime.user != "selected_control"
            assert runtime.role == "butler_general_rw"
            assert runtime.db_name == "butlers"
            assert deployment.purpose_logins["dashboard"].role == "dashboard_api"
            assert deployment.purpose_logins["credential"].role == "credential_store_writer"
            assert ("connector" in deployment.purpose_logins) is include_connector
            if include_connector:
                assert deployment.purpose_logins["connector"].role == "connector_writer"
            created = [sql for sql, _ in calls if sql.startswith("CREATE ROLE")]
            if include_connector:
                assert len(created) == len(deployment.runtime_logins) + 3
            else:
                assert len(created) == len(deployment.runtime_logins) + 2
            assert all("LOGIN NOINHERIT NOSUPERUSER" in sql for sql in created)
            grants = [sql for sql, _ in calls if sql.startswith("GRANT")]
            assert len(grants) == len(created)
            assert all(" ON " not in sql and " TO " in sql for sql in grants)
        drops = [sql for sql, _ in calls if sql.startswith("DROP ROLE")]
        assert len(drops) == len(created)
        engine.dispose.assert_called()
        # An unacknowledged/ambiguous CREATE never owns a drop, while an
        # earlier acknowledged role still receives independent cleanup.
        for failed_ordinal in (1, 2):
            calls.clear()
            create_count[0] = 0
            fail_create[0] = failed_ordinal
            fail_revoke[0] = failed_ordinal == 2
            with pytest.raises(RuntimeError, match="not acknowledged") as original:
                with (
                    migration.serving_test_deployment(
                        container, include_connector=True, finance_revision=finance_revision
                    )
                    if include_connector
                    else migration.serving_test_deployment(
                        container, finance_revision=finance_revision
                    )
                ):
                    pytest.fail("Partial serving identity setup was published")
            attempted = [sql for sql, _ in calls if sql.startswith("CREATE ROLE")]
            dropped = [sql for sql, _ in calls if sql.startswith("DROP ROLE")]
            assert len(dropped) == failed_ordinal - 1
            unacknowledged = attempted[-1].split('"')[1]
            assert all(unacknowledged not in sql for sql in dropped)
            if failed_ordinal == 2:
                assert "cleanup was incomplete" in " ".join(original.value.__notes__)
        for invalid in (None, 0, 1, "connector"):
            previous_creations = create.call_count
            with pytest.raises(ValueError):
                with migration.serving_test_deployment(container, include_connector=invalid):
                    pytest.fail("Invalid connector input was admitted")

            async def invalid_async(selected=invalid):
                async with migration.async_serving_test_deployment(
                    container, include_connector=selected
                ):
                    pytest.fail("Invalid async connector input was admitted")

            with pytest.raises(ValueError):
                asyncio.run(invalid_async())
            assert create.call_count == previous_creations

        for invalid_window in (False, True, 0, 18, "finance_019", "finance@head", ""):
            previous_creations = create.call_count
            with pytest.raises(ValueError, match="Finance predecessor"):
                with migration.serving_test_deployment(container, finance_revision=invalid_window):
                    pytest.fail("Invalid Finance window was admitted")

            async def invalid_finance(selected=invalid_window):
                async with migration.async_serving_test_deployment(
                    container, finance_revision=selected
                ):
                    pytest.fail("Invalid async Finance window was admitted")

            with pytest.raises(ValueError, match="Finance predecessor"):
                asyncio.run(invalid_finance())
            assert create.call_count == previous_creations


@pytest.mark.parametrize("finance_revision", [None, "finance_018"])
@pytest.mark.parametrize("cancel_phase", ["enter", "resources", "exit"])
@pytest.mark.parametrize("resource_fails", [False, True])
async def test_disposable_serving_deployment_joins_cancelled_worker_and_cleanup(
    cancel_phase, resource_fails, finance_revision
):
    """No cancelled caller abandons a creating or closing testcontainer worker."""
    import threading
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers.testing import migration

    loop = asyncio.get_running_loop()
    reached = asyncio.Event()
    release = threading.Event()
    order = []

    async def close_resources():
        order.append("resources_closed")
        if cancel_phase == "resources":
            reached.set()
            await asyncio.to_thread(release.wait, 5)
        if resource_fails:
            raise ValueError("Actual resource close failed")

    resource_closed = AsyncMock(side_effect=close_resources)

    @contextmanager
    def physical_producer(container, **options):
        assert options == ({"finance_revision": finance_revision} if finance_revision else {})
        if cancel_phase == "enter":
            loop.call_soon_threadsafe(reached.set)
            release.wait(5)
        order.append("prepared")
        try:
            yield SimpleNamespace(close_resources=resource_closed)
        finally:
            if cancel_phase == "exit":
                loop.call_soon_threadsafe(reached.set)
                release.wait(5)
            order.append("identities_closed")

    async def consumer():
        async with migration.async_serving_test_deployment(
            object(), finance_revision=finance_revision
        ):
            order.append("body")

    with patch.object(migration, "serving_test_deployment", physical_producer):
        task = asyncio.create_task(consumer())
        try:
            await asyncio.wait_for(reached.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert "identities_closed" not in order
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            resource_closed.assert_awaited_once()
            assert order.index("resources_closed") < order.index("identities_closed")
            if cancel_phase == "enter":
                assert "body" not in order
            else:
                assert "body" in order
        finally:
            release.set()
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)


async def test_disposable_serving_deployment_preserves_body_and_resource_failure_identity():
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers.testing import migration

    for body_fails in (False, True):
        resource_error = LookupError("Actual resource cleanup error")
        body_error = ValueError("Actual body error")
        identities_closed = []

        @contextmanager
        def physical_producer(
            container, resource_error=resource_error, identities_closed=identities_closed
        ):
            try:
                yield SimpleNamespace(close_resources=AsyncMock(side_effect=resource_error))
            finally:
                identities_closed.append(True)

        with patch.object(migration, "serving_test_deployment", physical_producer):
            with pytest.raises(ValueError if body_fails else LookupError) as observed:
                async with migration.async_serving_test_deployment(object()):
                    if body_fails:
                        raise body_error
            assert observed.value is (body_error if body_fails else resource_error)
            assert identities_closed == [True]


def test_explicit_disposable_migration_plans_keep_one_held_session_and_normal_subject():
    """Execute the real test producer; physical driver boundaries alone are doubled."""
    from contextlib import contextmanager

    from butlers.testing import migration

    held = object()
    container = object()
    normal = "postgresql://migration_selected@localhost/selected"
    plans = (
        ("core", "general", None),
        ("memory", "general_memory", None),
        ("core", "switchboard", None),
        ("switchboard", "switchboard", "sw_021"),
        ("switchboard", "switchboard", "sw_021"),
    )
    lifetime = []

    @contextmanager
    def maintenance(url, *, source_database):
        assert url == "postgresql://selected_control@localhost/selected"
        assert source_database == "butlers"
        lifetime.append("enter")
        try:
            yield held
        finally:
            lifetime.append("exit")

    with (
        patch.object(
            migration,
            "migration_bootstrap_db_url",
            return_value="postgresql://selected_control@localhost/selected",
        ) as selected,
        patch("butlers.migrations.migration_session", maintenance),
        patch("butlers.migrations.run_migrations", new_callable=AsyncMock) as upgrade,
        patch.object(migration, "_upgrade_chain_to_revision") as bounded,
        patch("butlers.migrations.finalize_authority") as finalize,
    ):
        assert migration.migrate_test_database(container, normal, plans=plans) == normal
        selected.assert_called_once_with(container, "selected")
        assert upgrade.await_args_list == [
            call(normal, chain="core", schema="general", connection=held),
            call(normal, chain="memory", schema="general_memory", connection=held),
            call(normal, chain="core", schema="switchboard", connection=held),
        ]
        assert bounded.call_args_list == [
            call(
                normal,
                chain="switchboard",
                schema="switchboard",
                revision="sw_021",
                connection=held,
            ),
            call(
                normal,
                chain="switchboard",
                schema="switchboard",
                revision="sw_021",
                connection=held,
            ),
        ]
        finalize.assert_called_once_with(held, source_database="butlers", plans=plans)
        assert lifetime == ["enter", "exit"]
        finalize.reset_mock()
        lifetime.clear()
        failure = RuntimeError("Bounded revision failed")
        bounded.side_effect = failure
        with pytest.raises(RuntimeError) as actual:
            migration.migrate_test_database(container, normal, plans=plans)
        assert actual.value is failure
        finalize.assert_not_called()
        assert lifetime == ["enter", "exit"]


@pytest.mark.parametrize("credential_tail_preexists", [False, True])
async def test_disposable_deployment_joins_late_credential_tail_before_dependent_close(
    credential_tail_preexists,
):
    """Compose actual lifecycle, diagnostic ownership and disposable cleanup without SQL."""
    import asyncio
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers.db_authority.catalog import observe_catalog_owned
    from butlers.lifecycle import _shutdown_resources
    from butlers.testing import migration

    release = asyncio.Event()
    entered = asyncio.Event()
    settle_owner = asyncio.Event()
    main_closed = asyncio.Event()
    order = []

    async def physical_diagnostic():
        entered.set()
        await release.wait()
        order.append("credential_released")

    async def credential_close():
        order.append("credential_closed")

    async def main_close():
        if not main_closed.is_set():
            order.append("main_closed")
            main_closed.set()

    pool = SimpleNamespace(close=AsyncMock(side_effect=credential_close))
    main = SimpleNamespace(close=AsyncMock(side_effect=main_close))
    daemon = SimpleNamespace(
        config=SimpleNamespace(shutdown_timeout_s=0),
        _chronicler_refresh_service=SimpleNamespace(
            closed=False,
            close=AsyncMock(return_value=False),
            settle=settle_owner.wait,
        ),
        _server=None,
        _server_task=None,
        _mcp_socket=None,
        _buffer=None,
        _route_inbox_tasks=set(),
        spawner=None,
        _switchboard_heartbeat_task=None,
        _disconnect_switchboard=AsyncMock(),
        _scheduler_loop_task=None,
        _route_inbox_recovery_task=None,
        _liveness_reporter_task=None,
        _active_modules=[],
        _modules=[],
        blob_store=None,
        _db_log_handler=None,
        _credential_purpose_pool=pool,
        db=main,
    )

    async def shutdown():
        await _shutdown_resources(daemon)

    daemon.shutdown = shutdown
    deployment = migration.ServingTestDeployment("localhost", 5432, "normal", {}, {})
    deployment.retain(main)
    deployment.retain(daemon, "shutdown")

    @contextmanager
    def physical_producer(container):
        try:
            yield deployment
        finally:
            order.append("identities_closed")

    diagnostic = asyncio.create_task(observe_catalog_owned(pool, physical_diagnostic))
    await entered.wait()
    diagnostic.cancel()
    with pytest.raises(asyncio.CancelledError):
        await diagnostic
    if credential_tail_preexists:
        with pytest.raises(TimeoutError):
            await _shutdown_resources(daemon)
        settle_owner.set()
        for _ in range(100):
            if hasattr(daemon, "_credential_dependents_close_task"):
                break
            await asyncio.sleep(0.01)
        assert hasattr(daemon, "_credential_dependents_close_task")
    body_done = asyncio.Event()

    async def consumer():
        async with migration.async_serving_test_deployment(object()):
            body_done.set()

    with patch.object(migration, "serving_test_deployment", physical_producer):
        caller = asyncio.create_task(consumer())
        try:
            await asyncio.wait_for(body_done.wait(), 1)
            for _ in range(100):
                if hasattr(daemon, "_chronicler_cleanup_task"):
                    break
                await asyncio.sleep(0.01)
            assert hasattr(daemon, "_chronicler_cleanup_task")
            if not credential_tail_preexists:
                assert not hasattr(daemon, "_credential_dependents_close_task")
            caller.cancel()
            await asyncio.sleep(0)
            settle_owner.set()
            first = daemon._chronicler_cleanup_task
            with pytest.raises(TimeoutError):
                # Exercise the actual independent five-second lifecycle bound.
                await asyncio.wait_for(asyncio.shield(first), 6)
            caller.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not caller.done()
            assert not daemon._credential_dependents_close_task.done()
            assert not main_closed.is_set()
            pool.close.assert_not_awaited()
            assert "identities_closed" not in order
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(caller, 1)
            assert order == [
                "credential_released",
                "credential_closed",
                "main_closed",
                "identities_closed",
            ]
            assert daemon._credential_dependents_close_task.done()
            assert daemon._credential_purpose_pool is None
        finally:
            settle_owner.set()
            release.set()
            tasks = [
                caller,
                *[
                    vars(daemon)[n]
                    for n in ("_chronicler_cleanup_task", "_credential_dependents_close_task")
                    if n in vars(daemon)
                ],
            ]
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_disposable_held_transition_settles_real_worker_before_caller_cancellation(
    monkeypatch,
):
    """The actual async adapter retains a running selected migration through cancellation."""
    import threading
    from types import SimpleNamespace

    from butlers.testing import migration

    loop = asyncio.get_running_loop()
    started, release, exited = asyncio.Event(), threading.Event(), threading.Event()
    config = SimpleNamespace()
    failure = LookupError("selected exact transition failed")

    def transition(container, operation, selected_config, revision):
        assert container is marker and operation is actual_operation
        assert selected_config is config and revision == "core@core_255"
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5)
            raise failure
        finally:
            exited.set()

    marker, actual_operation = object(), object()
    monkeypatch.setattr(migration, "run_test_alembic_command", transition)
    task = asyncio.create_task(
        migration.async_run_test_alembic_command(marker, actual_operation, config, "core@core_255")
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not exited.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert exited.is_set()

    monkeypatch.setattr(migration, "run_test_alembic_command", Mock(side_effect=failure))
    with pytest.raises(LookupError) as actual:
        await migration.async_run_test_alembic_command(
            marker, actual_operation, config, "core@core_255"
        )
    assert actual.value is failure


def test_historical_disposable_command_keeps_exact_graph_normal_role_and_owned_session(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers import migrations
    from butlers.db_authority import interfaces
    from butlers.testing import migration

    events = []
    connection = SimpleNamespace(commit=Mock(), rollback=Mock())
    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://migration_subject@localhost/test_exact_history"
    marker = LookupError("historical SQL denial")

    @contextmanager
    def session(url, *, source_database):
        assert url == "postgresql://control@localhost/test_exact_history"
        assert source_database == "butlers"
        events.append("maintenance_enter")
        try:
            yield connection
        finally:
            events.append("maintenance_exit")

    @contextmanager
    def ordinary(selected):
        assert selected is connection
        events.append("ordinary_enter")
        try:
            yield
        finally:
            events.append("ordinary_exit")

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "ordinary_migration_identity", ordinary)
    monkeypatch.setattr(interfaces, "stage_dashboard_interfaces", lambda c: events.append("stage"))
    config = migrations._build_alembic_config(normal, ["core"], target_schema="health")

    def upgrade(selected, target):
        assert selected is config and selected.attributes["connection"] is connection
        assert selected.get_main_option("sqlalchemy.url") == normal
        assert selected.get_main_option("butlers.target_schema") == "health"
        assert target in {"core@core_255", "core@core_280"}
        assert events[-1] == "ordinary_enter"
        events.append(target)
        if target == "core@core_255":
            raise marker
        return "actual command result"

    with pytest.raises(LookupError) as failure:
        migration.run_test_alembic_command(container, upgrade, config, "core@core_255")
    assert failure.value is marker
    assert events == [
        "maintenance_enter",
        "ordinary_enter",
        "core@core_255",
        "ordinary_exit",
        "maintenance_exit",
    ]
    assert "connection" not in config.attributes
    events.clear()
    assert (
        migration.run_test_alembic_command(container, upgrade, config, "core@core_280")
        == "actual command result"
    )
    assert events == [
        "maintenance_enter",
        "stage",
        "ordinary_enter",
        "core@core_280",
        "ordinary_exit",
        "maintenance_exit",
    ]
    assert "connection" not in config.attributes
    config.attributes["connection"] = connection
    events.clear()
    with pytest.raises(RuntimeError, match="already owns a connection"):
        migration.run_test_alembic_command(container, upgrade, config, "core@core_255")
    assert not events and config.attributes["connection"] is connection


def test_disposable_partial_transition_finalizes_complete_prior_plan_without_replaying_seeds(
    monkeypatch,
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers import migrations
    from butlers.testing import migration

    connection = object()
    events = []
    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://normal@localhost/selected_history"
    prior = (("core", "relationship", None), ("relationship", "relationship", None))
    transitions = (("core", "health", None), ("memory", "health", "memory_020"))
    complete = prior + transitions + (("memory", "health", "memory_020"),)

    @contextmanager
    def session(url, *, source_database):
        assert url == "postgresql://control@localhost/selected_history"
        assert source_database == "butlers"
        events.append("enter")
        try:
            yield connection
        finally:
            events.append("exit")

    async def run(url, *, chain, schema, connection):
        assert url == normal and connection is held
        events.append((chain, schema, None))

    def bounded(url, *, chain, schema, revision, connection):
        assert url == normal and connection is held
        events.append((chain, schema, revision))

    def finalize(selected, *, source_database, plans):
        assert selected is held and source_database == "butlers"
        assert plans == complete
        assert events == ["enter", *transitions]
        events.append("finalize")

    held = connection
    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "run_migrations", run)
    monkeypatch.setattr(migrations, "finalize_authority", finalize)
    monkeypatch.setattr(migration, "_upgrade_chain_to_revision", bounded)
    assert (
        migration.migrate_test_database(container, normal, plans=transitions, final_plans=complete)
        == normal
    )
    assert events == ["enter", *transitions, "finalize", "exit"]


def test_historical_window_uses_real_pre_cutover_graph_without_installing_current_authority(
    monkeypatch,
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers import migrations
    from butlers.testing import migration

    events = []
    held = object()
    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://normal@localhost/history_window"

    @contextmanager
    def session(url, *, source_database):
        assert url == "postgresql://control@localhost/history_window"
        assert source_database == "butlers"
        events.append("enter")
        try:
            yield held
        finally:
            events.append("exit")

    def bounded(url, *, chain, schema, revision, connection):
        assert url == normal and connection is held
        events.append((chain, schema, revision))

    async def run(url, *, chain, schema, connection):
        assert url == normal and connection is held
        events.append((chain, schema, None))

    def forbidden_finalizer(*args, **kwargs):
        pytest.fail("Historical window must not install current authority before its observations")

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "run_migrations", run)
    monkeypatch.setattr(migrations, "finalize_authority", forbidden_finalizer)
    monkeypatch.setattr(migration, "_upgrade_chain_to_revision", bounded)
    plans = (("core", "general", "core_280"), ("relationship", "relationship", None))
    assert migration.stage_test_migration_history(container, normal, plans=plans) == normal
    assert events == ["enter", *plans, "exit"]
    events.clear()
    pre_cutover = (("core", "general", "core_281"),)
    assert migration.stage_test_migration_history(container, normal, plans=pre_cutover) == normal
    assert events == ["enter", *pre_cutover, "exit"]
    events.clear()
    for revision in (None, "core_282"):
        with pytest.raises(ValueError, match="explicit pre-core_282"):
            migration.stage_test_migration_history(
                container, normal, plans=(("core", "general", revision),)
            )
        assert events == []
    with pytest.raises(RuntimeError, match="not source-owned"):
        migration.stage_test_migration_history(
            container, normal, plans=(("core", "general", "invented_revision"),)
        )
    assert events == []


async def test_serving_deployment_reentry_drops_only_acknowledged_database_after_owned_resources(
    monkeypatch,
):
    """Execute actual producer/wrapper twice; only network/SQL drivers are doubled."""
    import asyncio
    import re
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers import migrations
    from butlers.testing import migration

    events, roles, databases = [], {}, {}
    serial = [100]
    ambiguous_database = [False]

    def next_oid():
        serial[0] += 1
        return serial[0]

    class Result:
        def __init__(self, row):
            self.row = row

        def scalar_one(self):
            return self.row[0]

        def one(self):
            assert self.row is not None
            return self.row

        def one_or_none(self):
            return self.row

    class Connection:
        def execute(self, statement, params=None):
            sql = str(statement).strip()
            if sql.startswith("SELECT oid FROM pg_catalog.pg_roles"):
                return Result(
                    None
                    if params.get("role", params.get("name")) not in roles
                    else (roles[params.get("role", params.get("name"))],)
                )
            if sql.startswith("SELECT oid,datdba FROM pg_catalog.pg_database"):
                return Result(databases.get(params.get("database", params.get("name"))))
            if sql.startswith("CREATE DATABASE"):
                name, owner = re.findall(r'"([^\"]+)"', sql)
                if name in databases:
                    raise RuntimeError("Database already exists")
                databases[name] = (next_oid(), roles[owner])
                events.append("database_created")
                if ambiguous_database[0]:
                    raise RuntimeError("CREATE acknowledgement unavailable")
            elif sql.startswith("CREATE ROLE"):
                name = re.findall(r'"([^\"]+)"', sql)[0]
                assert name not in roles
                roles[name] = next_oid()
            elif sql.startswith("SELECT NOT rolcanlogin"):
                return Result((True,))
            else:
                pytest.fail("Unexpected disposable driver statement")
            return Result((True,))

        def exec_driver_sql(self, sql):
            if sql.startswith("DROP DATABASE"):
                assert events[-1] == "resources_closed"
                name = re.findall(r'"([^\"]+)"', sql)[0]
                del databases[name]
                events.append("database_dropped")
            elif sql.startswith("DROP ROLE"):
                name = re.findall(r'"([^\"]+)"', sql)[0]
                if any(owner == roles[name] for _, owner in databases.values()):
                    raise RuntimeError("Role still owns a database")
                del roles[name]
            elif not sql.startswith(("GRANT ", "REVOKE ")):
                pytest.fail("Unexpected disposable driver command")

    @contextmanager
    def connect():
        yield Connection()

    engine = SimpleNamespace(connect=connect, dispose=lambda: None)
    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://selected_control@localhost/postgres",
        get_container_host_ip=lambda: "localhost",
        get_exposed_port=lambda _: 5432,
    )

    def create_role(url, role, password):
        assert url == container.get_connection_url() and password
        if role in roles:
            raise RuntimeError("Role already exists")
        roles[role] = next_oid()

    @contextmanager
    def maintenance(url, *, source_database):
        assert url == "postgresql://selected_control@localhost/butlers"
        assert source_database == "butlers"
        yield object()

    class Resource:
        async def close(self):
            events.append("resources_closed")

    monkeypatch.setattr(migration, "create_engine", lambda *a, **k: engine)
    monkeypatch.setattr(migration, "_create_test_migration_role", create_role)
    monkeypatch.setattr(migration, "bootstrap_extensions", lambda *_: None)
    monkeypatch.setattr(migration, "_bootstrap_migration_prerequisites", lambda *_: None)
    monkeypatch.setattr(migration, "_upgrade_chain_to_revision", lambda *a, **k: None)
    monkeypatch.setattr(migrations, "migration_session", maintenance)
    monkeypatch.setattr(migrations, "run_migrations", AsyncMock())
    monkeypatch.setattr(migrations, "finalize_authority", lambda *a, **k: None)
    for _ in range(2):
        async with migration.async_serving_test_deployment(container) as deployment:
            assert "butlers" in databases
            deployment.retain(Resource())
        assert not databases and not roles
    assert events == [
        "database_created",
        "resources_closed",
        "database_dropped",
        "database_created",
        "resources_closed",
        "database_dropped",
    ]
    # Cancellation cannot advance identity teardown while a retained physical
    # resource is still closing, including repeated cancellation of the caller.
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class BlockedResource:
        async def close(self):
            closing.set()
            await release.wait()
            events.append("resources_closed")

    async def cancelled_deployment():
        async with migration.async_serving_test_deployment(container) as deployment:
            deployment.retain(BlockedResource())
            entered.set()
            await asyncio.Future()

    caller = asyncio.create_task(cancelled_deployment())
    await entered.wait()
    caller.cancel()
    await closing.wait()
    caller.cancel()
    await asyncio.sleep(0)
    assert not caller.done() and "butlers" in databases and roles
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not databases and not roles
    assert events[-2:] == ["resources_closed", "database_dropped"]

    # A failed CREATE ROLE never owns an existing role, even with the expected
    # disposable name. An obscured CREATE DATABASE acknowledgement also cannot
    # authorize dropping the database merely because it now exists.
    roles["migration_butlers"] = 700
    with pytest.raises(RuntimeError, match="Role already exists"):
        async with migration.async_serving_test_deployment(container):
            pytest.fail("Preexisting normal role was admitted as ours")
    assert roles == {"migration_butlers": 700} and not databases
    roles.clear()
    ambiguous_database[0] = True
    with pytest.raises(RuntimeError, match="acknowledgement unavailable") as ambiguous:
        async with migration.async_serving_test_deployment(container):
            pytest.fail("Ambiguous database creation was admitted as owned")
    assert "butlers" in databases and "migration_butlers" in roles
    assert any("cleanup was incomplete" in note for note in ambiguous.value.__notes__)
    # Only the IO double's catalog is reset between independent scenarios.
    databases.clear()
    roles.clear()
    ambiguous_database[0] = False
    # A preexisting database is not a creator receipt. Only our newly
    # acknowledged normal role may be removed after CREATE DATABASE refuses.
    databases["butlers"] = (777, 888)
    with pytest.raises(RuntimeError, match="already exists"):
        async with migration.async_serving_test_deployment(container):
            pytest.fail("Preexisting database was admitted as ours")
    assert databases == {"butlers": (777, 888)} and not roles
    # Even an old valid receipt must refuse a replaced foreign catalog identity.
    roles["foreign"] = 901
    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        migration._close_created_test_database(container, [("role", "foreign", 900, None)])
    assert roles == {"foreign": 901}


def test_pinned_historical_environment_preserves_physical_transaction_and_failure_ownership(
    monkeypatch,
    tmp_path,
):
    """Actual pinned online/Alembic/SQLAlchemy transaction protocol; no PostgreSQL IO."""
    import asyncio
    import hashlib
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace

    from alembic.config import Config
    from alembic.runtime.environment import EnvironmentContext
    from alembic.script import ScriptDirectory
    from alembic.util import load_python_file
    from sqlalchemy import create_engine, event

    from butlers import migrations
    from butlers.testing import migration

    source = (
        Path(__file__).resolve().parents[1] / "migrations/data/core_255_env_a090a8ac.py.txt"
    ).read_text()
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert digest == "d8a717988914d2c697db58d3e13212ed3ca3d69df6f9e66f8ffdc61487b46a1d"
    engine = create_engine(f"sqlite:///{tmp_path / 'transactions.sqlite'}")
    events, held = [], []
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE retained (value INTEGER)")

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def physical_sql(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(("SET ROLE ", "RESET ROLE", "CREATE SCHEMA ", "SET search_path")):
            events.append(statement)
            return "SELECT 1", ()
        return statement, parameters

    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://normal@localhost/history_adapter"
    control = "postgresql://control@localhost/history_adapter"

    @contextmanager
    def session(url, *, source_database):
        assert url == control and source_database == "butlers"
        with engine.connect() as connection:
            held.append(connection)
            events.append("lock_enter")
            connection.exec_driver_sql("SELECT 1")  # actual SQLAlchemy autobegin
            try:
                yield connection
            finally:
                assert not connection.closed
                connection.rollback()
                events.append("lock_exit")
                held.pop()

    @contextmanager
    def ordinary(connection):
        connection.exec_driver_sql("SET ROLE normal")
        try:
            yield connection
        finally:
            connection.exec_driver_sql("RESET ROLE")

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "ordinary_migration_identity", ordinary)
    versions = tmp_path / "versions"
    versions.mkdir()
    attempt = 0
    try:
        for schema in (None, "general"):
            for administrative in (False, True):
                for failure in (None, "error", "cancel"):
                    attempt += 1
                    config = Config()
                    config.set_main_option("sqlalchemy.url", control if administrative else normal)
                    config.set_main_option("version_locations", str(versions))
                    config.set_main_option("path_separator", "os")
                    if schema is not None:
                        config.set_main_option("butlers.target_schema", schema)
                    migration.bind_historical_test_environment(
                        config, tmp_path / f"online_{attempt}", source, expected_sha256=digest
                    )
                    primary = (
                        asyncio.CancelledError("historical caller cancelled")
                        if failure == "cancel"
                        else LookupError("actual historical operation failed")
                    )

                    def command(
                        config, revision, *, value=attempt, failure_kind=failure, original=primary
                    ):
                        assert revision == "historical_protocol_control"
                        script = ScriptDirectory.from_config(config)

                        def actual_operation(revision, context):
                            assert context.connection is held[-1]
                            assert not context._in_external_transaction
                            context.connection.exec_driver_sql(
                                "INSERT INTO retained(value) VALUES (?)", (value,)
                            )
                            if failure_kind is not None:
                                raise original
                            return []

                        with EnvironmentContext(config, script, fn=actual_operation):
                            load_python_file(script.dir, "env.py")

                    if failure is not None:
                        with pytest.raises(type(primary)) as error:
                            migration.run_historical_test_alembic_command(
                                container,
                                command,
                                config,
                                "historical_protocol_control",
                                normal_url=normal,
                                administrative=administrative,
                            )
                        assert error.value is primary
                    else:
                        migration.run_historical_test_alembic_command(
                            container,
                            command,
                            config,
                            "historical_protocol_control",
                            normal_url=normal,
                            administrative=administrative,
                        )
                    assert not held and events[-1] == "lock_exit"
                    assert not any(k.startswith("historical.") for k in config.attributes)
                    with engine.connect() as independent:
                        observed = independent.exec_driver_sql(
                            "SELECT count(*) FROM retained WHERE value=?", (attempt,)
                        ).scalar_one()
                    assert observed == (0 if failure is not None else 1)
        assert any(s.startswith("CREATE SCHEMA ") for s in events)
        assert "SET ROLE normal" in events and "RESET ROLE" in events
        before = len(events)
        config.set_main_option("sqlalchemy.url", "postgresql://foreign@localhost/history_adapter")
        with pytest.raises(AssertionError, match="explicit selected subject"):
            migration.run_historical_test_alembic_command(
                container, command, config, "historical_protocol_control", normal_url=normal
            )
        assert len(events) == before
        with pytest.raises(AssertionError, match="Pinned historical environment"):
            migration.bind_historical_test_environment(
                config, tmp_path / "invalid", source + "\n", expected_sha256=digest
            )
    finally:
        engine.dispose()


def test_core255_historical_fixture_binds_pinned_sources_without_redirecting_current_replay(
    monkeypatch,
    tmp_path,
):
    import ast
    import asyncio
    import hashlib
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace

    from butlers.testing import migration

    owner = migration.load_migration(
        Path(__file__).resolve().parents[1]
        / "migrations/test_core_255_insight_premise_binding_migration.py"
    )
    calls, current, created_environments = [], [], []
    normal = "postgresql://normal@localhost/history_source"
    control = "postgresql://control@localhost/history_source"
    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )

    class Connection:
        def exec_driver_sql(self, statement, parameters=None):
            calls.append((statement, parameters))

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(
        owner,
        "create_engine",
        lambda *a, **k: SimpleNamespace(connect=connection, dispose=lambda: None),
    )
    monkeypatch.setattr(owner, "_create_current_migration_db", lambda *_: normal)
    monkeypatch.setattr(
        owner, "_prepare_current_migration_prerequisites", lambda *a: current.append(a)
    )
    monkeypatch.setattr(owner, "_run_current_test_alembic_command", lambda *a: current.append(a))

    def historical(container_arg, operation, config, revision, *, normal_url, administrative):
        assert container_arg is container and normal_url == normal
        assert administrative == (config.get_main_option("sqlalchemy.url") == control)
        environment = Path(config.get_main_option("script_location")) / "env.py"
        created_environments.append(environment)
        old, new = ast.parse(owner._PINNED_ENV.read_text()), ast.parse(environment.read_text())

        def online(tree):
            return next(
                n
                for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "run_migrations_online"
            )

        assert ast.dump(online(old)) == ast.dump(online(new))
        calls.append(("historical", revision))
        return operation(config, revision)

    monkeypatch.setattr(owner, "run_historical_test_alembic_command", historical)
    pinned = owner._PINNED_INIT.read_text()
    assert hashlib.sha256(pinned.encode()).hexdigest() == owner._PINNED_INIT_DIGEST

    @owner._pinned_historical_producers
    def exercise(primary=None):
        assert owner.create_migration_db(container, "history_source") == normal
        for selected in (normal, control):
            config = owner._build_alembic_config(selected, ["core"])
            assert (
                owner.run_test_alembic_command(container, lambda *_: "done", config, "core_255")
                == "done"
            )
        neutral = tmp_path / "neutral.sql"
        start = pinned.index("-- AMENDMENT_OWNER_CONVERGENCE_BEGIN")
        end = pinned.index("-- AMENDMENT_OWNER_CONVERGENCE_END") + len(
            "-- AMENDMENT_OWNER_CONVERGENCE_END"
        )
        neutral.write_text(pinned[:start] + pinned[end:])
        with monkeypatch.context() as scoped:
            scoped.setattr(migration, "_INIT_DB", neutral)
            owner._bootstrap_migration_prerequisites(control, "normal")
        if primary is not None:
            raise primary

    exercise()
    for primary in (LookupError("body failure"), asyncio.CancelledError("body cancelled")):
        with pytest.raises(type(primary)) as raised:
            exercise(primary)
        assert raised.value is primary
        assert owner._HISTORICAL_CONTEXT.get() is None
    assert all(not p.exists() for p in created_environments)
    assert (pinned.replace("\\set ON_ERROR_STOP on\n", "", 1), None) in calls
    assert sum(statement == "historical" for statement, _ in calls) == 6
    assert current == []
    owner._bootstrap_migration_prerequisites(control, "normal")
    owner.run_test_alembic_command(container, lambda *_: None, object(), "core_254")
    assert len(current) == 2
    # The actual canonical negative retains separate current preparation,
    # current stamp and migrate_test_database calls, not the pinned adapter.
    negative = ast.parse(Path(owner.__file__).read_text())
    negative = next(
        n
        for n in negative.body
        if isinstance(n, ast.FunctionDef) and n.name == "_ordinary_bootstrap_owned_core_replay"
    )
    invoked = {
        n.func.id
        for n in ast.walk(negative)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert {
        "_prepare_current_migration_prerequisites",
        "_run_current_test_alembic_command",
        "migrate_test_database",
    } <= invoked
    assert "run_test_alembic_command" not in invoked


def test_current_held_alembic_command_commits_public_and_schema_or_rolls_back_primary(
    monkeypatch, tmp_path
):
    """Actual current env + SQLAlchemy/Alembic transaction ownership, without PG IO."""
    import asyncio
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace

    from alembic.config import Config
    from alembic.runtime.environment import EnvironmentContext
    from alembic.script import ScriptDirectory
    from alembic.util import load_python_file
    from sqlalchemy import create_engine, event

    from butlers import bootstrap_prerequisite, migrations
    from butlers.testing import migration

    engine = create_engine(f"sqlite:///{tmp_path / 'current_transactions.sqlite'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE retained (value INTEGER)")
    events, held = [], []

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def physical_sql(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(("SET ROLE ", "RESET ROLE", "CREATE SCHEMA ", "SET search_path")):
            events.append(statement)
            return "SELECT 1", ()
        if statement.startswith("SELECT pg_catalog.to_regclass("):
            return "SELECT NULL", ()  # fresh selected version table, before real operation
        return statement, parameters

    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://normal@localhost/current_protocol"
    control = "postgresql://control@localhost/current_protocol"

    @contextmanager
    def session(url, *, source_database):
        assert url == control and source_database == "butlers"
        with engine.connect() as connection:
            held.append(connection)
            events.append("lock_enter")
            try:
                yield connection
            finally:
                assert not connection.closed
                connection.rollback()
                events.append("lock_exit")
                held.pop()

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "maintenance_profile", lambda connection: "normal")
    monkeypatch.setattr(
        bootstrap_prerequisite, "check_bootstrap_connection", lambda connection: None
    )
    attempt = 0
    try:
        for schema in (None, "general"):
            for failure in (None, "error", "cancel"):
                attempt += 1
                config = Config()
                config.set_main_option("sqlalchemy.url", normal)
                config.set_main_option("script_location", str(Path(migrations.ALEMBIC_DIR)))
                config.set_main_option("path_separator", "os")
                if schema is not None:
                    config.set_main_option("butlers.target_schema", schema)
                primary = (
                    asyncio.CancelledError("current operation cancelled")
                    if failure == "cancel"
                    else LookupError("current operation failed")
                )
                observed_context = []

                def command(
                    config,
                    revision,
                    *,
                    observed=observed_context,
                    value=attempt,
                    failure_kind=failure,
                    original=primary,
                ):
                    assert revision == "current_protocol"
                    script = ScriptDirectory.from_config(config)

                    def actual_operation(revision, context):
                        assert context.connection is held[-1]
                        observed.append(context._in_external_transaction)
                        context.connection.exec_driver_sql(
                            "INSERT INTO retained(value) VALUES (?)", (value,)
                        )
                        if failure_kind is not None:
                            raise original
                        return []

                    # PostgreSQL uses transactional DDL. The real SQLite connection
                    # supplies SQLAlchemy transaction behavior; only PG control IO
                    # above is translated, and the actual current env runs unchanged.
                    with EnvironmentContext(
                        config,
                        script,
                        fn=actual_operation,
                        destination_rev="current_protocol",
                        transactional_ddl=True,
                    ):
                        load_python_file(script.dir, "env.py")
                    return "actual current command result"

                if failure is None:
                    result = migration.run_test_alembic_command(
                        container, command, config, "current_protocol"
                    )
                    assert result == "actual current command result"
                else:
                    with pytest.raises(type(primary)) as error:
                        migration.run_test_alembic_command(
                            container, command, config, "current_protocol"
                        )
                    assert error.value is primary
                assert observed_context == [False]
                assert "connection" not in config.attributes
                assert not held and events[-1] == "lock_exit"
                with engine.connect() as independent:
                    count = independent.exec_driver_sql(
                        "SELECT count(*) FROM retained WHERE value=?", (attempt,)
                    ).scalar_one()
                assert count == (1 if failure is None else 0)
        assert sum(s == 'SET ROLE "normal"' for s in events) == 12
        assert sum(s == "RESET ROLE" for s in events) == 12
    finally:
        engine.dispose()


def test_current_environment_preserves_successive_public_and_schema_chain_transactions(
    monkeypatch, tmp_path
):
    """Real current-env transaction boundaries across actual selected helper routes."""
    import asyncio
    from contextlib import contextmanager
    from types import SimpleNamespace

    from alembic.runtime.environment import EnvironmentContext
    from alembic.script import ScriptDirectory
    from alembic.util import load_python_file
    from sqlalchemy import create_engine, event

    from alembic import command
    from butlers import bootstrap_prerequisite, migrations
    from butlers.db_authority import interfaces
    from butlers.testing import migration

    engine = create_engine(f"sqlite:///{tmp_path / 'successive_transactions.sqlite'}")

    @event.listens_for(engine, "connect")
    def schema_transport(dbapi_connection, record):
        dbapi_connection.execute(
            "ATTACH DATABASE ? AS general", (str(tmp_path / "general.sqlite"),)
        )

    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE retained (value INTEGER)")
    held, events, observations = [], [], []

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def physical_sql(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(("SET ROLE ", "RESET ROLE", "CREATE SCHEMA ", "SET search_path")):
            events.append(statement)
            return "SELECT 1", ()
        if statement.startswith("SELECT pg_catalog.to_regclass("):
            return "SELECT NULL", ()
        return statement, parameters

    container = SimpleNamespace(
        get_connection_url=lambda: "postgresql://control@localhost/postgres"
    )
    normal = "postgresql://normal@localhost/current_successive"

    @contextmanager
    def session(url, *, source_database):
        assert url == "postgresql://control@localhost/current_successive"
        assert source_database == "butlers"
        with engine.connect() as connection:
            held.append(connection)
            events.append("lock_enter")
            try:
                yield connection
            finally:
                assert not connection.closed
                connection.rollback()
                held.pop()
                events.append("lock_exit")

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "maintenance_profile", lambda connection: "normal")
    monkeypatch.setattr(migrations, "check_bootstrap_connection", lambda connection: None)
    monkeypatch.setattr(
        bootstrap_prerequisite, "check_bootstrap_connection", lambda connection: None
    )
    monkeypatch.setattr(migrations, "_bootstrap_extensions", lambda *args, **kwargs: None)
    monkeypatch.setattr(interfaces, "stage_dashboard_interfaces", lambda connection: None)
    state = {}

    def execute_chain(config, target):
        assert config.attributes["connection"] is held[-1]
        state["targets"].append(target)
        script = ScriptDirectory.from_config(config)
        value = state["base"] + len(state["targets"])

        def actual_operation(revision, context):
            assert context.connection is held[-1]
            observations.append(context._in_external_transaction)
            context.connection.exec_driver_sql("INSERT INTO retained(value) VALUES (?)", (value,))
            if len(state["targets"]) == 2 and state["failure"] is not None:
                raise state["primary"]
            return []

        with EnvironmentContext(
            config, script, fn=actual_operation, destination_rev=target, transactional_ddl=True
        ):
            load_python_file(script.dir, "env.py")
        assert not config.attributes["connection"].closed

    def finalize(connection, *, source_database, plans):
        assert connection is held[-1] and source_database == "butlers"
        assert plans == state["plans"]
        # Read independently BEFORE the real finalizer's concluding commit.
        # It cannot retroactively save the first public chain from the next
        # current env's deliberate preflight rollback.
        with engine.connect() as independent:
            values = (
                independent.exec_driver_sql(
                    "SELECT value FROM retained WHERE value > ? ORDER BY value", (state["base"],)
                )
                .scalars()
                .all()
            )
        assert values == [state["base"] + 1, state["base"] + 2]
        state["finalized"] = True
        connection.commit()

    monkeypatch.setattr(command, "upgrade", execute_chain)
    monkeypatch.setattr(migrations, "finalize_authority", finalize)
    attempt = 0
    try:
        for route in ("run_migrations", "migrate_test_database", "stage_test_migration_history"):
            for schema in (None, "general"):
                for failure in (None, "error", "cancel"):
                    attempt += 1
                    plans = (("core", schema, "core_114"), ("switchboard", schema, None))
                    primary = (
                        asyncio.CancelledError("second current chain cancelled")
                        if failure == "cancel"
                        else LookupError("second current chain failed")
                    )
                    state.clear()
                    state.update(
                        base=attempt * 10,
                        targets=[],
                        plans=plans,
                        failure=failure,
                        primary=primary,
                        finalized=False,
                    )
                    observations.clear()

                    def operation(route=route, schema=schema, plans=plans):
                        if route == "run_migrations":
                            with session(
                                "postgresql://control@localhost/current_successive",
                                source_database="butlers",
                            ) as connection:
                                for chain in ("core", "switchboard"):
                                    asyncio.run(
                                        migrations.run_migrations(
                                            normal,
                                            chain=chain,
                                            schema=schema,
                                            connection=connection,
                                        )
                                    )
                        else:
                            selected = getattr(migration, route)
                            assert selected(container, normal, plans=plans) == normal

                    if failure is None:
                        operation()
                    else:
                        with pytest.raises(type(primary)) as error:
                            operation()
                        assert error.value is primary
                    assert not held and events[-1] == "lock_exit"
                    with engine.connect() as independent:
                        values = (
                            independent.exec_driver_sql(
                                "SELECT value FROM retained WHERE value > ? ORDER BY value",
                                (state["base"],),
                            )
                            .scalars()
                            .all()
                        )
                    assert values == [state["base"] + 1] + (
                        [state["base"] + 2] if failure is None else []
                    )
                    assert observations == [False, False]
                    assert state["finalized"] is (
                        route == "migrate_test_database" and failure is None
                    )
                    assert state["targets"] == (
                        ["core@head", "switchboard@head"]
                        if route == "run_migrations"
                        else ["core@core_114", "switchboard@head"]
                    )
    finally:
        engine.dispose()


def test_actual_core046_uses_physical_tables_without_acl_filtered_destination(monkeypatch):
    """Execute the real migration; its SQL/DDL IO alone is doubled."""
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    producer = runpy.run_path(
        str(Path("alembic/versions/core/core_046_migrate_user_context_to_public.py"))
    )
    upgrade = producer["upgrade"]

    def run_case(shared, public, remaining):
        events = []
        probes = []
        destination = object() if public else None
        state = {"shared": shared, "public": destination}

        def query(statement):
            sql = str(statement)
            if "count(*)" in sql:
                return SimpleNamespace(scalar=lambda: remaining)
            probes.append(sql)
            namespace = "shared" if "= 'shared'" in sql else "public"
            # The protected destination exists but is invisible through the
            # ordinary migrator's ACL-filtered information_schema view.
            if "information_schema.tables" in sql:
                present = shared if namespace == "shared" else False
            else:
                assert "pg_catalog.pg_class" in sql and "pg_catalog.pg_namespace" in sql
                assert "c.relname = 'user_context'" in sql and "c.relkind IN ('r', 'p')" in sql
                present = shared if namespace == "shared" else public
            events.append(("probe", namespace))
            return SimpleNamespace(scalar=lambda: 1 if present else None)

        def ddl(sql):
            events.append(sql)
            if sql.startswith("ALTER TABLE"):
                if state["public"] is not None:
                    raise RuntimeError("physical destination collision")
                state["public"], state["shared"] = object(), False
            elif sql == "DROP TABLE shared.user_context":
                state["shared"] = False
            else:
                assert sql == "DROP SCHEMA IF EXISTS shared"
                assert not state["shared"] and not remaining

        with monkeypatch.context() as patcher:
            patcher.setattr(
                upgrade.__globals__["op"], "get_bind", lambda: SimpleNamespace(execute=query)
            )
            patcher.setattr(upgrade.__globals__["op"], "execute", ddl)
            if remaining:
                with pytest.raises(RuntimeError, match="shared schema still contains"):
                    upgrade()
            else:
                upgrade()
        assert events[:2] == [("probe", "shared"), ("probe", "public")]
        assert len(probes) == 2 and all("pg_catalog.pg_class" in sql for sql in probes)
        assert state["shared"] is False
        if public:
            assert state["public"] is destination
        assert ("ALTER TABLE shared.user_context SET SCHEMA public" in events) is (
            shared and not public
        )
        assert ("DROP TABLE shared.user_context" in events) is (shared and public)
        assert ("DROP SCHEMA IF EXISTS shared" in events) is (not remaining)

    for case in (
        (True, False, 0),
        (True, True, 0),
        (False, True, 0),
        (False, False, 0),
        (True, True, 1),
    ):
        run_case(*case)

    primary = OSError("catalog transport refused")
    with monkeypatch.context() as patcher:
        patcher.setattr(
            upgrade.__globals__["op"],
            "get_bind",
            lambda: SimpleNamespace(execute=Mock(side_effect=primary)),
        )
        execute = Mock()
        patcher.setattr(upgrade.__globals__["op"], "execute", execute)
        with pytest.raises(OSError) as error:
            upgrade()
        assert error.value is primary
        execute.assert_not_called()


def test_actual_outbox_selected_upgrade_stages_history_then_finalizes_complete_head(monkeypatch):
    """Run the existing wrapper and core198 edge, doubling external IO only."""
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    namespace = runpy.run_path(
        str(Path("tests/migrations/test_runtime_attention_outbox_migration.py"))
    )
    selected = namespace["_selected_upgrade"]
    bound = selected.__globals__
    edge = runpy.run_path(str(Path("alembic/versions/core/core_198_runtime_attention_outbox.py")))[
        "upgrade"
    ]
    normal = "postgresql://normal:synthetic@invalid/selected"
    admin = "postgresql://control:synthetic@invalid/selected"
    connection = object()

    def run_case(failure):
        events = []
        plans = {"selected": {"general": None}}
        primary = (
            asyncio.CancelledError()
            if failure == "cancel"
            else RuntimeError("owned operation refused")
        )

        @contextmanager
        def session(url, *, source_database):
            assert url == admin and source_database == "butlers"
            events.append("enter")
            try:
                yield connection
                if failure == "exit":
                    raise primary
            finally:
                events.append("exit")

        def bounded(url, *, chain, schema, revision, connection):
            assert url == normal and chain == "core" and schema == "switchboard"
            assert connection is held
            events.append(revision)
            if failure in {"bounded", "cancel"}:
                raise primary
            if revision == "core_198":
                edge()

        async def head(url, *, chain, schema, connection):
            assert (url, chain, schema, connection) == (normal, "core", "switchboard", held)
            events.append("head")
            if failure == "head":
                raise primary

        def finalize(connection, *, source_database, plans):
            assert connection is held and source_database == "butlers"
            events.append("finalize")
            assert plans == (("core", "general", None), ("core", "switchboard", None))
            if failure == "finalize":
                raise primary

        held = connection
        config = SimpleNamespace(
            get_main_option=lambda key: normal if key == "sqlalchemy.url" else "switchboard"
        )
        with monkeypatch.context() as patcher:
            for key, value in {
                "_NORMAL_URLS": {"selected": normal},
                "_MAINTENANCE_URLS": {"selected": admin},
                "_SELECTED_PLANS": plans,
                "migration_session": session,
                "_upgrade_chain_to_revision": bounded,
                "run_migrations": head,
                "finalize_authority": finalize,
            }.items():
                patcher.setitem(bound, key, value)
            patcher.setattr(edge.__globals__["op"], "get_bind", lambda: held)
            patcher.setitem(
                edge.__globals__,
                "install_revision_group",
                lambda conn, group: events.append(("installer", conn is held, group)),
            )
            if failure in {"bounded", "cancel", "exit"}:
                with pytest.raises(type(primary)) as error:
                    selected(config, "core_197")
                assert error.value is primary
                assert plans == {"selected": {"general": None}}
                assert events[-1] == "exit"
                return
            selected(config, "core_197")
            assert events == ["enter", "core_197", "exit"]
            events.append("seed")
            selected(config, "core_198")
            assert events == [
                "enter",
                "core_197",
                "exit",
                "seed",
                "enter",
                "core_198",
                ("installer", True, "attention"),
                "exit",
            ]
            staged = dict(plans["selected"])
            assert staged == {"general": None, "switchboard": "core_198"}
            if failure is None:
                selected(config, "core@head")
                assert plans["selected"] == {"general": None, "switchboard": None}
            else:
                with pytest.raises(RuntimeError) as error:
                    selected(config, "core@head")
                assert error.value is primary
                assert plans["selected"] == staged
            assert events[-1] == "exit"
            assert events.count("finalize") == (0 if failure == "head" else 1)

    for failure in (None, "bounded", "head", "finalize", "exit", "cancel"):
        run_case(failure)


def test_pinned_predecessor_archive_is_offline_complete_and_refuses_before_pg(
    tmp_path, monkeypatch
):
    """Actual extractor/fixture; no historical Git, network or PostgreSQL execution."""
    import gzip
    import hashlib
    import io
    import runpy
    import socket
    import subprocess
    import tarfile
    from pathlib import Path

    import testcontainers.postgres

    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "integration" / "test_db_authority_catalog.py")
    )
    extract = namespace["_extract_pinned_predecessor"]
    fixture = namespace["rollback_database"].__wrapped__
    bound = extract.__globals__
    archive = (
        Path(__file__).parents[1] / "integration/data/db_authority_predecessor_30b376.tar.gz"
    ).read_bytes()
    expected = {}
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive))) as source:
        for member in source:
            if member.isfile():
                expected[member.name] = hashlib.sha256(
                    source.extractfile(member).read()
                ).hexdigest()
    expected["deploy/helm/butlers/files/pg_dump.sh"] = expected["deploy/backup/pg_dump.sh"]
    refused = []

    def no_io(*args, **kwargs):
        refused.append("unexpected IO")
        raise AssertionError("predecessor extraction attempted external IO")

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", no_io)
        patch.setattr(socket.socket, "connect", no_io)
        for name in ("ordinary", ".tmp/hidden"):
            destination = tmp_path / name
            destination.mkdir(parents=True)
            extract(destination)
            actual = {
                path.relative_to(destination).as_posix(): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in destination.rglob("*")
                if path.is_file()
            }
            assert actual == expected
            assert not any(path.is_symlink() for path in destination.rglob("*"))
        assert refused == []

    # Exercise the real fixture ordering too: every damaged source refuses
    # before even constructing its disposable PostgreSQL container.
    created = []

    def no_container(*args, **kwargs):
        created.append("constructed")
        raise AssertionError("damaged archive reached PostgreSQL setup")

    with monkeypatch.context() as patch:
        patch.setattr(testcontainers.postgres, "PostgresContainer", no_container)
        patch.setattr(subprocess, "run", no_io)
        patch.setattr(socket.socket, "connect", no_io)
        for index, payload in enumerate(
            (
                None,
                archive[:-1],
                b"X" + archive[1:],
                archive + b"\0",
                gzip.compress(b"wrong predecessor"),
            )
        ):
            root = tmp_path / f"damaged-{index}"
            data = root / "tests/integration/data/db_authority_predecessor_30b376.tar.gz"
            data.parent.mkdir(parents=True)
            if payload is not None:
                data.write_bytes(payload)
            patch.setitem(bound, "_REPOSITORY", root)
            attempt = root / "fixture"
            attempt.mkdir()
            with pytest.raises((FileNotFoundError, RuntimeError)):
                next(fixture(attempt, monkeypatch))
        assert created == []
        assert refused == []


def test_pinned_predecessor_extractor_refuses_unsafe_members_independent_of_destination(
    tmp_path, monkeypatch
):
    """Real extraction guards with source-pin literals rebound to synthetic archives.

    Only the isolated function copy's size/digest fixture literals change. The
    committed consumer's complete released archive pins remain untouched.
    """
    import ast
    import gzip
    import hashlib
    import inspect
    import io
    import runpy
    import tarfile
    from pathlib import Path

    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "integration" / "test_db_authority_catalog.py")
    )
    extract = namespace["_extract_pinned_predecessor"]
    real_source = inspect.getsource(extract)
    source_root = tmp_path / "source"
    data = source_root / "tests/integration/data/db_authority_predecessor_30b376.tar.gz"
    data.parent.mkdir(parents=True)
    real_packed = (
        Path(__file__).parents[1] / "integration/data/db_authority_predecessor_30b376.tar.gz"
    ).read_bytes()
    real_released = gzip.decompress(real_packed)
    # These cases pass actual SHA verification with explicit synthetic pins,
    # then demand the unchanged real path/link/target guard to refuse.
    for case, name, target in (
        ("parent", "../escape", None),
        ("absolute", "/absolute/escape", None),
        ("foreign", "foreign-link", "../outside"),
        ("wrong-helm-link", "deploy/helm/butlers/files/pg_dump.sh", "../../../../outside"),
        (
            "missing-helm-target",
            "deploy/helm/butlers/files/pg_dump.sh",
            "../../../backup/pg_dump.sh",
        ),
        (
            "corrupt-helm-target",
            "deploy/helm/butlers/files/pg_dump.sh",
            "../../../backup/pg_dump.sh",
        ),
    ):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            member = tarfile.TarInfo(name)
            if target is None:
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
            else:
                member.type = tarfile.SYMTYPE
                member.linkname = target
                archive.addfile(member)
            if case == "corrupt-helm-target":
                member = tarfile.TarInfo("deploy/backup/pg_dump.sh")
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
        released = stream.getvalue()
        packed = gzip.compress(released, mtime=0)
        changes = {
            len(real_packed): len(packed),
            len(real_released): len(released),
            hashlib.sha256(real_packed).hexdigest(): hashlib.sha256(packed).hexdigest(),
            hashlib.sha256(real_released).hexdigest(): hashlib.sha256(released).hexdigest(),
        }

        class FixturePins(ast.NodeTransformer):
            def __init__(self, pins):
                self.pins = pins

            def visit_Constant(self, node):
                if node.value in self.pins:
                    return ast.copy_location(ast.Constant(self.pins[node.value]), node)
                return node

        tree = ast.fix_missing_locations(FixturePins(changes).visit(ast.parse(real_source)))
        isolated = dict(extract.__globals__, _REPOSITORY=source_root)
        exec(compile(tree, "<pinned-source-archive-control>", "exec"), isolated)
        data.write_bytes(packed)
        for destination_name in ("ordinary", ".tmp/hidden"):
            destination = tmp_path / case / destination_name
            destination.mkdir(parents=True)
            with pytest.raises((RuntimeError, FileNotFoundError)):
                isolated["_extract_pinned_predecessor"](destination)
            assert not (tmp_path / case / "escape").exists()
            assert not any(path.is_symlink() for path in destination.rglob("*"))


def test_current_relation_manifest_matches_actual_log_and_retirement_producers():
    import runpy
    from pathlib import Path

    from butlers.db_authority import apply, grants

    root = Path(__file__).parents[2]
    emitted = []
    for path in (
        "alembic/versions/core/core_089_butler_logs.py",
        "alembic/versions/core/core_090_butler_logs_chronicler.py",
    ):
        producer = runpy.run_path(str(root / path))
        with patch("alembic.op.execute", side_effect=emitted.append):
            producer["upgrade"]()
    import re

    log_schemas = {
        match[1]
        for sql in emitted
        if (match := re.search(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)\.butler_logs", sql))
    }
    assert len(log_schemas) == 12
    assert not {"public", "lifecycle"} & log_schemas
    full = grants.compile_relation_manifest(
        "test_selected", "normal_owner", "maintenance", source_database="butlers"
    )
    profiles = {profile.primary_schema for profile in full.profiles}
    # core_281 adds the selected current schema, independent of the old roster.
    current_emitted = []
    producer = runpy.run_path(
        str(root / "alembic/versions/core/core_281_current_schema_butler_logs.py")
    )
    with patch("alembic.op.execute", side_effect=current_emitted.append):
        producer["upgrade"]()
    assert len(current_emitted) == 1
    assert "own_schema text := current_schema()" in current_emitted[0]
    assert "CREATE TABLE IF NOT EXISTS %I.butler_logs" in current_emitted[0]
    assert "BIGSERIAL PRIMARY KEY" in current_emitted[0]
    assert "'butler_' || own_schema || '_rw'" in current_emitted[0]
    assert {row.schema for row in full.relations if row.name == "butler_logs"} == (
        profiles | {"public"}
    )
    public = next(row for row in full.relations if row.identity[:2] == ("public", "butler_logs"))
    assert not public.required and public.permissions == ()
    assert not any(
        row.name in {"dashboard_audit_log", "contacts_source_accounts"} for row in full.relations
    )
    for schema in ("public", "lifecycle", "concierge", *sorted(log_schemas)):
        selected = grants.select_migration_manifest(
            full, (("core", None if schema == "public" else schema, None),)
        )
        logs = [
            row for row in selected.relations if (row.schema, row.name) == (schema, "butler_logs")
        ]
        sequences = [
            row
            for row in selected.relations
            if (row.schema, row.name) == (schema, "butler_logs_id_seq")
        ]
        if schema != "public":
            public_logs = next(
                row
                for row in selected.relations
                if (row.schema, row.name) == ("public", "butler_logs")
            )
            assert not public_logs.required and public_logs.permissions == ()
        assert len(logs) == len(sequences) == 1
        for revision in ("core_088", "core_089", "core_090", "core_280", "core_281"):
            historical = grants.select_migration_manifest(
                full, (("core", None if schema == "public" else schema, revision),)
            )
            assert all(not row.required and not row.permissions for row in historical.relations)
            # No future grant or required table follows from an old prefix.
            if schema not in log_schemas and revision != "core_281":
                assert not any(
                    row.required or row.permissions
                    for row in historical.relations
                    if row.name in {"butler_logs", "butler_logs_id_seq"}
                )
        assert len(logs) == len(sequences) == 1
        table, sequence = logs[0], sequences[0]
        assert table.required and sequence.sequence_parent == (schema, "butler_logs", "id")
        assert sequence.permissions == tuple(
            grants.Permission(permission.role, ("USAGE",))
            for permission in table.permissions
            if "INSERT" in permission.verbs
        )
        with pytest.raises(AuthorityError):
            apply.validate_relations((), (table,))
        actual = dict(
            schema=schema,
            name="butler_logs",
            kind="r",
            owner=table.owner,
            rls=False,
            forced_rls=False,
            partition=False,
        )
        assert apply.validate_relations((actual,), (table,)) == ((actual, table),)
        for wrong in (
            dict(name="unlisted_log"),
            dict(owner="foreign_owner"),
            dict(kind="v"),
            dict(schema="foreign_schema"),
            dict(rls=True),
        ):
            with pytest.raises(AuthorityError):
                apply.validate_relations((actual | wrong,), (table,))


def test_retired_relation_manifests_keep_real_prefixes_and_refuse_current_resurrection():
    import runpy
    from pathlib import Path

    from butlers.db_authority import apply, grants

    root = Path(__file__).parents[2]
    full = grants.compile_relation_manifest(
        "test_selected", "normal_owner", "maintenance", source_database="butlers"
    )
    cases = (
        (
            "switchboard",
            "switchboard",
            "sw_025",
            "dashboard_audit_log",
            "roster/switchboard/migrations/026_drop_legacy_dashboard_audit_log.py",
        ),
        (
            "switchboard",
            None,
            "sw_025",
            "dashboard_audit_log",
            "roster/switchboard/migrations/026_drop_legacy_dashboard_audit_log.py",
        ),
        (
            "contacts",
            "relationship",
            "contacts_001",
            "contacts_source_accounts",
            "src/butlers/modules/contacts/migrations/002_drop_contacts_source_accounts.py",
        ),
    )
    for chain, target, prefix, name, path in cases:
        producer = runpy.run_path(str(root / path))
        statements = []
        with patch("alembic.op.execute", side_effect=statements.append):
            producer["upgrade"]()
        assert any("DROP TABLE" in sql and name in sql for sql in statements)
        old = grants.select_migration_manifest(full, ((chain, target, prefix),))
        current = grants.select_migration_manifest(full, ((chain, target, None),))
        accumulated = grants.select_migration_manifest(
            full, ((chain, target, prefix), (chain, target, None))
        )
        old_rows = [row for row in old.relations if row.name == name]
        assert len(old_rows) == 1
        row = old_rows[0]
        assert row.required is False and row.permissions == ()
        assert row.schema == (target or "public") and row.owner == "normal_owner"
        assert not any(value.name == name for value in (*current.relations, *accumulated.relations))
        actual = dict(
            schema=row.schema,
            name=name,
            kind="r",
            owner=row.owner,
            rls=False,
            forced_rls=False,
            partition=False,
            oid=42,
        )
        assert apply.validate_relations((), (row,), administrative_history=True) == ()
        assert apply.validate_relations((actual,), (row,), administrative_history=True) == (
            (actual, row),
        )
        with pytest.raises(AuthorityError):
            apply.validate_relations((actual,), current.relations)
        with pytest.raises(AuthorityError):
            apply.bind_partition_relations((actual,), current.relations, ())
        if chain == "contacts":
            for live in ("contacts_source_links", "contacts_sync_state"):
                live_row = next(value for value in current.relations if value.name == live)
                assert live_row.required and live_row.permissions
                with pytest.raises(AuthorityError):
                    apply.validate_relations((), (live_row,))
    assert next(row for row in full.relations if row.schema == "public" and row.name == "audit_log")


def test_actual_ordinary_ddl_programming_note_preserves_driver_error_and_convergence(monkeypatch):
    import hashlib
    import json

    import psycopg2
    from sqlalchemy.exc import ProgrammingError

    from butlers.db_authority import apply, interfaces
    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.roles import runtime_profiles

    definition = next(
        row
        for row in compile_function_manifest(
            runtime_profiles("butlers"), "normal_owner", (("core", None, None),)
        )
        if "%" in row.sql and row.security_definer
    )
    key = interfaces._signature(definition)
    row = dict(
        oid=42,
        schema=definition.schema,
        name=definition.name,
        arguments=definition.signature,
        owner=definition.owner,
    )
    bound = apply.validate_function_population((row,), (definition,), {definition.identity})

    class UndefinedFunction(psycopg2.ProgrammingError):
        @property
        def pgcode(self):
            return "42883"

    for primary in (
        UndefinedFunction("private driver message sentinel"),
        TypeError("private unexpected message sentinel"),
        asyncio.CancelledError(),
        None,
    ):
        state = {
            "body": "old",
            "installed": 0,
            "acls": set(definition.executors) - {definition.owner},
        }
        captured = []

        def respond(statement, parameters, decoded, *, state=state, primary=primary):
            if "WITH owners AS" in statement:
                return ("valid",), [(True,)]
            if "pg_get_function_identity_arguments" in statement:
                return ("oid", "owner", "definer", "config", "body"), [
                    (
                        42,
                        definition.owner,
                        definition.security_definer,
                        list(definition.settings),
                        state["body"],
                    )
                ]
            if "pg_catalog.aclexplode" in statement:
                return ("role", "privilege", "grantable"), [
                    (role, "EXECUTE", False) for role in sorted(state["acls"])
                ]
            if "current_user=session_user AND rolsuper" in statement:
                return ("valid",), [(True,)]
            if decoded == definition.sql:
                if primary is not None:
                    raise primary
                state["body"] = definition.body
                state["installed"] += 1
                return None, []
            for role in definition.executors:
                if role == definition.owner:
                    continue
                quoted = apply.quote_identifier(role)
                if decoded == f"REVOKE ALL ON FUNCTION {key} FROM {quoted}":
                    state["acls"].discard(role)
                    return None, []
                if decoded == f"GRANT EXECUTE ON FUNCTION {key} TO {quoted}":
                    state["acls"].add(role)
                    return None, []
            assert (
                decoded
                == f"ALTER FUNCTION {key} OWNER TO {apply.quote_identifier(definition.owner)}"
            )
            return None, []

        with _recording_postgresql_connection(respond) as (connection, calls):
            dispatch = connection.exec_driver_sql

            def observed_dispatch(*args, dispatch=dispatch, captured=captured, **kwargs):
                try:
                    return dispatch(*args, **kwargs)
                except ProgrammingError as driver_error:
                    captured.append((driver_error, driver_error.__traceback__))
                    raise

            monkeypatch.setattr(connection, "exec_driver_sql", observed_dispatch)
            if primary is None:
                with patch.object(apply, "_add_ordinary_ddl_failure_note") as observer:
                    apply.converge_ordinary_functions(connection, bound)
                    apply.converge_ordinary_functions(connection, bound)
                    observer.assert_not_called()
                assert state["installed"] == 1
                continue
            with pytest.raises(BaseException) as caught:
                apply.converge_ordinary_functions(connection, bound)
            error = caught.value
            assert state["installed"] == 0
            assert not any(decoded.startswith("ALTER FUNCTION") for _, _, decoded in calls)
            if not isinstance(primary, psycopg2.ProgrammingError):
                assert error is primary and not hasattr(error, "__notes__")
                continue
            assert error is captured[0][0] and error.orig is primary
            assert error.__cause__ is primary
            traceback = error.__traceback__
            while traceback is not None and traceback is not captured[0][1]:
                traceback = traceback.tb_next
            assert traceback is captured[0][1]
            assert len(error.__notes__) == 1
            note = error.__notes__[0]
            assert "sentinel" not in note and definition.sql not in note
            payload = json.loads(note.split(" ", 1)[1])
            assert payload == {
                "version": 1,
                "phase": "ordinary_ddl",
                "exception": "ProgrammingError",
                "identity_sha256": hashlib.sha256(
                    json.dumps(
                        definition.identity, ensure_ascii=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "sql_sha256": hashlib.sha256(definition.sql.encode()).hexdigest(),
                "driver_class": "UndefinedFunction",
                "sqlstate": "42883",
            }


def test_ordinary_ddl_note_bounds_and_psycopg_driver_code_precedence(monkeypatch):
    import json
    from types import SimpleNamespace

    from sqlalchemy.exc import ProgrammingError

    from butlers.db_authority import apply

    definition = SimpleNamespace(identity=("fixed", "function", "text"), sql="fixed source SQL")
    for state, code, expected in (
        ("42883", "42P01", "42883"),
        (None, "42P01", "42P01"),
        ("invalid", "42P01", "42P01"),
        ("42883", "invalid", "42883"),
        (None, None, "UNKNOWN"),
        ("bad", "also bad", "UNKNOWN"),
        (True, 12345, "UNKNOWN"),
        ("42P01\n", "bad", "UNKNOWN"),
    ):
        original = SimpleNamespace(sqlstate=state, pgcode=code)
        error = ProgrammingError("private SQL sentinel", {"private": "sentinel"}, original)
        apply._add_ordinary_ddl_failure_note(error, definition)
        payload = json.loads(error.__notes__[0].split(" ", 1)[1])
        assert payload["sqlstate"] == expected and payload["driver_class"] == "UNKNOWN"
        assert "sentinel" not in error.__notes__[0]
    for invalid in (
        SimpleNamespace(identity=("fixed",), sql="fixed source SQL"),
        SimpleNamespace(identity=("x" * 4097, "function", "text"), sql="fixed source SQL"),
        SimpleNamespace(identity=definition.identity, sql="x" * (1024 * 1024 + 1)),
    ):
        error = ProgrammingError("private", {}, SimpleNamespace())
        apply._add_ordinary_ddl_failure_note(error, invalid)
        assert not hasattr(error, "__notes__")
    error = ProgrammingError("private", {}, SimpleNamespace())
    with monkeypatch.context() as scoped:
        scoped.setattr(json, "dumps", Mock(side_effect=ValueError("private observer sentinel")))
        apply._add_ordinary_ddl_failure_note(error, definition)
    assert not hasattr(error, "__notes__")

    class NoteFailure(ProgrammingError):
        def add_note(self, note):
            raise ValueError("private transport sentinel")

    error = NoteFailure("private", {}, SimpleNamespace())
    apply._add_ordinary_ddl_failure_note(error, definition)
    assert not hasattr(error, "__notes__")


def test_compiled_current_functions_replace_preexisting_overloads_without_dropping_dependencies():
    import psycopg2

    from butlers.db_authority import apply, grants, interfaces
    from butlers.db_authority.roles import runtime_profiles

    identities = {
        ("dashboard_auth", "api", "text,jsonb"),
        ("dashboard_auth", "host", "text,jsonb"),
        ("dashboard_auth", "cleanup", ""),
        ("relationship", "guard_fact_report_authority", ""),
        ("switchboard", "guard_admission_attempt_binding", ""),
    }
    plans = (
        ("core", None, None),
        ("relationship", "relationship", None),
        ("switchboard", "switchboard", None),
    )
    definitions = tuple(
        row
        for row in grants.compile_function_manifest(
            runtime_profiles("butlers"), "normal_migrator", plans
        )
        if row.identity in identities
    )
    assert {row.identity for row in definitions} == identities
    index = {interfaces._signature(row): row for row in definitions}
    catalog = {
        key: {"oid": number, "owner": row.owner, "body": "prior migrated body"}
        for number, (key, row) in enumerate(index.items(), 100)
    }
    original_oids = {key: state["oid"] for key, state in catalog.items()}
    # The recorder retains dependency sentinels; genuine OID/dependency survival
    # still requires the migrated PostgreSQL observers in the hosted lane.
    dependencies = {key: ("dependent trigger or caller",) for key in index}
    original_dependencies = dict(dependencies)
    acls = {key: {"PUBLIC"} for key in index}
    valid = {"owners": True, "maintenance": True, "catalog": True}
    installed = []
    driver_failure = {"enabled": False}

    class DuplicateFunction(psycopg2.ProgrammingError):
        @property
        def pgcode(self):
            return "42723"

    def respond(statement, parameters, decoded):
        if "WITH owners AS" in statement:
            assert set(parameters["owners"]) == {
                row.owner for row in definitions if row.security_definer
            }
            return ("valid",), [(valid["owners"],)]
        if "pg_get_function_identity_arguments" in statement:
            key = parameters["signature"]
            row, actual = index[key], catalog[key]
            return ("oid", "owner", "definer", "config", "body"), [
                (
                    actual["oid"] if valid["catalog"] else -1,
                    actual["owner"],
                    row.security_definer,
                    list(row.settings),
                    actual["body"],
                )
            ]
        if "pg_catalog.aclexplode" in statement:
            key = next(key for key, row in catalog.items() if row["oid"] == parameters["oid"])
            return ("role", "privilege", "grantable"), [
                (role, "EXECUTE", False) for role in sorted(acls[key])
            ]
        if "current_user=session_user AND rolsuper" in statement:
            return ("valid",), [(valid["maintenance"],)]
        for key, row in index.items():
            if decoded == row.sql:
                assert not parameters
                if driver_failure["enabled"] or decoded.startswith("CREATE FUNCTION "):
                    # Model only PostgreSQL's duplicate-overload driver result;
                    # the compiler/converger and SQLAlchemy dispatch are real.
                    raise DuplicateFunction("preexisting fixed overload")
                assert decoded.startswith("CREATE OR REPLACE FUNCTION ")
                catalog[key]["body"] = row.body
                installed.append(key)
                return None, []
            if decoded == f"ALTER FUNCTION {key} OWNER TO {apply.quote_identifier(row.owner)}":
                catalog[key]["owner"] = row.owner
                return None, []
            for role in tuple(acls[key] | set(row.executors)):
                quoted = "PUBLIC" if role == "PUBLIC" else apply.quote_identifier(role)
                if decoded == f"REVOKE ALL ON FUNCTION {key} FROM {quoted}":
                    acls[key].remove(role)
                    return None, []
                if decoded == f"GRANT EXECUTE ON FUNCTION {key} TO {quoted}":
                    acls[key].add(role)
                    return None, []
        raise AssertionError("unexpected fixed-function convergence operation")

    population = tuple(
        dict(
            oid=catalog[key]["oid"],
            schema=row.schema,
            name=row.name,
            arguments=row.signature,
            owner=row.owner,
        )
        for key, row in index.items()
    )
    bound = apply.validate_function_population(population, definitions, identities)
    for invalid in (
        (*population, population[0]),
        (*population, dict(population[0], name="unlisted_function")),
        (dict(population[0], owner="unlisted_owner"), *population[1:]),
        population[1:],
    ):
        with pytest.raises(AuthorityError):
            apply.validate_function_population(invalid, definitions, identities)

    with _recording_postgresql_connection(respond) as (connection, calls):
        for guard in valid:
            valid[guard] = False
            with pytest.raises(AuthorityError):
                apply.converge_ordinary_functions(connection, bound)
            valid[guard] = True
            assert installed == []
        apply.converge_ordinary_functions(connection, bound)
        assert installed == list(index)
        assert all(catalog[key]["body"] == row.body for key, row in index.items())
        assert all(catalog[key]["owner"] == row.owner for key, row in index.items())
        assert all(acls[key] == set(row.executors) - {row.owner} for key, row in index.items())
        apply.converge_ordinary_functions(connection, bound)
        assert installed == list(index)
        for actual in catalog.values():
            actual["body"] = "later drift"
        apply.converge_ordinary_functions(connection, bound)
        assert installed == list(index) * 2
        assert {key: state["oid"] for key, state in catalog.items()} == original_oids
        assert dependencies == original_dependencies
        assert all(
            interfaces.function_matches(
                connection, row, interfaces.inspect_function(connection, row)
            )
            for row in definitions
        )
        assert not any(decoded.startswith("DROP ") for _, _, decoded in calls)
        # A later driver refusal must identify the actual normalized compiled
        # statement, not the original CREATE-only predecessor SQL digest.
        import hashlib
        import json

        from sqlalchemy.exc import ProgrammingError

        first = next(iter(index.values()))
        catalog[interfaces._signature(first)]["body"] = "driver refusal drift"
        driver_failure["enabled"] = True
        with pytest.raises(ProgrammingError) as caught:
            apply.converge_ordinary_functions(connection, bound)
        payload = json.loads(caught.value.__notes__[0].split(" ", 1)[1])
        assert (
            payload["identity_sha256"]
            == hashlib.sha256(
                json.dumps(first.identity, separators=(",", ":")).encode()
            ).hexdigest()
        )
        assert payload["sql_sha256"] == hashlib.sha256(first.sql.encode()).hexdigest()
        assert payload["sqlstate"] == "42723" and payload["driver_class"] == "DuplicateFunction"
        assert caught.value.orig.pgcode == "42723"
        assert installed == list(index) * 2
        assert calls[-1][2] == first.sql

    raw = {row[1:4]: row for row in grants._ORDINARY_FUNCTION_SOURCES if row[1:4] in identities}
    assert set(raw) == identities
    for row in definitions:
        original = raw[row.identity]
        assert original[5].startswith("CREATE FUNCTION ")
        assert row.header == original[5].replace(
            "CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ", 1
        )
        assert row.body == original[6]
        assert row.security_definer is original[4] and row.producer == original[7]
    assert (
        grants.compile_function_manifest(
            runtime_profiles("butlers"), "normal_migrator", (("core", None, "core_239"),)
        )
        == ()
    )


def test_partition_precondition_note_identifies_actual_first_refused_row_and_condition():
    import hashlib
    import json

    from butlers.db_authority import apply
    from butlers.db_authority.grants import ObjectAuthority, ObjectKind

    parent = ObjectAuthority("public", "token_usage_ledger", ObjectKind.TABLE, "owner", (), "fixed")
    declared = dict(oid=1, schema=parent.schema, name=parent.name, owner="owner", kind="p")
    child = dict(
        oid=2, schema="public", name="legitimate_child", owner="owner", kind="r", partition=True
    )
    child_link = dict(oid=2, parent_schema=parent.schema, parent_name=parent.name)
    captured = []

    def capture(error, note):
        captured.append(error)
        BaseException.add_note(error, note)

    for partition, kind, link_count in (
        (False, "r", 1),
        (1, "r", 1),
        (None, "r", 1),
        ("missing", "r", 1),
        (True, "v", 1),
        (True, "m", 1),
        (True, "p", 1),
        (True, "S", 1),
        (True, "f", 1),
        (True, "unknown", 1),
        (True, "r", 0),
        (True, "r", 2),
    ):
        first = dict(
            oid=3, schema="public", name="first_private_sentinel", owner="owner", kind=kind
        )
        if partition != "missing":
            first["partition"] = partition
        second = dict(first, oid=4, name="second_private_sentinel")
        first_links = [dict(child_link, oid=3)] * link_count
        second_links = [dict(child_link, oid=4)] * link_count
        for refused in ((first, second), (second, first)):
            captured.clear()
            with patch.object(AuthorityError, "add_note", capture):
                with pytest.raises(AuthorityError) as caught:
                    apply.bind_partition_relations(
                        (declared, child, *refused),
                        (parent,),
                        (child_link, *first_links, *second_links),
                    )
            assert str(caught.value) == "Managed database object is not declared"
            assert captured == [caught.value]
            note = caught.value.__notes__[0]
            assert note.startswith("DB_AUTHORITY_PARTITION_BINDING_DIAGNOSTIC ")
            assert "private_sentinel" not in note and "public" not in note
            name = refused[0]["name"]
            assert json.loads(note.split(" ", 1)[1]) == {
                "version": 1,
                "phase": "bind_partitions_precondition",
                "exception": "AuthorityError",
                "reason": "unlisted_not_single_table_partition",
                "identity_sha256": hashlib.sha256(
                    json.dumps(["public", name], separators=(",", ":")).encode()
                ).hexdigest(),
                "schema_sha256": hashlib.sha256(b"public").hexdigest(),
                "name_sha256": hashlib.sha256(name.encode()).hexdigest(),
                "kind": {
                    "r": "table",
                    "v": "view",
                    "m": "materialized_view",
                    "p": "partitioned_table",
                    "S": "sequence",
                    "f": "foreign_table",
                }.get(kind, "UNKNOWN"),
                "declared_key": False,
                "partition_is_true": partition is True,
                "parent_link_count": {0: "zero", 1: "one", 2: "many"}[link_count],
            }


def test_partition_precondition_notes_preserve_admission_later_refusals_and_observer_failures():
    import json

    from butlers.db_authority import apply
    from butlers.db_authority.grants import ObjectAuthority, ObjectKind

    parent = ObjectAuthority("public", "token_usage_ledger", ObjectKind.TABLE, "owner", (), "fixed")
    declared = dict(oid=1, schema=parent.schema, name=parent.name, owner="owner", kind="p")
    child = dict(
        oid=2, schema="public", name="generated_child", owner="owner", kind="r", partition=True
    )
    link = dict(oid=2, parent_schema=parent.schema, parent_name=parent.name)
    with patch.object(apply, "_add_partition_precondition_note") as observer:
        bound = apply.bind_partition_relations((declared, child), (parent,), (link,))
        assert bound[0] is parent and len(bound) == 2
        assert bound[1].permissions == () and bound[1].required is False
        observer.assert_not_called()
        for invalid in (dict(child, owner="foreign_owner"), dict(child, schema="foreign_schema")):
            with pytest.raises(
                AuthorityError, match="partition does not match its source producer"
            ):
                apply.bind_partition_relations((invalid,), (parent,), (link,))
            observer.assert_not_called()
        with pytest.raises(AuthorityError, match="Required database object is unavailable"):
            apply.validate_relations((), (parent,))
        observer.assert_not_called()
    captured = []

    def capture(error, note):
        captured.append(error)
        raise ValueError("private observer failure")

    with patch.object(AuthorityError, "add_note", capture):
        with pytest.raises(AuthorityError) as caught:
            apply.bind_partition_relations((dict(child, partition=False),), (parent,), (link,))
        assert captured == [caught.value] and not hasattr(caught.value, "__notes__")
    with patch.object(json, "dumps", side_effect=ValueError("private builder failure")):
        with pytest.raises(AuthorityError) as caught:
            apply.bind_partition_relations((dict(child, partition=False),), (parent,), (link,))
        assert str(caught.value) == "Managed database object is not declared"
        assert not hasattr(caught.value, "__notes__")
    with patch.object(
        apply, "ObjectAuthority", side_effect=asyncio.CancelledError()
    ) as constructor:
        with pytest.raises(asyncio.CancelledError):
            apply.bind_partition_relations((child,), (parent,), (link,))
        constructor.assert_called_once()

    for invalid in (None, "", "x" * 1025, "é" * 513, 1):
        error = AuthorityError("Managed database object is not declared")
        apply._add_partition_precondition_note(error, dict(child, schema=invalid), ())
        assert not hasattr(error, "__notes__")


def test_real_retired_contact_and_switchboard_view_producers_select_exact_windows():
    import runpy
    from pathlib import Path

    from butlers.db_authority import apply, grants

    root = Path(__file__).parents[2]
    full = grants.compile_relation_manifest(
        "test_windows", "normal_owner", "maintenance", source_database="butlers"
    )
    created = []
    producer = runpy.run_path(str(root / "alembic/versions/core/core_002_identity.py"))
    with patch("alembic.op.execute", side_effect=created.append):
        producer["upgrade"]()
    assert any("CREATE TABLE IF NOT EXISTS public.contact_info" in sql for sql in created)
    dropped = []
    producer = runpy.run_path(str(root / "alembic/versions/core/core_115_drop_contact_info.py"))
    bind = Mock()
    bind.execute.return_value.scalar.side_effect = [1, None, 0]
    with (
        patch("alembic.op.execute", side_effect=dropped.append),
        patch("alembic.op.get_bind", return_value=bind),
    ):
        producer["upgrade"]()
    assert "DROP TABLE IF EXISTS public.contact_info CASCADE" in dropped
    for plans, present in (
        ((("core", None, "core_001"),), False),
        ((("core", None, "core_002"),), True),
        ((("core", "general", "core_114"),), True),
        ((("core", None, "core_115"),), False),
        ((("core", None, None),), False),
        ((("core", None, "core_114"), ("core", "general", None)), False),
    ):
        manifest = grants.select_migration_manifest(full, plans)
        rows = [r for r in manifest.relations if (r.schema, r.name) == ("public", "contact_info")]
        assert bool(rows) is present
        actual = dict(
            schema="public",
            name="contact_info",
            kind="r",
            owner="normal_owner",
            rls=False,
            forced_rls=False,
            oid=1,
            partition=False,
        )
        if present:
            (row,) = rows
            assert not row.required and row.permissions == ()
            assert apply.validate_relations((actual,), (row,), administrative_history=True)
            with pytest.raises(AuthorityError):
                apply.validate_relations(
                    (actual | {"owner": "foreign"},), (row,), administrative_history=True
                )
        else:
            with pytest.raises(AuthorityError):
                apply.bind_partition_relations((actual,), manifest.relations, ())

    producer = runpy.run_path(str(root / "roster/switchboard/migrations/024_v_qa_infra_state.py"))
    emitted = []
    with patch("alembic.op.execute", side_effect=emitted.append):
        producer["upgrade"]()
    names = ("v_qa_connector_state", "v_qa_butler_heartbeat")
    readers = {producer["_QA_ROLE"], *producer["_OTHER_BUTLER_ROLES"]}
    selected_roles = {profile.role for profile in full.profiles}
    for name in names:
        assert any(f"CREATE VIEW public.{name} AS" in sql for sql in emitted)
        row = next(r for r in full.relations if (r.schema, r.name) == ("public", name))
        assert row.kind == grants.ObjectKind.VIEW and row.owner == "normal_owner"
        assert {(p.role, p.verbs) for p in row.permissions} == {
            (role, ("SELECT",)) for role in readers & selected_roles
        }
    for target in (None, "switchboard"):
        for revision, present in (("sw_023", False), ("sw_024", True), (None, True)):
            manifest = grants.select_migration_manifest(full, (("switchboard", target, revision),))
            rows = [r for r in manifest.relations if r.name in names]
            assert {r.name for r in rows} == (set(names) if present else set())
            assert all(r.required is (revision is None) for r in rows)
            assert all(bool(r.permissions) is (revision is None) for r in rows)
    core = grants.select_migration_manifest(full, (("core", None, None),))
    assert not any(r.name in names for r in core.relations)
    accumulated = grants.select_migration_manifest(
        full, (("switchboard", "switchboard", "sw_023"), ("switchboard", "switchboard", None))
    )
    assert {r.name for r in accumulated.relations if r.name in names} == set(names)
    assert all(not r.permissions for r in accumulated.relations)


def test_finance_materialized_view_uses_actual_producer_namespace_and_closed_kind():
    import runpy
    from contextlib import nullcontext
    from dataclasses import replace
    from pathlib import Path

    from butlers.db_authority import apply, catalog, grants

    root = Path(__file__).parents[2]
    producer = runpy.run_path(str(root / "roster/finance/migrations/006_intelligence_tables.py"))
    emitted = []
    context = Mock()
    context.autocommit_block.side_effect = nullcontext
    with (
        patch("alembic.op.execute", side_effect=emitted.append),
        patch("alembic.op.get_context", return_value=context),
    ):
        producer["upgrade"]()
    assert any(
        "CREATE MATERIALIZED VIEW IF NOT EXISTS spending_summaries AS" in sql for sql in emitted
    )
    full = grants.compile_relation_manifest(
        "test_windows", "normal_owner", "maintenance", source_database="butlers"
    )
    source_row = next(
        r for r in full.relations if (r.schema, r.name) == ("finance", "spending_summaries")
    )
    assert "WHEN 'm' THEN 'materialized_view'" in catalog._CATALOG_OBJECT_SQL
    assert "ELSE 'invalid' END<>c.expected->>'kind'" in catalog._CATALOG_OBJECT_SQL
    assert "WHEN 'f'" not in catalog._CATALOG_OBJECT_SQL
    assert source_row.kind == grants.ObjectKind.MATERIALIZED_VIEW
    assert source_row.owner == "normal_owner" and source_row.required
    assert source_row.permissions == (
        grants.Permission("butler_finance_rw", ("SELECT",)),
        grants.Permission("dashboard_api", ("SELECT",)),
    )
    for target in (None, "finance"):
        for revision, present in (("finance_005", False), ("finance_006", True), (None, True)):
            manifest = grants.select_migration_manifest(full, (("finance", target, revision),))
            rows = [r for r in manifest.relations if r.name == "spending_summaries"]
            assert len(rows) == int(present)
            if rows:
                (row,) = rows
                assert row.schema == (target or "public")
                assert row.kind == grants.ObjectKind.MATERIALIZED_VIEW
                assert row.required is (revision is None)
                assert bool(row.permissions) is (revision is None and target == "finance")
        core_only = grants.select_migration_manifest(full, (("core", target, None),))
        assert not any(r.name == "spending_summaries" for r in core_only.relations)
    actual = dict(
        schema="finance",
        name="spending_summaries",
        kind="m",
        owner="normal_owner",
        rls=False,
        forced_rls=False,
        oid=42,
        partition=False,
    )
    assert apply.bind_partition_relations((actual,), (source_row,), ()) == (source_row,)
    assert apply.validate_relations((actual,), (source_row,)) == ((actual, source_row),)
    for invalid in (
        None,
        dict(kind="v"),
        dict(kind="r"),
        dict(kind="f"),
        dict(name="unknown_mv"),
        dict(schema="public"),
        dict(owner="foreign"),
        dict(rls=True),
        dict(forced_rls=True),
    ):
        with pytest.raises(AuthorityError):
            apply.validate_relations(() if invalid is None else (actual | invalid,), (source_row,))
    with pytest.raises(AuthorityError):
        apply.bind_partition_relations((actual | {"name": "unknown_mv"},), (source_row,), ())
    context = dict(
        phase="validate_relations", migration_owner="normal_owner", maintenance_owner="maintenance"
    )
    note = apply._relation_failure_note((actual,), (source_row,), **context)
    payload = __import__("json").loads(note.split(" ", 1)[1])
    assert payload["rows"][0]["declared_identity"] is True
    assert payload["rows"][0]["kind"] == "materialized_view"
    # The unchanged v2 missing-kind schema cannot encode a missing MV. The
    # required validator still refuses; diagnostic unavailability is not admission.
    assert apply._relation_failure_note((), (source_row,), **context) is None

    acl = {("unlisted_role", "SELECT")}

    def respond(statement, parameters, decoded):
        source_sql = __import__("re").sub(r"%\((\w+)\)s", r":\1", statement)
        if source_sql == apply._RELATION_ACL:
            assert parameters == {"oid": 42}
            return ("role", "verb", "grantable"), [(role, verb, False) for role, verb in acl]
        if source_sql == apply._COLUMN_ACL:
            return ("role", "name", "verb", "grantable"), []
        if decoded.startswith("REVOKE ALL PRIVILEGES ON TABLE"):
            assert (
                decoded
                == 'REVOKE ALL PRIVILEGES ON TABLE "finance"."spending_summaries" FROM "unlisted_role"'
            )
            acl.clear()
            return None, []
        for permission in source_row.permissions:
            if (
                decoded
                == f'GRANT SELECT ON TABLE "finance"."spending_summaries" TO "{permission.role}"'
            ):
                acl.add((permission.role, "SELECT"))
                return None, []
        pytest.fail("Unexpected materialized-view ACL operation")

    with _recording_postgresql_connection(respond) as (connection, calls):
        apply._revoke_relation(connection, actual, source_row)
        apply._grant_relation(connection, source_row)
        apply.verify_relation(connection, actual, source_row)
        assert acl == {(p.role, "SELECT") for p in source_row.permissions}
        assert not any("ROW LEVEL SECURITY" in decoded for _, _, decoded in calls)
    # Ownership transfer requires PostgreSQL's distinct MATERIALIZED VIEW DDL;
    # GRANT/REVOKE retain its TABLE privilege grammar.
    assert apply._relation_ddl_kind(source_row) == "MATERIALIZED VIEW"
    assert apply._relation_ddl_kind(replace(source_row, kind=grants.ObjectKind.VIEW)) == "VIEW"


class _HistoricalPriorityAuditConvergenceTransport:
    """Fixed audit component observations behind real finalizer/reader consumers.

    Ledger rows are explicit installed test inputs, never desired plans. Source
    producers provide function/table/trigger facts; all expected authority still
    comes from the unpatched application compiler. Unknown SQL is delegated to
    the original rejecting recorder. This is not native PostgreSQL evidence.
    """

    UNHANDLED = object()
    purpose = "priority_contact_audit_owner"
    function_name = "priority_contacts_cascade_audit"

    @staticmethod
    def _literal_queries(function):
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        return tuple(
            node.args[0].value
            for node in sorted(ast.walk(tree), key=lambda node: getattr(node, "lineno", 0))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        )

    def __init__(self, delegate, installed):
        import ast
        import inspect
        import re
        import runpy
        import textwrap
        from pathlib import Path

        from alembic.ddl.impl import DefaultImpl

        from butlers import migrations
        from butlers.db_authority import apply, interfaces

        self.delegate, self.apply, self.interfaces = delegate, apply, interfaces
        self.re = re
        # This dictionary is the recorder's physical stamp observation. The
        # reader below must independently qualify every revision and ACL.
        self.installed = {schema: tuple(revisions) for schema, revisions in installed.items()}
        self.ledgers = {}
        self.ledger_metadata = {}
        for schema in self.installed:
            table = DefaultImpl.version_table_impl(
                None,
                version_table="alembic_version",
                version_table_schema=schema,
                version_table_pk=True,
            )
            oid = 791000 + len(self.ledgers)
            self.ledgers[oid] = dict(
                oid=oid,
                schema=str(table.schema),
                name=str(table.name),
                kind="r",
                owner="normal_owner",
                rls=False,
                forced_rls=False,
                partition=False,
            )
            self.ledger_metadata[oid] = table
        self.role = dict(
            rolcanlogin=False,
            rolinherit=False,
            rolsuper=False,
            rolcreaterole=False,
            rolcreatedb=False,
            rolreplication=False,
            rolbypassrls=False,
        )
        self.memberships = []
        self.namespace_owner = "normal_owner"
        self.actor = "maintenance"
        self.defaults, self.owned_dependencies = [], []
        self.tables, self.table_acl, self.column_acl = {}, {}, {}
        self.namespace_acl, self.public_caps = set(), []
        self.function = None
        self.trigger = None
        self.audit_columns = ()
        self.sequence_binding = None
        self.effects = []
        root = Path(__file__).parents[2]
        core_prefix = set()
        for revisions in self.installed.values():
            for revision in revisions:
                if revision.startswith("core_"):
                    core_prefix.update(migrations.get_chain_revision_path("core", revision))
        self.produced = []
        if "core_101" in core_prefix and "core_205" not in core_prefix:
            first = runpy.run_path(
                str(root / "alembic/versions/core/core_101_priority_contacts.py")
            )
            audit = runpy.run_path(str(root / "alembic/versions/core/core_092_audit_log.py"))
            with patch(
                "alembic.op.execute", side_effect=lambda sql: self.produced.append(str(sql))
            ):
                audit["upgrade"]()
                upgrade_tree = ast.parse(textwrap.dedent(inspect.getsource(first["upgrade"])))
                table_statement = upgrade_tree.body[0].body[0].value.args[0].value
                from alembic import op

                assert "CREATE TABLE IF NOT EXISTS public.priority_contacts (" in table_statement
                op.execute(table_statement)
                first["_ordinary_audit_install"]()
                for role in first["_ALL_RUNTIME_ROLES"]:
                    first["_grant_best_effort"](
                        "public.priority_contacts", first["_TABLE_PRIVILEGES"], role
                    )
            function_sql = next(sql for sql in self.produced if "CREATE OR REPLACE FUNCTION" in sql)
            if "core_129" in core_prefix:
                middle = runpy.run_path(
                    str(root / "alembic/versions/core/core_129_priority_contacts_drop_butler.py")
                )
                function_sql = middle["_TRIGGER_FN_GLOBAL"]
            body = function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
            self.function = dict(
                oid=792001,
                owner="normal_owner",
                definer="SECURITY DEFINER" in function_sql,
                config=[],
                body=body,
                arguments="",
                acl=[("PUBLIC", "EXECUTE", False)],
                language="plpgsql",
                returns="trigger",
                strict=False,
                leakproof=False,
                parallel="u",
                cost=100,
                support=0,
                variadic=0,
                bin=None,
                transform=None,
            )
            self.normalized_sql = (
                "CREATE OR REPLACE FUNCTION public.priority_contacts_cascade_audit()\n"
                "RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER\n"
                "SET search_path = pg_catalog, pg_temp\n"
                "AS $db_authority_body$" + body + "$db_authority_body$;"
            )
            table_sql = next(
                sql for sql in self.produced if "CREATE TABLE IF NOT EXISTS public.audit_log" in sql
            )
            self.audit_columns = tuple(
                (column, "text")
                for column in ("actor", "action", "target", "note")
                if re.search(r"\b" + column + r"\s+TEXT\b", table_sql, re.I)
            )
            self.sequence_binding = (
                ("public", "audit_log", "id", "a") if "BIGSERIAL PRIMARY KEY" in table_sql else None
            )
            trigger_sql = next(
                sql
                for sql in self.produced
                if "CREATE TRIGGER trg_priority_contacts_cascade_audit" in sql
            )
            self.trigger = dict(
                table=("public", "priority_contacts"),
                name="trg_priority_contacts_cascade_audit",
                type=9 if "AFTER DELETE" in trigger_sql and "FOR EACH ROW" in trigger_sql else None,
                function=("public", self.function_name),
                enabled="O",
                args=(),
                constraint=0,
            )
            for name, kind in (
                ("audit_log", "r"),
                ("audit_log_id_seq", "S"),
                ("priority_contacts", "r"),
            ):
                oid = 793000 + len(self.tables)
                self.tables[oid] = dict(
                    oid=oid,
                    schema="public",
                    name=name,
                    kind=kind,
                    owner="normal_owner",
                    rls=False,
                    forced_rls=False,
                    partition=False,
                )
                self.table_acl[oid], self.column_acl[oid] = set(), set()
            for sql in self.produced:
                for match in re.finditer(
                    r"EXECUTE 'GRANT ([A-Z, ]+) ON TABLE public\.(audit_log|priority_contacts) TO \"([a-z_]+)\"'",
                    sql,
                ):
                    oid = next(oid for oid, row in self.tables.items() if row["name"] == match[2])
                    self.table_acl[oid].update(
                        (match[3], verb.strip()) for verb in match[1].split(",")
                    )
        (self.role_query,) = self._literal_queries(apply.validate_ordinary_purpose_owners)
        audit_queries = self._literal_queries(interfaces.validate_historical_priority_audit)
        self.overload_query, self.namespace_query, self.dependency_query, self.column_query = (
            audit_queries
        )
        (self.abi_query,) = self._literal_queries(interfaces._owner_lookup_revision_vector)
        self.capability_query = (
            self._literal_queries(interfaces._fixed_purpose_capability_matches)[0]
            .replace("rolname='identity_lookup_owner'", "rolname='priority_contact_audit_owner'")
            .replace(
                "'public.resolve_owner_triple(text,text[])'::regprocedure",
                "pg_catalog.to_regprocedure('public.priority_contacts_cascade_audit()')",
            )
        )
        (self.public_query,) = self._literal_queries(
            interfaces._owner_lookup_public_capability_matches
        )
        # Build the exact trigger metadata query from literal source fragments;
        # observed metadata below is independently stored, not expected success.
        tree = ast.parse(
            textwrap.dedent(inspect.getsource(interfaces._owner_lookup_metadata_matches))
        )
        trigger_predicate = next(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "p.prorettype='trigger'" in node.value
        )
        expression = next(
            node.args[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
            and node.args
            and isinstance(node.args[0], ast.BinOp)
        )
        self.metadata_query = (
            expression.left.left.value + trigger_predicate + expression.right.value
        )
        # Scalar observations below implement only these fixed SQL predicates.
        # Re-reading source literals is not permission to adopt a changed query.
        # An unknown source query remains UNHANDLED by this component adapter.
        import hashlib

        fixed_queries = {
            "role_query": "de58226fb14aac2e856e7163b634d111bd1903edf6d68930f0cf5364772d5ae1",
            "overload_query": "460f5c8cddab2fa11d5376f58442ce8c5bb861293263264c38745aef43685d0f",
            "namespace_query": "45d5fa6ef2940d1e7e3bb104976cba12816cf5b526f5948b79425654e1a4aa33",
            "dependency_query": "b26adb02c2b0efaa20c5e4be3b49854cf08fde4f5537896940a0ac203e0c7a7c",
            "column_query": "3b6fdaf9b63238abc6b237fdf0241b6b8c356c3cab47147064655d95cb1c6a62",
            "abi_query": "53eb6ead85943beddbf932af6e5d0ea8c53d437ec1c2177fb50f7d423297fd46",
            "capability_query": "981402ffb5a12f9ddd0034596e16a4ac9b0af57c9bfd5be9989dfcb6555a130f",
            "public_query": "d297895361b4c579924eb5f5a9554735e03d2c5e0e6ce87d4301240776593e44",
            "metadata_query": "b1102d91edf361cdb1a3a9fdc3b546d8a7fbc5a4415bcba7410938827f0eace7",
        }
        for name, digest in fixed_queries.items():
            if hashlib.sha256(getattr(self, name).encode()).hexdigest() != digest:
                setattr(self, name, self.UNHANDLED)

    @staticmethod
    def rows(values, columns):
        return columns, list(values)

    def dispatch(self, wire, params, decoded):
        sql = self.re.sub(r"%\((\w+)\)s", r":\1", wire).replace("%%", "%")
        sql = sql.replace(r"\:", ":")
        apply, interfaces = self.apply, self.interfaces
        if sql == self.role_query and params == {"owners": [self.purpose], "count": 1}:
            return ("valid",), [(not any(self.role.values()) and not self.memberships,)]
        if sql == self.overload_query and params == {}:
            return ("signature",), [] if self.function is None else [(self.function["arguments"],)]
        if sql == self.abi_query and params.get("oid") in self.ledger_metadata:
            table = self.ledger_metadata[params["oid"]]
            columns = tuple(table.columns)
            valid = (
                len(columns) == 1
                and str(columns[0].name) == "version_num"
                and str(columns[0].type) == "VARCHAR(32)"
                and columns[0].nullable is False
                and tuple(table.primary_key.columns) == columns
                and columns[0].server_default is None
                and columns[0].identity is None
                and columns[0].computed is None
            )
            return ("valid",), [(valid,)]
        for schema, revisions in self.installed.items():
            if decoded in {
                f'SELECT version_num FROM "{schema}".alembic_version',
                f'SELECT version_num FROM "{schema}"."alembic_version"',
            }:
                return ("version_num",), [(revision,) for revision in revisions]
        if sql in {apply._RELATION_ACL, apply._COLUMN_ACL} and params.get("oid") in self.ledgers:
            return (
                ("role", "verb", "grantable")
                if sql == apply._RELATION_ACL
                else ("role", "name", "verb", "grantable")
            ), []
        if sql == self.namespace_query and params == {"owner": "normal_owner"}:
            return ("valid",), [(self.namespace_owner in {"normal_owner", "pg_database_owner"},)]
        signature = '"public"."priority_contacts_cascade_audit"()'
        if sql == interfaces._FUNCTION_CATALOG and params == {"signature": signature}:
            keys = ("oid", "owner", "definer", "config", "body", "arguments")
            return keys, [] if self.function is None else [
                tuple(self.function[key] for key in keys)
            ]
        if sql == interfaces._FUNCTION_ACL and params == {"oid": 792001}:
            return ("role", "privilege", "grantable"), self.function["acl"]
        if sql == self.metadata_query and params == {
            "schema": "public",
            "name": self.function_name,
            "language": "plpgsql",
        }:
            f = self.function
            valid = f is not None and (
                f["language"],
                f["returns"],
                f["strict"],
                f["leakproof"],
                f["parallel"],
                f["cost"],
                f["support"],
                f["variadic"],
                f["bin"],
                f["transform"],
            ) == ("plpgsql", "trigger", False, False, "u", 100, 0, 0, None, None)
            return ("valid",), [(valid,)]
        if sql == interfaces._PRIORITY_AUDIT_TRIGGER_SQL and params == {}:
            valid = self.trigger == dict(
                table=("public", "priority_contacts"),
                name="trg_priority_contacts_cascade_audit",
                type=9,
                function=("public", self.function_name),
                enabled="O",
                args=(),
                constraint=0,
            )
            return ("valid",), [(valid,)]
        if sql == self.dependency_query and params == {"owner": "normal_owner"}:
            dependencies = [
                row
                for row in self.tables.values()
                if row["name"] in {"audit_log", "audit_log_id_seq"}
            ]
            valid = len(dependencies) == 2 and all(
                row["owner"] == "normal_owner" and not row["rls"] and not row["forced_rls"]
                for row in dependencies
            )
            return ("valid",), [(valid,)]
        if sql == self.column_query and params == {}:
            return ("valid",), [
                (
                    self.audit_columns
                    == tuple((col, "text") for col in ("actor", "action", "target", "note"))
                    and self.sequence_binding == ("public", "audit_log", "id", "a"),
                )
            ]
        if sql in (apply._RELATION_ACL, apply._COLUMN_ACL) and params.get("oid") in self.tables:
            oid = params["oid"]
            if sql == apply._RELATION_ACL:
                return ("role", "verb", "grantable"), [
                    (role, verb, False) for role, verb in self.table_acl[oid]
                ]
            return ("role", "name", "verb", "grantable"), [
                (role, col, verb, False) for role, col, verb in self.column_acl[oid]
            ]
        if sql == apply._SEQUENCE_LINKS and params.get("oids") == [793001]:
            return (
                "oid",
                "parent_schema",
                "parent_name",
                "parent_column",
                "parent_owner",
                "deptype",
            ), [(793001, "public", "audit_log", "id", "normal_owner", "a")]
        produced = self.default_producer(
            sql, decoded, self.purpose, self.defaults, self.owned_dependencies
        )
        if produced is not None:
            self.effects.append(decoded)
            return produced
        if sql == self.capability_query and params == {}:
            rows = [
                ("relation", "public", self.tables[oid]["name"], "", "", verb, False)
                for oid, acl in self.table_acl.items()
                for role, verb in acl
                if role == self.purpose
            ]
            rows += [
                ("column", "public", self.tables[oid]["name"], "", col, verb, False)
                for oid, acl in self.column_acl.items()
                for role, col, verb in acl
                if role == self.purpose
            ]
            rows += [
                ("namespace", "public", "", "", "", verb, False)
                for role, verb in self.namespace_acl
                if role == self.purpose
            ]
            rows += self.default_observations(
                sql, self.purpose, self.defaults, self.owned_dependencies
            )
            return ("kind", "schema", "name", "signature", "column", "verb", "grantable"), rows
        if sql == self.public_query:
            assert set(params) == {
                "relations",
                "functions",
                "contexts",
                "object_schemas",
                "schemas",
                "owners",
            }
            assert "a.grantee=0" in sql
            return ("kind", "schema", "name", "signature", "column", "verb", "grantable"), [
                row[:7] for row in self.public_caps if row[7] == 0
            ]
        if self.function is not None and decoded == self.normalized_sql:
            self.function.update(config=["search_path=pg_catalog, pg_temp"])
            self.effects.append(decoded)
            return None, []
        if (
            decoded
            == "SELECT session_user=current_user AND rolsuper FROM pg_catalog.pg_roles WHERE rolname=session_user"
        ):
            return ("valid",), [(self.actor == "maintenance",)]
        if (
            decoded
            == 'ALTER FUNCTION "public"."priority_contacts_cascade_audit"() OWNER TO "priority_contact_audit_owner"'
        ):
            assert self.function is not None
            self.function["owner"] = self.purpose
            self.effects.append(decoded)
            return None, []
        if match := self.re.fullmatch(
            r'REVOKE ALL ON FUNCTION "public"\."priority_contacts_cascade_audit"\(\) FROM (PUBLIC|"[a-z_]+")',
            decoded,
        ):
            self.function["acl"] = [
                entry for entry in self.function["acl"] if entry[0] != match[1].strip('"')
            ]
            self.effects.append(decoded)
            return None, []
        if decoded in {
            f'GRANT USAGE ON SCHEMA "public" TO "{self.purpose}"',
            f'REVOKE ALL ON SCHEMA "public" FROM "{self.purpose}"',
        }:
            self.namespace_acl.clear()
            if decoded.startswith("GRANT"):
                self.namespace_acl.add((self.purpose, "USAGE"))
            self.effects.append(decoded)
            return None, []
        for oid, row in self.tables.items():
            target = f'"public"."{row["name"]}"'
            if (
                decoded
                == f"REVOKE ALL PRIVILEGES ON {'SEQUENCE' if row['kind'] == 'S' else 'TABLE'} {target} FROM PUBLIC"
            ):
                self.table_acl[oid] = {
                    entry for entry in self.table_acl[oid] if entry[0] != "PUBLIC"
                }
                self.effects.append(decoded)
                return None, []
            if match := self.re.fullmatch(
                r"REVOKE ALL PRIVILEGES ON (TABLE|SEQUENCE) "
                + self.re.escape(target)
                + r' FROM "([a-z_]+)"',
                decoded,
            ):
                self.table_acl[oid] = {
                    entry for entry in self.table_acl[oid] if entry[0] != match[2]
                }
                self.effects.append(decoded)
                return None, []
            if (
                decoded
                == f'GRANT INSERT ("actor", "action", "target", "note") ON TABLE {target} TO "{self.purpose}"'
            ):
                assert row["name"] == "audit_log"
                self.column_acl[oid].update(
                    (self.purpose, col, "INSERT") for col in ("actor", "action", "target", "note")
                )
                self.effects.append(decoded)
                return None, []
            if decoded == f'GRANT USAGE ON SEQUENCE {target} TO "{self.purpose}"':
                assert row["name"] == "audit_log_id_seq"
                self.table_acl[oid].add((self.purpose, "USAGE"))
                self.effects.append(decoded)
                return None, []
        return self.UNHANDLED

    def respond(self, wire, params, decoded):
        result = self.dispatch(wire, params, decoded)
        if result is not self.UNHANDLED:
            return result
        sql = self.re.sub(r"%\((\w+)\)s", r":\1", wire).replace("%%", "%")
        columns, values = self.delegate(wire, params, decoded)
        values = list(values)
        if sql == self.apply._RELATIONS:
            records = [dict(zip(columns, row, strict=True)) for row in values]
            identities = {(row["schema"], row["name"]) for row in records}
            for row in records:
                if row["name"] == "alembic_version" and row["schema"] in self.installed:
                    source_oid = next(
                        oid
                        for oid, ledger in self.ledgers.items()
                        if ledger["schema"] == row["schema"]
                    )
                    self.ledger_metadata[row["oid"]] = self.ledger_metadata[source_oid]
            records += [
                row
                for row in (*self.ledgers.values(), *self.tables.values())
                if row["schema"] in params["schemas"]
                and (row["schema"], row["name"]) not in identities
            ]
            return columns, [tuple(row[key] for key in columns) for row in records]
        if (
            sql == self.apply._FUNCTIONS
            and self.function is not None
            and "public" in params["schemas"]
        ):
            row = dict(
                oid=self.function["oid"],
                schema="public",
                name=self.function_name,
                arguments=self.function["arguments"],
                owner=self.function["owner"],
            )
            values.append(tuple(row[key] for key in columns))
        if sql == self.apply._NAMESPACE_ACL:
            # The delegate supplies the actual namespace identity; its original
            # rights remain visible alongside independently recorded audit rights.
            if self.public_namespace_oid == params["oid"]:
                values += [(role, verb, False) for role, verb in self.namespace_acl]
        if sql == self.apply._NAMESPACE_CATALOG:
            for row in values:
                if row[columns.index("name")] == "public":
                    self.public_namespace_oid = row[columns.index("oid")]
        return columns, values

    public_namespace_oid = None

    @staticmethod
    def default_observations(sql, purpose, defaults, owned_dependencies):
        # Three independent catalog inputs: ACLITEM cardinality, aclexplode
        # population, and pg_shdepend class/OID. This is transport modeling,
        # not PostgreSQL ACL interpretation or a catalog-success cache.
        source_filter = all(
            fragment in sql
            for fragment in (
                "closed_defaults AS",
                "d.defaclrole=(SELECT oid FROM purpose)",
                "d.defaclnamespace=0",
                "d.defaclobjtype IN ('f','T')",
                "pg_catalog.cardinality(d.defaclacl)=1",
                "a.grantee=d.defaclrole",
                "a.grantor=d.defaclrole",
                "a.is_grantable=false",
                "WHEN 'f' THEN 'EXECUTE' WHEN 'T' THEN 'USAGE' END",
            )
        )
        count_required = (
            "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode(d.defaclacl))=1" in sql
        )
        validated = set()
        for row in defaults:
            matching = (purpose, purpose, "EXECUTE" if row["kind"] == "f" else "USAGE", False)
            if (
                source_filter
                and row["owner"] == purpose
                and row["schema"] == ""
                and row["kind"] in {"f", "T"}
                and row["aclitem_count"] == 1
                and matching in row["exploded"]
                and (not count_required or len(row["exploded"]) == 1)
            ):
                validated.add(row["oid"])
        caps = []
        for row in defaults:
            if "d.oid NOT IN (SELECT oid FROM closed_defaults)" in sql and row["oid"] in validated:
                continue
            caps += [
                ("default", row["schema"], row["kind"], "", "", verb, grantable)
                for grantor, grantee, verb, grantable in row["exploded"]
                if row["owner"] == purpose or grantee == purpose
            ]
        for classid, oid in owned_dependencies:
            excluded = (
                "d.classid='pg_catalog.pg_default_acl'::regclass" in sql
                and "d.objid IN (SELECT oid FROM closed_defaults)" in sql
                and classid == "pg_default_acl"
                and oid in validated
            )
            if not excluded:
                caps.append(("ownership", "", "", "", "", "OWN", False))
        return caps

    @staticmethod
    def default_producer(sql, decoded, purpose, defaults, owned_dependencies):
        from butlers.db_authority import apply

        normalized = __import__("re").sub(r"%\((\w+)\)s", r":\1", sql)
        if normalized == apply._DEFAULT_ACL:
            assert "a.grantee<>d.defaclrole" in sql
            return ("owner", "schema", "kind", "role", "verb"), [
                (row["owner"], row["schema"] or None, row["kind"], grantee, verb)
                for row in defaults
                for grantor, grantee, verb, grantable in row["exploded"]
                if grantee != row["owner"]
            ]
        if decoded.startswith(f'ALTER DEFAULT PRIVILEGES FOR ROLE "{purpose}" '):
            assert decoded in {
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{purpose}" REVOKE ALL ON FUNCTIONS FROM PUBLIC',
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{purpose}" REVOKE ALL ON TYPES FROM PUBLIC',
            }
            kind, verb = ("f", "EXECUTE") if " ON FUNCTIONS " in decoded else ("T", "USAGE")
            oid = 741001 if kind == "f" else 741002
            defaults[:] = [row for row in defaults if row["oid"] != oid]
            defaults.append(
                dict(
                    oid=oid,
                    owner=purpose,
                    schema="",
                    kind=kind,
                    aclitem_count=1,
                    exploded=[(purpose, purpose, verb, False)],
                )
            )
            if ("pg_default_acl", oid) not in owned_dependencies:
                owned_dependencies.append(("pg_default_acl", oid))
            return None, []
        return None


def test_actual_finalizer_keeps_finance_materialized_view_closed_on_bounded_source():
    from butlers.db_authority import apply, grants

    for target in (None, "finance"):
        schema = target or "public"
        actual = dict(
            schema=schema,
            name="spending_summaries",
            kind="m",
            owner="normal_owner",
            rls=False,
            forced_rls=False,
            oid=42,
            partition=False,
        )
        namespace_acl = set()

        def respond(
            statement,
            parameters,
            decoded,
            schema=schema,
            actual=actual,
            namespace_acl=namespace_acl,
        ):
            source_sql = __import__("re").sub(r"%\((\w+)\)s", r":\1", statement)
            if "FROM pg_catalog.pg_database d" in statement:
                return ("database", "owner", "valid"), [("test_windows", "normal_owner", True)]
            if decoded == "SELECT current_database(), session_user":
                return ("database", "actor"), [("test_windows", "maintenance")]
            if decoded == f'SELECT version_num FROM "{schema}".alembic_version':
                return ("version_num",), [("finance_006",)]
            if source_sql == apply._FUNCTIONS:
                return ("oid", "schema", "name", "arguments", "owner"), []
            if source_sql == apply._RELATIONS:
                return tuple(actual), [tuple(actual.values())]
            if source_sql in (
                apply._PARTITION_LINKS,
                apply._SEQUENCE_LINKS,
                apply._RELATION_ACL,
                apply._COLUMN_ACL,
                apply._DEFAULT_ACL,
            ):
                return ("unused",), []
            if source_sql == apply._NAMESPACE_CATALOG:
                return ("oid", "name", "owner"), [(43, schema, "normal_owner")]
            if source_sql == apply._NAMESPACE_ACL:
                return ("role", "verb", "grantable"), [
                    (role, "USAGE", False) for role in namespace_acl
                ]
            if decoded.startswith(f'GRANT USAGE ON SCHEMA "{schema}" TO '):
                namespace_acl.add(decoded.rsplit('"', 2)[1])
                return None, []
            if decoded.startswith("ALTER DEFAULT PRIVILEGES"):
                assert " REVOKE ALL " in decoded
                return None, []
            pytest.fail("Unexpected bounded finalizer operation")

        audit_transport = _HistoricalPriorityAuditConvergenceTransport(
            respond, {schema: ("finance_006",)}
        )
        with _recording_postgresql_connection(audit_transport.respond) as (connection, calls):
            connection.info["butlers.source_database"] = "butlers"
            apply.converge_authority(
                connection, source_database="butlers", plans=(("finance", target, "finance_006"),)
            )
            assert not any(
                decoded.startswith(
                    ("GRANT SELECT", "GRANT INSERT", "ALTER TABLE ", "ALTER MATERIALIZED")
                )
                for _, _, decoded in calls
            )
            relation_reads = [
                parameters
                for statement, parameters, _ in calls
                if __import__("re").sub(r"%\((\w+)\)s", r":\1", statement) == apply._RELATIONS
            ]
            assert len(relation_reads) == 3
            graph_schemas = sorted(
                set(
                    grants.compile_relation_manifest(
                        "test_windows", "normal_owner", "maintenance", source_database="butlers"
                    ).schemas
                )
                | {schema}
            )
            assert (
                sum(parameters.get("schemas") == graph_schemas for parameters in relation_reads)
                == 1
            )
            graph_index = next(
                index
                for index, (statement, parameters, _) in enumerate(calls)
                if __import__("re").sub(r"%\((\w+)\)s", r":\1", statement) == apply._RELATIONS
                and parameters.get("schemas") == graph_schemas
            )
            first_effect = next(
                index
                for index, (_, _, decoded) in enumerate(calls)
                if decoded.lstrip().startswith(("CREATE ", "ALTER ", "DROP ", "GRANT ", "REVOKE "))
            )
            assert graph_index < first_effect
            assert (
                sum(
                    __import__("re").sub(r"%\((\w+)\)s", r":\1", statement) == apply._RELATIONS
                    for statement, parameters, _ in calls
                    if parameters.get("schemas") == [schema]
                )
                == 2
            )

        # A rejected driver operation owns its separate connection lifetime;
        # do not reuse that SQLAlchemy transaction for positive finalization.
        from sqlalchemy import text

        with _recording_postgresql_connection(audit_transport.respond) as (connection, _calls):
            with pytest.raises(pytest.fail.Exception):
                connection.execute(
                    text(audit_transport.role_query.replace("NOT rolinherit", "true")),
                    {"owners": [audit_transport.purpose], "count": 1},
                )


def _converge_source_history(plans, observed, *, installed):
    """Exercise the real historical finalizer through a recording DBAPI boundary."""
    import re

    from butlers.db_authority import apply

    namespaces = {}
    namespace_acl = {}

    def respond(statement, parameters, decoded):
        source_sql = re.sub(r"%\((\w+)\)s", r":\1", statement)
        if "FROM pg_catalog.pg_database d" in statement:
            return ("database", "owner", "valid"), [("test_windows", "normal_owner", True)]
        if decoded == "SELECT current_database(), session_user":
            return ("database", "actor"), [("test_windows", "maintenance")]
        for _chain, target, revision in plans:
            if decoded == f'SELECT version_num FROM "{target or "public"}".alembic_version':
                return ("version_num",), [(revision,)]
        if source_sql == apply._FUNCTIONS:
            return ("oid", "schema", "name", "arguments", "owner"), []
        if source_sql == apply._RELATIONS:
            return tuple(observed[0]), [tuple(row.values()) for row in observed]
        if source_sql in (
            apply._PARTITION_LINKS,
            apply._SEQUENCE_LINKS,
            apply._RELATION_ACL,
            apply._COLUMN_ACL,
            apply._DEFAULT_ACL,
        ):
            return ("unused",), []
        if source_sql == apply._NAMESPACE_CATALOG:
            namespaces.update(
                {schema: index + 100 for index, schema in enumerate(parameters["schemas"])}
            )
            return ("oid", "name", "owner"), [
                (oid, schema, "normal_owner") for schema, oid in namespaces.items()
            ]
        if source_sql == apply._NAMESPACE_ACL:
            return ("role", "verb", "grantable"), [
                (role, "USAGE", False) for role in namespace_acl.get(parameters["oid"], ())
            ]
        match = re.fullmatch(r'GRANT USAGE ON SCHEMA "([a-z_]+)" TO "([a-z_]+)"', decoded)
        if match:
            namespace_acl.setdefault(namespaces[match[1]], set()).add(match[2])
            return None, []
        if decoded.startswith("ALTER DEFAULT PRIVILEGES"):
            assert " REVOKE ALL " in decoded
            return None, []
        pytest.fail("Unexpected historical producer finalizer operation")

    audit_transport = _HistoricalPriorityAuditConvergenceTransport(respond, installed)
    with _recording_postgresql_connection(audit_transport.respond) as (connection, calls):
        connection.info["butlers.source_database"] = "butlers"
        apply.converge_authority(connection, source_database="butlers", plans=plans)
        assert not any(
            decoded.startswith(("GRANT SELECT", "GRANT INSERT", "ALTER TABLE ", "CREATE "))
            and decoded
            not in {
                audit_transport.normalized_sql,
                'GRANT INSERT ("actor", "action", "target", "note") ON TABLE "public"."audit_log" TO "priority_contact_audit_owner"',
            }
            if audit_transport.function is not None
            else decoded.startswith(("GRANT SELECT", "GRANT INSERT", "ALTER TABLE ", "CREATE "))
            for _, _, decoded in calls
        )
        assert sum("FROM pg_catalog.pg_class c" in statement for statement, _, _ in calls) >= 2

        if audit_transport.function is not None:
            from copy import deepcopy

            from butlers.db_authority import grants, interfaces

            assert audit_transport.function["owner"] == audit_transport.purpose
            assert audit_transport.function["acl"] == []
            assert audit_transport.table_acl[793000] == set()
            assert audit_transport.table_acl[793001] == {(audit_transport.purpose, "USAGE")}
            assert audit_transport.table_acl[793002] == set()
            assert audit_transport.column_acl[793000] == {
                (audit_transport.purpose, column, "INSERT")
                for column in ("actor", "action", "target", "note")
            }
            assert audit_transport.namespace_acl == {(audit_transport.purpose, "USAGE")}
            assert all(
                decoded
                in {
                    audit_transport.normalized_sql,
                    'ALTER FUNCTION "public"."priority_contacts_cascade_audit"() OWNER TO "priority_contact_audit_owner"',
                    'GRANT INSERT ("actor", "action", "target", "note") ON TABLE "public"."audit_log" TO "priority_contact_audit_owner"',
                    'GRANT USAGE ON SEQUENCE "public"."audit_log_id_seq" TO "priority_contact_audit_owner"',
                    'GRANT USAGE ON SCHEMA "public" TO "priority_contact_audit_owner"',
                }
                for decoded in audit_transport.effects
                if decoded.startswith(("CREATE ", "ALTER FUNCTION ", "GRANT "))
            )
            full = grants.compile_relation_manifest(
                "test_windows", "normal_owner", "maintenance", source_database="butlers"
            )
            identity = grants._owner_lookup_source_template_identity()
            vector = interfaces._owner_lookup_revision_vector(
                connection, full, source_identity=identity, selected_plans=plans
            )
            authority = grants.compile_historical_priority_audit(
                full.profiles, "normal_owner", vector
            )
            fields = (
                "role",
                "function",
                "trigger",
                "tables",
                "audit_columns",
                "sequence_binding",
                "table_acl",
                "column_acl",
                "defaults",
                "owned_dependencies",
                "namespace_owner",
                "public_caps",
            )
            original_state = {field: deepcopy(getattr(audit_transport, field)) for field in fields}
            for fault in (
                "role",
                "signature",
                "body",
                "owner",
                "security",
                "path",
                "trigger",
                "dependencies",
                "columns",
                "sequence",
                "execute",
                "capability",
                "default",
                "public",
                "namespace",
            ):
                if fault == "role":
                    audit_transport.role["rolcanlogin"] = True
                elif fault in {"signature", "body", "owner", "security", "path", "execute"}:
                    key, value = {
                        "signature": ("arguments", "text"),
                        "body": ("body", "invalid"),
                        "owner": ("owner", "foreign"),
                        "security": ("definer", False),
                        "path": ("config", []),
                        "execute": ("acl", [("PUBLIC", "EXECUTE", False)]),
                    }[fault]
                    audit_transport.function[key] = value
                elif fault == "trigger":
                    audit_transport.trigger["type"] = 5
                elif fault == "dependencies":
                    audit_transport.tables[793000]["owner"] = "foreign"
                elif fault == "columns":
                    audit_transport.audit_columns = ()
                elif fault == "sequence":
                    audit_transport.sequence_binding = None
                elif fault == "capability":
                    audit_transport.table_acl[793000].add((audit_transport.purpose, "SELECT"))
                elif fault == "default":
                    audit_transport.defaults.append(
                        dict(
                            oid=749991,
                            owner=audit_transport.purpose,
                            schema="public",
                            kind="f",
                            aclitem_count=1,
                            exploded=[(audit_transport.purpose, "PUBLIC", "EXECUTE", False)],
                        )
                    )
                elif fault == "public":
                    audit_transport.public_caps.append(
                        ("column", "public", "audit_log", "", "actor", "SELECT", False, 749999)
                    )
                    assert (
                        interfaces.validate_historical_priority_audit(
                            connection, full, authority, allow_legacy=False
                        )
                        is not None
                    )
                    audit_transport.public_caps.pop()
                    audit_transport.public_caps.append(
                        ("column", "public", "audit_log", "", "actor", "SELECT", False, 0)
                    )
                else:
                    audit_transport.namespace_owner = "foreign"
                before_fault = len(calls)
                with pytest.raises(AuthorityError):
                    interfaces.validate_historical_priority_audit(
                        connection, full, authority, allow_legacy=False
                    )
                assert not any(
                    decoded.lstrip().startswith(("CREATE ", "ALTER ", "DROP ", "GRANT ", "REVOKE "))
                    for _, _, decoded in calls[before_fault:]
                ), fault
                for field in fields:
                    setattr(audit_transport, field, deepcopy(original_state[field]))
            assert (
                interfaces.validate_historical_priority_audit(
                    connection, full, authority, allow_legacy=False
                )
                is not None
            )


def test_sw037_receiver_view_preserves_actual_qa_only_producer_and_revision_edge():
    import runpy
    from pathlib import Path

    from butlers.db_authority import apply, grants

    root = Path(__file__).parents[2]
    producer = runpy.run_path(
        str(root / "roster/switchboard/migrations/037_qa_receiver_state_view.py")
    )
    emitted = []
    bind = Mock()
    bind.exec_driver_sql.return_value.scalar_one.return_value = "switchboard"
    with (
        patch("alembic.op.get_bind", return_value=bind),
        patch("alembic.op.execute", side_effect=emitted.append),
    ):
        producer["upgrade"]()
    name = "v_qa_butler_receiver_state"
    assert any(f"CREATE OR REPLACE VIEW public.{name} AS" in sql for sql in emitted)
    assert emitted[-2:] == [
        f"REVOKE ALL ON public.{name} FROM PUBLIC",
        f"GRANT SELECT ON public.{name} TO butler_qa_rw",
    ]
    full = grants.compile_relation_manifest(
        "test_windows", "normal_owner", "maintenance", source_database="butlers"
    )
    row = next(r for r in full.relations if (r.schema, r.name) == ("public", name))
    assert row.kind == grants.ObjectKind.VIEW and row.owner == "normal_owner"
    assert row.permissions == (grants.Permission("butler_qa_rw", ("SELECT",)),)
    actual = dict(
        schema="public",
        name=name,
        kind="v",
        owner="normal_owner",
        rls=False,
        forced_rls=False,
        oid=42,
        partition=False,
    )
    assert apply.bind_partition_relations((actual,), (row,), ()) == (row,)
    assert apply.validate_relations((actual,), (row,)) == ((actual, row),)
    statements = []
    with _recording_postgresql_connection(
        lambda _s, _p, decoded: statements.append(decoded) or (None, [])
    ) as (connection, _):
        apply._grant_relation(connection, row)
    assert statements == [f'GRANT SELECT ON TABLE "public"."{name}" TO "butler_qa_rw"']
    for target in (None, "switchboard"):
        for revision, present in (("sw_036", False), ("sw_037", True), (None, True)):
            manifest = grants.select_migration_manifest(full, (("switchboard", target, revision),))
            rows = [value for value in manifest.relations if value.name == name]
            assert bool(rows) is present
            assert all(value.required is (revision is None) for value in rows)
            assert all(bool(value.permissions) is (revision is None) for value in rows)
        assert not any(
            value.name == name
            for value in grants.select_migration_manifest(full, (("core", target, None),)).relations
        )
    for invalid in (None, dict(kind="r"), dict(owner="foreign"), dict(name="unknown_view")):
        with pytest.raises(AuthorityError):
            apply.validate_relations(() if invalid is None else (actual | invalid,), (row,))
    with pytest.raises(AuthorityError):
        grants.select_migration_manifest(
            full, (("switchboard", "switchboard", "unknown_revision"),)
        )
    _converge_source_history(
        (("switchboard", "switchboard", "sw_037"),),
        (actual,),
        installed={"switchboard": ("sw_037",)},
    )


def test_general_rename_manifest_follows_actual_producer_and_closed_historical_window():
    import runpy
    from pathlib import Path

    from butlers.db_authority import apply, grants

    root = Path(__file__).parents[2]
    emitted = []
    for filename in ("001_general_tables.py", "002_rename_entities_to_collection_items.py"):
        producer = runpy.run_path(str(root / "roster/general/migrations" / filename))
        with patch("alembic.op.execute", side_effect=emitted.append):
            producer["upgrade"]()
    assert any("CREATE TABLE IF NOT EXISTS entities" in sql for sql in emitted)
    assert any("ALTER TABLE entities RENAME TO collection_items" in sql for sql in emitted)
    full = grants.compile_relation_manifest(
        "test_windows", "normal_owner", "maintenance", source_database="butlers"
    )
    current = grants.select_migration_manifest(full, (("general", "general", None),))
    row = next(
        r for r in current.relations if (r.schema, r.name) == ("general", "collection_items")
    )
    assert row.required and row.permissions == (
        grants.Permission("butler_general_rw", ("SELECT", "INSERT", "UPDATE", "DELETE")),
        grants.Permission("dashboard_api", ("SELECT", "INSERT", "UPDATE", "DELETE")),
    )
    assert not any(r.schema == "general" and r.name == "entities" for r in current.relations)
    assert any(r.schema == "public" and r.name == "entities" for r in full.relations)
    for revision, old_name in (("gen_001", "entities"), ("gen_002", "collection_items")):
        manifest = grants.select_migration_manifest(full, (("general", "general", revision),))
        rows = [
            r
            for r in manifest.relations
            if r.schema == "general" and r.name in {"entities", "collection_items"}
        ]
        assert [r.name for r in rows] == [old_name]
        assert rows[0].permissions == () and not rows[0].required
        actual = dict(
            schema="general",
            name=old_name,
            kind="r",
            owner="normal_owner",
            rls=False,
            forced_rls=False,
            oid=42,
            partition=False,
        )
        _converge_source_history(
            (("general", "general", revision),), (actual,), installed={"general": (revision,)}
        )
    accumulated = grants.select_migration_manifest(
        full, (("general", "general", "gen_001"), ("general", "general", None))
    )
    assert not any(r.schema == "general" and r.name == "entities" for r in accumulated.relations)
    actual = dict(
        schema="general",
        name="collection_items",
        kind="r",
        owner="normal_owner",
        rls=False,
        forced_rls=False,
        oid=42,
        partition=False,
    )
    assert apply.validate_relations((actual,), (row,)) == ((actual, row),)
    for observed in ((), (actual | {"name": "entities"},), (actual | {"owner": "foreign"},)):
        with pytest.raises(AuthorityError):
            apply.validate_relations(observed, (row,))


def test_general_briefing_requiredness_follows_actual_core063_not_domain_chain():
    import runpy
    from pathlib import Path

    from butlers.db_authority import apply, grants

    root = Path(__file__).parents[2]
    foundation = runpy.run_path(str(root / "alembic/versions/core/core_001_foundation.py"))
    emitted = []
    with patch("alembic.op.execute", side_effect=emitted.append):
        foundation["_create_required_schemas"]()
    assert 'CREATE SCHEMA IF NOT EXISTS "general"' in emitted
    producer = runpy.run_path(
        str(root / "alembic/versions/core/core_063_v_briefing_contributions.py")
    )
    bind = Mock()
    bind.execute.side_effect = lambda statement, _params: Mock(
        scalar=lambda: 1 if "information_schema.schemata" in str(statement) else None
    )
    with (
        patch("alembic.op.get_bind", return_value=bind),
        patch("alembic.op.execute", side_effect=emitted.append),
    ):
        producer["upgrade"]()
    assert any(
        "CREATE OR REPLACE VIEW general.v_briefing_contributions AS" in sql and "WHERE FALSE" in sql
        for sql in emitted
    )
    full = grants.compile_relation_manifest(
        "test_windows", "normal_owner", "maintenance", source_database="butlers"
    )
    identity = ("general", "v_briefing_contributions")
    domain = grants.select_migration_manifest(full, (("general", "general", None),))
    optional = next(r for r in domain.relations if (r.schema, r.name) == identity)
    assert optional.required is False
    assert apply.validate_relations((), (optional,)) == ()
    complete = grants.select_migration_manifest(
        full, (("core", "general", None), ("general", "general", None))
    )
    required = next(r for r in complete.relations if (r.schema, r.name) == identity)
    assert required.required and required.permissions == optional.permissions
    with pytest.raises(AuthorityError):
        apply.validate_relations((), (required,))
    assert {permission.role for permission in required.permissions} == {
        "butler_general_rw",
        "dashboard_api",
        "butler_switchboard_rw",
    }
    for revision in ("core_062", "core_063"):
        manifest = grants.select_migration_manifest(full, (("core", "general", revision),))
        historical = next(r for r in manifest.relations if (r.schema, r.name) == identity)
        assert not historical.required and historical.permissions == ()


def test_public_contacts_window_uses_real_core002_and_core134_without_current_admission():
    import runpy
    from pathlib import Path

    from butlers.db_authority import apply, grants

    root = Path(__file__).parents[2]
    emitted = []
    for filename in ("core_002_identity.py", "core_134_drop_public_contacts.py"):
        producer = runpy.run_path(str(root / "alembic/versions/core" / filename))
        with patch("alembic.op.execute", side_effect=emitted.append):
            producer["upgrade"]()
    assert any("CREATE TABLE IF NOT EXISTS public.contacts (" in sql for sql in emitted)
    assert any(
        "DROP TABLE public.contacts;" in sql
        and "CREATE TABLE public.contacts_dropbak AS TABLE public.contacts" in sql
        for sql in emitted
    )
    full = grants.compile_relation_manifest(
        "test_windows", "normal_owner", "maintenance", source_database="butlers"
    )
    actual = dict(
        schema="public",
        name="contacts",
        kind="r",
        owner="normal_owner",
        rls=False,
        forced_rls=False,
        oid=42,
        partition=False,
    )
    for revision, present in (
        ("core_001", False),
        ("core_002", True),
        ("core_133", True),
        ("core_134", False),
        (None, False),
    ):
        manifest = grants.select_migration_manifest(full, (("core", None, revision),))
        rows = [r for r in manifest.relations if (r.schema, r.name) == ("public", "contacts")]
        assert bool(rows) is present
        if present:
            (row,) = rows
            assert not row.required and row.permissions == () and row.owner == "normal_owner"
            assert apply.validate_relations((actual,), (row,), administrative_history=True) == (
                (actual, row),
            )
            _converge_source_history(
                (("core", None, revision),), (actual,), installed={"public": (revision,)}
            )
            with pytest.raises(AuthorityError):
                apply.validate_relations(
                    (actual | {"owner": "foreign"},), (row,), administrative_history=True
                )
        else:
            with pytest.raises(AuthorityError):
                apply.bind_partition_relations((actual,), manifest.relations, ())
    accumulated = grants.select_migration_manifest(
        full, (("core", None, "core_133"), ("core", "general", None))
    )
    assert not any((r.schema, r.name) == ("public", "contacts") for r in accumulated.relations)


class _SelectedPolicyCatalog:
    """Source-derived catalog protocol double, never native PostgreSQL proof.

    All production compilers, validators, installers and policy producers run.
    The physical recording DBAPI supplies fixed current catalog observations;
    scratch shape comparisons preserve complete source declarations as opaque
    structural values rather than claiming PostgreSQL parsing/normalization.
    Persistent effects are tracked separately from temporary parser DDL.
    """

    def __init__(self, optional_domain_present=False, fault=None):
        import re

        from butlers.db_authority import apply, grants, interfaces
        from butlers.migrations import get_chain_head

        self.apply, self.grants, self.interfaces = apply, grants, interfaces
        self.re = re
        self.plans = (("core", "switchboard", None),)
        self.head = get_chain_head("core")
        self.audit_transport = _HistoricalPriorityAuditConvergenceTransport(
            self._respond_without_audit, {"switchboard": (self.head,)}
        )
        self.manifest = grants.select_migration_manifest(
            grants.compile_relation_manifest(
                "test_policy_targets", "normal_owner", "maintenance", source_database="butlers"
            ),
            self.plans,
        )
        self.optional = {
            ("switchboard", "butler_boot_registrations"),
            ("switchboard", "butler_registry_control_plane"),
        }
        selected = {(r.schema, r.name): r for r in self.manifest.relations}
        assert self.optional <= selected.keys()
        assert all(not selected[key].required for key in self.optional)
        self.relations = {}
        self.authority = {}
        for row in self.manifest.relations:
            key = row.schema, row.name
            if not row.required and not (optional_domain_present and key in self.optional):
                continue
            actual = dict(
                oid=len(self.relations) + 1,
                schema=row.schema,
                name=row.name,
                kind={"table": "r", "view": "v", "materialized_view": "m", "sequence": "S"}[
                    row.kind.value
                ],
                owner=row.owner,
                rls=apply.relation_security_flags(row)[0],
                forced_rls=apply.relation_security_flags(row)[1],
                partition=False,
            )
            self.relations[key] = actual
            self.authority[actual["oid"]] = row
        self.contract = grants.compiled_security_contract(self.manifest)
        self.policies = {}
        self.triggers = {}
        for category, target in (("policies", self.policies), ("triggers", self.triggers)):
            for row in self.contract[category]:
                key = row["schema"], row["table"]
                if key in self.relations:
                    target.setdefault(key, {})[row["name"]] = row
        assert self.optional <= {
            key[:2] for key in interfaces.ordinary_security_policy_changes(self.manifest)
        }
        self.scratch = {}
        self.effects = []
        self.scratch_events = []
        self.queried = set()
        self.relation_acl = {
            oid: {(p.role, verb) for p in row.permissions if not p.columns for verb in p.verbs}
            for oid, row in self.authority.items()
        }
        self.column_acl = {
            oid: {
                (p.role, column, verb)
                for p in row.permissions
                for column in p.columns
                for verb in p.verbs
            }
            for oid, row in self.authority.items()
        }
        definitions = {
            row.identity: row
            for row in (
                *grants.compile_function_manifest(
                    self.manifest.profiles, "normal_owner", self.plans
                ),
                *interfaces.selected_protected_functions(
                    self.manifest.profiles, "normal_owner", self.plans
                ),
            )
        }
        self.functions = {
            interfaces._signature(row): (10000 + index, row)
            for index, row in enumerate(definitions.values())
        }
        self.namespace_oids = {
            schema: 20000 + index
            for index, schema in enumerate(
                sorted(set(self.manifest.schemas) | {row.schema for row in definitions.values()})
            )
        }
        self.namespace_acl = {oid: set() for oid in self.namespace_oids.values()}
        self.shape_sources = {
            (schema, name): (body, indexes)
            for _group, schema, name, body, indexes in interfaces._PROTECTED_TABLE_SHAPES
        }
        if fault == "missing_required":
            del self.relations["switchboard", "sessions"]
        elif fault == "unknown_relation":
            self.relations["switchboard", "unlisted_policy_target"] = dict(
                self.relations["switchboard", "sessions"],
                oid=9999,
                name="unlisted_policy_target",
            )
        elif fault == "wrong_owner":
            self.relations["switchboard", "sessions"]["owner"] = "unlisted_owner"
        elif fault == "wrong_rls":
            self.relations["switchboard", "sessions"]["rls"] = True
        elif fault == "unknown_policy":
            self.policies.setdefault(("switchboard", "sessions"), {})["unlisted_policy"] = {
                "name": "unlisted_policy",
                "command": "*",
                "roles": ["PUBLIC"],
            }
        else:
            assert fault is None

    @staticmethod
    def rows(rows, columns=("unused",)):
        rows = list(rows)
        if rows and isinstance(rows[0], dict):
            columns = tuple(rows[0])
            rows = [tuple(row[column] for column in columns) for row in rows]
        return columns, rows

    def target(self, value):
        parts = tuple(self.re.findall(r'"([a-zA-Z0-9_]+)"|\b(pg_temp)\b', value))
        names = tuple(a or b for a, b in parts)
        assert len(names) == 2
        return names

    def shape(self, query, key):
        interfaces = self.interfaces
        original = self.scratch[key]["source"] if key[0] == "pg_temp" else key
        if query == interfaces._SHAPE_SECURITY:
            actual = self.relations[original]
            return self.rows(
                [dict(owner=actual["owner"], enabled=actual["rls"], forced=actual["forced_rls"])]
            )
        if query in (interfaces._SHAPE_POLICIES, interfaces._SHAPE_TRIGGERS):
            category = "policies" if query == interfaces._SHAPE_POLICIES else "triggers"
            if key[0] == "pg_temp":
                values = self.scratch[key][category]
            else:
                values = (self.policies if category == "policies" else self.triggers).get(key, {})
            return self.rows(values[name] for name in sorted(values))
        body, indexes = self.shape_sources[original]
        # These are explicit source-structural transport values, not native
        # pg_attribute/pg_constraint/parser observations. Full source text is
        # compared; an arbitrary empty shape cannot satisfy this recorder.
        if query == interfaces._SHAPE_COLUMNS:
            serials = interfaces._SERIAL_COLUMNS.get(original, ())
            return self.rows(
                [
                    *[
                        dict(attname=name, type="bigint", attnotnull=True, default="source_serial")
                        for name in serials
                    ],
                    dict(attname="source_columns", type=body, attnotnull=True, default=None),
                ]
            )
        if query == interfaces._SHAPE_CONSTRAINTS:
            return self.rows(
                [dict(contype="source_constraints", definition=body, convalidated=True)]
            )
        assert query == interfaces._SHAPE_INDEXES
        return self.rows(
            dict(amname="source_index", indisunique=unique, definition=suffix)
            for unique, suffix in indexes
        )

    def respond(self, statement, parameters, decoded):
        return self.audit_transport.respond(statement, parameters, decoded)

    def _respond_without_audit(self, statement, parameters, decoded):
        import hashlib

        apply, interfaces = self.apply, self.interfaces
        source = self.re.sub(r"%\((\w+)\)s", r":\1", statement).replace("%%", "%")
        source = source.replace(r"\:", ":")
        self.queried.add(hashlib.sha256(source.encode()).hexdigest())
        if "FROM pg_catalog.pg_database d" in source:
            return self.rows(
                [("test_policy_targets", "normal_owner", True)], ("db", "owner", "valid")
            )
        if decoded == "SELECT current_database(), session_user":
            return self.rows([("test_policy_targets", "maintenance")], ("database", "actor"))
        if "current_user=session_user AND rolsuper" in decoded:
            return self.rows([(True,)], ("valid",))
        if decoded == "SELECT session_user":
            return self.rows([("maintenance",)], ("actor",))
        if decoded == 'SELECT version_num FROM "switchboard".alembic_version':
            return self.rows([(self.head,)], ("version_num",))
        if source == apply._FUNCTIONS:
            return self.rows(
                dict(
                    oid=oid,
                    schema=row.schema,
                    name=row.name,
                    arguments=row.signature,
                    owner=row.owner,
                )
                for oid, row in self.functions.values()
                if row.schema in parameters["schemas"]
            )
        if source == apply._RELATIONS:
            return self.rows(self.relations.values())
        if source == apply._PARTITION_LINKS:
            return self.rows([])
        if source == apply._SEQUENCE_LINKS:
            return self.rows(
                dict(
                    oid=oid,
                    parent_schema=row.sequence_parent[0],
                    parent_name=row.sequence_parent[1],
                    parent_column=row.sequence_parent[2],
                    parent_owner=self.relations[row.sequence_parent[:2]]["owner"],
                )
                for oid, row in self.authority.items()
                if row.sequence_parent is not None and oid in parameters["oids"]
            )
        if source == apply._NAMESPACE_CATALOG:
            return self.rows(
                dict(
                    oid=self.namespace_oids[schema],
                    name=schema,
                    owner={
                        "dnd_generation_private": "dnd_generation_owner",
                        "restore_drill_executor": "restore_drill_executor_owner",
                        "dashboard_attention": "maintenance",
                        "dashboard_stepup": "maintenance",
                    }.get(schema, "normal_owner"),
                )
                for schema in parameters["schemas"]
            )
        if source == apply._NAMESPACE_ACL:
            return self.rows(
                dict(role=role, verb=verb, grantable=False)
                for role, verb in sorted(self.namespace_acl[parameters["oid"]])
            )
        if source == apply._RELATION_ACL:
            return self.rows(
                dict(role=role, verb=verb, grantable=False)
                for role, verb in sorted(self.relation_acl[parameters["oid"]])
            )
        if source == apply._COLUMN_ACL:
            return self.rows(
                dict(role=role, name=name, verb=verb, grantable=False)
                for role, name, verb in sorted(self.column_acl[parameters["oid"]])
            )
        if source == apply._DEFAULT_ACL:
            return self.rows([])
        if source == interfaces._FUNCTION_CATALOG:
            oid, row = self.functions[parameters["signature"]]
            return self.rows(
                [
                    dict(
                        oid=oid,
                        owner=row.owner,
                        definer=row.security_definer,
                        config=getattr(row, "settings", ("search_path=pg_catalog, pg_temp",)),
                        body=row.body,
                        arguments=row.signature,
                    )
                ]
            )
        if source == interfaces._FUNCTION_ACL:
            row = next(row for oid, row in self.functions.values() if oid == parameters["oid"])
            return self.rows(
                dict(role=role, privilege="EXECUTE", grantable=False)
                for role in row.executors
                if role != row.owner
            )
        if "WITH owners AS" in source and "pg_auth_members" in source:
            assert parameters["count"] == len(parameters["owners"])
            from butlers.db_authority.roles import PURPOSE_OWNERS

            assert set(parameters["owners"]) <= set(PURPOSE_OWNERS)
            return self.rows([(True,)], ("valid",))
        if "WHERE n.nspname=ANY(:schemas) OR (n.nspname='public'" in source:
            assert parameters["schemas"] == list(interfaces._RETIRED_ADMIN_SCHEMAS)
            return self.rows([])
        if "FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles owner" in source:
            assert parameters["schemas"] == list(interfaces._RETIRED_ADMIN_SCHEMAS)
            return self.rows([])
        if "WHERE p.proname='runtime_attention_plant_legacy_debounce_marker'" in source:
            return self.rows([])
        if "SELECT c.relkind" in source and "schema" in parameters:
            row = self.relations[parameters["schema"], parameters["name"]]
            return self.rows([dict(relkind=row["kind"], owner=row["owner"])])
        if "SELECT count(*)=1 AND bool_and(s.relkind='S'" in source:
            return self.rows([(True,)], ("valid",))
        if "SELECT 'nextval(' || quote_literal(pg_catalog.pg_get_serial_sequence(" in source:
            assert parameters["column"] == "id"
            return self.rows([("source_serial",)], ("default",))
        if "SELECT EXISTS(SELECT FROM pg_catalog.pg_trigger" in source:
            return self.rows([(True,)], ("present",))
        shape_queries = (
            interfaces._SHAPE_SECURITY,
            interfaces._SHAPE_COLUMNS,
            interfaces._SHAPE_CONSTRAINTS,
            interfaces._SHAPE_INDEXES,
            interfaces._SHAPE_POLICIES,
            interfaces._SHAPE_TRIGGERS.replace(r"\:", ":"),
        )
        if source in shape_queries:
            query = interfaces._SHAPE_TRIGGERS if source == shape_queries[-1] else source
            return self.shape(query, self.target(parameters["relation"]))
        if source == "SELECT pg_catalog.to_regclass(:relation) IS NOT NULL":
            return self.rows([(self.target(parameters["relation"]) in self.scratch,)], ("present",))
        create = self.re.fullmatch(r'CREATE TEMPORARY TABLE "(\w+)" \((.*)\)', decoded, self.re.S)
        if create:
            name, body = create.groups()
            original = (
                self.target(body.removeprefix("LIKE "))
                if body.startswith("LIKE ")
                else next(key for key, value in self.shape_sources.items() if value[0] == body)
            )
            self.scratch["pg_temp", name] = dict(source=original, policies={}, triggers={})
            self.scratch_events.append("create")
            return None, []
        like = self.re.fullmatch(r'CREATE TEMPORARY TABLE "(\w+)" \(LIKE (.+)\)', decoded)
        if like:
            name, target = like.groups()
            self.scratch["pg_temp", name] = dict(
                source=self.target(target), policies={}, triggers={}
            )
            self.scratch_events.append("create")
            return None, []
        if decoded.startswith("CREATE ") and "INDEX" in decoded:
            assert " ON pg_temp." in decoded
            self.scratch_events.append("index")
            return None, []
        policy = self.re.match(
            r'(CREATE|DROP) POLICY (?:IF EXISTS )?"(\w+)" ON (pg_temp|"\w+")\."(\w+)"', decoded
        )
        if policy:
            operation, name, schema, table = policy.groups()
            schema = schema.strip('"')
            key = schema, table
            if schema == "pg_temp":
                original = self.scratch[key]["source"]
                target = self.scratch[key]["policies"]
                self.scratch_events.append("policy")
            else:
                self.effects.append((operation + " POLICY", key, name))
                if key not in self.relations:
                    raise LookupError("absent optional policy target")
                original = key
                target = self.policies.setdefault(key, {})
            if operation == "DROP":
                target.pop(name, None)
            else:
                row = next(
                    r
                    for r in self.contract["policies"]
                    if (r["schema"], r["table"], r["name"]) == (*original, name)
                )
                allowed = {
                    interfaces._ordinary_policy_sql(row, target_sql, "normal_owner")
                    for target_sql in [
                        'pg_temp."' + table + '"'
                        if schema == "pg_temp"
                        else '"' + schema + '"."' + table + '"'
                    ]
                }
                if schema == "pg_temp":
                    allowed.update(
                        f'CREATE POLICY "{name}" ON pg_temp."{table}" {suffix}'
                        for literal in interfaces._RELATION_POLICIES.get(original[1], ())
                        for policy_name, suffix in [literal.split(" ", 1)]
                        if policy_name == name
                    )
                assert decoded in allowed
                target[name] = row
            return None, []
        trigger = self.re.match(
            r'CREATE (?:CONSTRAINT )?TRIGGER "(\w+)" .* ON (pg_temp|"\w+")\."(\w+)"', decoded
        )
        if trigger:
            name, schema, table = trigger.groups()
            assert schema == "pg_temp"
            original = self.scratch[schema, table]["source"]
            row = next(
                r
                for r in self.contract["triggers"]
                if (r["schema"], r["table"], r["name"]) == (*original, name)
            )
            self.scratch[schema, table]["triggers"][name] = row
            self.scratch_events.append("trigger")
            return None, []
        if decoded.startswith("ALTER TABLE pg_temp."):
            self.scratch_events.append("enable-trigger")
            return None, []
        if decoded.startswith("DROP TABLE pg_temp."):
            del self.scratch[self.target(decoded.removeprefix("DROP TABLE "))]
            self.scratch_events.append("drop")
            return None, []
        namespace = self.re.fullmatch(
            r'(GRANT (?:USAGE|CREATE)|REVOKE ALL) ON SCHEMA "(\w+)" (?:TO|FROM) "(\w+)"', decoded
        )
        if namespace:
            operation, schema, role = namespace.groups()
            self.effects.append((operation, (schema,), role))
            entries = self.namespace_acl[self.namespace_oids[schema]]
            if operation == "REVOKE ALL":
                entries.difference_update({entry for entry in entries if entry[0] == role})
            else:
                entries.add((role, operation.split()[1]))
            return None, []
        if decoded.startswith("ALTER DEFAULT PRIVILEGES"):
            assert " REVOKE ALL " in decoded
            self.effects.append(("DEFAULT REVOKE", (), ""))
            return None, []
        relation = self.re.fullmatch(
            r'(GRANT .+|REVOKE ALL(?: PRIVILEGES| \(.+\))?) ON (TABLE|SEQUENCE) ("\w+"\."\w+") (?:TO|FROM) ("\w+"|PUBLIC)',
            decoded,
        )
        if relation:
            operation, _kind, target, role = relation.groups()
            key = self.target(target)
            actual = self.relations[key]
            row = self.authority[actual["oid"]]
            role = role.strip('"')
            self.effects.append((operation, key, role))
            if operation.startswith("REVOKE"):
                if operation == "REVOKE ALL PRIVILEGES":
                    self.relation_acl[actual["oid"]].difference_update(
                        {item for item in self.relation_acl[actual["oid"]] if item[0] == role}
                    )
                else:
                    column = operation.removeprefix('REVOKE ALL ("').removesuffix('")')
                    self.column_acl[actual["oid"]].difference_update(
                        {
                            item
                            for item in self.column_acl[actual["oid"]]
                            if item[:2] == (role, column)
                        }
                    )
            else:
                allowed = []
                for permission in row.permissions:
                    verbs = ", ".join(permission.verbs)
                    if permission.columns:
                        columns = ", ".join(
                            apply.quote_identifier(column) for column in permission.columns
                        )
                        verbs = ", ".join(f"{verb} ({columns})" for verb in permission.verbs)
                    if permission.role == role and operation == "GRANT " + verbs:
                        allowed.append(permission)
                assert allowed
                for permission in allowed:
                    if permission.columns:
                        self.column_acl[actual["oid"]].update(
                            (role, col, verb)
                            for col in permission.columns
                            for verb in permission.verbs
                        )
                    else:
                        self.relation_acl[actual["oid"]].update(
                            (role, verb) for verb in permission.verbs
                        )
            return None, []
        raise AssertionError(
            "Unmodeled catalog operation " + hashlib.sha256(source.encode()).hexdigest()
        )


@pytest.mark.parametrize("optional_domain_present", [False, True])
def test_actual_selected_policy_convergence_uses_only_validated_present_tables(
    optional_domain_present,
):
    from butlers.db_authority import apply, interfaces

    model = _SelectedPolicyCatalog(optional_domain_present)
    with _recording_postgresql_connection(model.respond) as (connection, calls):
        connection.info["butlers.source_database"] = "butlers"
        apply.converge_authority(connection, source_database="butlers", plans=model.plans)
        changes = interfaces.ordinary_security_policy_changes(model.manifest)
        expected = {key for key in changes if key[:2] in model.relations}
        assert {
            (*key, name) for operation, key, name in model.effects if operation == "DROP POLICY"
        } == expected
        assert {
            (*key, name) for operation, key, name in model.effects if operation == "CREATE POLICY"
        } == expected
        assert (
            bool(expected & {key for key in changes if key[:2] in model.optional})
            is optional_domain_present
        )
        assert not model.scratch
        assert model.scratch_events
        assert __import__("hashlib").sha256(apply._RELATIONS.encode()).hexdigest() in model.queried
        assert calls
        assert connection.get_transaction() is None


@pytest.mark.parametrize(
    "fault", ["missing_required", "unknown_relation", "wrong_owner", "wrong_rls", "unknown_policy"]
)
def test_actual_selected_policy_convergence_keeps_preflight_refusals(fault):
    from butlers.db_authority import apply

    model = _SelectedPolicyCatalog(fault=fault)
    expected = {
        "missing_required": "Required database object is unavailable",
        "unknown_relation": "Managed database object is not declared",
        "wrong_owner": "Managed database object ownership is unavailable",
        "wrong_rls": "Required database row security is unavailable",
        "unknown_policy": "Database policy definition differs from source",
    }
    with _recording_postgresql_connection(model.respond) as (connection, _calls):
        connection.info["butlers.source_database"] = "butlers"
        with pytest.raises(AuthorityError, match=expected[fault]):
            apply.converge_authority(connection, source_database="butlers", plans=model.plans)
        assert model.effects == []
        assert not model.scratch


@pytest.mark.parametrize("finance_revision", [None, "finance_018"])
def test_serving_deployment_preserves_source_module_prerequisites(finance_revision):
    """Record the real source plan; physical migration/admission remains native proof."""
    from collections import Counter
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers import migrations
    from butlers.db_authority.grants import deployment_function_plans
    from butlers.db_authority.roles import runtime_profiles
    from butlers.testing import migration

    # These source producers are deliberately real, including the current roster.
    chains = migrations.get_all_chains()
    source_plans = tuple(
        row for row in deployment_function_plans(runtime_profiles("butlers")) if row[0] in chains
    )
    chain_order = {chain: index for index, chain in enumerate(chains)}
    expected = tuple(
        (chain, schema, finance_revision if chain == "finance" else revision)
        for chain, schema, revision in sorted(
            source_plans, key=lambda row: (chain_order[row[0]], row[1] or "")
        )
    )
    core = tuple(row for row in expected if row[0] == "core")
    ordered = (*core, *(row for row in expected if row[0] != "core"))
    events = []
    held = object()
    container = SimpleNamespace(
        get_container_host_ip=lambda: "localhost", get_exposed_port=lambda _: 5432
    )

    class PhysicalConnection:
        def execute(self, statement, params=None):
            if str(statement).startswith("CREATE ROLE"):
                events.append(("mint",))
            return SimpleNamespace(scalar_one=lambda: True)

        def exec_driver_sql(self, statement):
            return None

    @contextmanager
    def connect():
        yield PhysicalConnection()

    @contextmanager
    def maintenance(url, *, source_database):
        assert url == "postgresql://selected_control@localhost/butlers"
        assert source_database == "butlers"
        yield held

    def stage(url, *, chain, schema, revision, connection):
        assert url == "normal-owner-url" and connection is held
        events.append(("migration", chain, schema, revision))

    async def current(url, *, chain, schema, connection):
        stage(url, chain=chain, schema=schema, revision=None, connection=connection)

    def finalize(connection, *, source_database, plans):
        assert connection is held and source_database == "butlers"
        events.append(("finalize", tuple(plans)))

    engine = SimpleNamespace(connect=connect, dispose=Mock())
    with (
        patch.object(migration, "create_migration_db", return_value="normal-owner-url"),
        patch.object(
            migration,
            "migration_bootstrap_db_url",
            return_value="postgresql://selected_control@localhost/butlers",
        ),
        patch.object(migration, "create_engine", return_value=engine),
        patch.object(migration, "_upgrade_chain_to_revision", side_effect=stage),
        patch.object(migrations, "migration_session", maintenance),
        patch.object(migrations, "run_migrations", current),
        patch.object(migrations, "finalize_authority", finalize),
    ):
        with migration.serving_test_deployment(container, finance_revision=finance_revision):
            actual = [row[1:] for row in events if row[0] == "migration"]
            # The unchanged helper reaches this ordering assertion before the fix.
            assert actual.index(("memory", "lifestyle", None)) < actual.index(
                ("lifestyle", "lifestyle", None)
            )
            assert actual == [
                *(("core", schema, "core_114") for _, schema, _ in core),
                *ordered,
            ]
            finalized = [event[1] for event in events if event[0] == "finalize"]
            assert finalized == [expected]
            assert Counter(finalized[0]) == Counter(
                (chain, schema, finance_revision if chain == "finance" else revision)
                for chain, schema, revision in source_plans
            )
            assert events.index(("finalize", expected)) < next(
                index for index, event in enumerate(events) if event[0] == "mint"
            )
            assert all(event[0] == "migration" for event in events[: len(actual)])
        engine.dispose.assert_called()


class _ConnectorsReplayCatalog:
    """Catalog transport recorder; SQL shape parsing and PostgreSQL remain native proof."""

    def __init__(self, *, normal=False, local=False, closed=False, fault=None):
        from butlers.db_authority import grants, interfaces
        from butlers.db_authority.apply import namespace_permissions

        self.normal, self.local, self.closed, self.fault = normal or closed, local, closed, fault
        self.statements, self.scratch = [], set()
        self.manifest = grants.compile_relation_manifest(
            "test_replay", "normal_owner", "maintenance", source_database="butlers"
        )
        if closed:
            self.manifest = grants.select_migration_manifest(
                self.manifest,
                (("core", "general", None), ("finance", "finance", "finance_018")),
            )
            declaration = next(
                row
                for row in self.manifest.relations
                if (row.schema, row.name) == ("connectors", "filtered_events")
            )
            assert declaration.owner == "normal_owner"
            assert declaration.permissions == ()
        self.functions = {
            row.schema: row
            for row in grants.compile_function_manifest(
                self.manifest.profiles, "normal_owner", (("core", "general", None),)
            )
            if row.name == "connectors_filtered_events_ensure_partition"
        }
        if closed:
            from dataclasses import replace

            self.functions = {
                schema: replace(row, executors=()) for schema, row in self.functions.items()
            }
        self.parent = dict(
            oid=11,
            schema="connectors",
            name="filtered_events",
            kind="p",
            owner="normal_owner" if normal or closed else "connector_partition_owner",
            partition=False,
            rls=False,
            forced_rls=False,
            reloptions=None,
            relreplident="d",
            persistence="p",
            partition_key="RANGE (received_at)",
            bound=None,
            parents=[],
            rules=False,
        )
        self.child = dict(
            self.parent,
            oid=12,
            name="filtered_events_202610",
            kind="r",
            partition=True,
            partition_key=None,
            bound="FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00')",
            parents=[11],
        )
        self.relations = [self.parent, self.child]
        self.indexes = []
        definitions = (
            ("filtered_events_pkey", ["received_at", "id"], [0, 0], True),
            (
                "ix_filtered_events_drain",
                ["connector_type", "endpoint_identity", "status", "received_at"],
                [0, 0, 0, 3],
                False,
            ),
            ("ix_filtered_events_timeline", ["received_at"], [3], False),
            ("ix_filtered_events_id", ["id"], [0], False),
            ("ix_filtered_events_status_received", ["status", "received_at"], [0, 3], False),
        )
        for index, (name, columns, options, primary) in enumerate(definitions):
            self.indexes.append(
                dict(
                    oid=20 + index,
                    name=name,
                    kind="I",
                    owner=self.parent["owner"],
                    amname="btree",
                    reloptions=None,
                    indislive=True,
                    indisunique=primary,
                    indisprimary=primary,
                    indisexclusion=False,
                    indisvalid=True,
                    indisready=True,
                    indnkeyatts=len(columns),
                    indnatts=len(columns),
                    columns=columns,
                    options=options,
                    opclasses=[1] * len(columns),
                    collations=[0] * len(columns),
                    expressions=None,
                    predicate=None,
                    parents=[],
                )
            )
        users = set(namespace_permissions(self.manifest.relations)["connectors"])
        users.update(r.owner for r in self.manifest.relations if r.schema == "connectors")
        users.update((self.functions["connectors"].owner, *self.functions["connectors"].executors))
        self.namespace_acl = [
            dict(role=r, verb="USAGE", grantable=False) for r in sorted(users - {"normal_owner"})
        ]
        self.namespace_acl.append(
            dict(role="connector_partition_owner", verb="CREATE", grantable=False)
        )
        if closed:
            self.namespace_acl = [
                dict(role="connector_partition_owner", verb="USAGE", grantable=False)
            ]
        self.source_shape = interfaces._CONNECTORS_PARENT_BODY

    @staticmethod
    def result(rows):
        rows = list(rows)
        return (list(rows[0]) if rows else [], [tuple(r.values()) for r in rows])

    def respond(self, wire, parameters, decoded):
        import hashlib
        import re

        import psycopg2

        from butlers.db_authority import apply, grants, interfaces

        self.statements.append(decoded)
        sql = decoded.strip()
        normalized = re.sub(r"%\(([A-Za-z0-9_]+)\)s", r":\1", wire).replace("%%", "%").strip()

        def result(rows):
            return self.result(rows)

        if "actor.rolsuper AND actor.rolname=session_user" in sql:
            return ["database", "owner", "valid"], [
                ("test_replay", "normal_owner", self.fault != "actor")
            ]
        if sql == "SELECT current_database(), session_user, current_schema(), current_user":
            return ["database", "actor", "schema", "ordinary"], [
                (
                    "test_replay",
                    "maintenance",
                    "foreign" if self.fault == "target" else "general",
                    "normal_owner",
                )
            ]
        if "connectors-replay:function-population" in sql:
            rows = [
                dict(
                    schema="connectors",
                    arguments="timestamp with time zone",
                    owner="normal_owner"
                    if self.normal and not self.closed
                    else "connector_partition_owner",
                )
            ]
            if self.local:
                rows.append(dict(rows[0], schema="general"))
            if self.fault == "foreign_function":
                rows[0]["owner"] = "foreign"
            return result(rows)
        if "connectors-replay:partition-bounds" in sql:
            return ["valid"], [(self.fault != "bound_semantics",)]
        if "connectors-replay:relations" in sql:
            rows = [dict(r) for r in self.relations]
            if self.fault == "owner":
                rows[0]["owner"] = "foreign"
            if self.fault == "rls":
                rows[0]["rls"] = True
            if self.fault == "partition":
                rows[1]["parents"] = []
            if self.fault == "bound":
                rows[1]["bound"] = "DEFAULT"
            if self.fault == "rules":
                rows[0]["rules"] = True
            return result(rows)
        if "connectors-replay:owner" in sql or "connectors-replay:function-metadata" in sql:
            bad = self.fault == ("role" if "replay:owner" in sql else "metadata")
            return ["valid"], [(not bad,)]
        if normalized == interfaces._FUNCTION_CATALOG.strip() or "p.prosrc AS body" in sql:
            schema = "connectors" if '"connectors".' in parameters["signature"] else "general"
            if schema == "general" and not self.local:
                return ["oid", "owner", "definer", "config", "body", "arguments"], []
            definition = self.functions[schema]
            return result(
                [
                    dict(
                        oid=100 if schema == "connectors" else 101,
                        owner=definition.owner,
                        definer=definition.security_definer,
                        config=list(definition.settings),
                        body="counterfeit" if self.fault == "body" else definition.body,
                        arguments="reference_ts timestamp with time zone",
                    )
                ]
            )
        if normalized == interfaces._FUNCTION_ACL.strip():
            definition = self.functions["connectors" if parameters["oid"] == 100 else "general"]
            rows = [
                (role, "EXECUTE", False)
                for role in definition.executors
                if role != definition.owner
            ]
            if self.fault == "function_acl":
                rows.append(("PUBLIC", "EXECUTE", False))
            return ["role", "privilege", "grantable"], rows
        if normalized == apply._RELATION_ACL.strip():
            declaration = next(
                r
                for r in self.manifest.relations
                if (r.schema, r.name) == ("connectors", "filtered_events")
            )
            rows = (
                [
                    dict(role=p.role, verb=v, grantable=False)
                    for p in declaration.permissions
                    for v in p.verbs
                ]
                if parameters["oid"] == 11
                else []
            )
            if self.closed:
                rows = []
            if self.fault == "acl":
                rows.append(dict(role="PUBLIC", verb="SELECT", grantable=False))
            return ["role", "verb", "grantable"], [tuple(r.values()) for r in rows]
        if normalized == apply._COLUMN_ACL.strip():
            return ["name", "role", "verb", "grantable"], (
                [("id", "PUBLIC", "SELECT", False)] if self.fault == "column_acl" else []
            )
        if normalized == apply._NAMESPACE_CATALOG.strip():
            return ["oid", "name", "owner"], [(4, "connectors", "normal_owner")]
        if normalized == apply._NAMESPACE_ACL.strip():
            rows = list(self.namespace_acl)
            if self.fault == "namespace_acl":
                rows.append(dict(role="PUBLIC", verb="USAGE", grantable=False))
            return result(rows)
        if normalized == apply._DEFAULT_ACL.strip():
            return ["owner", "schema", "kind", "role", "verb"], (
                [("normal_owner", "connectors", "r", "PUBLIC", "SELECT")]
                if self.fault == "default_acl"
                else []
            )
        if sql.startswith("CREATE TEMPORARY TABLE"):
            token = re.match(r'CREATE TEMPORARY TABLE "([a-z0-9_]+)"', sql)[1]
            assert not self.scratch
            # Compare full fixed body to the actual original revision source, not a query stub.
            import ast
            from pathlib import Path

            original = Path(__file__).parents[2] / "alembic/versions/core/core_007_connectors.py"
            tree = ast.parse(original.read_text())
            table = next(
                n.value
                for n in ast.walk(tree)
                if isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and "CREATE TABLE IF NOT EXISTS connectors.filtered_events (" in n.value
            )
            body = table.split("(", 1)[1].rsplit(") PARTITION BY", 1)[0]
            actual = sql.split("(", 1)[1].rsplit(") PARTITION BY", 1)[0]
            assert grants.security_expression_tokens(actual) == grants.security_expression_tokens(
                body
            )
            self.scratch.add(token)
            return None, []
        if sql.startswith("CREATE INDEX") and "_butlers_connectors_" in sql:
            import ast
            from pathlib import Path

            ordinal = int(re.search(r'_([0-3])" ON', sql)[1])
            revision, name = (
                ("core_007", "ix_filtered_events_drain"),
                ("core_007", "ix_filtered_events_timeline"),
                ("core_231", "ix_filtered_events_id"),
                ("core_245", "ix_filtered_events_status_received"),
            )[ordinal]
            path = next(
                (Path(__file__).parents[2] / "alembic/versions/core").glob(revision + "_*.py")
            )
            source = next(
                n.value
                for n in ast.walk(ast.parse(path.read_text()))
                if isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and re.search(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+" + name + r"\b", n.value)
            )
            expected_suffix = source.split("connectors.filtered_events", 1)[1]
            actual_suffix = sql[sql.index("(") :]
            assert grants.security_expression_tokens(
                actual_suffix
            ) == grants.security_expression_tokens(expected_suffix)
            return None, []
        if sql.startswith("DROP TABLE pg_temp."):
            self.scratch.clear()
            return None, []
        if "connectors-replay:indexes" in sql:
            scratch = "pg_temp." in parameters["relation"]
            child = "filtered_events_202610" in parameters["relation"]
            rows = [dict(r) for r in self.indexes]
            if scratch:
                token = parameters["relation"].split('"')[1]
                rows = [
                    dict(r, name=token + "_pkey" if i == 0 else token + "_" + str(i - 1))
                    for i, r in enumerate(rows)
                ]
            if child:
                rows = [
                    dict(r, oid=40 + i, name="generated_" + str(i), kind="i", parents=[r["oid"]])
                    for i, r in enumerate(rows)
                ]
            if not scratch:
                if self.fault == "missing_index":
                    rows.pop()
                if self.fault == "index_keys":
                    rows[1]["columns"] = ["id"]
                if self.fault == "index_options":
                    rows[1]["options"] = [0, 0, 0, 0]
                if self.fault == "index_opclass":
                    rows[1]["opclasses"] = [9, 9, 9, 9]
                if self.fault == "index_invalid":
                    rows[1]["indisvalid"] = False
                if self.fault == "index_predicate":
                    rows[1]["predicate"] = "true"
                if self.fault == "index_owner":
                    rows[1]["owner"] = "foreign"
                if self.fault == "leaf" and child:
                    rows[1]["parents"] = []
            return result(rows)
        if "connectors-replay:column-metadata" in sql:
            return ["opaque"], [("source-column-catalog",)]
        if normalized == interfaces._SHAPE_COLUMNS.strip():
            # Source-opaque driver rows: mutation proves comparison, not native type parsing.
            rows = [
                dict(
                    attname="received_at",
                    type="timestamp with time zone",
                    attnotnull=True,
                    default="now()",
                )
            ]
            if self.fault == "columns" and "pg_temp." not in parameters["relation"]:
                rows[0]["type"] = "text"
            return result(rows)
        if normalized == interfaces._SHAPE_CONSTRAINTS.strip():
            return ["contype", "definition"], [("p", "PRIMARY KEY (received_at, id)")]
        if "FROM pg_catalog.pg_policy" in sql or "FROM pg_catalog.pg_trigger" in sql:
            return ["opaque"], [
                ("counterfeit",)
            ] if self.fault == "security" and "pg_temp." not in parameters["relation"] else []
        if sql == "SHOW lock_timeout":
            return ["lock_timeout"], [("0",)]
        if "pg_try_advisory_lock(" in sql:
            return ["locked"], [(True,)]
        if "SELECT child.oid, ns.nspname, child.relname" in sql:
            return ["oid", "schema", "table"], [(12, "connectors", "filtered_events_202610")]
        if "AND idx.indrelid = 12 AND idx.indisvalid" in sql:
            return ["attached"], []
        if "SELECT idx.indisvalid FROM pg_index" in sql:
            return ["valid"], [(False,)]
        if sql.startswith(
            (
                "SET lock_timeout",
                "SELECT pg_advisory_unlock",
                "SELECT set_config",
                "DROP INDEX CONCURRENTLY",
                "ALTER INDEX",
            )
        ):
            return None, []
        if (not self.normal or self.closed) and sql.startswith(
            "CREATE OR REPLACE FUNCTION connectors."
        ):
            raise psycopg2.errors.InsufficientPrivilege("purpose function replay control")
        if sql.startswith("CREATE INDEX") and not self.normal:
            raise psycopg2.errors.InsufficientPrivilege("source-owned index replay control")
        if sql.startswith(("CREATE ", "SELECT connectors.")):
            return None, []
        raise AssertionError(
            "Unmodeled catalog operation " + hashlib.sha256(decoded.encode()).hexdigest()
        )


@pytest.mark.parametrize("revision", ["core_007", "core_135", "core_231", "core_245"])
@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("closed", [False, True], ids=["current", "closed-current-core-history"])
def test_connectors_finalized_replay_preserves_global_catalog_and_new_target(
    revision, local, closed
):
    """Execute the real revision on SQLAlchemy's physical recording boundary."""
    import runpy
    from contextlib import nullcontext
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    root = Path(__file__).parents[2]
    path = next((root / "alembic/versions/core").glob(revision + "_*.py"))
    producer = runpy.run_path(str(path))
    model = _ConnectorsReplayCatalog(local=local, closed=closed)
    statements = model.statements

    with _recording_postgresql_connection(model.respond) as (connection, _):
        connection.info["butlers.source_database"] = "butlers"
        context = SimpleNamespace(
            autocommit_block=nullcontext, opts={"version_table_schema": "general"}
        )
        with (
            patch("alembic.op.get_bind", return_value=connection),
            patch("alembic.op.get_context", return_value=context),
            patch(
                "alembic.op.execute",
                side_effect=lambda statement: connection.execute(text(statement)),
            ),
        ):
            producer["upgrade"]()
    durable = [sql for sql in statements if "_butlers_connectors_" not in sql]
    assert not any(sql.lstrip().startswith("CREATE INDEX") for sql in durable)
    assert not any(
        "CREATE OR REPLACE FUNCTION connectors." in sql or "SELECT connectors." in sql
        for sql in durable
    )
    assert any(
        "CREATE OR REPLACE FUNCTION connectors_filtered_events_ensure_partition(" in sql
        for sql in durable
    ) is (revision in {"core_007", "core_135"} and not local)
    assert not any(
        sql.startswith(("RESET ROLE", "SET ROLE", "GRANT ", "ALTER TABLE")) for sql in statements
    )
    assert not model.scratch


def test_connectors_ordinary_history_keeps_original_producers_and_cic():
    import runpy
    from contextlib import nullcontext
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    for revision in ("core_007", "core_135", "core_231", "core_245"):
        model = _ConnectorsReplayCatalog(normal=True)
        producer = runpy.run_path(
            str(
                next((Path(__file__).parents[2] / "alembic/versions/core").glob(revision + "_*.py"))
            )
        )
        context = SimpleNamespace(
            autocommit_block=nullcontext, opts={"version_table_schema": "general"}
        )
        with _recording_postgresql_connection(model.respond) as (connection, _):
            connection.info["butlers.source_database"] = "butlers"
            with (
                patch("alembic.op.get_bind", return_value=connection),
                patch("alembic.op.get_context", return_value=context),
                patch(
                    "alembic.op.execute",
                    side_effect=lambda statement: connection.execute(text(statement)),
                ),
            ):
                producer["upgrade"]()
        if revision in {"core_231", "core_245"}:
            assert any("CREATE INDEX CONCURRENTLY" in sql for sql in model.statements)
            assert any("DROP INDEX CONCURRENTLY" in sql for sql in model.statements)
            assert any("ATTACH PARTITION" in sql for sql in model.statements)
            assert any("pg_advisory_unlock" in sql for sql in model.statements)
            assert model.statements[-1].startswith("SELECT set_config('lock_timeout'")
        else:
            assert any(
                "CREATE OR REPLACE FUNCTION connectors_filtered_events_ensure_partition(" in sql
                for sql in model.statements
            )
            assert any(
                "SELECT connectors.connectors_filtered_events_ensure_partition" in sql
                for sql in model.statements
            )
        assert not any(
            sql.startswith(("RESET ROLE", "SET ROLE", "GRANT ")) for sql in model.statements
        )


@pytest.mark.parametrize(
    "faults",
    [
        (
            "actor",
            "target",
            "owner",
            "foreign_function",
            "role",
            "metadata",
            "body",
            "rls",
            "partition",
            "bound",
            "bound_semantics",
            "rules",
            "acl",
            "function_acl",
            "column_acl",
            "namespace_acl",
            "default_acl",
            "security",
        ),
        (
            "missing_index",
            "index_keys",
            "index_options",
            "index_opclass",
            "index_invalid",
            "index_predicate",
            "index_owner",
            "leaf",
            "columns",
        ),
    ],
    ids=["identity-authority-partition", "index-column-closure"],
)
def test_connectors_current_reuse_refuses_counterfeit_before_managed_effects(faults):
    import itertools
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    for fault, closed in itertools.product(faults, (False, True)):
        model = _ConnectorsReplayCatalog(local=True, closed=closed, fault=fault)
        producer = runpy.run_path(
            str(next((Path(__file__).parents[2] / "alembic/versions/core").glob("core_007_*.py")))
        )
        with _recording_postgresql_connection(model.respond) as (connection, _):
            connection.info["butlers.source_database"] = "butlers"
            with (
                patch("alembic.op.get_bind", return_value=connection),
                patch(
                    "alembic.op.get_context",
                    return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
                ),
                patch(
                    "alembic.op.execute",
                    side_effect=lambda statement: connection.execute(text(statement)),
                ),
            ):
                with pytest.raises(AuthorityError):
                    producer["upgrade"]()
        assert not model.scratch
        assert not any(
            sql.lstrip().startswith(
                (
                    "CREATE INDEX",
                    "CREATE OR REPLACE FUNCTION",
                    "ALTER ",
                    "GRANT ",
                    "SET ROLE",
                    "RESET ROLE",
                )
            )
            and "_butlers_connectors_" not in sql
            for sql in model.statements
        )


def test_connectors_parser_cleanup_preserves_original_cancellation():
    import asyncio
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    for cleanup_fails in (False, True):
        model = _ConnectorsReplayCatalog(local=True)
        original = asyncio.CancelledError("source parser control")
        calls = []

        def respond(
            wire,
            parameters,
            decoded,
            *,
            original=original,
            calls=calls,
            cleanup_fails=cleanup_fails,
            model=model,
        ):
            if decoded.strip().startswith("DROP TABLE pg_temp."):
                calls.append("close")
                if cleanup_fails:
                    raise RuntimeError("source parser cleanup control")
            return model.respond(wire, parameters, decoded)

        producer = runpy.run_path(
            str(next((Path(__file__).parents[2] / "alembic/versions/core").glob("core_007_*.py")))
        )
        with _recording_postgresql_connection(respond) as (connection, _):
            connection.info["butlers.source_database"] = "butlers"
            execute = connection.execute

            def execute_catalog(statement, *args, original=original, execute=execute, **kwargs):
                if "connectors-replay:column-metadata" in str(statement):
                    raise original
                return execute(statement, *args, **kwargs)

            with (
                patch.object(connection, "execute", side_effect=execute_catalog),
                patch("alembic.op.get_bind", return_value=connection),
                patch(
                    "alembic.op.get_context",
                    return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
                ),
                patch("alembic.op.execute", side_effect=lambda sql: connection.execute(text(sql))),
            ):
                with pytest.raises(asyncio.CancelledError) as caught:
                    producer["upgrade"]()
        assert caught.value is original
        assert calls == ["close"]
        assert bool(getattr(original, "__notes__", ())) is cleanup_fails
        assert not any(sql.startswith("CREATE OR REPLACE FUNCTION") for sql in model.statements)


class _RuntimeProbeReplayCatalog(_ConnectorsReplayCatalog):
    """Real SQLAlchemy transport; aggregate server facts are not native PG proof."""

    def __init__(self, mode, *, namespace_owner="normal_owner", fault=None):
        from dataclasses import replace

        from butlers.db_authority import grants

        self.mode, self.probe_fault, self.public_owner = mode, fault, namespace_owner
        super().__init__(
            normal=mode in {"fresh", "ordinary"},
            local=True,
            closed=mode == "closed",
            fault="function_acl" if fault == "phase_acl" else None,
        )
        self.verifier = next(
            row
            for row in grants.compile_function_manifest(
                self.manifest.profiles, "normal_owner", (("core", "general", None),)
            )
            if row.name == "record_runtime_probe_verification"
        )
        if mode == "closed":
            self.verifier = replace(self.verifier, executors=())
        self.model_declaration = next(
            row
            for row in self.manifest.relations
            if (row.schema, row.name) == ("public", "model_catalog")
        )

    def respond(self, wire, parameters, decoded):
        import re

        import psycopg2

        from butlers.db_authority import apply, grants, interfaces

        sql = decoded.strip()
        normalized = re.sub(r"%\(([A-Za-z0-9_]+)\)s", r":\1", wire).replace("%%", "%").strip()
        fault = self.probe_fault

        def answer(columns, rows):
            self.statements.append(decoded)
            return columns, rows

        if "butlers:core_252:definer_search_paths" in sql:
            return answer(["locked"], [(None,)])
        if "p.proname = ANY(:names)" in normalized:
            definitions = (self.verifier, *self.functions.values())
            return answer(
                ["signature", "schema_name", "function_name", "search_path"],
                [
                    (
                        f"{row.schema}.{row.name}({row.signature})",
                        row.schema,
                        row.name,
                        next(
                            setting.split("=", 1)[1]
                            for setting in row.settings
                            if setting.startswith("search_path=")
                        ),
                    )
                    for row in definitions
                ],
            )
        if sql == "SELECT current_database(), session_user":
            return answer(["database", "actor"], [("test_replay", "maintenance")])
        if "runtime-probe-replay:population" in sql:
            rows = (
                []
                if self.mode == "fresh" or fault == "missing"
                else [
                    (
                        "uuid, boolean, integer, text",
                        "normal_owner"
                        if self.mode == "ordinary" or fault == "normal_owner"
                        else "model_probe_owner",
                    )
                ]
            )
            if fault == "signature":
                rows = [("text", "model_probe_owner")]
            if fault == "foreign_owner":
                rows = [("uuid, boolean, integer, text", "foreign")]
            if fault == "overload":
                rows *= 2
            return answer(["arguments", "owner"], rows)
        if sql.startswith("SELECT pg_catalog.pg_get_userbyid(relowner)"):
            return answer(["owner"], [(self.parent["owner"],)])
        if "runtime-probe-replay:owner" in sql:
            assert "NOT rolinherit" in sql and "pg_auth_members" in sql
            return answer(["valid"], [(fault not in {"role", "membership"},)])
        if "runtime-probe-replay:metadata" in sql:
            # Assert the actual source query contains every modeled predicate;
            # false aggregate responses prove refusal, not PostgreSQL evaluation.
            for predicate in (
                "p.prorettype='boolean'::regtype",
                "p.pronargdefaults=0",
                "p.proargnames=ARRAY",
                "NOT p.proretset",
                "l.lanname='plpgsql'",
                "NOT p.proisstrict",
                "NOT p.proleakproof",
                "p.prosupport=0",
            ):
                assert predicate in sql
            return answer(
                ["valid"], [(fault not in {"return_type", "defaults", "argnames", "language"},)]
            )
        if "runtime-probe-replay:namespace" in sql:
            assert "IN (:owner,'pg_database_owner')" in normalized
            return answer(
                ["valid"],
                [
                    (
                        self.public_owner in {"normal_owner", "pg_database_owner"}
                        and fault not in {"usage", "create"},
                    )
                ],
            )
        if (
            normalized == interfaces._FUNCTION_CATALOG.strip()
            and "record_runtime_probe_verification" in parameters["signature"]
        ):
            return answer(
                ["oid", "owner", "definer", "config", "body", "arguments"],
                [
                    (
                        300,
                        self.verifier.owner,
                        self.verifier.security_definer,
                        ["search_path=foreign"]
                        if fault == "settings"
                        else list(self.verifier.settings),
                        "counterfeit" if fault == "body" else self.verifier.body,
                        "p_catalog_entry_id uuid, p_ok boolean, p_latency_ms integer, p_error text",
                    )
                ],
            )
        if normalized == interfaces._FUNCTION_ACL.strip() and parameters["oid"] == 300:
            rows = [
                (role, "EXECUTE", False)
                for role in self.verifier.executors
                if role != self.verifier.owner
            ]
            if fault == "acl_extra":
                rows.append(("PUBLIC", "EXECUTE", False))
            if fault == "acl_missing":
                rows = []
            return answer(["role", "privilege", "grantable"], rows)
        if normalized == apply._RELATIONS.strip():
            enabled, forced = grants.relation_security_flags(self.model_declaration)
            return answer(
                ["schema", "name", "kind", "owner", "rls", "forced_rls", "oid", "partition"],
                []
                if fault == "missing_model"
                else [
                    (
                        "public",
                        "model_catalog",
                        "r",
                        "foreign" if fault == "model_owner" else "normal_owner",
                        not enabled if fault == "model_rls" else enabled,
                        forced,
                        301,
                        False,
                    )
                ],
            )
        if (
            normalized in {apply._RELATION_ACL.strip(), apply._COLUMN_ACL.strip()}
            and parameters["oid"] == 301
        ):
            columns = normalized == apply._COLUMN_ACL.strip()
            rows = []
            for permission in self.model_declaration.permissions:
                if bool(permission.columns) != columns:
                    continue
                for verb in permission.verbs:
                    if columns:
                        rows.extend(
                            (col, permission.role, verb, False) for col in permission.columns
                        )
                    else:
                        rows.append((permission.role, verb, False))
            if fault == "model_acl" and not columns:
                rows.append(("PUBLIC", "UPDATE", False))
            return answer(
                ["name", "role", "verb", "grantable"] if columns else ["role", "verb", "grantable"],
                rows,
            )
        if (
            sql.startswith(
                ("CREATE ", "DROP TRIGGER", "DROP POLICY", "ALTER TABLE", "REVOKE ", "DO ")
            )
            and "_butlers_connectors_" not in sql
        ):
            if (
                "CREATE OR REPLACE FUNCTION public.record_runtime_probe_verification(" in sql
                and self.mode not in {"fresh", "ordinary"}
            ):
                self.statements.append(decoded)
                raise psycopg2.errors.InsufficientPrivilege("source verifier replay control")
            return answer(None, [])
        return super().respond(wire, parameters, decoded)


def test_runtime_probe_verifier_replay_preserves_source_phase_and_ordinary_receipts(tmp_path):
    import runpy
    from contextlib import nullcontext
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    producer = runpy.run_path(
        str(
            Path(__file__).parents[2]
            / "alembic/versions/core/core_201_runtime_probe_control_receipts.py"
        )
    )
    for mode in ("current", "closed", "fresh", "ordinary", "current-empty"):
        roster = tmp_path / "roster"
        (roster / "general").mkdir(parents=True, exist_ok=True)
        (roster / "general/butler.toml").write_text(
            '[butler]\nname="general"\n[butler.db]\nname="butlers"\nschema="general"\n'
        )
        source = (
            patch.object(roles, "_ROSTER_ROOT", roster)
            if mode == "current-empty"
            else nullcontext()
        )
        with source:
            for namespace_owner in ("normal_owner", "pg_database_owner"):
                model = _RuntimeProbeReplayCatalog(mode, namespace_owner=namespace_owner)
                if mode == "current-empty":
                    assert model.verifier.executors == ()
                with _recording_postgresql_connection(model.respond) as (connection, _):
                    connection.info["butlers.source_database"] = "butlers"
                    with (
                        patch("alembic.op.get_bind", return_value=connection),
                        patch(
                            "alembic.op.get_context",
                            return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
                        ),
                        patch(
                            "alembic.op.execute",
                            side_effect=lambda sql: connection.execute(text(sql)),
                        ),
                    ):
                        producer["upgrade"]()
                        if mode in {"current", "closed", "current-empty"}:
                            for filename in (
                                "core_212_runtime_probe_control_receipts_owner_fence.py",
                                "core_252_pin_definer_search_paths.py",
                            ):
                                survivor = runpy.run_path(
                                    str(
                                        Path(__file__).parents[2]
                                        / "alembic/versions/core"
                                        / filename
                                    )
                                )
                                survivor["upgrade"]()
                if mode in {"current", "closed", "current-empty"}:
                    assert any(
                        "ALTER TABLE public.runtime_probe_control_receipts FORCE ROW LEVEL SECURITY"
                        in sql
                        for sql in model.statements
                    )
                    assert any(
                        "CREATE TRIGGER trg_runtime_probe_control_receipts_no_truncate" in sql
                        for sql in model.statements
                    )
                    assert any("p.proname = ANY" in sql for sql in model.statements)
                    assert not any(
                        sql.lstrip().startswith("ALTER FUNCTION") for sql in model.statements
                    )
                assert any(
                    "CREATE TABLE IF NOT EXISTS public.runtime_probe_control_receipts" in sql
                    for sql in model.statements
                )
                assert any(
                    "CREATE POLICY runtime_probe_control_receipts_switchboard" in sql
                    for sql in model.statements
                )
                assert any(
                    "CREATE OR REPLACE FUNCTION public.runtime_probe_control_receipt_retention"
                    in sql
                    for sql in model.statements
                )
                verifier_effects = [
                    sql
                    for sql in model.statements
                    if "FUNCTION public.record_runtime_probe_verification(" in sql
                    or "FUNCTION public.record_runtime_probe_verification(uuid," in sql
                ]
                assert bool(verifier_effects) is (mode in {"fresh", "ordinary"})
                if mode not in {"fresh", "ordinary"}:
                    assert any("runtime-probe-replay:metadata" in sql for sql in model.statements)
                    assert any("connectors-replay:indexes" in sql for sql in model.statements)
                assert not any(
                    sql.startswith(("RESET ROLE", "SET ROLE")) for sql in model.statements
                )
                assert not model.scratch


def test_runtime_probe_verifier_replay_refuses_counterfeit_before_effects():
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    producer = runpy.run_path(
        str(
            Path(__file__).parents[2]
            / "alembic/versions/core/core_201_runtime_probe_control_receipts.py"
        )
    )
    for mode in ("current", "closed"):
        faults = (
            "missing",
            "normal_owner",
            "foreign_owner",
            "signature",
            "overload",
            "role",
            "membership",
            "return_type",
            "defaults",
            "argnames",
            "language",
            "settings",
            "body",
            "acl_extra",
            "usage",
            "create",
            "missing_model",
            "model_owner",
            "model_rls",
            "model_acl",
            "phase_acl",
        )
        if mode == "current":
            faults += ("acl_missing",)
        for fault in (*faults, "namespace_owner"):
            model = _RuntimeProbeReplayCatalog(
                mode,
                namespace_owner="foreign" if fault == "namespace_owner" else "normal_owner",
                fault=fault,
            )
            with _recording_postgresql_connection(model.respond) as (connection, _):
                connection.info["butlers.source_database"] = "butlers"
                with (
                    patch("alembic.op.get_bind", return_value=connection),
                    patch(
                        "alembic.op.get_context",
                        return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
                    ),
                    patch(
                        "alembic.op.execute", side_effect=lambda sql: connection.execute(text(sql))
                    ),
                ):
                    with pytest.raises(AuthorityError):
                        producer["upgrade"]()
            assert not model.scratch
            assert not any(
                "runtime_probe_control_receipts" in sql
                and sql.lstrip().startswith(("CREATE ", "ALTER ", "REVOKE ", "DROP "))
                for sql in model.statements
            )


class _OwnerLookupReplayCatalog(_ConnectorsReplayCatalog):
    """Actual compiler/driver consumer; aggregate rows are not native catalog proof."""

    def __init__(self, mode, *, fault=None, public_owner="normal_owner"):
        from dataclasses import replace

        from butlers.db_authority import grants, interfaces
        from butlers.migrations import get_all_chains

        super().__init__(normal=mode in {"fresh", "ordinary"}, local=True, closed=mode == "closed")
        self.lookup_mode, self.lookup_fault, self.public_owner = mode, fault, public_owner
        self.lookup_manifest = grants.compile_relation_manifest(
            "test_replay", "normal_owner", "maintenance", source_database="butlers"
        )
        available = set(get_all_chains())
        plans = tuple(
            row
            for row in grants.deployment_function_plans(self.lookup_manifest.profiles)
            if row[0] in available
        )
        self.version_rows = {}
        source_heads = {
            chain: head
            for chain, head, _ in grants._owner_lookup_graph_templates(
                grants._owner_lookup_source_template_identity()
            )
        }
        for chain, schema, _ in plans:
            if mode == "absent" and schema == "relationship":
                continue
            head = "finance_018" if mode == "closed" and chain == "finance" else source_heads[chain]
            self.version_rows.setdefault(schema, []).append(head)
        definitions = grants.compile_function_manifest(
            self.lookup_manifest.profiles, "normal_owner", (("core", "general", None), *plans)
        )
        self.lookup_definitions = {}
        for definition in (
            *definitions,
            *interfaces.final_functions(self.lookup_manifest.profiles, "normal_owner"),
        ):
            if (
                definition.name == "connectors_filtered_events_ensure_partition"
                and definition.schema in {"general", "connectors"}
            ):
                continue
            ordinary = definition in definitions
            component_closed = mode == "closed" or (
                mode == "module_closed" and definition.schema == "relationship"
            )
            if ordinary and component_closed:
                definition = replace(definition, executors=())
            self.lookup_definitions[interfaces._signature(definition)] = definition
        self.function_oids = {
            key: 4000 + index for index, key in enumerate(sorted(self.lookup_definitions))
        }
        self.catalog_relations = {}
        for index, row in enumerate(self.lookup_manifest.relations):
            if (
                (row.schema, row.name) != ("public", "entities")
                and row.schema != "relationship"
                and row.name != "alembic_version"
            ):
                continue
            if mode == "absent" and row.schema == "relationship":
                continue
            if row.name == "alembic_version" and row.schema not in self.version_rows:
                continue
            if fault == "missing_facts" and (row.schema, row.name) == (
                "relationship",
                "entity_facts",
            ):
                continue
            if mode == "closed" or (mode == "module_closed" and row.schema == "relationship"):
                row = replace(row, permissions=())
            self.catalog_relations[3000 + index] = row
        self.namespace_oids = {50: "public", 51: "relationship"}

    def respond(self, wire, parameters, decoded):
        import re

        import psycopg2

        from butlers.db_authority import apply, grants, interfaces

        sql = decoded.strip()
        normalized = re.sub(r"%\(([A-Za-z0-9_]+)\)s", r":\1", wire).replace("%%", "%").strip()
        fault = self.lookup_fault

        def answer(names, rows):
            self.statements.append(decoded)
            return names, rows

        def records(rows):
            names = list(rows[0]) if rows else []
            return answer(names, [tuple(row[name] for name in names) for row in rows])

        if "owner-lookup-replay:population" in sql:
            rows = (
                []
                if self.lookup_mode == "fresh"
                else [
                    (
                        "text, text[]",
                        "normal_owner"
                        if self.lookup_mode == "ordinary"
                        else "identity_lookup_owner",
                    )
                ]
            )
            if fault == "signature":
                rows = [("text", "identity_lookup_owner")]
            if fault == "overload":
                rows *= 2
            if fault == "foreign_owner":
                rows = [("text, text[]", "foreign")]
            return answer(["arguments", "owner"], rows)
        if normalized == apply._RELATIONS.strip():
            rows = []
            for oid, row in self.catalog_relations.items():
                if row.schema not in parameters["schemas"]:
                    continue
                enabled, forced = grants.relation_security_flags(row)
                rows.append(
                    dict(
                        schema=row.schema,
                        name=row.name,
                        kind={
                            "table": "r",
                            "view": "v",
                            "materialized_view": "m",
                            "sequence": "S",
                            "partition": "p",
                        }[row.kind.value],
                        owner="foreign"
                        if fault == "relation_owner" and row.name == "entities"
                        else row.owner,
                        rls=not enabled if fault == "rls" and row.name == "entities" else enabled,
                        forced_rls=forced,
                        oid=oid,
                        partition=False,
                    )
                )
            return records(rows)
        if "owner-lookup-replay:revision-abi" in sql:
            for predicate in (
                "version_num",
                "character varying(32)",
                "condeferrable",
                "indisvalid",
                "indexprs IS NULL",
            ):
                assert predicate in sql
            return answer(["valid"], [(fault != "ledger_abi",)])
        match = re.fullmatch(r'SELECT version_num FROM "([a-z_]+)"\."alembic_version"', sql)
        if match:
            rows = [(head,) for head in self.version_rows[match[1]]]
            if fault == "unknown_revision":
                rows.append(("unlisted_revision",))
            return answer(["version_num"], rows)
        if "owner-lookup-replay:owner" in sql:
            assert (
                "pg_auth_members" in sql
                and "NOT rolinherit" in sql
                and "has_database_privilege" in sql
            )
            return answer(
                ["valid"],
                [(fault not in {"role", "membership", "database_create", "role_ownership"},)],
            )
        if "owner-lookup-replay:metadata" in sql:
            for predicate in (
                "prokind",
                "proisstrict",
                "proleakproof",
                "proparallel",
                "prosupport",
                "pronargdefaults",
                "proargnames",
                "proargmodes",
                "provolatile",
            ):
                assert predicate in sql
            return answer(["valid"], [(fault != "metadata",)])
        if (
            normalized == interfaces._FUNCTION_CATALOG.strip()
            and parameters["signature"] in self.lookup_definitions
        ):
            key = parameters["signature"]
            row = self.lookup_definitions[key]
            return answer(
                ["oid", "owner", "definer", "config", "body", "arguments"],
                [
                    (
                        self.function_oids[key],
                        row.owner,
                        not row.security_definer if fault == "definer" else row.security_definer,
                        ["search_path=foreign"] if fault == "settings" else list(row.settings),
                        "counterfeit" if fault == "body" else row.body,
                        row.signature,
                    )
                ],
            )
        if (
            normalized == interfaces._FUNCTION_ACL.strip()
            and parameters["oid"] in self.function_oids.values()
        ):
            key = next(key for key, oid in self.function_oids.items() if oid == parameters["oid"])
            row = self.lookup_definitions[key]
            rows = [(role, "EXECUTE", False) for role in row.executors if role != row.owner]
            if row.name == "resolve_owner_triple":
                if fault == "executor_missing":
                    rows = rows[:-1]
                if fault == "executor_extra":
                    rows.append(("PUBLIC", "EXECUTE", False))
                if fault == "executor_grantable" and rows:
                    rows[0] = (*rows[0][:2], True)
            return answer(["role", "privilege", "grantable"], rows)
        if (
            normalized in {apply._RELATION_ACL.strip(), apply._COLUMN_ACL.strip()}
            and parameters["oid"] in self.catalog_relations
        ):
            row = self.catalog_relations[parameters["oid"]]
            columns = normalized == apply._COLUMN_ACL.strip()
            rows = []
            for permission in row.permissions:
                if bool(permission.columns) != columns or permission.role == row.owner:
                    continue
                for verb in permission.verbs:
                    rows.extend(
                        (col, permission.role, verb, False) for col in permission.columns
                    ) if columns else rows.append((permission.role, verb, False))
            if row.name in {"entities", "entity_facts"}:
                if fault == "column_missing" and columns:
                    rows = rows[:-1]
                if fault == "column_extra" and columns:
                    rows.append(("id", "PUBLIC", "SELECT", False))
                if fault == "table_extra" and not columns:
                    rows.append(("PUBLIC", "SELECT", False))
                if fault == "grantable" and rows:
                    rows[0] = (*rows[0][:-1], True)
            if fault == "ledger_acl" and row.name == "alembic_version" and not columns:
                rows.append(("PUBLIC", "INSERT", False))
            return answer(
                ["name", "role", "verb", "grantable"] if columns else ["role", "verb", "grantable"],
                rows,
            )
        if normalized == apply._NAMESPACE_CATALOG.strip() and parameters["schemas"] != [
            "connectors"
        ]:
            return answer(
                ["oid", "name", "owner"],
                [
                    (oid, name, self.public_owner if name == "public" else "normal_owner")
                    for oid, name in self.namespace_oids.items()
                    if name in parameters["schemas"]
                ],
            )
        if normalized == apply._NAMESPACE_ACL.strip() and parameters["oid"] in self.namespace_oids:
            schema = self.namespace_oids[parameters["oid"]]
            rows = tuple(row for row in self.lookup_manifest.relations if row.schema == schema)
            closed = self.lookup_mode == "closed" or (
                schema == "relationship" and self.lookup_mode == "module_closed"
            )
            if closed:
                from dataclasses import replace

                rows = tuple(replace(row, permissions=()) for row in rows)
            users = set(apply.namespace_permissions(rows).get(schema, ())) | {
                row.owner for row in rows
            }
            for row in self.lookup_definitions.values():
                if row.schema == schema:
                    users.update((row.owner, *row.executors))
            expected = {
                (role, "USAGE", False)
                for role in users
                if role != (self.public_owner if schema == "public" else "normal_owner")
            }
            if not closed:
                expected.update(
                    (role, "CREATE", False)
                    for role in grants.ordinary_schema_creators(rows).get(schema, ())
                )
            if fault == "namespace_extra":
                expected.add(("PUBLIC", "USAGE", False))
            if fault == "namespace_missing" and expected:
                expected.remove(sorted(expected)[0])
            return answer(["role", "verb", "grantable"], sorted(expected))
        if normalized == apply._DEFAULT_ACL.strip() and parameters["schemas"] != ["connectors"]:
            return answer(
                ["owner", "schema", "kind", "role", "verb"],
                [("normal_owner", "public", "f", "PUBLIC", "EXECUTE")]
                if fault == "default"
                else [],
            )
        if normalized == apply._FUNCTIONS.strip() and parameters["schemas"] == ["relationship"]:
            return answer(
                ["oid", "schema", "name", "arguments", "owner"],
                [
                    (self.function_oids[key], row.schema, row.name, row.signature, row.owner)
                    for key, row in self.lookup_definitions.items()
                    if row.schema == "relationship"
                ],
            )
        if "owner-lookup-replay:security" in sql:
            assert "pg_policy" in sql and "pg_trigger" in sql and "regexp_matches" in sql
            return answer(["valid"], [(fault != "security",)])
        if "owner-lookup-replay:dependency-columns" in sql:
            row = self.catalog_relations[parameters["oid"]]
            columns = (
                (("id", "uuid", True), ("roles", "text[]", True), ("metadata", "jsonb", False))
                if row.name == "entities"
                else (
                    ("id", "uuid", True),
                    ("subject", "uuid", True),
                    ("primary", "boolean", False),
                    ("validity", "text", True),
                    ("object_kind", "text", True),
                    ("predicate", "text", True),
                    ("object", "text", True),
                )
            )
            return answer(
                ["name", "type", "required", "generated", "identity"],
                [
                    (
                        name,
                        "integer" if fault == "dependency_type" and name == "id" else kind,
                        required,
                        "",
                        "",
                    )
                    for name, kind, required in columns
                ],
            )
        if "owner-lookup-replay:dependency-keys" in sql:
            assert "indisvalid" in sql and "confdeltype='c'" in sql and "convalidated" in sql
            return answer(["valid"], [(fault != "dependency_keys",)])
        if "resolve_owner_triple" in sql and (
            sql.startswith("DO ") or sql.startswith("CREATE ") or sql.startswith("GRANT ")
        ):
            self.statements.append(decoded)
            if self.lookup_mode not in {"fresh", "ordinary"}:
                raise psycopg2.errors.InsufficientPrivilege("source replay control")
            return None, []
        return super().respond(wire, parameters, decoded)


def test_owner_lookup_replay_uses_qualified_source_profiles_without_rewriting_functions(tmp_path):
    import runpy
    from contextlib import nullcontext
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    roster = tmp_path / "roster"
    (roster / "general").mkdir(parents=True)
    (roster / "general/butler.toml").write_text(
        '[butler]\nname="general"\n[butler.db]\nname="butlers"\nschema="general"\n'
    )
    cases = [
        (False, mode, "normal_owner")
        for mode in ("fresh", "ordinary", "current", "closed", "absent", "module_closed")
    ] + [(False, "current", "pg_database_owner"), (True, "absent", "normal_owner")]
    for sparse, mode, namespace_owner in cases:
        context = patch.object(roles, "_ROSTER_ROOT", roster) if sparse else nullcontext()
        with context:
            for revision in ("145", "233"):
                producer = runpy.run_path(
                    str(
                        next(
                            (Path(__file__).parents[2] / "alembic/versions/core").glob(
                                f"core_{revision}_*.py"
                            )
                        )
                    )
                )
                model = _OwnerLookupPrefixCatalog(mode, public_owner=namespace_owner)
                with _recording_postgresql_connection(model.respond) as (connection, _):
                    connection.info["butlers.source_database"] = "butlers"
                    with (
                        patch("alembic.op.get_bind", return_value=connection),
                        patch(
                            "alembic.op.get_context",
                            return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
                        ),
                        patch(
                            "alembic.op.execute",
                            side_effect=lambda sql: connection.execute(text(sql)),
                        ),
                    ):
                        producer["upgrade"]()
                effects = [
                    sql
                    for sql in model.statements
                    if "resolve_owner_triple" in sql
                    and sql.lstrip().startswith(("DO ", "CREATE ", "GRANT "))
                ]
                assert bool(effects) is (mode in {"fresh", "ordinary"})
                lookup = next(
                    row
                    for row in model.lookup_definitions.values()
                    if row.name == "resolve_owner_triple"
                )
                assert (lookup.executors == ()) is (mode == "closed")
                if mode != "closed":
                    assert {"connector_writer", "dashboard_api"} <= set(lookup.executors)
                assert not model.scratch


@pytest.mark.parametrize(
    "fault",
    (
        "signature",
        "overload",
        "foreign_owner",
        "role",
        "membership",
        "database_create",
        "role_ownership",
        "metadata",
        "definer",
        "settings",
        "body",
        "executor_missing",
        "executor_extra",
        "executor_grantable",
        "ledger_abi",
        "unknown_revision",
        "ledger_acl",
        "missing_facts",
        "relation_owner",
        "rls",
        "column_missing",
        "column_extra",
        "table_extra",
        "grantable",
        "namespace_extra",
        "namespace_missing",
        "default",
        "security",
        "dependency_type",
        "dependency_keys",
    ),
)
def test_owner_lookup_replay_refuses_incomplete_source_profiles_before_persistent_effects(fault):
    from types import SimpleNamespace

    from butlers.db_authority import interfaces
    from butlers.db_authority.roles import AuthorityError

    model = _OwnerLookupPrefixCatalog("current", fault=fault)
    with _recording_postgresql_connection(model.respond) as (connection, _):
        connection.info["butlers.source_database"] = "butlers"
        with patch(
            "alembic.op.get_context",
            return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
        ):
            with pytest.raises(AuthorityError):
                interfaces.owner_lookup_revision_reuse(connection, "core_233")
    assert not any(
        "resolve_owner_triple" in sql and sql.lstrip().startswith(("DO ", "CREATE ", "GRANT "))
        for sql in model.statements
    )
    assert not model.scratch


class _OwnerLookupPrefixCatalog(_OwnerLookupReplayCatalog):
    """Source-compiled profiles over a recording driver, never physical PG proof.

    The recorder models catalog transport only. Actual compiler, revision graph,
    component validation, function comparator and migration bodies stay unpatched.
    """

    def __init__(
        self,
        mode,
        *,
        fault=None,
        public_owner="normal_owner",
        prefix=37,
        transition=None,
        contacts=False,
        backups=False,
    ):
        from butlers.db_authority import grants, interfaces

        super().__init__(mode, fault=fault, public_owner=public_owner)
        self.component = None
        if mode in {"fresh", "ordinary"}:
            return
        if mode != "absent":
            versions = [
                row for row in self.version_rows["relationship"] if not row.startswith("rel_")
            ]
            if prefix:
                versions.append(f"rel_{prefix:03}")
            self.version_rows["relationship"] = versions
        # Read every actual source chain once for this recording model,
        # rather than re-importing all migration modules for every ledger row.
        # This caches no physical row, ACL, or validation verdict.
        source_revisions = {
            chain: revisions
            for chain, _, revisions in grants._owner_lookup_graph_templates(
                grants._owner_lookup_source_template_identity()
            )
        }
        vector = tuple(
            sorted(
                (chain, schema, head)
                for schema, heads in self.version_rows.items()
                for head in heads
                for chain, revisions in source_revisions.items()
                if head in revisions
            )
        )
        profiles = grants.compile_relationship_component_profiles(
            self.lookup_manifest, installed_plans=vector
        )
        phase = "closed" if mode in {"closed", "module_closed"} else "current"
        wanted = transition or f"{phase}:{prefix}:{phase}"
        allowed = [
            row
            for row in profiles
            if wanted in row.source_states
            and ("contacts" in {obj.name for obj in row.relations})
            is (contacts or (0 < prefix < 3))
            and ("addresses_dedup_bak_rel_030" in {obj.name for obj in row.relations}) is backups
            and ({obj.name for obj in row.relations} & {"reminders", "_reminders_backup"})
            == (
                {"reminders"}
                if 0 < prefix < 7
                else {"_reminders_backup"}
                if prefix < 20 and prefix >= 7
                else set()
            )
        ]
        if mode == "absent":
            allowed = list(profiles)
        assert allowed, (mode, prefix, wanted)
        self.component = allowed[0]
        self.catalog_relations = {
            oid: row for oid, row in self.catalog_relations.items() if row.schema != "relationship"
        }
        for index, row in enumerate((*self.component.relations, *self.component.context_relations)):
            if fault == "missing_facts" and row.name == "entity_facts":
                continue
            self.catalog_relations[9000 + index] = row
        self.lookup_definitions = {
            key: row for key, row in self.lookup_definitions.items() if row.schema != "relationship"
        }
        for row in (
            *self.component.functions,
            *(group[0] for group in self.component.context_functions),
        ):
            self.lookup_definitions[interfaces._signature(row)] = row
        self.function_oids = {
            key: 4000 + index for index, key in enumerate(sorted(self.lookup_definitions))
        }
        self.expected_capabilities = grants.compile_owner_lookup_capabilities(
            self.lookup_manifest, component=self.component, ordinary_closed=mode == "closed"
        )

    def respond(self, wire, parameters, decoded):
        import re

        from butlers.db_authority import apply

        sql = decoded.strip()
        normalized = re.sub(r"%\(([A-Za-z0-9_]+)\)s", r":\1", wire).replace("%%", "%").strip()

        def answer(names, rows):
            self.statements.append(decoded)
            return names, rows

        if sql == (
            "SELECT pg_catalog.pg_get_userbyid(relowner) FROM pg_catalog.pg_class "
            "WHERE oid='connectors.filtered_events'::regclass"
        ):
            return answer(["owner"], [(self.parent["owner"],)])
        if "owner-lookup-replay:complete-purpose-capabilities" in sql:
            assert all(
                name in sql
                for name in (
                    "pg_class",
                    "pg_attribute",
                    "pg_proc",
                    "pg_namespace",
                    "pg_default_acl",
                    "pg_database",
                    "pg_type",
                    "pg_language",
                    "pg_tablespace",
                    "pg_largeobject_metadata",
                    "pg_foreign_data_wrapper",
                    "pg_foreign_server",
                )
            )
            rows = [
                (row.kind, row.schema, row.name, row.signature, row.column, row.verb, False)
                for row in self.expected_capabilities.capabilities
            ]
            if self.lookup_fault == "model_catalog_extra_column":
                rows.append(("column", "public", "model_catalog", "", "id", "SELECT", False))
            elif self.lookup_fault == "other_function_grant":
                rows.append(
                    ("function", "public", "posture_writer_allowed", "text", "", "EXECUTE", False)
                )
            elif self.lookup_fault == "purpose_default_grant":
                rows.append(("default", "public", "r", "", "", "SELECT", False))
            elif self.lookup_fault in {"parameter_set_grant", "parameter_alter_system_grant"}:
                # An omitted producer query cannot observe this catalog. This
                # control therefore FAILS on R4's omission rather than feeding
                # a fictitious parameter row through an unrelated ACL query.
                if "pg_parameter_acl" in sql:
                    verb = "SET" if self.lookup_fault == "parameter_set_grant" else "ALTER SYSTEM"
                    rows.append(("parameter", "", "work_mem", "", "", verb, False))
            elif self.lookup_fault == "uuid_execute_missing":
                rows = [
                    row
                    for row in rows
                    if not (row[0] == "function" and row[2] == "uuid_generate_v7")
                ]
            return answer(
                ["kind", "schema", "name", "signature", "column", "verb", "grantable"], rows
            )
        if "owner-lookup-replay:managed-public-capabilities" in sql:
            import json

            assert "a.grantee=0" in sql and "pg_attribute" in sql
            assert "COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))" in sql
            relation_keys = {
                (row["schema"], row["name"]) for row in json.loads(parameters["relations"])
            }
            function_keys = {
                (row["schema"], row["name"], row["signature"])
                for row in json.loads(parameters["functions"])
            }
            rows = []
            for row in self.catalog_relations.values():
                if (row.schema, row.name) not in relation_keys or row.schema not in parameters[
                    "object_schemas"
                ]:
                    continue
                for permission in row.permissions:
                    if permission.role != "PUBLIC":
                        continue
                    for verb in permission.verbs:
                        if permission.columns:
                            rows.extend(
                                ("column", row.schema, row.name, "", column, verb, False)
                                for column in permission.columns
                            )
                        else:
                            rows.append(("relation", row.schema, row.name, "", "", verb, False))
            for row in self.lookup_definitions.values():
                if (
                    row.identity in function_keys
                    and "PUBLIC" in row.executors
                    and row.schema in parameters["object_schemas"]
                ):
                    rows.append(
                        ("function", row.schema, row.name, row.signature, "", "EXECUTE", False)
                    )
            if self.component is not None and "PUBLIC" in self.component.namespace_users:
                rows.append(("namespace", self.component.schema, "", "", "", "USAGE", False))
            if self.lookup_fault == "public_model_catalog_extra_column":
                assert ("public", "model_catalog") in relation_keys
                rows.append(("column", "public", "model_catalog", "", "id", "SELECT", False))
            if self.lookup_fault == "public_context_extra_column":
                assert any(row.name == "sessions" for row in self.component.context_relations)
                if ("relationship", "sessions") in relation_keys:
                    rows.append(("column", "relationship", "sessions", "", "id", "SELECT", False))
            # Only an actual namespace-wide non-extension catalog predicate
            # can observe these unknown source-external user-defined objects.
            # Exact source names do not expand in response to these rows.
            if self.lookup_fault == "public_unknown_relation" and (
                "c.relkind IN ('r','p','v','m','S','f')" in sql
                and "d.objid=c.oid" in sql
                and "d.deptype='e'" in sql
            ):
                assert "public" in parameters["schemas"]
                rows.append(
                    ("relation", "public", "undeclared_owner_probe", "", "", "SELECT", False)
                )
            if self.lookup_fault == "public_unknown_column" and (
                "c.relkind IN ('r','p','v','m','S','f')" in sql
                and "d.objid=c.oid" in sql
                and "d.deptype='e'" in sql
            ):
                rows.append(
                    ("column", "public", "undeclared_owner_probe", "", "id", "SELECT", False)
                )
            if self.lookup_fault == "public_unknown_namespace" and "n.nspname !~ '^pg_'" in sql:
                rows.append(("namespace", "undeclared_owner_probe", "", "", "", "USAGE", False))
            if self.lookup_fault == "public_unknown_function" and (
                "d.objid=p.oid" in sql and "d.deptype='e'" in sql
            ):
                assert "public" in parameters["schemas"]
                rows.append(
                    ("function", "public", "undeclared_owner_probe", "", "", "EXECUTE", False)
                )
            global_faults = {
                "public_database_create": ("pg_database", "database", "test_replay", "CREATE"),
                "public_tablespace_create": ("pg_tablespace", "tablespace", "pg_default", "CREATE"),
                "public_large_object_read": (
                    "pg_largeobject_metadata",
                    "large_object",
                    "50000",
                    "SELECT",
                ),
                "public_foreign_wrapper_usage": (
                    "pg_foreign_data_wrapper",
                    "foreign_wrapper",
                    "unlisted_wrapper",
                    "USAGE",
                ),
                "public_foreign_server_usage": (
                    "pg_foreign_server",
                    "foreign_server",
                    "unlisted_server",
                    "USAGE",
                ),
            }
            if self.lookup_fault in global_faults:
                catalog, kind, name, verb = global_faults[self.lookup_fault]
                if "FROM pg_catalog." + catalog + " " in sql:
                    if kind == "database":
                        assert "pg_catalog.acldefault('d',d.datdba)" in sql
                    rows.append((kind, "", name, "", "", verb, False))
            return answer(
                ["kind", "schema", "name", "signature", "column", "verb", "grantable"], rows
            )
        if "owner-lookup-replay:prefix-columns" in sql:
            return answer(
                ["name", "type", "required", "generated", "identity"],
                [
                    (name, kind, required, "s" if name == "object_entity_id" else "", "")
                    for name, kind, required in self.component.dependency_columns
                ],
            )
        if normalized == apply._NAMESPACE_ACL.strip() and parameters["oid"] == 51:
            rows = [
                (role, "USAGE", False)
                for role in self.component.namespace_users
                if role != "normal_owner"
            ]
            if self.lookup_fault == "namespace_extra":
                rows.append(("PUBLIC", "USAGE", False))
            if self.lookup_fault == "namespace_missing" and rows:
                rows = rows[:-1]
            return answer(["role", "verb", "grantable"], rows)
        if "butlers:core_252:definer_search_paths" in sql:
            return answer(["locked"], [(None,)])
        if "p.proname = ANY(:names)" in normalized:
            return answer(
                ["signature", "schema_name", "function_name", "search_path"],
                [
                    (
                        key,
                        row.schema,
                        row.name,
                        next(
                            setting.partition("=")[2]
                            for setting in row.settings
                            if setting.startswith("search_path=")
                        ),
                    )
                    for key, row in self.lookup_definitions.items()
                    if row.name == "resolve_owner_triple"
                ],
            )
        return super().respond(wire, parameters, decoded)


@pytest.mark.parametrize("prefix", range(1, 38))
def test_owner_lookup_all_relationship_source_prefixes_use_actual_consumers(prefix):
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    from butlers.migrations import get_chain_revision_path

    actual = get_chain_revision_path("relationship", f"rel_{prefix:03}")
    assert actual == frozenset(f"rel_{n:03}" for n in range(1, prefix + 1))
    for mode in ("current", "closed", "module_closed"):
        # One unchanged catalog is replayed by all three actual consumers;
        # every consumer still issues its own fresh catalog/role/ACL reads.
        model = _OwnerLookupPrefixCatalog(mode, prefix=prefix)
        for revision in ("145", "233", "252"):
            producer = runpy.run_path(
                str(
                    next(
                        (Path(__file__).parents[2] / "alembic/versions/core").glob(
                            f"core_{revision}_*.py"
                        )
                    )
                )
            )
            with _recording_postgresql_connection(model.respond) as (connection, _):
                connection.info["butlers.source_database"] = "butlers"
                with (
                    patch("alembic.op.get_bind", return_value=connection),
                    patch(
                        "alembic.op.get_context",
                        return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
                    ),
                    patch(
                        "alembic.op.execute", side_effect=lambda sql: connection.execute(text(sql))
                    ),
                ):
                    producer["upgrade"]()
            names = {row.name for row in model.component.relations}
            assert ("entity_facts" in names) is (prefix >= 13)
            assert (
                "guard_fact_report_authority" in {row.name for row in model.component.functions}
            ) is (prefix == 37)
            assert (
                "reject_fact_evidence_update" in {row.name for row in model.component.functions}
            ) is (prefix >= 34)
            assert not any(
                "resolve_owner_triple" in sql
                and sql.lstrip().startswith(("DO ", "CREATE ", "GRANT ", "ALTER "))
                for sql in model.statements
            )
            uuid = [
                row
                for row in model.expected_capabilities.capabilities
                if row.kind == "function" and row.name == "uuid_generate_v7"
            ]
            assert bool(uuid) is (mode != "closed")
            assert not model.scratch


def test_owner_lookup_prefix_transitions_keep_actual_recreated_and_retained_rights():
    # These real source producers grant SELECT to existing and future
    # Switchboard tables, including its physical Alembic ledger. Record their
    # actual SQL independently of the source-profile compiler under test.
    import re
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    from butlers.db_authority import apply, interfaces

    root = Path(__file__).parents[2]
    produced_readers = []
    producer_statements = []

    def observe_producer(wire, parameters, decoded):
        producer_statements.append(decoded)
        match = re.search(
            r"""EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA "switchboard" TO "([a-z_]+)"';""",
            decoded,
        )
        if match:
            produced_readers.append(match[1])
        return None, []

    with _recording_postgresql_connection(observe_producer) as (connection, _):
        with patch("alembic.op.execute", side_effect=lambda sql: connection.execute(text(sql))):
            for edge in (77, 143):
                source = runpy.run_path(
                    str(next((root / "alembic/versions/core").glob(f"core_{edge:03}_*.py")))
                )
                source["upgrade"]()
    assert produced_readers == [
        "butler_relationship_rw",
        "butler_messenger_rw",
        "butler_travel_rw",
    ]
    assert len(producer_statements) == 9

    class SourceLedgerCatalog(_OwnerLookupPrefixCatalog):
        def __init__(self, mode):
            super().__init__(mode)
            self.ledger_readers = tuple(produced_readers)
            self.ledger_fault = None
            self.version_reads = 0

        def respond(self, wire, parameters, decoded):
            normalized = re.sub(r"%\(([A-Za-z0-9_]+)\)s", r":\1", wire).replace("%%", "%").strip()
            names, records = super().respond(wire, parameters, decoded)
            records = list(records)
            relation = self.catalog_relations.get(parameters.get("oid"))
            switchboard_ledger = relation is not None and (relation.schema, relation.name) == (
                "general" if self.ledger_fault == "wrong_ledger" else "switchboard",
                "alembic_version",
            )
            if normalized == apply._RELATION_ACL.strip() and switchboard_ledger:
                records.extend((role, "SELECT", False) for role in self.ledger_readers)
                if self.ledger_fault in {"write", "grantable"}:
                    records.append(
                        (
                            produced_readers[0],
                            "INSERT" if self.ledger_fault == "write" else "SELECT",
                            self.ledger_fault == "grantable",
                        )
                    )
            if normalized == apply._COLUMN_ACL.strip() and switchboard_ledger:
                if self.ledger_fault == "column":
                    records.append(("version_num", produced_readers[0], "SELECT", False))
            if normalized == apply._RELATIONS.strip() and self.ledger_fault in {
                "owner",
                "schema",
                "kind",
                "rls",
            }:
                records = [list(row) for row in records]
                for row in records:
                    if (
                        row[names.index("schema")] == "switchboard"
                        and row[names.index("name")] == "alembic_version"
                    ):
                        key, value = {
                            "owner": ("owner", "foreign_owner"),
                            "schema": ("schema", "unknown_ledger"),
                            "kind": ("kind", "v"),
                            "rls": ("rls", True),
                        }[self.ledger_fault]
                        row[names.index(key)] = value
            if decoded.strip().startswith("SELECT version_num FROM "):
                self.version_reads += 1
            return names, records

    # Actual source migration bodies reach the real full reuse consumer. The
    # old reader rejects the legitimate producer SELECTs here, before effects.
    migration = runpy.run_path(str(next((root / "alembic/versions/core").glob("core_145_*.py"))))
    for mode in ("current", "closed"):
        ledger = SourceLedgerCatalog(mode)
        with _recording_postgresql_connection(ledger.respond) as (connection, _):
            connection.info["butlers.source_database"] = "butlers"
            with (
                patch("alembic.op.get_bind", return_value=connection),
                patch(
                    "alembic.op.get_context",
                    return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
                ),
            ):
                migration["upgrade"]()
        assert ledger.version_reads > 0
        assert not ledger.scratch
        assert not any(
            sql.lstrip().startswith(
                ("CREATE OR REPLACE FUNCTION public.resolve_owner_triple", "GRANT ")
            )
            for sql in ledger.statements
        )

    from butlers.db_authority import grants

    ledger = SourceLedgerCatalog("current")
    source_identity = grants._owner_lookup_source_template_identity()
    original_versions = {schema: list(rows) for schema, rows in ledger.version_rows.items()}
    with _recording_postgresql_connection(ledger.respond) as (connection, _):
        for fault in (
            "wrong_role",
            "unknown_role",
            "wrong_ledger",
            "pre077",
            "pre143",
            "unknown_revision",
            "duplicate",
            "owner",
            "schema",
            "kind",
            "rls",
            "abi",
            "column",
            "write",
            "grantable",
        ):
            ledger.version_rows = {schema: list(rows) for schema, rows in original_versions.items()}
            ledger.ledger_readers = tuple(produced_readers)
            ledger.ledger_fault = fault
            ledger.lookup_fault = "ledger_abi" if fault == "abi" else None
            if fault in {"wrong_role", "unknown_role"}:
                ledger.ledger_readers = (
                    "butler_general_rw" if fault == "wrong_role" else "unknown_role",
                )
            if fault in {"pre077", "pre143"}:
                predecessor = "core_076" if fault == "pre077" else "core_142"
                ledger.ledger_readers = (produced_readers[0 if fault == "pre077" else 1],)
                ledger.version_rows = {
                    schema: [predecessor if row.startswith("core_") else row for row in rows]
                    for schema, rows in original_versions.items()
                }
            if fault == "unknown_revision":
                ledger.version_rows["switchboard"].append("unknown_revision")
            if fault == "duplicate":
                ledger.version_rows["switchboard"].append(ledger.version_rows["switchboard"][0])
            ledger.version_reads = 0
            before_effects = tuple(ledger.statements)
            with pytest.raises(AuthorityError):
                interfaces._owner_lookup_revision_vector(
                    connection, ledger.lookup_manifest, "general", source_identity=source_identity
                )
            if fault in {
                "wrong_role",
                "unknown_role",
                "wrong_ledger",
                "owner",
                "schema",
                "kind",
                "rls",
                "abi",
                "column",
                "write",
                "grantable",
            }:
                # Earlier ledgers may already have been read; none of these
                # observations may perform a persistent effect or grant.
                assert not any(
                    sql.lstrip().startswith(("CREATE ", "ALTER ", "GRANT ", "REVOKE "))
                    for sql in ledger.statements[len(before_effects) :]
                )

    from butlers.db_authority import grants
    from butlers.migrations import get_chain_head

    model = _OwnerLookupPrefixCatalog("current", prefix=36, transition="current:35:current")
    rows = {row.name: row for row in model.component.relations}
    current_facts = next(
        row
        for row in model.lookup_manifest.relations
        if row.schema == "relationship" and row.name == "entity_facts"
    )
    # A finalized-current component retains its source-declared rights. The
    # rel033 producer is not replayed by a later rel035 -> rel036 transition.
    assert rows["entity_facts"].permissions == current_facts.permissions
    assert "identity_lookup_owner" in {p.role for p in rows["entity_facts"].permissions}
    assert "connector_writer" not in {p.role for p in rows["entity_facts"].permissions}
    # Crossing rel033 really reintroduces only its exact Connector read grant.
    import runpy
    from pathlib import Path

    producer = runpy.run_path(
        str(next((Path(__file__).parents[2] / "roster/relationship/migrations").glob("033_*.py")))
    )
    emitted = []

    def record(wire, parameters, decoded):
        emitted.append(decoded)
        return None, []

    with _recording_postgresql_connection(record) as (connection, _):
        with patch("alembic.op.execute", side_effect=lambda sql: connection.execute(text(sql))):
            producer["upgrade"]()
            upgrade = tuple(emitted)
            emitted.clear()
            producer["downgrade"]()
    assert len(upgrade) == len(emitted) == 2
    assert (
        sum(
            'GRANT SELECT ON TABLE relationship.entity_facts TO "connector_writer"' in sql
            for sql in upgrade
        )
        == 1
    )
    assert (
        sum(
            'REVOKE SELECT ON TABLE relationship.entity_facts FROM "connector_writer"' in sql
            for sql in emitted
        )
        == 1
    )
    crossed = _OwnerLookupPrefixCatalog("current", prefix=36, transition="current:32:current")
    crossed_facts = next(row for row in crossed.component.relations if row.name == "entity_facts")
    assert (
        tuple(p for p in crossed_facts.permissions if p.role != "connector_writer")
        == current_facts.permissions
    )
    assert tuple(p for p in crossed_facts.permissions if p.role == "connector_writer") == (
        grants.Permission("connector_writer", grants._READ),
    )
    assert {p.role for p in rows["meeting_debriefs"].permissions} == {"butler_relationship_rw"}
    # Real rel033 inverse removes only its fixed Connector rights; current
    # authority does not disappear merely because the installed prefix is old.
    older = _OwnerLookupPrefixCatalog("current", prefix=32, transition="current:32:current")
    assert "connector_writer" not in {
        p.role
        for row in older.component.relations
        if row.name == "entity_facts"
        for p in row.permissions
    }
    assert "connector_writer" not in older.component.namespace_users
    profiles = grants.compile_relationship_component_profiles(
        model.lookup_manifest,
        installed_plans=(
            ("core", "relationship", get_chain_head("core")),
            ("relationship", "relationship", "rel_030"),
        ),
    )
    # Conditional branch alternatives must never make all eight backups fresh-required.
    assert any(
        not any(row.name.endswith("_dedup_bak_rel_030") for row in profile.relations)
        for profile in profiles
    )
    assert any(
        sum(row.name.endswith("_dedup_bak_rel_030") for row in profile.relations) == 8
        for profile in profiles
    )


@pytest.mark.parametrize(
    "fault",
    (
        "model_catalog_extra_column",
        "public_model_catalog_extra_column",
        "public_context_extra_column",
        "public_unknown_relation",
        "public_unknown_column",
        "public_unknown_function",
        "public_unknown_namespace",
        "public_database_create",
        "public_tablespace_create",
        "public_large_object_read",
        "public_foreign_wrapper_usage",
        "public_foreign_server_usage",
        "other_function_grant",
        "purpose_default_grant",
        "uuid_execute_missing",
        "parameter_set_grant",
        "parameter_alter_system_grant",
    ),
)
def test_owner_lookup_complete_fixed_purpose_capability_census_refuses_unrelated_grants(fault):
    from types import SimpleNamespace

    from butlers.db_authority import interfaces
    from butlers.db_authority.roles import AuthorityError

    model = _OwnerLookupPrefixCatalog("current", fault=fault)
    with _recording_postgresql_connection(model.respond) as (connection, _):
        connection.info["butlers.source_database"] = "butlers"
        with patch(
            "alembic.op.get_context",
            return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
        ):
            with pytest.raises(AuthorityError):
                interfaces.owner_lookup_revision_reuse(connection, "core_233")
    assert not any(
        "resolve_owner_triple" in sql and sql.lstrip().startswith(("DO ", "CREATE ", "GRANT "))
        for sql in model.statements
    )
    assert not model.scratch


@pytest.mark.parametrize(
    "schema,name",
    (
        ("relationship", "contacts"),
        ("relationship", "addresses_dedup_bak_rel_030"),
        ("relationship", "contact_labels_dedup_bak_rel_030"),
        ("relationship", "group_members_dedup_bak_rel_030"),
        ("relationship", "important_dates_dedup_bak_rel_030"),
        ("relationship", "life_events_dedup_bak_rel_030"),
        ("relationship", "relationships_dedup_bak_rel_030"),
        ("relationship", "tasks_dedup_bak_rel_030"),
        ("relationship", "contact_entity_map_dedup_bak_rel_030"),
        ("public", "contacts_preferred_channel_dropbak_rel_003"),
    ),
)
def test_relationship_retained_actual_compiler_and_validator_preserve_closed_source_outputs(
    schema, name
):
    from butlers.db_authority import apply, grants, interfaces
    from butlers.db_authority.roles import AuthorityError

    manifest = grants.compile_relation_manifest(
        "test_retained", "normal_owner", "maintenance", source_database="butlers"
    )
    selected = grants.select_migration_manifest(
        manifest, (("core", "relationship", None), ("relationship", "relationship", None))
    )
    for source in (manifest, selected):
        declared = tuple(
            row for row in source.relations if row.schema in {"public", "relationship"}
        )
        observed = []
        for index, row in enumerate(declared):
            if not row.required and (row.schema, row.name) != (schema, name):
                continue
            enabled, forced = grants.relation_security_flags(row)
            observed.append(
                dict(
                    schema=row.schema,
                    name=row.name,
                    kind={
                        "table": "r",
                        "sequence": "S",
                        "view": "v",
                        "materialized_view": "m",
                        "partition": "p",
                    }[row.kind.value],
                    owner=row.owner,
                    rls=enabled,
                    forced_rls=forced,
                    oid=index + 1,
                    partition=False,
                )
            )
        # The source producer observation is independent of whether the old
        # compiler remembered to declare it: this is the pre-correction RED.
        if not any((row["schema"], row["name"]) == (schema, name) for row in observed):
            observed.append(
                dict(
                    schema=schema,
                    name=name,
                    kind="r",
                    owner="normal_owner",
                    rls=False,
                    forced_rls=False,
                    oid=999999,
                    partition=False,
                )
            )
        bound = apply.validate_relations(observed, declared)
        actual, row = next(
            (actual, row) for actual, row in bound if (row.schema, row.name) == (schema, name)
        )
        assert row.owner == "normal_owner" and row.permissions == () and row.required is False
        inverse = tuple(row for row in declared if (row.schema, row.name) != (schema, name))
        with pytest.raises(AuthorityError):
            apply.validate_relations(observed, inverse)
        with pytest.raises(AuthorityError):
            apply.validate_relations(
                [dict(value, owner="foreign") if value is actual else value for value in observed],
                declared,
            )
        with pytest.raises(AuthorityError):
            apply.validate_relations(
                [*observed, dict(actual, name="unlisted_retained_lookalike", oid=999998)], declared
            )
        # Complete absence of optional outputs remains a valid fresh shape.
        apply.validate_relations([value for value in observed if value is not actual], declared)

        def acl(wire, parameters, decoded):
            if "pg_attribute col" in decoded:
                return ["name", "role", "verb", "grantable"], []
            return ["role", "verb", "grantable"], [("dashboard_api", "SELECT", False)]

        with _recording_postgresql_connection(acl) as (connection, _):
            assert not interfaces._owner_lookup_acl_matches(connection, actual, row)
        assert not any(
            (entry.schema, entry.name) == (schema, name) and entry.permissions
            for entry in grants.ordinary_purpose_relations((row,))
        )


def test_relationship_retained_source_edges_preserve_real_retirement_and_no_future_admission():
    from butlers.db_authority import grants
    from butlers.migrations import get_chain_revision_path

    manifest = grants.compile_relation_manifest(
        "test_retained", "normal_owner", "maintenance", source_database="butlers"
    )
    for rel, core, expected in (
        ("rel_002", "core_123", True),
        ("rel_003", "core_123", True),
        ("rel_031", "core_133", True),
        ("rel_031", "core_151", False),
    ):
        rows = grants.relationship_retained_objects(
            "normal_owner",
            relationship_revisions=get_chain_revision_path("relationship", rel),
            core_revisions=get_chain_revision_path("core", core),
        )
        name = (
            "entities_contact_profile_bak_rel_031"
            if rel == "rel_031"
            else "contacts_preferred_channel_dropbak_rel_003"
        )
        assert any(row.name == name for row in rows) is expected
        assert all(
            row.owner == "normal_owner" and row.permissions == () and not row.required
            for row in rows
        )
    before = grants.select_migration_manifest(
        manifest,
        (("core", "relationship", "core_133"), ("relationship", "relationship", "rel_029")),
    )
    after = grants.select_migration_manifest(
        manifest,
        (("core", "relationship", "core_133"), ("relationship", "relationship", "rel_030")),
    )
    assert not any(row.name.endswith("_dedup_bak_rel_030") for row in before.relations)
    assert sum(row.name.endswith("_dedup_bak_rel_030") for row in after.relations) == 8
    core_only = grants.select_migration_manifest(manifest, (("core", "general", None),))
    assert not any(
        row.name in grants._RELATIONSHIP_RETAINED_NAMES
        for row in core_only.relations
        if row.schema == "relationship"
    )


@pytest.mark.parametrize("edge", (15, 17))
def test_owner_lookup_actual_relationship_registry_regrants_preserve_closed_retained_rights(edge):
    """Actual producer SQL is independent of the profile-generated catalog."""
    import runpy
    from dataclasses import replace
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    from butlers.db_authority import grants, interfaces
    from butlers.migrations import get_chain_head

    root = Path(__file__).parents[2]
    producer = runpy.run_path(
        str(next((root / "roster/relationship/migrations").glob(f"{edge:03}_*.py")))
    )
    emitted = []

    def record(wire, parameters, decoded):
        emitted.append(decoded)
        return None, []

    with _recording_postgresql_connection(record) as (connection, _):
        with patch("alembic.op.execute", side_effect=lambda sql: connection.execute(text(sql))):
            producer["upgrade"]()
            upgrade = tuple(emitted)
            emitted.clear()
            producer["downgrade"]()
    grant = 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE relationship.entity_predicate_registry TO "butler_relationship_rw"'
    assert sum(grant in statement for statement in upgrade) == 1
    assert not any("REVOKE" in statement for statement in emitted)
    # Independent source-action observation: only the registry receives this
    # grant; no credential/fact/namespace grant can follow from this SQL.
    assert not any("GRANT" in statement and grant not in statement for statement in upgrade)
    model = _OwnerLookupPrefixCatalog("module_closed", prefix=edge - 1)
    old = {row.name: row for row in model.component.relations}
    assert old["entity_facts"].permissions == ()
    if edge == 17:
        assert old["credentials"].permissions == ()
    changed = replace(
        old["entity_predicate_registry"],
        permissions=(grants.Permission("butler_relationship_rw", grants._CRUD),),
    )
    observed = tuple(
        changed if row.name == changed.name else row for row in model.component.relations
    )
    plans = (
        ("core", "relationship", get_chain_head("core")),
        ("relationship", "relationship", f"rel_{edge:03}"),
    )
    profiles = grants.compile_relationship_component_profiles(
        model.lookup_manifest, installed_plans=plans
    )

    # The catalog cannot observe the compiler's descriptive producer label.
    # All actual identity/owner/ACL/RLS/requiredness/security fields stay exact.
    def observable(rows):
        return tuple(replace(row, producer="") for row in rows)

    assert any(
        observable(profile.relations) == observable(observed)
        and profile.namespace_users == model.component.namespace_users
        for profile in profiles
    )
    # The no-op/no-revoke inverse must retain the same independent observation.
    inverse_profiles = grants.compile_relationship_component_profiles(
        model.lookup_manifest,
        installed_plans=(
            ("core", "relationship", get_chain_head("core")),
            ("relationship", "relationship", f"rel_{edge - 1:03}"),
        ),
    )
    assert any(
        observable(profile.relations) == observable(observed) for profile in inverse_profiles
    )
    model.component = replace(model.component, revision=f"rel_{edge:03}", relations=observed)
    model.catalog_relations.update(
        {
            oid: changed
            for oid, row in model.catalog_relations.items()
            if row.schema == "relationship" and row.name == changed.name
        }
    )
    model.version_rows["relationship"] = [
        f"rel_{edge:03}" if value.startswith("rel_") else value
        for value in model.version_rows["relationship"]
    ]
    with _recording_postgresql_connection(model.respond) as (connection, _):
        connection.info["butlers.source_database"] = "butlers"
        with patch(
            "alembic.op.get_context",
            return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
        ):
            assert interfaces.owner_lookup_revision_reuse(connection, "core_233") is True
    assert not model.scratch


@pytest.mark.parametrize("calendar", ("absent", "junction_absent", "present"))
def test_owner_lookup_actual_reminder_guard_and_inverse_populations(calendar):
    import runpy
    from pathlib import Path

    from sqlalchemy import text

    from butlers.db_authority import grants
    from butlers.migrations import get_chain_head

    root = Path(__file__).parents[2] / "roster/relationship/migrations"
    rel007 = runpy.run_path(str(next(root.glob("007_*.py"))))
    rel020 = runpy.run_path(str(next(root.glob("020_*.py"))))
    rel001 = runpy.run_path(str(next(root.glob("001_*.py"))))
    tables = {"reminders"}
    statements = []

    def record(wire, parameters, decoded):
        sql = decoded.strip()
        statements.append(sql)
        if sql.startswith("SELECT to_regclass("):
            name = sql.partition("('")[2].partition("')")[0]
            exists = (
                name in tables
                or name == "calendar_events"
                and calendar != "absent"
                or name == "calendar_event_entities"
                and calendar == "present"
            )
            return ["oid"], [(1 if exists else None,)]
        if sql == "ALTER TABLE reminders RENAME TO _reminders_backup":
            tables.remove("reminders")
            tables.add("_reminders_backup")
        elif sql == "ALTER TABLE _reminders_backup RENAME TO reminders":
            tables.remove("_reminders_backup")
            tables.add("reminders")
        elif sql == "DROP TABLE IF EXISTS _reminders_backup":
            tables.discard("_reminders_backup")
        elif sql.startswith("CREATE TABLE IF NOT EXISTS _reminders_backup"):
            tables.add("_reminders_backup")
        elif sql in {
            "DROP TABLE IF EXISTS " + name
            for name in (
                "activity_feed",
                "quick_facts",
                "contact_labels",
                "labels",
                "group_members",
                "groups",
                "loans",
                "gifts",
                "reminders",
                "interactions",
                "notes",
                "important_dates",
                "relationships",
                "contacts",
            )
        }:
            tables.discard(sql.rpartition(" ")[2])
        elif sql.startswith("SELECT id FROM calendar_sources WHERE source_key"):
            return ["id"], [(None,)]
        else:
            raise AssertionError("Unmodeled reminder producer operation")
        return None, []

    manifest = grants.compile_relation_manifest(
        "butlers", "normal_owner", "maintenance", source_database="butlers"
    )

    def matches(prefix):
        profiles = grants.compile_relationship_component_profiles(
            manifest,
            installed_plans=(
                ("core", "relationship", get_chain_head("core")),
                *((("relationship", "relationship", f"rel_{prefix:03}"),) if prefix else ()),
            ),
        )
        return any(
            {
                row.name
                for row in profile.relations
                if row.name in {"reminders", "_reminders_backup"}
            }
            == tables
            for profile in profiles
        )

    with _recording_postgresql_connection(record) as (connection, _):
        with (
            patch("alembic.op.get_bind", return_value=connection),
            patch("alembic.op.execute", side_effect=lambda sql: connection.execute(text(sql))),
        ):
            rel007["upgrade"]()
            assert tables == ({"_reminders_backup"} if calendar == "present" else {"reminders"})
            assert matches(7)
            rel020["upgrade"]()
            assert tables == (set() if calendar == "present" else {"reminders"})
            assert matches(20)
            rel020["downgrade"]()
            assert "_reminders_backup" in tables
            assert matches(19)
            rel007["downgrade"]()
            assert "reminders" in tables
            assert matches(6)
            before_base = len(statements)
            rel001["downgrade"]()
            assert len(statements) - before_base == 14
            assert tables == (set() if calendar == "present" else {"_reminders_backup"})
            assert matches(0)
    if calendar != "present":
        assert not any(
            sql == "ALTER TABLE reminders RENAME TO _reminders_backup" for sql in statements
        )
    retained = grants.relationship_retained_objects(
        "normal_owner",
        relationship_revisions=frozenset(f"rel_{n:03}" for n in range(1, 38)),
        core_revisions=frozenset(),
    )
    row = next(row for row in retained if row.name == "reminders")
    assert row.permissions == () and row.owner == "normal_owner" and row.required is False


@pytest.mark.parametrize(
    "edge,name", ((34, "reject_fact_evidence_update"), (37, "guard_fact_report_authority"))
)
def test_owner_lookup_managed_public_census_preserves_real_function_producers(edge, name):
    """Actual producer actions establish PUBLIC-default source provenance."""
    import re
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    from butlers.db_authority import grants, interfaces
    from butlers.db_authority.roles import runtime_profiles

    producer = runpy.run_path(
        str(
            next(
                (Path(__file__).parents[2] / "roster/relationship/migrations").glob(
                    f"{edge:03}_*.py"
                )
            )
        )
    )
    statements = []

    def record(wire, parameters, decoded):
        statements.append(decoded)
        return None, []

    with _recording_postgresql_connection(record) as (connection, _):
        with patch("alembic.op.execute", side_effect=lambda sql: connection.execute(text(sql))):
            producer["upgrade"]()
    create = [
        sql
        for sql in statements
        if f"CREATE OR REPLACE FUNCTION relationship.{name}()" in sql
        or f"CREATE FUNCTION relationship.{name}()" in sql
    ]
    assert len(create) == 1
    assert (
        sum(
            create[0].count(prefix + f"relationship.{name}()")
            for prefix in ("CREATE OR REPLACE FUNCTION ", "CREATE FUNCTION ")
        )
        == 1
    )
    assert not any("REVOKE" in sql for sql in statements)
    # rel034 declares LANGUAGE after its dollar body. Reconstructed current
    # DDL must preserve that actual producer fact as well as the exact body;
    # the historical PUBLIC-default assertions below remain independent.
    current = next(
        row
        for row in grants.compile_function_manifest(
            runtime_profiles("butlers"),
            "normal_owner",
            (("core", "relationship", None), ("relationship", "relationship", None)),
        )
        if row.schema == "relationship" and row.name == name
    )
    function_sql = create[0].split(f"FUNCTION relationship.{name}()", 1)[1]
    source_language = re.search(r"\bLANGUAGE\s+([a-z]+)", function_sql, re.IGNORECASE)
    assert source_language is not None
    compiled_language = re.search(r"\bLANGUAGE\s+([a-z]+)", current.sql, re.IGNORECASE)
    assert compiled_language is not None
    assert compiled_language[1].lower() == source_language[1].lower() == "plpgsql"
    source_body = re.search(r"\bAS\s+(\$[A-Za-z_0-9]*\$)(.*?)\1", function_sql, re.I | re.S)
    assert source_body is not None and current.body.strip() == source_body[2].strip()
    model = _OwnerLookupPrefixCatalog("closed", prefix=edge, transition=f"closed:{edge - 1}:closed")
    row = next(row for row in model.component.functions if row.name == name)
    assert row.executors == ("PUBLIC",) and row.settings == ()
    public = [
        row
        for row in model.expected_capabilities.public_capabilities
        if row.kind == "function" and row.name == name
    ]
    assert len(public) == 1 and public[0].verb == "EXECUTE"
    with _recording_postgresql_connection(model.respond) as (connection, _):
        connection.info["butlers.source_database"] = "butlers"
        with patch(
            "alembic.op.get_context",
            return_value=SimpleNamespace(opts={"version_table_schema": "general"}),
        ):
            assert interfaces.owner_lookup_revision_reuse(connection, "core_233") is True
    assert any("owner-lookup-replay:managed-public-capabilities" in sql for sql in model.statements)
    assert not model.scratch


def test_owner_lookup_public_census_preserves_actual_ordinary_target_wrapper():
    import runpy
    from dataclasses import replace
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import text

    from butlers.db_authority import interfaces

    producer = runpy.run_path(
        str(next((Path(__file__).parents[2] / "alembic/versions/core").glob("core_135_*.py")))
    )
    model = _OwnerLookupPrefixCatalog("current")
    model.local = False
    context = SimpleNamespace(opts={"version_table_schema": "general"})
    with _recording_postgresql_connection(model.respond) as (connection, _):
        connection.info["butlers.source_database"] = "butlers"
        with (
            patch("alembic.op.get_bind", return_value=connection),
            patch("alembic.op.get_context", return_value=context),
            patch("alembic.op.execute", side_effect=lambda sql: connection.execute(text(sql))),
        ):
            producer["upgrade"]()
    created = [
        sql
        for sql in model.statements
        if sql.lstrip().startswith(
            "CREATE OR REPLACE FUNCTION connectors_filtered_events_ensure_partition("
        )
    ]
    assert len(created) == 1
    assert producer["_FUNCTION_BODY"] in created[0]
    assert "SET search_path = connectors, public, pg_temp" in created[0]
    assert not any(
        sql.lstrip().startswith("CREATE OR REPLACE FUNCTION connectors.")
        for sql in model.statements
    )
    # Catalog transport follows the actual ordinary session and observed
    # producer body/default ACL. No membership, serving grant or owner switch.
    ordinary = replace(
        model.functions["general"],
        owner="normal_owner",
        executors=("PUBLIC",),
        settings=("search_path=connectors, public, pg_temp",),
    )
    model.functions["general"] = ordinary
    model.local = True
    model.lookup_definitions[interfaces._signature(ordinary)] = ordinary
    model.function_oids[interfaces._signature(ordinary)] = max(model.function_oids.values()) + 1
    model.version_rows["general"] = [
        "core_144" if value.startswith("core_") else value
        for value in model.version_rows["general"]
    ]

    def observe(wire, parameters, decoded):
        if "connectors-replay:function-population" in decoded:
            model.statements.append(decoded)
            return ["schema", "arguments", "owner"], [
                ("connectors", "timestamp with time zone", "connector_partition_owner"),
                ("general", "timestamp with time zone", "normal_owner"),
            ]
        return model.respond(wire, parameters, decoded)

    with _recording_postgresql_connection(observe) as (connection, _):
        connection.info["butlers.source_database"] = "butlers"
        with patch("alembic.op.get_context", return_value=context):
            assert interfaces.owner_lookup_revision_reuse(connection, "core_145") is True
    assert "general" not in model.expected_capabilities.public_object_schemas
    assert ordinary.executors == ("PUBLIC",) and not model.scratch


async def test_catalog_security_predicate_groups_are_closed():
    """Check actual emitted groups; lexical balance is not PostgreSQL grammar proof."""
    import re

    from butlers.db_authority import catalog

    connection = AsyncMock()
    connection.fetchval.return_value = True
    await catalog.assert_object_catalog(connection, "butlers")
    statement, encoded = connection.fetchval.call_args.args
    assert statement == catalog._CATALOG_OBJECT_SQL and encoded
    # Ignore only complete SQL literals, identifiers, comments and dollar data.
    # In particular, regexp pattern parentheses are data, not SQL grouping.
    token = re.compile(
        r"\$(?P<tag>[A-Za-z_][A-Za-z0-9_]*|)\$.*?\$(?P=tag)\$"
        r"|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*.*?\*/",
        re.DOTALL,
    )
    code = token.sub(" ", statement)
    depth = 0
    for character in code:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            assert depth >= 0
    assert depth == 0
    # All three actual predicate consumers remain present in the sent query.
    assert "p.polqual" in statement and "p.polwithcheck" in statement
    assert "pg_catalog.pg_get_triggerdef(t.oid,false)" in statement


def test_switchboard_decision_source_relations_are_declared():
    """Real revision actions and real compiler/validators meet at the driver seam."""
    import re
    import runpy
    from pathlib import Path

    from butlers.db_authority import apply, grants
    from butlers.migrations import get_chain_revision_path

    root = Path(__file__).parents[2]
    emitted = []
    for relative in (
        "roster/switchboard/migrations/042_decision_desk_intents.py",
        "roster/switchboard/migrations/046_decision_intent_supersession.py",
    ):
        producer = runpy.run_path(str(root / relative))
        with patch("alembic.op.execute", side_effect=emitted.append):
            producer["upgrade"]()
    produced = {
        match[1]
        for statement in emitted
        if (match := re.search(r"CREATE TABLE IF NOT EXISTS (decision_\w+)\s*\(", statement))
    }
    assert produced == {"decision_intents", "decision_prompts"}
    assert any("ALTER TABLE decision_intents ADD CONSTRAINT" in sql for sql in emitted)
    assert "sw_042" not in get_chain_revision_path("switchboard", "sw_041")
    assert "sw_042" in get_chain_revision_path("switchboard", "sw_042")
    full = grants.compile_relation_manifest(
        "test_decisions", "normal_owner", "maintenance", source_database="butlers"
    )
    actual = tuple(
        dict(
            schema="switchboard",
            name=name,
            kind="r",
            owner="normal_owner",
            rls=False,
            forced_rls=False,
            oid=700000 + index,
            partition=False,
        )
        for index, name in enumerate(sorted(produced))
    )
    # The producer observations do not depend on the compiler remembering them.
    apply.bind_partition_relations(actual, full.relations, ())
    declared = tuple(
        row for row in full.relations if row.schema == "switchboard" and row.name in produced
    )
    assert len(declared) == 2
    for row in declared:
        assert row.owner == "normal_owner" and row.kind == grants.ObjectKind.TABLE
        assert row.required is True
        assert row.permissions == (
            grants.Permission("butler_switchboard_rw", ("SELECT", "INSERT", "UPDATE", "DELETE")),
            grants.Permission("dashboard_api", ("SELECT", "INSERT", "UPDATE", "DELETE")),
        )
    assert apply.validate_relations(actual, declared) == tuple(zip(actual, declared, strict=True))
    current = grants.select_migration_manifest(full, (("switchboard", "switchboard", None),))
    assert all(
        row.required and row.permissions
        for row in current.relations
        if row.schema == "switchboard" and row.name in produced
    )
    # Before the actual edge there are no producer tables. Historical source
    # declarations confer neither required presence nor serving permission.
    previous = grants.select_migration_manifest(full, (("switchboard", "switchboard", "sw_041"),))
    historical = tuple(
        row for row in previous.relations if row.schema == "switchboard" and row.name in produced
    )
    assert all(not row.required and not row.permissions for row in historical)
    assert apply.validate_relations((), historical, administrative_history=True) == ()
    for fault in (dict(name="decision_lookalike"), dict(owner="foreign"), dict(kind="v")):
        with pytest.raises(AuthorityError):
            apply.validate_relations((actual[0] | fault, actual[1]), declared)
    with pytest.raises(AuthorityError):
        apply.validate_relations(actual[:1], declared)
    for observed, row in zip(actual, declared, strict=True):
        entries = [
            (role, verb, False)
            for role in ("butler_switchboard_rw", "dashboard_api")
            for verb in ("SELECT", "INSERT", "UPDATE", "DELETE")
        ]
        state = {"fault": None}

        def acl_transport(
            statement, parameters, decoded, *, observed=observed, entries=entries, state=state
        ):
            assert parameters == {"oid": observed["oid"]}
            if "FROM pg_catalog.pg_attribute col" in statement:
                columns = [("id", "PUBLIC", "SELECT", False)] if state["fault"] == "column" else []
                return ("name", "role", "verb", "grantable"), columns
            assert "FROM pg_catalog.pg_class c" in statement
            found = list(entries)
            if state["fault"] == "extra":
                found.append(("PUBLIC", "SELECT", False))
            elif state["fault"] == "grantable":
                found[0] = (*found[0][:2], True)
            elif state["fault"] == "missing":
                found.pop()
            return ("role", "verb", "grantable"), found

        with _recording_postgresql_connection(acl_transport) as (connection, calls):
            apply.verify_relation(connection, observed, row)
            for fault in ("extra", "grantable", "missing", "column"):
                state["fault"] = fault
                with pytest.raises(AuthorityError):
                    apply.verify_relation(connection, observed, row)
            assert all(statement.lstrip().startswith("SELECT") for statement, _, _ in calls)


def test_relationship_retained_outputs_follow_admitted_producer_target():
    """Unqualified producer outputs follow each selected source target, with no ACL."""
    import re
    import runpy
    from pathlib import Path

    from butlers.db_authority import apply, grants

    root = Path(__file__).parents[2]
    emitted = []
    for relative in (
        "roster/relationship/migrations/001_relationship_tables.py",
        "roster/relationship/migrations/030_dedup_contacts_drop_contacts_fks.py",
    ):
        producer = runpy.run_path(str(root / relative))
        with patch("alembic.op.execute", side_effect=emitted.append):
            producer["upgrade"]()
    # This records real producer statements, including conditional DO data;
    # it does not claim those branches executed in PostgreSQL.
    names = {
        match
        for statement in emitted
        for match in re.findall(r"CREATE TABLE\s+(\w+_dedup_bak_rel_030)\s+AS", statement)
    }
    assert names == {
        "addresses_dedup_bak_rel_030",
        "contact_labels_dedup_bak_rel_030",
        "group_members_dedup_bak_rel_030",
        "important_dates_dedup_bak_rel_030",
        "life_events_dedup_bak_rel_030",
        "relationships_dedup_bak_rel_030",
        "tasks_dedup_bak_rel_030",
        "contact_entity_map_dedup_bak_rel_030",
    }
    local = {
        match
        for statement in emitted
        for match in re.findall(
            r"CREATE TABLE IF NOT EXISTS\s+(contacts|reminders)\s*\(", statement
        )
    }
    assert local == {"contacts", "reminders"}
    full = grants.compile_relation_manifest(
        "test_retained_targets", "normal_owner", "maintenance", source_database="butlers"
    )
    for schema in (None, "relationship", "isolated_admin"):
        target = schema or "public"
        for revision in (None, "rel_030"):
            selected = grants.select_migration_manifest(full, (("relationship", schema, revision),))
            actual = tuple(
                dict(
                    schema=target,
                    name=name,
                    kind="r",
                    owner="normal_owner",
                    rls=False,
                    forced_rls=False,
                    oid=710000 + index,
                    partition=False,
                )
                for index, name in enumerate(sorted(names | local))
            )
            apply.bind_partition_relations(actual, selected.relations, ())
            retained = tuple(
                row
                for row in selected.relations
                if row.schema == target and row.name in names | local
            )
            assert len(retained) == 10
            assert all(
                not row.required and not row.permissions and row.owner == "normal_owner"
                for row in retained
            )
            assert len(apply.validate_relations(actual, retained)) == 10
            assert apply.validate_relations((), retained) == ()
            assert any(
                row.schema == target
                and row.name == "important_dates"
                and row.required == (revision is None)
                for row in selected.relations
            )
            with pytest.raises(AuthorityError):
                apply.validate_relations((actual[0] | {"owner": "foreign"}, *actual[1:]), retained)
            with pytest.raises(AuthorityError):
                apply.bind_partition_relations(
                    (actual[0] | {"name": "unknown_retained_lookalike"},), selected.relations, ()
                )
            row = next(row for row in retained if row.name in names)
            observed = next(value for value in actual if value["name"] == row.name)
            state = {"extra": False}

            def acl_transport(statement, parameters, decoded, *, state=state, observed=observed):
                assert parameters == {"oid": observed["oid"]}
                if "FROM pg_catalog.pg_attribute col" in statement:
                    return ("name", "role", "verb", "grantable"), []
                assert "FROM pg_catalog.pg_class c" in statement
                return ("role", "verb", "grantable"), (
                    [("PUBLIC", "SELECT", False)] if state["extra"] else []
                )

            with _recording_postgresql_connection(acl_transport) as (connection, _):
                apply.verify_relation(connection, observed, row)
                state["extra"] = True
                with pytest.raises(AuthorityError):
                    apply.verify_relation(connection, observed, row)
        previous = grants.select_migration_manifest(full, (("relationship", schema, "rel_029"),))
        assert not any(row.schema == target and row.name in names for row in previous.relations)
    mixed = grants.select_migration_manifest(
        full, (("relationship", None, "rel_029"), ("relationship", "relationship", "rel_030"))
    )
    assert not any(row.schema == "public" and row.name in names for row in mixed.relations)
    assert {
        row.name for row in mixed.relations if row.schema == "relationship" and row.name in names
    } == names
    core_only = grants.select_migration_manifest(full, (("core", None, None),))
    assert not any(row.name in names for row in core_only.relations)
    assert any(
        row.name == "contacts_preferred_channel_dropbak_rel_003"
        and row.schema == "public"
        and not row.required
        and not row.permissions
        for row in mixed.relations
    )
    bounded = grants.select_migration_manifest(
        full, (("core", None, "core_133"), ("relationship", "isolated_admin", "rel_031"))
    )
    assert any(
        row.name == "entities_contact_profile_bak_rel_031"
        and row.schema == "public"
        and not row.required
        and not row.permissions
        for row in bounded.relations
    )


def test_selected_user_context_owner_follows_actual_dnd_producer_edge():
    """Admit the ordinary predecessor only before the selected fixed DND edge."""
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    from butlers.db_authority import apply, grants

    root = Path(__file__).parents[2]
    move = runpy.run_path(
        str(root / "alembic/versions/core/core_046_migrate_user_context_to_public.py")
    )
    emitted = []

    def predecessor_transport(statement, parameters, decoded):
        assert "FROM pg_catalog.pg_class c" in statement
        if "n.nspname = 'shared'" in statement:
            return ("present",), [(1,)]
        assert "n.nspname = 'public'" in statement
        return ("present",), []

    with _recording_postgresql_connection(predecessor_transport) as (connection, _):
        with patch("alembic.op.get_bind", return_value=connection):
            with patch("alembic.op.execute", side_effect=emitted.append):
                move["upgrade"]()
    assert emitted == [
        "ALTER TABLE shared.user_context SET SCHEMA public",
        "DROP SCHEMA IF EXISTS shared",
    ]
    assert not any("OWNER TO" in statement for statement in emitted)
    dnd = runpy.run_path(
        str(root / "alembic/versions/core/core_197_canonical_dnd_generation_guard.py")
    )
    dispatched = []
    held = SimpleNamespace(source="test-held-driver")
    with patch.dict(
        dnd["upgrade"].__globals__,
        {"install_revision_group": lambda conn, group: dispatched.append((conn, group))},
    ):
        with patch("alembic.op.get_bind", return_value=held):
            dnd["upgrade"]()
    assert dispatched == [(held, "dnd")]
    full = grants.compile_relation_manifest(
        "test_context_edge", "normal_owner", "maintenance", source_database="butlers"
    )
    for target in (None, "relationship"):
        for revision in ("core_076", "core_170", "core_196", "core_197", None):
            selected = grants.select_migration_manifest(full, (("core", target, revision),))
            row = next(
                row
                for row in selected.relations
                if (row.schema, row.name) == ("public", "user_context")
            )
            after_edge = revision in {"core_197", None}
            actual = dict(
                schema="public",
                name="user_context",
                kind="r",
                owner="dnd_generation_owner" if after_edge else "normal_owner",
                rls=after_edge,
                forced_rls=after_edge,
                oid=720000,
            )
            # Baseline reaches the actual validator with a source-produced normal
            # predecessor; no import or mocked compiler failure is the RED.
            assert apply.validate_relations(
                (actual,), (row,), administrative_history=revision is not None
            ) == ((actual, row),)
            assert row.owner == ("dnd_generation_owner" if after_edge else "normal_owner")
            assert bool(row.permissions) == (revision is None)
            assert all(
                row.owner == original.owner
                for row in selected.relations
                for original in full.relations
                if row.identity == original.identity
                and row.name != "user_context"
                and (row.schema, row.name) not in grants.ORDINARY_PARTITION_OWNERS
            )
            for owner in ("foreign", "normal_owner" if after_edge else "dnd_generation_owner"):
                with pytest.raises(AuthorityError):
                    apply.validate_relations(
                        (actual | {"owner": owner},),
                        (row,),
                        administrative_history=revision is not None,
                    )
            with pytest.raises(AuthorityError):
                apply.validate_relations(
                    (actual | {"name": "user_context_decoy"},),
                    (row,),
                    administrative_history=revision is not None,
                )
            if revision is not None:
                state = {"fault": None}

                def acl_transport(statement, parameters, decoded, *, state=state):
                    assert parameters == {"oid": 720000}
                    if "FROM pg_catalog.pg_attribute col" in statement:
                        return ("name", "role", "verb", "grantable"), (
                            [("id", "PUBLIC", "SELECT", False)]
                            if state["fault"] == "column"
                            else []
                        )
                    assert "FROM pg_catalog.pg_class c" in statement
                    return ("role", "verb", "grantable"), (
                        [("PUBLIC", "SELECT", False)] if state["fault"] == "table" else []
                    )

                with _recording_postgresql_connection(acl_transport) as (connection, _):
                    apply.verify_relation(connection, actual, row)
                    for fault in ("column", "table"):
                        state["fault"] = fault
                        with pytest.raises(AuthorityError):
                            apply.verify_relation(connection, actual, row)
    for plans in (
        (("core", "relationship", "core_076"), ("core", "switchboard", "core_197")),
        (("core", "switchboard", "core_197"), ("core", "relationship", "core_076")),
        (("core", None, None), ("core", "relationship", "core_170")),
        (("core", "relationship", "core_170"), ("core", None, None)),
    ):
        selected = grants.select_migration_manifest(full, plans)
        row = next(
            row
            for row in selected.relations
            if (row.schema, row.name) == ("public", "user_context")
        )
        assert row.owner == "dnd_generation_owner" and not row.permissions
    no_core = grants.select_migration_manifest(full, (("relationship", None, "rel_037"),))
    row = next(
        row for row in no_core.relations if (row.schema, row.name) == ("public", "user_context")
    )
    assert row.owner == "dnd_generation_owner" and not row.permissions


def test_historical_priority_audit_compiler_follows_actual_fixed_producers():
    """Real producer literals and the population/permission consumers agree."""
    import runpy
    from dataclasses import replace
    from pathlib import Path

    from butlers.db_authority import apply, grants, roles

    root = Path(__file__).parents[2]
    first = runpy.run_path(str(root / "alembic/versions/core/core_101_priority_contacts.py"))
    middle = runpy.run_path(
        str(root / "alembic/versions/core/core_129_priority_contacts_drop_butler.py")
    )
    last = runpy.run_path(
        str(root / "alembic/versions/core/core_205_priority_contacts_drop_cascade_audit.py")
    )
    produced = []
    with patch("alembic.op.execute", side_effect=lambda sql: produced.append(str(sql))):
        first["_ordinary_audit_install"]()
    original = next(sql for sql in produced if "CREATE OR REPLACE FUNCTION" in sql)
    source101 = original.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    source129 = middle["_TRIGGER_FN_GLOBAL"].split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert "OLD.butler" in source101 and "OLD.butler" not in source129
    assert middle["_TRIGGER_FN_GLOBAL"] == last["_TRIGGER_FN_GLOBAL"]
    produced.clear()
    with patch("alembic.op.execute", side_effect=lambda sql: produced.append(str(sql))):
        last["_ordinary_upgrade"]()
    assert len(produced) == 2 and all("audit_log" not in sql for sql in produced)
    full = grants.compile_relation_manifest(
        "test_priority_history", "normal_owner", "maintenance", source_database="butlers"
    )
    compiler = getattr(grants, "compile_historical_priority_audit", None)
    profile = (
        compiler(full.profiles, "normal_owner", (("core", "public", "core_101"),))
        if compiler
        else None
    )
    definitions = (profile.definition,) if profile else ()
    observed = (
        {
            "schema": "public",
            "name": "priority_contacts_cascade_audit",
            "arguments": "",
            "owner": "normal_owner",
            "oid": 730001,
        },
    )
    # The old implementation fails here in the actual population validator.
    assert apply.validate_function_population(
        observed,
        definitions,
        {("public", "priority_contacts_cascade_audit", "")},
        migration_owner="normal_owner",
    )
    for revision, expected in (
        ("core_100", None),
        ("core_101", "101"),
        ("core_128", "101"),
        ("core_129", "129"),
        ("core_204", "129"),
        ("core_205", None),
        (None, None),
    ):
        plans = (("core", "public", revision),)
        authority = compiler(full.profiles, "normal_owner", plans)
        if expected is None:
            assert authority is None
            continue
        definition = authority.definition
        assert authority.stage == expected
        assert definition.body == (source101 if expected == "101" else source129)
        assert definition.owner == "priority_contact_audit_owner" and definition.security_definer
        assert definition.settings == ("search_path=pg_catalog, pg_temp",)
        assert not definition.executors and definition.signature == ""
        selected = grants.select_migration_manifest(full, plans)
        dependencies = tuple(
            row
            for row in selected.relations
            if row.identity in {dep.identity for dep in authority.dependencies}
        )
        actual = tuple(
            dict(
                schema=row.schema,
                name=row.name,
                kind="S" if row.kind == grants.ObjectKind.SEQUENCE else "r",
                owner="normal_owner",
                rls=False,
                forced_rls=False,
                oid=730010 + i,
            )
            for i, row in enumerate(dependencies)
        )
        assert apply.validate_relations(
            actual, dependencies, administrative_history=True, historical_audit=authority
        )
        with pytest.raises(AuthorityError):
            apply.validate_relations(actual, dependencies, administrative_history=True)
        for wrong in ("PUBLIC", "butler_switchboard_rw", "foreign"):
            bad = replace(dependencies[0], permissions=(grants.Permission(wrong, ("INSERT",)),))
            with pytest.raises(AuthorityError):
                apply.validate_relations(
                    actual,
                    (bad, *dependencies[1:]),
                    administrative_history=True,
                    historical_audit=authority,
                )
        contract = grants.compiled_security_contract(selected)
        assert any(
            row["name"] == "trg_priority_contacts_cascade_audit" and row["type"] == 9
            for row in contract["triggers"]
        )
        capabilities = grants.historical_priority_audit_capabilities(selected, authority)
        assert len(capabilities.capabilities) == 6
        assert all(
            cap.verb not in {"SELECT", "UPDATE", "EXECUTE", "CREATE"}
            for cap in capabilities.capabilities
        )
        assert (
            grants.PurposeCapability("relation", "public", "audit_log_id_seq", "", "", "USAGE")
            in capabilities.capabilities
        )
        with pytest.raises(AuthorityError):
            apply.validate_function_population(
                (observed[0] | {"arguments": "text"},),
                (definition,),
                set(),
                migration_owner="normal_owner",
            )
        with pytest.raises(AuthorityError):
            apply.validate_function_population(
                (observed[0] | {"owner": "foreign"},),
                (definition,),
                set(),
                migration_owner="normal_owner",
            )
    for plans, expected in (
        ((("core", "public", "core_101"), ("core", "relationship", "core_129")), "129"),
        ((("core", "public", "core_101"), ("core", "relationship", "core_205")), None),
        ((("core", "public", None), ("core", "relationship", "core_129")), None),
    ):
        result = compiler(full.profiles, "normal_owner", plans)
        assert (result.stage if result else None) == expected
    for invalid in (
        (("core", "public", "core_unknown"),),
        (("core", "public", "core_101"), ("core", "public", "core_129")),
    ):
        with pytest.raises(AuthorityError):
            compiler(full.profiles, "normal_owner", invalid)
    assert "priority_contact_audit_owner" in roles.PURPOSE_OWNERS
    assert "priority_contact_audit_owner" not in grants._declared_function_executors(
        "uuid_generate_v7", "public", full.profiles, False
    )
    current = grants.historical_priority_audit_capabilities(full, None)
    assert not current.capabilities and not current.functions


def test_historical_priority_audit_held_transition_preserves_owner_and_audit_boundaries():
    """Actual source dispatch and held driver restore the ordinary actor."""
    import runpy
    from pathlib import Path

    from butlers.db_authority import apply, grants, interfaces

    root = Path(__file__).parents[2]
    first = runpy.run_path(str(root / "alembic/versions/core/core_101_priority_contacts.py"))
    middle = runpy.run_path(
        str(root / "alembic/versions/core/core_129_priority_contacts_drop_butler.py")
    )
    source = []
    with patch("alembic.op.execute", side_effect=lambda sql: source.append(str(sql))):
        first["_ordinary_audit_install"]()
    source101 = (
        next(sql for sql in source if "CREATE OR REPLACE FUNCTION" in sql)
        .split("AS $$", 1)[1]
        .rsplit("$$", 1)[0]
    )
    state = {
        "revision": "core_128",
        "actor": "normal_owner",
        "body": source101,
        "exists": True,
        "acl": [],
        "fault": None,
        "reset": 0,
        "restore": 0,
        "target": "public",
        "vectors": None,
        "defaults": [],
        "owned_dependencies": [],
    }
    capabilities = [
        ("column", "public", "audit_log", "", col, "INSERT", False)
        for col in ("actor", "action", "target", "note")
    ]
    capabilities += [
        ("relation", "public", "audit_log_id_seq", "", "", "USAGE", False),
        ("namespace", "public", "", "", "", "USAGE", False),
    ]

    default_observations = _HistoricalPriorityAuditConvergenceTransport.default_observations

    default_producer = _HistoricalPriorityAuditConvergenceTransport.default_producer

    def default_matrix(connection, expected, purpose, defaults, owned_dependencies):
        from copy import deepcopy

        def matches():
            return interfaces._fixed_purpose_capability_matches(
                connection, expected, purpose=purpose
            )

        assert matches()  # No default record is also source-valid.
        apply.converge_default_privileges(connection, ("public",), (purpose,))
        assert {row["kind"] for row in defaults} == {"f", "T"}
        original = deepcopy(defaults)
        original_owned = list(owned_dependencies)
        for kinds in ({"f"}, {"T"}, {"f", "T"}):
            defaults[:] = [deepcopy(row) for row in original if row["kind"] in kinds]
            owned_dependencies[:] = [("pg_default_acl", row["oid"]) for row in defaults]
            assert matches()
        mixed_bits_observations = []
        for kind in ("f", "T"):
            for mutation in (
                "public",
                "foreign",
                "grantor",
                "owner",
                "schema",
                "kind",
                "verb",
                "grantable",
                "duplicate",
                "mixed_bits",
                "unknown_oid",
                "other_class",
                "unvalidated_empty",
                "unrelated_owned",
            ):
                defaults[:] = deepcopy(original)
                owned_dependencies[:] = original_owned
                row = next(row for row in defaults if row["kind"] == kind)
                verb = "EXECUTE" if kind == "f" else "USAGE"
                if mutation in {"public", "foreign"}:
                    row["aclitem_count"] = 2
                    row["exploded"].append(
                        (purpose, "PUBLIC" if mutation == "public" else "foreign", verb, False)
                    )
                elif mutation == "grantor":
                    row["exploded"] = [("foreign", purpose, verb, False)]
                elif mutation == "owner":
                    row["owner"] = "foreign"
                elif mutation == "schema":
                    row["schema"] = "public"
                elif mutation == "kind":
                    row["kind"] = "S"
                elif mutation == "verb":
                    row["exploded"] = [(purpose, purpose, "SELECT", False)]
                elif mutation == "grantable":
                    row["exploded"] = [(purpose, purpose, verb, True)]
                elif mutation == "duplicate":
                    row["aclitem_count"] = 2
                    row["exploded"] += row["exploded"]
                elif mutation == "mixed_bits":
                    # One ACLITEM, two privileges: EXISTS alone over-admits it.
                    assert row["aclitem_count"] == 1
                    row["exploded"].append((purpose, purpose, "SELECT", False))
                elif mutation == "unknown_oid":
                    owned_dependencies.append(("pg_default_acl", 749999))
                elif mutation == "other_class":
                    owned_dependencies.append(("pg_class", row["oid"]))
                elif mutation == "unvalidated_empty":
                    defaults.append(
                        dict(
                            oid=749998,
                            owner=purpose,
                            schema="",
                            kind="r",
                            aclitem_count=0,
                            exploded=[],
                        )
                    )
                    owned_dependencies.append(("pg_default_acl", 749998))
                else:
                    owned_dependencies.append(("pg_class", 749997))
                if mutation == "mixed_bits":
                    mixed_bits_observations.append((kind, matches()))
                else:
                    assert not matches(), (kind, mutation)
        assert mixed_bits_observations == [("f", False), ("T", False)]
        defaults[:] = original
        owned_dependencies[:] = original_owned
        assert matches()

    def transport(sql, params, decoded):
        if "actor.rolsuper AND actor.rolname=session_user" in sql:
            return ("database", "owner", "valid"), [("test_priority_driver", "normal_owner", True)]
        if sql == "SELECT current_schema()":
            return ("schema",), [(state["target"],)]
        if sql == "SELECT current_database(), current_user, session_user":
            return ("database", "actor", "maintenance"), [
                ("test_priority_driver", state["actor"], "maintenance")
            ]
        if sql.lstrip().startswith("SELECT n.nspname AS schema, c.relname AS name"):
            columns = ("oid", "schema", "name", "kind", "owner", "rls", "forced_rls", "partition")
            vectors = state["vectors"] or [("public", state["revision"])]
            return columns, [
                (731000 + i, schema, "alembic_version", "r", "normal_owner", False, False, False)
                for i, (schema, revision) in enumerate(vectors)
            ]
        if "owner-lookup-replay:revision-abi" in sql:
            return ("valid",), [(True,)]
        if "FROM pg_catalog.pg_attribute col" in sql:
            return ("name", "role", "verb", "grantable"), []
        if "a.privilege_type AS verb" in sql and "FROM pg_catalog.pg_class c" in sql:
            return ("role", "verb", "grantable"), []
        if sql.startswith('SELECT version_num FROM "'):
            schema = sql.split('"')[1]
            vectors = dict(state["vectors"] or [("public", state["revision"])])
            return ("version_num",), [(vectors[schema],)]
        if "WITH owners AS" in sql:
            return ("valid",), [(state["fault"] != "role",)]
        if "AS signature" in sql and "p.proname='priority_contacts_cascade_audit'" in sql:
            return ("signature",), (
                [("text" if state["fault"] == "signature" else "",)] if state["exists"] else []
            )
        if "pg_get_function_identity_arguments" in sql:
            return ("oid", "owner", "definer", "config", "body", "arguments"), (
                [
                    (
                        731001,
                        "foreign" if state["fault"] == "owner" else "priority_contact_audit_owner",
                        state["fault"] != "security",
                        [] if state["fault"] == "path" else ["search_path=pg_catalog, pg_temp"],
                        state["body"] if state["fault"] != "body" else "invalid",
                        "",
                    )
                ]
                if state["exists"]
                else []
            )
        if "owner-lookup-replay:metadata" in sql:
            return ("valid",), [(state["fault"] != "metadata",)]
        if "AS privilege" in sql and "FROM pg_catalog.pg_proc p" in sql:
            return ("role", "privilege", "grantable"), (
                [("PUBLIC", "EXECUTE", False)] if state["fault"] == "execute" else state["acl"]
            )
        if "historical-audit:public-namespace" in sql:
            assert params == {"owner": "normal_owner"}
            return ("valid",), [(state["fault"] != "namespace",)]
        if "t.tgfoid=pg_catalog.to_regprocedure" in sql:
            return ("valid",), [(state["fault"] != "trigger",)]
        if "count(*)=2 AND bool_and" in sql:
            return ("valid",), [(state["fault"] != "dependencies",)]
        if "count(*)=4 AND bool_and" in sql:
            return ("valid",), [(state["fault"] != "sequence",)]
        produced = default_producer(
            sql,
            decoded,
            "priority_contact_audit_owner",
            state["defaults"],
            state["owned_dependencies"],
        )
        if produced is not None:
            return produced
        if "complete-purpose-capabilities" in sql:
            assert "rolname='priority_contact_audit_owner'" in sql
            return ("kind", "schema", "name", "signature", "column", "verb", "grantable"), (
                capabilities
                + default_observations(
                    sql,
                    "priority_contact_audit_owner",
                    state["defaults"],
                    state["owned_dependencies"],
                )
                + (
                    [("parameter", "", "work_mem", "", "", "SET", False)]
                    if state["fault"] == "capability"
                    else []
                )
            )
        if "managed-public-capabilities" in sql:
            assert "a.grantee=0" in sql
            return ("kind", "schema", "name", "signature", "column", "verb", "grantable"), (
                [("column", "public", "model_catalog", "", "id", "SELECT", False)]
                if state["fault"] == "public"
                else []
            )
        if sql == "RESET ROLE":
            state["actor"] = "maintenance"
            state["reset"] += 1
            return None, []
        if sql.startswith('SET ROLE "normal_owner"'):
            state["actor"] = "normal_owner"
            state["restore"] += 1
            if state["fault"] == "cancel-close":
                raise RuntimeError("restore failed")
            return None, []
        if "SELECT session_user=current_user AND rolsuper" in sql:
            return ("valid",), [(state["actor"] == "maintenance",)]
        if "CREATE OR REPLACE FUNCTION public.priority_contacts_cascade_audit" in sql:
            if state["actor"] != "maintenance":
                raise PermissionError("normal actor denied replacement")
            if state["fault"] in {"cancel", "cancel-close"}:
                raise asyncio.CancelledError()
            state["body"] = decoded.split("AS $db_authority_body$", 1)[1].rsplit(
                "$db_authority_body$", 1
            )[0]
            state["exists"] = True
            return None, []
        if sql.startswith('ALTER FUNCTION "public"."priority_contacts_cascade_audit"() OWNER'):
            return None, []
        if sql.startswith("REVOKE ALL ON FUNCTION"):
            state["acl"] = []
            return None, []
        if "DO $audit_attach$" in sql or "DO $audit_retire$" in sql:
            assert state["actor"] == "maintenance"
            return None, []
        if sql.startswith("DROP FUNCTION IF EXISTS public.priority_contacts_cascade_audit"):
            assert state["actor"] == "maintenance"
            state["exists"] = False
            return None, []
        raise AssertionError(
            "Unmodeled catalog operation " + __import__("hashlib").sha256(sql.encode()).hexdigest()
        )

    with _recording_postgresql_connection(transport) as (connection, calls):
        connection.info["butlers.source_database"] = "butlers"

        def ordinary(sql):
            if "CREATE OR REPLACE FUNCTION" in str(sql):
                raise PermissionError("ordinary actor cannot replace purpose function")

        with patch.dict(
            middle["upgrade"].__globals__,
            {
                "_table_exists": lambda conn: True,
                "_column_exists": lambda *a: False,
                "_grant_best_effort": lambda *a: None,
            },
        ):
            with (
                patch("alembic.op.get_bind", return_value=connection),
                patch("alembic.op.execute", side_effect=ordinary),
            ):
                middle["upgrade"]()
        assert state["body"] == middle["_TRIGGER_FN_GLOBAL"].split("AS $$", 1)[1].rsplit("$$", 1)[0]
        assert state["actor"] == "normal_owner" and state["reset"] == state["restore"] == 1
        for fault in (
            "role",
            "body",
            "signature",
            "owner",
            "security",
            "path",
            "execute",
            "metadata",
            "trigger",
            "dependencies",
            "sequence",
            "capability",
            "namespace",
        ):
            state.update(revision="core_129", fault=fault)
            before = state["reset"]
            with pytest.raises(AuthorityError):
                interfaces.historical_priority_audit_revision(connection, "core_205", "upgrade")
            assert state["reset"] == before
        state.update(revision="core_129", fault=None)
        manifest = grants.compile_relation_manifest(
            "test_priority_driver", "normal_owner", "maintenance", source_database="butlers"
        )
        authority = grants.compile_historical_priority_audit(
            manifest.profiles, "normal_owner", (("core", "public", "core_129"),)
        )
        expected = grants.historical_priority_audit_capabilities(manifest, authority)
        assert interfaces._fixed_purpose_capability_matches(
            connection, expected, purpose="priority_contact_audit_owner"
        )
        default_matrix(
            connection,
            expected,
            "priority_contact_audit_owner",
            state["defaults"],
            state["owned_dependencies"],
        )
        state["fault"] = "public"
        assert not interfaces._fixed_purpose_capability_matches(
            connection, expected, purpose="priority_contact_audit_owner"
        )
        state.update(revision="core_204", fault=None)
        assert interfaces.historical_priority_audit_revision(connection, "core_205", "upgrade")
        assert not state["exists"] and state["actor"] == "normal_owner"
        from butlers.migrations import get_chain_head

        for target, vectors in (
            ("public", [("public", "core_205"), ("switchboard", get_chain_head("core"))]),
            ("public", [("public", "core_204"), ("switchboard", "core_205")]),
            ("public", [("public", "core_205"), ("switchboard", "core_205")]),
            ("switchboard", [("public", "core_205"), ("switchboard", "core_204")]),
        ):
            state.update(target=target, vectors=vectors)
            before = state["reset"]
            with pytest.raises(AuthorityError):
                interfaces.historical_priority_audit_revision(connection, "core_205", "downgrade")
            assert state["reset"] == before and not state["exists"]
        state.update(target="public", vectors=None, revision="core_205")
        assert interfaces.historical_priority_audit_revision(connection, "core_205", "downgrade")
        assert state["exists"] and state["actor"] == "normal_owner"
        state["revision"] = "core_129"
        assert interfaces.historical_priority_audit_revision(connection, "core_129", "downgrade")
        assert state["body"] == source101
        for fault in ("cancel", "cancel-close"):
            state.update(revision="core_128", fault=fault)
            primary = asyncio.CancelledError()

            def cancelled_install(*args, primary=primary):
                raise primary

            with patch.object(
                interfaces, "normalize_historical_priority_audit", side_effect=cancelled_install
            ):
                with pytest.raises(asyncio.CancelledError) as observed:
                    interfaces.historical_priority_audit_revision(connection, "core_129", "upgrade")
            assert observed.value is primary
            assert state["actor"] == "normal_owner"
        state["fault"] = None
        for args in (("core_101;DROP", "upgrade"), ("core_129", "other")):
            before = len(calls)
            with pytest.raises(AuthorityError):
                interfaces.historical_priority_audit_revision(connection, *args)
            assert len(calls) == before

    # Exercise the same actual shared query with the other fixed purpose and
    # its real compiler-selected UUID/function/PUBLIC population.
    lookup = _OwnerLookupPrefixCatalog("current")
    lookup_defaults = []
    lookup_owned = []

    def lookup_transport(sql, params, decoded):
        produced = default_producer(
            sql, decoded, "identity_lookup_owner", lookup_defaults, lookup_owned
        )
        if produced is not None:
            return produced
        columns, rows = lookup.respond(sql, params, decoded)
        if "complete-purpose-capabilities" in sql:
            assert "rolname='identity_lookup_owner'" in sql
            rows = list(rows) + default_observations(
                sql, "identity_lookup_owner", lookup_defaults, lookup_owned
            )
        return columns, rows

    with _recording_postgresql_connection(lookup_transport) as (connection, calls):
        assert any(
            row.name == "uuid_generate_v7" for row in lookup.expected_capabilities.capabilities
        )
        default_matrix(
            connection,
            lookup.expected_capabilities,
            "identity_lookup_owner",
            lookup_defaults,
            lookup_owned,
        )
        for fault in (
            "uuid_execute_missing",
            "parameter_set_grant",
            "model_catalog_extra_column",
            "public_model_catalog_extra_column",
        ):
            lookup.lookup_fault = fault
            assert not interfaces._owner_lookup_capability_matches(
                connection, lookup.expected_capabilities
            )
        lookup.lookup_fault = None
        assert interfaces._owner_lookup_capability_matches(connection, lookup.expected_capabilities)

    # A selected private-module retry is not evidence that the installed public
    # core retired its source-produced trigger. Record actual producers and
    # actual finalizer effects; the catalog boundary remains software only.
    import re

    phase_sources = []
    for filename in ("core_002_identity.py", "core_092_audit_log.py"):
        producer = runpy.run_path(str(root / "alembic/versions/core" / filename))
        with patch("alembic.op.execute", side_effect=lambda sql: phase_sources.append(str(sql))):
            producer["upgrade"]()
    # Capture the actual ordinary core101 producer too; its function-only
    # helper alone cannot prove the priority_contacts table was produced.
    state.update(
        revision="core_100",
        actor="normal_owner",
        fault=None,
        exists=False,
        vectors=None,
        target="public",
    )
    with _recording_postgresql_connection(transport) as (producer_connection, _):
        producer_connection.info["butlers.source_database"] = "butlers"
        with (
            patch("alembic.op.get_bind", return_value=producer_connection),
            patch("alembic.op.execute", side_effect=lambda sql: phase_sources.append(str(sql))),
        ):
            first["upgrade"]()
    phase_sources.extend(source)
    memory_root = root / "src/butlers/modules/memory/migrations"
    memory_sources = []
    for filename in (
        "001_memory_schema.py",
        "002_seed_predicates.py",
        "003_wellness_predicates.py",
        "004_embedding_model_version.py",
        "005_drop_embedding_versions.py",
        "006_drop_rule_applications.py",
    ):
        producer = runpy.run_path(str(memory_root / filename))
        with patch("alembic.op.execute", side_effect=lambda sql: memory_sources.append(str(sql))):
            producer["upgrade"]()
    full = grants.compile_relation_manifest(
        "test_priority_driver", "normal_owner", "maintenance", source_database="butlers"
    )
    private = next(p.private_schemas[0] for p in full.profiles if p.private_schemas)
    assert any(private in p.private_schemas and "memory" in p.modules for p in full.profiles)
    from butlers.migrations import finalize_authority, get_chain_revision_path

    assert {"core_092", "core_101", "core_129"} <= get_chain_revision_path("core", "core_133")
    assert {"mem_001", "mem_005", "mem_006"} <= get_chain_revision_path("memory", "mem_006")
    # The other public identity tables are outside this component observation;
    # every table modeled below is bound to its actual emitted producer.
    public_names = {"contacts", "audit_log", "priority_contacts"}
    observed_names = {
        match[1]
        for sql in phase_sources
        if (match := re.search(r"CREATE TABLE IF NOT EXISTS public\.([a-z_]+)\s*\(", sql))
        and match[1] in public_names
    }
    assert observed_names == public_names
    assert any("id         BIGSERIAL PRIMARY KEY" in sql for sql in phase_sources)
    assert any("CREATE TRIGGER trg_priority_contacts_cascade_audit" in sql for sql in source)
    assert all(
        f"{col}" in next(sql for sql in phase_sources if "public.audit_log (" in sql)
        for col in ("actor", "action", "target", "note")
    )
    memory_names = set()
    for sql in memory_sources:
        if match := re.search(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)\s*\(", sql):
            memory_names.add(match[1])
        if match := re.fullmatch(r"DROP TABLE IF EXISTS ([a-z_]+)(?: CASCADE)?", sql):
            memory_names.discard(match[1])
    assert memory_names and not {"embedding_versions", "rule_applications"} & memory_names
    tables = {}
    for schema, names in (
        ("public", observed_names | {"audit_log_id_seq"}),
        (private, memory_names),
    ):
        for name in sorted(names):
            oid = 751000 + len(tables)
            tables[oid] = dict(
                oid=oid,
                schema=schema,
                name=name,
                kind="S" if name == "audit_log_id_seq" else "r",
                owner="normal_owner",
                rls=False,
                forced_rls=False,
                partition=False,
            )
    # The ledger ABI is constructed by the installed real Alembic producer,
    # independently of the application's reader/compiler expectations.
    from alembic.ddl.impl import DefaultImpl

    ledger = {"public": "core_133", private: "mem_006"}
    ledger_metadata = {}
    for schema in ledger:
        table = DefaultImpl.version_table_impl(
            None,
            version_table="alembic_version",
            version_table_schema=schema,
            version_table_pk=True,
        )
        oid = 751000 + len(tables)
        tables[oid] = dict(
            oid=oid,
            schema=str(table.schema),
            name=str(table.name),
            kind="r",
            owner="normal_owner",
            rls=False,
            forced_rls=False,
            partition=False,
        )
        ledger_metadata[oid] = table
    table_acl = {oid: set() for oid in tables}
    column_acl = {oid: set() for oid in tables}
    # Source runtime grants are observed preimages and later revoked; they do
    # not define the finalizer's allow-set or the non-serving purpose profile.
    for sql in phase_sources:
        for grant in re.finditer(
            r"EXECUTE 'GRANT ([A-Z, ]+) ON TABLE public\.([a-z_]+) TO \"([a-z_]+)\"'", sql
        ):
            for oid, row in tables.items():
                if (row["schema"], row["name"]) == ("public", grant[2]):
                    table_acl[oid].update((grant[3], verb.strip()) for verb in grant[1].split(","))
    namespace_ids = {
        schema: 752000 + i for i, schema in enumerate(("public", "connectors", "calendar", private))
    }
    namespace_acl = {oid: set() for oid in namespace_ids.values()}
    audit_body = middle["_TRIGGER_FN_GLOBAL"].split("AS $$", 1)[1].rsplit("$$", 1)[0]
    memory_body = next(sql for sql in memory_sources if "CREATE OR REPLACE FUNCTION" in sql)
    memory_body = memory_body.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    functions = {
        ("public", "priority_contacts_cascade_audit"): dict(
            oid=753001,
            owner="normal_owner",
            definer=True,
            config=[],
            body=audit_body,
            arguments="",
            acl=[("PUBLIC", "EXECUTE", False)],
        ),
        (private, "predicate_registry_search_vector_trigger"): dict(
            oid=753002,
            owner="normal_owner",
            definer=False,
            config=[],
            body=memory_body,
            arguments="",
            acl=[("PUBLIC", "EXECUTE", False)],
        ),
    }
    state.update(
        revision="core_133",
        actor="maintenance",
        fault=None,
        defaults=[],
        owned_dependencies=[],
        exists=True,
        vectors=list(ledger.items()),
    )

    ledger_grant_options = set()
    duplicate_ledger = []
    public_column_acl = set()
    public_observations = []
    source_read_drift = {"armed": False, "active": False}

    def phase_transport(sql, params, decoded):
        canonical = re.sub(r"%\((\w+)\)s", r":\1", sql)
        if decoded == "SELECT current_database(), session_user":
            return ("database", "actor"), [("test_priority_driver", "maintenance")]
        for schema, revision in ledger.items():
            if decoded in {
                f'SELECT version_num FROM "{schema}".alembic_version',
                f'SELECT version_num FROM "{schema}"."alembic_version"',
            }:
                values = revision if type(revision) is tuple else (revision,)
                if source_read_drift["armed"]:
                    source_read_drift["active"] = True
                return ("version_num",), [(value,) for value in values]
        if "owner-lookup-replay:revision-abi" in sql:
            table = ledger_metadata[params["oid"]]
            columns = tuple(table.columns)
            valid = (
                len(columns) == 1
                and columns[0].name == "version_num"
                and str(columns[0].type) == "VARCHAR(32)"
                and not columns[0].nullable
                and tuple(col.name for col in table.primary_key) == ("version_num",)
                and columns[0].server_default is None
                and columns[0].identity is None
                and columns[0].computed is None
            )
            return ("valid",), [(valid,)]
        if canonical == apply._FUNCTIONS:
            return ("oid", "schema", "name", "arguments", "owner"), [
                (row["oid"], schema, name, row["arguments"], row["owner"])
                for (schema, name), row in functions.items()
                if schema in params["schemas"]
            ]
        if canonical == apply._RELATIONS:
            rows = [row for row in tables.values() if row["schema"] in params["schemas"]]
            rows += [row for row in duplicate_ledger if row["schema"] in params["schemas"]]
            return tuple(next(iter(tables.values()))), [tuple(row.values()) for row in rows]
        if canonical == apply._PARTITION_LINKS:
            return ("oid", "parent_schema", "parent_name"), []
        if canonical == apply._SEQUENCE_LINKS:
            return (
                "oid",
                "parent_schema",
                "parent_name",
                "parent_column",
                "parent_owner",
                "deptype",
            ), [
                (oid, "public", "audit_log", "id", "normal_owner", "a")
                for oid in params["oids"]
                if tables[oid]["name"] == "audit_log_id_seq"
            ]
        if canonical == apply._RELATION_ACL:
            return ("role", "verb", "grantable"), [
                (role, verb, params["oid"] in ledger_grant_options)
                for role, verb in table_acl[params["oid"]]
            ]
        if canonical == apply._COLUMN_ACL:
            return ("role", "name", "verb", "grantable"), [
                (role, col, verb, False) for role, col, verb in column_acl[params["oid"]]
            ]
        if canonical == apply._NAMESPACE_CATALOG:
            return ("oid", "name", "owner"), [
                (namespace_ids[s], s, "normal_owner") for s in params["schemas"]
            ]
        if canonical == apply._NAMESPACE_ACL:
            return ("role", "verb", "grantable"), [
                (role, verb, False) for role, verb in namespace_acl[params["oid"]]
            ]
        if "AS signature" in sql and "p.proname='priority_contacts_cascade_audit'" in sql:
            return ("signature",), [
                (functions[("public", "priority_contacts_cascade_audit")]["arguments"],)
            ]
        if canonical == interfaces._FUNCTION_CATALOG:
            signature = params["signature"]
            for (schema, name), row in functions.items():
                if signature == f'"{schema}"."{name}"()':
                    keys = ("oid", "owner", "definer", "config", "body", "arguments")
                    return keys, [tuple(row[key] for key in keys)]
            raise AssertionError("Unknown component function observation")
        if canonical == interfaces._FUNCTION_ACL:
            return ("role", "privilege", "grantable"), next(
                row["acl"] for row in functions.values() if row["oid"] == params["oid"]
            )
        if "managed-public-capabilities" in sql:
            assert "a.grantee=0" in sql
            public_observations.append(True)
            rows = [
                ("column", tables[oid]["schema"], tables[oid]["name"], "", column, verb, grantable)
                for oid, column, grantee, verb, grantable in public_column_acl
                if grantee == 0 and tables[oid]["schema"] in params["object_schemas"]
            ]
            return ("kind", "schema", "name", "signature", "column", "verb", "grantable"), rows
        if "complete-purpose-capabilities" in sql:
            assert "rolname='priority_contact_audit_owner'" in sql
            rows = [
                ("relation", row["schema"], row["name"], "", "", verb, False)
                for oid, row in tables.items()
                for role, verb in table_acl[oid]
                if role == "priority_contact_audit_owner"
            ]
            rows += [
                ("column", row["schema"], row["name"], "", col, verb, False)
                for oid, row in tables.items()
                for role, col, verb in column_acl[oid]
                if role == "priority_contact_audit_owner"
            ]
            rows += [
                ("namespace", schema, "", "", "", verb, False)
                for schema, oid in namespace_ids.items()
                for role, verb in namespace_acl[oid]
                if role == "priority_contact_audit_owner"
            ]
            rows += default_observations(
                sql, "priority_contact_audit_owner", state["defaults"], state["owned_dependencies"]
            )
            return ("kind", "schema", "name", "signature", "column", "verb", "grantable"), rows
        if decoded.startswith("CREATE OR REPLACE FUNCTION public.priority_contacts_cascade_audit"):
            assert state["actor"] == "maintenance"
            row = functions[("public", "priority_contacts_cascade_audit")]
            row.update(
                body=decoded.split("AS $db_authority_body$", 1)[1].rsplit("$db_authority_body$", 1)[
                    0
                ],
                config=["search_path=pg_catalog, pg_temp"],
            )
            return None, []
        if match := re.fullmatch(
            r'ALTER FUNCTION "([a-z_]+)"\."([a-z_]+)"\(\) OWNER TO "([a-z_]+)"', decoded
        ):
            functions[(match[1], match[2])]["owner"] = match[3]
            return None, []
        if match := re.fullmatch(
            r'REVOKE ALL ON FUNCTION "([a-z_]+)"\."([a-z_]+)"\(\) FROM (PUBLIC|"[a-z_]+")', decoded
        ):
            row = functions[(match[1], match[2])]
            role = match[3].strip('"')
            row["acl"] = [acl for acl in row["acl"] if acl[0] != role]
            return None, []
        if match := re.fullmatch(r'GRANT USAGE ON SCHEMA "([a-z_]+)" TO "([a-z_]+)"', decoded):
            namespace_acl[namespace_ids[match[1]]].add((match[2], "USAGE"))
            return None, []
        if match := re.fullmatch(r'REVOKE ALL ON SCHEMA "([a-z_]+)" FROM "([a-z_]+)"', decoded):
            namespace_acl[namespace_ids[match[1]]].difference_update(
                {(match[2], "USAGE"), (match[2], "CREATE")}
            )
            return None, []
        for oid, row in tables.items():
            target = f'"{row["schema"]}"."{row["name"]}"'
            if match := re.fullmatch(
                r"REVOKE ALL PRIVILEGES ON (TABLE|SEQUENCE) "
                + re.escape(target)
                + r' FROM "([a-z_]+)"',
                decoded,
            ):
                table_acl[oid].difference_update(
                    {entry for entry in table_acl[oid] if entry[0] == match[2]}
                )
                return None, []
            if (
                decoded
                == f'GRANT INSERT ("actor", "action", "target", "note") ON TABLE {target} TO "priority_contact_audit_owner"'
            ):
                assert (row["schema"], row["name"]) == ("public", "audit_log")
                column_acl[oid].update(
                    ("priority_contact_audit_owner", col, "INSERT")
                    for col in ("actor", "action", "target", "note")
                )
                return None, []
            if decoded == f'GRANT USAGE ON SEQUENCE {target} TO "priority_contact_audit_owner"':
                assert (row["schema"], row["name"]) == ("public", "audit_log_id_seq")
                table_acl[oid].add(("priority_contact_audit_owner", "USAGE"))
                return None, []
        if canonical == apply._DEFAULT_ACL:
            return ("owner", "schema", "kind", "role", "verb"), []
        if decoded.startswith('ALTER DEFAULT PRIVILEGES FOR ROLE "'):
            role = decoded.split('"')[1]
            assert role in {*roles.PURPOSE_OWNERS, "normal_owner", "maintenance"}
            if role != "priority_contact_audit_owner":
                assert decoded in {
                    f'ALTER DEFAULT PRIVILEGES FOR ROLE "{role}" REVOKE ALL ON {kind} FROM PUBLIC'
                    for kind in ("FUNCTIONS", "TYPES")
                }
                return None, []
        return transport(sql, params, decoded)

    with _recording_postgresql_connection(phase_transport) as (connection, calls):
        connection.info["butlers.source_database"] = "butlers"
        initial = (("core", "public", "core_133"), ("memory", private, "mem_006"))
        finalize_authority(connection, source_database="butlers", plans=initial)
        assert (
            functions[("public", "priority_contacts_cascade_audit")]["owner"]
            == "priority_contact_audit_owner"
        )
        assert functions[("public", "priority_contacts_cascade_audit")]["acl"] == []
        assert ledger == {"public": "core_133", private: "mem_006"}
        # This actual consumer must retain the already-finalized public phase;
        # requested module plans cannot authorize global retirement.
        assert not connection.in_transaction()
        assert all(
            role == "priority_contact_audit_owner"
            for rows in table_acl.values()
            for role, _verb in rows
        )
        assert all(
            role == "priority_contact_audit_owner"
            for rows in column_acl.values()
            for role, _column, _verb in rows
        )
        before_replay = len(calls)
        try:
            finalize_authority(
                connection, source_database="butlers", plans=(("memory", private, "mem_006"),)
            )
        except AuthorityError as error:
            # Closed source-category check; no raw exception text is retained.
            assert str(error) == "Retired priority audit function remains available"
            assert not any(
                decoded.lstrip().startswith(("CREATE ", "ALTER ", "DROP ", "GRANT ", "REVOKE "))
                for _, _, decoded in calls[before_replay:]
            )
            assert ledger == {"public": "core_133", private: "mem_006"}
            error.add_note(
                "DB_MODULE_ONLY_PHASE_BASELINE_V1 initial_finalizer=complete selected_memory=mem_006 durable_core=core_133 category=retired_audit"
            )
            raise

        assert ledger == {"public": "core_133", private: "mem_006"}
        assert (
            functions[("public", "priority_contacts_cascade_audit")]["owner"]
            == "priority_contact_audit_owner"
        )
        assert functions[("public", "priority_contacts_cascade_audit")]["acl"] == []
        assert not any(
            "priority_contacts_cascade_audit" in decoded
            and decoded.lstrip().startswith(("CREATE ", "ALTER ", "DROP ", "GRANT ", "REVOKE "))
            for _, _, decoded in calls[before_replay:]
        )

        # These contexts come from the installed source vector. Requested future
        # source205 may not retire a still-installed133 component.
        installed = (("core", "public", "core_133"), ("memory", private, "mem_006"))
        future = grants.select_migration_manifest(
            full, (("core", "public", "core_205"),), installed_audit_vector=installed
        )
        assert any(
            row.name == "audit_log"
            and any(
                permission.role == "priority_contact_audit_owner" for permission in row.permissions
            )
            for row in future.relations
        )
        for installed_core, expected_stage in (
            ("core_100", None),
            ("core_101", "101"),
            ("core_129", "129"),
            ("core_133", "129"),
            ("core_205", None),
        ):
            compiled = grants.compile_historical_priority_audit(
                full.profiles,
                "normal_owner",
                (("core", "public", installed_core), ("memory", private, "mem_006")),
            )
            assert (compiled.stage if compiled else None) == expected_stage
        assert (
            grants.compile_historical_priority_audit(
                full.profiles, "normal_owner", (("memory", private, "mem_006"),)
            )
            is None
        )
        assert (
            grants.compile_historical_priority_audit(
                full.profiles,
                "normal_owner",
                (("core", "public", "core_133"), ("core", "general", "core_205")),
            )
            is None
        )
        from copy import deepcopy

        public_ledger = next(
            oid
            for oid, row in tables.items()
            if row["schema"] == "public" and row["name"] == "alembic_version"
        )
        audit = functions[("public", "priority_contacts_cascade_audit")]
        audit_oid = next(
            oid
            for oid, row in tables.items()
            if row["schema"] == "public" and row["name"] == "audit_log"
        )
        original_ledger = dict(tables[public_ledger])
        original_audit = deepcopy(audit)
        for fault in (
            "public_column",
            "owner",
            "kind",
            "rls",
            "forced_rls",
            "abi",
            "unknown_revision",
            "duplicate_revision",
            "duplicate_ledger",
            "unknown_reader",
            "write",
            "grant_option",
            "column",
            "audit_body",
            "audit_owner",
            "audit_security",
            "audit_path",
            "audit_signature",
            "audit_execute",
            "audit_capability",
            "namespace",
        ):
            tables[public_ledger].update(original_ledger)
            audit.update(deepcopy(original_audit))
            ledger["public"] = "core_133"
            if fault == "public_column":
                public_column_acl.add((audit_oid, "actor", 0, "SELECT", False))
            elif fault in {"owner", "kind", "rls", "forced_rls"}:
                field = {
                    "owner": "owner",
                    "kind": "kind",
                    "rls": "rls",
                    "forced_rls": "forced_rls",
                }[fault]
                tables[public_ledger][field] = {
                    "owner": "foreign",
                    "kind": "v",
                    "rls": True,
                    "forced_rls": True,
                }[fault]
            elif fault == "abi":
                ledger_metadata[public_ledger].c.version_num.nullable = True
            elif fault == "unknown_revision":
                ledger["public"] = "core_unknown"
            elif fault == "duplicate_revision":
                ledger["public"] = ("core_133", "core_133")
            elif fault == "duplicate_ledger":
                duplicate_ledger.append(dict(tables[public_ledger]))
            elif fault in {"unknown_reader", "write", "grant_option"}:
                role, verb = (
                    ("unknown_role", "SELECT")
                    if fault == "unknown_reader"
                    else ("dashboard_api", "UPDATE" if fault == "write" else "SELECT")
                )
                table_acl[public_ledger].add((role, verb))
                if fault == "grant_option":
                    ledger_grant_options.add(public_ledger)
            elif fault == "column":
                column_acl[public_ledger].add(("dashboard_api", "version_num", "SELECT"))
            elif fault.startswith("audit_"):
                field = fault.removeprefix("audit_")
                if field == "body":
                    audit["body"] = "invalid"
                elif field == "owner":
                    audit["owner"] = "foreign"
                elif field == "security":
                    audit["definer"] = False
                elif field == "path":
                    audit["config"] = []
                elif field == "signature":
                    audit["arguments"] = "text"
                elif field == "execute":
                    audit["acl"] = [("PUBLIC", "EXECUTE", False)]
                else:
                    table_acl[audit_oid].add(("priority_contact_audit_owner", "SELECT"))
            else:
                state["fault"] = "namespace"
            before_fault = len(calls)
            before_public_observation = len(public_observations)
            with pytest.raises(AuthorityError):
                finalize_authority(
                    connection, source_database="butlers", plans=(("memory", private, "mem_006"),)
                )
            if fault == "public_column":
                assert len(public_observations) > before_public_observation
            assert not any(
                decoded.lstrip().startswith(("CREATE ", "ALTER ", "DROP ", "GRANT ", "REVOKE "))
                for _, _, decoded in calls[before_fault:]
            ), fault
            tables[public_ledger].update(original_ledger)
            audit.update(deepcopy(original_audit))
            ledger["public"] = "core_133"
            ledger_metadata[public_ledger].c.version_num.nullable = False
            duplicate_ledger.clear()
            ledger_grant_options.clear()
            public_column_acl.clear()
            table_acl[public_ledger].clear()
            column_acl[public_ledger].clear()
            table_acl[audit_oid].discard(("priority_contact_audit_owner", "SELECT"))
            state["fault"] = None
        assert ledger == {"public": "core_133", private: "mem_006"}
        assert audit == original_audit

        actual_source_bytes = Path.read_bytes
        compiler_path = Path(grants.__file__).resolve()

        def changed_source_read(path):
            observed = actual_source_bytes(path)
            if source_read_drift["active"] and path.resolve() == compiler_path:
                return observed + b"\n# independently observed source change\n"
            return observed

        before_drift = len(calls)
        source_read_drift["armed"] = True
        try:
            with patch.object(Path, "read_bytes", changed_source_read):
                with pytest.raises(AuthorityError):
                    finalize_authority(
                        connection,
                        source_database="butlers",
                        plans=(("memory", private, "mem_006"),),
                    )
        finally:
            source_read_drift.update(armed=False, active=False)
        assert not any(
            decoded.lstrip().startswith(("CREATE ", "ALTER ", "DROP ", "GRANT ", "REVOKE "))
            for _, _, decoded in calls[before_drift:]
        )
        assert ledger == {"public": "core_133", private: "mem_006"}


# Explicit credential composition: driver controls below are software evidence,
# never a claim that native role/catalog authority was executed.
def test_credential_purpose_input_is_private_and_has_no_runtime_fallback():
    from butlers.credential_store import (
        CredentialPurposeConfig,
        credential_purpose_config_from_env,
    )

    runtime = {"DATABASE_URL": "postgresql://runtime:synthetic@localhost/butlers"}
    assert credential_purpose_config_from_env(runtime) is None
    selected = credential_purpose_config_from_env(
        runtime
        | {
            "BUTLERS_CREDENTIAL_DATABASE_URL": (
                "postgresql://credential:unprinted-marker@localhost/butlers"
            ),
            "BUTLER_SHARED_DB_SCHEMA": "public",
        }
    )
    assert isinstance(selected, CredentialPurposeConfig)
    assert selected.schema == "public"
    assert "unprinted-marker" not in repr(selected)
    assert "postgresql" not in repr(selected)
    with pytest.raises(FrozenInstanceError):
        selected.schema = "general"
    for invalid in ("", " ", None):
        with pytest.raises(AuthorityError):
            credential_purpose_config_from_env({"BUTLERS_CREDENTIAL_DATABASE_URL": invalid})


async def test_daemon_credential_omission_never_consumes_ambient_writer(driver, monkeypatch):
    from butlers.daemon import ButlerDaemon

    _, pools, connect = driver
    monkeypatch.setenv(
        "BUTLERS_CREDENTIAL_DATABASE_URL",
        "postgresql://credential:synthetic@localhost/butlers",
    )
    monkeypatch.setenv("BUTLER_SHARED_DB_SCHEMA", "public")
    daemon = ButlerDaemon(butler_name="general")
    local_pool = Mock()
    store = await daemon._build_credential_store(local_pool)
    assert store.pool is local_pool
    assert store.has_system_global_authority is False
    with pytest.raises(RuntimeError):
        store.require_system_global_pool()
    assert daemon._credential_purpose_pool is None
    assert pools == []
    connect.assert_not_called()


async def test_explicit_daemon_credential_input_reuses_real_admission_and_acquire_fence(driver):
    from butlers.credential_store import CredentialPurposeConfig
    from butlers.daemon import ButlerDaemon

    connection, pools, _ = driver
    config = CredentialPurposeConfig("postgresql://credential:synthetic@localhost/butlers")
    refused = ButlerDaemon(butler_name="general", credential_purpose=config)
    connection.facts["ownership_ok"] = False
    with pytest.raises(AuthorityError):
        await refused._build_credential_store(Mock())
    assert refused._credential_purpose_pool is None
    pools[-1].close.assert_awaited_once()

    connection.facts["ownership_ok"] = True
    daemon = ButlerDaemon(butler_name="general", credential_purpose=config)
    local_pool = Mock()
    store = await daemon._build_credential_store(local_pool)
    pool = store.require_system_global_pool()
    assert pool is daemon._credential_purpose_pool
    assert store.pool is local_pool
    async with pool.acquire() as acquired:
        assert acquired is connection
    connection.facts["role_set_ok"] = False
    with pytest.raises(AuthorityError):
        async with pool.acquire():
            pytest.fail("Drifted explicit purpose escaped to a credential consumer")
    connection.facts["role_set_ok"] = True
    await pool.close()
    pools[-1].close.assert_awaited_once()

    invalid = ButlerDaemon(
        butler_name="general",
        credential_purpose=CredentialPurposeConfig(
            "postgresql://credential:synthetic@localhost/unlisted"
        ),
    )
    before = len(pools)
    with pytest.raises(AuthorityError):
        await invalid._build_credential_store(local_pool)
    assert len(pools) == before
    assert invalid._credential_purpose_pool is None


@pytest.mark.parametrize("entry", ["all", "single", "control"])
async def test_real_process_composition_transports_explicit_credential_input(entry, monkeypatch):
    """Execute the process composition, doubling daemon I/O rather than authority."""
    from pathlib import Path
    from types import SimpleNamespace

    from butlers import cli, control
    from butlers.credential_store import CredentialPurposeConfig

    selected = CredentialPurposeConfig("postgresql://credential:synthetic@localhost/butlers")
    monkeypatch.setattr(
        "butlers.credential_store.credential_purpose_config_from_env", lambda: selected
    )
    signal_handlers = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda signal, fn: signal_handlers.append(fn))
    monkeypatch.setattr(loop, "remove_signal_handler", lambda signal: None)
    instances = []

    class StartedDaemon:
        def __init__(self, path, **kwargs):
            assert kwargs.get("credential_purpose") is selected
            self.config = SimpleNamespace(name="general", port=41100)
            self.shutdown = AsyncMock()
            instances.append(self)

        async def start(self):
            if entry == "control":
                raise RuntimeError("composition-stop")
            signal_handlers[0]()

    monkeypatch.setattr("butlers.daemon.ButlerDaemon", StartedDaemon)
    if entry == "all":
        monkeypatch.setattr(cli, "_record_deployment_boot", AsyncMock())
        await cli._start_all({"general": Path("roster/general")})
    elif entry == "single":
        await cli._start_single(Path("roster/general"))
    else:
        checked = []
        monkeypatch.setattr(control, "check_private_key_absent", checked.append)
        listener = SimpleNamespace(close=AsyncMock())
        monkeypatch.setattr(control, "SessionRefusalListener", lambda **kwargs: listener)
        with pytest.raises(RuntimeError, match="composition-stop"):
            await control.run_control(
                Path("roster/general"), profile=control.KEY_ABSENT_STAGE, session_host="127.0.0.1"
            )
        assert checked == [control.KEY_ABSENT_STAGE]
        listener.close.assert_awaited_once()
    assert len(instances) == 1
    instances[0].shutdown.assert_awaited_once()
