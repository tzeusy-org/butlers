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
        body.count("'^[a-z0-9_.:-]{1,128}$'") == 4
    )  # expectation kind, condition source, capability, bundle
    assert body.count("'^[a-z0-9_.-]{1,64}$'") == 1


def test_sql_and_python_agree_on_the_tags():
    sql_tags = set(re.findall(r"WHEN '([a-z_]+)'(?:,'([a-z_]+)')? THEN", _body()))
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
