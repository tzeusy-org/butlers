"""Contract tests for ``deploy/backup/pg_dump.sh`` (bu-e1410).

The nightly backup dumps as ``$POSTGRES_USER`` — the shared migration/runtime
login that ``scripts/init-db.sql`` deliberately fences away from the
trusted-bootstrap control plane.  ``pg_dump`` takes ``LOCK TABLE`` over every
relation in scope before writing a byte, so one unreadable relation aborts the
whole dump. The script captures the producer status explicitly and keeps a
partial gzip behind a temporary name, so a failing run leaves *no file at all*.
Absence, not corruption, is the failure shape — and absence is the one that
goes unnoticed.

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
   non-zero, publishes no artifact, and says ``FAILED`` on stderr rather than
   ending on a stray exit code.  It does leave one thing behind: the run
   receipt (bu-xrqyu), because "no artifact" is indistinguishable from "not due
   yet" to anyone reading the directory afterwards.  What the receipt contains
   is pinned in ``tests/scripts/test_pg_dump_run_sentinel.py``.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
import time
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


_FAKE_PSQL_SNAPSHOT_HOLDER = """#!/bin/sh
set -eu

output_file=""
while IFS= read -r line; do
  case "${line}" in
    "\\\\o "*)
      output_file=${line#"\\\\o "}
      ;;
    "\\\\o")
      case "${output_file}" in
        */id) printf 'ABCDEF-012345\\n' > "${output_file}" ;;
        */policy-count) printf '3\\n' > "${output_file}" ;;
      esac
      output_file=""
      ;;
  esac
done
"""


_PSQL_SNAPSHOT_BARRIER = """#!/bin/sh
set -eu

real_psql=/usr/local/bin/psql
has_command=0
for arg in "$@"; do
  if [ "${arg}" = "-c" ]; then
    has_command=1
    break
  fi
done
if [ "${has_command}" -eq 1 ] \
  || [ -z "${BACKUP_TEST_SNAPSHOT_READY:-}" ] \
  || [ -z "${BACKUP_TEST_SNAPSHOT_RELEASE:-}" ]; then
  exec "${real_psql}" "$@"
fi

proxy_dir="$(mktemp -d)"
proxy_fifo="${proxy_dir}/input"
psql_pid=""
cleanup() {
  exec 3>&- 2>/dev/null || true
  if [ -n "${psql_pid}" ]; then
    kill "${psql_pid}" 2>/dev/null || true
    wait "${psql_pid}" 2>/dev/null || true
  fi
  rm -rf "${proxy_dir}"
}
trap cleanup EXIT

mkfifo "${proxy_fifo}"
"${real_psql}" "$@" < "${proxy_fifo}" &
psql_pid=$!
exec 3>"${proxy_fifo}"
while IFS= read -r line; do
  case "${line}" in
    *"SELECT pg_export_snapshot();"*)
      : > "${BACKUP_TEST_SNAPSHOT_READY}"
      while [ ! -e "${BACKUP_TEST_SNAPSHOT_RELEASE}" ]; do
        sleep 0.01
      done
      ;;
  esac
  printf '%s\\n' "${line}" >&3
done
exec 3>&-
if wait "${psql_pid}"; then
  psql_pid=""
  exit 0
fi
wait_status=$?
psql_pid=""
exit "${wait_status}"
"""

docker_available = shutil.which("docker") is not None


_EXPECTED_SCOPED_DATA_TABLES = {
    "public.cost_claims",
    "public.cost_claim_resolutions",
    "public.cost_claim_events",
}


def _read_backup_sets() -> tuple[set[str], set[str], set[str]]:
    """Parse the exclusion and included-RLS sets declared in the backup script."""
    source = _SCRIPT.read_text(encoding="utf-8")

    def _one(name: str) -> set[str]:
        match = re.search(rf'^{name}="([^"]*)"$', source, re.MULTILINE)
        assert match is not None, f"{name} assignment not found in {_SCRIPT}"
        return set(match.group(1).split())

    return (
        _one("BACKUP_EXCLUDE_SCHEMAS"),
        _one("BACKUP_EXCLUDE_TABLES"),
        _one("BACKUP_SCOPED_DATA_TABLES"),
    )


# ---------------------------------------------------------------------------
# Pure-unit guards (no Docker)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_script_keeps_pg_dump_fail_loud_and_scopes_the_rls_data_path() -> None:
    """The ordinary dump never opts globally into policy-filtered output."""
    code = [
        line
        for line in _SCRIPT.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert not [line for line in code if "--enable-row-security" in line]
    _excluded_schemas, _excluded_tables, scoped_data_tables = _read_backup_sets()
    assert scoped_data_tables == _EXPECTED_SCOPED_DATA_TABLES
    assert any('--snapshot="${BACKUP_SNAPSHOT}"' in line for line in code)
    for table in scoped_data_tables:
        assert any('"--exclude-table-data=${table}"' in line for line in code)


@pytest.mark.unit
def test_failed_run_says_so_and_publishes_nothing(tmp_path: Path) -> None:
    """A dump that fails must exit non-zero, say ``FAILED``, and publish no dump.

    This is the whole point of bu-e1410: the failing run publishes nothing, so
    its log line has to be unmistakable -- and of bu-xrqyu: a log line nobody
    reads is not a signal, so the run also records its outcome on disk.  The
    DB-free stubs complete the snapshot handshake, then make ``pg_dump`` fail
    as any dump-time error could (permission denied, unreachable host, killed
    process) — the script's behaviour afterwards is what is pinned.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    stub = fake_bin / "pg_dump"
    stub.write_text('#!/bin/sh\necho "stub failure" >&2\nexit 1\n', encoding="utf-8")
    stub.chmod(0o755)
    psql_stub = fake_bin / "psql"
    psql_stub.write_text(_FAKE_PSQL_SNAPSHOT_HOLDER, encoding="utf-8")
    psql_stub.chmod(0o755)

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
    assert list(backup_dir.glob("butlers_*.sql.gz")) == []
    # The one trace a failed run does leave, and must (bu-xrqyu): an artifact
    # that was never published looks exactly like one that was not due yet.
    assert json.loads((backup_dir / "last_run.json").read_text())["result"] == "failed"


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


def _fetch_rows(db_url: str, sql: str) -> list[tuple]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return [tuple(row) for row in conn.execute(text(sql))]
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
    excluded_schemas, excluded_tables, scoped_data_tables = _read_backup_sets()
    fenced = _fetch(bootstrapped_db_url, _FENCED_RELATIONS_SQL)
    everything = _fetch(bootstrapped_db_url, _ALL_RELATIONS_SQL)

    def _is_excluded(qualified: str) -> bool:
        schema, _, _table = qualified.partition(".")
        return schema in excluded_schemas or qualified in excluded_tables

    missed = sorted(
        rel for rel in fenced if not _is_excluded(rel) and rel not in scoped_data_tables
    )
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

    assert scoped_data_tables <= fenced
    policy_rows = _fetch_rows(
        bootstrapped_db_url,
        """
        SELECT n.nspname || '.' || c.relname,
               p.polpermissive,
               p.polcmd,
               p.polroles = ARRAY[0::oid],
               pg_get_expr(p.polqual, p.polrelid)
        FROM pg_policy AS p
        JOIN pg_class AS c ON c.oid = p.polrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname || '.' || c.relname IN (
            'public.cost_claims',
            'public.cost_claim_resolutions',
            'public.cost_claim_events'
        )
          AND p.polcmd IN ('r', '*')
        ORDER BY 1
        """,
    )
    assert policy_rows == [
        (table, True, "r", True, "true") for table in sorted(_EXPECTED_SCOPED_DATA_TABLES)
    ], (
        "Every RLS table admitted to the backup must expose every row through one "
        f"permissive PUBLIC SELECT policy; got {policy_rows}."
    )


def _backup_command(
    db_url: str,
    backup_dir: Path,
    host_port: str,
    *,
    extra_env: dict[str, str] | None = None,
    extra_mounts: list[tuple[Path, str]] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Build the isolated sidecar command used by both foreground and race tests."""
    parsed = urlparse(db_url)
    env = {**os.environ, "PGPASSWORD_FOR_TEST": parsed.password or ""}
    command = [
        "docker",
        "run",
        "--rm",
        "--add-host=host.docker.internal:host-gateway",
        "-v",
        f"{_SCRIPT}:/backup/pg_dump.sh:ro",
        "-v",
        f"{backup_dir}:/backups",
    ]
    for host_path, container_path in extra_mounts or []:
        command.extend(["-v", f"{host_path}:{container_path}:ro"])
    command.extend(
        [
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
        ]
    )
    for name, value in (extra_env or {}).items():
        command.extend(["-e", f"{name}={value}"])
    command.extend(
        [
            _BACKUP_IMAGE,
            "sh",
            "-c",
            'POSTGRES_PASSWORD="$PGPASSWORD_FOR_TEST" sh /backup/pg_dump.sh',
        ]
    )
    return command, env


def _run_backup_script(
    db_url: str, backup_dir: Path, host_port: str
) -> subprocess.CompletedProcess[str]:
    """Run the real script in the real sidecar image against *db_url*."""
    command, env = _backup_command(db_url, backup_dir, host_port)
    return subprocess.run(command, env=env, capture_output=True, text=True, check=False)


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
    engine = create_engine(bootstrapped_db_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SET ROLE butler_relationship_rw")
            claim_id = conn.exec_driver_sql(
                """
                INSERT INTO public.cost_claims
                    (claim_key, asserted_by, kind, direction, amount, currency,
                     counterparty_label, description)
                VALUES
                    ('backup-contract-claim', 'relationship', 'receivable', 'inbound',
                     25, 'SGD', 'Backup fixture', 'Durable backup contract evidence')
                RETURNING id
                """
            ).scalar_one()
            conn.exec_driver_sql("SET ROLE butler_finance_rw")
            conn.exec_driver_sql(
                "INSERT INTO public.cost_claim_resolutions "
                "(claim_id, state, unverifiable_reason) "
                "VALUES (%s, 'unverifiable', 'no_account')",
                (claim_id,),
            )
            conn.exec_driver_sql("RESET ROLE")
            mapping_entity = conn.exec_driver_sql(
                "INSERT INTO public.entities (canonical_name, entity_type) "
                "VALUES ('Backup mapping fixture', 'person') RETURNING id"
            ).scalar_one()
            conn.exec_driver_sql(
                "INSERT INTO connectors.home_assistant_persons (ha_entity_id, entity_id) "
                "VALUES ('person.backup_mapping_fixture', %s)",
                (mapping_entity,),
            )
            conn.exec_driver_sql(
                "INSERT INTO public.ha_person_mapping_receipts "
                "(key_digest, request_digest, receipt, complete, received_count, "
                "created_count, unchanged_count, conflict_count, invalid_reference_count, "
                "outcome) VALUES (decode(repeat('ab', 32), 'hex'), "
                "decode(repeat('cd', 32), 'hex'), "
                "'00000000-0000-4000-8000-000000000246', true, 1, 1, 0, 0, 0, 'success')"
            )
    finally:
        engine.dispose()

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
    assert "CREATE TABLE public.ha_person_mapping_receipts" in dump
    assert "COPY connectors.home_assistant_persons" in dump
    assert "person.backup_mapping_fixture" in dump
    assert "00000000-0000-4000-8000-000000000246" in dump
    # ... and every fenced object stayed out.
    excluded_schemas, excluded_tables, scoped_data_tables = _read_backup_sets()
    for schema in excluded_schemas:
        assert f"CREATE SCHEMA {schema};" not in dump
    for qualified in excluded_tables:
        assert f"CREATE TABLE {qualified} " not in dump
    for qualified in scoped_data_tables:
        assert f"CREATE TABLE {qualified} " in dump
        assert f"COPY {qualified} " not in dump
    assert "-- Butlers scoped cost-claim ledger data" in dump
    assert "SELECT public.cost_claim_restore_row(" in dump
    staged_payloads = [
        bytes.fromhex(line.split("\t", 2)[2]).decode("utf-8")
        for line in dump.splitlines()
        if line.startswith(
            ("1\tcost_claims\t", "2\tcost_claim_resolutions\t", "3\tcost_claim_events\t")
        )
    ]
    assert any("backup-contract-claim" in payload for payload in staged_payloads)
    assert any(str(claim_id) in payload for payload in staged_payloads)


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_scoped_export_fails_before_publish_when_read_policy_can_filter(
    bootstrapped_db_url: str, postgres_container, tmp_path: Path
) -> None:
    """A new restrictive policy cannot silently narrow the scoped export."""
    engine = create_engine(bootstrapped_db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE POLICY cost_claims_backup_regression "
                "ON public.cost_claims AS RESTRICTIVE FOR SELECT USING (false)"
            )
        result = _run_backup_script(
            bootstrapped_db_url,
            tmp_path,
            str(postgres_container.get_exposed_port(5432)),
        )
        assert result.returncode != 0
        assert "policy is not the exact full-row contract" in result.stderr
        assert not list(tmp_path.glob("butlers_*.sql.gz"))
    finally:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "DROP POLICY IF EXISTS cost_claims_backup_regression ON public.cost_claims"
            )
        engine.dispose()


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_scoped_export_rejects_a_policy_changed_between_precheck_and_snapshot(
    bootstrapped_db_url: str, postgres_container, tmp_path: Path
) -> None:
    """A policy change cannot turn a precheck into a filtered published ledger."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    barrier_bin = tmp_path / "snapshot-barrier-bin"
    barrier_bin.mkdir()
    wrapper = barrier_bin / "psql"
    wrapper.write_text(_PSQL_SNAPSHOT_BARRIER, encoding="utf-8")
    wrapper.chmod(0o755)

    ready = backup_dir / "snapshot-ready"
    release = backup_dir / "snapshot-release"
    command, env = _backup_command(
        bootstrapped_db_url,
        backup_dir,
        str(postgres_container.get_exposed_port(5432)),
        extra_env={
            "BACKUP_TEST_SNAPSHOT_READY": "/backups/snapshot-ready",
            "BACKUP_TEST_SNAPSHOT_RELEASE": "/backups/snapshot-release",
            "PATH": "/backup-test-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
        extra_mounts=[(barrier_bin, "/backup-test-bin")],
    )

    engine = create_engine(bootstrapped_db_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.exec_driver_sql("SET ROLE butler_relationship_rw")
        try:
            conn.exec_driver_sql(
                """
                INSERT INTO public.cost_claims
                    (claim_key, asserted_by, kind, direction, amount, currency,
                     counterparty_label, description)
                VALUES
                    ('snapshot-drift-regression', 'relationship', 'receivable', 'inbound',
                     25, 'SGD', 'Snapshot fixture', 'Must not be silently filtered')
                """
            )
        finally:
            conn.exec_driver_sql("RESET ROLE")
    process = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(
                    "backup exited before the snapshot barrier: "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                pytest.fail("backup did not reach the deterministic pre-snapshot barrier")
            time.sleep(0.05)

        with engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE POLICY cost_claims_snapshot_drift_regression "
                "ON public.cost_claims AS RESTRICTIVE FOR ALL USING (false)"
            )
        release.touch()

        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode != 0, f"backup unexpectedly published: {stdout}\n{stderr}"
        assert "policy is not the exact full-row contract" in stderr
        assert not list(backup_dir.glob("butlers_*.sql.gz"))
    finally:
        release.touch(exist_ok=True)
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=30)
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "DROP POLICY IF EXISTS cost_claims_snapshot_drift_regression ON public.cost_claims"
            )
        engine.dispose()


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
