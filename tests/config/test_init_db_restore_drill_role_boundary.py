"""Static privilege-boundary coverage for the restore-drill bootstrap path.

The checks intentionally inspect checked-in contracts only.  They do not run
``init-db.sql`` or contact PostgreSQL, so no role or database is modified.
"""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INIT_DB = _REPO_ROOT / "scripts" / "init-db.sql"
_PROVISIONER = _REPO_ROOT / "scripts" / "provision_restore_drill_executor.sh"
_MIGRATION = (
    _REPO_ROOT / "alembic" / "versions" / "core" / "core_196_restore_drill_executor_boundary.py"
)
_DASHBOARD_APP = _REPO_ROOT / "src" / "butlers" / "api" / "app.py"
_EXECUTOR = _REPO_ROOT / "src" / "butlers" / "jobs" / "restore_drill_executor.py"
_OPERATIONS_DOC = _REPO_ROOT / "docs" / "operations" / "backup-restore.md"


def test_init_db_reserves_an_isolated_executor_without_widening_shared_roles() -> None:
    """REQ-database-security-006 keeps every normal login NOCREATEDB."""
    source = _INIT_DB.read_text(encoding="utf-8")

    assert "_restore_drill_executor_role TEXT := 'restore_drill_executor'" in source
    assert (
        "CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB"
        in source
    )
    assert "ALTER ROLE %I NOCREATEDB" in source
    assert "GRANT USAGE ON SCHEMA public TO %I" in source
    assert "GRANT %I TO %I WITH SET TRUE" in source
    runtime_roles_start = source.index("_all_runtime_roles TEXT[]")
    runtime_roles_end = source.index("];", runtime_roles_start)
    assert "restore_drill_executor" not in source[runtime_roles_start:runtime_roles_end]


def test_managed_provisioner_reads_the_executor_password_only_from_its_private_file() -> None:
    """The checked-in bootstrap path must not embed or echo the executor secret."""
    source = _PROVISIONER.read_text(encoding="utf-8")

    assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" in source
    assert "restore_drill_executor" in source
    assert "ALTER ROLE restore_drill_executor" in source
    assert "CREATEDB" in source
    assert "POSTGRES_PASSWORD" not in source
    assert "DATABASE_URL" not in source
    assert "echo" not in source


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def test_managed_provisioner_rejects_multiline_password_before_running_psql(
    tmp_path: Path,
) -> None:
    """A newline must fail before client input can reinterpret it as psql syntax."""
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("first-line\nsecond-line", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    psql_called = tmp_path / "psql-called"
    _write_executable(
        bin_dir / "psql",
        '#!/bin/sh\ntouch "$PSQL_CALLED_MARKER"\ncat >/dev/null\n',
    )

    completed = subprocess.run(
        [_PROVISIONER],
        check=False,
        env={
            **os.environ,
            "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE": str(password_file),
            "PSQL_CALLED_MARKER": str(psql_called),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert not psql_called.exists()
    assert "single line" in completed.stderr


def test_managed_provisioner_passes_secret_to_psql_as_encoded_literal_data(tmp_path: Path) -> None:
    """The psql input must not interpolate the raw file secret into SQL/meta syntax."""
    password = "test quote' dollar$ punctuation!"
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text(password, encoding="utf-8")
    psql_input = tmp_path / "psql-input"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "psql",
        '#!/bin/sh\ncat > "$PSQL_INPUT_CAPTURE"\n',
    )

    completed = subprocess.run(
        [_PROVISIONER],
        check=False,
        env={
            **os.environ,
            "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE": str(password_file),
            "PSQL_INPUT_CAPTURE": str(psql_input),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    input_text = psql_input.read_text(encoding="utf-8")
    assert password not in input_text
    assert base64.b64encode(password.encode()).decode() in input_text
    assert input_text.startswith("\\set restore_drill_executor_password_b64 ")
    assert "\n\\unset restore_drill_executor_password_b64\n" in input_text
    assert "decode(:'restore_drill_executor_password_b64', 'base64')" in input_text


def test_migration_owns_fixed_search_path_executor_persistence_boundary() -> None:
    """The executor receives functions, not broad audit-table or schema grants."""
    source = _MIGRATION.read_text(encoding="utf-8")

    assert "SECURITY DEFINER" in source
    assert "CREATE SCHEMA IF NOT EXISTS restore_drill_executor" in source
    assert "ALTER FUNCTION restore_drill_executor.is_due(INTEGER)" in source
    assert "ALTER FUNCTION restore_drill_executor.record_result(" in source
    assert "OWNER TO restore_drill_executor_owner" in source
    assert "SET search_path = pg_catalog, public" in source
    assert "GRANT USAGE ON SCHEMA restore_drill_executor" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "public.restore_drill_executor_is_due" not in source
    assert "public.record_restore_drill_executor_result" not in source
    assert "GRANT SELECT, INSERT ON TABLE public.audit_log" in source
    assert "restore_drill_executor_owner" in source
    assert "GRANT SELECT ON TABLE public.audit_log TO restore_drill_executor" not in source
    assert "GRANT INSERT ON TABLE public.audit_log TO restore_drill_executor" not in source
    assert "attention_ledger" not in source


def test_dashboard_has_no_restore_drill_scheduler_or_shared_credential_launch_path() -> None:
    """REQ-database-security-006 keeps the privileged lifecycle out of the API."""
    dashboard_source = _DASHBOARD_APP.read_text(encoding="utf-8")
    executor_source = _EXECUTOR.read_text(encoding="utf-8")

    assert "run_restore_drill" not in dashboard_source
    assert "db_params_from_env" not in executor_source
    assert "DatabaseManager" not in executor_source


def test_operations_document_the_managed_boundary_without_a_live_workaround() -> None:
    """REQ-database-security-006: operators get no shared-role escape hatch."""
    source = _OPERATIONS_DOC.read_text(encoding="utf-8")

    assert "restore-drill-executor" in source
    assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" in source
    assert "single-executor" in source
    assert "live application database" in source
    assert "ALTER ROLE" not in source
    assert "CREATE DATABASE butlers_restore" not in source
    assert "pg_restore.sh" not in source
