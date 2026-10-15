"""Home Assistant content adapter for the existing shared bearer scrubber.

No HA-specific detector or aggressive numeric-code mode lives here. Source,
event, sender and control identity stay outside content scrubbing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from butlers import ingestion_bearer_scrub as bearer_scrub


class HABearerScrubError(RuntimeError):
    """Content-free refusal safe for the existing replay error-detail seam."""


def scrub_ha_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Copy a state; scrub its state value and every nested attribute value."""
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


def scrub_ha_event(event: dict[str, Any]) -> dict[str, Any]:
    """Copy the event; preserve identity/context/time and scrub both states."""
    try:
        result = dict(event)
        data = dict(event.get("data") or {})
        for key in ("old_state", "new_state"):
            if key in data:
                data[key] = scrub_ha_state(data[key])
        if "data" in event:
            result["data"] = data
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
        result, _ = bearer_scrub.scrub_envelope(envelope)
        # The dispatcher already scrubbed the state dictionaries. Preserve the
        # scrub marker when those protected placeholders reach an envelope.
        if _has_placeholder(result.get("payload")):
            result["control"] = {**(result.get("control") or {}), "bearer_scrubbed": True}
        return result
    except Exception:
        raise HABearerScrubError("Home Assistant content scrub failed") from None
