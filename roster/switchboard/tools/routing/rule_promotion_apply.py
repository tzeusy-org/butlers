"""Apply rule-promotion suggestions: mint the ingestion_rules row.

bu-o62bc (rule-promotion bead 4 of 7). The promotion trigger (bead 3) only ever
writes ``pending_review`` suggestions; this module mints the actual
``switchboard.ingestion_rules`` row when a suggestion is *applied*, in two ways:

- **auto-apply** for the clearly-automated ``skip``/``metadata_only`` tier. The
  owner gate (bu-4pq0s) decided auto-apply for that tier: a
  ``is_clearly_automated`` sender whose repeated LLM verdict is ``skip`` or
  ``metadata_only`` is low blast-radius (it only ever suppresses/downgrades an
  already-automated sender, never routes owner-facing traffic), so it mints
  without a confirm click and is surfaced as an *informational, reversible*
  auto-applied rule (a disable/demote affordance, not a pre-apply gate).
- **owner-confirm** for everything else — every ``route_to:<butler>`` suggestion
  (higher blast radius: a wrong route sends real traffic to the wrong butler)
  and any non-automated ``skip``/``metadata_only`` — via individual approval
  cards that call :func:`apply_suggestion` on confirm.

Both paths funnel through :func:`mint_rule_from_suggestion` so provenance
(``created_by='promotion'``, ``promoted_from_suggestion_id``) and the
suggestion-lifecycle transition (``status='confirmed'`` + ``created_rule_id`` +
``decided_at``/``decided_by``) are written identically and atomically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.tools.switchboard.routing.rule_promotion import parse_proposed_action

logger = logging.getLogger(__name__)

# skip/metadata_only are the only actions eligible for unattended auto-apply
# (owner gate bu-4pq0s). route_to always requires an explicit owner confirm.
AUTO_APPLY_ACTIONS: frozenset[str] = frozenset({"skip", "metadata_only"})

# decided_by marker for the unattended auto-apply path — distinguishes an
# owner-clicked confirm from the automated tier in the suggestion's audit trail.
AUTO_APPLY_ACTOR = "auto:promotion"

# Priority for a promoted rule. Promoted rules match one exact sender address
# (proposed_condition = {"address": <full address>}), so they are specific and
# should win over broad catch-all rules; this sits just below the priority-5
# seed automated-sender rules (migration 003) — high enough precedence to take
# effect, low enough not to jump ahead of the curated seeds. First-match-wins is
# priority ASC, so a lower number is higher precedence.
PROMOTED_RULE_PRIORITY = 10


class SuggestionNotApplicable(Exception):
    """A suggestion cannot be applied (already decided, or a bad route_to target).

    ``status_code`` maps the failure to an HTTP status for the REST layer:
    409 for a non-``pending_review`` suggestion (already confirmed/dismissed/
    superseded), 404 when the suggestion id does not exist, 422 for a
    ``route_to`` target that is not a registered butler.
    """

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class AutoApplyResult:
    """Summary counters for one auto-apply pass."""

    candidates: int = 0
    applied: int = 0
    skipped_conflict: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "auto_apply_candidates": self.candidates,
            "auto_apply_applied": self.applied,
            "auto_apply_skipped_conflict": self.skipped_conflict,
            "auto_apply_errors": self.errors,
        }


async def _assert_route_to_registered(conn: Any, action: str) -> None:
    """Raise :class:`SuggestionNotApplicable` (422) if a route_to target is not a
    registered butler. No-op for non-route_to actions."""
    verdict_action, verdict_target = parse_proposed_action(action)
    if verdict_action != "route_to":
        return
    row = await conn.fetchrow(
        "SELECT name FROM switchboard.butler_registry WHERE name = $1", verdict_target
    )
    if row is None:
        raise SuggestionNotApplicable(
            f"route_to target '{verdict_target}' is not a registered butler",
            status_code=422,
        )


async def mint_rule_from_suggestion(
    pool: asyncpg.Pool,
    suggestion_id: Any,
    *,
    decided_by: str,
) -> asyncpg.Record:
    """Atomically mint an ingestion_rules row from a pending suggestion.

    Locks the suggestion row (``FOR UPDATE``), verifies it is still
    ``pending_review`` (raising :class:`SuggestionNotApplicable` 404/409
    otherwise so a double-click / concurrent auto-apply cannot double-mint),
    validates a ``route_to`` target, inserts the rule with provenance, and
    transitions the suggestion to ``confirmed`` — all in one transaction.

    Returns the created ``ingestion_rules`` row (for the API response / audit).
    """
    async with pool.acquire() as conn, conn.transaction():
        suggestion = await conn.fetchrow(
            """
            SELECT id, suggestion_kind, proposed_rule_type, proposed_condition,
                   proposed_action, status
            FROM rule_promotion_suggestions
            WHERE id = $1
            FOR UPDATE
            """,
            suggestion_id,
        )
        if suggestion is None:
            raise SuggestionNotApplicable("suggestion not found", status_code=404)
        if suggestion["status"] != "pending_review":
            raise SuggestionNotApplicable(
                f"suggestion is '{suggestion['status']}', not 'pending_review'",
                status_code=409,
            )
        if suggestion["suggestion_kind"] != "promotion":
            # Demotion suggestions (bead 5) revoke an existing rule; they are not
            # minted here. Guard rather than silently mis-handle.
            raise SuggestionNotApplicable(
                "only promotion suggestions can be minted into a rule",
                status_code=409,
            )

        action = suggestion["proposed_action"]
        await _assert_route_to_registered(conn, action)

        condition = suggestion["proposed_condition"]
        rule_row = await conn.fetchrow(
            """
            INSERT INTO ingestion_rules
                (scope, rule_type, condition, action, priority, enabled,
                 name, description, created_by, promoted_from_suggestion_id)
            VALUES ('global', $1, $2::jsonb, $3, $4, TRUE, $5, $6, 'promotion', $7)
            RETURNING id, scope, rule_type, condition, action, priority, enabled,
                      name, description, created_by, created_at, updated_at, deleted_at,
                      promoted_from_suggestion_id
            """,
            suggestion["proposed_rule_type"],
            condition,
            action,
            PROMOTED_RULE_PRIORITY,
            f"Promoted: {_condition_label(condition)} → {action}",
            "Auto-created from repeated LLM routing agreement (rule promotion).",
            suggestion["id"],
        )

        await conn.execute(
            """
            UPDATE rule_promotion_suggestions
            SET status = 'confirmed',
                created_rule_id = $1,
                decided_at = $2,
                decided_by = $3
            WHERE id = $4
            """,
            rule_row["id"],
            datetime.now(UTC),
            decided_by,
            suggestion["id"],
        )
        return rule_row


def _condition_label(condition: Any) -> str:
    """Best-effort human label for a rule name (never raises)."""
    if isinstance(condition, dict):
        return str(condition.get("address") or condition.get("domain") or condition)
    return str(condition)


async def apply_suggestion(
    pool: asyncpg.Pool,
    suggestion_id: Any,
    *,
    decided_by: str,
) -> asyncpg.Record:
    """Owner-confirm entry point: mint the rule for one suggestion.

    Thin alias over :func:`mint_rule_from_suggestion` — kept as a named seam so
    the REST confirm handler and any future caller read intent-first.
    """
    return await mint_rule_from_suggestion(pool, suggestion_id, decided_by=decided_by)


async def auto_apply_automated_suggestions(pool: asyncpg.Pool) -> dict[str, int]:
    """Mint every pending clearly-automated skip/metadata_only suggestion.

    The owner-gate-decided auto-apply pass (bu-4pq0s). Best-effort per row: one
    suggestion's failure (e.g. a race that already confirmed it, or a transient
    DB error) is counted and logged, never aborting the rest. Intended to run
    alongside the hourly promotion trigger so a freshly-eligible automated
    suggestion is applied within one cadence rather than waiting for a click
    that, for this tier, never comes.
    """
    result = AutoApplyResult()
    rows = await pool.fetch(
        """
        SELECT id
        FROM rule_promotion_suggestions
        WHERE status = 'pending_review'
          AND suggestion_kind = 'promotion'
          AND is_clearly_automated = TRUE
          AND proposed_action = ANY($1::text[])
        ORDER BY created_at ASC
        """,
        list(AUTO_APPLY_ACTIONS),
    )
    result.candidates = len(rows)
    for row in rows:
        try:
            await mint_rule_from_suggestion(pool, row["id"], decided_by=AUTO_APPLY_ACTOR)
            result.applied += 1
        except SuggestionNotApplicable as exc:
            # A concurrent apply already decided it (409/404) — benign. A 422
            # (unroutable route_to) cannot occur here (skip/metadata_only only).
            logger.info("auto_apply: skipping suggestion %s: %s", row["id"], exc)
            result.skipped_conflict += 1
        except Exception:
            logger.exception("auto_apply: error applying suggestion %s", row["id"])
            result.errors += 1
    return result.as_dict()
