"""Gmail connector tier assignment and label filtering.

Implements connector-side tier assignment and label include/exclude policy from:
  - docs/connectors/email_ingestion_policy.md
  - docs/switchboard/email_priority_queuing.md

Three-layer pipeline (applied in order):
  1. Label include/exclude filtering (gates ingestion before tiering).
  2. Policy tier assignment (high_priority / interactive / default).
  3. Ingestion tier classification (Tier 1 = full, Tier 2 = metadata-only, Tier 3 = skip).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from prometheus_client import Counter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus tier counters
# ---------------------------------------------------------------------------

_gmail_tier_counter = Counter(
    "butlers_connector_gmail_tier_assigned_total",
    "Gmail messages assigned to each ingestion tier",
    labelnames=["endpoint_identity", "ingestion_tier", "reason"],
)

_gmail_policy_tier_counter = Counter(
    "butlers_connector_gmail_priority_tier_assigned_total",
    "Gmail messages assigned to each policy priority tier (per spec §6.1)",
    labelnames=["endpoint_identity", "policy_tier", "assignment_rule"],
)

_gmail_label_filter_counter = Counter(
    "butlers_connector_gmail_label_filter_total",
    "Gmail messages filtered by label include/exclude policy",
    labelnames=["endpoint_identity", "filter_action", "reason"],
)


# ---------------------------------------------------------------------------
# Email address normalization
# ---------------------------------------------------------------------------

_ANGLE_BRACKET_RE = re.compile(r"<([^>]+)>")


def _normalize_email(address: str) -> str:
    """Normalize an email address for comparison.

    Per spec §2.3: trim whitespace, strip angle-bracket formatting,
    compare lowercase. Gmail-specific alias rewrites (dot removal, +tag
    stripping) are NOT applied unless both sides are normalized the same way.
    """
    address = address.strip()
    # Strip display-name angle-bracket: "Alice <alice@example.com>" -> "alice@example.com"
    m = _ANGLE_BRACKET_RE.search(address)
    if m:
        address = m.group(1).strip()
    return address.lower()


def _extract_addresses(raw: str) -> list[str]:
    """Extract all normalized email addresses from a comma-separated header."""
    parts = [p.strip() for p in raw.split(",")]
    return [_normalize_email(p) for p in parts if p]


def _header_value(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup."""
    name_lower = name.lower()
    for k, v in headers.items():
        if k.lower() == name_lower:
            return v
    return None


# ---------------------------------------------------------------------------
# Label filter policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelFilterPolicy:
    """Include/exclude label policy for Gmail ingestion.

    Rules (per spec §9):
    - Label filters MUST be applied before triage evaluation.
    - GMAIL_LABEL_EXCLUDE takes precedence over include matches.
    - Empty include list means all labels allowed (except excluded).
    - Deployments SHOULD exclude SPAM and TRASH.

    Label comparisons are case-insensitive.
    """

    include_labels: frozenset[str]
    """Allowlist of Gmail label IDs/names. Empty = allow all."""

    exclude_labels: frozenset[str]
    """Blocklist of Gmail label IDs/names. Checked before include."""

    @classmethod
    def from_lists(
        cls,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> LabelFilterPolicy:
        """Build policy from raw label lists (normalized to uppercase for Gmail system labels)."""
        normalized_include = frozenset(
            label.strip().upper() for label in (include or []) if label.strip()
        )
        normalized_exclude = frozenset(
            label.strip().upper() for label in (exclude or []) if label.strip()
        )
        return cls(include_labels=normalized_include, exclude_labels=normalized_exclude)

    def evaluate(self, message_labels: list[str]) -> tuple[bool, str]:
        """Evaluate whether a message should be ingested based on its labels.

        Returns:
            (should_ingest, reason) - True to allow, False to skip.
        """
        normalized_msg_labels = {label.strip().upper() for label in message_labels}

        # Exclude check takes precedence
        excluded_hits = normalized_msg_labels & self.exclude_labels
        if excluded_hits:
            return False, f"label_excluded:{','.join(sorted(excluded_hits))}"

        # Include check (empty include = allow all)
        if self.include_labels:
            included_hits = normalized_msg_labels & self.include_labels
            if not included_hits:
                return False, "label_not_in_include_list"

        return True, "label_allowed"

    @classmethod
    def default(cls) -> LabelFilterPolicy:
        """Default policy: exclude SPAM and TRASH, allow all others."""
        return cls.from_lists(exclude=["SPAM", "TRASH"])


# ---------------------------------------------------------------------------
# Policy tier assignment (high_priority / interactive / default)
# ---------------------------------------------------------------------------

# Policy tier values per spec §2.1
POLICY_TIER_HIGH_PRIORITY = "high_priority"
POLICY_TIER_INTERACTIVE = "interactive"
POLICY_TIER_DEFAULT = "default"

# Assignment rule labels for telemetry
RULE_KNOWN_CONTACT = "known_contact"
RULE_REPLY_TO_OUTBOUND = "reply_to_outbound"
RULE_DIRECT_CORRESPONDENCE = "direct_correspondence"
RULE_FALLBACK_DEFAULT = "fallback_default"


@dataclass
class PolicyTierAssigner:
    """Assigns policy_tier to a Gmail message.

    Rules per spec §2.2 (evaluated in order, first match wins):
    1. high_priority - sender is in known-contact set.
    2. high_priority - In-Reply-To references a sent-message id.
    3. interactive  - user address in To/Cc, no List-Unsubscribe, no bulk Precedence.
    4. default      - fallback.
    """

    user_email: str
    """Normalized user email (the account owner's address)."""

    known_contacts: frozenset[str] = field(default_factory=frozenset)
    """Set of normalized known-contact email addresses."""

    sent_message_ids: frozenset[str] = field(default_factory=frozenset)
    """Set of Message-IDs sent by the user (for reply detection)."""

    def __post_init__(self) -> None:
        self.user_email = _normalize_email(self.user_email)

    def assign(
        self,
        sender_address: str,
        headers: dict[str, str],
    ) -> tuple[str, str]:
        """Assign policy_tier and return (tier, assignment_rule).

        Parameters
        ----------
        sender_address:
            Raw sender address string (From header value).
        headers:
            All message headers (dict, key case-insensitive).

        Returns
        -------
        (policy_tier, assignment_rule) tuple.
        """
        normalized_sender = _normalize_email(sender_address)

        # Rule 1: Known contact
        if normalized_sender in self.known_contacts:
            return POLICY_TIER_HIGH_PRIORITY, RULE_KNOWN_CONTACT

        # Rule 2: Reply to user's outbound mail
        in_reply_to = _header_value(headers, "In-Reply-To")
        if in_reply_to and self.sent_message_ids:
            # Strip angle brackets and whitespace from In-Reply-To value
            reply_msg_id = in_reply_to.strip().strip("<>").strip()
            msg_id_bracketed = f"<{reply_msg_id}>"
            if reply_msg_id and (
                msg_id_bracketed in self.sent_message_ids or reply_msg_id in self.sent_message_ids
            ):
                return POLICY_TIER_HIGH_PRIORITY, RULE_REPLY_TO_OUTBOUND

        # Rule 3: Direct correspondence
        # - user address in To or Cc
        # - no List-Unsubscribe header
        # - no bulk signal in Precedence
        to_header = _header_value(headers, "To") or ""
        cc_header = _header_value(headers, "Cc") or ""
        all_recipients = _extract_addresses(to_header) + _extract_addresses(cc_header)
        user_is_recipient = self.user_email in all_recipients

        has_list_unsubscribe = _header_value(headers, "List-Unsubscribe") is not None

        precedence = _header_value(headers, "Precedence") or ""
        has_bulk_precedence = precedence.strip().lower() in ("bulk", "list")

        if user_is_recipient and not has_list_unsubscribe and not has_bulk_precedence:
            return POLICY_TIER_INTERACTIVE, RULE_DIRECT_CORRESPONDENCE

        # Rule 4: Default fallback
        return POLICY_TIER_DEFAULT, RULE_FALLBACK_DEFAULT


# ---------------------------------------------------------------------------
# Ingestion tier classification (Tier 1 / 2 / 3)
# ---------------------------------------------------------------------------

# Ingestion tier values
INGESTION_TIER_FULL = 1  # Full pipeline
INGESTION_TIER_METADATA = 2  # Metadata-only pipeline
INGESTION_TIER_SKIP = 3  # Skip pipeline

# ingestion_tier string names for envelope control field
INGESTION_TIER_NAME = {
    INGESTION_TIER_FULL: "full",
    INGESTION_TIER_METADATA: "metadata",
    INGESTION_TIER_SKIP: "skip",
}


def classify_ingestion_tier(triage_action: str) -> int:
    """Map a triage action to an ingestion tier per spec §4.

    Action-to-tier mapping:
    - route_to -> Tier 1
    - metadata_only -> Tier 2
    - skip -> Tier 3
    - low_priority_queue -> Tier 1 (deferred dispatch, not metadata-only)
    - pass_through -> Tier 1 (default is Tier 1 for safety)
    - (no match) -> Tier 1

    Parameters
    ----------
    triage_action:
        Action string from triage rule evaluation (e.g. "skip", "metadata_only",
        "route_to:finance", "low_priority_queue", "pass_through").
    """
    action_lower = triage_action.strip().lower()

    if action_lower.startswith("route_to"):
        return INGESTION_TIER_FULL
    if action_lower == "metadata_only":
        return INGESTION_TIER_METADATA
    if action_lower == "skip":
        return INGESTION_TIER_SKIP
    if action_lower == "low_priority_queue":
        return INGESTION_TIER_FULL
    # pass_through and all unknown actions default to Tier 1 for safety
    return INGESTION_TIER_FULL


# ---------------------------------------------------------------------------
# Full message policy evaluation (label filter + policy tier + ingestion tier)
# ---------------------------------------------------------------------------


@dataclass
class MessagePolicyResult:
    """Result of applying the full policy pipeline to a Gmail message."""

    should_ingest: bool
    """True if the message should be submitted to Switchboard ingest."""

    ingestion_tier: int
    """Ingestion tier: 1 = full, 2 = metadata-only, 3 = skip."""

    policy_tier: str
    """Policy tier string: high_priority, interactive, or default."""

    assignment_rule: str
    """Policy tier assignment rule for telemetry."""

    filter_reason: str
    """Label filter reason (e.g. 'label_allowed', 'label_excluded:SPAM')."""

    triage_action: str
    """Triage action that drove ingestion tier, or 'pass_through' if no match."""


def evaluate_message_policy(
    message_data: dict[str, Any],
    *,
    label_filter: LabelFilterPolicy,
    tier_assigner: PolicyTierAssigner,
    triage_rules: list[dict[str, Any]] | None = None,
    endpoint_identity: str = "",
) -> MessagePolicyResult:
    """Evaluate the full policy pipeline for a single Gmail message.

    Pipeline order per spec §8:
    1. Apply label include/exclude filters.
    2. Evaluate triage rules to get ingestion tier.
    3. Assign policy_tier for queue ordering.
    4. Emit counters.

    Parameters
    ----------
    message_data:
        Raw Gmail message dict from messages.get API.
    label_filter:
        Label include/exclude filter policy.
    tier_assigner:
        Policy tier assigner (known contacts, sent IDs, user email).
    triage_rules:
        Active triage rules (sorted, evaluated in order). If None or empty,
        defaults to pass_through -> Tier 1.
    endpoint_identity:
        Connector endpoint identity for Prometheus label cardinality.

    Returns
    -------
    MessagePolicyResult
    """
    # Extract message labels
    message_labels: list[str] = message_data.get("labelIds") or []

    # --- Step 1: Label filter ---
    should_ingest, filter_reason = label_filter.evaluate(message_labels)
    if not should_ingest:
        _gmail_label_filter_counter.labels(
            endpoint_identity=endpoint_identity,
            filter_action="excluded",
            reason=filter_reason,
        ).inc()
        logger.debug(
            "Message excluded by label filter: reason=%s labels=%s",
            filter_reason,
            message_labels,
        )
        return MessagePolicyResult(
            should_ingest=False,
            ingestion_tier=INGESTION_TIER_SKIP,
            policy_tier=POLICY_TIER_DEFAULT,
            assignment_rule=RULE_FALLBACK_DEFAULT,
            filter_reason=filter_reason,
            triage_action="skip",
        )

    _gmail_label_filter_counter.labels(
        endpoint_identity=endpoint_identity,
        filter_action="allowed",
        reason=filter_reason,
    ).inc()

    # --- Step 2: Triage rule evaluation -> ingestion tier ---
    triage_action = "pass_through"
    if triage_rules:
        triage_action = _evaluate_triage_rules(message_data, triage_rules)

    ingestion_tier = classify_ingestion_tier(triage_action)

    # --- Step 3: Policy tier assignment ---
    headers_raw = message_data.get("payload", {}).get("headers", [])
    headers_dict: dict[str, str] = {}
    for h in headers_raw:
        if isinstance(h, dict):
            headers_dict[h.get("name", "")] = h.get("value", "")

    from_header = headers_dict.get("From") or headers_dict.get("from") or ""
    policy_tier, assignment_rule = tier_assigner.assign(from_header, headers_dict)

    # --- Step 4: Emit counters ---
    tier_name = INGESTION_TIER_NAME.get(ingestion_tier, str(ingestion_tier))
    _gmail_tier_counter.labels(
        endpoint_identity=endpoint_identity,
        ingestion_tier=tier_name,
        reason=triage_action,
    ).inc()

    _gmail_policy_tier_counter.labels(
        endpoint_identity=endpoint_identity,
        policy_tier=policy_tier,
        assignment_rule=assignment_rule,
    ).inc()

    logger.debug(
        "Message policy: ingestion_tier=%d policy_tier=%s rule=%s triage=%s",
        ingestion_tier,
        policy_tier,
        assignment_rule,
        triage_action,
    )

    return MessagePolicyResult(
        should_ingest=ingestion_tier != INGESTION_TIER_SKIP,
        ingestion_tier=ingestion_tier,
        policy_tier=policy_tier,
        assignment_rule=assignment_rule,
        filter_reason=filter_reason,
        triage_action=triage_action,
    )


def _evaluate_triage_rules(
    message_data: dict[str, Any],
    rules: list[dict[str, Any]],
) -> str:
    """Evaluate connector-side triage rules for a Gmail message.

    Evaluates rules in order (priority ASC). First match wins.
    Returns the action string of the matched rule, or "pass_through" if no match.

    This is a simplified connector-side evaluation of the same rule format
    used by the Switchboard triage evaluator (roster/switchboard/tools/triage/evaluator.py).
    Supports: sender_domain, sender_address, header_condition, label_match.

    The full Switchboard triage evaluator runs post-ingest for Tier 1 messages;
    this connector-side evaluation is pre-ingest to gate Tier 2/3 decisions.
    """
    # Extract envelope fields for rule matching
    headers_raw = message_data.get("payload", {}).get("headers", [])
    headers: dict[str, str] = {}
    for h in headers_raw:
        if isinstance(h, dict):
            headers[h.get("name", "")] = h.get("value", "")

    from_address = _normalize_email(headers.get("From") or headers.get("from") or "")
    message_labels_upper = {label.strip().upper() for label in (message_data.get("labelIds") or [])}

    for rule in rules:
        try:
            matched = _match_connector_rule(
                rule=rule,
                from_address=from_address,
                headers=headers,
                message_labels=message_labels_upper,
            )
        except Exception:
            logger.exception(
                "Error evaluating triage rule id=%s type=%s; skipping",
                rule.get("id"),
                rule.get("rule_type"),
            )
            continue

        if matched:
            return str(rule.get("action", "pass_through"))

    return "pass_through"


def _match_connector_rule(
    rule: dict[str, Any],
    from_address: str,
    headers: dict[str, str],
    message_labels: set[str],
) -> bool:
    """Match a single triage rule against a Gmail message."""
    rule_type = str(rule.get("rule_type", ""))
    condition = rule.get("condition") or {}

    if rule_type == "sender_domain":
        domain_pattern = str(condition.get("domain", "")).strip().lower()
        match_type = str(condition.get("match", "exact")).strip().lower()
        if not domain_pattern or "@" not in from_address:
            return False
        sender_domain = from_address.split("@", 1)[1]
        if match_type == "exact":
            return sender_domain == domain_pattern
        if match_type == "suffix":
            return sender_domain == domain_pattern or sender_domain.endswith(f".{domain_pattern}")
        return False

    if rule_type == "sender_address":
        target = str(condition.get("address", "")).strip().lower()
        return bool(target) and from_address == target

    if rule_type == "header_condition":
        header_name = str(condition.get("header", "")).strip()
        op = str(condition.get("op", "")).strip().lower()
        value = condition.get("value")
        if not header_name or not op:
            return False
        header_name_lower = header_name.lower()
        matched_value: str | None = None
        for k, v in headers.items():
            if k.lower() == header_name_lower:
                matched_value = v
                break
        if op == "present":
            return matched_value is not None
        if matched_value is None:
            return False
        if op == "equals":
            return matched_value.strip() == str(value).strip() if value is not None else False
        if op == "contains":
            return str(value) in matched_value if value is not None else False
        return False

    if rule_type == "label_match":
        # Gmail-specific rule type: match against message label IDs
        label_pattern = str(condition.get("label", "")).strip().upper()
        return bool(label_pattern) and label_pattern in message_labels

    logger.warning("Unknown connector rule_type: %r", rule_type)
    return False


# ---------------------------------------------------------------------------
# Config parsing helpers
# ---------------------------------------------------------------------------


def parse_label_list(raw: str | None) -> list[str]:
    """Parse a comma-separated label list from environment variable."""
    if not raw:
        return []
    return [label.strip() for label in raw.split(",") if label.strip()]


# ---------------------------------------------------------------------------
# Known contacts cache loader
# ---------------------------------------------------------------------------


def load_known_contacts_from_file(path: str) -> frozenset[str]:
    """Load known contact email addresses from a JSON file.

    Expected format:
        {"contacts": ["alice@example.com", "bob@example.com"], "generated_at": "..."}
    or a plain list of strings.

    Returns frozenset of normalized email addresses.
    """
    import json
    import pathlib

    p = pathlib.Path(path)
    if not p.exists():
        logger.warning("Known contacts file not found: %s; treating as empty", path)
        return frozenset()

    try:
        data = json.loads(p.read_text())
        if isinstance(data, dict):
            contacts = data.get("contacts") or []
        elif isinstance(data, list):
            contacts = data
        else:
            logger.warning("Unexpected known contacts file format at %s; treating as empty", path)
            return frozenset()
        return frozenset(_normalize_email(str(c)) for c in contacts if c)
    except Exception as exc:
        logger.warning("Failed to load known contacts from %s: %s", path, exc)
        return frozenset()


__all__ = [
    # Label filtering
    "LabelFilterPolicy",
    "parse_label_list",
    # Policy tier assignment
    "PolicyTierAssigner",
    "POLICY_TIER_HIGH_PRIORITY",
    "POLICY_TIER_INTERACTIVE",
    "POLICY_TIER_DEFAULT",
    "RULE_KNOWN_CONTACT",
    "RULE_REPLY_TO_OUTBOUND",
    "RULE_DIRECT_CORRESPONDENCE",
    "RULE_FALLBACK_DEFAULT",
    # Ingestion tier
    "INGESTION_TIER_FULL",
    "INGESTION_TIER_METADATA",
    "INGESTION_TIER_SKIP",
    "classify_ingestion_tier",
    # Full evaluation
    "evaluate_message_policy",
    "MessagePolicyResult",
    # Helpers
    "load_known_contacts_from_file",
    "_normalize_email",
]
