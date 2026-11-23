"""One predicate corpus for the Python and SQL validators (REQ-owner-watches-001).

Each entry is ``(name, document, valid)``. ``valid`` is the declared verdict, so the corpus is
checked against both validators and cannot drift into agreeing on the wrong answer.
"""

from __future__ import annotations

import json


class Raw(str):
    """A document given as JSON text, so a number keeps its source spelling.

    Python validates it with ``predicate.loads`` and the database receives the text unchanged.
    """


def text_of(document: object) -> str:
    return str(document) if isinstance(document, Raw) else json.dumps(document)


U1 = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
U2 = "22222222-2222-4222-8222-222222222222"

DECISION = {"tag": "decision", "subject_id": U1, "outcome": "approved"}
TRANSITION = {
    "tag": "transition",
    "kind": "owner.place",
    "subject": U1,
    "from": "known",
    "to": "unknown",
}
EXPECTATION = {
    "tag": "expectation",
    "expectation_kind": "expect.window",
    "subject": U1,
    "deviation": "missed",
}
READ = {
    "tag": "read_check",
    "capability": "finance.balance",
    "field": "available",
    "op": "lt",
    "value": 100,
}


def _all(*children: dict) -> dict:
    return {"tag": "all", "children": list(children)}


def _any(*children: dict) -> dict:
    return {"tag": "any", "children": list(children)}


def _noncanonical_forms(uuid: str) -> dict[str, str]:
    return {
        "uppercase": uuid.upper(),
        "braces": "{" + uuid + "}",
        "unhyphenated": uuid.replace("-", ""),
        "urn": "urn:uuid:" + uuid,
    }


def corpus() -> list[tuple[str, object, bool]]:
    cases: list[tuple[str, object, bool]] = [
        ("decision", DECISION, True),
        ("transition", TRANSITION, True),
        ("transition null subject", {**TRANSITION, "subject": None}, True),
        ("expectation", EXPECTATION, True),
        (
            "expectation unregistered kind",
            {**EXPECTATION, "expectation_kind": "routine.window"},
            False,
        ),
        (
            "expectation well-formed unknown expect kind",
            {**EXPECTATION, "expectation_kind": "expect.unregistered"},
            False,
        ),
        ("expectation kind not a string", {**EXPECTATION, "expectation_kind": 7}, False),
        ("read_check", READ, True),
        ("read_check string eq", {**READ, "op": "eq", "value": "ok"}, True),
        ("read_check bool ordered", {**READ, "value": True}, False),
        ("mode", {"tag": "mode", "mode": "asleep", "change": "entered"}, True),
        ("bundle", {"tag": "bundle", "bundle_id": "delay-commitment"}, True),
        ("extra field", {**DECISION, "extra": 1}, False),
        ("not an object", [DECISION], False),
        ("empty all", _all(), False),
        ("depth three", _all(_all(_all(DECISION))), False),
        ("depth two", _all(_any(DECISION)), True),
    ]
    for leaf_name, leaf, field in (
        ("decision", DECISION, "subject_id"),
        ("transition", TRANSITION, "subject"),
        ("expectation", EXPECTATION, "subject"),
        ("routine_deviation", {"tag": "routine_deviation", "routine_id": U1}, "routine_id"),
    ):
        cases.append((f"{leaf_name} canonical uuid", leaf, True))
        for form, value in _noncanonical_forms(U1).items():
            cases.append((f"{leaf_name} {form} uuid", {**leaf, field: value}, False))
    # Leaf totals count across the whole tree, not per node.
    for total, valid in ((16, True), (17, False), (18, False)):
        first = total // 2
        groups = [
            _any(*[DECISION] * first),
            _any(*[DECISION] * (total - first)),
        ]
        cases.append((f"{total} leaves via all of two any", _all(*groups), valid))
    cases.append(("16 children flat", _any(*[DECISION] * 16), True))
    cases.append(("17 children flat", _any(*[DECISION] * 17), False))
    cases.append(
        ("18 leaves via 2 x any of 9", _all(_any(*[DECISION] * 9), _any(*[DECISION] * 9)), False)
    )
    # Size. The largest valid document stays under 8KiB compact (jsonb text with its ", " and
    # ": " spacing is larger), so both validators must accept it.
    big_leaf = {**READ, "capability": "c" * 128, "field": "f" * 64, "op": "eq", "value": "v" * 128}
    cases.append(("largest valid document", _all(*[_any(big_leaf)] * 16), True))
    cases.append(("largest valid flat document", _any(*[big_leaf] * 16), True))
    cases.extend(_peer_cases())
    cases.extend(_numeric_cases())
    return cases


def _read_text(literal: str) -> Raw:
    leaf = {**READ, "value": "@@"}
    return Raw(json.dumps(leaf).replace('"@@"', literal))


def _numeric_cases() -> list[tuple[str, object, bool]]:
    """Exact-value number rules, written as source text because jsonb normalizes spelling."""
    cases = []
    for literal, valid in (
        ("0", True),
        ("-0", True),
        ("0.0", True),
        ("-0.0", True),
        ("0e+100", True),
        ("-0e0", True),
        ("0.000000", True),
        ("1e-6", True),
        ("1E-6", True),
        ("0.000001", True),
        ("0.0000009", False),
        ("1e15", True),
        ("1000000000000000", True),
        ("1000000000000001", False),
        ("9e-1000", False),
        ("1e1000", False),
        ("0e-100", False),
        ("1.23456789e-6", True),
        ("-1e15", True),
        ("-1e-7", False),
    ):
        cases.append((f"number literal {literal}", _read_text(literal), valid))
    cases.extend(_wide_number_elsewhere_cases())
    return cases


WIDE = "1.2345678901234567890123456789012345678901234567890"  # 51 characters, not an integer


def _wide_number_elsewhere_cases() -> list[tuple[str, object, bool]]:
    """A number wider than the 40-character bound outside ``read_check.value``.

    SQL bounds width only on the read value; everywhere else a number is simply the wrong type.
    Both validators must refuse each of these, and neither may raise anything else.
    """
    leaf_text = json.dumps({**READ, "value": 1}, sort_keys=True)
    texts = {
        "wide number as a capability": leaf_text.replace(f'"{READ["capability"]}"', WIDE),
        "wide number as a field": leaf_text.replace(f'"{READ["field"]}"', WIDE),
        "wide number as a tag": leaf_text.replace('"read_check"', WIDE),
        "wide number as a child": '{"tag":"all","children":[' + WIDE + "]}",
        "wide number inside a child leaf": '{"tag":"any","children":['
        + leaf_text.replace(f'"{READ["field"]}"', WIDE)
        + "]}",
        "wide exponent as a field": leaf_text.replace(f'"{READ["field"]}"', "1e999999999"),
    }
    return [(name, Raw(text), False) for name, text in texts.items()]


def sized_document(target: int, number: float = 1.23456789e-6, extra: int = 2) -> dict:
    """A valid document measuring exactly ``target`` bytes on the shared size measure.

    The measure is compact canonical JSON with each float in plain decimal form (what jsonb
    prints): ``1.23456789e-06`` is 14 bytes canonical but 16 in decimal, so 2 are added.
    Control characters cost six bytes each, which lets the search hit the byte exactly.
    """
    from butlers.perception.model import canonical

    leaf = {**READ, "capability": "c" * 128, "field": "f" * 64}
    numeric = {**leaf, "value": number}
    for controls in range(15 * 128):
        widths = [max(0, min(128, controls - 128 * index)) for index in range(15)]
        for trim in range(5):
            leaves = [
                {
                    **leaf,
                    "op": "eq",
                    "value": "\x01" * w + "a" * (128 - w - (trim if i == 14 else 0)),
                }
                for i, w in enumerate(widths)
            ]
            document = {"tag": "all", "children": [numeric, *leaves]}
            if len(canonical(document).encode()) + extra == target:
                return document
    raise AssertionError(f"cannot build a {target}-byte document")


def _peer_cases() -> list[tuple[str, object, bool]]:
    """Differences found against a native PostgreSQL 18 run, plus their neighbours."""
    effect = {"tag": "effect_settled", "effect_id": U1}
    at = {"tag": "at", "zone": "UTC"}
    wide = {**READ, "capability": "c" * 128, "field": "f" * 64}
    cases: list[tuple[str, object, bool]] = [
        ("effect statuses distinct", {**effect, "status_set": ["failed", "succeeded"]}, True),
        ("effect statuses duplicated", {**effect, "status_set": ["failed", "failed"]}, False),
        ("effect status unhashable", {**effect, "status_set": [[]]}, False),
        ("effect status unknown", {**effect, "status_set": ["exploded"]}, False),
        ("read op array", {**READ, "op": []}, False),
        ("read op object", {**READ, "op": {}}, False),
        ("read op number", {**READ, "op": 1}, False),
        ("decision outcome array", {**DECISION, "outcome": []}, False),
        ("transition kind object", {**TRANSITION, "kind": {}}, False),
        ("transition state array", {**TRANSITION, "to": []}, False),
        ("expectation deviation array", {**EXPECTATION, "deviation": []}, False),
        ("mode array", {"tag": "mode", "mode": [], "change": "entered"}, False),
        ("condition state array", {"tag": "condition", "source": "s", "state": []}, False),
        ("wide number x16", _all(*[{**wide, "value": 1e300}] * 16), False),
        ("wide number single", {**READ, "value": 1e300}, False),
        ("huge integer", {**READ, "value": 10**400}, False),
        ("huge negative integer", {**READ, "value": -(10**400)}, False),
        ("largest magnitude", {**READ, "value": 10**15}, True),
        ("past largest magnitude", {**READ, "value": 10**15 + 1}, False),
        ("smallest non-zero", {**READ, "value": 1e-6}, True),
        ("below smallest non-zero", {**READ, "value": 1e-7}, False),
        ("tiny number x16", _all(*[{**wide, "value": 1e-300}] * 16), False),
        ("zero", {**READ, "value": 0}, True),
        ("string with NUL", {**READ, "op": "eq", "value": "a\0b"}, False),
        ("string with lone surrogate", {**READ, "op": "eq", "value": "\ud800"}, False),
        ("name with NUL", {**READ, "capability": "a\0b"}, False),
        ("exactly 8192 bytes with a long-expansion number", sized_document(8192), True),
        ("exactly 8193 bytes with a long-expansion number", sized_document(8193), False),
        ("exactly 8192 bytes with negative zero", sized_document(8192, -0.0, -1), True),
        ("exactly 8193 bytes with negative zero", sized_document(8193, -0.0, -1), False),
        ("exactly 8192 bytes with positive zero", sized_document(8192, 0.0, 0), True),
        ("negative", {**READ, "op": "ne", "value": -5}, True),
        ("fraction", {**READ, "op": "ge", "value": 99.5}, True),
    ]
    for label, instant, valid in (
        ("Z", "2026-11-23T09:34:00Z", True),
        ("+00:00", "2026-11-23T09:34:00+00:00", True),
        ("fraction", "2026-11-23T09:34:00.123456Z", True),
        ("compact offset", "2026-11-23T09:34:00+0000", False),
        ("hour offset", "2026-11-23T09:34:00+00", False),
        ("other offset", "2026-11-23T17:34:00+08:00", False),
        ("seven digit fraction", "2026-11-23T09:34:00.1234567Z", False),
        ("lowercase z", "2026-11-23T09:34:00z", False),
        ("space separator", "2026-11-23 09:34:00Z", False),
        ("leap second", "2026-11-23T23:59:60Z", False),
        ("hour 24", "2026-11-23T24:00:00Z", False),
        ("month 13", "2026-13-23T09:34:00Z", False),
        ("february 30", "2026-02-30T09:34:00Z", False),
        ("date only", "2026-11-23Z", False),
        ("word form", "Nov 23 2026 09:34 Z", False),
    ):
        cases.append((f"at instant {label}", {**at, "instant": instant}, valid))
    cases.append(("at instant not a string", {**at, "instant": 5}, False))
    cases.append(
        (
            "at zone Singapore",
            {**at, "instant": "2026-11-23T09:34:00Z", "zone": "Asia/Singapore"},
            True,
        )
    )
    cases.append(
        ("at zone unknown", {**at, "instant": "2026-11-23T09:34:00Z", "zone": "Mars/Base"}, False)
    )
    return cases
