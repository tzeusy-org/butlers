"""Re-embedding migration tool for MemoryModuleConfig.embedding_model changes.

When the configured embedding_model changes, existing stored embeddings are
incomparable to embeddings produced by the new model (different vector space).
This module provides:

  count_pending(pool, current_model, tier=None) -> dict[str, int]
    Count rows in each tier whose embedding_model_version != current_model
    AND whose embedding IS NOT NULL.

  run(pool, engine, dry_run, tiers, batch_size) -> ReembedResult
    Re-embed stale rows tier-by-tier in batches and record the producing model.

Both helpers operate at the asyncpg-pool level so they can be called from the
MCP tool wrapper or a background task without pulling in the MCP layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier registry — table name, id column, text-content column(s).
# ---------------------------------------------------------------------------

# Each entry: (table_name, content_column)
# content_column is the column whose text is passed to embed().
_TIERS: dict[str, tuple[str, str]] = {
    "episodes": ("episodes", "content"),
    "facts": ("facts", "content"),
    "rules": ("rules", "content"),
}

ALL_TIERS: tuple[str, ...] = tuple(_TIERS.keys())


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ReembedResult:
    """Summary of a re-embedding run."""

    dry_run: bool
    current_model: str
    tiers_processed: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    """Rows re-embedded (or would-be re-embedded in dry_run) per tier."""
    errors: list[str] = field(default_factory=list)
    """Non-fatal per-row errors encountered during the run."""

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "current_model": self.current_model,
            "tiers_processed": self.tiers_processed,
            "counts": self.counts,
            "total": self.total,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# count_pending
# ---------------------------------------------------------------------------


async def count_pending(
    pool: Pool,
    current_model: str,
    tier: str | None = None,
) -> dict[str, int]:
    """Count rows per tier whose embedding was produced by a different model.

    Only rows where ``embedding IS NOT NULL`` are counted — rows with no
    embedding at all are not stale (they were never embedded).

    Args:
        pool: asyncpg connection pool.
        current_model: The model name currently configured.
        tier: If given, restrict to this tier only (one of ``ALL_TIERS``).
              If None, all tiers are checked.

    Returns:
        Dict mapping tier name → stale row count.

    Raises:
        ValueError: If ``tier`` is not a recognised tier name.
    """
    tiers = _resolve_tiers(tier)
    result: dict[str, int] = {}
    async with pool.acquire() as conn:
        for tier_name in tiers:
            table, _ = _TIERS[tier_name]
            row = await conn.fetchrow(
                f"""
                SELECT COUNT(*) AS cnt
                FROM {table}
                WHERE embedding IS NOT NULL
                  AND (
                      embedding_model_version IS NULL
                      OR embedding_model_version != $1
                  )
                """,
                current_model,
            )
            result[tier_name] = row["cnt"] if row else 0
    return result


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


async def run(
    pool: Pool,
    engine: Any,
    *,
    dry_run: bool = True,
    tiers: list[str] | None = None,
    batch_size: int = 50,
) -> ReembedResult:
    """Re-embed all stale rows and update embedding_model_version.

    Processes each tier in batches of ``batch_size``.  In dry-run mode, rows
    are counted and logged but no DB writes are performed.

    Args:
        pool: asyncpg connection pool.
        engine: EmbeddingEngine instance (provides embed_batch + model_name).
        dry_run: If True, count and log only — make no DB changes.
        tiers: Optional list of tier names to process.  Defaults to all tiers.
        batch_size: Number of rows to fetch and embed per DB round-trip.

    Returns:
        ReembedResult summarising the run.
    """
    current_model = engine.model_name
    if tiers is None:
        tier_names = _resolve_tiers(None)
    else:
        tier_names = _resolve_tiers_list(tiers)

    result = ReembedResult(
        dry_run=dry_run,
        current_model=current_model,
        tiers_processed=list(tier_names),
    )

    for tier_name in tier_names:
        table, content_col = _TIERS[tier_name]
        processed = await _process_tier(
            pool=pool,
            engine=engine,
            table=table,
            content_col=content_col,
            current_model=current_model,
            dry_run=dry_run,
            batch_size=batch_size,
            errors=result.errors,
        )
        result.counts[tier_name] = processed
        logger.info(
            "reembedding tier=%s dry_run=%s processed=%d model=%s",
            tier_name,
            dry_run,
            processed,
            current_model,
        )

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_tiers(tier: str | None) -> list[str]:
    """Resolve a single optional tier name to a list of tier names.

    Args:
        tier: A single tier name or None (all tiers).

    Returns:
        List of tier names to process.

    Raises:
        ValueError: If ``tier`` is not recognised.
    """
    if tier is None:
        return list(ALL_TIERS)
    if tier not in _TIERS:
        raise ValueError(f"Unknown tier {tier!r}. Must be one of: {sorted(_TIERS)}")
    return [tier]


def _resolve_tiers_list(tiers: list[str]) -> list[str]:
    """Validate and return a list of tier names.

    Args:
        tiers: A list of tier names.

    Returns:
        Validated list (same order).

    Raises:
        ValueError: If any tier name is not recognised.
    """
    unknown = [t for t in tiers if t not in _TIERS]
    if unknown:
        raise ValueError(f"Unknown tiers: {unknown}. Must be from: {sorted(_TIERS)}")
    return list(tiers)


async def _process_tier(
    *,
    pool: Pool,
    engine: Any,
    table: str,
    content_col: str,
    current_model: str,
    dry_run: bool,
    batch_size: int,
    errors: list[str],
) -> int:
    """Process one tier, returning total rows re-embedded (or counted in dry_run)."""
    total = 0

    while True:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, {content_col}
                FROM {table}
                WHERE embedding IS NOT NULL
                  AND (
                      embedding_model_version IS NULL
                      OR embedding_model_version != $1
                  )
                ORDER BY id
                LIMIT $2
                """,
                current_model,
                batch_size,
            )

        if not rows:
            break

        batch_size_actual = len(rows)

        if dry_run:
            total += batch_size_actual
            # In dry_run we don't write — continue to next batch.
            # We can't paginate by id without tracking the last seen id in
            # dry_run (since we don't update anything).  So in dry_run we
            # just count the first batch and stop — the exact count for the
            # full tier is provided by count_pending().
            break

        # Embed all texts in one batch call for throughput.
        texts = [row[content_col] for row in rows]
        row_ids = [row["id"] for row in rows]

        try:
            vectors = engine.embed_batch(texts)
        except Exception as exc:  # noqa: BLE001
            msg = f"embed_batch failed for {table}: {exc}"
            logger.error(msg)
            errors.append(msg)
            break

        # Write back embeddings and model version in a single transaction.
        async with pool.acquire() as conn:
            async with conn.transaction():
                for row_id, vector in zip(row_ids, vectors, strict=True):
                    try:
                        await conn.execute(
                            f"""
                            UPDATE {table}
                            SET embedding = $1::vector,
                                embedding_model_version = $2
                            WHERE id = $3
                            """,
                            str(vector),
                            current_model,
                            row_id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        msg = f"update failed for {table} id={row_id}: {exc}"
                        logger.error(msg)
                        errors.append(msg)

        total += batch_size_actual

        if batch_size_actual < batch_size:
            # Last batch — no more rows.
            break

    return total
