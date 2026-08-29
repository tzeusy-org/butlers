"""Drift guards for the hand-provisioned table stand-ins in ``butlers.testing``.

An integration test that needs one table from *another* butler's migration
chain sometimes provisions it by hand rather than running that whole chain.
``connector_registry`` is the recurring case: three integration tests each
stood up their own copy of that table, with a column list covering only what
their own endpoint queried.

That is a silent-breakage machine.  When ``sw_031`` widened the registry, the
stale stand-ins produced five of the nine failures on PR #3853 -- and none of
them pointed at the DDL.  The endpoint's ``SELECT`` raised, the route returned
its DEGRADED envelope, and the test died much later on ``KeyError:
'hourly_events_available'`` or an ``IndexError`` on an empty device list.  Each
file also passed in isolation against its own stand-in, so the cost was only
paid ~35 minutes into a full run, by whoever changed the schema next.

These tests move the failure back to the point of breakage:

- :func:`test_standin_matches_the_real_migration_chain` diffs every registered
  stand-in against the table the real chain builds, naming the exact columns,
  constraints and indexes that drifted.
- :func:`test_the_index_diff_can_fail` keeps that index arm honest: a guard
  nobody has watched go red is indistinguishable from one that cannot.
- :func:`test_no_test_hand_rolls_a_standin_table` stops the class from
  recurring by refusing a fourth hand-written copy.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.testing.schema_standins import (
    AUTONOMY_APPROVAL_HISTORY,
    AUTONOMY_SUGGESTIONS,
    PENDING_ACTIONS,
    STANDINS,
    TableStandin,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PARITY_SCHEMA = "standin_parity"
_BLINDED_SCHEMA = "standin_parity_blinded"
_EXEMPTION_MARKER = "schema-standin-exempt:"
_EXEMPTION_LOOKBACK_LINES = 8

docker_available = shutil.which("docker") is not None


@pytest.fixture(scope="module")
def parity_db_url(postgres_container) -> str:
    """Provision every chain the registered stand-ins claim to mirror."""
    chains: list[str] = []
    for standin in STANDINS.values():
        chains.extend(chain for chain in standin.chains if chain not in chains)
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=chains)


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
            "WHERE n.nspname = :s AND t.relname = :t AND c.contype IN ('p', 'c')"
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


def _describe_drift(standin: TableStandin, real: dict, mirror: dict, kind: str) -> list[str]:
    """Return one human-readable line per drifted item, or an empty list."""
    problems: list[str] = []
    for name in sorted(set(real) - set(mirror)):
        problems.append(
            f"  MISSING {kind}: {name} {real[name]} -- the migration chain has it, "
            f"{standin.constant_path} does not"
        )
    for name in sorted(set(mirror) - set(real)):
        problems.append(
            f"  EXTRA {kind}: {name} {mirror[name]} -- {standin.constant_path} has it, "
            "the migration chain does not"
        )
    for name in sorted(set(real) & set(mirror)):
        if real[name] != mirror[name]:
            problems.append(
                f"  MISMATCHED {kind}: {name} -- chain says {real[name]}, "
                f"{standin.constant_path} says {mirror[name]}"
            )
    return problems


def _drift(conn, standin: TableStandin, mirror_schema: str) -> list[str]:
    """Build the stand-in in ``mirror_schema`` and diff it against the real table."""
    conn.execute(text(f"DROP SCHEMA IF EXISTS {mirror_schema} CASCADE"))
    conn.execute(text(f"CREATE SCHEMA {mirror_schema}"))
    conn.execute(text(standin.ddl(schema=mirror_schema)))

    real_columns = _columns(conn, standin.real_schema, standin.table)
    assert real_columns, (
        f"{standin.real_schema}.{standin.table} was not created by chains "
        f"{list(standin.chains)} -- the stand-in's chain/schema metadata is wrong"
    )

    problems = _describe_drift(
        standin, real_columns, _columns(conn, mirror_schema, standin.table), "column"
    )
    for reader, kind in ((_constraints, "constraint"), (_indexes, "index")):
        problems += _describe_drift(
            standin,
            reader(conn, standin.real_schema, standin.table),
            reader(conn, mirror_schema, standin.table),
            kind,
        )
    return problems


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.parametrize("standin", list(STANDINS.values()), ids=list(STANDINS))
def test_standin_matches_the_real_migration_chain(parity_db_url: str, standin: TableStandin):
    """Every stand-in column, type, nullability, default, constraint and index matches."""
    engine = create_engine(parity_db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            problems = _drift(conn, standin, _PARITY_SCHEMA)
    finally:
        engine.dispose()

    assert not problems, (
        f"The {standin.table} test stand-in has drifted from the "
        f"{'/'.join(standin.chains)} migration chain:\n" + "\n".join(problems) + "\n"
        f"Reconcile {standin.constant_path} with the chain. A stale stand-in does not "
        "fail here in CI -- it fails as a DEGRADED envelope and a downstream KeyError "
        "in whichever integration test uses it (PR #3853)."
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.parametrize(
    "standin",
    (AUTONOMY_APPROVAL_HISTORY, AUTONOMY_SUGGESTIONS),
    ids=("autonomy_approval_history", "autonomy_suggestions"),
)
async def test_autonomy_standin_ddl_is_independently_creatable(
    provisioned_postgres_pool, standin: TableStandin
) -> None:
    """A stand-in must not rely on sibling tables absent from its fresh test DB."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(standin.ddl())


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_the_index_diff_can_fail(parity_db_url: str):
    """Dropping a real index from a stand-in must be reported, not tolerated.

    ``ux_pending_actions_active_deduplication_key`` (``approvals_013``) is the
    concrete case: it is a *unique* partial index, so it decides which rows the
    real table accepts.  A stand-in without it accepts writes production
    rejects.  Diffing a deliberately blinded copy proves the index arm of
    :func:`test_standin_matches_the_real_migration_chain` reports that, rather
    than passing the way it did while indexes went unread (bu-cwv9l).
    """
    engine = create_engine(parity_db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            problems = _drift(conn, replace(PENDING_ACTIONS, indexes=()), _BLINDED_SCHEMA)
    finally:
        engine.dispose()

    assert any(
        "MISSING index: ux_pending_actions_active_deduplication_key" in problem
        for problem in problems
    ), (
        "The parity guard did not notice a missing unique partial index. "
        f"It reported: {problems or 'no drift at all'}"
    )


def _exempted(lines: list[str], match_line_index: int) -> bool:
    start = max(0, match_line_index - _EXEMPTION_LOOKBACK_LINES)
    window = lines[start : match_line_index + 1]
    return any(
        line.split(_EXEMPTION_MARKER, 1)[1].strip() for line in window if _EXEMPTION_MARKER in line
    )


@pytest.mark.unit
def test_no_test_hand_rolls_a_standin_table():
    """A fourth hand-written copy of a stand-in table is refused at source level.

    The three original ``connector_registry`` stand-ins each looked reasonable
    in isolation; the defect only existed across them.  Import the shared
    constant instead, or annotate a genuinely different use with
    ``# schema-standin-exempt: <why>`` (as
    ``tests/config/test_init_db_bootstrap.py`` does for its two-column
    GRANT target, which is a privilege fixture rather than a query stand-in).
    """
    search_roots = [_REPO_ROOT / "tests", *sorted(_REPO_ROOT.glob("roster/*/tests"))]
    offenders: list[str] = []
    for standin in STANDINS.values():
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
        "definition:\n" + "\n".join(offenders) + "\nUse the constant in "
        "src/butlers/testing/schema_standins.py so a migration can never leave "
        "one copy stale (bu-r8opr) -- or, for a fixture that genuinely is not a "
        f"query stand-in, put '# {_EXEMPTION_MARKER} <why>' within the "
        f"{_EXEMPTION_LOOKBACK_LINES} lines above it."
    )
