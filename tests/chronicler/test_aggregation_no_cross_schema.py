"""Static-analysis guardrail: no cross-schema SQL references in chronicler handlers.

Per design.md §D17, every SQL string literal in ``roster/chronicler/api/router.py``
must reference only relations that resolve into the ``chronicler`` schema.

Rules enforced by this test:
  - ``chronicler.<name>``  → OK if ``<name>`` is in the known relation list; FAIL otherwise.
  - bare ``<name>``        → OK if ``<name>`` is in the known relation list; FAIL otherwise.
  - ``<other_schema>.<name>`` → FAIL unconditionally (cross-schema read).

This test supersedes the partial per-handler guardrails added in earlier PRs
(#1138, #1141, #1144, #1145, #1147, #1148, #1150, #1152) and provides a single
authoritative check for the entire handler module.

Backfill audit result (run 2026-04-25 against main):
  All 11 SQL strings extracted from router.py resolved to known chronicler
  relations. Zero violations found. Clean.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Target file
# ---------------------------------------------------------------------------

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

# ---------------------------------------------------------------------------
# Known chronicler-schema relations
#
# Derived from scanning all migration files in roster/chronicler/migrations/:
#   001_chronicler_tables.py  → tables + views
#   004_tier2_cache.py        → tier2_cache table
#
# Core butler tables (present in every butler schema via the core module):
#   scheduled_tasks
# ---------------------------------------------------------------------------

_CHRONICLER_RELATIONS: frozenset[str] = frozenset(
    {
        # ── Tables (001_chronicler_tables.py) ─────────────────────────────
        "source_adapter_state",
        "projection_checkpoints",
        "point_events",
        "episodes",
        "episode_event_links",
        "overrides",
        "idempotency_keys",
        # ── Views (001_chronicler_tables.py) ──────────────────────────────
        "v_latest_overrides",
        "v_episodes_corrected",
        "v_point_events_corrected",
        # ── Tables (004_tier2_cache.py) ───────────────────────────────────
        "tier2_cache",
        # ── Core butler tables (every butler schema) ──────────────────────
        "scheduled_tasks",
    }
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# SQL verb heuristic: strings that begin with a DML/DDL keyword are SQL.
# This excludes docstrings, log messages, and prose strings that happen to
# contain words like FROM or JOIN.
_SQL_START_RE = re.compile(
    r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH)\b",
    re.IGNORECASE,
)

# Capture the relation name following FROM or JOIN.
_FROM_JOIN_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)",
    re.IGNORECASE,
)


def _extract_sql_string_literals(source: str) -> list[str]:
    """Return all string constants from *source* that start with a SQL verb keyword."""
    tree = ast.parse(source)
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _SQL_START_RE.match(node.value):
                literals.append(node.value)
    return literals


def _categorize_relation(raw_token: str) -> tuple[str, str | None]:
    """Categorize a raw FROM/JOIN token into (verdict, detail).

    Returns:
        (``"ok"``, None)       – known chronicler relation
        (``"fail"``, detail)   – cross-schema read or unknown bare name
    """
    token = raw_token.strip().lower()
    if not token:
        return ("ok", None)

    if "." in token:
        schema, _, bare = token.partition(".")
        if schema == "chronicler":
            # chronicler.<name> — check the bare name is known
            if bare in _CHRONICLER_RELATIONS:
                return ("ok", None)
            return ("fail", f"schema-qualified chronicler reference to unknown relation: {token!r}")
        # Any other schema — unconditional cross-schema fail
        return ("fail", f"cross-schema reference: {token!r}")

    # Bare name — must appear in the chronicler relation list
    if token in _CHRONICLER_RELATIONS:
        return ("ok", None)
    return ("fail", f"unknown bare relation (not in chronicler schema list): {token!r}")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_no_cross_schema_sql_in_router() -> None:
    """All SQL in router.py must reference only known chronicler-schema relations.

    Validates:
    1. Every FROM/JOIN token in every SQL string literal is either:
       - a bare name present in ``_CHRONICLER_RELATIONS``, or
       - ``chronicler.<name>`` where ``<name>`` is in ``_CHRONICLER_RELATIONS``.
    2. Any other schema-qualified reference (e.g. ``public.contacts``,
       ``connectors.steam_play_history``) causes the test to fail.
    3. Bare names not in ``_CHRONICLER_RELATIONS`` also fail, because the
       test cannot infer ``search_path`` resolution at parse time.
    """
    assert _ROUTER_PATH.exists(), f"router.py not found at {_ROUTER_PATH}"
    source = _ROUTER_PATH.read_text()
    sql_literals = _extract_sql_string_literals(source)

    violations: list[str] = []
    for sql in sql_literals:
        for match in _FROM_JOIN_RE.finditer(sql):
            raw_token = match.group(1)
            verdict, detail = _categorize_relation(raw_token)
            if verdict == "fail":
                # Include a short SQL snippet for context
                snippet = sql.strip()[:120].replace("\n", " ")
                violations.append(f"{detail} [sql: {snippet!r}]")

    assert not violations, (
        f"router.py contains {len(violations)} SQL reference(s) that violate the "
        f"no-cross-schema-read invariant (D17):\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_router_has_extractable_sql_strings() -> None:
    """Sanity check: the extractor must find at least one SQL string in router.py.

    This prevents a false-green result where the router file is empty or the
    SQL heuristic silently matches nothing.
    """
    assert _ROUTER_PATH.exists(), f"router.py not found at {_ROUTER_PATH}"
    source = _ROUTER_PATH.read_text()
    sql_literals = _extract_sql_string_literals(source)
    assert len(sql_literals) >= 1, (
        "Expected to find at least one SQL string literal in router.py; "
        f"found {len(sql_literals)}. The extractor may be broken."
    )


def test_chronicler_relations_list_matches_migrations() -> None:
    """Cross-check: every name in _CHRONICLER_RELATIONS must appear in at least one migration.

    This test detects stale entries in the relations list (e.g. a renamed or
    dropped table whose old name was forgotten in the guardrail).
    """
    migrations_dir = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "migrations"
    assert migrations_dir.exists(), f"migrations dir not found at {migrations_dir}"

    # Collect all migration source text (skip __init__.py)
    migration_source = ""
    for mig_file in sorted(migrations_dir.glob("*.py")):
        if mig_file.name == "__init__.py":
            continue
        migration_source += mig_file.read_text()

    # Core tables like scheduled_tasks are not created by chronicler migrations —
    # they are part of the core butler schema. Exclude them from this check.
    _CORE_BUTLER_TABLES = frozenset({"scheduled_tasks"})

    stale: list[str] = []
    for relation in sorted(_CHRONICLER_RELATIONS - _CORE_BUTLER_TABLES):
        # A relation must appear as a table/view name in at least one migration.
        # We do a simple substring search for the quoted name or bare name.
        if relation not in migration_source:
            stale.append(relation)

    assert not stale, (
        "The following names are in _CHRONICLER_RELATIONS but do not appear in any "
        "chronicler migration file — they may be stale:\n" + "\n".join(f"  {r}" for r in stale)
    )
