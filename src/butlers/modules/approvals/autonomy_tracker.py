"""Autonomy tracker stub — provides compute_fingerprint for autonomy_suggestions.

This module will be fully implemented in a separate issue (bu-oc2r). This stub
provides compute_fingerprint so that autonomy_suggestions.py can be used and
tested independently.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_fingerprint(tool_name: str, tool_args: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 fingerprint for a tool invocation.

    The fingerprint is derived from the canonical JSON of ``(tool_name, tool_args)``
    with dictionary keys sorted alphabetically at every level.

    Parameters
    ----------
    tool_name:
        The name of the tool being invoked.
    tool_args:
        The arguments to the tool invocation.

    Returns
    -------
    str
        Lowercase hex SHA-256 digest.
    """
    payload = {"tool_name": tool_name, "tool_args": tool_args}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
