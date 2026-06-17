"""Backfill: re-home memory edge-facts into relationship.entity_facts.

One-time migration that reads ``relationship.facts`` edge-facts (rows with
``object_entity_id`` set), classifies each predicate via the alias map, and for
mappable (registry-relational) predicates:

1. Re-asserts the edge via ``relationship_assert_fact`` (the central writer).
2. Retracts the migrated memory copy (``validity='retracted'``).

Narrative predicates (not resolvable to a registry predicate) are left
untouched in ``relationship.facts``.

Usage
-----
    python -m butlers.scripts.backfill_edge_facts_to_entity_facts          # dry-run (default)
    python -m butlers.scripts.backfill_edge_facts_to_entity_facts --apply  # write

Environment
-----------
DATABASE_URL, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD
control the DB connection (same env vars as the butler daemon).

Idempotency
-----------
Only ``validity='active'`` rows are processed; successfully migrated rows are
immediately retracted, so a second run finds nothing to process.  The central
writer's ``(subject, predicate, object)`` unique-active contract ensures
duplicate assertions are no-ops.

Issue: bu-1fu8c
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import os
import sys
import uuid
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
logger = logging.getLogger("backfill_edge_facts")

# ---------------------------------------------------------------------------
# Predicate alias map
# Must stay in sync with:
#   roster/relationship/tools/relationship_assert_fact.py::_PREDICATE_ALIAS_MAP
# ---------------------------------------------------------------------------
_PREDICATE_ALIAS_MAP: dict[str, str] = {
    "works_at": "works-at",
    "friend_of": "friend-of",
    "child_of": "child-of",
    "parent_of": "parent-of",
    "colleague_of": "colleague-of",
    "family_of": "family-of",
    "partner_of": "partner-of",
    "member_of": "member-of",
    "sibling_of": "family-of",
    "married_to": "partner-of",
}


def classify_predicate(predicate: str, registered: set[str]) -> tuple[bool, str]:
    """Return ``(mappable, canonical_predicate)``.

    A predicate is mappable when its alias-normalised form exists in the
    entity_predicate_registry.  Unmapped predicates are left as narrative.

    Args:
        predicate: Raw predicate string from ``relationship.facts``.
        registered: Set of canonical predicate names from the registry
            (``object_kind='entity'``).

    Returns:
        ``(True, canonical)`` when the edge should move to ``entity_facts``.
        ``(False, predicate)`` when it is narrative and should stay.
    """
    canonical = _PREDICATE_ALIAS_MAP.get(predicate, predicate)
    return canonical in registered, canonical


# ---------------------------------------------------------------------------
# Lazy loader for the central writer
# ---------------------------------------------------------------------------


def _load_assert_fact_fn() -> Any:
    """Load ``relationship_assert_fact`` from the roster via importlib."""
    this_dir = Path(__file__).resolve().parent
    # src/butlers/scripts → src/butlers → src → repo root
    repo_root = this_dir.parent.parent.parent
    path = repo_root / "roster" / "relationship" / "tools" / "relationship_assert_fact.py"
    if not path.exists():
        raise FileNotFoundError(f"relationship_assert_fact not found at {path}")
    spec = importlib.util.spec_from_file_location("_roster_assert_fact", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec_module so that @dataclass on
    # AssertResult can resolve KW_ONLY via sys.modules.get(cls.__module__).
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return mod.relationship_assert_fact


_cached_assert_fact: Any = None


def _get_assert_fact() -> Any:
    global _cached_assert_fact
    if _cached_assert_fact is None:
        _cached_assert_fact = _load_assert_fact_fn()
    return _cached_assert_fact


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _create_pool(schema: str = "relationship") -> asyncpg.Pool:
    """Create an asyncpg pool with ``relationship,public`` search_path."""
    from butlers.db import register_jsonb_codec

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        pool = await asyncpg.create_pool(
            dsn=database_url,
            server_settings={"search_path": f"{schema},public"},
            init=register_jsonb_codec,
        )
    else:
        pool = await asyncpg.create_pool(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "butlers"),
            password=os.environ.get("POSTGRES_PASSWORD", "butlers"),
            database=os.environ.get("POSTGRES_DB", "butlers"),
            server_settings={"search_path": f"{schema},public"},
            init=register_jsonb_codec,
        )
    assert pool is not None
    return pool


async def _fetch_registered_predicates(pool: asyncpg.Pool) -> set[str]:
    """Return canonical predicate names for ``object_kind='entity'`` from the registry."""
    rows = await pool.fetch(
        """
        SELECT predicate
          FROM relationship.entity_predicate_registry
         WHERE object_kind = 'entity'
        """
    )
    return {row["predicate"] for row in rows}


async def _fetch_edge_facts(pool: asyncpg.Pool) -> list[Any]:
    """Return active edge-facts from ``relationship.facts``."""
    return await pool.fetch(
        """
        SELECT id, entity_id, object_entity_id, predicate,
               confidence, last_confirmed_at, source_butler
          FROM relationship.facts
         WHERE object_entity_id IS NOT NULL
           AND validity = 'active'
         ORDER BY entity_id, predicate
        """
    )


# ---------------------------------------------------------------------------
# Per-predicate stats
# ---------------------------------------------------------------------------


class _PredicateStats:
    def __init__(self, canonical: str, mappable: bool) -> None:
        self.canonical = canonical
        self.mappable = mappable
        self.migrated = 0
        self.retracted = 0
        self.left_narrative = 0
        self.errors = 0


# ---------------------------------------------------------------------------
# Core backfill logic
# ---------------------------------------------------------------------------


async def run(
    pool: asyncpg.Pool,
    *,
    dry_run: bool = True,
    _assert_fact: Any = None,
) -> dict[str, Any]:
    """Execute the backfill.

    Args:
        pool: asyncpg connection pool with ``relationship,public`` search_path.
        dry_run: When ``True`` (default), print the mapping plan without writing.
        _assert_fact: Injectable override for the central writer — used in tests.

    Returns:
        Dict with ``per_predicate`` map and aggregate totals.
    """
    assert_fact = _assert_fact if _assert_fact is not None else _get_assert_fact()

    registered = await _fetch_registered_predicates(pool)
    rows = await _fetch_edge_facts(pool)

    logger.info("Found %d active edge-facts in relationship.facts", len(rows))

    per_pred: dict[str, _PredicateStats] = {}

    for row in rows:
        raw_pred = row["predicate"]
        mappable, canonical = classify_predicate(raw_pred, registered)

        if raw_pred not in per_pred:
            per_pred[raw_pred] = _PredicateStats(canonical, mappable)

        stats = per_pred[raw_pred]

        if not mappable:
            stats.left_narrative += 1
            logger.debug(
                "[NARRATIVE] fact %s predicate=%r → not in registry, leaving in memory",
                row["id"],
                raw_pred,
            )
            continue

        # Mappable edge — re-assert via central writer, then retract source.
        subject: uuid.UUID = row["entity_id"]
        obj: str = str(row["object_entity_id"])
        conf: float = row["confidence"] if row["confidence"] is not None else 1.0
        last_seen = row["last_confirmed_at"]

        logger.debug(
            "[%s] fact %s predicate=%r → %r  subject=%s object=%s",
            "DRY-RUN" if dry_run else "MIGRATE",
            row["id"],
            raw_pred,
            canonical,
            subject,
            obj,
        )

        if dry_run:
            stats.migrated += 1
            stats.retracted += 1
            continue

        try:
            await assert_fact(
                pool,
                subject,
                canonical,
                obj,
                src="backfill",
                object_kind="entity",
                conf=conf,
                last_seen=last_seen,
            )
            # Retract the source only after a successful assertion.
            await pool.execute(
                "UPDATE relationship.facts"
                " SET validity = 'retracted' WHERE id = $1 AND validity = 'active'",
                row["id"],
            )
            stats.migrated += 1
            stats.retracted += 1
        except Exception:
            logger.exception("Failed to migrate fact %s (predicate=%r)", row["id"], raw_pred)
            stats.errors += 1

    # Aggregate totals.
    total_migrated = sum(s.migrated for s in per_pred.values())
    total_retracted = sum(s.retracted for s in per_pred.values())
    total_left_narrative = sum(s.left_narrative for s in per_pred.values())
    total_errors = sum(s.errors for s in per_pred.values())

    _print_summary(per_pred, dry_run)

    return {
        "per_predicate": {
            pred: {
                "canonical": s.canonical,
                "mappable": s.mappable,
                "migrated": s.migrated,
                "retracted": s.retracted,
                "left_narrative": s.left_narrative,
                "errors": s.errors,
            }
            for pred, s in sorted(per_pred.items())
        },
        "total_processed": len(rows),
        "total_migrated": total_migrated,
        "total_retracted": total_retracted,
        "total_left_narrative": total_left_narrative,
        "total_errors": total_errors,
    }


def _print_summary(per_pred: dict[str, _PredicateStats], dry_run: bool) -> None:
    """Print per-predicate counts and aggregate totals."""
    mode = "DRY-RUN" if dry_run else "APPLIED"
    logger.info("=== Backfill summary [%s] ===", mode)

    # Mappable predicates first, then narrative.
    mappable = [(p, s) for p, s in sorted(per_pred.items()) if s.mappable]
    narrative = [(p, s) for p, s in sorted(per_pred.items()) if not s.mappable]

    if mappable:
        logger.info("Mappable predicates (migrated to entity_facts):")
        for pred, s in mappable:
            logger.info(
                "  %-30s → %-30s  migrated=%d  retracted=%d  errors=%d",
                pred,
                s.canonical,
                s.migrated,
                s.retracted,
                s.errors,
            )
    else:
        logger.info("  (no mappable predicates found)")

    if narrative:
        logger.info("Narrative predicates (left in memory):")
        for pred, s in narrative:
            logger.info("  %-30s  left_narrative=%d", pred, s.left_narrative)
    else:
        logger.info("  (no narrative predicates found)")

    total_migrated = sum(s.migrated for s in per_pred.values())
    total_left_narrative = sum(s.left_narrative for s in per_pred.values())
    total_errors = sum(s.errors for s in per_pred.values())
    logger.info(
        "Totals: migrated=%d  left_narrative=%d  errors=%d  [%s]",
        total_migrated,
        total_left_narrative,
        total_errors,
        mode,
    )

    if total_errors:
        logger.warning("%d edge(s) failed to migrate — check logs above.", total_errors)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def _main_async(dry_run: bool) -> int:
    pool = await _create_pool()
    try:
        result = await run(pool, dry_run=dry_run)
    finally:
        await pool.close()

    if result["total_errors"]:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-home memory edge-facts into relationship.entity_facts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Run without --apply first to preview the mapping plan (dry-run mode).\n"
            "Inspect the per-predicate summary, then re-run with --apply to commit.\n"
            "Safe to re-run: already-migrated edges are skipped (idempotent)."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write changes. Omit for a dry-run preview (default).",
    )
    args = parser.parse_args(argv)

    dry_run = not args.apply
    if dry_run:
        logger.info("DRY-RUN mode — no writes will be made. Pass --apply to commit.")
    else:
        logger.info("APPLY mode — edges will be migrated and source facts retracted.")

    return asyncio.run(_main_async(dry_run=dry_run))


if __name__ == "__main__":
    sys.exit(main())
