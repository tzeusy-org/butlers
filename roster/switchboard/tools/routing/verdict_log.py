"""Routing verdict mining substrate: ``switchboard.routing_verdict_log`` writer.

Bead 1 of 7 in the switchboard rule-promotion sequence (bu-aga08). See
``docs/plans/2026-07-06-switchboard-rule-promotion-design.md`` section 1 and
``openspec/changes/switchboard-rule-promotion/specs/switchboard-rule-promotion/spec.md``
("Requirement: Routing Verdict Log") for the normative design this module
implements.

This module is the single write path for ``routing_verdict_log`` rows. Callers
(currently ``src/butlers/modules/pipeline.py``'s rule-bypass and LLM-verdict
sites) record durable "the triage layer decided X for sender Y" history so a
later bead (3) can mine repeated agreement into a promoted
``ingestion_rules`` row without excavating per-butler ``sessions.tool_calls``
JSONB.

Degraded-honesty contract: :func:`record_routing_verdict` is best-effort,
mirroring ``butlers.core.attention_ledger.record_attention_event`` — a
ledger-write failure (unmigrated DB, transient connection error, ...) must
never block or fail the routing decision it is describing. Failures are
logged at WARNING and swallowed.

Sender-key normalization: this bead intentionally computes its own minimal
``sender_key`` (lowercase email extraction, same regex approach as
``butlers.ingestion_policy._extract_emails``), independent of bu-qeaou's
in-flight normalized-sender-column work on ``public.ingestion_events``. Bead 7
(low priority, tracked separately) switches this over to bu-qeaou's shared
column once it ships and deletes the local regex here — see the design doc's
"Dependency on bu-qeaou" section for the sequencing rationale.

Verdict-source mapping for rule-shaped bypasses: ``request_context`` carries
``triage_rule_type`` for every non-LLM bypass decision (set by
``roster/switchboard/tools/ingestion/ingest.py::_build_request_context``).
Three shapes exist today:

- an actual ``ingestion_rules`` match (``triage_rule_type`` is the rule's
  ``rule_type``, e.g. ``sender_domain``) -> ``verdict_source='rule'``,
  ``matched_rule_id`` set from ``triage_rule_id``.
- ``triage_rule_type == 'thread_affinity'`` (routing-history lookup, not a
  row in ``ingestion_rules``) -> ``verdict_source='rule'`` with
  ``matched_rule_id=None``. The verdict-source vocabulary is a fixed
  four-value CHECK constraint (``llm``/``rule``/``pinned``/``spot_check``)
  per the spec delta; thread-affinity is a deterministic non-LLM bypass, so
  it is bucketed under ``rule`` rather than inventing a fifth value. This has
  no effect on bead 3's promotion mining, which only ever reads
  ``verdict_source='llm'`` rows.
- ``triage_rule_type == 'pinned_target'`` (explicit dashboard
  ``control.pinned_target`` override) -> ``verdict_source='pinned'``,
  excluded from promotion mining per the spec's "Pinned-target verdict
  excluded from mining" scenario.
"""

from __future__ import annotations

import logging
import re
from typing import Literal
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

VerdictSource = Literal["llm", "rule", "pinned", "spot_check"]
VerdictAction = Literal["route_to", "skip", "metadata_only", "pass_through", "block"]

VALID_VERDICT_SOURCES = frozenset({"llm", "rule", "pinned", "spot_check"})
VALID_VERDICT_ACTIONS = frozenset({"route_to", "skip", "metadata_only", "pass_through", "block"})

# Mirrors butlers.ingestion_policy._EMAIL_RE. Duplicated intentionally (see
# module docstring) — bead 7 removes this copy once bu-qeaou's shared
# normalized-sender column ships.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[\w]+", re.ASCII)


def normalize_sender_key(raw: str | None) -> str:
    """Return a normalized, lowercase sender key for mining/grouping.

    Extracts the first email address found in *raw* (handles RFC 2822
    ``"Display Name <user@example.com>"`` as well as a bare address). Falls
    back to the lowercased, stripped raw string when no email address is
    present (e.g. non-email channels whose sender identity is a chat id or
    phone number) so every channel still gets a stable, comparable key.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    emails = _EMAIL_RE.findall(text)
    if emails:
        return emails[0].lower()
    return text.lower()


async def record_routing_verdict(
    pool: asyncpg.Pool | None,
    *,
    ingestion_event_id: UUID | str | None,
    sender_identity: str | None,
    source_channel: str,
    verdict_source: VerdictSource,
    verdict_action: VerdictAction,
    verdict_target: str | None = None,
    matched_rule_id: UUID | str | None = None,
    session_id: UUID | str | None = None,
) -> str | None:
    """Record one ``routing_verdict_log`` row. Best-effort — never raises.

    Returns the new row's id (as a string) on success, or ``None`` if the
    write could not be completed (pool absent, missing required fields,
    table missing on an unmigrated DB, or any other error). Callers must
    never branch on the return value for routing-affecting decisions — this
    is an observability/mining substrate, not a gate.
    """
    if pool is None:
        return None
    if ingestion_event_id is None:
        logger.warning(
            "record_routing_verdict: missing ingestion_event_id; dropping ledger row "
            "(verdict_source=%s verdict_action=%s)",
            verdict_source,
            verdict_action,
        )
        return None
    if verdict_source not in VALID_VERDICT_SOURCES:
        logger.warning(
            "record_routing_verdict: invalid verdict_source %r; dropping ledger row",
            verdict_source,
        )
        return None
    if verdict_action not in VALID_VERDICT_ACTIONS:
        logger.warning(
            "record_routing_verdict: invalid verdict_action %r; dropping ledger row",
            verdict_action,
        )
        return None

    sender_key = normalize_sender_key(sender_identity)

    try:
        row_id = await pool.fetchval(
            """
            INSERT INTO routing_verdict_log
                (ingestion_event_id, sender_key, source_channel, verdict_source,
                 verdict_action, verdict_target, matched_rule_id, session_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            ingestion_event_id,
            sender_key,
            source_channel,
            verdict_source,
            verdict_action,
            verdict_target,
            matched_rule_id,
            session_id,
        )
    except Exception:
        # Never let verdict-log trouble affect the routing decision it
        # describes — mirrors butlers.core.attention_ledger's degraded-honesty
        # write pattern.
        logger.warning(
            "record_routing_verdict: failed to record verdict row "
            "(ingestion_event_id=%s source_channel=%s verdict_source=%s "
            "verdict_action=%s)",
            ingestion_event_id,
            source_channel,
            verdict_source,
            verdict_action,
            exc_info=True,
        )
        return None
    return str(row_id) if row_id is not None else None
