"""Contract tests for the restore-side ownership guard in ``scripts/pg_restore.sh``.

A plain ``pg_dump`` writes ``CREATE FUNCTION`` and then ``ALTER FUNCTION ...
OWNER TO <role>``.  ``psql`` does not stop on error when it reads a script, and
it exits 0 regardless, so a failed ``ALTER`` leaves the function behind — owned
by whoever ran the restore.  For a SECURITY DEFINER function that inverts the
fence rather than losing it: a body meant to run with a constrained ``NOLOGIN``
owner's privileges now runs with the restoring login's, and the restore reports
success.

Neither obvious fix works, and this module pins the measurement for each:

* ``ON_ERROR_STOP=1`` does not help.  A real dump of a database bootstrapped by
  ``scripts/init-db.sql`` assigns ownership before it creates anything —
  ``test_a_real_dump_assigns_ownership_before_it_creates_anything`` pins that
  the first ``ALTER ... OWNER TO`` precedes the first ``CREATE TABLE``.
  Stopping on the first ownership error therefore aborts at the top of the file
  and recovers nothing, trading a silent privilege escalation for a
  disaster-recovery path that cannot run.
* Pre-creating the fenced roles on the target does not help either.  Assigning
  ownership to a role requires membership in it, and *not* being a member is
  precisely what the fence is, so the ``ALTER`` fails with ``must be able to
  SET ROLE`` even with the role sitting right there —
  ``test_pre_creating_the_fenced_roles_does_not_preserve_ownership`` pins it.

So the restore is allowed to complete and is then *audited*, and the audit
refuses to certify a restore in which any fence inverted.  What that leaves
pinned here:

1. :func:`test_plain_psql_restore_silently_launders_definer_ownership` — the
   defect itself, on a real dump: ``psql`` exits 0 while SECURITY DEFINER
   functions land on the restoring login.
2. :func:`test_restore_script_refuses_to_certify_a_laundered_restore` — the
   guard turns that silent success into a non-zero exit that names each
   function *and the owner the backup declared for it*, so an operator can tell
   an inverted fence from a merely renamed login.
3. :func:`test_certified_restore_leaves_no_definer_function_owned_by_restorer` —
   on a target the guard *does* certify, no SECURITY DEFINER function in
   ``public`` is owned by the restoring login.  That absence is mutation-tested
   in place: the test hands one fenced function to the restoring login and
   requires the very same query to name it, so the assertion cannot pass by
   being blind.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RESTORE_SCRIPT = _REPO_ROOT / "scripts" / "pg_restore.sh"
_BACKUP_SCRIPT = _REPO_ROOT / "deploy" / "backup" / "pg_dump.sh"

#: The image the ``backup-cron`` sidecar runs, so the dump matches production.
_BACKUP_IMAGE = "postgres:17-alpine"
#: The restore script needs bash as well as a PG17 ``psql``/``createdb``; the
#: alpine variant has no bash, and a host client is routinely a major version
#: behind the server, which a PG17 dump's own meta-commands trip over.
_CLIENT_IMAGE = "postgres:17"

docker_available = shutil.which("docker") is not None

#: The absence AC2 is about, and the one query every case here runs.
#: ``prosecdef`` is what makes an inverted fence dangerous: the body executes
#: with the owner's privileges, so an owner that is the restoring login means
#: the body executes with the restorer's.
_DEFINER_FUNCTIONS_OWNED_BY_SQL = """
SELECT p.proname
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.prosecdef
  AND pg_get_userbyid(p.proowner) = :login
ORDER BY 1
"""

#: Functions in ``public`` that a fenced ``NOLOGIN`` role owns.  These are the
#: ones whose ownership a restore cannot reproduce, and the dump carries them
#: in full because ``pg_dump`` has no way to exclude a function by name.
_FENCED_DEFINER_FUNCTIONS_SQL = """
SELECT p.proname
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid = p.pronamespace
JOIN pg_roles AS r ON r.oid = p.proowner
WHERE n.nspname = 'public'
  AND p.prosecdef
  AND NOT r.rolcanlogin
ORDER BY 1
"""

#: The roles those functions are fenced behind, needed by the pre-creation test.
_FENCED_OWNER_ROLES_SQL = """
SELECT DISTINCT r.rolname
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid = p.pronamespace
JOIN pg_roles AS r ON r.oid = p.proowner
WHERE n.nspname = 'public'
  AND p.prosecdef
  AND NOT r.rolcanlogin
ORDER BY 1
"""


def _query(db_url: str, sql: str, **params: object) -> list[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return [row[0] for row in conn.execute(text(sql), params)]
    finally:
        engine.dispose()


def _exec(db_url: str, sql: str, **params: object) -> None:
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(sql), params)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def source_db_url(postgres_container) -> str:
    """A database bootstrapped by the real init-db.sql and migrated to core@head.

    This is the production shape the nightly backup runs against: the fences are
    installed by the checked-in ``scripts/init-db.sql``, not by a hand-written
    approximation, and the returned URL is the ordinary migration login.
    """
    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@head")
    return db_url


@pytest.fixture(scope="module")
def backup_artifact(source_db_url: str, postgres_container, tmp_path_factory) -> Path:
    """A real ``.sql.gz`` produced by the real ``deploy/backup/pg_dump.sh``."""
    backup_dir = tmp_path_factory.mktemp("backups")
    parsed = urlparse(source_db_url)
    result = subprocess.run(
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
            f"POSTGRES_PORT={postgres_container.get_exposed_port(5432)}",
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
        env={**os.environ, "PGPASSWORD_FOR_TEST": parsed.password or ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"backup run failed:\n{result.stdout}\n{result.stderr}"
    artifacts = sorted(backup_dir.glob("butlers_*.sql.gz"))
    assert len(artifacts) == 1, f"expected exactly one artifact, got {artifacts}"
    return artifacts[0]


class _Target:
    """One restore target: where to point the script, and how to inspect it."""

    def __init__(self, port: str, login: str, password: str, admin_url: str) -> None:
        self.port = port
        self.login = login
        self.password = password
        self._admin_url = admin_url

    def url(self, db_name: str) -> str:
        """An admin connection URL for *db_name* on this target."""
        return urlparse(self._admin_url)._replace(path=f"/{db_name}").geturl()


@pytest.fixture(scope="module")
def disaster_recovery_target() -> Iterator[_Target]:
    """A second cluster that has never been bootstrapped — the real DR shape.

    Roles are cluster-wide, so a target that is *missing* the fenced owner roles
    has to be a different cluster.  Nothing here runs ``init-db.sql``: this is a
    bare PostgreSQL that a dump is being restored onto after a host loss.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17") as container:
        admin_url = container.get_connection_url()
        login = f"restorer_{uuid.uuid4().hex[:8]}"
        password = uuid.uuid4().hex
        _exec(
            admin_url,
            f'CREATE ROLE "{login}" LOGIN CREATEDB NOSUPERUSER NOCREATEROLE PASSWORD :password',
            password=password,
        )
        yield _Target(
            port=str(container.get_exposed_port(5432)),
            login=login,
            password=password,
            admin_url=admin_url,
        )


def _docker_client(
    backup: Path, shell: str, *, password: str, mount_script: bool = True
) -> subprocess.CompletedProcess:
    """Run *shell* in a PG17 client container that can reach the host.

    The password crosses only as the value of an environment variable forwarded
    *by name*, so it is never in this process' argv nor in the container's.
    """
    mounts = ["-v", f"{backup}:/backup.sql.gz:ro"]
    if mount_script:
        mounts += ["-v", f"{_RESTORE_SCRIPT}:/pg_restore.sh:ro"]
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--add-host=host.docker.internal:host-gateway",
            *mounts,
            "-e",
            "PGPASSWORD_FOR_TEST",
            _CLIENT_IMAGE,
            "bash",
            "-c",
            shell,
        ],
        env={**os.environ, "PGPASSWORD_FOR_TEST": password},
        capture_output=True,
        text=True,
        check=False,
    )


def _run_restore_script(
    backup: Path, target: _Target, target_db: str
) -> subprocess.CompletedProcess:
    """Run the real ``scripts/pg_restore.sh`` against *target*.

    The password reaches the script through ``POSTGRES_PASSWORD``, which is the
    documented fallback when ``--password`` is not given, rather than through
    the flag — a restore drill's password has no business in a process table.
    """
    return _docker_client(
        backup,
        'export POSTGRES_PASSWORD="$PGPASSWORD_FOR_TEST"; '
        "bash /pg_restore.sh /backup.sql.gz "
        "--host host.docker.internal "
        f"--port {target.port} --user {target.login} "
        f"--target-db {target_db} --drop-existing",
        password=target.password,
    )


def _raw_restore(
    backup: Path, target: _Target, target_db: str, *, on_error_stop: bool = False
) -> subprocess.CompletedProcess:
    """Restore with bare ``psql``, the way the script did before the guard."""
    stop = "-v ON_ERROR_STOP=1 " if on_error_stop else ""
    conn = f"-h host.docker.internal -p {target.port} -U {target.login} --no-password"
    return _docker_client(
        backup,
        'export PGPASSWORD="$PGPASSWORD_FOR_TEST"; '
        f"dropdb {conn} --if-exists {target_db} && createdb {conn} {target_db} && "
        f"gunzip -c /backup.sql.gz | psql {conn} -d {target_db} --quiet {stop}",
        password=target.password,
        mount_script=False,
    )


# ---------------------------------------------------------------------------
# Why the two obvious fixes were rejected — measured, not asserted in a comment
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_a_real_dump_assigns_ownership_before_it_creates_anything(
    backup_artifact: Path,
) -> None:
    """``ON_ERROR_STOP=1`` would abort the restore before any data exists.

    This is the whole case against the safer-looking fix.  The dump's first
    ``ALTER ... OWNER TO`` names the migration login and lands in the first
    handful of statements, long before the first ``CREATE TABLE``; on a target
    where that role is absent, ``ON_ERROR_STOP`` stops right there and the
    restore recovers nothing.  If a future ``pg_dump`` ever ordered its output
    differently this test goes red, and the rejected route is worth revisiting
    — that is exactly what it is here for.
    """
    dump = gzip.decompress(backup_artifact.read_bytes()).decode("utf-8")
    lines = dump.splitlines()

    def _first(pattern: str) -> int:
        for index, line in enumerate(lines, start=1):
            if re.match(pattern, line):
                return index
        raise AssertionError(f"no line matching {pattern!r} in the dump")

    first_owner = _first(r"^ALTER .* OWNER TO ")
    first_table = _first(r"^CREATE TABLE ")
    assert first_owner < first_table, (
        "ON_ERROR_STOP was rejected because ownership is assigned before any "
        f"table is created (first ALTER..OWNER TO at line {first_owner}, first "
        f"CREATE TABLE at line {first_table} of {len(lines)}). That is no "
        "longer true, so re-open the choice recorded in scripts/pg_restore.sh."
    )


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_pre_creating_the_fenced_roles_does_not_preserve_ownership(
    backup_artifact: Path, source_db_url: str, disaster_recovery_target: _Target
) -> None:
    """Creating the fenced roles on the target does not rebuild the fence.

    The case against the other obvious fix, and the reason the guard cannot be
    downgraded to a documented precondition alone.  Assigning ownership to a
    role requires membership in it; the restoring login is fenced away from
    these roles *by design*, so every ``ALTER FUNCTION ... OWNER TO`` still
    fails — now with ``must be able to SET ROLE`` instead of ``does not
    exist`` — and every fenced function still lands on the restorer.
    """
    target = disaster_recovery_target
    fenced_roles = _query(source_db_url, _FENCED_OWNER_ROLES_SQL)
    assert fenced_roles, "no fenced owner roles in the source; nothing to pre-create"

    admin = target.url("postgres")
    db_name = "butlers_restore_roles_precreated"
    # Roles are cluster-wide, so this test alters state the whole module shares.
    # It puts the cluster back to bare afterwards rather than leaving the other
    # tests' meaning dependent on execution order.
    try:
        for role in fenced_roles:
            _exec(admin, f'CREATE ROLE "{role}" NOLOGIN')

        result = _raw_restore(backup_artifact, target, db_name)
        assert result.returncode == 0

        for role in fenced_roles:
            assert f'must be able to SET ROLE "{role}"' in result.stderr, (
                f"expected the ALTER to fail on membership in {role} even though "
                f"the role exists; got:\n{result.stderr[-2000:]}"
            )

        landed = _query(target.url(db_name), _DEFINER_FUNCTIONS_OWNED_BY_SQL, login=target.login)
        fenced_functions = _query(source_db_url, _FENCED_DEFINER_FUNCTIONS_SQL)
        assert set(fenced_functions) <= set(landed), (
            "Pre-creating the roles was expected to change nothing about where "
            f"the fenced functions land. fenced={sorted(fenced_functions)} "
            f"landed={sorted(landed)}"
        )
    finally:
        _exec(admin, f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        for role in fenced_roles:
            _exec(admin, f'DROP ROLE IF EXISTS "{role}"')


# ---------------------------------------------------------------------------
# The defect, the guard, and the absence the guard buys
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_plain_psql_restore_silently_launders_definer_ownership(
    backup_artifact: Path, source_db_url: str, disaster_recovery_target: _Target
) -> None:
    """The unguarded restore reports success while inverting the fence.

    This is the defect, reproduced on a real dump rather than argued from the
    source: ``psql`` prints its ownership errors, keeps going, and exits 0.
    """
    target = disaster_recovery_target
    fenced = _query(source_db_url, _FENCED_DEFINER_FUNCTIONS_SQL)
    assert fenced, (
        "The source database has no SECURITY DEFINER function in public owned "
        "by a NOLOGIN role, so there is no fence left for a restore to invert. "
        "If init-db.sql genuinely stopped fencing these, delete this whole "
        "module rather than weakening it."
    )

    db_name = "butlers_restore_unguarded"
    result = _raw_restore(backup_artifact, target, db_name)

    assert result.returncode == 0, (
        "psql is expected to report success here — that is the defect. If it "
        f"now fails, this control needs rewriting.\n{result.stderr[-2000:]}"
    )
    assert "does not exist" in result.stderr or "SET ROLE" in result.stderr, (
        "expected ownership-assignment errors on stderr, got:\n" + result.stderr[-2000:]
    )

    landed = _query(target.url(db_name), _DEFINER_FUNCTIONS_OWNED_BY_SQL, login=target.login)
    assert set(fenced) <= set(landed), (
        "The fenced SECURITY DEFINER functions were expected to land on the "
        f"restoring login. fenced={sorted(fenced)} landed={sorted(landed)}"
    )


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_restore_script_refuses_to_certify_a_laundered_restore(
    backup_artifact: Path, source_db_url: str, disaster_recovery_target: _Target
) -> None:
    """The script turns that silent success into a loud, named failure.

    It deliberately does not repair or delete anything: a disaster-recovery path
    must not quietly rewrite the database it just produced, because the
    operator's next move may well be to create the missing roles and reassign
    ownership by hand.  Refusing to certify is the guard.

    The message has to carry the *declared* owner as well as the function, or
    an operator cannot tell an inverted fence (a ``NOLOGIN`` owner) from a
    target whose login is merely named differently from the source's.
    """
    result = _run_restore_script(
        backup_artifact, disaster_recovery_target, "butlers_restore_guarded"
    )

    assert result.returncode != 0, (
        "The restore inverted the fence and the script still reported success:\n"
        f"{result.stdout[-2000:]}"
    )
    assert "SECURITY FAILURE" in result.stderr
    for name in _query(source_db_url, _FENCED_DEFINER_FUNCTIONS_SQL):
        assert f"public.{name}" in result.stderr, (
            f"public.{name} was laundered but the failure message does not name "
            f"it, so an operator cannot act on it:\n{result.stderr[-2000:]}"
        )
    for role in _query(source_db_url, _FENCED_OWNER_ROLES_SQL):
        assert f"backup says: {role}" in result.stderr, (
            f"the message does not say the backup declared {role} as an owner, "
            "so an inverted fence reads the same as a renamed login:\n"
            f"{result.stderr[-2000:]}"
        )


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_certified_restore_leaves_no_definer_function_owned_by_restorer(
    backup_artifact: Path, postgres_container
) -> None:
    """A restore the script certifies has no definer function on the restorer.

    The supported path: a target where the fenced roles already exist and the
    restoring login can assume them.  Here every ``ALTER FUNCTION ... OWNER
    TO`` succeeds, so the absence below is real rather than an artefact of
    nothing having been restored.

    The absence is then mutation-tested in place.  One fenced function is handed
    to the restoring login and the *same* query is re-run: it must name it.  An
    assertion that cannot distinguish the two states is not evidence of
    anything, and a security guard proven only by a query that always returns
    empty is exactly the failure this bead is about.
    """
    admin_url = postgres_container.get_connection_url()
    parsed = urlparse(admin_url)
    login = parsed.username or ""
    target = _Target(
        port=str(postgres_container.get_exposed_port(5432)),
        login=login,
        password=parsed.password or "",
        admin_url=admin_url,
    )
    db_name = "butlers_restore_certified"

    result = _run_restore_script(backup_artifact, target, db_name)
    assert result.returncode == 0, (
        f"the supported restore path must certify:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )

    restored_url = target.url(db_name)
    landed = _query(restored_url, _DEFINER_FUNCTIONS_OWNED_BY_SQL, login=login)
    assert landed == [], (
        "These SECURITY DEFINER functions in public are owned by the restoring "
        f"login '{login}', so their bodies now run with its privileges: {landed}"
    )

    # Not vacuous in the other direction either: the fences are actually here.
    survivors = _query(restored_url, _FENCED_DEFINER_FUNCTIONS_SQL)
    assert survivors, (
        "no fenced definer function survived the restore, so the absence above proves nothing"
    )

    # Mutation: move one fence onto the restoring login and require the same
    # query to catch it, then put it back.
    victim = survivors[0]
    # ``regprocedure`` renders the full, correctly quoted signature, so this
    # works for an overloaded or argument-taking function rather than only for
    # the zero-argument one that happens to sort first today.
    signature, owner = _query(
        restored_url,
        "SELECT p.oid::regprocedure::text || '\t' || pg_get_userbyid(p.proowner) "
        "FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname = :name",
        name=victim,
    )[0].split("\t")

    _exec(restored_url, f'ALTER FUNCTION {signature} OWNER TO "{login}"')
    try:
        mutated = _query(restored_url, _DEFINER_FUNCTIONS_OWNED_BY_SQL, login=login)
        assert victim in mutated, (
            f"public.{victim} was handed to '{login}' and the absence query did "
            "not notice, so the assertion above is vacuous"
        )
    finally:
        _exec(restored_url, f'ALTER FUNCTION {signature} OWNER TO "{owner}"')
