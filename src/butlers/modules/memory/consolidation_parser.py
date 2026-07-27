"""Parser for consolidation output from Claude Code sessions.

Extracts structured consolidation actions (new facts, updated facts, new rules,
confirmations) from CC text output that contains a JSON block.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valid permanence levels (must match storage.py _PERMANENCE_DECAY keys).
# ---------------------------------------------------------------------------
_VALID_PERMANENCE = {"permanent", "stable", "standard", "volatile", "ephemeral"}

# ---------------------------------------------------------------------------
# Data classes for parsed output
# ---------------------------------------------------------------------------


@dataclass
class NewFact:
    subject: str
    predicate: str
    content: str
    permanence: str = "standard"
    importance: float = 5.0
    tags: list[str] = field(default_factory=list)
    entity_id: str | None = None
    object_entity_id: str | None = None
    valid_at: datetime | None = None


@dataclass
class UpdatedFact:
    target_id: str  # UUID string of fact to supersede
    content: str
    permanence: str = "standard"


@dataclass
class NewRule:
    content: str
    tags: list[str] = field(default_factory=list)


@dataclass
class ConsolidationResult:
    new_facts: list[NewFact] = field(default_factory=list)
    updated_facts: list[UpdatedFact] = field(default_factory=list)
    new_rules: list[NewRule] = field(default_factory=list)
    confirmations: list[str] = field(default_factory=list)  # UUID strings
    parse_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------

# Match ```json ... ``` fenced blocks (with optional whitespace).
_CODE_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)

# Match a bare top-level JSON object.
_BARE_JSON_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


def _extract_json_text(text: str) -> str | None:
    """Extract the first JSON block from *text*.

    Tries fenced code blocks first, then falls back to the outermost ``{...}``
    pattern.
    """
    m = _CODE_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()

    # Fallback: find the outermost balanced braces.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _is_uuid(value: str) -> bool:
    """Return True if *value* looks like a valid UUID string."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def _clamp_importance(value: float | int) -> float:
    """Clamp importance to [1.0, 10.0]."""
    return max(1.0, min(10.0, float(value)))


def _validate_permanence(value: str) -> str:
    """Return a valid permanence string, defaulting to 'standard'."""
    if value in _VALID_PERMANENCE:
        return value
    logger.warning("Invalid permanence '%s', defaulting to 'standard'", value)
    return "standard"


def _parse_valid_at(
    raw: dict,
    item_name: str,
    errors: list[str],
) -> tuple[datetime | None, bool]:
    """Parse an optional ISO-8601 ``valid_at`` value.

    The boolean indicates whether parsing succeeded. Missing values are valid
    and remain ``None`` for property facts; malformed values reject the action
    so they cannot be silently downgraded from temporal to property semantics.
    """
    value = raw.get("valid_at")
    if value is None:
        return None, True
    if not isinstance(value, str):
        parsed = None
    else:
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            parsed = None
    if parsed is None:
        msg = f"Skipping {item_name}: invalid valid_at"
        logger.warning(msg)
        errors.append(msg)
        return None, False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed, True


# ---------------------------------------------------------------------------
# Item parsers
# ---------------------------------------------------------------------------


def _parse_new_fact(raw: dict, errors: list[str]) -> NewFact | None:
    """Parse a single new_fact entry. Returns None if required fields missing."""
    subject = raw.get("subject")
    predicate = raw.get("predicate")
    content = raw.get("content")

    missing = []
    if not subject:
        missing.append("subject")
    if not predicate:
        missing.append("predicate")
    if not content:
        missing.append("content")

    if missing:
        msg = f"Skipping new_fact: missing required fields {missing}"
        logger.warning(msg)
        errors.append(msg)
        return None

    permanence = _validate_permanence(raw.get("permanence", "standard"))
    importance = _clamp_importance(raw.get("importance", 5.0))
    tags = raw.get("tags", [])
    if not isinstance(tags, list):
        tags = []

    entity_id_raw = raw.get("entity_id")
    entity_id = (
        entity_id_raw if isinstance(entity_id_raw, str) and _is_uuid(entity_id_raw) else None
    )
    object_entity_id_raw = raw.get("object_entity_id")
    object_entity_id = None
    if object_entity_id_raw is not None:
        if not isinstance(object_entity_id_raw, str) or not _is_uuid(object_entity_id_raw):
            msg = "Skipping new_fact: invalid object_entity_id"
            logger.warning(msg)
            errors.append(msg)
            return None
        object_entity_id = object_entity_id_raw
    valid_at, valid_at_ok = _parse_valid_at(raw, "new_fact", errors)
    if not valid_at_ok:
        return None

    return NewFact(
        subject=subject,
        predicate=predicate,
        content=content,
        permanence=permanence,
        importance=importance,
        tags=tags,
        entity_id=entity_id,
        object_entity_id=object_entity_id,
        valid_at=valid_at,
    )


def _parse_updated_fact(raw: dict, errors: list[str]) -> UpdatedFact | None:
    """Parse a single updated_fact entry. Returns None if required fields missing."""
    if raw.get("valid_at") is not None:
        msg = "Skipping updated_fact: temporal observations must use new_facts"
        logger.warning(msg)
        errors.append(msg)
        return None

    target_id = raw.get("target_id")
    content = raw.get("content")

    missing = []
    if not target_id:
        missing.append("target_id")
    if not content:
        missing.append("content")

    if missing:
        msg = f"Skipping updated_fact: missing required fields {missing}"
        logger.warning(msg)
        errors.append(msg)
        return None

    if not _is_uuid(target_id):
        msg = f"Skipping updated_fact: invalid UUID target_id '{target_id}'"
        logger.warning(msg)
        errors.append(msg)
        return None

    permanence = _validate_permanence(raw.get("permanence", "standard"))

    return UpdatedFact(
        target_id=target_id,
        content=content,
        permanence=permanence,
    )


def _parse_new_rule(raw: dict, errors: list[str]) -> NewRule | None:
    """Parse a single new_rule entry. Returns None if required fields missing."""
    content = raw.get("content")
    if not content:
        msg = "Skipping new_rule: missing required field 'content'"
        logger.warning(msg)
        errors.append(msg)
        return None

    tags = raw.get("tags", [])
    if not isinstance(tags, list):
        tags = []

    return NewRule(content=content, tags=tags)


def _parse_confirmation(raw: str, errors: list[str]) -> str | None:
    """Parse a single confirmation UUID. Returns None if invalid."""
    if not isinstance(raw, str) or not _is_uuid(raw):
        msg = f"Skipping confirmation: invalid UUID '{raw}'"
        logger.warning(msg)
        errors.append(msg)
        return None
    return raw


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_consolidation_output(text: str) -> ConsolidationResult:
    """Parse CC consolidation output into structured actions.

    Extracts a JSON block from *text* (fenced or bare) and converts it to a
    :class:`ConsolidationResult`.  Malformed or missing data is reported via
    ``parse_errors`` rather than raising exceptions.
    """
    result = ConsolidationResult()

    json_text = _extract_json_text(text)
    if json_text is None:
        result.parse_errors.append("No JSON block found in consolidation output")
        return result

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        result.parse_errors.append(f"Invalid JSON: {exc}")
        return result

    if not isinstance(data, dict):
        result.parse_errors.append("Expected top-level JSON object, got " + type(data).__name__)
        return result

    # --- new_facts ---
    for raw in data.get("new_facts", []):
        if not isinstance(raw, dict):
            msg = f"Skipping new_fact: expected dict, got {type(raw).__name__}"
            result.parse_errors.append(msg)
            continue
        fact = _parse_new_fact(raw, result.parse_errors)
        if fact is not None:
            result.new_facts.append(fact)

    # --- updated_facts ---
    for raw in data.get("updated_facts", []):
        if not isinstance(raw, dict):
            result.parse_errors.append(
                f"Skipping updated_fact: expected dict, got {type(raw).__name__}"
            )
            continue
        fact = _parse_updated_fact(raw, result.parse_errors)
        if fact is not None:
            result.updated_facts.append(fact)

    # --- new_rules ---
    for raw in data.get("new_rules", []):
        if not isinstance(raw, dict):
            result.parse_errors.append(
                f"Skipping new_rule: expected dict, got {type(raw).__name__}"
            )
            continue
        rule = _parse_new_rule(raw, result.parse_errors)
        if rule is not None:
            result.new_rules.append(rule)

    # --- confirmations ---
    for raw in data.get("confirmations", []):
        confirmation = _parse_confirmation(raw, result.parse_errors)
        if confirmation is not None:
            result.confirmations.append(confirmation)

    return result
