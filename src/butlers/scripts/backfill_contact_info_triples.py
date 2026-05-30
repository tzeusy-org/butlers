"""Backfill legacy contact_info rows into relationship.entity_facts has-* triples.

Bead: bu-q8rro — Contact-info legacy backfill job.

Background
----------
After the write-path cut-over (Migration bead 8, bu-k9ylx), NEW channel writes
go directly to ``relationship.entity_facts``.  However, pre-existing rows in
``public.contact_info`` that were created before the cut-over were never
migrated — the snapshot-based migration (contact_backfill_triples.py, bead
bu-tr8j3) targeted the per-date snapshot tables rather than the live table.

This script fills that gap: it reads the LIVE ``public.contact_info`` table,
identifies rows whose (entity_id, predicate, value) triple is not yet present
in ``relationship.entity_facts``, and asserts those triples via
:func:`relationship_assert_fact` — the authoritative central writer.

All writes go through ``relationship_assert_fact`` so predicates, validity,
owner carve-out, and value_hash handling are consistent with the rest of the
write path.

Skip rules
----------
1. ``secured = true`` — credential carve-out (bu-pl8fy / bu-fa5ex handle these
   separately in ``relationship.credentials``).  NOT backfilled.
2. ``entity_id IS NULL`` — the contact has no linked entity yet.  Logged and
   counted; requires the orphan-resolver to run first.
3. Unmapped type — ``type`` is not in the ``_CI_TYPE_TO_PREDICATE`` mapping
   (e.g. ``telegram_chat_id``, ``telegram_user_id``, ``telegram_username``,
   ``google_health``, ``home_assistant_url``).  Logged and counted.

Gap detection
-------------
A contact_info row is a "gap" when no active triple exists in
``relationship.entity_facts`` with the same (subject=entity_id,
predicate=has-<type>, object=value).

Idempotency
-----------
The gap check (SELECT before asserting) combined with
``relationship_assert_fact``'s own ON CONFLICT supersession logic ensures
re-running the script is safe — existing triples are not duplicated; the
``unchanged`` outcome is counted and reported.

Usage
-----
    # Dry-run (default) — report what would be backfilled, no writes:
    python -m butlers.scripts.backfill_contact_info_triples

    # Apply — perform writes:
    python -m butlers.scripts.backfill_contact_info_triples --apply

    # Report file:
    python -m butlers.scripts.backfill_contact_info_triples --apply \\
        --report-path /tmp/ci-backfill.md

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
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
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
logger = logging.getLogger("backfill_contact_info_triples")

# ---------------------------------------------------------------------------
# Repo-root discovery
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Return the repository root (three levels above this file in src layout)."""
    # src/butlers/scripts/<this file> → src/butlers → src → repo root
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Lazy import of the central writer
# ---------------------------------------------------------------------------


def _load_assert_fact():  # type: ignore[return]
    """Lazy-import relationship_assert_fact from the roster tools package.

    Uses the same roster-relative import path established in identity.py and
    modules/contacts/backfill.py to avoid a hard dependency cycle.
    """
    repo = _repo_root()
    tools_path = repo / "roster" / "relationship" / "tools" / "relationship_assert_fact.py"
    if not tools_path.exists():
        raise FileNotFoundError(
            f"Central writer not found at {tools_path}. "
            "Run from the repository root with `uv run python -m butlers.scripts...`"
        )
    import importlib.util

    spec = importlib.util.spec_from_file_location("relationship_assert_fact", tools_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


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


async def _facts_table_exists(pool: asyncpg.Pool) -> bool:
    return await pool.fetchval(
        "SELECT EXISTS ("
        "SELECT 1 FROM pg_tables WHERE schemaname = 'relationship' AND tablename = 'entity_facts'"
        ")",
    )


async def _predicate_registry_exists(pool: asyncpg.Pool) -> bool:
    return await pool.fetchval(
        "SELECT EXISTS ("
        "SELECT 1 FROM pg_tables"
        " WHERE schemaname = 'relationship' AND tablename = 'entity_predicate_registry'"
        ")",
    )


# ---------------------------------------------------------------------------
# Stats tracker
# ---------------------------------------------------------------------------


@dataclass
class BackfillStats:
    """Counters for a single backfill run."""

    ci_total: int = 0

    # Outcomes per contact_info row
    asserted: int = 0  # gap rows that were written (or would-write in dry-run)
    already_present: int = 0  # rows whose triple already exists (idempotent)
    skipped_secured: int = 0  # secured=true — credential carve-out
    skipped_null_entity: int = 0  # entity_id IS NULL — orphan, needs resolver first
    skipped_unmapped_type: int = 0  # type not in predicate map
    errors: int = 0

    # per-predicate breakdown for asserted rows
    asserted_by_predicate: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # per-type breakdown for unmapped skips
    skipped_unmapped_by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))


# ---------------------------------------------------------------------------
# Gap detection query
# ---------------------------------------------------------------------------

_GAP_QUERY = """
SELECT
    ci.id           AS ci_id,
    ci.contact_id,
    ci.type,
    ci.value,
    ci.is_primary,
    ci.secured,
    ci.created_at,
    c.entity_id     AS entity_id
FROM public.contact_info ci
JOIN public.contacts c ON c.id = ci.contact_id
ORDER BY ci.created_at ASC NULLS LAST, ci.id ASC
"""


# ---------------------------------------------------------------------------
# Core backfill logic
# ---------------------------------------------------------------------------


async def _has_active_triple(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    predicate: str,
    value: str,
) -> bool:
    """Return True if an active (entity_id, predicate, value) triple already exists."""
    row = await pool.fetchrow(
        """
        SELECT 1
        FROM relationship.entity_facts
        WHERE subject   = $1
          AND predicate = $2
          AND object    = $3
          AND validity  = 'active'
        LIMIT 1
        """,
        entity_id,
        predicate,
        value,
    )
    return row is not None


async def _run_backfill_with_pool(
    pool: asyncpg.Pool,
    *,
    apply: bool,
    report_path: Path,
) -> int:
    """Execute the backfill using an already-open pool.  Does NOT close the pool."""
    # --- Preflight ---
    if not await _facts_table_exists(pool):
        logger.error(
            "Table relationship.entity_facts does not exist. "
            "Run the Alembic migration for relationship.entity_facts first."
        )
        return 1
    if not await _predicate_registry_exists(pool):
        logger.error(
            "Table relationship.entity_predicate_registry does not exist. "
            "Run the Alembic migration for the predicate registry first."
        )
        return 1

    # Lazy import of the central writer (avoids hard import at module level)
    writer_mod = _load_assert_fact()
    relationship_assert_fact = writer_mod.relationship_assert_fact
    contact_info_type_to_predicate = writer_mod.contact_info_type_to_predicate
    AssertOutcome = writer_mod.AssertOutcome

    stats = BackfillStats()
    mode_label = "APPLY" if apply else "DRY-RUN"
    logger.info("[%s] Starting contact_info → entity_facts backfill", mode_label)

    # --- Load all live contact_info rows ---
    rows = await pool.fetch(_GAP_QUERY)
    stats.ci_total = len(rows)
    logger.info("Loaded %d contact_info rows", stats.ci_total)

    for row in rows:
        ci_type: str = row["type"] or ""
        ci_value: str = row["value"] or ""
        secured: bool = bool(row["secured"])
        entity_id: uuid.UUID | None = row["entity_id"]

        # --- Skip: credential carve-out ---
        if secured:
            stats.skipped_secured += 1
            logger.debug(
                "skip secured: ci_id=%s type=%s (secured=true)",
                row["ci_id"],
                ci_type,
            )
            continue

        # --- Skip: no entity_id (orphan contact) ---
        if entity_id is None:
            stats.skipped_null_entity += 1
            logger.debug(
                "skip null_entity_id: ci_id=%s contact_id=%s type=%s",
                row["ci_id"],
                row["contact_id"],
                ci_type,
            )
            continue

        # --- Skip: type has no predicate mapping ---
        predicate = contact_info_type_to_predicate(ci_type)
        if predicate is None:
            stats.skipped_unmapped_type += 1
            stats.skipped_unmapped_by_type[ci_type] += 1
            logger.debug(
                "skip unmapped_type: ci_id=%s type=%s (no predicate mapping)",
                row["ci_id"],
                ci_type,
            )
            continue

        # --- Gap check: does this triple already exist? ---
        already_present = await _has_active_triple(pool, entity_id, predicate, ci_value)
        if already_present:
            stats.already_present += 1
            logger.debug(
                "already present: entity_id=%s predicate=%s value=%.60s",
                entity_id,
                predicate,
                ci_value,
            )
            continue

        # --- This is a gap row ---
        if not apply:
            # Dry-run: count but do not write
            stats.asserted += 1
            stats.asserted_by_predicate[predicate] += 1
            logger.debug(
                "[DRY-RUN] Would assert: entity_id=%s predicate=%s value=%.60s",
                entity_id,
                predicate,
                ci_value,
            )
            continue

        # --- Apply: assert the triple via the central writer ---
        created_at: datetime | None = row.get("created_at")
        is_primary: bool = bool(row.get("is_primary") or False)

        try:
            result = await relationship_assert_fact(
                pool,
                entity_id,
                predicate,
                ci_value,
                src="migration",
                object_kind="literal",
                conf=1.0,
                last_seen=created_at,
                verified=False,
                primary=is_primary,
                why=(
                    f"Backfill of legacy contact_info row (ci_id={row['ci_id']}) "
                    f"that predates the write-path cut-over. "
                    f"Approve to record `{predicate} = {ci_value}` on this entity."
                ),
                evidence=[
                    f"ci_id={row['ci_id']}",
                    f"contact_id={row['contact_id']}",
                    f"type={ci_type}",
                    "src=migration",
                    f"is_primary={is_primary}",
                ],
            )
            if result.outcome == AssertOutcome.pending_approval:
                # Owner entity: parked for approval — still counts as "would backfill"
                stats.asserted += 1
                stats.asserted_by_predicate[predicate] += 1
                logger.info(
                    "pending_approval (owner entity): ci_id=%s predicate=%s action_id=%s",
                    row["ci_id"],
                    predicate,
                    result.action_id,
                )
            elif result.outcome in (AssertOutcome.inserted, AssertOutcome.superseded):
                stats.asserted += 1
                stats.asserted_by_predicate[predicate] += 1
                logger.info(
                    "asserted (%s): entity_id=%s predicate=%s value=%.60s",
                    result.outcome,
                    entity_id,
                    predicate,
                    ci_value,
                )
            else:
                # unchanged — gap check said no triple but writer sees one; race?
                stats.already_present += 1
                logger.info(
                    "writer returned unchanged: entity_id=%s predicate=%s value=%.60s",
                    entity_id,
                    predicate,
                    ci_value,
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error asserting triple: ci_id=%s type=%s entity_id=%s: %s",
                row["ci_id"],
                ci_type,
                entity_id,
                exc,
            )
            stats.errors += 1

    # --- Summary log ---
    logger.info(
        "[%s] Done: ci_total=%d asserted=%d already_present=%d "
        "skipped_secured=%d skipped_null_entity=%d skipped_unmapped=%d errors=%d",
        mode_label,
        stats.ci_total,
        stats.asserted,
        stats.already_present,
        stats.skipped_secured,
        stats.skipped_null_entity,
        stats.skipped_unmapped_type,
        stats.errors,
    )

    if not apply:
        logger.info(
            "[DRY-RUN] %d gap rows would be backfilled. Re-run with --apply to write.",
            stats.asserted,
        )

    # --- Report ---
    report_text = _render_report(stats=stats, apply=apply)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("Report written to: %s", report_path)

    return 0 if stats.errors == 0 else 1


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    sep = " | ".join("---" for _ in headers)
    header_line = " | ".join(headers)
    lines = [f"| {header_line} |", f"| {sep} |"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


_REPORT_TEMPLATE = """\
# Contact-Info Legacy Backfill Report

**Generated:** {generated_at}
**Mode:** {mode}
**Script:** `src/butlers/scripts/backfill_contact_info_triples.py`
**Bead:** bu-q8rro

---

## Summary

| Outcome | Count |
|---------|-------|
| Gap rows asserted{dry_run_note} | {asserted} |
| Already present in entity_facts (idempotent) | {already_present} |
| Skipped — credential (`secured=true`) | {skipped_secured} |
| Skipped — null entity_id (orphan contact) | {skipped_null_entity} |
| Skipped — unmapped type | {skipped_unmapped_type} |
| Errors | {errors} |
| **Total contact_info rows examined** | **{ci_total}** |

---

## Asserted by predicate{dry_run_note}

{asserted_by_predicate_table}

---

## Skipped unmapped types

{skipped_unmapped_table}

---

## Notes

- **Secured rows** (`secured=true`): {skipped_secured} — not backfilled; tracked by
  bu-pl8fy / bu-fa5ex for migration into `relationship.credentials`.
- **Null entity_id rows**: {skipped_null_entity} — contact has no linked entity;
  run `contact_orphan_resolver.py` first to mint entities for these contacts, then
  re-run this script.
- **Unmapped types**: types such as `telegram_user_id`, `telegram_username`,
  `telegram_chat_id`, `google_health` have no registered has-* predicate and were
  not backfilled.
- **Idempotency**: re-running this script in --apply mode is safe; triples already
  present in `relationship.entity_facts` are counted as "already present" and
  skipped without writing.
"""


def _render_report(*, stats: BackfillStats, apply: bool) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    mode = "APPLY" if apply else "DRY-RUN"
    dry_run_note = "" if apply else " (would-assert)"

    asserted_rows = [
        [f"`{pred}`", str(n)]
        for pred, n in sorted(stats.asserted_by_predicate.items(), key=lambda x: -x[1])
    ]
    asserted_table = (
        _md_table(["Predicate", "Count"], asserted_rows)
        if asserted_rows
        else "_No gap rows found._"
    )

    unmapped_rows = [
        [f"`{t}`", str(n)]
        for t, n in sorted(stats.skipped_unmapped_by_type.items(), key=lambda x: -x[1])
    ]
    unmapped_table = (
        _md_table(["Type", "Count"], unmapped_rows)
        if unmapped_rows
        else "_No unmapped types encountered._"
    )

    return _REPORT_TEMPLATE.format(
        generated_at=now,
        mode=mode,
        dry_run_note=dry_run_note,
        asserted=stats.asserted,
        already_present=stats.already_present,
        skipped_secured=stats.skipped_secured,
        skipped_null_entity=stats.skipped_null_entity,
        skipped_unmapped_type=stats.skipped_unmapped_type,
        errors=stats.errors,
        ci_total=stats.ci_total,
        asserted_by_predicate_table=asserted_table,
        skipped_unmapped_table=unmapped_table,
    )


# ---------------------------------------------------------------------------
# Public entry point (injectable pool for tests)
# ---------------------------------------------------------------------------


async def run_backfill(
    *,
    apply: bool,
    report_path: Path,
    _pool: asyncpg.Pool | None = None,
) -> int:
    """Run the backfill.  Returns 0 on success, 1 on error.

    The ``_pool`` parameter is for testing only — callers should omit it so
    the function creates and closes its own pool.
    """
    own_pool = _pool is None
    pool = _pool if _pool is not None else await _create_pool()
    try:
        return await _run_backfill_with_pool(pool, apply=apply, report_path=report_path)
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
        / f"contact-info-legacy-backfill-{today[:4]}-{today[4:6]}-{today[6:]}.md"
    )

    parser = argparse.ArgumentParser(
        description=(
            "Backfill legacy public.contact_info rows into relationship.entity_facts has-* triples "
            "(Bead bu-q8rro)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help=(
            "Execute writes.  Without this flag the script runs in dry-run mode and "
            "reports what it would do without touching relationship.entity_facts."
        ),
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=default_report,
        metavar="PATH",
        help=f"Where to write the backfill report (default: {default_report}).",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    rc = asyncio.run(
        run_backfill(
            apply=args.apply,
            report_path=args.report_path,
        )
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
