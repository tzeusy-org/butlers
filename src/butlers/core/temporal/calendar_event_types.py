"""Bounded read-side calendar declarations, shared by their consumers.

These are provider declarations, not inferred physical presence or writable
Google event payloads. Unknown event types remain ordinary calendar entries.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any


def normalize_event_type(value: Any) -> str:
    """Keep bounded future provider types without inventing a finite allowlist."""
    if not isinstance(value, str):
        return "default"
    value = value.strip()
    if not value or len(value) > 64 or any(unicodedata.category(c).startswith("C") for c in value):
        return "default"
    return value


def normalize_working_location(value: Any) -> dict[str, str] | None:
    """Minimize a canonical declaration to its discriminant and display label."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if not isinstance(value, dict):
        return None
    kind = value.get("type")
    if kind == "homeOffice":
        return {"type": "homeOffice"}
    if kind not in ("officeLocation", "customLocation"):
        return None
    label = value.get("label")
    if not isinstance(label, str):
        return None
    label = "".join(c for c in label if not unicodedata.category(c).startswith("C")).strip()
    if not label:
        return None
    return {"type": kind, "label": label[:256]}


def google_working_location(properties: Any) -> dict[str, str] | None:
    """Read only the nested label matching Google's declared discriminant."""
    if not isinstance(properties, dict):
        return None
    kind = properties.get("type")
    if kind == "homeOffice":
        return {"type": "homeOffice"}
    if kind not in ("officeLocation", "customLocation"):
        return None
    nested = properties.get(kind)
    if not isinstance(nested, dict):
        return None
    return normalize_working_location({"type": kind, "label": nested.get("label")})
