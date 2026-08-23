"""Real-Postgres contract tests for the stored-function body drift probe (bu-bi5an).

``scripts/init-db.sql`` defines the body of every managed stored function, and a
change to one of those bodies reaches an already-installed database only when an
operator re-runs that script by hand.  Nothing in ``deploy/`` or the Makefile
does, so a deployed database can keep executing an old body indefinitely with no
signal anywhere that the deployed body and the committed body disagree.

``butlers.core.stored_function_drift`` is the signal.  These tests hold it to
both halves of the contract:

* it **does** report a body that diverged from the committed definition, and
* it does **not** report a body that is only cosmetically different.

The second half is the one that matters most.  A probe that cries wolf on every
deploy gets ignored, which reproduces the exact defect the probe exists to fix,
so each whitespace rule in :func:`normalize_function_body` gets its own
perturbation applied through PostgreSQL -- not to a string in memory -- and each
asserts the round trip reads as ``matched``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass

import asyncpg
import pytest
from sqlalchemy import Connection, create_engine, text

from alembic import command
from butlers.core.stored_function_drift import (
    DRIFTED,
    INIT_DB_SQL_PATH,
    MATCHED,
    NOT_DEPLOYED,
    StoredFunctionDriftReport,
    compute_stored_function_drift,
    log_stored_function_drift,
    normalize_function_body,
    parse_function_definitions,
)
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

#: The function bu-95gq7 rewrote, and the reason this bead exists.  It is a
#: nested definition -- ``runtime_attention_admin.install_legacy_debounce_marker``
#: emits it -- so it is also the hardest case for the parser.
_PLANTER = "public.runtime_attention_plant_legacy_debounce_marker"
#: Two committed variants: v1 from ``install_interface``, v2 from
#: ``upgrade_producers_v2`` (invoked once by ``core_199``).
_TWO_VARIANT = "public.append_runtime_attention_model_breaker"

#: The literal bu-95gq7 introduced, and the one it replaced.  Regressing the
#: deployed body between them is exactly the drift this probe must catch.
_CURRENT_NOTE = "'legacy_debounce_planted'"
_LEGACY_NOTE = "'blocked_old_binary'"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def postgres_container():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17") as postgres:
        yield postgres


@dataclass(frozen=True)
class _Database:
    """One disposable database plus the two logins these tests need."""

    #: Ordinary NOCREATEDB migration login -- what the probe reads through.
    url: str
    #: testcontainers' privileged control login -- rewrites stored bodies.
    bootstrap_url: str


@pytest.fixture(scope="module")
def head_db(postgres_container) -> _Database:
    """``init-db.sql`` plus the whole core chain: a realistic deployed database."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@head")
    return _Database(
        url=db_url, bootstrap_url=migration_bootstrap_db_url(postgres_container, db_name)
    )


@pytest.fixture(scope="module")
def bare_db(postgres_container) -> _Database:
    """``init-db.sql`` only -- no migration chain has invoked any installer.

    A real, legitimate state in which most of the functions ``init-db.sql``
    defines are simply not deployed.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    return _Database(
        url=db_url, bootstrap_url=migration_bootstrap_db_url(postgres_container, db_name)
    )


@pytest.fixture
def rewrite_planter(head_db: _Database):
    """Rewrite the planter's stored body, then restore it exactly.

    The restore runs even on failure, so no test can leave a rewritten body
    behind for the next one -- which would make these tests order-dependent in
    the worst possible way (a false all-clear).
    """
    engine = create_engine(head_db.bootstrap_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        original = _functiondef(conn, f"{_PLANTER}()")

    def _rewrite(new_definition: str) -> None:
        assert new_definition != original, (
            "the rewrite is a no-op, so this test would assert against the untouched deployed body"
        )
        with engine.connect() as conn:
            conn.exec_driver_sql(new_definition)

    try:
        yield _rewrite
    finally:
        with engine.connect() as conn:
            conn.exec_driver_sql(original)
        engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _functiondef(conn: Connection, signature: str) -> str:
    """Return the catalog's complete ``CREATE OR REPLACE FUNCTION`` statement."""
    return conn.execute(
        text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": signature},
    ).scalar_one()


def _deployed_body(definition_sql: str) -> str:
    """Return the body out of a ``pg_get_functiondef()`` statement."""
    parsed = parse_function_definitions(definition_sql)
    assert len(parsed) == 1, f"expected one definition in the catalog DDL, got {len(parsed)}"
    return parsed[0].body


def _perturbed(definition_sql: str, perturb) -> str:
    """Return *definition_sql* with only its body replaced by ``perturb(body)``."""
    body = _deployed_body(definition_sql)
    perturbed_body = perturb(body)
    assert perturbed_body != body, "the perturbation is a no-op"
    assert normalize_function_body(perturbed_body) == normalize_function_body(body), (
        "this perturbation changes the normalised body, so it is not a "
        "semantically identical rewrite and proves nothing about false positives"
    )
    return definition_sql.replace(body, perturbed_body)


def _probe(db_url: str) -> StoredFunctionDriftReport:
    async def _run() -> StoredFunctionDriftReport:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
        assert pool is not None
        try:
            return await compute_stored_function_drift(pool)
        finally:
            await pool.close()

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_scope_is_every_committed_function_with_no_opt_in_list(head_db: _Database) -> None:
    """Scope is discovered from the source, so nothing can be forgotten."""
    committed = {
        definition.function
        for definition in parse_function_definitions(INIT_DB_SQL_PATH.read_text(encoding="utf-8"))
    }
    report = _probe(head_db.url)

    assert len(committed) >= 20, "init-db.sql was not parsed into committed definitions"
    assert report.is_available
    assert {entry.function for entry in report.entries} == committed


# ---------------------------------------------------------------------------
# Drift IS reported
# ---------------------------------------------------------------------------


def test_reports_drift_when_a_deployed_body_diverges(head_db: _Database, rewrite_planter) -> None:
    """Regress the planter to its pre-bu-95gq7 body; the probe must say so."""
    engine = create_engine(head_db.bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            current = _functiondef(conn, f"{_PLANTER}()")
    finally:
        engine.dispose()

    regressed = current.replace(_CURRENT_NOTE, _LEGACY_NOTE)
    assert regressed != current, (
        f"the deployed planter body does not contain {_CURRENT_NOTE}; this test and "
        "init-db.sql disagree about the current vocabulary"
    )
    rewrite_planter(regressed)

    report = _probe(head_db.url)

    assert report.is_available
    entry = report.entry(_PLANTER)
    assert entry is not None
    assert entry.status == DRIFTED
    assert report.is_drifted
    assert _PLANTER in {drifted.function for drifted in report.drifted}
    assert entry.matched_line is None
    assert entry.deployed_digests and entry.deployed_digests[0] not in entry.committed_digests


def test_drift_of_one_function_does_not_taint_the_others(
    head_db: _Database, rewrite_planter
) -> None:
    engine = create_engine(head_db.bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            current = _functiondef(conn, f"{_PLANTER}()")
    finally:
        engine.dispose()

    rewrite_planter(current.replace(_CURRENT_NOTE, _LEGACY_NOTE))

    report = _probe(head_db.url)

    assert {drifted.function for drifted in report.drifted} == {_PLANTER}
    assert len(report.matched) >= 20


# ---------------------------------------------------------------------------
# Drift is NOT reported for semantically identical bodies
#
# One test per normalisation rule, each perturbation applied through PostgreSQL.
# ---------------------------------------------------------------------------


def test_a_freshly_bootstrapped_database_reports_no_drift(head_db: _Database) -> None:
    """The broadest false-positive proof: real bootstrap, real chain, zero drift."""
    report = _probe(head_db.url)

    assert report.is_available
    assert report.drifted == ()
    assert not report.is_drifted
    assert len(report.matched) >= 20
    assert report.not_deployed == ()


@pytest.mark.parametrize(
    ("rule", "perturb"),
    [
        pytest.param(
            "line endings",
            lambda body: body.replace("\n", "\r\n"),
            id="crlf-line-endings",
        ),
        pytest.param(
            "trailing whitespace",
            lambda body: "\n".join(
                line + "  \t" if line.strip() else line for line in body.split("\n")
            ),
            id="trailing-whitespace",
        ),
        pytest.param(
            "uniform indentation",
            lambda body: "\n".join(
                "    " + line if line.strip() else line for line in body.split("\n")
            ),
            id="uniform-reindent",
        ),
        pytest.param(
            "blank edges",
            lambda body: "\n\n" + body + "\n\n",
            id="blank-lines-around-body",
        ),
    ],
)
def test_a_cosmetically_rewritten_body_is_not_reported_as_drift(
    head_db: _Database, rewrite_planter, rule: str, perturb
) -> None:
    """Round-trip a whitespace-only rewrite through PostgreSQL and expect silence.

    Each parameter targets exactly one rule of the comparison.  If that rule is
    removed, this case fails -- which is what keeps the probe from reporting a
    difference no operator can act on.
    """
    engine = create_engine(head_db.bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            current = _functiondef(conn, f"{_PLANTER}()")
    finally:
        engine.dispose()

    rewrite_planter(_perturbed(current, perturb))

    report = _probe(head_db.url)

    entry = report.entry(_PLANTER)
    assert entry is not None
    assert entry.status == MATCHED, (
        f"a body differing only in {rule} was reported as drift; a probe that "
        "cries wolf on a cosmetic difference gets ignored"
    )
    assert report.drifted == ()
    assert entry.matched_line is not None


def test_either_committed_variant_of_a_two_body_function_matches(head_db: _Database) -> None:
    """A database on the v1 or the v2 body is on a committed body either way."""
    report = _probe(head_db.url)

    entry = report.entry(_TWO_VARIANT)
    assert entry is not None
    assert entry.status == MATCHED
    assert len(entry.committed_digests) == 2
    assert entry.matched_line in entry.committed_lines


# ---------------------------------------------------------------------------
# A function init-db.sql defines but the database lacks
# ---------------------------------------------------------------------------


def test_an_undeployed_function_is_named_rather_than_silently_passed(
    bare_db: _Database,
) -> None:
    """``init-db.sql`` alone installs no runtime-attention interface.

    That is a legitimate state, not drift -- so it must be visible in the
    report without being escalated as a mismatch.
    """
    report = _probe(bare_db.url)

    assert report.is_available
    entry = report.entry(_PLANTER)
    assert entry is not None
    assert entry.status == NOT_DEPLOYED
    assert entry.deployed_digests == ()
    assert _PLANTER in {absent.function for absent in report.not_deployed}
    assert not report.is_drifted, "an undeployed function is not a body mismatch"
    assert report.matched, "the functions init-db.sql installs directly must still be compared"


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_the_report_carries_digests_and_never_a_function_body(head_db: _Database) -> None:
    """A stored body can hold operator-supplied literals; it must not leak."""
    report = _probe(head_db.url)

    assert report.entries, "an empty report proves nothing about redaction"
    rendered = repr(report)
    assert _CURRENT_NOTE.strip("'") not in rendered
    assert "INSERT INTO public.audit_log" not in rendered
    for entry in report.entries:
        for digest in (*entry.committed_digests, *entry.deployed_digests):
            assert re.fullmatch(r"[0-9a-f]{12}", digest), f"{digest!r} is not a short digest"


def test_the_startup_log_line_names_functions_not_bodies(
    head_db: _Database, rewrite_planter, caplog: pytest.LogCaptureFixture
) -> None:
    engine = create_engine(head_db.bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            current = _functiondef(conn, f"{_PLANTER}()")
    finally:
        engine.dispose()

    rewrite_planter(current.replace(_CURRENT_NOTE, _LEGACY_NOTE))
    report = _probe(head_db.url)

    with caplog.at_level(logging.WARNING, logger="butlers.core.stored_function_drift"):
        log_stored_function_drift(report)

    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert warnings, "drift was detected but nothing was logged"
    message = "\n".join(record.getMessage() for record in warnings)
    assert _PLANTER in message
    assert "scripts/init-db.sql" in message, "the log line must say what to re-run"
    assert _CURRENT_NOTE.strip("'") not in message
    assert _LEGACY_NOTE.strip("'") not in message
