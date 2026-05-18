"""Backfill triples from public.contact_info snapshots into relationship.entity_facts.

Migration bead 5 — entity-redesign contacts → triples migration.

Reads every existing row from the pre-migration snapshot tables created by
``contact_migration_snapshot.py`` (Migration bead 1):

  - ``public.contacts_pre_migration_<YYYYMMDD>``
  - ``public.contact_info_pre_migration_<YYYYMMDD>``

For each non-credential, non-orphan row it emits a corresponding triple via a
direct ``INSERT ... ON CONFLICT DO UPDATE`` into ``relationship.entity_facts``,
relying on the ``UNIQUE (subject, predicate, object) WHERE validity='active'``
constraint for idempotency.

Skipped rows
------------
  - ``secured = true`` — credentials carve-out per Brief §6b Amendment 1.1.A.4.
    These migrate to ``relationship.credentials`` (not yet implemented) rather
    than to the facts triple store.  The backfill report tallies them separately.
  - Orphan contacts (``contacts.entity_id IS NULL``) — these are the worklist
    for ``contact_orphan_resolver.py`` (Migration bead 5.5).  The backfill
    script counts and lists them in a separate section of the report but does
    NOT attempt entity creation.

Schema mapping (Brief §6b Amendment 1 clause 4)
------------------------------------------------
  subject   ← contacts.entity_id (UUID FK to public.entities)
  predicate ← f"has-{contact_info.type}"
  object    ← contact_info.value
  object_kind ← 'literal'
  src       ← 'migration'  (contact_info has no source column; migration is
               the authoritative author of this triple provenance)
  last_seen ← contact_info.created_at  (closest available timestamp;
               contact_info has no last_seen column)
  verified  ← false  (secured=true rows are SKIPPED, not set to verified=true;
               non-secured rows have not yet been owner-confirmed)
  conf      ← 1.0  (migrated from authoritative source)
  validity  ← 'active'
  primary   ← contact_info.is_primary

Spec anchors
------------
  - Brief §6b Amendment 1.1.A.2  (Row-count parity)
  - Brief §6b Amendment 1.1.A.4  (Credentials carve-out)
  - Brief §6b Amendment 1.1.C bead 5  (this script IS bead 5)
  - Brief §6b Amendment 1 clause 4  (Schema mapping)
  - relationship-facts/spec.md § "Requirement: Credentials carve-out"
  - relationship-facts/spec.md § "Requirement: Orphan contact handling"

Idempotency
-----------
The script is safe to run multiple times.  ``relationship.entity_facts`` has a unique
constraint ``UNIQUE (subject, predicate, object) WHERE validity='active'``.  On
conflict the row is touched (``updated_at`` refreshed) but no duplicate is
inserted.  A second run will report the same rows as "already present" with
zero new inserts.

Usage
-----
    # Dry-run (default) — prints plan, no writes:
    python -m butlers.scripts.contact_backfill_triples --date 20260601

    # Apply — perform writes and write the report:
    python -m butlers.scripts.contact_backfill_triples --date 20260601 --apply

    # Custom report path:
    python -m butlers.scripts.contact_backfill_triples \\
        --date 20260601 --apply \\
        --report-path /tmp/backfill-report.md

Environment variables
---------------------
DATABASE_URL, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD,
POSTGRES_DB control the DB connection (same env vars as the butler daemon).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("contact_backfill_triples")

# ---------------------------------------------------------------------------
# Repo-root discovery
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Return the repository root (four levels above this file in src layout)."""
    # src/butlers/scripts/<this file> → src/butlers → src → repo root
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

_DATE_LABEL_RE = re.compile(r"^\d{8}$")


def _validate_date_label(date_label: str) -> None:
    """Raise ValueError if date_label is not a safe YYYYMMDD string.

    Validates before the value is interpolated into SQL identifiers.
    """
    if not _DATE_LABEL_RE.match(date_label):
        raise ValueError(f"Invalid date_label {date_label!r}: must be exactly 8 digits (YYYYMMDD).")


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------


async def _create_pool() -> asyncpg.Pool:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        pool = await asyncpg.create_pool(dsn=database_url)
    else:
        pool = await asyncpg.create_pool(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "butlers"),
            password=os.environ.get("POSTGRES_PASSWORD", "butlers"),
            database=os.environ.get("POSTGRES_DB", "butlers"),
        )
    assert pool is not None
    return pool


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------


async def _snapshot_exists(pool: asyncpg.Pool, table: str) -> bool:
    """Return True if *table* already exists in the public schema."""
    result = await pool.fetchval(
        "SELECT to_regclass($1::text)",
        f"public.{table}",
    )
    return result is not None


async def _facts_table_exists(pool: asyncpg.Pool) -> bool:
    """Return True if relationship.entity_facts exists."""
    result = await pool.fetchval(
        "SELECT to_regclass('relationship.entity_facts'::text)",
    )
    return result is not None


# ---------------------------------------------------------------------------
# Stats tracker
# ---------------------------------------------------------------------------


@dataclass
class BackfillStats:
    """Counters for a single backfill run."""

    contacts_total: int = 0
    contact_info_total: int = 0

    # contact_info processing outcomes
    asserted: int = 0  # triples successfully written
    already_present: int = 0  # rows whose triple already existed (idempotent re-run)
    skipped_credential: int = 0  # secured=true — credential carve-out
    skipped_orphan: int = 0  # entity_id IS NULL — orphan worklist for bu-yxdzq
    errors: int = 0

    # per-source (type) breakdown for asserted rows
    per_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Orphan contact rows (for report section)
    orphan_contacts: list[dict[str, Any]] = field(default_factory=list)

    # entities.listed update outcomes (post-loop, Option A — contact-listed-flag-decision.md)
    entities_listed_updated: int = 0  # total entity rows touched by the UPDATE
    entities_listed_false_count: int = 0  # entities whose listed=false after UPDATE


# ---------------------------------------------------------------------------
# Core backfill logic
# ---------------------------------------------------------------------------


async def _assert_triple(
    pool: asyncpg.Pool,
    *,
    subject: uuid.UUID,
    predicate: str,
    object_value: str,
    is_primary: bool,
    created_at: datetime | None,
    apply: bool,
) -> bool:
    """Insert or touch a triple in relationship.entity_facts.

    Returns True if a new row was inserted, False if it was already present.
    Idempotent: ON CONFLICT updates only ``updated_at`` and ``primary`` so the
    row is "touched" but not duplicated.

    The ``apply=False`` (dry-run) path logs the intended write without touching
    the DB.
    """
    now = datetime.now(UTC)
    last_seen = created_at or now

    if not apply:
        logger.debug(
            "[DRY-RUN] Would assert triple: subject=%s predicate=%s object=%s",
            subject,
            predicate,
            object_value[:80],
        )
        return True  # treat as "would insert" for reporting purposes

    result = await pool.fetchrow(
        """
        INSERT INTO relationship.entity_facts (
            id,
            subject,
            predicate,
            object,
            object_kind,
            src,
            conf,
            last_seen,
            verified,
            "primary",
            validity,
            created_at,
            updated_at
        )
        VALUES (
            gen_random_uuid(),
            $1,
            $2,
            $3,
            'literal',
            'migration',
            1.0,
            $4,
            false,
            $5,
            'active',
            now(),
            now()
        )
        ON CONFLICT (subject, predicate, object)
        WHERE validity = 'active'
        DO UPDATE
            SET updated_at = now(),
                "primary"  = EXCLUDED."primary"
        RETURNING (xmax = 0) AS inserted
        """,
        subject,
        predicate,
        object_value,
        last_seen,
        is_primary,
    )

    return bool(result["inserted"]) if result else False


async def _update_entities_listed(
    pool: asyncpg.Pool,
    *,
    contacts_snap: str,
    apply: bool,
) -> tuple[int, int]:
    """Apply the Option-A entities.listed UPDATE and return (updated, false_count).

    Executes a single SQL UPDATE that propagates ``public.contacts.listed``
    (OR-merged across all contacts sharing the same entity) into
    ``public.entities.listed``.

    The OR-merge rule means that if *any* contact linked to an entity has
    ``listed = true``, the entity remains listed.  Only when *all* contacts
    for an entity are ``listed = false`` does the entity become unlisted.

    Entities that have no matching contact row in the snapshot are left
    untouched; they retain the column default (``true``), which is correct —
    they were never explicitly archived.

    Returns:
        (entities_listed_updated, entities_listed_false_count)
        entities_listed_updated: number of entity rows updated by the statement
        entities_listed_false_count: number of entities where listed=false after the UPDATE

    See: docs/reports/contact-listed-flag-decision.md (Option A).
    """
    if not apply:
        # Dry-run: report what *would* change without writing.
        # JOIN to entities to exclude tombstoned (merged_into) entities, matching apply-mode
        # behaviour. Note: dry-run may slightly overcount if merged entities exist without
        # contact rows, but the JOIN keeps it accurate for the common case.
        false_count: int = await pool.fetchval(
            f"""
            SELECT COUNT(*) FROM (
                SELECT c.entity_id
                FROM public."{contacts_snap}" c
                JOIN public.entities e ON e.id = c.entity_id
                WHERE c.entity_id IS NOT NULL AND e.metadata->>'merged_into' IS NULL
                GROUP BY c.entity_id
                HAVING bool_or(c.listed) = false
            ) subq
            """
        )
        logger.info(
            "[DRY-RUN] Would set entities.listed via OR-merge. "
            "Entities that would become listed=false: %d",
            false_count,
        )
        return 0, false_count

    # Apply path: single UPDATE using bool_or aggregate across all contacts per entity.
    # Excludes tombstoned entities (metadata->>'merged_into' IS NOT NULL) per codebase
    # pattern (see roster/relationship/api/router.py and contact-listed-flag-decision.md).
    result = await pool.execute(
        f"""
        UPDATE public.entities
        SET listed = subq.any_listed
        FROM (
            SELECT entity_id, bool_or(listed) AS any_listed
            FROM public."{contacts_snap}"
            WHERE entity_id IS NOT NULL
            GROUP BY entity_id
        ) subq
        WHERE entities.id = subq.entity_id
          AND entities.metadata->>'merged_into' IS NULL
        """
    )
    # asyncpg returns "UPDATE N" for DML statements.
    updated = int(result.split()[-1]) if result and result.startswith("UPDATE") else 0

    false_count = await pool.fetchval(
        "SELECT COUNT(*) FROM public.entities"
        " WHERE listed = false AND metadata->>'merged_into' IS NULL"
    )

    logger.info(
        "entities.listed UPDATE complete: rows_updated=%d entities_now_listed_false=%d",
        updated,
        false_count,
    )
    return updated, false_count


async def _run_backfill_with_pool(
    pool: asyncpg.Pool,
    *,
    date_label: str,
    report_path: Path,
    apply: bool,
) -> int:
    """Execute the backfill using an already-open pool.  Does NOT close the pool."""
    _validate_date_label(date_label)

    contacts_snap = f"contacts_pre_migration_{date_label}"
    contact_info_snap = f"contact_info_pre_migration_{date_label}"

    # --- Preflight: verify snapshot tables exist ---
    for table in (contacts_snap, contact_info_snap):
        if not await _snapshot_exists(pool, table):
            logger.error(
                "Snapshot table public.%s does not exist.  "
                "Run contact_migration_snapshot.py first (Migration bead 1).",
                table,
            )
            return 1

    # --- Preflight: verify relationship.entity_facts exists ---
    if not await _facts_table_exists(pool):
        logger.error(
            "Table relationship.entity_facts does not exist.  "
            "Run the relationship.entity_facts Alembic migration (task 10.1) first."
        )
        return 1

    stats = BackfillStats()

    # --- Baseline counts ---
    stats.contacts_total = await pool.fetchval(f'SELECT COUNT(*) FROM public."{contacts_snap}"')
    stats.contact_info_total = await pool.fetchval(
        f'SELECT COUNT(*) FROM public."{contact_info_snap}"'
    )
    logger.info(
        "Snapshot baseline: contacts=%d contact_info=%d",
        stats.contacts_total,
        stats.contact_info_total,
    )

    # --- Collect orphan contacts (entity_id IS NULL) for reporting ---
    orphan_rows = await pool.fetch(
        f"""
        SELECT id, first_name, last_name, nickname
        FROM public."{contacts_snap}"
        WHERE entity_id IS NULL
        """
    )
    stats.orphan_contacts = [dict(r) for r in orphan_rows]
    orphan_ids: set[uuid.UUID] = {r["id"] for r in orphan_rows}
    logger.info("Orphan contacts (entity_id IS NULL): %d", len(orphan_ids))

    # --- Load all contact_info rows with their resolved entity_id ---
    rows = await pool.fetch(
        f"""
        SELECT
            ci.id            AS ci_id,
            ci.contact_id,
            ci.type,
            ci.value,
            ci.is_primary,
            ci.secured,
            ci.created_at,
            c.entity_id      AS entity_id
        FROM public."{contact_info_snap}" ci
        JOIN public."{contacts_snap}" c ON c.id = ci.contact_id
        ORDER BY ci.created_at ASC NULLS LAST
        """
    )
    logger.info("Processing %d contact_info rows…", len(rows))

    for row in rows:
        ci_type: str = row["type"] or ""
        ci_value: str = row["value"] or ""
        secured: bool = bool(row["secured"])
        entity_id: uuid.UUID | None = row["entity_id"]
        contact_id: uuid.UUID = row["contact_id"]

        # --- Credentials carve-out (Amendment 1.1.A.4) ---
        if secured:
            stats.skipped_credential += 1
            logger.debug(
                "Skipping credential row: contact_id=%s type=%s (secured=true)",
                contact_id,
                ci_type,
            )
            continue

        # --- Orphan skip (entity_id IS NULL → worklist for bu-yxdzq) ---
        if entity_id is None or contact_id in orphan_ids:
            stats.skipped_orphan += 1
            logger.debug(
                "Skipping orphan row: contact_id=%s type=%s (entity_id IS NULL)",
                contact_id,
                ci_type,
            )
            continue

        # --- Predicate construction ---
        predicate = f"has-{ci_type}" if ci_type else "has-unknown"
        created_at: datetime | None = row.get("created_at")
        is_primary: bool = bool(row.get("is_primary") or False)

        try:
            inserted = await _assert_triple(
                pool,
                subject=entity_id,
                predicate=predicate,
                object_value=ci_value,
                is_primary=is_primary,
                created_at=created_at,
                apply=apply,
            )
            if inserted:
                stats.asserted += 1
                stats.per_type[ci_type] += 1
            else:
                stats.already_present += 1
                stats.per_type[ci_type] += 1  # still count in per-type even if already present
        except Exception as exc:
            logger.error(
                "Error asserting triple for contact_info ci_id=%s type=%s: %s",
                row["ci_id"],
                ci_type,
                exc,
            )
            stats.errors += 1

    # --- Post-loop: propagate contacts.listed → entities.listed (Option A) ---
    # See docs/reports/contact-listed-flag-decision.md for the decision rationale.
    (
        stats.entities_listed_updated,
        stats.entities_listed_false_count,
    ) = await _update_entities_listed(pool, contacts_snap=contacts_snap, apply=apply)

    # --- Summary ---
    logger.info(
        "Backfill complete: asserted=%d already_present=%d "
        "skipped_credential=%d skipped_orphan=%d errors=%d "
        "entities_listed_updated=%d entities_listed_false=%d",
        stats.asserted,
        stats.already_present,
        stats.skipped_credential,
        stats.skipped_orphan,
        stats.errors,
        stats.entities_listed_updated,
        stats.entities_listed_false_count,
    )

    if apply:
        # --- Render and write report ---
        report_text = _render_report(
            date_label=date_label,
            contacts_snap=contacts_snap,
            contact_info_snap=contact_info_snap,
            stats=stats,
            apply=apply,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8")
        logger.info("Report written to: %s", report_path)
    else:
        logger.info("[DRY-RUN] No writes performed.  Pass --apply to execute.")

    return 0 if stats.errors == 0 else 1


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a simple Markdown table."""
    sep = " | ".join("---" for _ in headers)
    header_line = " | ".join(headers)
    lines = [f"| {header_line} |", f"| {sep} |"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


_REPORT_TEMPLATE = """\
# Contact Migration — Backfill Triples Report

**Generated:** {generated_at}
**Date label:** {date_label}
**Mode:** {mode}
**Script:** `src/butlers/scripts/contact_backfill_triples.py`
**Epic:** bu-uhjxr (entity-redesign — contacts → triples migration)
**Bead:** bu-tr8j3 (Migration bead 5 — backfill triples from contact_info)

---

## Input snapshot tables

| Table | Rows |
|-------|------|
| `public.{contacts_snap}` | {contacts_total} |
| `public.{contact_info_snap}` | {contact_info_total} |

---

## Backfill outcome

| Outcome | Count |
|---------|-------|
| Triples asserted (new) | {asserted} |
| Already present (idempotent) | {already_present} |
| Skipped — credential (`secured=true`) | {skipped_credential} |
| Skipped — orphan (`entity_id IS NULL`) | {skipped_orphan} |
| Errors | {errors} |
| **Total contact_info rows processed** | **{contact_info_total}** |

**Parity check:** `asserted + already_present + skipped_credential + skipped_orphan + errors`
= {contact_info_total} → Actual sum: {actual_sum}  — {parity_status}

---

## Per-type (predicate) breakdown

{per_type_table}

---

## Skipped credentials

{skipped_credential} rows where `secured = true` were NOT migrated to
`relationship.entity_facts`.  Per Brief §6b Amendment 1.1.A.4 (Credentials carve-out),
these move to `relationship.credentials` (Migration bead 10.4) rather than to the
triple store.

---

## Orphan contacts (worklist for bu-yxdzq)

{orphan_count} contacts have `entity_id IS NULL` in the snapshot.  Their
`contact_info` rows were NOT backfilled — they depend on an entity being minted
first.  This section is the worklist for `contact_orphan_resolver.py`
(Migration bead 5.5, bead bu-yxdzq).

{orphan_table}

---

## entities.listed parity (Option A — contact-listed-flag-decision.md)

| Metric | Value |
|--------|-------|
| Entities whose `listed` was updated | {entities_listed_updated} |
| Entities with `listed = false` after UPDATE | {entities_listed_false_count} |

The UPDATE applied `bool_or(contacts.listed)` per `entity_id` — if any contact
linked to an entity is `listed = true`, the entity remains listed.  Entities
with no contact rows in the snapshot were not touched and retain the default
`listed = true`.

---

## Design decisions recorded

- **`verified` rule:** rows with `secured=true` are skipped (credential carve-out).
  Rows that ARE migrated receive `verified=false` because they have not yet been
  owner-confirmed via the approval ceremony.  `secured` does NOT map 1:1 to
  `verified`; the two fields serve different purposes.
- **`src`:** set to `'migration'` because `public.contact_info` has no `source`
  column.  The migration script is the authoritative author of this triple
  provenance batch.
- **`last_seen`:** mapped from `contact_info.created_at` (the closest available
  timestamp; `contact_info` has no `last_seen` column).
- **`conf`:** 1.0 — migrated from the authoritative identity table.
- **`object_kind`:** `'literal'` — all contact predicates (`has-email`,
  `has-phone`, etc.) store literal values, not entity references.
- **Idempotency:** relies on `UNIQUE (subject, predicate, object) WHERE
  validity='active'` in `relationship.entity_facts`.  Conflicts trigger
  `DO UPDATE SET updated_at=now()` so re-runs are safe.
- **`entities.listed` merge rule:** OR across all contacts per entity.  A single
  `listed=true` contact keeps the entity visible.  Set `listed=false` only when
  every linked contact is archived.  Matches the semantics of `contact_archive()`
  per `contact-listed-flag-decision.md` § Option A.
"""


def _render_report(
    *,
    date_label: str,
    contacts_snap: str,
    contact_info_snap: str,
    stats: BackfillStats,
    apply: bool,
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    mode = "APPLY" if apply else "DRY-RUN"

    actual_sum = (
        stats.asserted
        + stats.already_present
        + stats.skipped_credential
        + stats.skipped_orphan
        + stats.errors
    )
    parity_ok = actual_sum == stats.contact_info_total
    parity_status = "PASS" if parity_ok else f"FAIL (expected {stats.contact_info_total})"

    # Per-type table
    per_type_rows = [
        [f"`has-{t}`", str(n)] for t, n in sorted(stats.per_type.items(), key=lambda x: -x[1])
    ]
    per_type_table = (
        _md_table(["Predicate", "Count"], per_type_rows)
        if per_type_rows
        else "_No triples asserted._"
    )

    # Orphan table
    if stats.orphan_contacts:
        orphan_rows = [
            [
                str(o.get("id", "")),
                str(o.get("first_name") or ""),
                str(o.get("last_name") or ""),
                str(o.get("nickname") or ""),
            ]
            for o in stats.orphan_contacts
        ]
        orphan_table = _md_table(
            ["Contact ID", "first_name", "last_name", "nickname"],
            orphan_rows,
        )
    else:
        orphan_table = "_No orphan contacts found._"

    return _REPORT_TEMPLATE.format(
        generated_at=now,
        date_label=date_label,
        mode=mode,
        contacts_snap=contacts_snap,
        contact_info_snap=contact_info_snap,
        contacts_total=stats.contacts_total,
        contact_info_total=stats.contact_info_total,
        asserted=stats.asserted,
        already_present=stats.already_present,
        skipped_credential=stats.skipped_credential,
        skipped_orphan=stats.skipped_orphan,
        errors=stats.errors,
        actual_sum=actual_sum,
        parity_status=parity_status,
        per_type_table=per_type_table,
        orphan_count=len(stats.orphan_contacts),
        orphan_table=orphan_table,
        entities_listed_updated=stats.entities_listed_updated,
        entities_listed_false_count=stats.entities_listed_false_count,
    )


# ---------------------------------------------------------------------------
# Public entry point (injectable pool for tests)
# ---------------------------------------------------------------------------


async def run_backfill(
    *,
    date_label: str,
    report_path: Path,
    apply: bool,
    _pool: asyncpg.Pool | None = None,
) -> int:
    """Run the backfill.  Returns 0 on success, 1 on error.

    The ``_pool`` parameter is for testing only — callers should omit it so
    the function creates and closes its own pool.
    """
    own_pool = _pool is None
    pool = _pool if _pool is not None else await _create_pool()
    try:
        return await _run_backfill_with_pool(
            pool,
            date_label=date_label,
            report_path=report_path,
            apply=apply,
        )
    except Exception:
        logger.exception("Backfill failed with unexpected exception")
        return 1
    finally:
        if own_pool:
            await pool.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    today = datetime.now(UTC).strftime("%Y%m%d")
    default_report = (
        _repo_root()
        / "docs"
        / "reports"
        / f"entity-redesign-contact-migration-{today[:4]}-{today[4:6]}-{today[6:]}.md"
    )

    parser = argparse.ArgumentParser(
        description=(
            "Backfill triples from public.contact_info snapshots into relationship.entity_facts "
            "(Migration bead 5)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--date",
        default=today,
        metavar="YYYYMMDD",
        help=f"Date label matching the snapshot tables created by contact_migration_snapshot.py "
        f"(default: today = {today}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Execute writes.  Without this flag the script runs in dry-run mode and "
        "reports what it would do without touching relationship.entity_facts.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=default_report,
        metavar="PATH",
        help=f"Where to write the reconciliation report (default: {default_report}).",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    rc = asyncio.run(
        run_backfill(
            date_label=args.date,
            report_path=args.report_path,
            apply=args.apply,
        )
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
