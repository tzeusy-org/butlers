"""Daily spend-rule savings computation job (§5.4).

For each row in ``public.spend_rules``, computes the 7-day savings compared
to a workhorse-tier baseline and writes the result back to
``spend_rules.saved_7d``.

Algorithm
---------
1. Load all spend rules from ``public.spend_rules``.
2. For each rule whose ``action`` contains a ``model`` key:
   a. Discover all butler schemas that have a ``sessions`` table.
   b. Sum ``input_tokens`` and ``output_tokens`` for sessions where
      ``model = rule_model`` over the trailing 7 days, across all butler
      schemas (UNION ALL).
   c. Compute **actual cost** = token volume × pricing for ``rule_model``.
   d. Identify the **baseline model** — the highest-priority enabled model
      in the ``workhorse`` tier from ``public.model_catalog``.  Falls back
      to the cheapest model in ``pricing`` when the catalog is unavailable.
   e. Compute **baseline cost** = same token volume × pricing for the
      baseline model.
   f. ``saved_7d = baseline_cost - actual_cost``  (negative = more expensive)
3. UPDATE ``public.spend_rules SET saved_7d = $1 WHERE id = $2``.
4. Return a summary dict with counts and totals.

Idempotency: re-running for the same day produces identical results because
the 7-day window is purely time-based and the UPDATE is a SET (not an
accumulate).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.api.pricing import PricingConfig, estimate_session_cost, load_pricing

logger = logging.getLogger(__name__)

_INTERNAL_SCHEMAS = frozenset(
    {
        "connector",
        "information_schema",
        "pg_catalog",
        "public",
        "shared",
    }
)

# Workhorse is the standard default dispatch tier; used as the savings baseline.
_BASELINE_COMPLEXITY_TIER = "workhorse"


async def _discover_session_schemas(pool: asyncpg.Pool) -> tuple[str, ...]:
    """Return schemas that have a 'sessions' table (butler runtime schemas only)."""
    rows = await pool.fetch(
        """
        SELECT table_schema
        FROM information_schema.tables
        WHERE table_name = 'sessions'
          AND table_schema != ALL($1::text[])
          AND table_schema NOT LIKE 'pg_%'
        ORDER BY table_schema ASC
        """,
        list(_INTERNAL_SCHEMAS),
    )
    return tuple(row["table_schema"] for row in rows)


async def _sum_tokens_for_model(
    pool: asyncpg.Pool,
    schemas: tuple[str, ...],
    model_id: str,
    since: datetime,
) -> tuple[int, int]:
    """Return (total_input_tokens, total_output_tokens) for *model_id* since *since*.

    Queries each butler schema's ``sessions`` table and sums across all schemas.
    Returns (0, 0) when no schemas are available or no sessions match.
    """
    if not schemas:
        return 0, 0

    # Build a UNION ALL across all butler schemas
    union_parts = [
        f"""
        SELECT
            COALESCE(input_tokens, 0) AS input_tokens,
            COALESCE(output_tokens, 0) AS output_tokens
        FROM {schema}.sessions
        WHERE model = $1 AND started_at >= $2
        """
        for schema in schemas
    ]
    union_sql = " UNION ALL ".join(union_parts)
    sql = f"""
    SELECT
        COALESCE(SUM(input_tokens), 0)::bigint AS total_input,
        COALESCE(SUM(output_tokens), 0)::bigint AS total_output
    FROM ({union_sql}) AS combined
    """
    row = await pool.fetchrow(sql, model_id, since)
    if row is None:
        return 0, 0
    return int(row["total_input"]), int(row["total_output"])


async def _get_baseline_model_id(pool: asyncpg.Pool) -> str | None:
    """Return the model_id of the highest-priority enabled workhorse-tier model.

    Queries ``public.model_catalog``.  Returns ``None`` when the catalog table
    is unavailable or contains no enabled workhorse entries.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT model_id
            FROM public.model_catalog
            WHERE complexity_tier = $1
              AND enabled = true
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """,
            _BASELINE_COMPLEXITY_TIER,
        )
        if row is not None:
            return str(row["model_id"])
    except Exception as exc:
        logger.warning(
            "spend_rule_savings: could not query model_catalog for baseline model (%s: %s)",
            type(exc).__name__,
            exc,
        )
    return None


def _cheapest_workhorse_from_pricing(pricing: PricingConfig) -> str | None:
    """Return the model_id with the lowest total price (input+output) as a fallback baseline."""
    best_model: str | None = None
    best_cost: float = float("inf")
    for model_id in pricing.model_ids:
        # Use 1M tokens each as a comparison unit
        cost = pricing.estimate_cost(model_id, 1_000_000, 1_000_000)
        if cost is None:
            continue
        if cost < best_cost:
            best_cost = cost
            best_model = model_id
    return best_model


async def compute_spend_rule_savings(
    pool: asyncpg.Pool,
    pricing: PricingConfig | None = None,
) -> dict[str, Any]:
    """Compute and persist 7-day savings per spend rule.

    Parameters
    ----------
    pool:
        asyncpg pool with access to ``public.spend_rules``, butler session
        schemas, and ``public.model_catalog``.
    pricing:
        Pricing configuration used for cost estimation.  Loaded from the
        default ``pricing.toml`` when ``None``.

    Returns
    -------
    dict with keys:
        ``rules_processed`` – total rules examined,
        ``rules_updated``   – rules for which ``saved_7d`` was written,
        ``rules_skipped``   – rules with no ``action.model`` or missing pricing,
        ``baseline_model``  – model_id used as the savings baseline,
        ``errors``          – count of per-rule errors (the rule is skipped).
    """
    if pricing is None:
        try:
            pricing = load_pricing()
        except Exception as exc:
            logger.error(
                "spend_rule_savings: cannot load pricing config — aborting job (%s: %s)",
                type(exc).__name__,
                exc,
            )
            return {
                "rules_processed": 0,
                "rules_updated": 0,
                "rules_skipped": 0,
                "baseline_model": None,
                "errors": 1,
            }

    since = datetime.now(tz=UTC) - timedelta(days=7)

    # Resolve baseline model
    baseline_model_id = await _get_baseline_model_id(pool)
    if baseline_model_id is None:
        baseline_model_id = _cheapest_workhorse_from_pricing(pricing)
        if baseline_model_id is not None:
            logger.info(
                "spend_rule_savings: model_catalog unavailable; "
                "falling back to pricing-based baseline: %s",
                baseline_model_id,
            )
    if baseline_model_id is None:
        logger.warning("spend_rule_savings: no baseline model found; job will skip all rules")

    # Fetch all spend rules
    try:
        rule_rows = await pool.fetch(
            "SELECT id, action FROM public.spend_rules ORDER BY position ASC"
        )
    except Exception as exc:
        logger.error(
            "spend_rule_savings: cannot fetch spend_rules (%s: %s)",
            type(exc).__name__,
            exc,
        )
        return {
            "rules_processed": 0,
            "rules_updated": 0,
            "rules_skipped": 0,
            "baseline_model": baseline_model_id,
            "errors": 1,
        }

    if not rule_rows:
        logger.info("spend_rule_savings: no spend rules found; nothing to compute")
        return {
            "rules_processed": 0,
            "rules_updated": 0,
            "rules_skipped": 0,
            "baseline_model": baseline_model_id,
            "errors": 0,
        }

    # Discover all butler session schemas once (shared across all rules)
    schemas = await _discover_session_schemas(pool)
    if not schemas:
        logger.info(
            "spend_rule_savings: no butler session schemas found; saved_7d will be 0 for all rules"
        )

    rules_processed = 0
    rules_updated = 0
    rules_skipped = 0
    errors = 0

    for row in rule_rows:
        rules_processed += 1
        rule_id = row["id"]

        # Extract model from action (may be dict or JSON string)
        action = row["action"]
        if isinstance(action, str):
            import json

            try:
                action = json.loads(action)
            except Exception:
                action = {}
        if not isinstance(action, dict):
            action = {}

        rule_model_id = action.get("model")
        if not rule_model_id:
            logger.debug("spend_rule_savings: rule %s has no action.model — skipping", rule_id)
            rules_skipped += 1
            continue

        if baseline_model_id is None:
            rules_skipped += 1
            continue

        try:
            input_tok, output_tok = await _sum_tokens_for_model(pool, schemas, rule_model_id, since)

            actual_cost = estimate_session_cost(pricing, rule_model_id, input_tok, output_tok)
            baseline_cost = estimate_session_cost(pricing, baseline_model_id, input_tok, output_tok)
            saved_7d = baseline_cost - actual_cost

            await pool.execute(
                "UPDATE public.spend_rules SET saved_7d = $1, updated_at = now() WHERE id = $2",
                saved_7d,
                rule_id,
            )
            rules_updated += 1
            logger.debug(
                "spend_rule_savings: rule %s → actual=%.6f baseline=%.6f saved=%.6f (in=%d out=%d)",
                rule_id,
                actual_cost,
                baseline_cost,
                saved_7d,
                input_tok,
                output_tok,
            )
        except Exception as exc:
            errors += 1
            logger.warning(
                "spend_rule_savings: error processing rule %s (%s: %s)",
                rule_id,
                type(exc).__name__,
                exc,
            )

    logger.info(
        "spend_rule_savings: processed=%d updated=%d skipped=%d errors=%d baseline=%s",
        rules_processed,
        rules_updated,
        rules_skipped,
        errors,
        baseline_model_id,
    )
    return {
        "rules_processed": rules_processed,
        "rules_updated": rules_updated,
        "rules_skipped": rules_skipped,
        "baseline_model": baseline_model_id,
        "errors": errors,
    }
