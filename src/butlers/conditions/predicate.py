"""The predicate language (RFC 0038 S4, S4.M7): a closed, bounded tagged AST.

The first-switch leaves are ``decision``, ``owner_input``, ``effect_settled``, ``at`` and
``transition``. M7 (bu-4c7nvp.1.5, REQ-owner-watches-001) adds ``expectation``,
``routine_deviation``, ``condition``, ``mode``, ``read_check`` and ``bundle`` to the same AST;
depth, leaf and size bounds and three-valued evaluation are unchanged.

Validation mirrors ``perception_private.valid_predicate`` in ``scripts/init-db.sql`` so a
predicate accepted here is accepted by the dark ``perception_bind`` store and vice versa.
No text expression, code, SQL, or model evaluation exists; unknown tags, fields or values
refuse before any registration side effect.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.perception.model import KINDS, MODES, STATES, canonical, instant

MAX_CHILDREN = 16
MAX_LEAVES = 16
MAX_DEPTH = 2
MAX_BYTES = 8192

DECISION_OUTCOMES = frozenset({"approved", "rejected", "expired", "cancelled"})
EFFECT_STATUSES = frozenset({"succeeded", "failed", "cancelled", "unknown"})
DEVIATIONS = frozenset({"pending", "within", "missed"})
CONDITION_STATES = frozenset({"opened", "resolved"})
MODE_CHANGES = frozenset({"entered", "exited"})
READ_OPS = frozenset({"eq", "ne", "lt", "le", "gt", "ge"})
ORDERED_OPS = frozenset({"lt", "le", "gt", "ge"})
#: The registered ``expect.*`` perception kinds an ``expectation`` leaf may name.
EXPECTATION_KINDS = frozenset(kind for kind in KINDS if kind.startswith("expect."))
#: A read_check number is a finite double within this magnitude (0 or at least ``READ_MIN``): wider
#: values would make the database's expanded numeric text, and so its size bound, diverge.
MAX_NUMBER_TEXT = 40  # a numeric literal's own text, as the database bounds it
READ_MAX = 10**15
READ_MIN = Decimal("1e-6")
#: One canonical RFC 3339 UTC instant form for ``at`` (no compact offset, no leap second).
INSTANT_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?"
    r"(Z|\+00:00)"
)
MAX_NAME_BYTES = 128
MAX_FIELD_BYTES = 64
#: A registered name (condition source, read capability, field, bundle): bounded lowercase ASCII.
NAME_PATTERN = re.compile(r"[a-z0-9_.:-]{1,128}")
FIELD_PATTERN = re.compile(r"[a-z0-9_.-]{1,64}")

SOURCE_DECISION = "goal.decision"
SOURCE_INBOX = "goal.inbox"
SOURCE_EFFECT = "goal.effect"
SOURCE_CLOCK = "clock"
SOURCE_EXPECT = "perception:expect.window"
SOURCE_MODE = "perception:owner.mode"
#: M7 leaves. Each is an occurrence of a typed row, so a stale or late one is never a wake.
M7_TAGS = frozenset(
    {"expectation", "routine_deviation", "condition", "mode", "read_check", "bundle"}
)


class PredicateError(ValueError):
    """A predicate is outside the closed S4 language."""


@dataclass(frozen=True)
class Leaf:
    path: str
    tag: str
    body: dict

    @property
    def source(self) -> str:
        if self.tag == "decision":
            return SOURCE_DECISION
        if self.tag == "owner_input":
            return SOURCE_INBOX
        if self.tag == "effect_settled":
            return SOURCE_EFFECT
        if self.tag == "at":
            return SOURCE_CLOCK
        if self.tag in ("expectation", "routine_deviation"):
            return SOURCE_EXPECT
        if self.tag == "mode":
            return SOURCE_MODE
        if self.tag == "condition":
            return f"condition:{self.body['source']}"
        if self.tag == "read_check":
            return f"read_check:{self.body['capability']}"
        if self.tag == "bundle":
            return f"bundle:{self.body['bundle_id']}"
        return f"perception:{self.body['kind']}"


def _uuid(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise PredicateError(f"{what} must be a canonical UUID string")
    try:
        ok = str(UUID(value)) == value
    except ValueError:
        ok = False
    if not ok:
        raise PredicateError(f"{what} must be a canonical UUID string")
    return value


def _member(value: object, allowed: frozenset[str]) -> bool:
    """Membership that is false, never a TypeError, for an unhashable or non-string value."""
    return type(value) is str and value in allowed


def _closed(node: dict, names: set[str]) -> None:
    if set(node) != names:
        raise PredicateError(f"{node.get('tag')} requires exactly fields {sorted(names)}")


def _name(value: object, what: str, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or not pattern.fullmatch(value):
        raise PredicateError(f"{what} must be a bounded lowercase name")
    return value


def _decimal(value: int | float | Decimal) -> Decimal:
    """The exact decimal a JSON number denotes (a float by its shortest repr, as json writes it)."""
    if type(value) is Decimal:
        return value
    if type(value) is float:
        return Decimal(repr(value))
    return Decimal(value)


def _pg_width(d: Decimal) -> int:
    """Characters in jsonb's plain text for a finite decimal, from its digits and exponent alone.

    Computed arithmetically so a value such as ``1e999999999`` is judged without ever being
    expanded into a string.
    """
    sign, digits, exponent = d.as_tuple()
    count = len(digits)
    if d.is_zero():
        return 1 if exponent >= 0 else 2 - exponent  # type: ignore[operator]
    negative = 1 if sign else 0
    if exponent >= 0:  # type: ignore[operator]
        return negative + count + exponent  # type: ignore[operator]
    if -exponent >= count:  # type: ignore[operator]
        return negative + 2 - exponent  # type: ignore[operator]
    return negative + count + 1


def _pg_text(value: int | float | Decimal) -> str:
    """The text jsonb prints for this number: plain decimal, scale kept, no sign on zero."""
    d = _decimal(value)
    if d.is_finite() and _pg_width(d) > MAX_NUMBER_TEXT:
        raise PredicateError(f"numeric value exceeds {MAX_NUMBER_TEXT} characters")
    if d.is_zero():
        exponent = d.as_tuple().exponent
        return "0" if exponent >= 0 else "0." + "0" * -exponent  # type: ignore[operator]
    return format(d, "f")


def _number(value: int | float | Decimal) -> bool:
    d = _decimal(value)
    if not d.is_finite() or _pg_width(d) > MAX_NUMBER_TEXT:
        return False
    magnitude = d.copy_abs()
    return d.is_zero() or READ_MIN <= magnitude <= Decimal(READ_MAX)


_NUMBERS = (int, float, Decimal)


def _read_value(node: dict) -> None:
    value = node["value"]
    if node["op"] in ORDERED_OPS:
        # An ordered comparison reads a number; a bool is not one.
        if type(value) not in _NUMBERS or not _number(value):
            raise PredicateError("read_check ordered comparisons need a bounded finite number")
    elif type(value) is str:
        if len(value.encode()) > MAX_NAME_BYTES:
            raise PredicateError("read_check.value string exceeds 128 bytes")
    elif type(value) is not bool and (type(value) not in _NUMBERS or not _number(value)):
        raise PredicateError("read_check.value must be a bounded number, boolean or string")


def _validate_m7(tag: str, node: dict) -> None:
    if tag == "expectation":
        _closed(node, {"tag", "expectation_kind", "subject", "deviation"})
        if type(node["expectation_kind"]) is not str or (
            node["expectation_kind"] not in EXPECTATION_KINDS
        ):
            raise PredicateError("expectation_kind must be a registered expect.* kind")
        _uuid(node["subject"], "expectation.subject")
        if not _member(node["deviation"], DEVIATIONS):
            raise PredicateError("unknown expectation deviation")
    elif tag == "routine_deviation":
        _closed(node, {"tag", "routine_id"})
        _uuid(node["routine_id"], "routine_deviation.routine_id")
    elif tag == "condition":
        _closed(node, {"tag", "source", "state"})
        _name(node["source"], "condition.source", NAME_PATTERN)
        if not _member(node["state"], CONDITION_STATES):
            raise PredicateError("unknown condition state")
    elif tag == "mode":
        _closed(node, {"tag", "mode", "change"})
        if not _member(node["mode"], MODES):
            raise PredicateError("unknown mode")
        if not _member(node["change"], MODE_CHANGES):
            raise PredicateError("unknown mode change")
    elif tag == "read_check":
        _closed(node, {"tag", "capability", "field", "op", "value"})
        _name(node["capability"], "read_check.capability", NAME_PATTERN)
        _name(node["field"], "read_check.field", FIELD_PATTERN)
        if not _member(node["op"], READ_OPS):
            raise PredicateError("unknown read_check comparison")
        _read_value(node)
    else:
        _closed(node, {"tag", "bundle_id"})
        _name(node["bundle_id"], "bundle.bundle_id", NAME_PATTERN)


def _validate(node: object, depth: int) -> int:
    """Validate one node; return its leaf count."""
    if depth > MAX_DEPTH:
        raise PredicateError("predicate nests deeper than two levels")
    if type(node) is not dict or type(node.get("tag")) is not str:
        raise PredicateError("predicate node must be an object with a string tag")
    tag = node["tag"]
    if tag in ("all", "any"):
        _closed(node, {"tag", "children"})
        children = node["children"]
        if type(children) is not list or not 1 <= len(children) <= MAX_CHILDREN:
            raise PredicateError("all/any need 1..16 children")
        return sum(_validate(child, depth + 1) for child in children)
    if tag == "decision":
        _closed(node, {"tag", "subject_id", "outcome"})
        _uuid(node["subject_id"], "decision.subject_id")
        if not _member(node["outcome"], DECISION_OUTCOMES):
            raise PredicateError("unknown decision outcome")
    elif tag == "owner_input":
        _closed(node, {"tag", "goal_id", "binding_ref"})
        _uuid(node["goal_id"], "owner_input.goal_id")
        _uuid(node["binding_ref"], "owner_input.binding_ref")
    elif tag == "effect_settled":
        _closed(node, {"tag", "effect_id", "status_set"})
        _uuid(node["effect_id"], "effect_settled.effect_id")
        statuses = node["status_set"]
        if (
            type(statuses) is not list
            or not 1 <= len(statuses) <= 4
            or len(set(map(str, statuses))) != len(statuses)
            or any(not _member(s, EFFECT_STATUSES) for s in statuses)
        ):
            raise PredicateError("effect_settled.status_set must be 1..4 known statuses")
    elif tag == "at":
        _closed(node, {"tag", "instant", "zone"})
        if type(node["instant"]) is not str or not INSTANT_PATTERN.fullmatch(node["instant"]):
            raise PredicateError("at.instant must be an RFC 3339 UTC instant (Z or +00:00)")
        try:
            instant(node["instant"])
        except ValueError as exc:
            raise PredicateError(str(exc)) from None
        try:
            ZoneInfo(node["zone"])
        except (ZoneInfoNotFoundError, TypeError, ValueError, OSError):
            raise PredicateError("at.zone must be an IANA zone") from None
    elif tag == "transition":
        _closed(node, {"tag", "kind", "subject", "from", "to"})
        if not _member(node["kind"], KINDS):
            raise PredicateError("unknown transition kind")
        if not _member(node["from"], STATES) or not _member(node["to"], STATES):
            raise PredicateError("unknown transition state")
        if node["subject"] is not None:
            _uuid(node["subject"], "transition.subject")
    elif tag in M7_TAGS:
        _validate_m7(tag, node)
    else:
        raise PredicateError(f"unknown predicate tag {tag!r}")
    return 1


def _plain(value: object) -> int:
    """json ``default`` hook: a Decimal enters the text as ``0``; ``_scan`` adds its real size."""
    if type(value) is Decimal and value.is_finite():
        return 0
    raise TypeError(f"{type(value).__name__} is not canonical JSON")


def _canonical_text(predicate: object) -> str:
    """Compact canonical JSON; refuses a cycle or a non-finite number (raises ``ValueError``)."""
    return json.dumps(
        predicate,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_plain,
    )


def _scan(predicate: object) -> int:
    """Walk every string and number once, without recursion (the text already proved no cycle).

    A NUL or a lone surrogate in a string or key is refused: jsonb cannot store either. The
    return value is the extra bytes jsonb's text form adds to the compact canonical JSON: it
    prints a numeric in plain decimal form (``1.5e-06`` is ``0.0000015``), keeps a literal's
    scale, and drops the sign of zero. The 8KiB bound is measured on that one form in both
    validators.
    """
    extra, stack = 0, [predicate]
    while stack:
        node = stack.pop()
        if type(node) is str:
            if "\x00" in node:
                raise PredicateError("predicate strings must not contain NUL")
            try:
                node.encode()
            except UnicodeEncodeError:
                raise PredicateError("predicate strings must be valid Unicode") from None
        elif type(node) is float:
            extra += len(_pg_text(node)) - len(repr(node))
        elif type(node) is Decimal:
            extra += len(_pg_text(node)) - 1  # the text carried a placeholder 0
        elif type(node) is dict:
            stack.extend(node.keys())
            stack.extend(node.values())
        elif type(node) is list:
            stack.extend(node)
    return extra


def _final(value: object) -> object:
    return 0.0 if value == 0 else float(value)  # type: ignore[arg-type]


def validate(predicate: object) -> dict:
    """Return the canonical copy of a valid predicate or raise ``PredicateError``."""
    try:
        text = _canonical_text(predicate)  # first: it refuses a cycle before anything walks it
        too_big = len(text.encode()) + _scan(predicate) > MAX_BYTES
    except PredicateError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise PredicateError(f"predicate is not canonical JSON: {exc}") from None
    if too_big:
        raise PredicateError("predicate exceeds 8KiB canonical JSON")
    try:
        leaf_count = _validate(predicate, 0)
    except PredicateError:
        raise
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError, OSError) as exc:
        # A validator raises its own error and nothing else, whatever the document holds.
        raise PredicateError(f"predicate is malformed: {type(exc).__name__}") from None
    if leaf_count > MAX_LEAVES:
        raise PredicateError("predicate has more than 16 leaves")
    return json.loads(
        json.dumps(predicate, sort_keys=True, separators=(",", ":"), default=_final)
        if _has_decimal(predicate)
        else text
    )


def _has_decimal(node: object) -> bool:
    stack = [node]
    while stack:
        item = stack.pop()
        if type(item) is Decimal:
            return True
        if type(item) is dict:
            stack.extend(item.values())
        elif type(item) is list:
            stack.extend(item)
    return False


def _literal(text: str) -> Decimal:
    if len(text) > MAX_NUMBER_TEXT:
        raise PredicateError(f"numeric literal exceeds {MAX_NUMBER_TEXT} characters")
    return Decimal(text)


def _integer(text: str) -> int:
    if len(text) > MAX_NUMBER_TEXT:
        raise PredicateError(f"numeric literal exceeds {MAX_NUMBER_TEXT} characters")
    return int(text)


def loads(text: str) -> dict:
    """Parse and validate JSON text, reading each number exactly as the database does.

    A fractional or exponent literal is a ``Decimal``, never a float, so ``9e-1000`` is the
    non-zero value it is and a zero in any spelling is zero.
    """
    try:
        document = json.loads(text, parse_float=_literal, parse_int=_integer)
    except PredicateError:
        raise
    except (ValueError, RecursionError, ArithmeticError) as exc:
        raise PredicateError(f"predicate is not JSON: {exc}") from None
    return validate(document)


def digest(predicate: dict) -> str:
    return hashlib.sha256(canonical(predicate).encode()).hexdigest()


def leaves(predicate: dict, prefix: str = "") -> Iterator[Leaf]:
    if predicate["tag"] in ("all", "any"):
        for i, child in enumerate(predicate["children"]):
            yield from leaves(child, f"{prefix}{i}.")
        return
    yield Leaf(prefix.rstrip(".") or "0", predicate["tag"], predicate)


def sources(predicate: dict) -> frozenset[str]:
    return frozenset(leaf.source for leaf in leaves(predicate))
