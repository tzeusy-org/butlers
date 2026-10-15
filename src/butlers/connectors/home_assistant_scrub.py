"""Home Assistant content adapter for the existing shared bearer scrubber.

No HA-specific detector or aggressive numeric-code mode lives here. Source,
event, sender and control identity stay outside content scrubbing.
"""

from __future__ import annotations

from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any

from butlers import ingestion_bearer_scrub as bearer_scrub
from butlers.connectors.home_assistant_envelope import build_state_changed_normalized_text


class HABearerScrubError(RuntimeError):
    """Content-free refusal safe for the existing replay error-detail seam."""


def _scrub_state_values(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    try:
        result = dict(state)
        for key in ("state", "attributes"):
            if key in state:
                result[key], _ = bearer_scrub.scrub_json(
                    state[key], provider_domain="home_assistant"
                )
        return result
    except Exception:
        raise HABearerScrubError("Home Assistant content scrub failed") from None


def scrub_ha_normalized_text(text: str) -> str:
    """Defend assembled text before discretion can retain it."""
    try:
        return bearer_scrub.scrub_text(text, provider_domain="home_assistant")[0]
    except Exception:
        raise HABearerScrubError("Home Assistant content scrub failed") from None


def _placeholder_tokens(text: str) -> list[str]:
    """Treat shared placeholders atomically when locating its redactions.

    Matching their internal punctuation against the source could otherwise
    split one detector replacement into misleading partial source fragments.
    This parses only public shared placeholders; it does not detect secrets.
    """
    placeholders = {
        bearer_scrub.placeholder(hit.kind, hit.provider_domain)
        for hit in bearer_scrub.placeholder_artifacts(text)
    }
    tokens = []
    index = 0
    while index < len(text):
        hold = next((p for p in placeholders if text.startswith(p, index)), None)
        tokens.append(hold if hold is not None else text[index])
        index += len(hold) if hold is not None else 1
    return tokens


def _context_redactions(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Locate only replacements already chosen by the existing detector."""
    safe = scrub_ha_normalized_text(text)
    if safe == text:
        return safe, []
    before, after = _placeholder_tokens(text), _placeholder_tokens(safe)
    replacements = []
    for kind, i, j, k, end in SequenceMatcher(None, before, after).get_opcodes():
        if kind == "equal":
            continue
        original = "".join(before[i:j])
        replacement = "".join(after[k:end])
        artifacts = bearer_scrub.placeholder_artifacts(replacement)
        if (
            not original
            or replacement
            != "".join(bearer_scrub.placeholder(hit.kind, hit.provider_domain) for hit in artifacts)
            or not artifacts
        ):
            raise HABearerScrubError("Home Assistant content scrub failed")
        replacements.append((original, replacement))
    return safe, replacements


def _replace_values(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for original, replacement in replacements:
            value = value.replace(original, replacement)
        return value
    if isinstance(value, Mapping):
        return {key: _replace_values(child, replacements) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_replace_values(child, replacements) for child in value]
    return value


def _replace_state_content(
    state: dict[str, Any] | None, replacements: list[tuple[str, str]]
) -> dict[str, Any] | None:
    if state is None:
        return None
    result = dict(state)
    for key in ("state", "attributes"):
        if key in result:
            result[key] = _replace_values(result[key], replacements)
    return result


def _render_context(data: dict[str, Any], single_state: str | None) -> str:
    old, new = data.get("old_state") or {}, data.get("new_state") or {}
    attributes = new.get("attributes") or {}
    current = (data.get(single_state) or {}) if single_state else new
    return build_state_changed_normalized_text(
        entity_id=data.get("entity_id") or "home_assistant",
        friendly_name=data.get("friendly_name") or attributes.get("friendly_name"),
        old_state=None if single_state else old.get("state"),
        new_state=current.get("state"),
        unit_of_measurement=attributes.get("unit_of_measurement"),
    )


def _scrub_transition(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    for key in ("old_state", "new_state"):
        if key in result:
            result[key] = _scrub_state_values(result[key])
    # The same label describes each state. Single-state renderings also protect
    # the second code when a first code consumes the shared detector's match.
    for single in ("old_state", "new_state", None):
        if single and not result.get(single):
            continue
        safe, replacements = _context_redactions(_render_context(result, single))
        if not replacements:
            continue
        candidate = dict(result)
        for key in ("old_state", "new_state"):
            if key in candidate:
                candidate[key] = _replace_state_content(candidate[key], replacements)
        if "friendly_name" in candidate:
            candidate["friendly_name"] = _replace_values(candidate["friendly_name"], replacements)
        nested = candidate.get("ha_event")
        if isinstance(nested, dict) and isinstance(nested.get("data"), dict):
            nested_data = dict(nested["data"])
            for key in ("old_state", "new_state"):
                if key in nested_data:
                    nested_data[key] = _replace_state_content(nested_data[key], replacements)
            candidate["ha_event"] = {**nested, "data": nested_data}
        # Never guess at a changed rendering or retain unprojected content.
        if _render_context(candidate, single) != safe:
            raise HABearerScrubError("Home Assistant content scrub failed")
        result = candidate
    return result


def scrub_ha_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Copy and scrub a direct state sink, including its friendly-name context."""
    if state is None:
        return None
    try:
        return _scrub_transition({"new_state": state})["new_state"]
    except Exception:
        raise HABearerScrubError("Home Assistant content scrub failed") from None


def scrub_ha_event(event: dict[str, Any]) -> dict[str, Any]:
    """Copy the event; preserve identity/context/time and scrub both states."""
    try:
        result = dict(event)
        data = dict(event.get("data") or {})
        if "data" in event:
            result["data"] = _scrub_transition(data)
        return result
    except Exception:
        raise HABearerScrubError("Home Assistant content scrub failed") from None


def _has_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return bool(bearer_scrub.placeholder_artifacts(value))
    if isinstance(value, Mapping):
        return any(_has_placeholder(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_placeholder(child) for child in value)
    return False


def scrub_ha_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Scrub all envelope content, including duplicates and legacy replay text."""
    try:
        prepared = dict(envelope)
        payload = dict(envelope.get("payload") or {})
        raw = payload.get("raw")
        if isinstance(raw, dict):
            raw = _scrub_transition(raw)
            if isinstance(raw.get("ha_event"), dict):
                raw["ha_event"] = scrub_ha_event(raw["ha_event"])
            payload["raw"] = raw
        prepared["payload"] = payload
        result, _ = bearer_scrub.scrub_envelope(prepared)
        # The dispatcher already scrubbed the state dictionaries. Preserve the
        # scrub marker when those protected placeholders reach an envelope.
        if _has_placeholder(result.get("payload")):
            result["control"] = {**(result.get("control") or {}), "bearer_scrubbed": True}
        return result
    except Exception:
        raise HABearerScrubError("Home Assistant content scrub failed") from None
