"""Drift detector: BUTLER_SESSIONS_COLUMNS vs production core.sessions migration.

Guards against phantom columns in the fake butler sessions table used by
Chronicler integration tests.  A phantom column is one that appears in
``BUTLER_SESSIONS_COLUMNS`` (and therefore in the fake DDL created by
``make_sessions_table_ddl()``) but does NOT exist in the canonical production
migration for ``core.sessions``.

Background
----------
PR #1313 (bu-pylew) created ``roster/chronicler/tests/_inline_ddl.py`` as the
single source of truth for the fake butler sessions DDL used in integration
tests.  The module docstring noted: "A future drift-detector test should
compare BUTLER_SESSIONS_COLUMNS against the production migration to enforce
this automatically."  Without this detector, a future migration that removes a
column from ``core.sessions`` could silently leave a phantom column in the
fake table — exactly the failure mode that the bu-fkqv0 / bu-8orvr cycle hit
(missing ``ingestion_event_id``).

The fake table is an **intentional subset** of the production schema.  Tests do
not need every column; they only need the columns the adapter actually queries.
Therefore this test does NOT fail when production has columns that are absent
from the fake table — it only fails if the fake table references columns that
do NOT exist in production.

Mechanism
---------
1. Parse ``BUTLER_SESSIONS_COLUMNS`` from
   ``roster/chronicler/tests/_inline_ddl.py`` (Python import; no DB needed).
2. Parse the canonical production column list by extracting the
   ``CREATE TABLE IF NOT EXISTS sessions (...)`` block from
   ``alembic/versions/core/core_001_foundation.py``.
3. Compute the set difference: ``BUTLER_SESSIONS_COLUMNS - production_columns``.
4. Fail with a clear message listing each phantom column so the fix is
   unambiguous.

This test is **pure Python** — no Docker, no DB, no integration marker.

How to update when a column is added to core.sessions
------------------------------------------------------
1. Add the migration file under ``alembic/versions/core/``.
2. If the Chronicler adapter (``src/butlers/chronicler/adapters/sessions.py``)
   starts reading the new column, add it to ``BUTLER_SESSIONS_COLUMNS`` in
   ``roster/chronicler/tests/_inline_ddl.py`` AND add the column definition to
   ``make_sessions_table_ddl()``.
3. Re-run this test — it will pass once both copies are in sync.

How to update when a column is removed from core.sessions
---------------------------------------------------------
1. Add the migration file under ``alembic/versions/core/``.
2. Remove the column from ``BUTLER_SESSIONS_COLUMNS`` and from
   ``make_sessions_table_ddl()`` in ``roster/chronicler/tests/_inline_ddl.py``.
3. Re-run this test — it will pass once the phantom column is removed.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The canonical production migration that creates core.sessions
_FOUNDATION_MIGRATION = _REPO_ROOT / "alembic" / "versions" / "core" / "core_001_foundation.py"

# The _inline_ddl module (imported as Python to avoid parsing overhead)
_INLINE_DDL_MODULE = _REPO_ROOT / "roster" / "chronicler" / "tests" / "_inline_ddl.py"


# ---------------------------------------------------------------------------
# Helpers
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


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


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
