"""Contract: chronicler schema-drift guardrails (RFC 0014 §D5, bu-pylew).

Guards two related drift surfaces:

1. **Inline DDL drift** — ``BUTLER_SESSIONS_COLUMNS`` in
   ``roster/chronicler/tests/_inline_ddl.py`` must not reference columns
   absent from the canonical production ``core.sessions`` migration.
   (Promoted from tests/chronicler/test_inline_ddl_drift.py [bu-m564i])

2. **Alembic migration drift** — The inline DDL applied by
   ``_apply_chronicler_schema`` must stay in column-sync with the full
   Alembic chronicler migration chain.
   (Promoted from tests/chronicler/test_schema_drift.py [bu-m564i])

Background — both tests guard against the same class of failure:
PR #1222 (bu-fkqv0 / bu-8orvr cycle) hit two near-misses where manual DDL
duplication silently diverged from the migration chain (missing
``watermark_id`` after migration 005, missing ``carryover`` after 006).
"""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from collections import defaultdict
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migration_db

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The canonical production migration that creates core.sessions
_FOUNDATION_MIGRATION = _REPO_ROOT / "alembic" / "versions" / "core" / "core_001_foundation.py"

# The _inline_ddl module (imported as Python to avoid parsing overhead)
_INLINE_DDL_MODULE = _REPO_ROOT / "roster" / "chronicler" / "tests" / "_inline_ddl.py"

_CHRONICLER_MIGRATION_CHAIN = "chronicler"

docker_available = shutil.which("docker") is not None

pytestmark = pytest.mark.contract

# ---------------------------------------------------------------------------
# ── Part 1: BUTLER_SESSIONS_COLUMNS vs production core.sessions ─────────────
# ---------------------------------------------------------------------------


def _parse_production_sessions_columns() -> frozenset[str]:
    """Extract the column names from the CREATE TABLE sessions statement.

    Parses ``core_001_foundation.py`` to find the canonical list of columns
    defined for ``core.sessions``.  Uses parenthesis-depth tracking so the
    parser is not confused by column-level DEFAULT expressions containing ``(``.

    Returns:
        Frozenset of lowercase column names defined in the production schema.

    Raises:
        ValueError: If the CREATE TABLE sessions block cannot be found.
    """
    source = _FOUNDATION_MIGRATION.read_text(encoding="utf-8")

    needle = "CREATE TABLE IF NOT EXISTS sessions ("
    start = source.find(needle)
    if start == -1:
        raise ValueError(
            f"Could not locate 'CREATE TABLE IF NOT EXISTS sessions (' in "
            f"{_FOUNDATION_MIGRATION}.  Has the migration been renamed?"
        )

    # Advance to the opening paren of the column list.
    paren_pos = start + len(needle) - 1  # position of the '(' in the needle
    depth = 1
    i = paren_pos + 1
    while i < len(source) and depth > 0:
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
        i += 1

    ddl_body = source[paren_pos + 1 : i - 1]

    # Each non-blank, non-constraint line starts with the column name.
    _CONSTRAINT_KEYWORDS = frozenset({"primary", "foreign", "unique", "check", "constraint"})
    columns: list[str] = []
    for raw_line in ddl_body.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        m = re.match(r"^([a-z_][a-z0-9_]*)", line, re.IGNORECASE)
        if m and m.group(1).lower() not in _CONSTRAINT_KEYWORDS:
            columns.append(m.group(1).lower())

    return frozenset(columns)


def test_butler_sessions_columns_is_subset_of_production_schema() -> None:
    """BUTLER_SESSIONS_COLUMNS must not reference columns absent from production.

    Parses the canonical column list from ``core_001_foundation.py`` and
    verifies that every entry in ``BUTLER_SESSIONS_COLUMNS`` exists in
    production.

    The fake table is an intentional subset — production may have more columns
    than the fake table, and that is fine.  Only phantom columns (present in
    fake but absent from production) cause a failure.

    FAILS when a column is removed from the production migration but still
    listed in ``BUTLER_SESSIONS_COLUMNS``.

    To fix: remove the phantom column from both ``BUTLER_SESSIONS_COLUMNS`` and
    the DDL returned by ``make_sessions_table_ddl()`` in
    ``roster/chronicler/tests/_inline_ddl.py``.
    """
    # Import the constant directly; keeps the test independent of sys.path tricks.
    import importlib.util

    spec = importlib.util.spec_from_file_location("_inline_ddl", _INLINE_DDL_MODULE)
    assert spec is not None and spec.loader is not None, (
        f"Could not locate _inline_ddl module at {_INLINE_DDL_MODULE}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    butler_columns: tuple[str, ...] = module.BUTLER_SESSIONS_COLUMNS

    production_columns = _parse_production_sessions_columns()

    phantom_columns = sorted(col for col in butler_columns if col.lower() not in production_columns)

    if phantom_columns:
        raise AssertionError(
            "BUTLER_SESSIONS_COLUMNS contains column(s) that do NOT exist in the "
            "production core.sessions migration:\n"
            + "".join(f"  - {col}\n" for col in phantom_columns)
            + "\n"
            "These are phantom columns that would silently break if the fake "
            "sessions table DDL were used against a real DB.\n"
            "\n"
            "Fix: remove phantom columns from BUTLER_SESSIONS_COLUMNS and from "
            "make_sessions_table_ddl() in "
            "roster/chronicler/tests/_inline_ddl.py, or add them to the "
            "production migration (core_001_foundation.py) if they are genuinely "
            "new columns."
        )


def test_production_sessions_columns_parseable() -> None:
    """Sanity-check: the production column parser returns a non-empty result.

    Guards against silent parser failures that would make the drift detector
    vacuously pass (empty production set = no phantom columns reported).

    Verifies that the mandatory core columns are present in the parsed result.
    """
    production_columns = _parse_production_sessions_columns()

    # These columns must always be present in the production schema.
    _MANDATORY_COLUMNS = frozenset(
        {"id", "prompt", "trigger_source", "started_at", "completed_at", "request_id"}
    )
    missing_mandatory = sorted(_MANDATORY_COLUMNS - production_columns)
    assert not missing_mandatory, (
        "Production sessions column parser returned incomplete results; "
        f"missing expected columns: {missing_mandatory!r}.  "
        "This likely means the parser is broken — check "
        f"{_FOUNDATION_MIGRATION}."
    )

    assert len(production_columns) >= len(_MANDATORY_COLUMNS), (
        f"Production sessions column parser returned only {len(production_columns)} "
        f"columns; expected at least {len(_MANDATORY_COLUMNS)}.  Parser may be broken."
    )


# ---------------------------------------------------------------------------
# ── Part 2: inline DDL vs Alembic migration chain (requires Docker) ──────────
# ---------------------------------------------------------------------------


async def _apply_inline_ddl(conn: asyncpg.Connection) -> None:
    """Apply the chronicler inline DDL (mirrors _apply_chronicler_schema)."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS source_adapter_state (
            source_name TEXT PRIMARY KEY,
            chronicler_compatibility TEXT NOT NULL
                CHECK (chronicler_compatibility IN (
                    'supported', 'deferred', 'not_time_bearing', 'planned'
                )),
            read_surface TEXT,
            boundary_semantics TEXT,
            optional_schema BOOLEAN NOT NULL DEFAULT false,
            active BOOLEAN NOT NULL DEFAULT false,
            inactive_reason TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS projection_checkpoints (
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name)
                ON DELETE CASCADE,
            subsource TEXT NOT NULL DEFAULT '',
            watermark TIMESTAMPTZ,
            watermark_id BIGINT,
            carryover JSONB,
            last_run_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_error TEXT,
            rows_projected BIGINT NOT NULL DEFAULT 0,
            run_count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_name, subsource)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS point_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name),
            source_ref TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            precision TEXT NOT NULL DEFAULT 'exact'
                CHECK (precision IN ('exact', 'minute', 'hour', 'day', 'unknown')),
            title TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            privacy TEXT NOT NULL DEFAULT 'normal'
                CHECK (privacy IN ('normal', 'sensitive', 'restricted')),
            retention_days INTEGER,
            tombstone_at TIMESTAMPTZ,
            tombstone_reason TEXT,
            entity_id UUID,
            layer TEXT NOT NULL DEFAULT 'evidence'
                CHECK (layer IN ('intent', 'evidence', 'activity')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_name, source_ref)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name),
            source_ref TEXT NOT NULL,
            episode_type TEXT NOT NULL,
            start_at TIMESTAMPTZ NOT NULL,
            end_at TIMESTAMPTZ,
            precision TEXT NOT NULL DEFAULT 'exact'
                CHECK (precision IN ('exact', 'minute', 'hour', 'day', 'unknown')),
            title TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            privacy TEXT NOT NULL DEFAULT 'normal'
                CHECK (privacy IN ('normal', 'sensitive', 'restricted')),
            retention_days INTEGER,
            tombstone_at TIMESTAMPTZ,
            tombstone_reason TEXT,
            layer TEXT NOT NULL DEFAULT 'evidence'
                CHECK (layer IN ('intent', 'evidence', 'activity')),
            confidence TEXT NOT NULL DEFAULT 'low'
                CHECK (confidence IN ('high', 'medium', 'low')),
            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_name, source_ref),
            CHECK (end_at IS NULL OR end_at >= start_at)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS episode_event_links (
            episode_id UUID NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
            event_id UUID NOT NULL REFERENCES point_events(id) ON DELETE CASCADE,
            relation TEXT NOT NULL DEFAULT 'supports'
                CHECK (relation IN ('supports', 'boundary_start', 'boundary_end', 'evidence')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (episode_id, event_id, relation)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS overrides (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            target_kind TEXT NOT NULL CHECK (target_kind IN ('episode', 'point_event')),
            target_id UUID NOT NULL,
            corrected_start_at TIMESTAMPTZ,
            corrected_end_at TIMESTAMPTZ,
            corrected_title TEXT,
            corrected_privacy TEXT
                CHECK (corrected_privacy IS NULL OR
                       corrected_privacy IN ('normal', 'sensitive', 'restricted')),
            corrected_tombstone_at TIMESTAMPTZ,
            note TEXT,
            submitted_by TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (
                corrected_start_at IS NOT NULL OR
                corrected_end_at IS NOT NULL OR
                corrected_title IS NOT NULL OR
                corrected_privacy IS NOT NULL OR
                corrected_tombstone_at IS NOT NULL OR
                note IS NOT NULL
            )
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name)
                ON DELETE CASCADE,
            key TEXT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            hit_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (source_name, key)
        )
    """)


def _get_table_columns(db_url: str) -> dict[str, frozenset[str]]:
    """Return {table_name: frozenset(column_names)} for all BASE TABLEs in public schema."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT table_name, column_name "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "  AND table_name IN ("
                    "    SELECT table_name FROM information_schema.tables"
                    "    WHERE table_schema = 'public'"
                    "      AND table_type = 'BASE TABLE'"
                    "  ) "
                    "ORDER BY table_name, column_name"
                )
            ).fetchall()
    finally:
        engine.dispose()

    table_cols: dict[str, list[str]] = defaultdict(list)
    for table_name, column_name in rows:
        table_cols[table_name].append(column_name)
    return {t: frozenset(cols) for t, cols in table_cols.items()}


async def _run_inline_ddl(dsn: str) -> None:
    """Connect to the target DB via DSN and apply the inline chronicler DDL."""
    conn = await asyncpg.connect(dsn=dsn)
    try:
        await _apply_inline_ddl(conn)
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_inline_ddl_matches_alembic_migration_chain(postgres_container: object) -> None:
    """Column sets for shared tables must match between inline DDL and migration chain.

    Creates two isolated databases:
    - DB-A: inline DDL from ``_apply_chronicler_schema``
    - DB-B: full Alembic chronicler migration chain

    Compares column sets for every table present in both DBs (the intersection).
    Tables only in DB-B (e.g. ``tier2_cache``) are intentionally excluded
    because the inline DDL deliberately omits tables it does not use.

    FAILS if any column appears in one DB but not the other, with a diff that
    identifies the exact table and missing column so the fix is unambiguous.
    """
    from alembic import command as alembic_command
    from butlers.migrations import _build_alembic_config

    # ── Provision DB-A: inline DDL ────────────────────────────────────────
    db_a_name = f"test_drift_inline_{uuid.uuid4().hex[:10]}"
    db_a_url = create_migration_db(postgres_container, db_a_name)

    asyncio.run(_run_inline_ddl(db_a_url))

    # ── Provision DB-B: Alembic migration chain ───────────────────────────
    db_b_name = f"test_drift_migrated_{uuid.uuid4().hex[:10]}"
    db_b_url = create_migration_db(postgres_container, db_b_name)

    config = _build_alembic_config(db_b_url, chains=[_CHRONICLER_MIGRATION_CHAIN])
    alembic_command.upgrade(config, f"{_CHRONICLER_MIGRATION_CHAIN}@head")

    # ── Introspect column sets ────────────────────────────────────────────
    inline_cols = _get_table_columns(db_a_url)
    migrated_cols = _get_table_columns(db_b_url)

    inline_names = set(inline_cols.keys())
    migrated_names = set(migrated_cols.keys())

    drift_lines: list[str] = []

    # Tables created by inline DDL but absent from the migration chain.
    missing_from_migrations = sorted(inline_names - migrated_names)
    if missing_from_migrations:
        drift_lines.append(
            "  Tables in inline DDL but absent from migration chain: "
            + ", ".join(missing_from_migrations)
        )

    # Compare column sets for tables present in both DBs.
    for table in sorted(inline_names & migrated_names):
        in_inline = inline_cols[table]
        in_migrated = migrated_cols[table]

        only_in_inline = sorted(in_inline - in_migrated)
        only_in_migrated = sorted(in_migrated - in_inline)

        if only_in_inline:
            drift_lines.append(
                f"  {table}: column(s) in inline DDL but NOT in migration chain: "
                + ", ".join(only_in_inline)
            )
        if only_in_migrated:
            drift_lines.append(
                f"  {table}: column(s) in migration chain but NOT in inline DDL: "
                + ", ".join(only_in_migrated)
            )

    if drift_lines:
        raise AssertionError(
            "Inline DDL in _apply_chronicler_schema is out of sync with the "
            "Alembic migration chain.\n"
            "Update roster/chronicler/tests/test_storage_integration.py "
            "_apply_chronicler_schema to match the migration chain.\n"
            "Drift detected:\n" + "\n".join(drift_lines)
        )
