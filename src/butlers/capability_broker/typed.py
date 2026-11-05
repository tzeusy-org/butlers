"""The closed typed shape for quarantined extraction and child returns (M7).

A typed boundary is where third-party content may cross to a model only as a
value of a fixed, finite shape. This module is the one adopted shape, and it
is deliberately small: an object of named slots, each a boolean, a bounded
integer or number, a token from a bounded enum, a calendar date, a UTC
instant, an opaque handle of the same goal, or a bounded unique array of one of
those.

There is no free string. Nothing here can carry a URL, hostname, address,
recipient, device or resource selector, instruction text or a nested schema:
any keyword, type or format outside the set refuses the whole schema rather
than being ignored, and a value is checked exactly against it (no coercion,
no extra or missing members).

The shape constrains what reaches a model, never what a value may *do*. With
the typed-value semantic choice held on ``bu-4c7nvp.1.1``, a typed value keeps
the full label of the content it came from (``TYPED_VALUE_RELAXATION``), so it
cannot launder D6 exposure into an effect argument.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import rfc8785

from butlers.core.goal_labels import Label

#: The held M7 typed-value choice. ``None`` means no relaxation is adopted: a
#: typed value carries its source's whole label. Only an adopted owner decision
#: (bu-4c7nvp.1.1) and its exact text may replace this; nothing reads it as a
#: default to guess from.
TYPED_VALUE_RELAXATION: None = None

MAX_SLOTS = 16
MAX_ENUM = 32
MAX_ITEMS = 32
_SAFE = 2**53 - 1
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
# Enum tokens are short identifiers: no spaces, dots, slashes, colons or ``@``,
# so a token cannot spell a URL, hostname, address or sentence.
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_FORMATS = frozenset({"date", "date-time", "handle"})


class TypedRefused(ValueError):
    """A schema or value outside the adopted closed shape; content-blind."""


@dataclass(frozen=True, slots=True)
class TypedSchema:
    """A parsed schema of the adopted shape, with its canonical digest."""

    document: dict[str, Any]
    digest: str

    @property
    def slots(self) -> dict[str, dict[str, Any]]:
        return self.document["properties"]

    @property
    def has_handles(self) -> bool:
        return any(
            slot.get("items", slot).get("format") == "handle" for slot in self.slots.values()
        )

    def handle_refs(self, value: dict[str, Any]) -> frozenset[UUID]:
        """Every handle a validated value names (for same-goal checks)."""
        refs: set[UUID] = set()
        for name, slot in self.slots.items():
            if name not in value:
                continue
            item_slot = slot.get("items", slot)
            if item_slot.get("format") != "handle":
                continue
            items = value[name] if slot["type"] == "array" else [value[name]]
            refs.update(UUID(item) for item in items)
        return frozenset(refs)


def _exact(node: Any, keys: set[str], required: set[str]) -> dict[str, Any]:
    if type(node) is not dict or not required <= set(node) or not set(node) <= keys:
        raise TypedRefused("schema_outside_adopted_shape")
    return node


def _bound(value: Any) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > _SAFE:
        raise TypedRefused("schema_outside_adopted_shape")
    return value


def _scalar(node: Any) -> None:
    if type(node) is not dict:
        raise TypedRefused("schema_outside_adopted_shape")
    kind = node.get("type")
    if kind == "boolean":
        _exact(node, {"type"}, {"type"})
    elif kind in ("integer", "number"):
        _exact(node, {"type", "minimum", "maximum"}, {"type", "minimum", "maximum"})
        low, high = _bound(node["minimum"]), _bound(node["maximum"])
        if kind == "integer" and (type(low) is not int or type(high) is not int):
            raise TypedRefused("schema_outside_adopted_shape")
        if low > high:
            raise TypedRefused("schema_outside_adopted_shape")
    elif kind == "string" and "enum" in node:
        _exact(node, {"type", "enum"}, {"type", "enum"})
        tokens = node["enum"]
        if (
            type(tokens) is not list
            or not 1 <= len(tokens) <= MAX_ENUM
            or len(set(map(str, tokens))) != len(tokens)
            or any(type(t) is not str or not _TOKEN.fullmatch(t) for t in tokens)
        ):
            raise TypedRefused("schema_outside_adopted_shape")
    elif kind == "string":
        # A string slot exists only as one of the closed formats; a bare string,
        # a pattern or any other format (uri, hostname, email, uuid selector...)
        # is free text by another name.
        _exact(node, {"type", "format"}, {"type", "format"})
        if node["format"] not in _FORMATS:
            raise TypedRefused("schema_outside_adopted_shape")
    else:
        raise TypedRefused("schema_outside_adopted_shape")


def parse_schema(document: Any) -> TypedSchema:
    """Accept exactly the adopted shape; anything else refuses whole."""
    root = _exact(
        document,
        {"type", "properties", "required", "additionalProperties"},
        {"type", "properties", "required", "additionalProperties"},
    )
    if root["type"] != "object" or root["additionalProperties"] is not False:
        raise TypedRefused("schema_outside_adopted_shape")
    slots, required = root["properties"], root["required"]
    if type(slots) is not dict or not 1 <= len(slots) <= MAX_SLOTS:
        raise TypedRefused("schema_outside_adopted_shape")
    if (
        type(required) is not list
        or len(set(map(str, required))) != len(required)
        or any(type(name) is not str or name not in slots for name in required)
    ):
        raise TypedRefused("schema_outside_adopted_shape")
    for name, slot in slots.items():
        if type(name) is not str or not _NAME.fullmatch(name):
            raise TypedRefused("schema_outside_adopted_shape")
        if type(slot) is dict and slot.get("type") == "array":
            _exact(
                slot,
                {"type", "items", "maxItems", "uniqueItems"},
                {"type", "items", "maxItems", "uniqueItems"},
            )
            bound = slot["maxItems"]
            if type(bound) is not int or not 1 <= bound <= MAX_ITEMS:
                raise TypedRefused("schema_outside_adopted_shape")
            if slot["uniqueItems"] is not True:
                raise TypedRefused("schema_outside_adopted_shape")
            _scalar(slot["items"])
        else:
            _scalar(slot)
    try:
        canonical = rfc8785.dumps(document)
    except Exception:
        raise TypedRefused("schema_outside_adopted_shape") from None
    return TypedSchema(document, hashlib.sha256(canonical).hexdigest())


def _check_scalar(slot: dict[str, Any], value: Any) -> None:
    kind = slot["type"]
    if kind == "boolean":
        ok = type(value) is bool
    elif kind == "integer":
        ok = type(value) is int and slot["minimum"] <= value <= slot["maximum"]
    elif kind == "number":
        ok = (
            type(value) in (int, float)
            and math.isfinite(value)
            and slot["minimum"] <= value <= slot["maximum"]
        )
    elif "enum" in slot:
        ok = type(value) is str and value in slot["enum"]
    elif slot["format"] == "date":
        ok = type(value) is str and _DATE.fullmatch(value) is not None and _real_date(value)
    elif slot["format"] == "date-time":
        ok = type(value) is str and _INSTANT.fullmatch(value) is not None and _real_instant(value)
    else:  # handle: an opaque, canonical lowercase UUID
        ok = type(value) is str and _UUID.fullmatch(value) is not None
    if not ok:
        raise TypedRefused("value_outside_schema")


def _real_date(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _real_instant(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") == value


def typed_value_label(source: Label) -> Label:
    """The label a typed value carries: with no adopted relaxation, its source's whole label.

    This is the single place a typed-value relaxation could ever apply, and it
    applies none: ``TYPED_VALUE_RELAXATION`` is ``None`` until bu-4c7nvp.1.1.
    """
    return source


def validate_value(schema: TypedSchema, value: Any) -> dict[str, Any]:
    """Return ``value`` unchanged if it matches exactly; otherwise refuse."""
    if type(value) is not dict or not set(value) <= set(schema.slots):
        raise TypedRefused("value_outside_schema")
    if not set(schema.document["required"]) <= set(value):
        raise TypedRefused("value_outside_schema")
    for name, item in value.items():
        slot = schema.slots[name]
        if slot["type"] == "array":
            if type(item) is not list or len(item) > slot["maxItems"]:
                raise TypedRefused("value_outside_schema")
            for member in item:
                _check_scalar(slot["items"], member)
            if len({rfc8785.dumps(member) for member in item}) != len(item):
                raise TypedRefused("value_outside_schema")
        else:
            _check_scalar(slot, item)
    return value


__all__ = [
    "MAX_ENUM",
    "MAX_ITEMS",
    "MAX_SLOTS",
    "TYPED_VALUE_RELAXATION",
    "TypedRefused",
    "TypedSchema",
    "parse_schema",
    "typed_value_label",
    "validate_value",
]
