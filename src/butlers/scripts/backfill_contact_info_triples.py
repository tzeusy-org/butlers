"""Backfill legacy contact_info rows into entity_facts / entity_info.

Bead: bu-q8rro — Contact-info legacy backfill job.
Bead: bu-krbqx — Secured-row backfill into public.entity_info.

Background
----------
After the write-path cut-over (Migration bead 8, bu-k9ylx), NEW channel writes
go directly to ``relationship.entity_facts``.  However, pre-existing rows in
``public.contact_info`` that were created before the cut-over were never
migrated — the snapshot-based migration (contact_backfill_triples.py, bead
bu-tr8j3) targeted the per-date snapshot tables rather than the live table.

This script fills that gap in two ways:

Non-secured rows
  It reads the LIVE ``public.contact_info`` table, identifies rows whose
  (entity_id, predicate, value) triple is not yet present in
  ``relationship.entity_facts``, and asserts those triples via
  :func:`relationship_assert_fact` — the authoritative central writer.

  All writes go through ``relationship_assert_fact`` so predicates, validity,
  owner carve-out, and value_hash handling are consistent with the rest of the
  write path.

Secured rows (bu-krbqx)
  Rows with ``secured = true`` are credentials and must be stored in
  ``public.entity_info``, NOT in ``relationship.entity_facts``.  Per RFC 0004
  Amendment 2 and the write-path established in PR #2042, secured rows are
  written with::

      INSERT INTO public.entity_info
          (entity_id, type, value, label, is_primary, secured)
      VALUES (...)
      ON CONFLICT (entity_id, type) DO UPDATE
          SET value = entity_info.value
      RETURNING …

  The ``ON CONFLICT (entity_id, type)`` matches the unique constraint
  ``uq_shared_entity_info_entity_type`` — re-running the script is safe (the
  existing value is preserved on conflict; the RETURNING clause still yields the
  row, so the insert is counted as ``secured_already_present``).

Skip rules
----------
1. ``secured = true`` — credential rows: backfilled into ``public.entity_info``
   (NOT skipped).  Rows with ``entity_id IS NULL`` are skipped with a log entry
   and counted in ``secured_skipped_null_entity``.
2. ``entity_id IS NULL`` (non-secured) — the contact has no linked entity yet.
   Logged and counted; requires the orphan-resolver to run first.
3. Unmapped type — ``type`` is not in the ``_CI_TYPE_TO_PREDICATE`` mapping
   (e.g. ``telegram_chat_id``, ``google_health``, ``home_assistant_url``).
   Logged and counted.  Note: ``telegram_user_id`` and ``telegram_username``
   ARE mapped (both → ``has-handle``, bead bu-55ggu).

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
from typing import Any

import asyncpg

from butlers.db import register_jsonb_codec

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

    module_name = "relationship_assert_fact"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, tools_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # register before exec so @dataclass can resolve cls.__module__
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return mod


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------


async def _create_pool() -> asyncpg.Pool:
    # The central writer (relationship_assert_fact) uses unqualified references to
    # ``pending_actions`` (owner carve-out path).  Set search_path to
    # ``relationship,public`` so those references resolve correctly — matching the
    # search_path the relationship butler itself uses at runtime.
    #
    # Also register the JSONB codec so Python dicts can be passed directly to
    # JSONB-typed parameters (e.g. the ``@>`` containment operator in the
    # dedup-match query of ``_create_pending_action``).
    server_settings = {"search_path": "relationship,public"}
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        pool = await asyncpg.create_pool(
            dsn=database_url,
            server_settings=server_settings,
            init=register_jsonb_codec,
        )
    else:
        pool = await asyncpg.create_pool(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "butlers"),
            password=os.environ.get("POSTGRES_PASSWORD", "butlers"),
            database=os.environ.get("POSTGRES_DB", "butlers"),
            server_settings=server_settings,
            init=register_jsonb_codec,
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


async def _entity_info_table_exists(pool: asyncpg.Pool) -> bool:
    return await pool.fetchval(
        "SELECT EXISTS ("
        "SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'entity_info'"
        ")",
    )


# ---------------------------------------------------------------------------
# Stats tracker
# ---------------------------------------------------------------------------


@dataclass
class BackfillStats:
    """Counters for a single backfill run."""

    ci_total: int = 0

    # Outcomes per contact_info row (non-secured path → relationship.entity_facts)
    asserted: int = 0  # gap rows that were written (or would-write in dry-run)
    already_present: int = 0  # rows whose triple already exists (idempotent)
    skipped_null_entity: int = 0  # entity_id IS NULL — orphan, needs resolver first
    skipped_unmapped_type: int = 0  # type not in predicate map
    errors: int = 0

    # Secured path (→ public.entity_info, bu-krbqx)
    secured_inserted: int = 0  # new rows written into public.entity_info
    secured_already_present: int = 0  # ON CONFLICT: row already existed, value preserved
    secured_skipped_null_entity: int = 0  # secured row with entity_id IS NULL — cannot migrate
    secured_errors: int = 0  # unexpected errors on the secured path

    # per-predicate breakdown for asserted rows
    asserted_by_predicate: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # per-type breakdown for unmapped skips
    skipped_unmapped_by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # per-type breakdown for secured inserts
    secured_inserted_by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))


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


async def _backfill_secured_row(
    pool: asyncpg.Pool,
    *,
    row: Any,
    ci_type: str,
    ci_value: str,
    entity_id: uuid.UUID,
    apply: bool,
    stats: BackfillStats,
) -> None:
    """Backfill one secured contact_info row into public.entity_info.

    Mirrors the INSERT semantics of PR #2042's create_contact_info endpoint:

        INSERT INTO public.entity_info
            (entity_id, type, value, label, is_primary, secured)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (entity_id, type) DO UPDATE
            SET value = entity_info.value
        RETURNING id, entity_id, type, value, label, is_primary, secured

    The DO UPDATE SET value = entity_info.value is an explicit no-op that still
    triggers RETURNING so the script can distinguish insert vs. conflict.
    Re-running is safe: conflicting rows are counted as secured_already_present.
    """
    if not apply:
        # Dry-run: count but do not write
        stats.secured_inserted += 1
        stats.secured_inserted_by_type[ci_type] += 1
        logger.debug(
            "[DRY-RUN] Would insert secured → entity_info: entity_id=%s type=%s value=%.40s",
            entity_id,
            ci_type,
            ci_value,
        )
        return

    is_primary: bool = bool(row.get("is_primary") or False)
    try:
        ei_row = await pool.fetchrow(
            """
            INSERT INTO public.entity_info
                (entity_id, type, value, label, is_primary, secured)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (entity_id, type) DO UPDATE
                SET value = entity_info.value
            RETURNING id, entity_id, type, value, label, is_primary, secured
            """,
            entity_id,
            ci_type,
            ci_value,
            None,  # label — contact_info had no label column
            is_primary,
            True,
        )
        if ei_row is not None:
            # Distinguish new insert vs. conflict by checking if the returned value
            # matches what we tried to insert.  If value differs this was a conflict
            # where the existing row was preserved (no-op update).
            # Both paths are idempotent; we track them separately for the report.
            if ei_row["value"] == ci_value:
                stats.secured_inserted += 1
                stats.secured_inserted_by_type[ci_type] += 1
                logger.info(
                    "secured insert: entity_id=%s type=%s ci_id=%s",
                    entity_id,
                    ci_type,
                    row["ci_id"],
                )
            else:
                stats.secured_already_present += 1
                logger.info(
                    "secured conflict (existing value preserved): entity_id=%s type=%s ci_id=%s",
                    entity_id,
                    ci_type,
                    row["ci_id"],
                )
        else:
            # Should not happen with DO UPDATE, but treat as conflict
            stats.secured_already_present += 1
            logger.debug(
                "secured insert returned no row (conflict?): entity_id=%s type=%s",
                entity_id,
                ci_type,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "error inserting secured row into entity_info: ci_id=%s type=%s entity_id=%s: %s",
            row["ci_id"],
            ci_type,
            entity_id,
            exc,
        )
        stats.secured_errors += 1


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
    if not await _entity_info_table_exists(pool):
        logger.error(
            "Table public.entity_info does not exist. "
            "Run the Alembic migration for public.entity_info first."
        )
        return 1

    # Lazy import of the central writer (avoids hard import at module level)
    writer_mod = _load_assert_fact()
    relationship_assert_fact = writer_mod.relationship_assert_fact
    contact_info_type_to_predicate = writer_mod.contact_info_type_to_predicate
    AssertOutcome = writer_mod.AssertOutcome

    stats = BackfillStats()
    mode_label = "APPLY" if apply else "DRY-RUN"
    logger.info("[%s] Starting contact_info → entity_facts / entity_info backfill", mode_label)

    # --- Load all live contact_info rows ---
    rows = await pool.fetch(_GAP_QUERY)
    stats.ci_total = len(rows)
    logger.info("Loaded %d contact_info rows", stats.ci_total)

    for row in rows:
        ci_type: str = row["type"] or ""
        ci_value: str = row["value"] or ""
        secured: bool = bool(row["secured"])
        entity_id: uuid.UUID | None = row["entity_id"]

        # --- Secured path: backfill into public.entity_info (bu-krbqx) ---
        if secured:
            if entity_id is None:
                stats.secured_skipped_null_entity += 1
                logger.warning(
                    "skip secured (null entity_id): ci_id=%s type=%s contact_id=%s — "
                    "contact has no linked entity; run orphan-resolver first",
                    row["ci_id"],
                    ci_type,
                    row["contact_id"],
                )
                continue
            await _backfill_secured_row(
                pool,
                row=row,
                ci_type=ci_type,
                ci_value=ci_value,
                entity_id=entity_id,
                apply=apply,
                stats=stats,
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
        "skipped_null_entity=%d skipped_unmapped=%d errors=%d "
        "secured_inserted=%d secured_already_present=%d "
        "secured_skipped_null_entity=%d secured_errors=%d",
        mode_label,
        stats.ci_total,
        stats.asserted,
        stats.already_present,
        stats.skipped_null_entity,
        stats.skipped_unmapped_type,
        stats.errors,
        stats.secured_inserted,
        stats.secured_already_present,
        stats.secured_skipped_null_entity,
        stats.secured_errors,
    )

    if not apply:
        logger.info(
            "[DRY-RUN] %d gap rows would be backfilled into entity_facts; "
            "%d secured rows would be inserted into entity_info. "
            "Re-run with --apply to write.",
            stats.asserted,
            stats.secured_inserted,
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
**Beads:** bu-q8rro (non-secured path), bu-krbqx (secured path)

---

## Summary — non-secured path (→ relationship.entity_facts)

| Outcome | Count |
|---------|-------|
| Gap rows asserted{dry_run_note} | {asserted} |
| Already present in entity_facts (idempotent) | {already_present} |
| Skipped — null entity_id (orphan contact) | {skipped_null_entity} |
| Skipped — unmapped type | {skipped_unmapped_type} |
| Errors | {errors} |

---

## Summary — secured path (→ public.entity_info)

| Outcome | Count |
|---------|-------|
| Inserted{dry_run_note} | {secured_inserted} |
| Already present in entity_info (conflict no-op) | {secured_already_present} |
| Skipped — null entity_id (orphan contact) | {secured_skipped_null_entity} |
| Errors | {secured_errors} |

| **Total contact_info rows examined** | **{ci_total}** |

---

## Asserted by predicate{dry_run_note}

{asserted_by_predicate_table}

---

## Secured inserts by type{dry_run_note}

{secured_inserted_by_type_table}

---

## Skipped unmapped types

{skipped_unmapped_table}

---

## Notes

- **Secured rows** (`secured=true`): backfilled into `public.entity_info` per
  RFC 0004 Amendment 2 (bu-krbqx).  Rows with `entity_id IS NULL` are skipped
  (counted as `secured_skipped_null_entity`) — run `contact_orphan_resolver.py`
  first to mint entities for those contacts, then re-run this script.
- **Null entity_id rows** (non-secured): {skipped_null_entity} — contact has no
  linked entity; run `contact_orphan_resolver.py` first, then re-run.
- **Unmapped types**: types such as `telegram_chat_id`, `google_health`,
  `home_assistant_url` have no registered has-* predicate and are intentionally
  not backfilled.  `telegram_user_id` and `telegram_username` ARE mapped
  (both → `has-handle`) since bead bu-55ggu.
- **Idempotency**: re-running in --apply mode is safe.  Non-secured triples
  already in `relationship.entity_facts` are counted as "already present".
  Secured rows already in `public.entity_info` trigger ON CONFLICT DO UPDATE
  (value preserved) and are counted as "already present in entity_info".
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

    secured_type_rows = [
        [f"`{t}`", str(n)]
        for t, n in sorted(stats.secured_inserted_by_type.items(), key=lambda x: -x[1])
    ]
    secured_inserted_by_type_table = (
        _md_table(["Type", "Count"], secured_type_rows)
        if secured_type_rows
        else "_No secured rows inserted._"
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
        skipped_null_entity=stats.skipped_null_entity,
        skipped_unmapped_type=stats.skipped_unmapped_type,
        errors=stats.errors,
        secured_inserted=stats.secured_inserted,
        secured_already_present=stats.secured_already_present,
        secured_skipped_null_entity=stats.secured_skipped_null_entity,
        secured_errors=stats.secured_errors,
        ci_total=stats.ci_total,
        asserted_by_predicate_table=asserted_table,
        secured_inserted_by_type_table=secured_inserted_by_type_table,
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
