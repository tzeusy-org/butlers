"""Static source coverage for the canonical DND authority boundary.

These checks deliberately do not execute ``init-db.sql`` or start PostgreSQL.
The companion integration suite must exercise the catalog/RLS/role contract in
an explicitly authorized database environment.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.sql.elements import TextClause

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INIT_DB = _REPO_ROOT / "scripts" / "init-db.sql"
_MIGRATION = (
    _REPO_ROOT / "alembic" / "versions" / "core" / "core_197_canonical_dnd_generation_guard.py"
)
_POSTGRES_INTEGRATION_TEST = (
    _REPO_ROOT / "tests" / "config" / "test_dnd_generation_guard_postgres.py"
)
_CONTEXT_PRODUCERS_INTEGRATION_TEST = _REPO_ROOT / "tests" / "jobs" / "test_context_producers.py"
_CONTEXT_BUS_INTEGRATION_TEST = _REPO_ROOT / "tests" / "core" / "test_context_bus.py"


def _load_core_197():
    spec = importlib.util.spec_from_file_location("core_197_dnd_boundary", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_dnd_postgres_integration_test():
    spec = importlib.util.spec_from_file_location(
        "dnd_generation_guard_postgres", _POSTGRES_INTEGRATION_TEST
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _function_source(source: str, function_name: str) -> str:
    start = source.index(f"def {function_name}(")
    next_function = source.find("\ndef ", start + 1)
    return source[start:] if next_function == -1 else source[start:next_function]


def _catalog_expectation_values(source: str) -> list[bool]:
    module = ast.parse(source)
    catalog_test = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_dnd_final_catalog_has_no_login_owner_force_rls_and_no_public_execute"
    )
    catalog_assertion = next(
        node
        for node in ast.walk(catalog_test)
        if isinstance(node, ast.Assert)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "catalog"
    )
    expected = catalog_assertion.test.comparators[0]
    assert isinstance(expected, ast.Tuple)
    return [ast.literal_eval(value) for value in expected.elts]


def test_dnd_boundary_uses_trusted_installer_not_migration_owned_ddl() -> None:
    source = _INIT_DB.read_text(encoding="utf-8")
    migration = _MIGRATION.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION dnd_generation_admin.install_interface()" in source
    assert "CREATE OR REPLACE FUNCTION dnd_generation_admin.finalize_interface()" in source
    assert "DND authority interface must be absent before fixed bootstrap installation" in source
    assert "the pre-DND table must have no user policies at" in source
    assert "entire known core shape rather than adopting a compatible-looking table" in source
    assert "constraint_row.contype = 'u'" in source
    assert "constraint_row.conname = 'user_context_confidence_check'" in source
    assert "index_row.indexprs IS NOT NULL" in source
    assert "idx_user_context_active_signals" in source
    assert source.count("LOCK TABLE public.user_context IN ACCESS EXCLUSIVE MODE") == 3
    assert "CREATE ROLE dnd_generation_owner" in source
    assert (
        "NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS" in source
    )
    assert "roleid = v_owner OR member = v_owner" in source
    assert "DND generation owner role is untrusted or has memberships" in source
    assert "ALTER TABLE public.user_context OWNER TO dnd_generation_owner" in source
    assert "ALTER TABLE public.user_context ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE public.user_context FORCE ROW LEVEL SECURITY" in source

    assert "_TRUSTED_FINALIZED_INTERFACE_SQL" in migration
    assert "_TRUSTED_BOOTSTRAP_INSTALLER_SQL" in migration
    assert 'op.execute(f"SELECT {_ADMIN_INSTALLER}()")' in migration
    assert "CREATE TABLE" not in migration
    assert "CREATE FUNCTION" not in migration
    assert "ALTER TABLE" not in migration


def test_dnd_installer_compares_the_index_catalog_with_int2vector_safe_shape_checks() -> None:
    """The pg_index key vector is not interchangeable with a smallint array."""
    source = _INIT_DB.read_text(encoding="utf-8")
    installer_start = source.index(
        "CREATE OR REPLACE FUNCTION dnd_generation_admin.install_interface()"
    )
    installer_end = source.index("$dnd_installer$;", installer_start)
    installer = source[installer_start:installer_end]

    assert "index_row.indnkeyatts = 1" in installer
    assert "index_row.indnatts = 1" in installer
    assert "index_row.indkey[0] = (" in installer
    assert "index_row.indkey = ARRAY[" not in installer


def test_core_197_catalog_probes_use_sqlalchemy_text_for_literal_like_patterns() -> None:
    """Migration catalog probes must not hand literal ``%`` patterns to DBAPI directly."""
    migration = _load_core_197()
    finalized = MagicMock()
    finalized.scalar_one.return_value = False
    installer = MagicMock()
    installer.scalar_one.return_value = True
    bind = MagicMock()
    bind.execute.side_effect = [finalized, installer]
    bind.exec_driver_sql.side_effect = AssertionError(
        "DBAPI-direct SQL is unsafe for LIKE patterns"
    )
    op = MagicMock()
    op.get_bind.return_value = bind

    with patch.object(migration, "op", op):
        migration.upgrade()

    statements = [call.args[0] for call in bind.execute.call_args_list]
    assert all(isinstance(statement, TextClause) for statement in statements)
    assert "LIKE '%signal_type%'" in statements[0].text
    assert len(statements) == 2
    bind.exec_driver_sql.assert_not_called()


def test_dnd_catalog_test_reads_sealed_functions_from_pg_proc_not_regprocedure() -> None:
    """Catalog assertions must not require USAGE on sealed DND schemas."""
    integration_test = _function_source(
        _POSTGRES_INTEGRATION_TEST.read_text(encoding="utf-8"),
        "test_dnd_final_catalog_has_no_login_owner_force_rls_and_no_public_execute",
    )

    assert (
        "dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure"
        not in integration_test
    )
    assert "dnd_generation_private.canonical_json(jsonb)'::regprocedure" not in integration_test
    assert "dnd_generation_admin.install_interface()'::regprocedure" not in integration_test
    assert "dnd_generation_admin.finalize_interface()'::regprocedure" not in integration_test
    assert "private_mutation.pronamespace = private_schema.oid" in integration_test
    assert "canonical_json.pronamespace = private_schema.oid" in integration_test
    assert "installer.pronamespace = admin_schema.oid" in integration_test
    assert "finalizer.pronamespace = admin_schema.oid" in integration_test
    assert "admin_function.pronamespace = admin_schema.oid" in integration_test


def test_dnd_catalog_expectation_includes_the_trusted_bootstrap_owner_proof() -> None:
    """The catalog tuple must retain the bootstrap owner's superuser assertion."""
    integration_test = _POSTGRES_INTEGRATION_TEST.read_text(encoding="utf-8")

    assert "bootstrap_owner.rolsuper" in integration_test
    expected = _catalog_expectation_values(integration_test)
    assert len(expected) == 27
    assert expected[16] is True


def test_dnd_mutation_replay_lookup_qualifies_the_receipt_column() -> None:
    """A PL/pgSQL result column must not collide with the receipt lookup column."""
    source = _INIT_DB.read_text(encoding="utf-8")
    private_start = source.index("CREATE FUNCTION dnd_generation_private.mutate(")
    gateway_start = source.index("CREATE FUNCTION public.context_dnd_mutate(", private_start)
    private_mutation = source[private_start:gateway_start]

    assert "FROM public.dnd_generation_mutations AS receipt" in private_mutation
    assert "WHERE receipt.mutation_id = p_mutation_id" in private_mutation
    assert "WHERE mutation_id = p_mutation_id" not in private_mutation
    assert "UPDATE public.dnd_generation_guard AS guard" in private_mutation
    assert "SET generation = guard.generation + 1" in private_mutation
    assert "RETURNING guard.generation INTO v_guard_generation" in private_mutation


def test_core_197_downgrade_delegates_to_a_trusted_privileged_bootstrap_rollback() -> None:
    """A core downgrade must not replace a managed rollback with an unconditional raise."""
    migration = _load_core_197()
    rollback = MagicMock()
    rollback.scalar_one.return_value = True
    bind = MagicMock()
    bind.execute.return_value = rollback
    op = MagicMock()
    op.get_bind.return_value = bind

    with patch.object(migration, "op", op):
        migration.downgrade()

    statements = [call.args[0] for call in bind.execute.call_args_list]
    assert len(statements) == 1
    assert isinstance(statements[0], TextClause)
    assert "dnd_generation_admin" in statements[0].text
    assert "rollback_interface" in statements[0].text
    assert "rolsuper" in statements[0].text
    op.execute.assert_called_once_with(f"SELECT {migration._ADMIN_ROLLBACK}()")


def test_core_197_rollback_catalog_proof_requires_exclusive_trusted_admin_control() -> None:
    """The delegating migration proves the rollback dependency chain before calling it."""
    migration = _load_core_197()
    rollback_proof = migration._TRUSTED_BOOTSTRAP_ROLLBACK_SQL

    assert "JOIN pg_roles AS configured_bootstrap_owner" in rollback_proof
    assert "configured_bootstrap_owner.oid = bootstrap_owner.oid" in rollback_proof
    assert "JOIN pg_proc AS finalizer" in rollback_proof
    assert "finalizer.prosecdef" in rollback_proof
    assert "acl.grantee <> bootstrap_owner.oid" in rollback_proof


def test_core_197_rollback_catalog_proof_reads_roles_from_bootstrap_configuration_row() -> None:
    """Catalog metadata and stored bootstrap roles must remain distinct."""
    migration = _load_core_197()
    rollback_proof = migration._TRUSTED_BOOTSTRAP_ROLLBACK_SQL

    assert "JOIN pg_class AS bootstrap_configuration_relation" in rollback_proof
    assert (
        "JOIN dnd_generation_admin.bootstrap_configuration AS bootstrap_configuration"
        in rollback_proof
    )
    assert "ON bootstrap_configuration.singleton" in rollback_proof
    assert (
        "configured_bootstrap_owner.rolname = bootstrap_configuration.bootstrap_role"
        in rollback_proof
    )
    assert "migration_role.rolname = bootstrap_configuration.migration_role" in rollback_proof
    assert "bootstrap_configuration_relation.relowner = bootstrap_owner.oid" in rollback_proof
    assert "bootstrap_configuration_relation.relacl" in rollback_proof


def test_core_197_treats_the_privileged_rollback_as_part_of_the_trusted_interface() -> None:
    """A missing rollback function must not look like a complete DND boundary."""
    migration = _load_core_197()

    assert "rollback_interface.proname = 'rollback_interface'" in (
        migration._TRUSTED_FINALIZED_INTERFACE_SQL
    )
    assert "rollback_interface.proname = 'rollback_interface'" in (
        migration._TRUSTED_BOOTSTRAP_INSTALLER_SQL
    )


def test_core_197_installer_allows_only_catalog_proven_trusted_bootstrap_reapply() -> None:
    """A managed superuser down/up uses the same fixed installer, not a test exception."""
    migration = _load_core_197()
    installer_proof = migration._TRUSTED_BOOTSTRAP_INSTALLER_SQL

    assert "JOIN pg_roles AS installer_operator" in installer_proof
    assert "installer_operator.rolname = current_user" in installer_proof
    assert "installer_operator.rolsuper" in installer_proof
    assert "NOT has_function_privilege(current_user, finalizer.oid, 'EXECUTE')" in installer_proof


def test_dnd_installer_requires_the_known_pre_guard_handoff_for_reversible_rollback() -> None:
    """Rollback can restore only a source-validated owner and ordinary RLS posture."""
    source = _INIT_DB.read_text(encoding="utf-8")
    installer_start = source.index(
        "CREATE OR REPLACE FUNCTION dnd_generation_admin.install_interface()"
    )
    installer_end = source.index("$dnd_installer$;", installer_start)
    installer = source[installer_start:installer_end]

    assert "SELECT migration_role, bootstrap_role" in installer
    assert "migration_role.rolname = v_migration_role" in installer
    assert "relation.relrowsecurity OR relation.relforcerowsecurity" in installer
    assert "recorded pre-guard ownership and ordinary RLS posture" in installer
    assert installer.index(
        "LOCK TABLE public.user_context IN ACCESS EXCLUSIVE MODE"
    ) < installer.index("recorded pre-guard ownership and ordinary RLS posture")


def test_dnd_privileged_rollback_refuses_receipts_and_restores_the_pre_guard_handoff() -> None:
    """Only an unused guard may be removed; receipt-bearing state remains fail-closed."""
    source = _INIT_DB.read_text(encoding="utf-8")
    rollback_start = source.index(
        "CREATE OR REPLACE FUNCTION dnd_generation_admin.rollback_interface()"
    )
    rollback_end = source.index(
        "REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.rollback_interface() FROM PUBLIC;",
        rollback_start,
    )
    rollback = source[rollback_start:rollback_end]

    assert "SECURITY DEFINER" in rollback
    assert "WHERE rolname = session_user" in rollback
    assert "IF EXISTS (SELECT 1 FROM public.dnd_generation_mutations)" in rollback
    assert "v_generation <> 0" in rollback
    assert "LOCK TABLE public.user_context IN ACCESS EXCLUSIVE MODE" in rollback
    assert "ALTER TABLE public.user_context NO FORCE ROW LEVEL SECURITY" in rollback
    assert "ALTER TABLE public.user_context DISABLE ROW LEVEL SECURITY" in rollback
    assert "DROP TABLE public.dnd_generation_mutations" in rollback
    assert "DROP TABLE public.dnd_generation_guard" in rollback
    assert "DROP TABLE public.user_context" not in rollback
    assert "DELETE FROM public.user_context" not in rollback
    assert "TRUNCATE TABLE public.user_context" not in rollback
    assert "DROP ROLE dnd_generation_owner" not in rollback
    assert "ALTER TABLE public.user_context OWNER TO %I" in rollback
    assert "GRANT EXECUTE ON FUNCTION dnd_generation_admin.install_interface() TO %I" in rollback
    first_receipt_check = rollback.index(
        "IF EXISTS (SELECT 1 FROM public.dnd_generation_mutations)"
    )
    trusted_finalize = rollback.index("PERFORM dnd_generation_admin.finalize_interface()")
    first_destructive_ddl = rollback.index("DROP FUNCTION public.context_dnd_mutate")
    assert first_receipt_check < trusted_finalize < first_destructive_ddl


def test_dnd_gateway_checks_active_role_before_private_definer() -> None:
    source = _INIT_DB.read_text(encoding="utf-8")

    assert "CREATE FUNCTION public.context_dnd_mutate(" in source
    assert "SECURITY INVOKER" in source
    assert "IF current_user = 'butler_general_rw'" in source
    assert "ELSIF current_user = 'butler_switchboard_rw'" in source
    assert "CREATE FUNCTION dnd_generation_private.mutate(" in source
    assert "CREATE FUNCTION dnd_generation_private.canonical_json(p_document JSONB)" in source
    assert "SECURITY DEFINER" in source
    assert "current_setting('role', true)" in source
    assert "\"normalize\"(p_value, 'NFC')" in source
    assert "DND metadata has duplicate NFC-normalized keys" in source
    assert "RETURN trim_scale((p_document #>> '{}')::numeric)::text" in source
    assert "DND writer does not match the active runtime role" in source
    assert "polname NOT IN (" in source
    assert "REVOKE ALL PRIVILEGES ON FUNCTION public.context_dnd_mutate" in source
    assert "REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_private.mutate" in source


def test_dnd_receipt_schema_is_content_minimizing_and_role_catalog_gated() -> None:
    source = _INIT_DB.read_text(encoding="utf-8")
    migration = _MIGRATION.read_text(encoding="utf-8")

    audit_start = source.index("CREATE TABLE public.dnd_generation_mutations")
    audit_end = source.index(
        "CREATE UNIQUE INDEX dnd_generation_mutations_generation_key", audit_start
    )
    audit = source[audit_start:audit_end]
    assert "mutation_id UUID PRIMARY KEY" in audit
    assert "semantic_fingerprint_version" in audit
    assert "semantic_fingerprint TEXT NOT NULL" in audit
    assert "value TEXT" not in audit
    assert "metadata JSONB" not in audit

    assert "relrowsecurity" in migration
    assert "relforcerowsecurity" in migration
    assert "NOT dnd_owner.rolcanlogin" in migration
    assert "NOT dnd_owner.rolbypassrls" in migration
    assert "migration_role.rolname = current_user" in migration
    assert "acl.grantee = migration_role.oid" in migration
    assert "effective inherited gateway" in migration
    assert "general_runtime.rolname = 'butler_general_rw'" in migration
    assert "switchboard_runtime.rolname = 'butler_switchboard_rw'" in migration
    assert "DND authority ACL finalization is incomplete" in source
    assert "REVOKE DELETE ON TABLE public.user_context" in source


def test_dnd_correlation_is_derived_from_its_opaque_mutation_identity() -> None:
    """Neither audit nor SQL mutation API may accept free-form correlation text."""
    source = _INIT_DB.read_text(encoding="utf-8")
    audit_start = source.index("CREATE TABLE public.dnd_generation_mutations")
    audit_end = source.index(
        "CREATE UNIQUE INDEX dnd_generation_mutations_generation_key", audit_start
    )
    audit = source[audit_start:audit_end]
    private_start = source.index("CREATE FUNCTION dnd_generation_private.mutate(")
    gateway_start = source.index("CREATE FUNCTION public.context_dnd_mutate(", private_start)
    private_mutation = source[private_start:gateway_start]
    installer_end = source.index(
        "REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin", gateway_start
    )
    dnd_gateway = source[gateway_start:installer_end]

    assert "correlation ~ '^dnd-action:" in audit
    assert "p_correlation" not in private_mutation
    assert "p_correlation" not in dnd_gateway
    assert "v_correlation := 'dnd-action:' || p_mutation_id::text" in private_mutation


def test_dnd_finalizer_preserves_the_existing_optional_calendar_permission_matrix() -> None:
    """DND finalization must not widen calendar's pre-existing read-only role."""
    source = _INIT_DB.read_text(encoding="utf-8")
    finalizer_start = source.index(
        "CREATE OR REPLACE FUNCTION dnd_generation_admin.finalize_interface()"
    )
    installer_start = source.index(
        "CREATE OR REPLACE FUNCTION dnd_generation_admin.install_interface()", finalizer_start
    )
    finalizer = source[finalizer_start:installer_start]

    assert "'butler_calendar_rw'" not in finalizer
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE public.user_context TO %I" in finalizer
    assert "GRANT SELECT ON TABLE public.dnd_generation_guard TO %I" in finalizer


@pytest.mark.parametrize(
    ("result_kwargs", "expected_result", "materialization_event"),
    [
        ({"fetch_one": True}, "receipt", "receipt-one"),
        ({"return_rowcount": True}, 0, "rowcount"),
    ],
)
def test_dnd_role_results_materialize_before_connection_teardown(
    result_kwargs: dict[str, bool], expected_result: str | int, materialization_event: str
) -> None:
    """A real-role result cannot outlive the SQLAlchemy connection that owns it."""
    integration_test = _load_dnd_postgres_integration_test()
    events: list[str] = []

    class Result:
        def one(self) -> str:
            events.append("receipt-one")
            assert connection.open
            return "receipt"

        @property
        def rowcount(self) -> int:
            events.append("rowcount")
            assert connection.open
            return 0

    class Connection:
        open = False

        def execute(self, statement, params=None):
            assert self.open
            sql = str(statement).strip()
            if sql.startswith("SET ROLE"):
                events.append("set-role")
                return None
            if sql == "RESET ROLE":
                events.append("reset-role")
                return None
            events.append("query")
            return Result()

    class ConnectionContext:
        def __enter__(self):
            events.append("connection-enter")
            connection.open = True
            return connection

        def __exit__(self, exc_type, exc_value, traceback):
            events.append("connection-exit")
            connection.open = False
            return False

    class Engine:
        def connect(self):
            events.append("connect")
            return ConnectionContext()

        def dispose(self) -> None:
            events.append("dispose")

    connection = Connection()
    with patch.object(integration_test, "create_engine", return_value=Engine()):
        result = integration_test._execute_as_role(
            "postgresql://example.invalid/test",
            "butler_general_rw",
            "SELECT receipt"
            if result_kwargs.get("fetch_one", False)
            else "UPDATE user_context SET value = 'ignored'",
            **result_kwargs,
        )

    assert result == expected_result
    assert events == [
        "connect",
        "connection-enter",
        "set-role",
        "query",
        materialization_event,
        "reset-role",
        "connection-exit",
        "dispose",
    ]


def test_dnd_postgres_suite_has_no_inline_result_consumption_after_role_teardown() -> None:
    """Role-bound query results must be materialized by the helper itself."""
    tree = ast.parse(_POSTGRES_INTEGRATION_TEST.read_text(encoding="utf-8"))
    stale_result_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "one"
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == "_execute_as_role"
    ]

    assert stale_result_reads == []


def test_context_producer_fixture_resets_only_non_dnd_rows_without_truncate() -> None:
    """Fixture cleanup must respect the DND table-level privilege boundary."""
    source = _CONTEXT_PRODUCERS_INTEGRATION_TEST.read_text(encoding="utf-8")
    init_db = _INIT_DB.read_text(encoding="utf-8")
    finalizer_start = init_db.index(
        "CREATE OR REPLACE FUNCTION dnd_generation_admin.finalize_interface()"
    )
    installer_start = init_db.index(
        "CREATE OR REPLACE FUNCTION dnd_generation_admin.install_interface()", finalizer_start
    )
    finalizer = init_db[finalizer_start:installer_start]

    assert "TRUNCATE public.user_context" not in source
    assert "async def _clear_non_dnd_context" in source
    assert "UPDATE public.user_context" in source
    assert "SET superseded_at = now()" in source
    assert "WHERE signal_type <> 'dnd'" in source
    assert "await _clear_non_dnd_context(p)" in source
    assert "await _clear_non_dnd_context(pool)" in source
    assert "REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.user_context FROM %I" in finalizer


def test_context_bus_fixture_resets_only_non_dnd_rows_without_truncate() -> None:
    """A fresh context-bus test DB still must not bypass the finalizer ACL."""
    source = _CONTEXT_BUS_INTEGRATION_TEST.read_text(encoding="utf-8")
    init_db = _INIT_DB.read_text(encoding="utf-8")
    finalizer_start = init_db.index(
        "CREATE OR REPLACE FUNCTION dnd_generation_admin.finalize_interface()"
    )
    installer_start = init_db.index(
        "CREATE OR REPLACE FUNCTION dnd_generation_admin.install_interface()", finalizer_start
    )
    finalizer = init_db[finalizer_start:installer_start]

    assert "TRUNCATE public.user_context" not in source
    assert "async def _clear_non_dnd_context" in source
    assert "UPDATE public.user_context" in source
    assert "SET superseded_at = now()" in source
    assert "WHERE signal_type <> 'dnd'" in source
    assert "await _clear_non_dnd_context(p)" in source
    assert "await _clear_non_dnd_context(pool)" in source
    assert "REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.user_context FROM %I" in finalizer


def test_dnd_postgres_suite_treats_a_filtered_direct_update_as_no_effect() -> None:
    """RLS can deny UPDATE by filtering its target rows instead of raising an error."""
    gateway_test = _function_source(
        _POSTGRES_INTEGRATION_TEST.read_text(encoding="utf-8"),
        "test_dnd_gateway_replay_and_real_role_denials",
    )

    direct_update_start = gateway_test.index("UPDATE public.user_context")
    direct_update_end = gateway_test.index(
        "\n\n    with pytest.raises(DBAPIError):", direct_update_start
    )
    direct_update = gateway_test[direct_update_start:direct_update_end]

    assert "return_rowcount=True" in direct_update
    assert "assert direct_dnd_update_rowcount == 0" in gateway_test
