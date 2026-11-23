"""One predicate corpus for the Python and SQL validators (REQ-owner-watches-001).

Each entry is ``(name, document, valid)``. ``valid`` is the declared verdict, so the corpus is
checked against both validators and cannot drift into agreeing on the wrong answer.
"""

from __future__ import annotations

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
    return cases
