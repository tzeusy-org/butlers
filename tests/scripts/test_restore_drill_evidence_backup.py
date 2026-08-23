"""Where restore-drill evidence lives in a backup, and what keeps it there (bu-nu6cn).

``deploy/backup/pg_dump.sh`` excludes the whole ``restore_drill_executor``
schema, because the backup runs as the shared migration login and that login is
deliberately fenced away from the drill ledger.  bu-e1410 recorded the cost of
that exclusion as "restore-drill history is not backed up"; this module is the
measurement that corrected it, and ``docs/operations/backup-restore.md`` and the
script header now state what is measured here.

Measured against a real bootstrapped database, that is half right, and the half
it gets wrong is the half that matters:

* The **authoritative ledger** really is unreachable and really is absent from
  every published artifact — :func:`test_the_backup_role_cannot_read_the_ledger`
  and :func:`test_the_published_artifact_contains_no_ledger_row` prove it with
  drill results actually in the ledger at the moment the backup runs.
* The **history itself is backed up**, in ``public.audit_log``.  Every
  ``record_result()`` writes a fixed projection row in the *same transaction*
  as its ledger insert, so no ledger row can exist without one, and
  ``public.audit_log`` is neither fenced nor excluded nor ever pruned
  (``openspec/specs/dashboard-audit-log/spec.md``, "Audit Log Retention":
  retained indefinitely, no deletes).

So the evidence survives a disaster inside the ordinary nightly dump, and this
module is what keeps that true.  It pins the two things that would silently end
it — the transactional coupling, and the projection's presence in the published
artifact — and proves the round trip end to end, because an evidence path
nobody has restored is not an evidence path.

What the projection is *not* is authoritative.  Ordinary application roles hold
broad ``public.audit_log`` DML, so an ``actor='restore_drill'`` row can be
forged; ``scripts/init-db.sql`` says so in as many words ("Public audit is
fixed telemetry, never a result authority").  That is a property of the
evidence, not of this backup: authority lives in a fenced ledger only while the
database that fences it is alive, and no exported file of any shape carries it.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKUP_SCRIPT = _REPO_ROOT / "deploy" / "backup" / "pg_dump.sh"

#: The image the ``backup-cron`` sidecar runs in docker-compose.yml.  Used for
#: both dump and restore so the client major version matches the server.
_BACKUP_IMAGE = "postgres:17-alpine"

#: The fixed shape ``restore_drill_executor_admin.write_audit_projection()``
#: writes for every recorded drill result.  Nothing else in the fleet uses this
#: actor/action pair, so it identifies the projection unambiguously.
_PROJECTION_ACTOR = "restore_drill"
_PROJECTION_ACTION = "restore_drill_result"

_LEDGER_SQL = """
SELECT id, recorded_at, result
FROM restore_drill_executor.restore_drill_results
ORDER BY id
"""

_PROJECTION_SQL = """
SELECT id, ts, result
FROM public.audit_log
WHERE actor = :actor AND action = :action
ORDER BY id
"""

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _query(db_url: str, sql: str, **params) -> list[tuple]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return [tuple(row) for row in conn.execute(text(sql), params)]
    finally:
        engine.dispose()


def _execute(db_url: str, sql: str, **params) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    finally:
        engine.dispose()


def _record_drill_results(privileged_url: str, results: list[str]) -> None:
    """Record real drill results through the sanctioned writer.

    Each result gets its own transaction, exactly as a real drill run does, so
    the ledger and its projection carry distinct timestamps rather than one
    shared statement timestamp.  ``record_result`` is EXECUTE-restricted to the
    executor login; a cluster superuser bypasses that ACL, which is how the test
    stands in for the executor without provisioning its credential.
    """
    for result in results:
        _execute(
            privileged_url,
            "SELECT restore_drill_executor.record_result("
            "  'butlers_backup.sql.gz', :result, NULL, 1)",
            result=result,
        )


@pytest.fixture(scope="module")
def bootstrapped(postgres_container) -> tuple[str, str]:
    """A real init-db.sql database, as (backup-role URL, privileged URL).

    The first is the ordinary migration login the ``backup-cron`` sidecar uses:
    everything this module claims about what the backup can and cannot reach is
    measured through it, never through a hand-written ACL approximation.  The
    second is testcontainers' control login, used only to stand in for the
    fenced executor when seeding evidence.
    """
    db_name = migration_db_name()
    backup_role_url = create_migration_db(postgres_container, db_name)
    command.upgrade(_build_alembic_config(backup_role_url, chains=["core"]), "core@head")
    return backup_role_url, migration_bootstrap_db_url(postgres_container, db_name)


@dataclass(frozen=True)
class _EvidenceBackup:
    """One published artifact, with what the database held when it was taken.

    The snapshots are captured at publication time rather than read back later:
    this module's other tests go on recording drill results in the same
    database, and an artifact compared against a moving database would fail for
    the one reason that says nothing about the backup.
    """

    artifact: str
    ledger: list[tuple]
    projection: list[tuple]


@pytest.fixture(scope="module")
def evidence_backup(bootstrapped, postgres_container, tmp_path_factory) -> _EvidenceBackup:
    """Seed real drill results, then publish one real nightly artifact.

    The seeding happens *before* the dump so the ledger is non-empty at the
    moment the backup runs — an artifact taken against an empty ledger could not
    tell "the evidence was excluded" from "there was no evidence".
    """
    backup_role_url, privileged_url = bootstrapped
    _record_drill_results(privileged_url, ["pass", "fail", "pass"])

    backup_dir = tmp_path_factory.mktemp("backups")
    result = _run_backup_script(
        backup_role_url, backup_dir, str(postgres_container.get_exposed_port(5432))
    )
    assert result.returncode == 0, f"backup run failed:\n{result.stdout}\n{result.stderr}"

    artifacts = sorted(backup_dir.glob("butlers_*.sql.gz"))
    assert len(artifacts) == 1, f"expected exactly one artifact, got {artifacts}"
    with gzip.open(artifacts[0], "rt", encoding="utf-8", errors="replace") as handle:
        artifact = handle.read()

    return _EvidenceBackup(
        artifact=artifact,
        ledger=_query(privileged_url, _LEDGER_SQL),
        projection=_query(
            privileged_url,
            _PROJECTION_SQL,
            actor=_PROJECTION_ACTOR,
            action=_PROJECTION_ACTION,
        ),
    )


def _run_backup_script(
    db_url: str, backup_dir: Path, host_port: str
) -> subprocess.CompletedProcess:
    """Run the real nightly script in the real sidecar image."""
    parsed = urlparse(db_url)
    # Forwarded by name, not by value, so the generated password never lands in
    # this process' argv.
    env = {**os.environ, "PGPASSWORD_FOR_TEST": parsed.password or ""}
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--add-host=host.docker.internal:host-gateway",
            "-v",
            f"{_BACKUP_SCRIPT}:/backup/pg_dump.sh:ro",
            "-v",
            f"{backup_dir}:/backups",
            "-e",
            "POSTGRES_HOST=host.docker.internal",
            "-e",
            f"POSTGRES_PORT={host_port}",
            "-e",
            f"POSTGRES_USER={parsed.username}",
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


# ---------------------------------------------------------------------------
# The gap: the authoritative ledger is genuinely out of reach
# ---------------------------------------------------------------------------


def test_the_backup_role_cannot_read_the_ledger(bootstrapped) -> None:
    """Measured, not inferred from the exclusion flag.

    The exclusion set in ``pg_dump.sh`` is a *claim* that this login cannot read
    the ledger.  Reading the flag proves only that someone believed it once.
    """
    backup_role_url, privileged_url = bootstrapped
    _record_drill_results(privileged_url, ["pass"])
    assert _query(privileged_url, _LEDGER_SQL), "the ledger must hold evidence to be denied"

    with pytest.raises(Exception, match="permission denied"):
        _query(backup_role_url, _LEDGER_SQL)

    reach = _query(
        backup_role_url,
        """
        SELECT
          has_table_privilege(
            current_user, 'restore_drill_executor.restore_drill_results', 'SELECT'),
          pg_has_role(current_user, 'restore_drill_executor_owner', 'USAGE'),
          pg_has_role(current_user, 'restore_drill_executor_owner', 'MEMBER'),
          has_schema_privilege(current_user, 'restore_drill_executor', 'CREATE')
        """,
    )
    assert reach == [(False, False, False, False)], (
        "The backup role reached the drill ledger through a table grant or a "
        f"membership in its owner: {reach}. Either dissolves the fence the "
        "schema exists to hold — the backup must never be the way in."
    )


def test_the_ledger_owner_cannot_be_logged_into(bootstrapped) -> None:
    """An export taken *as* ``restore_drill_executor_owner`` is not buildable.

    The role is created NOLOGIN, re-forced NOLOGIN on every ``init-db.sql`` run,
    and ``core_196`` refuses to treat the interface as trusted unless
    ``NOT rolcanlogin`` still holds.  Any design that proposes connecting as the
    ledger owner has to break that check first, so it is not a smaller change
    than it looks.
    """
    _backup_role_url, privileged_url = bootstrapped
    attributes = _query(
        privileged_url,
        "SELECT rolcanlogin, rolsuper, rolcreatedb, rolreplication "
        "FROM pg_roles WHERE rolname = 'restore_drill_executor_owner'",
    )
    assert attributes == [(False, False, False, False)], attributes


def test_the_published_artifact_contains_no_ledger_row(evidence_backup) -> None:
    """The real artifact, published while the ledger held results, has none of them."""
    assert len(evidence_backup.ledger) >= 3, (
        f"expected seeded drill evidence in the ledger, got {evidence_backup.ledger}"
    )

    artifact = evidence_backup.artifact
    assert "CREATE SCHEMA restore_drill_executor;" not in artifact
    assert "restore_drill_results" not in artifact, (
        "The drill ledger reached the published backup. The backup role cannot "
        "read it, so this means the fence moved, not that the backup improved."
    )


# ---------------------------------------------------------------------------
# The evidence path that does survive
# ---------------------------------------------------------------------------


def test_every_ledger_row_has_an_audit_projection_row(bootstrapped) -> None:
    """The projection is complete because it is written in the ledger's transaction.

    This is the property the whole evidence path rests on: the backup carries
    ``public.audit_log``, so it carries the drill history exactly as long as
    every ledger insert still brings a projection row with it.
    """
    _backup_role_url, privileged_url = bootstrapped
    before_ledger = len(_query(privileged_url, _LEDGER_SQL))
    before_projection = len(
        _query(privileged_url, _PROJECTION_SQL, actor=_PROJECTION_ACTOR, action=_PROJECTION_ACTION)
    )

    _record_drill_results(privileged_url, ["pass", "fail"])

    ledger = _query(privileged_url, _LEDGER_SQL)
    projection = _query(
        privileged_url, _PROJECTION_SQL, actor=_PROJECTION_ACTOR, action=_PROJECTION_ACTION
    )
    assert len(ledger) - before_ledger == 2
    assert len(projection) - before_projection == 2, (
        "A drill result reached the ledger without a projection row. The ledger "
        "is excluded from every backup, so that result is now evidence that "
        "exists only in a database a disaster would take with it."
    )
    assert [row[2] for row in ledger] == [row[2] for row in projection], (
        "The projection disagrees with the ledger about what the drills said: "
        f"ledger={[row[2] for row in ledger]} projection={[row[2] for row in projection]}"
    )


def test_a_failed_projection_leaves_no_ledger_row(bootstrapped) -> None:
    """Mutate the coupling and watch it hold: no projection, no result.

    Without this, "every ledger row has a projection row" could be true only by
    coincidence of the happy path.  Revoking the projection writer's one grant
    is the smallest real break available, and the ledger insert must not survive
    it — a recorded result whose evidence never left the fence would be exactly
    the silent loss this module exists to prevent.
    """
    _backup_role_url, privileged_url = bootstrapped
    before = _query(privileged_url, _LEDGER_SQL)

    _execute(
        privileged_url,
        "REVOKE INSERT ON TABLE public.audit_log FROM restore_drill_executor_audit_writer",
    )
    try:
        with pytest.raises(Exception, match="permission denied"):
            _record_drill_results(privileged_url, ["pass"])
        assert _query(privileged_url, _LEDGER_SQL) == before, (
            "The ledger kept a result whose audit projection failed. The "
            "projection is the only copy of that result the backup carries, so "
            "the insert must roll back with it."
        )
    finally:
        _execute(
            privileged_url,
            "GRANT INSERT ON TABLE public.audit_log TO restore_drill_executor_audit_writer",
        )

    # The repaired path still works, so the mutation proved the coupling rather
    # than merely breaking the fixture for everything after it.
    _record_drill_results(privileged_url, ["pass"])
    assert len(_query(privileged_url, _LEDGER_SQL)) == len(before) + 1


def test_the_published_artifact_carries_the_drill_history(evidence_backup) -> None:
    """The nightly dump really does contain the evidence, row for row."""
    assert evidence_backup.projection, (
        "the projection must hold evidence for this test to mean anything"
    )

    assert "CREATE TABLE public.audit_log" in evidence_backup.artifact
    copied = [
        line
        for line in evidence_backup.artifact.splitlines()
        if f"\t{_PROJECTION_ACTOR}\t{_PROJECTION_ACTION}\t" in line
    ]
    assert len(copied) == len(evidence_backup.projection), (
        f"the artifact carries {len(copied)} drill projection rows but the "
        f"database held {len(evidence_backup.projection)} when it was published"
    )


def test_the_evidence_projection_is_never_excluded_from_the_backup() -> None:
    """``public.audit_log`` must stay out of the exclusion set.

    Adding it would be a one-line edit with no visible failure: the dump would
    keep succeeding, keep publishing, and quietly stop carrying the only copy of
    the drill history that leaves the database.
    """
    source = _BACKUP_SCRIPT.read_text(encoding="utf-8")
    declarations = [
        line
        for line in source.splitlines()
        if line.startswith(("BACKUP_EXCLUDE_SCHEMAS=", "BACKUP_EXCLUDE_TABLES="))
    ]
    assert declarations, "exclusion set declarations not found in the backup script"
    for declaration in declarations:
        assert "public.audit_log" not in declaration, (
            "public.audit_log is excluded from the backup. It carries the "
            "restore-drill evidence projection, which is the only copy of the "
            "drill history that survives losing the database."
        )


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


def _restore_artifact(artifact: str, postgres_container, target_db: str) -> str:
    """Restore *artifact* into a fresh database and return its privileged URL."""
    control_url = postgres_container.get_connection_url()
    engine = create_engine(control_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{target_db}"'))
    finally:
        engine.dispose()

    parsed = urlparse(control_url)
    env = {**os.environ, "PGPASSWORD_FOR_TEST": parsed.password or ""}
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--add-host=host.docker.internal:host-gateway",
            "-e",
            "PGPASSWORD_FOR_TEST",
            _BACKUP_IMAGE,
            "sh",
            "-c",
            'PGPASSWORD="$PGPASSWORD_FOR_TEST" psql --host=host.docker.internal '
            f"--port={postgres_container.get_exposed_port(5432)} "
            f"--username={parsed.username} --dbname={target_db} "
            "--no-password --quiet -v ON_ERROR_STOP=0",
        ],
        input=artifact,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return parsed._replace(path=f"/{target_db}").geturl()


def test_restoring_the_artifact_yields_the_drill_history_back(
    evidence_backup, postgres_container
) -> None:
    """Restore the real artifact into a clean database and read the evidence out.

    An evidence path nobody has restored is a claim, not a backup.  This is the
    whole round trip: drills recorded through the fenced writer, published by
    the real nightly script, restored into a database that has never seen a
    drill, and queried back.
    """
    expected = [row[2] for row in evidence_backup.projection]
    assert expected, "the source database must hold drill evidence"

    restored_url = _restore_artifact(
        evidence_backup.artifact, postgres_container, f"restored_{migration_db_name()}"
    )
    recovered = [
        row[2]
        for row in _query(
            restored_url, _PROJECTION_SQL, actor=_PROJECTION_ACTOR, action=_PROJECTION_ACTION
        )
    ]

    assert recovered == expected, (
        "The restored database does not show the drill history the source had: "
        f"expected {expected}, recovered {recovered}."
    )

    # And the authoritative ledger is, as designed, not among what came back:
    # the bootstrap rebuilds that boundary, a restore never does.
    ledger_present = _query(
        restored_url,
        "SELECT to_regclass('restore_drill_executor.restore_drill_results') IS NOT NULL",
    )
    assert ledger_present == [(False,)], (
        "The restore reconstructed the drill ledger. Only the managed bootstrap "
        "may create that boundary; a dump that carries it would land its objects "
        "under whoever ran the restore."
    )
