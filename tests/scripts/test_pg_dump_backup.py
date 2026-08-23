"""Contract tests for ``deploy/backup/pg_dump.sh`` (bu-e1410).

The nightly backup dumps as ``$POSTGRES_USER`` — the shared migration/runtime
login that ``scripts/init-db.sql`` deliberately fences away from the
trusted-bootstrap control plane.  ``pg_dump`` takes ``LOCK TABLE`` over every
relation in scope before writing a byte, so one unreadable relation aborts the
whole dump; because the script pipes through gzip under ``set -o pipefail`` with
a cleanup trap, a failing run leaves *no file at all*.  Absence, not corruption,
is the failure shape — and absence is the one that goes unnoticed.

Three things are pinned here:

1. :func:`test_exclusion_set_matches_the_fenced_objects_exactly` — the declared
   exclusion set equals, in both directions, the set of relations the dump role
   genuinely cannot read on a real bootstrapped database.  A new fenced schema
   fails this test instead of silently killing the nightly backup; an exclusion
   that covers a *readable* relation fails it too, so the set can never be
   quietly widened to make a red run go green.
2. :func:`test_script_produces_a_verifiable_artifact` — the real script, run
   from the real ``postgres:17-alpine`` sidecar image against a real
   bootstrapped database, actually publishes a non-empty, gzip-clean dump that
   contains application data and none of the fenced objects.
3. :func:`test_failed_run_says_so_and_publishes_nothing` — a failing dump exits
   non-zero, leaves the backup directory empty, and says ``FAILED`` on stderr
   rather than ending on a stray exit code.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "deploy" / "backup" / "pg_dump.sh"

#: The image the ``backup-cron`` sidecar runs in docker-compose.yml.  Tests use
#: the same one so the pg_dump major version matches production exactly; the
#: host's client version is irrelevant and often too old for a PG17 server.
_BACKUP_IMAGE = "postgres:17-alpine"

docker_available = shutil.which("docker") is not None


def _read_exclusion_set() -> tuple[set[str], set[str]]:
    """Parse the schema/table exclusion sets declared in the backup script."""
    source = _SCRIPT.read_text(encoding="utf-8")

    def _one(name: str) -> set[str]:
        match = re.search(rf'^{name}="([^"]*)"$', source, re.MULTILINE)
        assert match is not None, f"{name} assignment not found in {_SCRIPT}"
        return set(match.group(1).split())

    return _one("BACKUP_EXCLUDE_SCHEMAS"), _one("BACKUP_EXCLUDE_TABLES")


# ---------------------------------------------------------------------------
# Pure-unit guards (no Docker)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_script_never_enables_row_security() -> None:
    """``--enable-row-security`` would trade a loud failure for a silent one.

    With row security left off (the pg_dump default), a table whose policies
    hide rows from the dump role raises an error and the run fails visibly.
    Turning it on makes that same dump succeed while quietly omitting exactly
    those rows — a backup that lies about its own completeness.
    """
    code = [
        line
        for line in _SCRIPT.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert not [line for line in code if "--enable-row-security" in line]


@pytest.mark.unit
def test_failed_run_says_so_and_publishes_nothing(tmp_path: Path) -> None:
    """A dump that fails must exit non-zero, say ``FAILED``, and leave no file.

    This is the whole point of bu-e1410: the failing run's only trace is its
    log line, so that line has to be unmistakable.  A stub ``pg_dump`` on PATH
    stands in for any dump-time failure (permission denied, unreachable host,
    a killed process) — the script's behaviour afterwards is what is pinned.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    stub = fake_bin / "pg_dump"
    stub.write_text('#!/bin/sh\necho "stub failure" >&2\nexit 1\n', encoding="utf-8")
    stub.chmod(0o755)

    backup_dir = tmp_path / "backups"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BACKUP_DIR": str(backup_dir),
    }
    result = subprocess.run(
        ["sh", str(_SCRIPT)], env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode != 0
    assert "FAILED" in result.stderr
    assert list(backup_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Database-backed contract
# ---------------------------------------------------------------------------

#: Every relation the dump role cannot read, computed the same way pg_dump's
#: own ``LOCK TABLE`` + ``COPY`` would discover it: no schema USAGE, no table
#: SELECT, or row-level security that applies to this role while pg_dump runs
#: with ``row_security = off`` (which it does unless --enable-row-security is
#: passed, and it never is — see the unit guard above).
_FENCED_RELATIONS_SQL = """
SELECT n.nspname, c.relname
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'm', 'S')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\\_%'
  AND (
        NOT has_schema_privilege(current_user, n.oid, 'USAGE')
        OR NOT has_table_privilege(current_user, c.oid, 'SELECT')
        OR (
            c.relrowsecurity
            AND (
                c.relforcerowsecurity
                OR c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = current_user)
            )
        )
      )
ORDER BY 1, 2
"""

#: Every relation in scope for the dump at all, used to prove that nothing the
#: dump role *can* read is swept up by the exclusion set.
_ALL_RELATIONS_SQL = """
SELECT n.nspname, c.relname
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'm', 'S')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\\_%'
ORDER BY 1, 2
"""


@pytest.fixture(scope="module")
def bootstrapped_db_url(postgres_container) -> str:
    """A database bootstrapped by the real init-db.sql and migrated to core@head.

    This is the production shape the backup runs against: the trusted-bootstrap
    fences are installed by the checked-in ``scripts/init-db.sql`` (not a
    hand-written ACL approximation) and the returned URL is the ordinary,
    non-privileged migration login the backup-cron sidecar uses.
    """
    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@head")
    return db_url


def _fetch(db_url: str, sql: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return {f"{row[0]}.{row[1]}" for row in conn.execute(text(sql))}
    finally:
        engine.dispose()


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_exclusion_set_matches_the_fenced_objects_exactly(bootstrapped_db_url: str) -> None:
    """The declared exclusion set is exactly the set of unreadable relations.

    Both directions matter.  Under-excluding aborts the nightly dump and leaves
    no file; over-excluding drops data the dump role could have backed up, and
    does it silently.  Deciding either way is a backup-completeness decision
    that belongs in the script's header, not in a quiet edit here.
    """
    excluded_schemas, excluded_tables = _read_exclusion_set()
    fenced = _fetch(bootstrapped_db_url, _FENCED_RELATIONS_SQL)
    everything = _fetch(bootstrapped_db_url, _ALL_RELATIONS_SQL)

    def _is_excluded(qualified: str) -> bool:
        schema, _, _table = qualified.partition(".")
        return schema in excluded_schemas or qualified in excluded_tables

    missed = sorted(rel for rel in fenced if not _is_excluded(rel))
    assert not missed, (
        "These relations are fenced away from the backup role but are not "
        "excluded, so the nightly pg_dump aborts and publishes no file at all: "
        f"{missed}. Decide explicitly whether each one belongs in the backup, "
        f"then record the decision in {_SCRIPT.relative_to(_REPO_ROOT)}."
    )

    over_excluded = sorted(rel for rel in everything - fenced if _is_excluded(rel))
    assert not over_excluded, (
        "These relations are readable by the backup role but the exclusion set "
        f"drops them from every backup: {over_excluded}. Narrow the exclusion "
        "set, or state in the script header why this data is not backed up."
    )

    unused_schemas = sorted(
        s for s in excluded_schemas if not any(r.startswith(f"{s}.") for r in fenced)
    )
    unused_tables = sorted(t for t in excluded_tables if t not in fenced)
    assert not unused_schemas and not unused_tables, (
        "These exclusions no longer correspond to anything fenced and are now "
        f"only hiding data: schemas={unused_schemas} tables={unused_tables}."
    )


def _run_backup_script(
    db_url: str, backup_dir: Path, host_port: str
) -> subprocess.CompletedProcess:
    """Run the real script in the real sidecar image against *db_url*."""
    parsed = urlparse(db_url)
    env = {**os.environ, "PGPASSWORD_FOR_TEST": parsed.password or ""}
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--add-host=host.docker.internal:host-gateway",
            "-v",
            f"{_SCRIPT}:/backup/pg_dump.sh:ro",
            "-v",
            f"{backup_dir}:/backups",
            "-e",
            "POSTGRES_HOST=host.docker.internal",
            "-e",
            f"POSTGRES_PORT={host_port}",
            "-e",
            f"POSTGRES_USER={parsed.username}",
            # Forwarded by name, not by value, so the generated test password
            # never lands in this process' argv.
            "-e",
            "PGPASSWORD_FOR_TEST",
            "-e",
            f"POSTGRES_DB={(parsed.path or '').lstrip('/')}",
            "-e",
            "BACKUP_DIR=/backups",
            _BACKUP_IMAGE,
            "sh",
            "-c",
            'POSTGRES_PASSWORD="$PGPASSWORD_FOR_TEST" sh /backup/pg_dump.sh',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_script_produces_a_verifiable_artifact(
    bootstrapped_db_url: str, postgres_container, tmp_path: Path
) -> None:
    """The real script publishes one non-empty, gzip-clean, useful dump.

    Asserting on the artifact's *contents* rather than its mere existence is
    what makes this a backup test instead of a smoke test: a dump that is
    published but has quietly lost the application tables is no better than the
    absent file this bead is about.
    """
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    result = _run_backup_script(
        bootstrapped_db_url, backup_dir, str(postgres_container.get_exposed_port(5432))
    )
    assert result.returncode == 0, f"backup run failed:\n{result.stdout}\n{result.stderr}"

    artifacts = sorted(backup_dir.glob("butlers_*.sql.gz"))
    assert len(artifacts) == 1, f"expected exactly one artifact, got {artifacts}"
    assert not list(backup_dir.glob("*.tmp"))

    with gzip.open(artifacts[0], "rt", encoding="utf-8", errors="replace") as handle:
        dump = handle.read()

    # Ordinary application data is present ...
    assert "CREATE TABLE public.entities" in dump
    assert "CREATE TABLE public.sessions" in dump
    # ... and every fenced object stayed out.
    excluded_schemas, excluded_tables = _read_exclusion_set()
    for schema in excluded_schemas:
        assert f"CREATE SCHEMA {schema};" not in dump
    for qualified in excluded_tables:
        assert f"CREATE TABLE {qualified} " not in dump


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_unfenced_dump_would_fail_without_the_exclusion_set(
    bootstrapped_db_url: str, postgres_container, tmp_path: Path
) -> None:
    """Guard the guard: without the exclusions the dump really does die.

    If a future init-db.sql stops fencing these objects, the exclusion set
    becomes dead weight that silently narrows the backup.  This test fails then,
    which is the moment to delete the exclusions rather than carry them forever.
    """
    parsed = urlparse(bootstrapped_db_url)
    env = {**os.environ, "PGPASSWORD_FOR_TEST": parsed.password or ""}
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--add-host=host.docker.internal:host-gateway",
            "-e",
            "PGPASSWORD_FOR_TEST",
            _BACKUP_IMAGE,
            "sh",
            "-c",
            'PGPASSWORD="$PGPASSWORD_FOR_TEST" pg_dump '
            "--host=host.docker.internal "
            f"--port={postgres_container.get_exposed_port(5432)} "
            f"--username={parsed.username} "
            f"--dbname={(parsed.path or '').lstrip('/')} "
            "--format=plain --no-password > /dev/null",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "permission denied" in result.stderr
