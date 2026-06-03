"""Pre-migration snapshot: public.contacts + public.contact_info.

SUPERSEDED (migration bead 10, bu-e2ja9 / core_115): ``public.contact_info`` has
been dropped. This one-shot snapshot tool read the *live* legacy table before the
cut-over and is retained only as historical record. The drop migration (core_115)
takes its own ``contact_info_dropbak_core_115`` snapshot at drop time.

Creates date-stamped snapshot tables and emits a baseline row-count report.

This script is meant to be run by an operator **before** the contacts →
triples migration cut-over.  It captures the full state of
``public.contacts`` and ``public.contact_info`` so that:

  - Post-migration parity checks have a stable reference (Migration bead 2).
  - The orphan-resolver script (Migration bead 5.5) can read from the snapshot
    rather than live tables.
  - Auditors can verify zero data loss at any point during the migration.

Snapshot tables created
-----------------------
  - ``public.contacts_pre_migration_YYYYMMDD``
  - ``public.contact_info_pre_migration_YYYYMMDD``

Both tables are created via ``CREATE TABLE … AS SELECT *`` and therefore
inherit all columns of their source tables at snapshot time.

Idempotency
-----------
If the snapshot tables for today's date already exist the script exits
immediately with a success message.  Use ``--date`` to override the date
(YYYYMMDD format) if you need to create snapshots labelled for a specific
cut-over date.

Report
------
Writes ``docs/reports/contact-migration-baseline.md`` relative to the
repository root (auto-detected as the parent three levels above this file).
The path is configurable via ``--report-path``.

Usage
-----
    python -m butlers.scripts.contact_migration_snapshot
    python -m butlers.scripts.contact_migration_snapshot --dry-run
    python -m butlers.scripts.contact_migration_snapshot --date 20260601
    python -m butlers.scripts.contact_migration_snapshot \\
        --report-path /tmp/baseline.md

Environment variables
---------------------
DATABASE_URL, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD
control the DB connection (same env vars as the butler daemon).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
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
logger = logging.getLogger("contact_migration_snapshot")

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
# Core logic
# ---------------------------------------------------------------------------


async def _snapshot_exists(pool: asyncpg.Pool, table: str) -> bool:
    """Return True if *table* already exists in the public schema."""
    # table is expected to be like "contacts_pre_migration_20260601"
    result = await pool.fetchval(
        "SELECT to_regclass($1::text)",
        f"public.{table}",
    )
    return result is not None


async def _create_snapshot(pool: asyncpg.Pool, source: str, target: str) -> int:
    """CREATE TABLE public.target AS SELECT * FROM public.source; return row count."""
    await pool.execute(f'CREATE TABLE public."{target}" AS SELECT * FROM public."{source}"')
    count: int = await pool.fetchval(f'SELECT COUNT(*) FROM public."{target}"')
    return count


# ---------------------------------------------------------------------------
# Breakdown queries
# ---------------------------------------------------------------------------


async def _contacts_breakdown(pool: asyncpg.Pool, table: str) -> list[dict]:
    """Per-entity_id-nullity + has_entity breakdown for contacts."""
    rows = await pool.fetch(
        f"""
        SELECT
            CASE WHEN entity_id IS NULL THEN 'no_entity' ELSE 'has_entity' END AS bucket,
            COUNT(*) AS row_count
        FROM public."{table}"
        GROUP BY 1
        ORDER BY 1
        """
    )
    return [dict(r) for r in rows]


async def _contact_info_breakdown(pool: asyncpg.Pool, table: str) -> list[dict]:
    """Per-type breakdown for contact_info.

    The ``type`` column identifies the channel/source (e.g. telegram, email,
    phone, google_health) and effectively maps to the originating butler or
    connector.
    """
    rows = await pool.fetch(
        f"""
        SELECT
            type,
            COUNT(*) AS row_count,
            COUNT(CASE WHEN secured THEN 1 END) AS secured_count,
            COUNT(CASE WHEN is_primary THEN 1 END) AS primary_count
        FROM public."{table}"
        GROUP BY type
        ORDER BY row_count DESC, type
        """
    )
    return [dict(r) for r in rows]


async def _orphan_count(pool: asyncpg.Pool, contacts_table: str) -> int:
    """Count contacts with entity_id IS NULL (orphans for migration bead 5.5)."""
    result: int = await pool.fetchval(
        f'SELECT COUNT(*) FROM public."{contacts_table}" WHERE entity_id IS NULL'
    )
    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

_REPORT_TEMPLATE = """\
# Contact Migration Baseline Report

**Generated:** {generated_at}
**Date label:** {date_label}
**Script:** `src/butlers/scripts/contact_migration_snapshot.py`
**Epic:** bu-uhjxr (entity-redesign — contacts → triples migration)
**Bead:** bu-5ekvq (Migration bead 1 — pre-migration snapshot)

---

## Snapshot tables created

| Table | Source |
|-------|--------|
| `public.{contacts_snapshot}` | `public.contacts` |
| `public.{contact_info_snapshot}` | `public.contact_info` |

---

## Row counts

| Table | Row count |
|-------|-----------|
| `public.contacts` (snapshot) | {contacts_total} |
| `public.contact_info` (snapshot) | {contact_info_total} |

---

## contacts breakdown (entity linkage)

{contacts_breakdown_table}

**Orphan contacts** (entity_id IS NULL): **{orphan_count}**

Orphan contacts are unresolved before backfill.  They will be handled by
the orphan-resolver script (Migration bead 5.5,
`src/butlers/scripts/contact_orphan_resolver.py`), which reads from the
`public.{contacts_snapshot}` snapshot table.

---

## contact_info breakdown (per type / source channel)

The `type` column identifies the channel or connector that contributed each
row.  Types map to source butlers / connectors as follows (non-exhaustive):

| type | originating butler / connector |
|------|-------------------------------|
| `email` | relationship butler (manual entry, contacts sync) |
| `telegram` | Telegram connector / switchboard |
| `phone` | relationship butler (manual entry, contacts sync) |
| `google_health` | Google Health connector |
| `home_assistant_token` | home butler |
| `google_account` | OAuth / calendar connector |
| *(other)* | varies — inspect `secured` column for credentials |

{contact_info_breakdown_table}

**Note:** rows where `secured = true` are credentials (RFC 0004 §4).  They
are **not** migrated to `relationship.entity_facts`; they will remain in a
`contact_info_credentials` sub-table or move to `relationship.credentials`.
See Brief §6b Amendment 1.1.A.4 for the credentials carve-out decision.

---

## Sign-off requirements before cut-over

- [ ] Snapshot tables verified (SELECT COUNT(*) matches above)
- [ ] Orphan count reviewed and orphan-resolution strategy confirmed
- [ ] Credentials carve-out decision recorded (Amendment 1.1.A.4)
- [ ] Migration bead 2 (write-path inventory) complete
- [ ] Migration bead 3 (central writer `relationship_assert_fact()`) deployed
"""


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a simple Markdown table."""
    sep = " | ".join("---" for _ in headers)
    header_line = " | ".join(headers)
    lines = [f"| {header_line} |", f"| {sep} |"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def _render_report(
    *,
    date_label: str,
    contacts_snapshot: str,
    contact_info_snapshot: str,
    contacts_total: int,
    contact_info_total: int,
    contacts_breakdown: list[dict],
    contact_info_breakdown: list[dict],
    orphan_count: int,
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    contacts_rows = [[r["bucket"], str(r["row_count"])] for r in contacts_breakdown]
    contacts_table = _md_table(["Bucket", "Row count"], contacts_rows)

    ci_rows = [
        [
            f"`{r['type']}`",
            str(r["row_count"]),
            str(r["secured_count"]),
            str(r["primary_count"]),
        ]
        for r in contact_info_breakdown
    ]
    ci_table = _md_table(
        ["type", "Total rows", "secured (credentials)", "is_primary"],
        ci_rows,
    )

    return _REPORT_TEMPLATE.format(
        generated_at=now,
        date_label=date_label,
        contacts_snapshot=contacts_snapshot,
        contact_info_snapshot=contact_info_snapshot,
        contacts_total=contacts_total,
        contact_info_total=contact_info_total,
        contacts_breakdown_table=contacts_table,
        contact_info_breakdown_table=ci_table,
        orphan_count=orphan_count,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _run_snapshot_with_pool(
    pool: asyncpg.Pool,
    *,
    date_label: str,
    report_path: Path,
    dry_run: bool,
) -> int:
    """Execute the snapshot using an already-open pool.  Does NOT close the pool."""
    _validate_date_label(date_label)

    contacts_snap = f"contacts_pre_migration_{date_label}"
    contact_info_snap = f"contact_info_pre_migration_{date_label}"

    # Idempotency check
    contacts_exists = await _snapshot_exists(pool, contacts_snap)
    info_exists = await _snapshot_exists(pool, contact_info_snap)

    if contacts_exists and info_exists:
        logger.info("Snapshot tables for %s already exist — nothing to do.", date_label)
        logger.info("  public.%s", contacts_snap)
        logger.info("  public.%s", contact_info_snap)
        return 0

    if contacts_exists or info_exists:
        existing = contacts_snap if contacts_exists else contact_info_snap
        missing = contact_info_snap if contacts_exists else contacts_snap
        logger.error(
            "Partial snapshot detected for %s: public.%s exists but public.%s does not. "
            "Manual cleanup required before re-running.",
            date_label,
            existing,
            missing,
        )
        return 1

    if dry_run:
        logger.info("[DRY-RUN] Would create:")
        logger.info("  CREATE TABLE public.%s AS SELECT * FROM public.contacts", contacts_snap)
        logger.info(
            "  CREATE TABLE public.%s AS SELECT * FROM public.contact_info",
            contact_info_snap,
        )
        contacts_total: int = await pool.fetchval("SELECT COUNT(*) FROM public.contacts")
        ci_total: int = await pool.fetchval("SELECT COUNT(*) FROM public.contact_info")
        logger.info("[DRY-RUN] public.contacts → %d rows", contacts_total)
        logger.info("[DRY-RUN] public.contact_info → %d rows", ci_total)
        return 0

    # Create snapshots
    logger.info("Creating snapshot: public.%s …", contacts_snap)
    contacts_total = await _create_snapshot(pool, "contacts", contacts_snap)
    logger.info("  %d rows copied.", contacts_total)

    logger.info("Creating snapshot: public.%s …", contact_info_snap)
    ci_total = await _create_snapshot(pool, "contact_info", contact_info_snap)
    logger.info("  %d rows copied.", ci_total)

    # Collect breakdown stats from the snapshot tables
    contacts_breakdown = await _contacts_breakdown(pool, contacts_snap)
    ci_breakdown = await _contact_info_breakdown(pool, contact_info_snap)
    orphans = await _orphan_count(pool, contacts_snap)

    logger.info("Orphan contacts (entity_id IS NULL): %d", orphans)

    # Render and write report
    report_text = _render_report(
        date_label=date_label,
        contacts_snapshot=contacts_snap,
        contact_info_snapshot=contact_info_snap,
        contacts_total=contacts_total,
        contact_info_total=ci_total,
        contacts_breakdown=contacts_breakdown,
        contact_info_breakdown=ci_breakdown,
        orphan_count=orphans,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("Report written to: %s", report_path)

    return 0


async def run_snapshot(
    *,
    date_label: str,
    report_path: Path,
    dry_run: bool,
    _pool: asyncpg.Pool | None = None,
) -> int:
    """Run the snapshot.  Returns 0 on success, 1 on error.

    The ``_pool`` parameter is for testing only — callers should omit it so
    the function creates and closes its own pool.
    """
    own_pool = _pool is None
    pool = _pool if _pool is not None else await _create_pool()
    try:
        return await _run_snapshot_with_pool(
            pool,
            date_label=date_label,
            report_path=report_path,
            dry_run=dry_run,
        )
    except Exception:
        logger.exception("Snapshot failed")
        return 1
    finally:
        if own_pool:
            await pool.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    today = datetime.now(UTC).strftime("%Y%m%d")
    default_report = _repo_root() / "docs" / "reports" / "contact-migration-baseline.md"

    parser = argparse.ArgumentParser(
        description="Create pre-migration snapshots of public.contacts and public.contact_info.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--date",
        default=today,
        metavar="YYYYMMDD",
        help=f"Date label for snapshot table names (default: today = {today}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Log what would be done without creating tables or writing the report.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=default_report,
        metavar="PATH",
        help=f"Where to write the baseline report (default: {default_report}).",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    rc = asyncio.run(
        run_snapshot(
            date_label=args.date,
            report_path=args.report_path,
            dry_run=args.dry_run,
        )
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
