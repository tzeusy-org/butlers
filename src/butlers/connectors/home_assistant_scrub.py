"""Home Assistant content adapter for the existing shared bearer scrubber.

No HA-specific detector or aggressive numeric-code mode lives here. Source,
event, sender and control identity stay outside content scrubbing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from butlers import ingestion_bearer_scrub as bearer_scrub
from butlers.connectors.home_assistant_envelope import build_state_changed_normalized_text

# Copy syntax, not bearer recognition: a value is a whitespace-delimited atom.
# Quotes/opening brackets may precede it; quotes/closing brackets and sentence
# punctuation may follow it. Other leading punctuation belongs to the value
# (.482913, ,482913 and :482913 are not copies of 482913), as do internal
# punctuation, signs and unit symbols. Never normalize or rewrite punctuation.
_COPY_ATOM = re.compile(r"\S+")
_COPY_OPENERS = "\"'([{“‘"
_COPY_TRAILERS = "\"')]}.,;:!?”’"


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


def _content_chunks(text: str) -> list[tuple[str, bool]]:
    """Partition public placeholders in order, searching each suffix only once.

    This is placeholder parsing, not a second secret detector. Keeping protected
    placeholders intact also prevents a code echo from rewriting their metadata.
    """
    chunks = []
    cursor = 0
    for hit in bearer_scrub.placeholder_artifacts(text):
        hold = bearer_scrub.placeholder(hit.kind, hit.provider_domain)
        index = text.find(hold, cursor)
        if index < 0:
            raise HABearerScrubError("Home Assistant content scrub failed")
        chunks.append((text[cursor:index], False))
        chunks.append((hold, True))
        cursor = index + len(hold)
    chunks.append((text[cursor:], False))
    return chunks


def _placeholder_tokens(text: str) -> list[str]:
    """Represent unchanged placeholders atomically in a linear token stream."""
    tokens = []
    for chunk, protected in _content_chunks(text):
        if protected:
            tokens.append(chunk)
        else:
            tokens.extend(chunk)
    return tokens


def _context_redactions(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Project one contiguous detector replacement without fuzzy alignment.

    Per-value scrubbing has already handled independently recognized content.
    Only the newly assembled label/state context needs projection here. Common
    prefix/suffix scans visit each token at most twice, even for repetitive or
    adversarial input. Separated/ambiguous edits refuse rather than searching
    possible alignments, guessing, or falling back to raw content.
    """
    safe = scrub_ha_normalized_text(text)
    if safe == text:
        return safe, []
    before, after = _placeholder_tokens(text), _placeholder_tokens(safe)
    start = 0
    while start < min(len(before), len(after)) and before[start] == after[start]:
        start += 1
    old_end, new_end = len(before), len(after)
    while old_end > start and new_end > start and before[old_end - 1] == after[new_end - 1]:
        old_end -= 1
        new_end -= 1
    original = "".join(before[start:old_end])
    replacement = "".join(after[start:new_end])
    artifacts = bearer_scrub.placeholder_artifacts(replacement)
    if (
        not original
        or not artifacts
        or replacement
        != "".join(bearer_scrub.placeholder(hit.kind, hit.provider_domain) for hit in artifacts)
    ):
        raise HABearerScrubError("Home Assistant content scrub failed")
    return safe, [(original, replacement)]


def _replace_copy(text: str, original: str, replacement: str) -> str:
    """Project only complete lexical copies of the shared detector's selection.

    Establish all atom edges in one pass. A formatted selection may span atoms,
    but both ends must coincide with complete values, including quoted/prose
    echoes. Numerical substrings inside a different atom are never copies.
    This does not select any new bearer: ``original`` comes only from the
    unchanged shared detector, and the context must re-render exactly afterward.
    """
    starts, ends = set(), set()
    for atom in _COPY_ATOM.finditer(text):
        start, end = atom.span()
        while start < end and text[start] in _COPY_OPENERS:
            start += 1
        while end > start and text[end - 1] in _COPY_TRAILERS:
            end -= 1
        starts.add(start)
        ends.add(end)
    pieces = []
    cursor = 0
    while (index := text.find(original, cursor)) >= 0:
        end = index + len(original)
        complete = index in starts and end in ends
        pieces.extend((text[cursor:index], replacement if complete else original))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _replace_values(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        chunks = _content_chunks(value)
        for original, replacement in replacements:
            chunks = [
                (chunk if protected else _replace_copy(chunk, original, replacement), protected)
                for chunk, protected in chunks
            ]
        return "".join(chunk for chunk, _ in chunks)
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
    current = (data.get(single_state) or {}) if single_state else new
    attributes = current.get("attributes") or {}
    friendly_name = data.get("friendly_name") or attributes.get("friendly_name")
    if single_state:
        friendly_name = attributes.get("friendly_name") or data.get("friendly_name")
    return build_state_changed_normalized_text(
        entity_id=data.get("entity_id") or "home_assistant",
        friendly_name=friendly_name,
        old_state=None if single_state else old.get("state"),
        new_state=current.get("state"),
        unit_of_measurement=attributes.get("unit_of_measurement"),
    )


def _scrub_transition(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    if isinstance(result.get("friendly_name"), str):
        result["friendly_name"] = scrub_ha_normalized_text(result["friendly_name"])
    for key in ("old_state", "new_state"):
        if key in result:
            result[key] = _scrub_state_values(result[key])
    # Each state owns its label/units, even if the other state is renamed or
    # absent. Also evaluate the actual transition rendering. Separate state
    # probes protect a second code consumed by the first shared-detector match.
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
