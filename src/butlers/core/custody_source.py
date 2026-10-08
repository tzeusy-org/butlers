"""Closed custody wire values and canonical binding integrity.

These values never confer authority. Only the registered guard may install a
verified call, and the owning transaction must check it again before committing.
The accepted protocol is documented in ``docs/identity_and_secrets/endpoint-custody.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

WIRE_LIMIT = 8192
TARGET_LIMIT = 64
JSON_DEPTH_LIMIT = 64
_KEY = re.compile(r"[a-z][a-z0-9_]*\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class CustodyError(Exception):
    """A content-free refusal suitable for a public transport response."""

    def __init__(self, code: str = "unavailable") -> None:
        self.code = (
            code
            if code in {"unavailable", "invalid", "refused", "expired", "conflict", "unknown"}
            else "unavailable"
        )
        super().__init__(self.code)


def canonical_json(value: Any, *, maximum: int = WIRE_LIMIT) -> bytes:
    """Encode custody canonical JSON v1, without numeric coercion or normalization."""

    def validate(item: Any, depth: int = 0) -> None:
        if depth > JSON_DEPTH_LIMIT:
            raise CustodyError("invalid")
        if item is None or type(item) in (bool, int):
            return
        if isinstance(item, str):
            if any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise CustodyError("invalid")
            return
        if type(item) is list:
            for child in item:
                validate(child, depth + 1)
            return
        if type(item) is dict:
            for key, child in item.items():
                if not isinstance(key, str) or not _KEY.fullmatch(key):
                    raise CustodyError("invalid")
                validate(child, depth + 1)
            return
        raise CustodyError("invalid")

    validate(value)
    try:
        result = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, OverflowError, RecursionError):
        raise CustodyError("invalid") from None
    if len(result) > maximum:
        raise CustodyError("invalid")
    return result


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def validate_minted_binding(
    minted: dict,
    *,
    source_ref: uuid.UUID,
    source_digest: str,
    operation: dict,
    destination_actor: str,
    receipt: dict,
) -> dict:
    """Source-side integrity check, never a replacement for online admission.

    Expected source digest comes from the producer's own committed registration;
    the enrollment receipt is held by its constructor-owned live anchor. Neither
    value is supplied by a model invocation. The receiver still proves actual
    current incarnation, challenge and committing authority independently.
    """
    closed_object(minted, required={"call_ref", "operation_digest", "expires_at", "binding"})
    binding = closed_object(
        minted["binding"],
        required={
            "call_ref",
            "source_ref",
            "source_digest",
            "issuer_process",
            "destination_process",
            "destination_actor",
            "operation",
            "control_epoch",
            "restore_epoch",
            "expires_at",
        },
    )
    for field in (
        "call_ref",
        "source_ref",
        "issuer_process",
        "destination_process",
        "restore_epoch",
    ):
        canonical_uuid(binding[field])
    canonical_uuid(minted["call_ref"])
    sha256_hex(source_digest)
    sha256_hex(minted["operation_digest"])
    try:
        expiry = datetime.fromisoformat(binding["expires_at"].replace("Z", "+00:00"))
        returned_expiry = datetime.fromisoformat(minted["expires_at"].replace("Z", "+00:00"))
        if (
            utc_timestamp(expiry) != binding["expires_at"]
            or utc_timestamp(returned_expiry) != binding["expires_at"]
            or binding["source_ref"] != str(source_ref)
            or binding["source_digest"] != source_digest
            or binding["issuer_process"] != receipt["process_id"]
            or binding["destination_actor"] != destination_actor
            or canonical_json(binding["operation"]) != canonical_json(operation)
            or type(binding["control_epoch"]) is not int
            or binding["control_epoch"] != receipt["control_epoch"]
            or binding["restore_epoch"] != receipt["restore_epoch"]
            or binding["call_ref"] != minted["call_ref"]
            or digest(binding) != minted["operation_digest"]
        ):
            raise CustodyError("refused")
    except (KeyError, ValueError, TypeError, AttributeError):
        raise CustodyError("refused") from None
    return minted


def utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise CustodyError("invalid")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def closed_object(value: Any, *, required: set[str], optional: set[str] | None = None) -> dict:
    if type(value) is not dict or not required <= value.keys():
        raise CustodyError("invalid")
    if value.keys() - required - (optional or set()):
        raise CustodyError("invalid")
    canonical_json(value)
    return value


def parse_wire(raw: bytes) -> dict:
    """Reject duplicate keys before JSON decoding discards their distinction."""
    if len(raw) > WIRE_LIMIT:
        raise CustodyError("invalid")

    def pairs(items: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise CustodyError("invalid")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise CustodyError("invalid") from None
    result = closed_object(
        value,
        required={
            "version",
            "call_ref",
            "challenge_ref",
            "operation_digest",
            "method",
            "arguments",
        },
    )
    if result["version"] != "custody-wire.v1" or type(result["arguments"]) is not dict:
        raise CustodyError("invalid")
    canonical_uuid(result["call_ref"])
    canonical_uuid(result["challenge_ref"])
    sha256_hex(result["operation_digest"])
    if not isinstance(result["method"], str) or not re.fullmatch(
        r"[a-z][a-z0-9_.]{0,63}", result["method"], flags=re.ASCII
    ):
        raise CustodyError("invalid")
    return result


def canonical_uuid(value: Any) -> str:
    if type(value) is not str:
        raise CustodyError("invalid")
    try:
        normalized = str(uuid.UUID(value))
    except ValueError:
        raise CustodyError("invalid") from None
    if normalized != value:
        raise CustodyError("invalid")
    return normalized


def sha256_hex(value: Any) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise CustodyError("invalid")
    return value


def canonical_targets(value: Any) -> list[dict]:
    """Return targets in the same canonical ordering-key order as SQL locks.

    A source/selection has one server-derived owner, so the common owner UUID
    prefix does not affect its array order. Duplicate IDs or typed bindings
    are refused rather than silently discarded or converted to another target.
    """
    if type(value) is not list or len(value) > TARGET_LIMIT:
        raise CustodyError("invalid")
    targets = []
    ids: set[str] = set()
    keys: set[str] = set()
    for target in value:
        target = closed_object(
            target,
            required={
                "target_id",
                "target_kind",
                "binding_digest",
                "binding_version",
                "generation",
            },
        )
        canonical_uuid(target["target_id"])
        sha256_hex(target["binding_digest"])
        if (
            target["target_kind"] not in ("endpoint", "account")
            or type(target["binding_version"]) is not int
            or not 1 <= target["binding_version"] < 2**63
            or type(target["generation"]) is not int
            or not 0 <= target["generation"] < 2**63
        ):
            raise CustodyError("invalid")
        key = f"{target['target_kind']}:{target['binding_digest']}:{target['binding_version']}"
        if target["target_id"] in ids or key in keys:
            raise CustodyError("invalid")
        ids.add(target["target_id"])
        keys.add(key)
        targets.append((key, target))
    return [target for _, target in sorted(targets)]


@dataclass(frozen=True, repr=False)
class VerifiedCustodyCall:
    """Private per-invocation result, deliberately not a serializable request model."""

    call_ref: uuid.UUID
    challenge_ref: str
    operation_digest: str
    source_ref: uuid.UUID
    method: str
    arguments: dict
    acquisition_generation: int

    def __reduce__(self):
        raise TypeError("custody call context cannot be serialized")


_verified_call: ContextVar[VerifiedCustodyCall | None] = ContextVar(
    "custody_registered_call", default=None
)


def current_custody_call() -> VerifiedCustodyCall:
    call = _verified_call.get()
    if call is None:
        raise CustodyError("refused")
    return call
