"""Shared security helpers for API request validation."""

from __future__ import annotations

import re

_CREDENTIAL_RE = re.compile(
    r"(password|token|secret|api[_-]?key|credential|private[_-]?key)",
    re.IGNORECASE,
)


def validate_no_secrets(text: str) -> bool:
    """Return True if *text* contains no credential-like patterns, False otherwise.

    Matches case-insensitively against common credential keywords so that
    free-text reason fields cannot be used to smuggle secrets into audit logs.
    """
    return _CREDENTIAL_RE.search(text) is None
