"""REQ-owner-watches-001: the SQL predicate validator mirrors the Python one.

The database function cannot run without Postgres, so this guards the drift that is checkable
from source: the closed vocabularies, the name patterns, the pinned body hash that the
migrations verify, and a Python/SQL agreement on the set of tags. The executing parity proof is
``test_perception_postgres.py`` (Docker).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from butlers.conditions import predicate as ast
from butlers.perception.model import MODES
from tests.perception.predicate_corpus import Raw, corpus

ROOT = Path(__file__).resolve().parents[2]
INIT_DB = (ROOT / "scripts" / "init-db.sql").read_text()
START = "AS $predicate$"


def _body() -> str:
    start = INIT_DB.index(START) + len(START)
    return INIT_DB[start : INIT_DB.index("$predicate$;", start)]


def _sql_set(field: str) -> frozenset[str]:
    """The quoted members of ``p->>'<field>' IN (...)`` for one field in the validator."""
    found = re.findall(rf"p->>'{field}' IN \(([^)]*)\)", _body())
    members = {frozenset(re.findall(r"'([^']*)'", group)) for group in found}
    assert len(members) == 1, f"{field}: expected one vocabulary, found {members}"
    return members.pop()


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("deviation", ast.DEVIATIONS),
        ("state", ast.CONDITION_STATES),
        ("change", ast.MODE_CHANGES),
        ("mode", MODES),
        ("outcome", ast.DECISION_OUTCOMES),
    ],
)
def test_sql_vocabulary_matches_python(field, expected):
    assert _sql_set(field) == expected


def test_read_ops_match_python():
    groups = re.findall(r"p->>'op' IN \(([^)]*)\)", _body())
    assert {frozenset(re.findall(r"'([^']*)'", g)) for g in groups} == {
        ast.READ_OPS,
        ast.ORDERED_OPS,
    }


def test_name_patterns_match_python():
    body = _body()
    assert ast.NAME_PATTERN.pattern == "[a-z0-9_.:-]{1,128}"
    assert ast.FIELD_PATTERN.pattern == "[a-z0-9_.-]{1,64}"
    assert (
        body.count("'^[a-z0-9_.:-]{1,128}$'") == 3
    )  # condition source, capability, bundle (expectation_kind is a registered kind)
    assert body.count("'^[a-z0-9_.-]{1,64}$'") == 1


def test_sql_and_python_agree_on_the_tags():
    sql_tags = set(re.findall(r"^\s*WHEN '([a-z_]+)'(?:,'([a-z_]+)')? THEN", _body(), re.MULTILINE))
    flat = {tag for pair in sql_tags for tag in pair if tag}
    python_tags = {
        "all",
        "any",
        "at",
        "transition",
        "decision",
        "owner_input",
        "effect_settled",
    } | ast.M7_TAGS
    assert flat == python_tags


def test_migrations_pin_the_installed_body():
    sha = hashlib.sha256(_body().encode()).hexdigest()
    for name in ("core_322_dark_perception.py", "core_363_m7_predicate_leaves.py"):
        text = (ROOT / "alembic" / "versions" / "core" / name).read_text()
        assert sha in text, f"{name} does not pin the current valid_predicate body"


def test_expectation_kinds_match_python_and_the_registered_expect_kinds():
    from butlers.perception.model import KINDS

    assert ast.EXPECTATION_KINDS == {k for k in KINDS if k.startswith("expect.")} != frozenset()
    found = re.findall(r"p->>'expectation_kind' IN \(([^)]*)\)", _body())
    assert [frozenset(re.findall(r"'([^']*)'", g)) for g in found] == [ast.EXPECTATION_KINDS]


def test_sql_uuid_rule_is_the_python_canonical_rule():
    assert (
        "uuid_re CONSTANT text:='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
        in _body()
    )
    assert "::uuid" not in _body()  # the cast accepts uppercase, braces and no hyphens


@pytest.mark.parametrize(
    ("name", "document", "valid"), corpus(), ids=[name for name, _, _ in corpus()]
)
def test_python_validator_gives_the_corpus_verdict(name, document, valid):
    try:
        ast.loads(document) if isinstance(document, Raw) else ast.validate(document)
        accepted = True
    except ast.PredicateError:
        accepted = False
    assert accepted is valid, name


def test_the_largest_valid_document_is_under_the_size_bound_in_compact_bytes():
    [(_, document, _)] = [c for c in corpus() if c[0] == "largest valid document"]
    assert len(ast.canonical(document).encode()) < ast.MAX_BYTES


def test_sql_instant_rule_is_the_python_canonical_rule():
    assert f"instant_re CONSTANT text:='^{ast.INSTANT_PATTERN.pattern}$'" in _body()


def _hostile_documents():
    values = [[], {}, None, 7, -1, 1.5, True, "", "x" * 300, 10**400, [[]], {"a": [1]}]
    for _, document, _ in corpus():
        if type(document) is not dict:
            continue  # raw JSON text and non-objects are covered by the corpus verdicts
        for key in document:
            if key == "children":
                continue
            for value in values:
                yield {**document, key: value}


def test_the_python_validator_raises_only_its_own_error_whatever_the_document_holds():
    """A hostile value (unhashable, huge, mistyped) refuses cleanly; it never raises a TypeError."""
    for document in _hostile_documents():
        try:
            ast.validate(document)
        except ast.PredicateError:
            pass


def test_loads_refuses_a_numeric_literal_longer_than_forty_characters():
    long_literal = "0." + "1" * 45
    text = (
        '{"tag":"read_check","capability":"finance.balance","field":"available",'
        f'"op":"lt","value":{long_literal}}}'
    )
    with pytest.raises(ast.PredicateError):
        ast.loads(text)
    assert ast.loads(text.replace(long_literal, "0.5"))["value"] == 0.5


@pytest.mark.parametrize("text", ["{", "[1,", '{"tag": NaN}', "7" * 5000])
def test_loads_raises_only_its_own_error(text):
    with pytest.raises(ast.PredicateError):
        ast.loads(text)


def _run_with_timeout(call, seconds=5):
    import threading

    outcome = []

    def target():
        try:
            call()
            outcome.append("accepted")
        except ast.PredicateError:
            outcome.append("refused")
        except BaseException as exc:  # noqa: BLE001
            outcome.append(type(exc).__name__)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    return outcome[0] if outcome else "hung"


def test_a_cyclic_predicate_is_refused_not_hung():
    cyclic: dict = {"tag": "all", "children": []}
    cyclic["children"].append(cyclic)
    assert _run_with_timeout(lambda: ast.validate(cyclic)) == "refused"
    shared = {
        "tag": "decision",
        "subject_id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        "outcome": "approved",
    }
    assert (
        _run_with_timeout(lambda: ast.validate({"tag": "any", "children": [shared, shared]}))
        == "accepted"
    )


def test_decimal_values_are_measured_and_bounded_exactly():
    from decimal import Decimal

    leaf = {
        "tag": "read_check",
        "capability": "finance.balance",
        "field": "available",
        "op": "lt",
    }
    assert ast.validate({**leaf, "value": Decimal("0.000001")})["value"] == 1e-6
    assert ast.validate({**leaf, "value": Decimal("-0.0")})["value"] == 0
    for bad in (Decimal("9e-1000"), Decimal("NaN"), Decimal("Infinity"), Decimal("1e1000")):
        with pytest.raises(ast.PredicateError):
            ast.validate({**leaf, "value": bad})
