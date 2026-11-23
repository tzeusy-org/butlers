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
from tests.perception.predicate_corpus import corpus

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
        ast.validate(document)
        accepted = True
    except ast.PredicateError:
        accepted = False
    assert accepted is valid, name


def test_the_largest_valid_document_is_under_the_size_bound_in_compact_bytes():
    [(_, document, _)] = [c for c in corpus() if c[0] == "largest valid document"]
    assert len(ast.canonical(document).encode()) < ast.MAX_BYTES
