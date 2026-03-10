#!/usr/bin/env python3
"""Backfill string-anchored facts with transitory entities.

Finds active facts stored with bare string subjects (entity_id IS NULL) across all
butler schemas, creates transitory entities in shared.entities, and links the facts
to those entities.

Usage:
    # Dry run — print diagnostic report only (default):
    uv run python scripts/backfill_transitory_entities.py

    # Actually run the backfill:
    uv run python scripts/backfill_transitory_entities.py --apply

    # Target a specific schema:
    uv run python scripts/backfill_transitory_entities.py --schema finance --apply

Environment:
    BUTLERS_DATABASE_URL  — required asyncpg DSN, e.g.
                            postgresql://user:pass@localhost:5432/butlers

Issue: bu-cbs.4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field

import asyncpg

# ---------------------------------------------------------------------------
# Generic labels that must NOT be given transitory entities.
# These are always owner-anchored via preamble entity_id; any leftover
# entity_id-less facts with these subjects are data quality issues, not
# unknown entities.
# ---------------------------------------------------------------------------
GENERIC_LABELS: frozenset[str] = frozenset(
    {
        "Owner",
        "owner",
        "User",
        "user",
        "Me",
        "me",
        "I",
        "Self",
        "self",
    }
)

# ---------------------------------------------------------------------------
# Scope → entity_type heuristics
# ---------------------------------------------------------------------------
_SCOPE_TYPE_MAP: dict[str, str] = {
    "finance": "organization",
    "health": "organization",
    "education": "organization",
    "travel": "place",
    "home": "other",
    "relationship": "person",
}

_PERSON_INDICATORS = frozenset(
    {"dr", "mr", "mrs", "ms", "prof", "dr.", "mr.", "mrs.", "ms.", "prof."}
)

_ORG_INDICATORS = frozenset(
    {
        "inc",
        "ltd",
        "llc",
        "corp",
        "co",
        "pte",
        "sdn",
        "bhd",
        "sg",
        "lp",
        "inc.",
        "ltd.",
        "llc.",
        "corp.",
        "co.",
        "pte.",
        "sdn.",
        "bhd.",
        "clinic",
        "hospital",
        "centre",
        "center",
        "group",
        "services",
        "solutions",
        "technologies",
        "consulting",
        "consulting",
        "systems",
    }
)

_PLACE_INDICATORS = frozenset(
    {
        "bay",
        "beach",
        "mountain",
        "park",
        "lake",
        "river",
        "street",
        "avenue",
        "road",
        "mall",
        "plaza",
        "hotel",
        "resort",
        "airport",
        "station",
    }
)


def infer_entity_type(subject: str, scope: str) -> str:
    """Infer entity_type from subject string and scope.

    Heuristics (in priority order):
    1. Scope-based map (finance → organization, travel → place, relationship → person)
    2. Token presence: name-prefix (Dr/Mr/Mrs) → person
    3. Token presence: org/company suffixes (Inc/Ltd/Pte) or industry terms → organization
    4. Token presence: geographic terms → place
    5. Fallback → other

    Args:
        subject: The human-readable subject label.
        scope:   The fact's scope namespace.

    Returns:
        One of 'person', 'organization', 'place', 'other'.
    """
    tokens = {t.lower().rstrip(".,") for t in subject.split()}

    # Scope-based override takes priority for well-known butler scopes
    if scope in _SCOPE_TYPE_MAP:
        scope_type = _SCOPE_TYPE_MAP[scope]
        # But override back to person if scope says "relationship" and the subject
        # looks like a person's first name (single word, title-cased, no org signals).
        if scope_type == "person":
            return "person"
        # For finance/health/education, default to organization — but allow
        # person-prefix tokens to override.
        if tokens & _PERSON_INDICATORS:
            return "person"
        return scope_type

    # No scope mapping — use token heuristics
    if tokens & _PERSON_INDICATORS:
        return "person"
    if tokens & _ORG_INDICATORS:
        return "organization"
    if tokens & _PLACE_INDICATORS:
        return "place"

    return "other"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticRow:
    """One (subject, scope) pair needing a transitory entity."""

    subject: str
    scope: str
    fact_count: int
    inferred_type: str


@dataclass
class BackfillResult:
    """Summary of what was done (or would be done in dry-run)."""

    schema: str
    diagnostic: list[DiagnosticRow] = field(default_factory=list)
    entities_created: int = 0
    entities_resolved: int = 0
    facts_updated: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


async def run_diagnostic(conn: asyncpg.Connection, schema: str) -> list[DiagnosticRow]:
    """Query schema.facts for string-anchored rows and return diagnostics.

    Returns distinct (subject, scope) pairs with entity_id IS NULL and
    validity = 'active', excluding generic labels.

    Args:
        conn:   asyncpg connection (already set to the target schema search path).
        schema: The butler schema name (used for logging only).

    Returns:
        List of DiagnosticRow sorted by scope then subject.
    """
    rows = await conn.fetch(
        """
        SELECT subject, scope, COUNT(*) AS fact_count
        FROM facts
        WHERE entity_id IS NULL
          AND validity = 'active'
        GROUP BY subject, scope
        ORDER BY scope, subject
        """
    )

    results: list[DiagnosticRow] = []
    for row in rows:
        subject = row["subject"]
        scope = row["scope"]
        if subject in GENERIC_LABELS:
            continue
        results.append(
            DiagnosticRow(
                subject=subject,
                scope=scope,
                fact_count=row["fact_count"],
                inferred_type=infer_entity_type(subject, scope),
            )
        )
    return results


async def resolve_or_create_entity(
    conn: asyncpg.Connection,
    subject: str,
    scope: str,
    entity_type: str,
    schema: str,
    *,
    tenant_id: str = "shared",
) -> tuple[str, bool]:
    """Find an existing entity for (subject, entity_type) or create a transitory one.

    Attempts INSERT first; on unique constraint violation falls back to SELECT.

    Args:
        conn:        asyncpg connection.
        subject:     The entity's canonical name.
        scope:       Source scope (stored in entity metadata for provenance).
        entity_type: 'person', 'organization', 'place', or 'other'.
        schema:      Butler schema name (stored in entity metadata for provenance).
        tenant_id:   Tenant scope for the entity row.

    Returns:
        Tuple of (entity_uuid_str, was_created).
    """
    metadata = json.dumps(
        {
            "unidentified": True,
            "source": "backfill",
            "source_butler": schema,
            "source_scope": scope,
        }
    )

    # Try INSERT first
    try:
        entity_id = await conn.fetchval(
            """
            INSERT INTO shared.entities
                (tenant_id, canonical_name, entity_type, aliases, metadata, roles)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            RETURNING id
            """,
            tenant_id,
            subject,
            entity_type,
            [],
            metadata,
            [],
        )
        return str(entity_id), True
    except asyncpg.UniqueViolationError:
        pass

    # Entity already exists — resolve it
    entity_id = await conn.fetchval(
        """
        SELECT id FROM shared.entities
        WHERE tenant_id = $1
          AND canonical_name = $2
          AND entity_type = $3
          AND (metadata->>'merged_into') IS NULL
        """,
        tenant_id,
        subject,
        entity_type,
    )
    if entity_id is None:
        # Tombstoned entity — widen to any type as a fallback
        entity_id = await conn.fetchval(
            """
            SELECT id FROM shared.entities
            WHERE tenant_id = $1
              AND canonical_name = $2
              AND (metadata->>'merged_into') IS NULL
            LIMIT 1
            """,
            tenant_id,
            subject,
        )
    if entity_id is None:
        raise RuntimeError(
            f"Entity '{subject}' (type={entity_type}) exists but could not be resolved "
            f"after constraint violation"
        )
    return str(entity_id), False


async def backfill_schema(
    pool: asyncpg.Pool,
    schema: str,
    *,
    apply: bool,
    tenant_id: str = "shared",
) -> BackfillResult:
    """Backfill a single butler schema.

    For each unique (subject, scope) pair with entity_id IS NULL:
      1. Infer entity_type from subject + scope
      2. Create (or resolve) a transitory entity in shared.entities
      3. UPDATE matching facts to set entity_id

    Args:
        pool:      asyncpg connection pool.
        schema:    Butler schema name (e.g. 'finance', 'health').
        apply:     When False, run diagnostics only (no writes).
        tenant_id: Tenant scope for created entities.

    Returns:
        BackfillResult summarising what happened (or what would happen).
    """
    result = BackfillResult(schema=schema)

    async with pool.acquire() as conn:
        # Set search_path so unqualified table references resolve to this schema.
        await conn.execute(f"SET search_path TO {schema}, shared, public")

        # Run diagnostic
        try:
            diagnostic_rows = await run_diagnostic(conn, schema)
        except asyncpg.UndefinedTableError:
            # Schema exists but has no facts table — skip silently
            return result

        result.diagnostic = diagnostic_rows

        if not apply:
            return result

        # Apply backfill inside one transaction per schema
        async with conn.transaction():
            for row in diagnostic_rows:
                try:
                    entity_id_str, was_created = await resolve_or_create_entity(
                        conn,
                        row.subject,
                        row.scope,
                        row.inferred_type,
                        schema,
                        tenant_id=tenant_id,
                    )
                except Exception as exc:
                    result.errors.append(
                        f"[{schema}] Entity create/resolve failed for "
                        f"subject={row.subject!r} scope={row.scope!r}: {exc}"
                    )
                    continue

                if was_created:
                    result.entities_created += 1
                else:
                    result.entities_resolved += 1

                # Update all matching facts in this schema
                updated = await conn.execute(
                    f"""
                    UPDATE {schema}.facts
                    SET entity_id = $1
                    WHERE entity_id IS NULL
                      AND subject = $2
                      AND scope = $3
                      AND validity = 'active'
                    """,
                    entity_id_str,
                    row.subject,
                    row.scope,
                )
                # asyncpg returns status string like "UPDATE 3"
                n = int(updated.split()[-1])
                result.facts_updated += n

    return result


async def discover_memory_schemas(pool: asyncpg.Pool) -> list[str]:
    """Return all schemas that contain a 'facts' table (memory module enabled).

    Queries information_schema.tables to find schemas with a facts table,
    excluding public, information_schema, and pg_* schemas.

    Args:
        pool: asyncpg connection pool.

    Returns:
        Sorted list of schema names.
    """
    rows = await pool.fetch(
        """
        SELECT DISTINCT table_schema
        FROM information_schema.tables
        WHERE table_name = 'facts'
          AND table_schema NOT IN ('public', 'information_schema', 'shared')
          AND table_schema NOT LIKE 'pg_%'
        ORDER BY table_schema
        """
    )
    return [row["table_schema"] for row in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_diagnostic(result: BackfillResult, *, verbose: bool = False) -> None:
    """Print a human-readable diagnostic summary."""
    total_facts = sum(r.fact_count for r in result.diagnostic)
    print(
        f"\n[{result.schema}] {len(result.diagnostic)} distinct (subject, scope) pairs, "
        f"{total_facts} total string-anchored facts"
    )
    if result.diagnostic and verbose:
        print(f"  {'subject':<40} {'scope':<15} {'inferred_type':<15} {'facts':>6}")
        print(f"  {'-' * 40} {'-' * 15} {'-' * 15} {'-' * 6}")
        for row in result.diagnostic:
            print(
                f"  {row.subject:<40} {row.scope:<15} {row.inferred_type:<15} {row.fact_count:>6}"
            )


def _print_result(result: BackfillResult) -> None:
    """Print a backfill execution summary."""
    print(
        f"\n[{result.schema}] "
        f"created={result.entities_created} "
        f"resolved={result.entities_resolved} "
        f"facts_updated={result.facts_updated}"
    )
    for err in result.errors:
        print(f"  ERROR: {err}", file=sys.stderr)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill string-anchored facts with transitory entities"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually apply the backfill (default: dry-run diagnostic only)",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help="Target a specific butler schema (default: all schemas with a facts table)",
    )
    parser.add_argument(
        "--tenant-id",
        default="shared",
        help="Tenant ID to use for created entities (default: shared)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Print per-row detail in diagnostic mode",
    )
    args = parser.parse_args(argv)

    db_url = os.environ.get("BUTLERS_DATABASE_URL")
    if not db_url:
        print("ERROR: BUTLERS_DATABASE_URL environment variable is not set", file=sys.stderr)
        return 1

    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
    except Exception as exc:
        print(f"ERROR: Failed to connect to database: {exc}", file=sys.stderr)
        return 1

    try:
        if args.schema:
            schemas = [args.schema]
        else:
            schemas = await discover_memory_schemas(pool)
            if not schemas:
                print("No schemas with a 'facts' table found. Nothing to do.")
                return 0
            print(f"Discovered {len(schemas)} schema(s) with memory module: {schemas}")

        mode = "APPLY" if args.apply else "DRY RUN (use --apply to write changes)"
        print(f"\nMode: {mode}")

        results: list[BackfillResult] = []
        for schema in schemas:
            result = await backfill_schema(pool, schema, apply=args.apply, tenant_id=args.tenant_id)
            results.append(result)

            if args.apply:
                _print_result(result)
            else:
                _print_diagnostic(result, verbose=args.verbose)

        # Totals
        total_pairs = sum(len(r.diagnostic) for r in results)
        total_facts = sum(sum(d.fact_count for d in r.diagnostic) for r in results)
        total_errors = sum(len(r.errors) for r in results)

        print(
            f"\n{'=' * 60}\n"
            f"Total: {total_pairs} (subject, scope) pairs, "
            f"{total_facts} string-anchored facts"
        )
        if args.apply:
            total_created = sum(r.entities_created for r in results)
            total_resolved = sum(r.entities_resolved for r in results)
            total_updated = sum(r.facts_updated for r in results)
            print(
                f"       {total_created} entities created, "
                f"{total_resolved} entities resolved, "
                f"{total_updated} facts updated"
            )
            if total_errors:
                print(f"       {total_errors} errors (see stderr)")
                return 1
        else:
            print("Run with --apply to perform the backfill.")

        return 0

    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
