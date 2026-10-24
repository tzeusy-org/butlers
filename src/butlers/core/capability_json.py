"""Raw-argument I-JSON validation and RFC 8785 hashing for capability assertions.

There is deliberately no alternate encoder. The broker stays unavailable until
the reviewed RFC 8785 dependency is installed by the owning lockfile change.
"""

from __future__ import annotations

import hashlib
import json
import math


class InvalidArguments(ValueError):
    """Arguments cannot participate in a portable, unambiguous digest."""


class CanonicalizationUnavailable(RuntimeError):
    """The required canonicalizer has not been delivered."""


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
        if abs(value) > 2**53 - 1:
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


def validate_arguments(value: object) -> dict:
    """Validate without coercing keys, defaults, tuples, numbers or Unicode."""
    if type(value) is not dict:
        raise InvalidArguments("arguments_must_be_object")
    try:
        _validate(value)
    except RecursionError:
        raise InvalidArguments("argument_nesting") from None
    return value


def parse_arguments(raw: bytes) -> dict:
    """Reject duplicate names before a JSON parser could silently discard one."""

    def pairs(items: list[tuple[str, object]]) -> dict:
        result = {}
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


def canonical_bytes(value: dict) -> bytes:
    """Canonicalize the *raw* argument object, before any handler/default coercion."""
    validate_arguments(value)
    try:
        import rfc8785
    except ImportError:
        raise CanonicalizationUnavailable("rfc8785_unavailable") from None
    return rfc8785.dumps(value)


def arguments_digest(value: dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
