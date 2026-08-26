"""Lightweight recognizers for provider transport identifiers.

This module deliberately depends only on the standard library so boundary
modules can reject identifier-shaped display names without importing connector,
memory-tool, or embedding graphs.
"""

from __future__ import annotations

import re

_WHATSAPP_TRANSPORT_IDENTIFIER_RE = re.compile(
    r"^(?:\d+(?::\d+)?@s\.whatsapp\.net|\d+(?::\d+)?@lid)$"
)


def is_whatsapp_transport_identifier(value: str) -> bool:
    """Return whether value is a numeric individual WhatsApp JID or LID."""
    return bool(_WHATSAPP_TRANSPORT_IDENTIFIER_RE.fullmatch(value.strip()))


__all__ = ["is_whatsapp_transport_identifier"]
