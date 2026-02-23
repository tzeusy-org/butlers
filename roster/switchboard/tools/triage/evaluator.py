"""Deterministic triage rule evaluator.

Implements the evaluation pipeline from docs/switchboard/pre_classification_triage.md §5.

Pipeline position:
  1. Thread affinity (built-in, not implemented here — caller responsibility).
  2. triage_rules rows (ordered by priority ASC, created_at ASC, id ASC).
  First match wins; no match returns pass_through.

Evaluation is fully deterministic and synchronous against an in-memory rule list.
No database access occurs here; the caller supplies the rule set from the cache.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

_ROUTE_TO_PREFIX = "route_to:"
_VALID_ACTIONS = frozenset({"skip", "metadata_only", "low_priority_queue", "pass_through"})
_VALID_RULE_TYPES = frozenset({"sender_domain", "sender_address", "header_condition", "mime_type"})


@dataclass(frozen=True)
class TriageEnvelope:
    """Normalized ingest envelope fields consumed by the triage evaluator.

    This is a lightweight projection of IngestEnvelopeV1 that the evaluator
    needs — callers extract and pass only what is required.
    """

    sender_address: str
    """Full sender address in lowercase (e.g. 'alerts@chase.com')."""

    source_channel: str
    """Source channel (e.g. 'email', 'telegram')."""

    headers: dict[str, str] = field(default_factory=dict)
    """Normalized headers dict: keys and values preserved as-is from envelope.
    Key comparison is case-insensitive during evaluation.
    """

    mime_parts: list[str] = field(default_factory=list)
    """List of MIME type strings from envelope attachments/parts (e.g. ['text/plain'])."""

    thread_id: str | None = None
    """External thread identity, used by thread-affinity check (caller responsibility)."""


@dataclass(frozen=True)
class TriageDecision:
    """Result of the deterministic triage evaluation.

    Per spec §5.3 output decision contract.
    """

    decision: str
    """One of: route_to, skip, metadata_only, low_priority_queue, pass_through."""

    target_butler: str | None = None
    """Required when decision='route_to'; the target butler name."""

    matched_rule_id: str | None = None
    """UUID of the matched triage rule, or None for thread_affinity / no match."""

    matched_rule_type: str | None = None
    """One of: sender_domain, sender_address, header_condition, mime_type,
    thread_affinity, or None if no match."""

    reason: str = ""
    """Human-readable explanation."""

    @property
    def bypasses_llm(self) -> bool:
        """True when this decision means no LLM classification is needed."""
        return self.decision != "pass_through"


# ---------------------------------------------------------------------------
# Internal rule evaluation helpers
# ---------------------------------------------------------------------------


def _sender_domain(address: str) -> str:
    """Extract lowercase domain from a sender address."""
    address = address.strip().lower()
    if "@" in address:
        return address.split("@", 1)[1]
    return address


def _match_sender_domain(sender_address: str, condition: dict[str, Any]) -> bool:
    """Evaluate a sender_domain condition against the sender address.

    Condition schema:
      {"domain": "chase.com", "match": "exact" | "suffix"}

    suffix match: domain == suffix OR domain ends with ".<suffix>"
    """
    domain_pattern = str(condition.get("domain", "")).strip().lower()
    match_type = str(condition.get("match", "exact")).strip().lower()

    if not domain_pattern:
        return False

    sender_domain = _sender_domain(sender_address)

    if match_type == "exact":
        return sender_domain == domain_pattern
    if match_type == "suffix":
        return sender_domain == domain_pattern or sender_domain.endswith(f".{domain_pattern}")

    logger.warning("Unknown sender_domain match type: %r", match_type)
    return False


def _match_sender_address(sender_address: str, condition: dict[str, Any]) -> bool:
    """Evaluate a sender_address condition.

    Condition schema: {"address": "alerts@chase.com"}
    Comparison is case-insensitive.
    """
    target = str(condition.get("address", "")).strip().lower()
    return bool(target) and sender_address.strip().lower() == target


def _match_header_condition(headers: dict[str, str], condition: dict[str, Any]) -> bool:
    """Evaluate a header_condition.

    Condition schema:
      {
        "header": "List-Unsubscribe",
        "op": "present" | "equals" | "contains",
        "value": null | "string"
      }

    Header key matching is case-insensitive.
    """
    header_name = str(condition.get("header", "")).strip()
    op = str(condition.get("op", "")).strip().lower()
    value = condition.get("value")

    if not header_name or not op:
        return False

    # Case-insensitive header key lookup
    header_name_lower = header_name.lower()
    matched_value: str | None = None
    for key, hval in headers.items():
        if key.lower() == header_name_lower:
            matched_value = hval
            break

    if op == "present":
        return matched_value is not None

    if matched_value is None:
        return False

    if op == "equals":
        if value is None:
            return False
        return matched_value.strip() == str(value).strip()

    if op == "contains":
        if value is None:
            return False
        return str(value) in matched_value

    logger.warning("Unknown header_condition op: %r", op)
    return False


def _match_mime_type(mime_parts: list[str], condition: dict[str, Any]) -> bool:
    """Evaluate a mime_type condition.

    Condition schema: {"type": "text/calendar" | "image/*"}

    Supports exact matching and wildcard subtype with '/*' suffix.
    Evaluation is across all normalized MIME parts.
    """
    pattern = str(condition.get("type", "")).strip().lower()
    if not pattern:
        return False

    is_wildcard = pattern.endswith("/*")

    for part in mime_parts:
        part_lower = part.strip().lower()
        if is_wildcard:
            # e.g. "image/*" matches "image/png", "image/jpeg"
            main_type = pattern[:-2]  # strip "/*"
            if part_lower.startswith(f"{main_type}/") or part_lower == main_type:
                return True
        else:
            if part_lower == pattern:
                return True

    return False


def _evaluate_single_rule(
    envelope: TriageEnvelope,
    rule: dict[str, Any],
) -> bool:
    """Return True if the envelope matches this rule's condition."""
    rule_type = str(rule.get("rule_type", ""))
    condition = rule.get("condition") or {}

    if rule_type == "sender_domain":
        return _match_sender_domain(envelope.sender_address, condition)
    if rule_type == "sender_address":
        return _match_sender_address(envelope.sender_address, condition)
    if rule_type == "header_condition":
        return _match_header_condition(envelope.headers, condition)
    if rule_type == "mime_type":
        return _match_mime_type(envelope.mime_parts, condition)

    logger.warning("Unknown rule_type during triage evaluation: %r", rule_type)
    return False


def _parse_action(action: str) -> tuple[str, str | None]:
    """Parse action string into (decision, target_butler | None).

    Returns:
      ("route_to", "finance") for "route_to:finance"
      ("skip", None) for "skip"
      etc.
    """
    if action.startswith(_ROUTE_TO_PREFIX):
        target = action[len(_ROUTE_TO_PREFIX) :]
        return "route_to", target or None
    return action, None


# ---------------------------------------------------------------------------
# Public evaluation function
# ---------------------------------------------------------------------------


def evaluate_triage(
    envelope: TriageEnvelope,
    rules: list[dict[str, Any]],
    *,
    thread_affinity_target: str | None = None,
) -> TriageDecision:
    """Evaluate the triage pipeline and return a TriageDecision.

    Implements the spec §5.4 deterministic evaluation order:
      1. Thread affinity (if thread_affinity_target is provided).
      2. triage_rules rows in priority/created_at/id order.
      First match wins. No match returns pass_through.

    Parameters
    ----------
    envelope:
        Normalized envelope fields to evaluate.
    rules:
        Active triage rules sorted by (priority ASC, created_at ASC, id ASC).
        Each rule dict must have: id, rule_type, condition, action.
    thread_affinity_target:
        Pre-resolved thread affinity butler name, or None if no affinity hit.

    Returns
    -------
    TriageDecision
        The triage outcome for this envelope.
    """
    # Step 1: Thread affinity (pre-LLM, highest precedence)
    if thread_affinity_target:
        return TriageDecision(
            decision="route_to",
            target_butler=thread_affinity_target,
            matched_rule_id=None,
            matched_rule_type="thread_affinity",
            reason=f"thread affinity match → {thread_affinity_target}",
        )

    # Step 2: Evaluate triage_rules in order
    for rule in rules:
        try:
            matched = _evaluate_single_rule(envelope, rule)
        except Exception:
            logger.exception(
                "Unexpected error evaluating triage rule id=%s type=%s; skipping",
                rule.get("id"),
                rule.get("rule_type"),
            )
            continue

        if not matched:
            continue

        action = str(rule.get("action", "pass_through"))
        decision, target_butler = _parse_action(action)
        rule_id = str(rule.get("id", "")) or None
        rule_type = str(rule.get("rule_type", "")) or None

        return TriageDecision(
            decision=decision,
            target_butler=target_butler,
            matched_rule_id=rule_id,
            matched_rule_type=rule_type,
            reason=f"{rule_type} match → {action}",
        )

    # No match — pass through to LLM classification
    return TriageDecision(
        decision="pass_through",
        target_butler=None,
        matched_rule_id=None,
        matched_rule_type=None,
        reason="no deterministic rule matched",
    )


def make_triage_envelope_from_ingest(envelope_data: dict[str, Any]) -> TriageEnvelope:
    """Build a TriageEnvelope from a raw ingest.v1 envelope dict.

    This is a convenience adapter for the ingest pipeline integration.
    Handles missing/None fields gracefully.

    Parameters
    ----------
    envelope_data:
        Raw ingest.v1 envelope payload dict (already validated by ingest_v1).
    """
    sender = envelope_data.get("sender") or {}
    sender_address = str(sender.get("identity") or "").lower()

    source = envelope_data.get("source") or {}
    source_channel = str(source.get("channel") or "")

    payload = envelope_data.get("payload") or {}
    raw = payload.get("raw") or {}

    # Extract headers from raw payload (email-specific)
    raw_headers = raw.get("headers") or {}
    if isinstance(raw_headers, dict):
        headers = {str(k): str(v) for k, v in raw_headers.items()}
    else:
        headers = {}

    # Extract MIME parts from attachments or payload
    mime_parts: list[str] = []
    attachments = payload.get("attachments") or []
    for att in attachments:
        if isinstance(att, dict) and att.get("media_type"):
            mime_parts.append(str(att["media_type"]).lower())

    # Also extract from raw.mime_parts if present
    raw_mime = raw.get("mime_parts") or []
    for part in raw_mime:
        if isinstance(part, dict) and part.get("type"):
            mime_parts.append(str(part["type"]).lower())
        elif isinstance(part, str):
            mime_parts.append(part.lower())

    event = envelope_data.get("event") or {}
    thread_id = event.get("external_thread_id")

    return TriageEnvelope(
        sender_address=sender_address,
        source_channel=source_channel,
        headers=headers,
        mime_parts=mime_parts,
        thread_id=str(thread_id) if thread_id else None,
    )


__all__ = [
    "TriageDecision",
    "TriageEnvelope",
    "evaluate_triage",
    "make_triage_envelope_from_ingest",
]
