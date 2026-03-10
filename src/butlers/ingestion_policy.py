"""Unified ingestion policy evaluator.

Implements the IngestionPolicyEvaluator from the unified-ingestion-policy
design (D5-D7). Replaces both TriageRuleCache + evaluate_triage() and
SourceFilterEvaluator with a single scope-aware evaluator backed by the
``ingestion_rules`` table.

Pipeline position:
  - scope='global': post-ingest, pre-LLM (replaces triage_rules evaluation).
  - scope='connector:*': pre-ingest (replaces source_filter evaluation).

Evaluation:
  1. Load rules for the configured scope (WHERE scope=$1 AND enabled AND
     deleted_at IS NULL ORDER BY priority, created_at, id).
  2. Iterate rules in order. First match wins.
  3. No match returns pass_through (global) or allow (connector).
  4. TTL 60s background refresh. Fail-open on DB error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from butlers.ingestion_policy_metrics import IngestionPolicyMetrics

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

_ROUTE_TO_PREFIX = "route_to:"
_VALID_GLOBAL_ACTIONS = frozenset({"skip", "metadata_only", "low_priority_queue", "pass_through"})
_VALID_CONNECTOR_ACTIONS = frozenset({"block", "pass_through"})
_KNOWN_RULE_TYPES = frozenset(
    {
        "sender_domain",
        "sender_address",
        "header_condition",
        "mime_type",
        "substring",
        "chat_id",
        "channel_id",
    }
)


@dataclass(frozen=True)
class IngestionEnvelope:
    """Normalized envelope carrying all fields needed by any rule_type.

    Connectors populate only the fields relevant to their channel.
    The evaluator extracts the appropriate key per ``rule_type`` internally.

    See design.md D6.
    """

    sender_address: str = ""
    """Normalized email address or empty string."""

    source_channel: str = ""
    """Source channel identifier: 'email', 'telegram', 'discord', etc."""

    headers: dict[str, str] = field(default_factory=dict)
    """Email headers (case-insensitive keys). Empty for non-email channels."""

    mime_parts: list[str] = field(default_factory=list)
    """MIME type strings from envelope attachments/parts. Empty for non-email."""

    thread_id: str | None = None
    """External thread ID (used by thread-affinity, caller responsibility)."""

    raw_key: str = ""
    """Connector-specific opaque key for substring/chat_id/channel_id matching.

    For Gmail: the From header value.
    For Telegram: the chat_id as a string.
    For Discord: the channel_id as a string.
    """


@dataclass(frozen=True)
class PolicyDecision:
    """Result of the ingestion policy evaluation.

    Compatible with both global (triage) and connector (source filter) scopes.
    """

    action: str
    """Resolved action: block, skip, metadata_only, low_priority_queue,
    pass_through, or route_to."""

    target_butler: str | None = None
    """Required when action='route_to'; the target butler name."""

    matched_rule_id: str | None = None
    """UUID of the matched rule, or None for no match / thread affinity."""

    matched_rule_type: str | None = None
    """The rule_type of the matched rule, or None."""

    reason: str = ""
    """Human-readable explanation."""

    @property
    def bypasses_llm(self) -> bool:
        """True when the decision means no LLM classification is needed."""
        return self.action != "pass_through"

    @property
    def allowed(self) -> bool:
        """Convenience for connector-scoped evaluation: True if not blocked."""
        return self.action != "block"


# ---------------------------------------------------------------------------
# Condition matchers (all 7 rule_types)
# ---------------------------------------------------------------------------


def _sender_domain(address: str) -> str:
    """Extract lowercase domain from a sender address."""
    address = address.strip().lower()
    if "@" in address:
        return address.split("@", 1)[1]
    return address


def _match_sender_domain(envelope: IngestionEnvelope, condition: dict[str, Any]) -> bool:
    """Match sender_domain: exact or suffix against sender_address domain.

    Condition schema: {"domain": "chase.com", "match": "exact" | "suffix" | "any"}
    """
    domain_pattern = str(condition.get("domain", "")).strip().lower()
    match_type = str(condition.get("match", "exact")).strip().lower()

    if not domain_pattern:
        return False

    # Catch-all wildcard (from whitelist migration)
    if match_type == "any" or domain_pattern == "*":
        return True

    sender_domain = _sender_domain(envelope.sender_address)

    if match_type == "exact":
        return sender_domain == domain_pattern
    if match_type == "suffix":
        return sender_domain == domain_pattern or sender_domain.endswith(f".{domain_pattern}")

    logger.warning("Unknown sender_domain match type: %r", match_type)
    return False


def _match_sender_address(envelope: IngestionEnvelope, condition: dict[str, Any]) -> bool:
    """Match sender_address: exact or local-part prefix (case-insensitive).

    Condition schema:
      Exact:  {"address": "alerts@chase.com"}
      Prefix: {"address": "noreply", "match": "local_part_prefix"}
    """
    target = str(condition.get("address", "")).strip().lower()
    if not target:
        return False
    # Catch-all wildcard
    if target == "*":
        return True
    sender = envelope.sender_address.strip().lower()
    match_mode = str(condition.get("match", "")).strip().lower()
    if match_mode == "local_part_prefix":
        local_part = sender.split("@", 1)[0] if "@" in sender else sender
        return local_part.startswith(target)
    return sender == target


def _match_header_condition(envelope: IngestionEnvelope, condition: dict[str, Any]) -> bool:
    """Match header_condition: present/equals/contains.

    Condition schema:
      {"header": "List-Unsubscribe", "op": "present" | "equals" | "contains",
       "value": null | "string"}
    """
    header_name = str(condition.get("header", "")).strip()
    op = str(condition.get("op", "")).strip().lower()
    value = condition.get("value")

    if not header_name or not op:
        return False

    # Case-insensitive header key lookup
    header_name_lower = header_name.lower()
    matched_value: str | None = None
    for key, hval in envelope.headers.items():
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


def _match_mime_type(envelope: IngestionEnvelope, condition: dict[str, Any]) -> bool:
    """Match mime_type: exact or wildcard subtype (/*).

    Condition schema: {"type": "text/calendar" | "image/*"}
    """
    pattern = str(condition.get("type", "")).strip().lower()
    if not pattern:
        return False

    is_wildcard = pattern.endswith("/*")

    for part in envelope.mime_parts:
        part_lower = part.strip().lower()
        if is_wildcard:
            main_type = pattern[:-2]  # strip "/*"
            if part_lower.startswith(f"{main_type}/") or part_lower == main_type:
                return True
        else:
            if part_lower == pattern:
                return True

    return False


def _match_substring(envelope: IngestionEnvelope, condition: dict[str, Any]) -> bool:
    """Match substring: case-insensitive substring in raw_key.

    Condition schema: {"pattern": "some text"}
    """
    pattern = str(condition.get("pattern", "")).strip()
    if not pattern:
        return False
    # Catch-all wildcard
    if pattern == "*":
        return True
    return pattern.lower() in envelope.raw_key.lower()


def _match_chat_id(envelope: IngestionEnvelope, condition: dict[str, Any]) -> bool:
    """Match chat_id: exact string equality against raw_key.

    Condition schema: {"chat_id": "987654321"}
    """
    target = str(condition.get("chat_id", "")).strip()
    if not target:
        return False
    # Catch-all wildcard
    if target == "*":
        return True
    return envelope.raw_key.strip() == target


def _match_channel_id(envelope: IngestionEnvelope, condition: dict[str, Any]) -> bool:
    """Match channel_id: exact string equality against raw_key.

    Condition schema: {"channel_id": "987654321098765432"}
    """
    target = str(condition.get("channel_id", "")).strip()
    if not target:
        return False
    # Catch-all wildcard
    if target == "*":
        return True
    return envelope.raw_key.strip() == target


# Map rule_type → matcher function
_MATCHERS: dict[str, Any] = {
    "sender_domain": _match_sender_domain,
    "sender_address": _match_sender_address,
    "header_condition": _match_header_condition,
    "mime_type": _match_mime_type,
    "substring": _match_substring,
    "chat_id": _match_chat_id,
    "channel_id": _match_channel_id,
}


def _parse_action(action: str) -> tuple[str, str | None]:
    """Parse action string into (action_name, target_butler | None).

    Returns ("route_to", "finance") for "route_to:finance",
    ("skip", None) for "skip", etc.
    """
    if action.startswith(_ROUTE_TO_PREFIX):
        target = action[len(_ROUTE_TO_PREFIX) :]
        return "route_to", target or None
    return action, None


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator
# ---------------------------------------------------------------------------


class IngestionPolicyEvaluator:
    """Unified ingestion policy evaluator with scope-aware DB loading.

    Replaces both TriageRuleCache and SourceFilterEvaluator.

    Parameters
    ----------
    scope:
        Scope string: ``'global'`` or ``'connector:<type>:<identity>'``.
    db_pool:
        asyncpg connection pool for rule loading. May be ``None`` -- if so,
        the evaluator runs with an empty rule set (fail-open).
    refresh_interval_s:
        Seconds between TTL cache refreshes (default 60).
    """

    def __init__(
        self,
        scope: str,
        db_pool: asyncpg.Pool | None,
        refresh_interval_s: float = 60,
    ) -> None:
        self._scope = scope
        self._db_pool = db_pool
        self._refresh_interval_s = refresh_interval_s

        self._rules: list[dict[str, Any]] = []
        self._last_loaded_at: float | None = None
        self._load_lock = asyncio.Lock()
        self._background_refresh_task: asyncio.Task[None] | None = None
        self._metrics = IngestionPolicyMetrics(scope)

    @property
    def scope(self) -> str:
        """The scope this evaluator is configured for."""
        return self._scope

    @property
    def rules(self) -> list[dict[str, Any]]:
        """Current cached rule set (read-only snapshot)."""
        return self._rules

    # ------------------------------------------------------------------
    # DB loading
    # ------------------------------------------------------------------

    async def _load_rules(self) -> None:
        """Query ingestion_rules for this scope's active rules.

        On DB error: log WARNING and retain previous cache (fail-open).
        """
        if self._db_pool is None:
            logger.warning(
                "ingestion_policy: no DB pool for scope=%s — ALL rules disabled (fail-open). "
                "Pass a db_pool to the IngestionPolicyEvaluator to enable rule evaluation.",
                self._scope,
            )
            self._last_loaded_at = time.monotonic()
            return

        query = """
            SELECT
                id::text       AS id,
                rule_type,
                condition,
                action,
                priority,
                name,
                created_at::text AS created_at
            FROM switchboard.ingestion_rules
            WHERE scope = $1
              AND enabled = TRUE
              AND deleted_at IS NULL
            ORDER BY priority ASC, created_at ASC, id ASC
        """
        try:
            rows = await self._db_pool.fetch(query, self._scope)
            new_rules: list[dict[str, Any]] = []
            skipped = 0

            for row in rows:
                raw: dict[str, Any] = dict(row)
                condition = raw.get("condition")
                # asyncpg returns JSONB as dict when codec is registered,
                # but as a string when using a plain pool (e.g. cursor_pool).
                if isinstance(condition, str):
                    try:
                        condition = json.loads(condition)
                        raw["condition"] = condition
                    except (json.JSONDecodeError, TypeError):
                        pass
                if not isinstance(condition, dict):
                    logger.warning(
                        "ingestion_policy: rule id=%s has non-dict condition (%r); skipping",
                        raw.get("id"),
                        type(condition).__name__ if condition is not None else "None",
                    )
                    skipped += 1
                    continue

                rule_type = raw.get("rule_type")
                if rule_type not in _KNOWN_RULE_TYPES:
                    logger.warning(
                        "ingestion_policy: rule id=%s has unknown rule_type %r; skipping",
                        raw.get("id"),
                        rule_type,
                    )
                    skipped += 1
                    continue

                new_rules.append(raw)

            is_initial = self._last_loaded_at is None
            self._rules = new_rules
            self._last_loaded_at = time.monotonic()

            # Initial load at INFO so operators see rule count; refreshes at DEBUG.
            log_fn = logger.info if is_initial else logger.debug
            log_fn(
                "ingestion_policy: loaded %d rule(s) for scope=%s (%d skipped)",
                len(new_rules),
                self._scope,
                skipped,
            )
            if is_initial and len(new_rules) == 0 and self._scope == "global":
                logger.warning(
                    "ingestion_policy: zero global rules loaded — "
                    "no ingestion filtering will be applied",
                )
        except Exception as exc:
            logger.warning(
                "ingestion_policy: failed to load rules for scope=%s "
                "(retaining cache, fail-open): %s",
                self._scope,
                exc,
            )
            # Update timestamp so we don't hammer the DB every evaluate() call
            self._last_loaded_at = time.monotonic()

    # ------------------------------------------------------------------
    # Public: ensure_loaded
    # ------------------------------------------------------------------

    async def ensure_loaded(self) -> None:
        """Perform the initial rule load.

        Must be called once before evaluation begins. Subsequent refreshes
        are triggered lazily from ``evaluate()`` via a background task.
        """
        async with self._load_lock:
            if self._last_loaded_at is None:
                await self._load_rules()

    # ------------------------------------------------------------------
    # Public: evaluate
    # ------------------------------------------------------------------

    def evaluate(self, envelope: IngestionEnvelope) -> PolicyDecision:
        """Evaluate the envelope against cached rules. First match wins.

        Triggers a background TTL refresh if the cache is stale, but does
        **not** await it -- callers receive a response from the current
        cache without blocking.

        Records OTel telemetry metrics (D11): rule_matched / rule_pass_through
        counters and evaluation_latency_ms histogram.

        Parameters
        ----------
        envelope:
            Populated IngestionEnvelope to evaluate.

        Returns
        -------
        PolicyDecision
            The policy decision. No match returns ``action='pass_through'``.
        """
        self._maybe_schedule_refresh()
        t0 = time.perf_counter()

        for rule in self._rules:
            try:
                matched = self._evaluate_single_rule(envelope, rule)
            except Exception:
                logger.exception(
                    "ingestion_policy: error evaluating rule id=%s type=%s; skipping",
                    rule.get("id"),
                    rule.get("rule_type"),
                )
                continue

            if not matched:
                continue

            action_str = str(rule.get("action", "pass_through"))
            action_name, target_butler = _parse_action(action_str)
            rule_id = str(rule.get("id", "")) or None
            rule_type = str(rule.get("rule_type", "")) or None

            latency_ms = (time.perf_counter() - t0) * 1000
            self._metrics.record_match(
                rule_type=rule_type or "unknown",
                action=action_str,
                source_channel=envelope.source_channel,
                latency_ms=latency_ms,
            )

            return PolicyDecision(
                action=action_name,
                target_butler=target_butler,
                matched_rule_id=rule_id,
                matched_rule_type=rule_type,
                reason=f"{rule_type} match -> {action_str}",
            )

        # No match -- pass through
        latency_ms = (time.perf_counter() - t0) * 1000
        self._metrics.record_pass_through(
            source_channel=envelope.source_channel,
            reason="no rule matched",
            latency_ms=latency_ms,
        )

        return PolicyDecision(
            action="pass_through",
            target_butler=None,
            matched_rule_id=None,
            matched_rule_type=None,
            reason="no rule matched",
        )

    # ------------------------------------------------------------------
    # Public: invalidate
    # ------------------------------------------------------------------

    def invalidate(self) -> None:
        """Mark cache as stale to force refresh on next evaluate().

        Designed for event-driven invalidation triggered by rule mutations.
        Does NOT immediately reload.
        """
        self._last_loaded_at = 0.0
        logger.debug("ingestion_policy: cache invalidated for scope=%s", self._scope)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _evaluate_single_rule(envelope: IngestionEnvelope, rule: dict[str, Any]) -> bool:
        """Return True if the envelope matches this rule's condition."""
        rule_type = str(rule.get("rule_type", ""))
        condition = rule.get("condition") or {}

        matcher = _MATCHERS.get(rule_type)
        if matcher is None:
            logger.warning(
                "ingestion_policy: unknown rule_type %r during evaluation; skipping",
                rule_type,
            )
            return False

        return matcher(envelope, condition)

    def _maybe_schedule_refresh(self) -> None:
        """Schedule a background cache refresh if the TTL has elapsed."""
        if self._last_loaded_at is None:
            return
        elapsed = time.monotonic() - self._last_loaded_at
        if elapsed < self._refresh_interval_s:
            return
        # Don't stack multiple refresh tasks
        if self._background_refresh_task is not None and not self._background_refresh_task.done():
            return
        self._background_refresh_task = asyncio.create_task(self._load_rules())


__all__ = [
    "IngestionEnvelope",
    "IngestionPolicyEvaluator",
    "PolicyDecision",
]
