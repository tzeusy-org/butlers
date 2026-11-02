# Repository-wide foreign-butler SQL ratchet (bu-ys9hdx.5).
# The retained D17 guards below are the stricter Chronicler-router case.
"""Contract: no cross-schema SQL references in chronicler handlers (RFC D17).

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

Promoted from tests/chronicler/test_aggregation_no_cross_schema.py [bu-m564i].
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

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
#   025_day_close_cache_tuple_locks.py → tuple lock registry
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
        # ── Tables (014_episode_entities.py) ──────────────────────────────
        "episode_entities",
        # ── Views (001_chronicler_tables.py) ──────────────────────────────
        "v_latest_overrides",
        "v_episodes_corrected",
        "v_point_events_corrected",
        # ── Tables (004_tier2_cache.py) ───────────────────────────────────
        "tier2_cache",
        # ── Tables (025_day_close_cache_tuple_locks.py) ───────────────────
        "day_close_cache_locks",
        # ── Core butler tables (every butler schema) ──────────────────────
        "scheduled_tasks",
        # ── Per-butler tables accessed via fan-out (not via chronicler pool) ─
        # GET /api/chronicler/ops/sessions uses db.fan_out_with_status() to query
        # each butler's sessions table through its own schema-scoped pool.  This
        # does NOT violate D17 — the fan-out never runs SQL through the chronicler
        # pool; it runs through each butler's own connection pool.
        "sessions",
        # GET /api/chronicler/who-you-were-with uses
        # db.fan_out_with_status(butler_names=["relationship"]) to resolve
        # entity_id -> canonical_name through the relationship butler's OWN
        # pool (bare 'entities' resolves via that pool's search_path to
        # public.entities). Same fan_out carve-out as 'sessions' above — never
        # runs through the chronicler pool.
        "entities",
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

# SQL keywords that can appear immediately after FROM or JOIN but are never
# relation names.  For example: "CROSS JOIN LATERAL <expr>" or "JOIN ONLY <table>".
_SQL_NON_RELATION_KEYWORDS = frozenset(
    {
        "lateral",
        "only",
        "inner",
        "outer",
        "left",
        "right",
        "full",
        "cross",
        "natural",
        "select",
        "values",
        "with",
    }
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

    # Skip SQL keywords that are not relation names (e.g. LATERAL in
    # "CROSS JOIN LATERAL <function>(...)").
    if token in _SQL_NON_RELATION_KEYWORDS:
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
# Tests
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
    # they are part of the core butler schema or accessed via fan_out.
    # 'entities' is public.entities, resolved via fan_out to the relationship
    # butler's own pool (see the _CHRONICLER_RELATIONS comment above) — it will
    # never appear in a chronicler migration file either.
    # Exclude them from this migration-presence check.
    _CORE_BUTLER_TABLES = frozenset({"scheduled_tasks", "sessions", "entities"})

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


# Repository-wide ratchet. The three D17 owners above remain its stricter
# Chronicler case: public/unknown bare relations are forbidden there as well.
_ROOT = Path(__file__).resolve().parents[2]
_BUTLER_SCHEMAS = frozenset(p.parent.name for p in (_ROOT / "roster").glob("*/butler.toml"))
_SQL_HEADS = (
    r"SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|GRANT|REVOKE|TRUNCATE|"
    r"DO|COMMENT|SET|EXPLAIN|COPY|MERGE|PREPARE|DECLARE|CALL|EXECUTE|REFRESH|VALUES|TABLE|BEGIN|START"
)
_SQL_KEYWORD_RE = re.compile(r"\b(?:" + _SQL_HEADS + r")\b", re.IGNORECASE)
_SQL_FRAGMENT_RE = re.compile(
    r"^\s*(?:(?:--[^\n]*(?:\n|$)|/\*.*?\*/)\s*)*"
    r"(?!MERGE\b(?!\s+INTO\b))(?!BEGIN\b(?!\s*(?:(?:WORK|TRANSACTION)\s*)?;))"
    r"(?!START\b(?!\s+TRANSACTION\b))"
    r'(?!TABLE\b(?!\s+(?:ONLY\s+)?[\w."]+\s*(?:$|;|ORDER\b|LIMIT\b|OFFSET\b|FETCH\b|FOR\b)))'
    r"(?:" + _SQL_HEADS + r")\b",
    re.IGNORECASE | re.DOTALL,
)
_SQL_TOKEN_RE = re.compile(r"--|/\*|['\";$]")
_DOLLAR_QUOTE_RE = re.compile(r"\$(?:[^\W\d]\w*)?\$")
_DOLLAR_CODE_BODY_RE = re.compile(
    r"(?:\bDO(?:\s+LANGUAGE\s+\w+)?|"
    r"\bCREATE(?:\s+OR\s+REPLACE)?\s+(?:FUNCTION|PROCEDURE)\b.*\bAS)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED_IDENTIFIER = r'"(?:[^"]|"")*"'
_QUALIFIED_RE = re.compile(
    r'(?:(?<![\w.])(?P<quote>"?)(?P<schema>'
    + "|".join(sorted(_BUTLER_SCHEMAS))
    + r")(?P=quote)\s*\.\s*(?P<relation>"
    + _QUOTED_IDENTIFIER
    + r"|[a-zA-Z_][a-zA-Z0-9_]*)|"
    + _QUOTED_IDENTIFIER
    + r")",
    re.IGNORECASE,
)
_DYNAMIC_SLOT = r"(?:\{[^{}]*\}|%\([^)]+\)s|%s)"
_DYNAMIC_RELATION_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE|VIEW|REFERENCES|TRUNCATE)\s+"
    r"(?:(?:ONLY|IF\s+(?:NOT\s+)?EXISTS)\s+)*"
    r'((?:"?\w+"?\s*\.\s*)?"?' + _DYNAMIC_SLOT + r'"?'
    r'(?:\s*\.\s*"?(?:\w+|' + _DYNAMIC_SLOT + r')"?)?)|' + _QUOTED_IDENTIFIER,
    re.IGNORECASE,
)


# One entry per (source file, relation). Remove entries with their source reads;
# additions require an explicitly reviewed architecture/retirement decision.
# Dynamic entries are exact normalized relation slots, not whole-file waivers.
SCHEMA_REACH_ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "src/butlers/core/confirm_admission.py",
        "switchboard.effects",
    ): "sanctioned:RFC-0038: Switchboard-owned confirm admission verifies the exact dispatched effect digest and immutable recipient/deadline; no originating-butler schema access (bu-ow5a4)",
    (
        "roster/finance/tests/test_jobs.py",
        "switchboard.butler_registry_control_plane",
    ): "sanctioned:Finance-warning-currentness: privileged independent test observer reads the committed boot successor; production Finance receives no table read grant",
    (
        "roster/switchboard/migrations/052_finance_warning_boot_read.py",
        "<dynamic:{quoted}.butler_registry_control_plane>",
    ): "sanctioned:Finance-warning-currentness: trusted migration-selected schema is quoted and baked into the fixed pure Finance boot reader; no caller relation selector",
    (
        "roster/switchboard/migrations/052_finance_warning_boot_read.py",
        "<dynamic:{quoted}.butler_boot_registrations>",
    ): "sanctioned:Finance-warning-currentness: the same selected-schema pure reader verifies existing committed boot history without creating facts or granting table access",
    (
        "src/butlers/core/finance_warning_control.py",
        "switchboard.butler_registry_control_plane",
    ): "sanctioned:Finance-warning-currentness: fixed Switchboard issuer reads its existing authorized boot facts beside the current catalog snapshot; no Finance table access or new grant",
    (
        "src/butlers/core/queue_claim.py",
        "<dynamic:{queue.table}>",
    ): "dynamic-local: bu-m0vjeb.6 reviewed module-scope ClaimQueue constants for local episodes and public.runtime_attention_outbox; validated identifiers, bound request/row values, existing caller transaction/role and per-adapter authority retained; no new foreign-schema access",
    (
        "roster/chronicler/tests/_inline_ddl.py",
        '<dynamic:"{schema_name}".sessions>',
    ): "sanctioned:RFC-0014: schema-isolation/session projection fixtures with explicit quoted synthetic schemas",
    (
        "roster/chronicler/tests/test_storage_integration.py",
        '<dynamic:"{fake_schema}".sessions>',
    ): "sanctioned:RFC-0014: schema-isolation/session projection fixtures with explicit quoted synthetic schemas",
    (
        "roster/chronicler/tests/test_storage_integration.py",
        '<dynamic:"{schema_a}".sessions>',
    ): "sanctioned:RFC-0014: schema-isolation/session projection fixtures with explicit quoted synthetic schemas",
    (
        "roster/chronicler/tests/test_storage_integration.py",
        '<dynamic:"{schema_b}".sessions>',
    ): "sanctioned:RFC-0014: schema-isolation/session projection fixtures with explicit quoted synthetic schemas",
    (
        "roster/concierge/migrations/001_fleet_views.py",
        "<dynamic:{schema}.sessions>",
    ): "sanctioned:RFC-0030: fleet session/spend views and exact source/grant fixtures",
    (
        "roster/concierge/tests/test_dashboard_read.py",
        "<dynamic:{q.FLEET_SPEND_VIEW}>",
    ): "sanctioned:RFC-0030: fleet session/spend views and exact source/grant fixtures",
    (
        "roster/concierge/tests/test_dashboard_read.py",
        "<dynamic:{schema}.sessions>",
    ): "sanctioned:RFC-0030: fleet session/spend views and exact source/grant fixtures",
    (
        "roster/concierge/tests/test_dashboard_read.py",
        "health.sessions",
    ): "sanctioned:RFC-0030: fleet session/spend views and exact source/grant fixtures",
    (
        "roster/general/migrations/003_ordinary_source_authority.py",
        "<dynamic:{schema}.collection_vocabulary_keys>",
    ): "dynamic-local: quoted current General schema for trigger-owned collection tables; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/general/migrations/003_ordinary_source_authority.py",
        "<dynamic:{schema}.collections>",
    ): "dynamic-local: quoted current General schema for trigger-owned collection tables; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/messenger/tests/test_delivery_tracking_retirement.py",
        "<dynamic:{table}>",
    ): "dynamic-local: closed retired local Messenger table tuple in a test fixture; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/jobs/relationship_jobs.py",
        "switchboard.message_inbox",
    ): "burn-down: existing switchboard.message_inbox coupling; route through owning service or make local schema authority explicit",
    (
        "roster/relationship/migrations/003_consolidate_contacts_to_public.py",
        "<dynamic:relationship.{table}>",
    ): "dynamic-local: Relationship-local table loop; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/migrations/010_drop_legacy_contact_tables.py",
        "<dynamic:{table}>",
    ): "dynamic-local: closed local legacy table tuple; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/migrations/030_dedup_contacts_drop_contacts_fks.py",
        "<dynamic:{tbl}>",
    ): "dynamic-local: downgrade restores the closed _SNAPSHOT_TABLES tuple of Relationship-local tables from their local snapshots; inventory justification only, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/migrations/034_fact_evidence_and_coverage.py",
        "<dynamic:{table}>",
    ): "dynamic-local: local evidence/coverage grant table loop; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/tests/calendar_projection.py",
        '<dynamic:"{schema}".alembic_version>',
    ): "dynamic-review: frozen baseline relation selector; exact target ownership is unresolved by literal scan and needs inspection, not product cleanup",
    (
        "roster/relationship/tests/calendar_projection.py",
        "health.calendar_sources",
    ): "burn-down: existing health.calendar_sources coupling; route through owning service or make local schema authority explicit",
    (
        "roster/relationship/tests/calendar_projection.py",
        "switchboard.message_inbox",
    ): "burn-down: existing switchboard.message_inbox coupling; route through owning service or make local schema authority explicit",
    (
        "roster/relationship/tests/test_commitment_extraction_db.py",
        "<dynamic:{table}>",
    ): "dynamic-local: closed public table cleanup tuple; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/tests/test_contacts_merge_no_stranded_triples.py",
        "<dynamic:{_child}>",
    ): "dynamic-local: public account table fixtures; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/tests/test_contacts_merge_no_stranded_triples.py",
        "<dynamic:{table}>",
    ): "dynamic-local: public account table fixtures; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/tests/test_dedup_contacts_drop_fks_migration.py",
        "<dynamic:{tbl}>",
    ): "dynamic-local: local migration table fixtures; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/tests/test_interaction_sync_automated.py",
        "switchboard.message_inbox",
    ): "burn-down: test fixture seeds switchboard.message_inbox directly; route through owning service or make local schema authority explicit",
    (
        "roster/relationship/tests/test_jobs.py",
        "<dynamic:{schema}.calendar_events>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "roster/relationship/tests/test_jobs.py",
        "health.calendar_events",
    ): "burn-down: existing health.calendar_events coupling; route through owning service or make local schema authority explicit",
    (
        "roster/relationship/tests/test_relationship_assert_fact.py",
        "<dynamic:{table}>",
    ): "dynamic-local: closed local/public effect table fixture tuple; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/tests/test_relationship_assert_fact.py",
        "<dynamic:{t}>",
    ): "dynamic-local: closed local/public effect table fixture tuple; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/tests/test_whatsapp_reconciliation.py",
        "chronicler.episode_entities",
    ): "burn-down: companion cross-schema reconciliation fixtures; retire only with executing merge-invariant survivors",
    (
        "roster/relationship/tests/test_whatsapp_reconciliation.py",
        "chronicler.episodes",
    ): "burn-down: companion cross-schema reconciliation fixtures; retire only with executing merge-invariant survivors",
    (
        "roster/relationship/tests/test_whatsapp_reconciliation.py",
        "chronicler.point_events",
    ): "burn-down: companion cross-schema reconciliation fixtures; retire only with executing merge-invariant survivors",
    (
        "roster/relationship/tools/contacts.py",
        "<dynamic:{table}>",
    ): "dynamic-local: closed contact child table tuple; local ownership not statically visible in SQL; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/relationship/tools/whatsapp_reconciliation.py",
        "<dynamic:{qualified}>",
    ): "dynamic-review: catalog-discovered reconciliation relation, quoted at execution; frozen unresolved slot needs ownership inspection, not presumed foreign-read replacement",
    (
        "roster/relationship/tools/whatsapp_reconciliation.py",
        "<dynamic:{relation}>",
    ): "dynamic-review: catalog-discovered reconciliation relation, quoted at execution; frozen unresolved slot needs ownership inspection, not presumed foreign-read replacement",
    (
        "roster/switchboard/migrations/037_qa_receiver_state_view.py",
        "<dynamic:{quoted_schema}.butler_registry>",
    ): "sanctioned:QA-projection: migration-owned receiver/control-plane read surface; schema quoted at runtime",
    (
        "roster/switchboard/migrations/037_qa_receiver_state_view.py",
        "<dynamic:{quoted_schema}.butler_registry_control_plane>",
    ): "sanctioned:QA-projection: migration-owned receiver/control-plane read surface; schema quoted at runtime",
    (
        "roster/switchboard/migrations/038_qa_local_schedule_policy.py",
        "<dynamic:{quoted_schema}.butler_registry_control_plane>",
    ): "sanctioned:QA-projection: migration-owned receiver/control-plane read surface; schema quoted at runtime",
    (
        "roster/switchboard/migrations/053_butler_local_schedule_policy.py",
        "<dynamic:{quoted_schema}.butler_registry_control_plane>",
    ): "sanctioned:butler-local-policy: migration-owned role-derived read of the caller's own control-plane row (generalises sw_038); schema quoted at runtime",
    (
        "roster/switchboard/migrations/047_goal_ledger.py",
        "<dynamic:switchboard.{table}>",
    ): "dynamic-local: owning migration's loop over its fixed ledger table tuple; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/switchboard/migrations/047_goal_ledger.py",
        "<dynamic:{qualified}>",
    ): "dynamic-local: owning migration's loop over its fixed ledger table tuple; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/switchboard/tests/test_correct_route.py",
        "<dynamic:{table}>",
    ): "dynamic-local: local route-test cleanup table selection; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/switchboard/tests/test_identity_injection.py",
        "relationship.entity_facts",
    ): "burn-down: existing relationship.entity_facts coupling; route through owning service or make local schema authority explicit",
    (
        "roster/switchboard/tests/test_identity_mint_concurrency_integration.py",
        "relationship.entity_facts",
    ): "burn-down: existing relationship.entity_facts coupling; route through owning service or make local schema authority explicit",
    (
        "roster/switchboard/tests/test_switchboard_no_fact_writes.py",
        "relationship.entity_facts",
    ): "burn-down: existing relationship.entity_facts coupling; route through owning service or make local schema authority explicit",
    (
        "roster/travel/migrations/002_metadata_backfill.py",
        "<dynamic:travel.{table}>",
    ): "dynamic-local: Travel-local closed metadata table loop; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/travel/tests/test_bookings.py",
        "<dynamic:{table}>",
    ): "dynamic-local: Travel table fixtures; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/travel/tests/test_migration_travel_002.py",
        "<dynamic:travel.{table}>",
    ): "dynamic-local: Travel-local migration table fixture tuple; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "roster/travel/tools/bookings.py",
        "<dynamic:{table}>",
    ): "dynamic-local: closed booking table_map; Travel ownership hidden behind table selector; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/api/approval_review.py",
        "<dynamic:{table}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/read_models/activity.py",
        "<dynamic:{episodes_relation}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/read_models/search.py",
        "relationship.entity_facts",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/approvals.py",
        "switchboard.dead_letter_queue",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/contacts.py",
        "relationship.entity_facts",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/data_ops.py",
        "<dynamic:{table}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/data_ops.py",
        "<dynamic:{rel}>",
    ): "dynamic-review: closed memory-relation export template; per-butler pool, never a shared schema",
    (
        "src/butlers/api/routers/data_ops.py",
        "<dynamic:{source}>",
    ): "dynamic-review: closed memory-relation export source expanded per butler on its own pool",
    (
        "src/butlers/api/routers/identity.py",
        "relationship.entity_facts",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/memory.py",
        "<dynamic:{episodes_relation}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/memory.py",
        "<dynamic:{facts_relation}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/memory.py",
        "<dynamic:{relation}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/memory.py",
        "<dynamic:{rules_relation}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/memory.py",
        "<dynamic:{tombstones_relation}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/preferences.py",
        "<dynamic:{facts_relation}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/priority_contacts.py",
        "relationship.entity_facts",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/secrets_v2.py",
        "<dynamic:{found_relation}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/secrets_v2.py",
        "<dynamic:{relation}>",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/spotify.py",
        "switchboard.connector_registry",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/api/routers/whatsapp.py",
        "switchboard.connector_registry",
    ): "pending-bu-lsxqb0.2: on-demand schema-selected dashboard/calendar source read; no exception adopted here",
    (
        "src/butlers/bootstrap_prerequisite.py",
        '<dynamic:"{admin}".bootstrap_configuration>',
    ): "dynamic-review: frozen baseline relation selector; exact target ownership is unresolved by literal scan and needs inspection, not product cleanup",
    (
        "src/butlers/chronicler/adapters/calendar.py",
        "<dynamic:{quoted}.calendar_event_entities>",
    ): "sanctioned:RFC-0014: declared read-only source-adapter surface (calendar/session slots enumerate configured schemas)",
    (
        "src/butlers/chronicler/adapters/calendar.py",
        "<dynamic:{quoted}.calendar_event_instances>",
    ): "sanctioned:RFC-0014: declared read-only source-adapter surface (calendar/session slots enumerate configured schemas)",
    (
        "src/butlers/chronicler/adapters/calendar.py",
        "<dynamic:{quoted}.calendar_events>",
    ): "sanctioned:RFC-0014: declared read-only source-adapter surface (calendar/session slots enumerate configured schemas)",
    (
        "src/butlers/chronicler/adapters/calendar.py",
        "<dynamic:{quoted}.calendar_sources>",
    ): "sanctioned:RFC-0014: declared read-only source-adapter surface (calendar/session slots enumerate configured schemas)",
    (
        "src/butlers/chronicler/adapters/comms.py",
        "relationship.entity_facts",
    ): "sanctioned:RFC-0014: declared read-only source-adapter surface (calendar/session slots enumerate configured schemas)",
    (
        "src/butlers/chronicler/adapters/google_health.py",
        "health.facts",
    ): "sanctioned:RFC-0014: declared read-only source-adapter surface (calendar/session slots enumerate configured schemas)",
    (
        "src/butlers/chronicler/adapters/meals.py",
        "health.meals",
    ): "sanctioned:RFC-0014: declared read-only source-adapter surface (calendar/session slots enumerate configured schemas)",
    (
        "src/butlers/chronicler/adapters/reading.py",
        "health.facts",
    ): "sanctioned:RFC-0014: declared read-only source-adapter surface (calendar/session slots enumerate configured schemas)",
    (
        "src/butlers/chronicler/adapters/sessions.py",
        "<dynamic:{quoted}.sessions>",
    ): "sanctioned:RFC-0014: declared read-only source-adapter surface (calendar/session slots enumerate configured schemas)",
    (
        "src/butlers/chronicler/adapters/sessions.py",
        "relationship.entity_facts",
    ): "sanctioned:RFC-0014: declared read-only source-adapter surface (calendar/session slots enumerate configured schemas)",
    (
        "src/butlers/connectors/cursor_store.py",
        "switchboard.connector_registry",
    ): "burn-down: existing switchboard.connector_registry coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/connectors/gmail_policy.py",
        "relationship.entity_facts",
    ): "burn-down: existing relationship.entity_facts coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/connectors/google_drive.py",
        "switchboard.connector_registry",
    ): "burn-down: existing switchboard.connector_registry coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/connectors/owntracks.py",
        "switchboard.connector_registry",
    ): "burn-down: existing switchboard.connector_registry coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/connectors/telegram_bot.py",
        "switchboard.media_refs",
    ): "burn-down: existing switchboard.media_refs coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/control.py",
        "switchboard.butler_secrets",
    ): "sanctioned:RFC-0038: KEY_ABSENT_STAGE startup refuses when Switchboard's own secret store holds GOAL private-key metadata (secret_key names only, never values); public.butler_secrets is checked beside it",
    (
        "src/butlers/core/condition_ledger.py",
        "<dynamic:{table}>",
    ): "dynamic-local: validated public condition-ledger relation selector; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/jobs/external_deadman.py",
        "switchboard.butler_registry",
    ): "sanctioned:REQ-infrastructure-reliability-002: Dashboard-owned deterministic deadman reads its existing Switchboard pool's registry names/TTLs to require complete fleet coverage; no other domain or write authority",
    (
        "src/butlers/jobs/external_deadman.py",
        "switchboard.butler_registry_control_plane",
    ): "sanctioned:REQ-infrastructure-reliability-002: same Dashboard controller reads receiver boot/policy/readiness facts through its existing Switchboard pool; missing facts refuse, no new role/grant or domain reach",
    (
        "src/butlers/core/control_plane_identity.py",
        "switchboard.butler_registry",
    ): "burn-down: existing switchboard.butler_registry coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/core/control_plane_identity.py",
        "switchboard.butler_registry_control_plane",
    ): "burn-down: existing switchboard.butler_registry_control_plane coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/core/corrections.py",
        "<dynamic:{corrections_table}>",
    ): "dynamic-local: schema-qualified session/state/memory correction selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/core/corrections.py",
        "<dynamic:{memory_table}>",
    ): "dynamic-local: schema-qualified session/state/memory correction selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/core/corrections.py",
        "<dynamic:{sessions_table}>",
    ): "dynamic-local: schema-qualified session/state/memory correction selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/core/corrections.py",
        "<dynamic:{state_table}>",
    ): "dynamic-local: schema-qualified session/state/memory correction selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/core/corrections.py",
        "<dynamic:{table}>",
    ): "dynamic-local: schema-qualified session/state/memory correction selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/core/deployments.py",
        '<dynamic:"{schema}".alembic_version>',
    ): "dynamic-review: frozen baseline relation selector; exact target ownership is unresolved by literal scan and needs inspection, not product cleanup",
    (
        "src/butlers/core/entity_graph_edges.py",
        "relationship.entity_facts",
    ): "burn-down: existing relationship.entity_facts coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/core/fact_authority.py",
        "switchboard.message_inbox",
    ): "burn-down: existing switchboard.message_inbox coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/core/fleet_conditions.py",
        "switchboard.butler_registry_control_plane",
    ): "burn-down: existing switchboard.butler_registry_control_plane coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/core/goal_ledger.py",
        "switchboard.admission_attempt_bindings",
    ): "sanctioned:RFC-0038: D1/D17 Switchboard-owned immutable admission retry identity; same ledger transaction and runtime role as canonical decisions (bu-lb99gq)",
    (
        "src/butlers/core/goal_ledger.py",
        "switchboard.admission_decisions",
    ): "sanctioned:RFC-0038: Switchboard-owned goal ledger; D1/D17 control-plane record with qualified SQL allocated to core/goal_ledger.py (bu-fkoexp)",
    (
        "src/butlers/core/goal_ledger.py",
        "switchboard.budgets",
    ): "sanctioned:RFC-0038: Switchboard-owned goal ledger; D1/D17 control-plane record with qualified SQL allocated to core/goal_ledger.py (bu-fkoexp)",
    (
        "src/butlers/core/goal_ledger.py",
        "switchboard.effects",
    ): "sanctioned:RFC-0038: Switchboard-owned goal ledger; D1/D17 control-plane record with qualified SQL allocated to core/goal_ledger.py (bu-fkoexp)",
    (
        "src/butlers/core/goal_ledger.py",
        "switchboard.goal_inbox",
    ): "sanctioned:RFC-0038: Switchboard-owned goal ledger; D1/D17 control-plane record with qualified SQL allocated to core/goal_ledger.py (bu-fkoexp)",
    (
        "src/butlers/core/goal_ledger.py",
        "switchboard.goals",
    ): "sanctioned:RFC-0038: Switchboard-owned goal ledger; D1/D17 control-plane record with qualified SQL allocated to core/goal_ledger.py (bu-fkoexp)",
    (
        "src/butlers/core/goal_ledger.py",
        "switchboard.invocations",
    ): "sanctioned:RFC-0038: Switchboard-owned goal ledger; D1/D17 control-plane record with qualified SQL allocated to core/goal_ledger.py (bu-fkoexp)",
    (
        "src/butlers/core/ingestion_events.py",
        "<dynamic:{table}>",
    ): "dynamic-local: partition/ingestion table selector; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/core/ingestion_events.py",
        "switchboard.connector_registry",
    ): "burn-down: existing switchboard.connector_registry coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/core/runtime_config.py",
        "<dynamic:{self._schema}.runtime_config>",
    ): "dynamic-local: configured runtime schema; source ownership is dynamic; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/core/runtime_config.py",
        "switchboard.runtime_config",
    ): "sanctioned:RFC-0038: goal admission reads only Switchboard's explicit goal_admission_config snapshot (D10, bu-fkoexp)",
    (
        "src/butlers/daemon.py",
        "relationship.entity_facts",
    ): "burn-down: existing relationship.entity_facts coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/entity_facts_channels.py",
        "relationship.entity_facts",
    ): "burn-down: existing relationship.entity_facts coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/identity.py",
        "relationship.entity_facts",
    ): "retire:bu-ys9hdx.4: identity/channel resolution source and its fixture authority",
    (
        "src/butlers/ingestion_policy.py",
        "switchboard.ingestion_rules",
    ): "burn-down: existing switchboard.ingestion_rules coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/jobs/atmosphere_consumers.py",
        "travel.trips",
    ): "burn-down: existing travel.trips coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/jobs/briefing.py",
        "education.mind_map_nodes",
    ): "burn-down: direct education/travel tables; RFC-0010 sanctions contribution views, not these raw reads",
    (
        "src/butlers/jobs/briefing.py",
        "education.mind_maps",
    ): "burn-down: direct education/travel tables; RFC-0010 sanctions contribution views, not these raw reads",
    (
        "src/butlers/jobs/briefing.py",
        "education.quiz_responses",
    ): "burn-down: direct education/travel tables; RFC-0010 sanctions contribution views, not these raw reads",
    (
        "src/butlers/jobs/briefing.py",
        "general.v_briefing_contributions",
    ): "sanctioned:RFC-0010: migration-tracked daily briefing contribution view",
    (
        "src/butlers/jobs/briefing.py",
        "travel.accommodations",
    ): "burn-down: direct education/travel tables; RFC-0010 sanctions contribution views, not these raw reads",
    (
        "src/butlers/jobs/briefing.py",
        "travel.documents",
    ): "burn-down: direct education/travel tables; RFC-0010 sanctions contribution views, not these raw reads",
    (
        "src/butlers/jobs/briefing.py",
        "travel.legs",
    ): "burn-down: direct education/travel tables; RFC-0010 sanctions contribution views, not these raw reads",
    (
        "src/butlers/jobs/briefing.py",
        "travel.trips",
    ): "burn-down: direct education/travel tables; RFC-0010 sanctions contribution views, not these raw reads",
    (
        "src/butlers/jobs/calendar_overlay.py",
        "travel.accommodations",
    ): "burn-down: direct travel overlay tables; RFC-0020 sanctions the contribution view, not these direct reads",
    (
        "src/butlers/jobs/calendar_overlay.py",
        "travel.legs",
    ): "burn-down: direct travel overlay tables; RFC-0020 sanctions the contribution view, not these direct reads",
    (
        "src/butlers/jobs/calendar_overlay.py",
        "travel.trips",
    ): "burn-down: direct travel overlay tables; RFC-0020 sanctions the contribution view, not these direct reads",
    (
        "src/butlers/jobs/calendar_prep.py",
        "switchboard.message_inbox",
    ): "burn-down: existing switchboard.message_inbox coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/jobs/context_producers.py",
        "travel.trips",
    ): "burn-down: existing travel.trips coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/jobs/deploy_drift.py",
        '<dynamic:"{schema}".alembic_version>',
    ): "dynamic-review: frozen baseline relation selector; exact target ownership is unresolved by literal scan and needs inspection, not product cleanup",
    (
        "src/butlers/jobs/flight_status.py",
        "travel.legs",
    ): "burn-down: existing travel.legs coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/jobs/flight_status.py",
        "travel.trips",
    ): "burn-down: existing travel.trips coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/jobs/home.py",
        "home.maintenance_items",
    ): "burn-down: existing home.maintenance_items coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/jobs/meeting_debrief.py",
        "relationship.entity_facts",
    ): "burn-down: existing relationship.entity_facts coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/jobs/retention.py",
        '<dynamic:connectors."{name}">',
    ): "dynamic-local: validated schema-specific session_process_logs selector; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/jobs/retention.py",
        "<dynamic:{table}>",
    ): "dynamic-local: validated schema-specific session_process_logs selector; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/jobs/spend.py",
        "<dynamic:{schema}.sessions>",
    ): "burn-down: dynamic fleet session schema enumeration; not the RFC-0030 Concierge view",
    (
        "src/butlers/modules/approvals/email_guard.py",
        "relationship.entity_facts",
    ): "retire:bu-ys9hdx.4: identity/channel resolution source and its fixture authority",
    (
        "src/butlers/modules/approvals/migrations/003_fingerprint_v2.py",
        "<dynamic:{table_name}>",
    ): "dynamic-local: local approvals table loop; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/contacts/email_identity_matching.py",
        "relationship.entity_facts",
    ): "burn-down: existing relationship.entity_facts coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/modules/dashboard_read/queries.py",
        "concierge.v_fleet_sessions",
    ): "sanctioned:RFC-0030: fleet session/spend views and exact source/grant fixtures",
    (
        "src/butlers/modules/dashboard_read/queries.py",
        "concierge.v_fleet_spend",
    ): "sanctioned:RFC-0030: fleet session/spend views and exact source/grant fixtures",
    (
        "src/butlers/modules/memory/ann_observability.py",
        "<dynamic:{table}>",
    ): "dynamic-local: closed local memory tier tables; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/knowledge_gaps.py",
        "relationship.entity_facts",
    ): "burn-down: Relationship-only branch of shared memory code; preserve local fact/coverage behavior when exposing ownership",
    (
        "src/butlers/modules/memory/knowledge_gaps.py",
        "relationship.entity_predicate_registry",
    ): "burn-down: Relationship-only branch of shared memory code; preserve local fact/coverage behavior when exposing ownership",
    (
        "src/butlers/modules/memory/knowledge_gaps.py",
        "relationship.fact_coverage",
    ): "burn-down: Relationship-only branch of shared memory code; preserve local fact/coverage behavior when exposing ownership",
    (
        "src/butlers/modules/memory/migrations/004_embedding_model_version.py",
        "<dynamic:{table}>",
    ): "dynamic-local: local memory tier table loop; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/migrations/013_content_authority.py",
        "<dynamic:{table}>",
    ): "dynamic-local: local memory tier table loop; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/reembedding.py",
        "<dynamic:{relation}>",
    ): "dynamic-local: validated memory-tier/source-schema selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/reembedding.py",
        "<dynamic:{table}>",
    ): "dynamic-local: validated memory-tier/source-schema selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/search.py",
        "<dynamic:{table}>",
    ): "dynamic-local: closed local memory table/type maps; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/storage.py",
        "<dynamic:{episodes_relation}>",
    ): "dynamic-local: memory relation helpers/source-schema selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/storage.py",
        "<dynamic:{facts_relation}>",
    ): "dynamic-local: memory relation helpers/source-schema selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/storage.py",
        "<dynamic:{links_relation}>",
    ): "dynamic-local: memory relation helpers/source-schema selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/storage.py",
        "<dynamic:{rules_relation}>",
    ): "dynamic-local: memory relation helpers/source-schema selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/storage.py",
        "<dynamic:{table}>",
    ): "dynamic-local: memory relation helpers/source-schema selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/storage.py",
        "<dynamic:{tombstones_relation}>",
    ): "dynamic-local: memory relation helpers/source-schema selectors; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/modules/memory/tools/entities.py",
        "chronicler.episode_entities",
    ): "burn-down: existing chronicler.episode_entities coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/modules/memory/tools/writing.py",
        "relationship.entity_predicate_registry",
    ): "burn-down: existing relationship.entity_predicate_registry coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/owner_bootstrap.py",
        "relationship.entity_facts",
    ): "burn-down: existing relationship.entity_facts coupling; route through owning service or make local schema authority explicit",
    (
        "src/butlers/testing/migration.py",
        "<dynamic:{_quote_ident(schema)}.alembic_version>",
    ): "dynamic-local: validated target schema migration-version table; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/testing/recall_bench.py",
        "<dynamic:{table}>",
    ): "dynamic-local: benchmark-selected memory table; baseline ownership justification, not a foreign-schema exception or cleanup authorization",
    (
        "src/butlers/testing/schema_standins.py",
        "<dynamic:{qualified}>",
    ): "dynamic-review: frozen baseline relation selector; exact target ownership is unresolved by literal scan and needs inspection, not product cleanup",
}


def _sql_nodes(source: str) -> list[tuple[int, str]]:
    """Extract SQL literals/fragments without executing source or guessing dataflow.

    Adjacent literals are folded by Python's AST. F-string expressions become
    explicit slots; format() braces retain their spelling. Entirely runtime SQL
    and relation names obtained through arbitrary Python dataflow are outside a
    literal scan's authority (they are not silently certified as local).
    """
    result: list[tuple[int, str]] = []
    if not _SQL_KEYWORD_RE.search(source):
        return result
    tree = ast.parse(source)
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and target.id.isupper()
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            constants[target.id] = value.value
    if constants:
        stores = dict.fromkeys(constants, 0)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names = [node.id]
            elif isinstance(node, ast.arg):
                names = [node.arg]
            elif isinstance(node, ast.alias):
                names = [node.asname or node.name.split(".")[0]]
            elif isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.ExceptHandler,
                    ast.MatchAs,
                    ast.MatchStar,
                ),
            ):
                names = [node.name]
            elif isinstance(node, ast.MatchMapping):
                names = [node.rest]
            else:
                names = []
            for name in names:
                if name in stores:
                    stores[name] += 1
        constants = {name: value for name, value in constants.items() if stores[name] == 1}

    class Visitor(ast.NodeVisitor):
        def visit_Expr(self, node: ast.Expr) -> None:
            if not isinstance(node.value, ast.Constant):
                self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant) -> None:
            if isinstance(node.value, str) and _is_sql_fragment(node.value):
                result.append((node.lineno, node.value))

        def visit_BinOp(self, node: ast.BinOp) -> None:
            first = node
            while isinstance(first, ast.BinOp) and isinstance(first.op, ast.Add):
                first = first.left
            if not (
                isinstance(node.op, ast.Add)
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _is_sql_fragment(first.value)
            ):
                self.generic_visit(node)
                return

            def render(part: ast.AST) -> str:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    return part.value
                if isinstance(part, ast.BinOp) and isinstance(part.op, ast.Add):
                    return render(part.left) + render(part.right)
                if isinstance(part, ast.Name) and part.id in constants:
                    return constants[part.id]
                return "{" + ast.unparse(part) + "}"

            result.append((node.lineno, render(node)))

        def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
            sql = "".join(
                part.value
                if isinstance(part, ast.Constant)
                else constants.get(ast.unparse(part.value), "{" + ast.unparse(part.value) + "}")
                for part in node.values
            )
            if _is_sql_fragment(sql):
                result.append((node.lineno, sql))

    Visitor().visit(tree)
    return result


def _mask_sql_noncode(sql: str) -> str:
    """Blank data/comments without losing quoted tokens or executable bodies.

    Comments are whitespace, including nested block comments. Quoted identifiers
    remain intact for relation matching; single/E strings and dollar data do not.
    Every replacement preserves length/newlines for original source diagnostics.
    """
    code = list(sql)
    position = statement_start = 0

    def blank(start: int, end: int) -> None:
        code[start:end] = ["\n" if char == "\n" else " " for char in sql[start:end]]

    while token := _SQL_TOKEN_RE.search(sql, position):
        position = start = token.start()
        if sql.startswith("--", start):
            end = sql.find("\n", start)
            position = len(sql) if end == -1 else end
            blank(start, position)
        elif sql.startswith("/*", start):
            position += 2
            depth = 1
            while position < len(sql) and depth:
                if sql.startswith("/*", position):
                    depth += 1
                    position += 2
                elif sql.startswith("*/", position):
                    depth -= 1
                    position += 2
                else:
                    position += 1
            blank(start, position)
        elif sql[start] in ("'", '"'):
            quote = sql[start]
            escaped = (
                quote == "'"
                and start > 0
                and sql[start - 1] in "eE"
                and (start == 1 or not (sql[start - 2].isalnum() or sql[start - 2] in "_$"))
            )
            position += 1
            while position < len(sql):
                if escaped and sql[position] == "\\":
                    position = min(position + 2, len(sql))
                elif sql[position] == quote:
                    position += 1
                    if position < len(sql) and sql[position] == quote:
                        position += 1
                    else:
                        break
                else:
                    position += 1
            if quote == "'":
                blank(start, position)
        elif (delimiter := _DOLLAR_QUOTE_RE.match(sql, start)) and (
            start == 0 or not (sql[start - 1].isalnum() or sql[start - 1] in "_$")
        ):
            end = sql.find(delimiter.group(), delimiter.end())
            position = len(sql) if end == -1 else end + len(delimiter.group())
            prefix = "".join(code[statement_start:start])
            if end != -1 and _DOLLAR_CODE_BODY_RE.search(prefix):
                # Context uses already-blanked comments, so AS/DO whitespace
                # cannot turn executable function/procedure/DO bodies into data.
                body = _mask_sql_noncode(sql[delimiter.end() : end])
                blank(start, delimiter.end())
                code[delimiter.end() : end] = body
                blank(end, position)
            else:
                blank(start, position)
        else:
            if sql[start] == ";":
                statement_start = start + 1
            position += 1
    return "".join(code)


def _is_sql_fragment(sql: str) -> bool:
    """Recognize supported commands after real, non-data statement boundaries."""
    if not _SQL_KEYWORD_RE.search(sql):
        return False
    code = _mask_sql_noncode(sql)
    return any(_SQL_FRAGMENT_RE.match(statement) for statement in code.split(";"))


def schema_reach_inventory(root: Path = _ROOT) -> dict[tuple[str, str], list[int]]:
    """Return foreign literal references and unresolved relation slots with lines."""
    found: dict[tuple[str, str], list[int]] = {}
    for directory in ("src", "roster"):
        for path in sorted((root / directory).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            owner = path.relative_to(root).parts[1] if directory == "roster" else None
            for line, sql in _sql_nodes(path.read_text()):
                # SQL data strings and comments are not relations. Preserve line
                # offsets while masking them (including source_ref/email values).
                sql = _mask_sql_noncode(sql)
                for match in _QUALIFIED_RE.finditer(sql):
                    schema = match.group("schema")
                    if schema is None:  # Consume a whole standalone quoted identifier.
                        continue
                    schema = schema.lower()
                    relation = match.group("relation").strip('"').replace('""', '"').lower()
                    if schema != owner:
                        key = (relative, f"{schema}.{relation}")
                        found.setdefault(key, []).append(line + sql[: match.start()].count("\n"))
                for match in _DYNAMIC_RELATION_RE.finditer(sql):
                    if sql.lstrip().upper().startswith("REVOKE"):
                        continue
                    if match.group(1) is None:  # Quoted alias, not a relation slot.
                        continue
                    slot = re.sub(r"\s+", " ", match.group(1)).strip()
                    key = (relative, f"<dynamic:{slot}>")
                    found.setdefault(key, []).append(line + sql[: match.start()].count("\n"))
    return found


@pytest.fixture(scope="module")
def schema_reach() -> dict[tuple[str, str], list[int]]:
    """One source snapshot shared by both ratchet assertions."""
    return schema_reach_inventory()


def test_foreign_schema_sql_is_allowlisted(
    schema_reach: dict[tuple[str, str], list[int]], tmp_path: Path
) -> None:
    """One SQL-literal fence for every source and roster owning surface."""
    inventory = schema_reach
    violations = [
        f"{path}:{line}: {relation}"
        for (path, relation), lines in sorted(inventory.items())
        if (path, relation) not in SCHEMA_REACH_ALLOWLIST
        for line in sorted(set(lines))
    ]
    assert not violations, "Unallowlisted foreign/dynamic SQL relations:\n" + "\n".join(violations)
    assert all(
        reason.startswith(
            (
                "sanctioned:",
                "retire:bu-ys9hdx.4:",
                "pending-bu-lsxqb0.2:",
                "burn-down:",
                "dynamic-local:",
                "dynamic-review:",
            )
        )
        for reason in SCHEMA_REACH_ALLOWLIST.values()
    ), "Every allowance needs a governing foreign-relation or explicit dynamic-slot reason"
    assert not any(
        path == "roster/chronicler/api/router.py" for path, _ in SCHEMA_REACH_ALLOWLIST
    ), "The Chronicler router has no foreign-schema or dynamic allowance (D17)"

    # Exercise the scanner with complete statements, including data that looks
    # like a comment. These controls share this source gate's existing owner.
    probe = tmp_path / "src" / "probe.py"
    probe.parent.mkdir()
    foreign = "health.review_unallowlisted"
    statements = {
        "select": f"SELECT * FROM {foreign}",
        "explain": f"EXPLAIN SELECT * FROM {foreign}",
        "explain_options": 'EXPLAIN (ANALYZE false, FORMAT JSON) SELECT * FROM "health"."review_unallowlisted"',
        "copy": f"COPY {foreign} TO STDOUT",
        "copy_select": f"COPY (SELECT * FROM {foreign}) TO STDOUT",
        "merge": f"MERGE INTO {foreign} USING public.x ON false WHEN MATCHED THEN DELETE",
        "lock_then_select": f"LOCK TABLE public.entities IN ACCESS SHARE MODE; SELECT * FROM {foreign}",
        "transaction": f"BEGIN; SELECT * FROM {foreign}; COMMIT",
        "transaction_work": f"BEGIN WORK; SELECT * FROM {foreign}; COMMIT",
        "transaction_comment": f"START TRANSACTION; -- actual comment\nSELECT * FROM {foreign}; COMMIT",
        "prepare": f"PREPARE probe AS SELECT * FROM {foreign}",
        "declare": f"DECLARE probe CURSOR FOR SELECT * FROM {foreign}",
        "table": f"TABLE {foreign}",
        "table_comment": f"TABLE {foreign} -- a real comment",
        "refresh": f"REFRESH MATERIALIZED VIEW {foreign}",
        "call": f"CALL {foreign}()",
        "dollar_comment_data": f"SELECT $$--$$ AS marker FROM {foreign}",
        "tagged_comment_data": f"SELECT $tag$--$tag$ AS marker FROM {foreign}",
        "tagged_block_data": f"SELECT $tag$/* '$tag$ AS marker FROM {foreign}",
        "do_body": f"DO $body$ BEGIN PERFORM * FROM {foreign}; END $body$",
        "function_body": f"CREATE FUNCTION probe() RETURNS integer AS $body$ SELECT count(*) FROM {foreign} $body$ LANGUAGE sql",
    }
    references = {}
    for name, sql in statements.items():
        probe.write_text("query = " + repr(sql))
        references[name] = set(schema_reach_inventory(tmp_path))
    assert references == {name: {("src/probe.py", foreign)} for name in statements}, (
        "Complete SQL statements and executable bodies must retain their foreign reference"
    )

    data_only = {
        "dollar_data": f"SELECT $${foreign}$$ AS marker FROM public.entities",
        "tagged_data": f"SELECT $tag$-- {foreign}$tag$ AS marker FROM public.entities",
        "case_sensitive_tag": f"SELECT $Tag$-- $tag$ {foreign}$Tag$ FROM public.entities",
        "single_quote": f"SELECT '-- {foreign}' FROM public.entities",
        "line_comment": f"SELECT 1 -- FROM {foreign}\nFROM public.entities",
        "block_comment": f"SELECT /* FROM {foreign} */ 1 FROM public.entities",
        "table_prose": "Skipping %s credential lookup; table %s is missing",
        "merge_prose": "Merge duplicate entity source into {target['canonical_name']}",
        "quoted_statement_data": f"message = 'marker; SELECT * FROM {foreign}'",
        "comment_statement_data": f"message -- marker; SELECT * FROM {foreign}",
        "dollar_statement_data": f"SELECT $$; SELECT * FROM {foreign}$$ FROM public.entities",
        "bare_local": "EXPLAIN SELECT * FROM local_relation",
    }
    nonreferences = {}
    for name, sql in data_only.items():
        probe.write_text("query = " + repr(sql))
        nonreferences[name] = schema_reach_inventory(tmp_path)
    assert nonreferences == {name: {} for name in data_only}, (
        "Data strings, comments and bare local relations must not become foreign references"
    )

    # Comments are whitespace around executable dollar bodies; quoted tokens
    # own their comment-like characters rather than swallowing later SQL.
    lexical_statements = {
        "quoted_identifier": f'SELECT 1 AS "--marker" FROM {foreign}',
        "quoted_relation": 'SELECT * FROM health."review--unallowlisted"',
        "escaped_quote": r"SELECT E'\'--' AS marker FROM " + foreign,
        "nested_comment": f"SELECT /* outer /* inner */ still comment */ 1 FROM {foreign}",
    }
    for kind, declaration, body, language in (
        (
            "function",
            "CREATE FUNCTION probe() RETURNS int AS",
            f"SELECT count(*) FROM {foreign}",
            "LANGUAGE sql",
        ),
        (
            "procedure",
            "CREATE PROCEDURE probe() AS",
            f"BEGIN PERFORM * FROM {foreign}; END;",
            "LANGUAGE plpgsql",
        ),
        ("do", "DO", f"BEGIN PERFORM * FROM {foreign}; END;", ""),
        ("do_language", "DO LANGUAGE plpgsql", f"BEGIN PERFORM * FROM {foreign}; END;", ""),
    ):
        for comment_kind, comment in (
            ("block", "/* context */"),
            ("line", "-- context\n"),
            ("nested", "/* outer /* inner */ still context */"),
        ):
            lexical_statements[f"{kind}_{comment_kind}"] = (
                f"{declaration} {comment} $body$ {body} $body$ {language}"
            )
    lexical_references = {}
    for name, sql in lexical_statements.items():
        probe.write_text("query = " + repr(sql))
        lexical_references[name] = set(schema_reach_inventory(tmp_path))
    assert lexical_references == {
        name: {
            (
                "src/probe.py",
                "health.review--unallowlisted" if name == "quoted_relation" else foreign,
            )
        }
        for name in lexical_statements
    }, "Quoted tokens and comments cannot erase executable relation references"

    lexical_data = {
        "quoted_identifier": f'SELECT 1 AS "--{foreign}" FROM public.entities',
        "escaped_quote": r"SELECT E'\'--" + foreign + "' AS marker FROM public.entities",
        "nested_comment": f"SELECT /* outer /* inner */ FROM {foreign} */ 1 FROM public.entities",
        "body_nested_data": f"DO $$ BEGIN RAISE NOTICE '--{foreign}'; END; $$",
        "function_nested_data": f"CREATE FUNCTION probe() RETURNS text AS /* context */ $$ SELECT '{foreign}' $$ LANGUAGE sql",
        "dollar_context_text": f"SELECT $$DO /* AS */ {foreign}$$ FROM public.entities",
        "local_quoted_relation": f'SELECT * FROM public."--{foreign}"',
        "quoted_semicolon": f'SELECT 1 AS "marker; FROM {foreign}" FROM public.entities',
        "doubled_identifier_quote": f'SELECT 1 AS "marker""--{foreign}" FROM public.entities',
        "escaped_backslash": r"SELECT E'\\' AS marker -- " + foreign,
    }
    lexical_nonreferences = {}
    for name, sql in lexical_data.items():
        probe.write_text("query = " + repr(sql))
        lexical_nonreferences[name] = schema_reach_inventory(tmp_path)
    assert lexical_nonreferences == {name: {} for name in lexical_data}, (
        "True comments, quoted identifier content and escaped/dollar data are not relation references"
    )
    probe.write_text("query = " + repr("SELECT E'\\'--' AS marker\nFROM " + foreign))
    assert schema_reach_inventory(tmp_path) == {("src/probe.py", foreign): [2]}, (
        "Masking must retain original source line offsets"
    )


def test_schema_reach_allowlist_has_no_stale_entries(
    schema_reach: dict[tuple[str, str], list[int]],
) -> None:
    """Deleted reads must shrink the allowlist in the same change."""
    stale = SCHEMA_REACH_ALLOWLIST.keys() - schema_reach.keys()
    assert not stale, "Stale schema-reach allowances:\n" + "\n".join(
        f"{path}: {relation}" for path, relation in sorted(stale)
    )
