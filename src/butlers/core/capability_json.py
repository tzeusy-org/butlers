"""RFC 8785 canonical argument digests shared by the broker and domain verifiers.

RFC 0038 D7 binds every forwarded call to ``args_digest``: the SHA-256 of the
RFC 8785 (JCS) canonical JSON of the *raw wire arguments*, before any handler
coercion or default filling. The broker signs it and the domain recomputes it,
so both sides must import this one module; there is deliberately no alternate
encoder. ``golden_vectors()`` returns the checked-in vectors both sides test
against, so signer and verifier cannot drift.

Arguments must be I-JSON (RFC 7493): duplicate member names, non-finite
numbers, integers outside the IEEE 754 safe range and lone surrogates are
refused instead of being normalised into a digest the other side cannot
reproduce.
"""

from __future__ import annotations

import hashlib
import json
import math
from functools import cache
from pathlib import Path
from typing import Any

import rfc8785

_VECTORS = Path(__file__).with_name("capability_digest_vectors.json")
_SAFE_INTEGER = 2**53 - 1


class InvalidArguments(ValueError):
    """Arguments cannot take part in a portable, unambiguous digest."""


def _validate(value: object) -> None:
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise InvalidArguments("invalid_unicode") from None
        return
    if type(value) is int:
        if abs(value) > _SAFE_INTEGER:
            raise InvalidArguments("integer_outside_safe_range")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise InvalidArguments("nonfinite_number")
        return
    if type(value) is list:
        for item in value:
            _validate(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise InvalidArguments("nonstring_key")
            _validate(key)
            _validate(item)
        return
    raise InvalidArguments("unsupported_json_type")


def validate_arguments(value: object) -> dict[str, Any]:
    """Validate an argument object without coercing anything in it."""
    if type(value) is not dict:
        raise InvalidArguments("arguments_must_be_object")
    try:
        _validate(value)
    except RecursionError:
        raise InvalidArguments("argument_nesting") from None
    return value


def parse_json_object(raw: bytes) -> dict[str, Any]:
    """Parse wire JSON, refusing duplicate names a parser would silently drop."""

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise InvalidArguments("duplicate_key")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise InvalidArguments("nonfinite_number")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise InvalidArguments("invalid_json") from None
    return validate_arguments(value)


def canonical_bytes(value: dict[str, Any]) -> bytes:
    """RFC 8785 canonical UTF-8 bytes of a validated argument object."""
    validate_arguments(value)
    return rfc8785.dumps(value)


def arguments_digest(value: dict[str, Any]) -> str:
    """Lowercase hex SHA-256 of the canonical bytes: the D7 ``args_digest``."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@cache
def golden_vectors() -> tuple[dict[str, Any], ...]:
    """Checked-in vectors: ``raw`` wire JSON, its ``canonical`` form and ``digest``.

    Entries with ``"refused"`` instead of a digest name the InvalidArguments
    reason both sides must raise for that wire input.
    """
    return tuple(json.loads(_VECTORS.read_text(encoding="utf-8"))["vectors"])
