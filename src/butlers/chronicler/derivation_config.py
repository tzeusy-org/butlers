"""Canonical, bounded serialization for private derivation configuration snapshots.

Callers must select the configuration fields they need before calling this module;
it must never be handed an environment or an unfiltered settings object.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

SNAPSHOT_KINDS = frozenset(
    {"anchors", "ssid_map", "place_refs", "routines", "timezone", "adapter_settings"}
)
MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_SNAPSHOT_DEPTH = 20


def _validate(value: Any, depth: int = 0) -> None:
    if depth > MAX_SNAPSHOT_DEPTH:
        raise ValueError("configuration snapshot exceeds maximum depth")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _validate(item, depth + 1)
        return
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("configuration snapshot keys must be strings")
        for item in value.values():
            _validate(item, depth + 1)
        return
    raise ValueError("configuration snapshot contains a non-JSON value")


def serialize_snapshot(kind: str, body: dict[str, Any]) -> tuple[str, str]:
    """Return a versioned content digest and canonical JSON body.

    The kind is included in the digest because the snapshot table has a single
    digest primary key shared by all kinds.  Values are not included in errors.
    """
    if kind not in SNAPSHOT_KINDS:
        raise ValueError("unknown configuration snapshot kind")
    if type(body) is not dict:
        raise ValueError("configuration snapshot body must be an object")
    _validate(body)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    try:
        body_bytes = encoded.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("configuration snapshot contains invalid Unicode") from None
    if len(body_bytes) > MAX_SNAPSHOT_BYTES:
        raise ValueError("configuration snapshot exceeds maximum size")
    digest = hashlib.sha256(b"chronicler-config-v1\0" + kind.encode() + b"\0" + body_bytes)
    return "v1:" + digest.hexdigest(), encoded
