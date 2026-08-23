"""Static privilege-boundary coverage for the restore-drill bootstrap path.

The checks intentionally inspect checked-in contracts only.  They do not run
``init-db.sql`` or contact PostgreSQL, so no role or database is modified.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
from pathlib import Path

import pytest

from butlers.testing.migration import init_db_sql_for_dbapi

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
_SCRIPTS_README = _REPO_ROOT / "scripts" / "README.md"
_SCHEMA_TOPOLOGY_DOC = _REPO_ROOT / "docs" / "data_and_storage" / "schema-topology.md"
_COMPOSE_FILE = _REPO_ROOT / "docker-compose.yml"


# ---------------------------------------------------------------------------
# Restore-script invocation detection
#
# PR #3708 removed the hand-run ``scripts/pg_restore.sh`` escape hatch from the
# operations doc.  The ban that replaced it pinned the *substring*
# ``pg_restore.sh``, so the doc could not name the path it was describing.  What
# has to stay banned is the runnable form: a command line an operator can copy
# out of the doc.
#
# Caught:
#   * a path-executable reference -- ``./scripts/pg_restore.sh``,
#     ``../scripts/pg_restore.sh``, ``~/butlers/scripts/pg_restore.sh``,
#     ``/opt/butlers/scripts/pg_restore.sh``
#   * an interpreter prefix -- ``bash scripts/pg_restore.sh``,
#     ``sh -x pg_restore.sh``
#   * the name followed by an argument-shaped token rather than by the next word
#     of a sentence: a flag, a path, a shell variable, a quote, a
#     ``<placeholder>``, or a dotted filename
#   * any line naming the script inside a fenced code block, runnable or not
#
# NOT caught, deliberately -- a narrowed boundary whose holes go unrecorded reads
# as more precise than it is:
#   * a bare or repo-relative mention in prose (``pg_restore.sh``,
#     ``scripts/pg_restore.sh``); permitting this is the point of the narrowing
#   * a four-space-indented code block or an HTML ``<pre>`` block.  This doc uses
#     neither, and indented blocks are indistinguishable from nested list items
#     without a full Markdown parse
#   * indirection -- ``SCRIPT=pg_restore.sh`` followed by ``./$SCRIPT``, or a
#     name assembled from fragments
#   * an invocation split so the name and its argument never share a line
#   * ``source pg_restore.sh`` / ``. pg_restore.sh``
#   * prose urging a hand-run restore without showing the command, and any
#     invocation of a renamed copy of the script
# ---------------------------------------------------------------------------

_RESTORE_SCRIPT_NAME = "pg_restore.sh"

# A token that reads as an argument rather than as the next word of a sentence.
_ARGUMENT_TOKEN = r"""(?:-{1,2}\w|[<{$"']|[.~]{0,2}/|\w[\w.-]*\.\w)"""

_RESTORE_SCRIPT_INVOCATION_PATTERNS = (
    # Path-executable reference: ./x, ../x, ~/x, /abs/x.
    re.compile(
        rf"(?<![\w.~/-])(?:\.{{1,2}}|~)?/(?:[\w.~-]+/)*{re.escape(_RESTORE_SCRIPT_NAME)}"
    ),
    # Interpreter prefix, with or without flags and a leading directory.
    re.compile(
        rf"\b(?:ba|da|k|z)?sh\b[ \t]+(?:-\S+[ \t]+)*(?:[\w.~/-]*/)?"
        rf"{re.escape(_RESTORE_SCRIPT_NAME)}"
    ),
    # The name followed by an argument instead of by prose.
    re.compile(rf"{re.escape(_RESTORE_SCRIPT_NAME)}[ \t]+{_ARGUMENT_TOKEN}"),
)


def _fenced_code_blocks(markdown: str) -> list[str]:
    """Return the body of every fenced code block in *markdown*."""
    blocks: list[str] = []
    fence: str | None = None
    body: list[str] = []
    for line in markdown.splitlines():
        stripped = line.lstrip()
        if fence is None:
            if stripped.startswith(("```", "~~~")):
                fence, body = stripped[:3], []
            continue
        if stripped.startswith(fence):
            blocks.append("\n".join(body))
            fence = None
            continue
        body.append(line)
    if fence is not None:  # Unterminated fence: treat the remainder as a block.
        blocks.append("\n".join(body))
    return blocks


def _restore_script_invocations(markdown: str) -> list[str]:
    """Return every runnable reference to the restore script in *markdown*.

    Naming the script in prose is not runnable and is not returned.  The comment
    above this helper enumerates both the caught and the uncaught shapes.
    """
    found: set[str] = set()
    for block in _fenced_code_blocks(markdown):
        found.update(
            line.strip() for line in block.splitlines() if _RESTORE_SCRIPT_NAME in line
        )
    for pattern in _RESTORE_SCRIPT_INVOCATION_PATTERNS:
        found.update(match.group(0) for match in pattern.finditer(markdown))
    return sorted(found)


_DESCRIPTIVE_RESTORE_SCRIPT_MENTIONS = (
    "The restore path is `pg_restore.sh`, and it is never driven by hand.",
    "`scripts/pg_restore.sh` audits ownership once the restore has completed.",
    "pg_restore.sh restores only to a named scratch database.",
    "Ownership is pinned by tests/scripts/test_pg_restore_definer_ownership.py.",
)

_RESTORE_SCRIPT_INVOCATIONS = (
    "./scripts/pg_restore.sh",
    "Run ./scripts/pg_restore.sh dump.sql.gz --target-db butlers_restore by hand.",
    "/opt/butlers/scripts/pg_restore.sh dump.sql.gz",
    "../scripts/pg_restore.sh",
    "~/butlers/scripts/pg_restore.sh",
    "bash scripts/pg_restore.sh dump.sql.gz",
    "sh -x pg_restore.sh /backups/latest.sql.gz",
    # Runnable with no argument and no leading ``./``: only the interpreter
    # prefix distinguishes these from prose.
    "bash scripts/pg_restore.sh",
    "sh -x scripts/pg_restore.sh",
    "pg_restore.sh <backup-file.sql.gz> --target-db <name>",
    "pg_restore.sh --env-file .env.production",
    "pg_restore.sh $BACKUP_FILE",
    "```bash\npg_restore.sh\n```",
)


def test_init_db_reserves_an_isolated_executor_without_widening_shared_roles() -> None:
    """REQ-database-security-006 keeps every normal login NOCREATEDB."""
    source = _INIT_DB.read_text(encoding="utf-8")

    assert "_restore_drill_executor_role TEXT := 'restore_drill_executor'" in source
    assert (
        "CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB"
        in source
    )
    assert "ALTER ROLE %I NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB" in source
    assert "GRANT USAGE ON SCHEMA public TO %I" in source
    assert "GRANT %I TO %I WITH SET TRUE" in source
    assert "ALTER TABLE restore_drill_executor.restore_drill_results" in source
    assert "GRANT EXECUTE ON FUNCTION restore_drill_executor.latest_result() TO %I" in source
    assert "REVOKE ALL PRIVILEGES ON TABLE restore_drill_executor.restore_drill_results" in source
    assert "CREATE OR REPLACE FUNCTION restore_drill_executor_admin.install_interface()" in source
    assert "restore-drill interface ownership is untrusted" in source
    assert "GRANT USAGE, CREATE ON SCHEMA restore_drill_executor TO %I" not in source
    assert (
        "GRANT EXECUTE ON FUNCTION restore_drill_executor_admin.finalize_interface() TO %I"
        not in source
    )
    assert (
        "GRANT EXECUTE ON FUNCTION restore_drill_executor_admin.install_interface() TO %I" in source
    )
    assert (
        "REVOKE EXECUTE ON FUNCTION restore_drill_executor_admin.finalize_interface() FROM %I"
        in source
    )
    runtime_roles_start = source.index("_all_runtime_roles TEXT[]")
    runtime_roles_end = source.index("];", runtime_roles_start)
    assert "restore_drill_executor" not in source[runtime_roles_start:runtime_roles_end]


def test_initial_bootstrap_preflight_precedes_every_ddl_or_dcl_mutation() -> None:
    """A future preflight mutation must fail static review before it can partially apply."""
    source = _INIT_DB.read_text(encoding="utf-8")
    marker = "INITIAL_READ_ONLY_BOOTSTRAP_PREFLIGHT"
    marker_offset = source.index(marker)
    assert source.count(marker) == 1

    preflight_start = source.index("DO $$", marker_offset)
    preflight_end = source.index("$$;", preflight_start) + len("$$;")
    preflight = source[preflight_start:preflight_end]
    assert "restore-drill admin bootstrap requires a cluster superuser" in preflight

    first_psql_directive = re.search(r"^\s*\\\S+.*$", source, re.MULTILINE)
    assert first_psql_directive is not None
    assert first_psql_directive.group().strip() == r"\set ON_ERROR_STOP on"
    assert first_psql_directive.start() < preflight_start

    mutator = re.compile(
        r"^\s*(?:CREATE|ALTER|GRANT|REVOKE|DROP|UPDATE|INSERT|DELETE|TRUNCATE)\b",
        re.MULTILINE,
    )
    first_mutator = mutator.search(source)
    assert first_mutator is not None
    assert first_mutator.start() > preflight_end


def test_dbapi_bootstrap_source_removes_only_the_psql_fail_fast_directive() -> None:
    """Testcontainers' direct cursor path cannot receive psql-only commands."""
    source = _INIT_DB.read_text(encoding="utf-8")

    assert init_db_sql_for_dbapi() == source.replace(r"\set ON_ERROR_STOP on" + "\n", "", 1)


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
    assert "BEGIN;" in input_text
    assert "COMMIT;" in input_text


def test_managed_provisioner_accepts_one_terminal_lf_without_passing_it_to_psql(
    tmp_path: Path,
) -> None:
    """The provisioner and executor share one unambiguous file-secret contract."""
    password = "test-terminal-lf-secret"
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text(password + "\n", encoding="utf-8")
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
    assert base64.b64encode(password.encode()).decode() in input_text
    assert base64.b64encode((password + "\n").encode()).decode() not in input_text


def test_migration_uses_the_fixed_bootstrap_owned_executor_result_authority() -> None:
    """The shared migration login invokes only a trusted fixed installer."""
    source = _MIGRATION.read_text(encoding="utf-8")
    init_source = _INIT_DB.read_text(encoding="utf-8")

    assert "_TRUSTED_BOOTSTRAP_INSTALLER_SQL" in source
    assert "restore_drill_executor_admin.install_interface" in source
    assert "SELECT {_ADMIN_INSTALLER}()" in source
    assert "restore-drill bootstrap installer is missing or untrusted" in source
    assert "installer.proowner = admin_schema.nspowner" in source
    assert "finalizer.proowner = admin_schema.nspowner" in source
    assert "bootstrap_owner.rolsuper" in source
    assert "installer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]" in source
    assert "finalizer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]" in source
    assert "audit_projection.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]" in source
    assert "bootstrap_owner.rolname <> current_user" not in source
    assert "CREATE TABLE restore_drill_executor.restore_drill_results" not in source
    assert "CREATE SCHEMA IF NOT EXISTS restore_drill_executor" not in source
    assert "public.restore_drill_executor_is_due" not in source
    assert "public.record_restore_drill_executor_result" not in source
    assert "attention_ledger" not in source
    assert (
        "CREATE OR REPLACE FUNCTION restore_drill_executor_admin.install_interface()" in init_source
    )
    assert (
        "restore-drill authority interface must be absent before fixed bootstrap installation"
        in init_source
    )
    assert "restore-drill interface ownership is untrusted" in init_source
    assert "restore-drill admin bootstrap requires a cluster superuser" in init_source
    assert "restore-drill admin schema is not owned by a trusted bootstrap superuser" in init_source
    assert (
        "restore-drill admin interface function is not owned by the bootstrap role" in init_source
    )
    assert "GRANT USAGE, CREATE ON SCHEMA restore_drill_executor TO %I" not in init_source
    assert (
        "GRANT EXECUTE ON FUNCTION restore_drill_executor_admin.finalize_interface() TO %I"
        not in init_source
    )
    assert "p_result IS NULL OR p_result NOT IN ('pass', 'fail')" in init_source
    assert "'backup_file', p_backup_name" not in init_source
    assert "p_table_count must not be negative" not in init_source
    assert "'table_count', p_table_count" not in init_source
    assert "compatibility input except p_result is inert" in init_source
    assert "restore_drill_executor_audit_writer" in init_source
    assert "SET search_path = pg_catalog, pg_temp" in init_source
    assert "SET search_path = pg_catalog, public, pg_temp" in init_source
    assert "write_audit_projection" in init_source
    assert "ALTER FUNCTION restore_drill_executor_admin.write_audit_projection(TEXT)" in init_source
    assert "OWNER TO restore_drill_executor_audit_writer" in init_source
    assert "PERFORM restore_drill_executor_admin.write_audit_projection(p_result)" in init_source
    assert (
        "GRANT INSERT ON TABLE public.audit_log TO restore_drill_executor_audit_writer"
        in init_source
    )
    assert (
        "GRANT INSERT ON TABLE public.audit_log TO restore_drill_executor_owner" not in init_source
    )


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
    assert "butlers deploy" in source
    assert "verify-full" in source
    assert "RESTORE_DRILL_EXECUTOR_SSLROOTCERT_SOURCE_FILE" in source
    assert "restore_drill_executor_ca.pem" in source
    assert "sslmode=require" in source
    assert "cluster-superuser bootstrap" in source
    assert "restore_drill_executor_audit_writer" in source
    assert "hostile `public.audit_log` trigger" in source
    assert "rolls back its ledger insert" in source
    assert "ALTER ROLE" not in source
    assert "CREATE DATABASE butlers_restore" not in source
    assert _restore_script_invocations(source) == []


@pytest.mark.parametrize("mention", _DESCRIPTIVE_RESTORE_SCRIPT_MENTIONS)
def test_ops_docs_may_name_the_restore_script_descriptively(mention: str) -> None:
    """Naming the restore path is precision, not an escape hatch."""
    assert _restore_script_invocations(mention) == []


@pytest.mark.parametrize("invocation", _RESTORE_SCRIPT_INVOCATIONS)
def test_ops_docs_may_not_show_a_runnable_restore_script_invocation(invocation: str) -> None:
    """REQ-database-security-006 keeps the hand-run escape hatch unreachable.

    This is the load-bearing half of the narrowing: it fails if the invocation
    ban is dropped or weakened back toward allowing a runnable command line.
    """
    assert _restore_script_invocations(invocation), f"invocation not caught: {invocation!r}"


def test_operations_doc_names_the_restore_script_it_describes() -> None:
    """The doc has to be able to say which path it is imposing a precondition on."""
    source = _OPERATIONS_DOC.read_text(encoding="utf-8")

    assert _RESTORE_SCRIPT_NAME in source
    assert _restore_script_invocations(source) == []


def test_bootstrap_docs_require_a_cluster_superuser_distinct_from_the_migration_user() -> None:
    """Operator docs cannot resurrect the shared-owner bootstrap escape hatch."""
    for source in (
        _SCRIPTS_README.read_text(encoding="utf-8"),
        _SCHEMA_TOPOLOGY_DOC.read_text(encoding="utf-8"),
        _COMPOSE_FILE.read_text(encoding="utf-8"),
    ):
        normalized_source = " ".join(source.split())
        assert "privileged cluster superuser" in normalized_source
        assert "butlers.connecting_user" in source
        assert "must not be the active bootstrap identity" in normalized_source
        assert "or the database owner" not in source
