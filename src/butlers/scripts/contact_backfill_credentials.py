"""Backfill secured contact_info rows → relationship.credentials.

SUPERSEDED (migration bead 10, bu-e2ja9 / core_115): the secured-row carve-out
shipped to ``public.entity_info`` instead (bu-pl8fy / #2042); the
``relationship.credentials`` destination referenced here was never created (stale
naming). This script reads pre-migration *snapshots* only and is retained as
historical record of the credentials carve-out design.

Migration bead bu-5oci9 — credentials carve-out backfill.

Reads every ``secured = true`` row from the pre-migration snapshot tables created by
``contact_migration_snapshot.py`` (Migration bead 1):

  - ``public.contacts_pre_migration_<YYYYMMDD>``
  - ``public.contact_info_pre_migration_<YYYYMMDD>``

For each secured row it emits a credential via a direct
``INSERT ... ON CONFLICT DO NOTHING`` into ``relationship.credentials``, relying
on the partial unique index ``uq_cred_entity_type_active`` on
``(entity_id, type) WHERE revoked_at IS NULL`` for idempotency.

Skipped rows
------------
  - Rows where the contact's ``entity_id IS NULL`` (orphans) — these are NOT
    inserted and are tallied separately.  Entity creation is out of scope for
    this script (Migration bead 5.5, bead bu-yxdzq).
  - Non-secured rows (``secured = false``) — these are handled by
    ``contact_backfill_triples.py``.

Schema mapping
--------------
  entity_id ← contacts.entity_id (UUID FK to public.entities)
  type      ← contact_info.type
  value     ← contact_info.value

Idempotency
-----------
The script is safe to run multiple times.  ``relationship.credentials`` has a
partial unique index ``uq_cred_entity_type_active`` on ``(entity_id, type) WHERE
revoked_at IS NULL``.  On conflict the INSERT does nothing.  A second run reports
the same rows as conflict-no-op with zero new inserts.

Usage
-----
    # Dry-run (default) — prints plan, no writes:
    python -m butlers.scripts.contact_backfill_credentials --date 20260601

    # Apply — perform writes and write the report:
    python -m butlers.scripts.contact_backfill_credentials --date 20260601 --apply

    # Custom report path:
    python -m butlers.scripts.contact_backfill_credentials \\
        --date 20260601 --apply \\
        --report-path /tmp/credentials-backfill-report.md

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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("contact_backfill_credentials")

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


async def _credentials_table_exists(pool: asyncpg.Pool) -> bool:
    """Return True if relationship.credentials exists."""
    result = await pool.fetchval(
        "SELECT to_regclass('relationship.credentials'::text)",
    )
    return result is not None


# ---------------------------------------------------------------------------
# Stats tracker
# ---------------------------------------------------------------------------


@dataclass
class BackfillStats:
    """Counters for a single credentials backfill run."""

    contacts_total: int = 0
    contact_info_secured_total: int = 0

    # processing outcomes
    inserted: int = 0  # credentials successfully written
    conflict_no_op: int = 0  # rows skipped by ON CONFLICT DO NOTHING (idempotent re-run)
    skipped_orphan: int = 0  # entity_id IS NULL — orphan worklist for bu-yxdzq
    errors: int = 0


# ---------------------------------------------------------------------------
# Core backfill logic
# ---------------------------------------------------------------------------


async def _insert_credential(
    pool: asyncpg.Pool,
    *,
    entity_id: uuid.UUID,
    cred_type: str,
    cred_value: str,
    apply: bool,
) -> bool:
    """Insert a credential row into relationship.credentials.

    Returns True if a new row was inserted, False if skipped by ON CONFLICT.
    The ``apply=False`` (dry-run) path logs the intended write without touching the DB.
    """
    if not apply:
        logger.debug(
            "[DRY-RUN] Would insert credential: entity_id=%s type=%s",
            entity_id,
            cred_type,
        )
        return True  # treat as "would insert" for reporting purposes

    result = await pool.fetchrow(
        """
        INSERT INTO relationship.credentials (entity_id, type, value)
        VALUES ($1, $2, $3)
        ON CONFLICT (entity_id, type) WHERE revoked_at IS NULL DO NOTHING
        RETURNING id
        """,
        entity_id,
        cred_type,
        cred_value,
    )
    # RETURNING id is present only when a row was actually inserted;
    # ON CONFLICT DO NOTHING returns no row.
    return result is not None


async def _run_backfill_with_pool(
    pool: asyncpg.Pool,
    *,
    date_label: str,
    report_path: Path,
    apply: bool,
) -> int:
    """Execute the credentials backfill using an already-open pool.

    Does NOT close the pool.
    """
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

    # --- Preflight: verify relationship.credentials exists ---
    if not await _credentials_table_exists(pool):
        logger.error(
            "Table relationship.credentials does not exist.  "
            "Run the relationship.credentials Alembic migration (rel_016) first."
        )
        return 1

    stats = BackfillStats()

    # --- Baseline counts ---
    stats.contacts_total = await pool.fetchval(f'SELECT COUNT(*) FROM public."{contacts_snap}"')
    stats.contact_info_secured_total = await pool.fetchval(
        f'SELECT COUNT(*) FROM public."{contact_info_snap}" WHERE secured = true'
    )
    logger.info(
        "Snapshot baseline: contacts=%d secured_contact_info=%d",
        stats.contacts_total,
        stats.contact_info_secured_total,
    )

    # --- Load secured contact_info rows with their resolved entity_id ---
    rows = await pool.fetch(
        f"""
        SELECT
            ci.id            AS ci_id,
            ci.contact_id,
            ci.type,
            ci.value,
            c.entity_id      AS entity_id
        FROM public."{contact_info_snap}" ci
        JOIN public."{contacts_snap}" c ON c.id = ci.contact_id
        WHERE ci.secured = true
        ORDER BY ci.type ASC, ci.contact_id ASC
        """
    )
    logger.info("Processing %d secured contact_info rows…", len(rows))

    for row in rows:
        ci_type: str = row["type"]
        ci_value: str = row["value"]
        entity_id = row["entity_id"]
        contact_id = row["contact_id"]

        # --- Orphan skip (entity_id IS NULL → worklist for bu-yxdzq) ---
        if entity_id is None:
            stats.skipped_orphan += 1
            logger.debug(
                "Skipping orphan credential row: contact_id=%s type=%s (entity_id IS NULL)",
                contact_id,
                ci_type,
            )
            continue

        try:
            inserted = await _insert_credential(
                pool,
                entity_id=entity_id,
                cred_type=ci_type,
                cred_value=ci_value,
                apply=apply,
            )
            if inserted:
                stats.inserted += 1
            else:
                stats.conflict_no_op += 1
        except Exception as exc:
            logger.error(
                "Error inserting credential for contact_id=%s type=%s: %s",
                contact_id,
                ci_type,
                exc,
            )
            stats.errors += 1

    # --- Summary ---
    logger.info(
        "Credentials backfill complete: inserted=%d conflict_no_op=%d skipped_orphan=%d errors=%d",
        stats.inserted,
        stats.conflict_no_op,
        stats.skipped_orphan,
        stats.errors,
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

_REPORT_TEMPLATE = """\
# Contact Migration — Backfill Credentials Report

**Generated:** {generated_at}
**Date label:** {date_label}
**Mode:** {mode}
**Script:** `src/butlers/scripts/contact_backfill_credentials.py`
**Epic:** bu-uhjxr (entity-redesign — contacts → triples migration)
**Bead:** bu-5oci9 (backfill secured contact_info → relationship.credentials)

---

## Input snapshot tables

| Table | Rows |
|-------|------|
| `public.{contacts_snap}` | {contacts_total} |
| `public.{contact_info_snap}` (secured only) | {contact_info_secured_total} |

---

## Backfill outcome

| Outcome | Count |
|---------|-------|
| Credentials inserted (new) | {inserted} |
| Conflict no-op (idempotent) | {conflict_no_op} |
| Skipped — orphan (`entity_id IS NULL`) | {skipped_orphan} |
| Errors | {errors} |
| **Total secured rows processed** | **{contact_info_secured_total}** |

**Parity check:** `inserted + conflict_no_op + skipped_orphan + errors`
= {contact_info_secured_total} → Actual sum: {actual_sum}  — {parity_status}

---

## Orphan credentials (worklist for bu-yxdzq)

{skipped_orphan} secured rows have contacts with `entity_id IS NULL` in the snapshot.
These were NOT backfilled — they depend on an entity being minted first by
`contact_orphan_resolver.py` (Migration bead 5.5).

---

## Design decisions recorded

- **Idempotency:** relies on partial unique index `uq_cred_entity_type_active` on
  `(entity_id, type) WHERE revoked_at IS NULL` in `relationship.credentials`.
  Conflicts trigger `DO NOTHING` so re-runs are safe (conflict_no_op > 0 on second run).
- **Orphan handling:** contacts with `entity_id IS NULL` are skipped and counted
  separately.  Entity creation is out of scope; handled by Migration bead 5.5.
- **Non-secured rows:** rows with `secured = false` are handled by
  `contact_backfill_triples.py` and are excluded from this script's scope.
- **Schema mapping:** `type` and `value` are mapped directly from `contact_info`.
  `entity_id` is resolved by joining to the contacts snapshot.
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

    actual_sum = stats.inserted + stats.conflict_no_op + stats.skipped_orphan + stats.errors
    parity_ok = actual_sum == stats.contact_info_secured_total
    parity_status = "PASS" if parity_ok else f"FAIL (expected {stats.contact_info_secured_total})"

    return _REPORT_TEMPLATE.format(
        generated_at=now,
        date_label=date_label,
        mode=mode,
        contacts_snap=contacts_snap,
        contact_info_snap=contact_info_snap,
        contacts_total=stats.contacts_total,
        contact_info_secured_total=stats.contact_info_secured_total,
        inserted=stats.inserted,
        conflict_no_op=stats.conflict_no_op,
        skipped_orphan=stats.skipped_orphan,
        errors=stats.errors,
        actual_sum=actual_sum,
        parity_status=parity_status,
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
    """Run the credentials backfill.  Returns 0 on success, 1 on error.

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
        logger.exception("Credentials backfill failed with unexpected exception")
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
        / f"entity-redesign-credentials-backfill-{today[:4]}-{today[4:6]}-{today[6:]}.md"
    )

    parser = argparse.ArgumentParser(
        description=(
            "Backfill secured contact_info rows from pre-migration snapshots "
            "into relationship.credentials (Migration bead bu-5oci9)."
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
        "reports what it would do without touching relationship.credentials.",
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
