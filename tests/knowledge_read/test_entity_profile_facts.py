"""entity_profile fact set: symmetric duplicates collapse, the 200 cap holds (bu-swkpoo.5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from butlers.knowledge_read.core import ReadContext
from butlers.knowledge_read.entities import PROFILE_FACT_LIMIT, entity_profile

NOW = datetime(2026, 11, 22, tzinfo=UTC)
OWNER = "00000000-0000-0000-0000-000000000001"


def _other(n: int) -> str:
    return f"00000000-0000-0000-0001-{n:012d}"


def _fid(prefix: int, n: int) -> str:
    return f"{prefix:08d}-0000-0000-0000-{n:012d}"


def _entity_fact(fid, predicate, subject, obj, age_days):
    return {
        "source_id": fid,
        "fields": {
            "id": fid,
            "predicate": predicate,
            "subject": subject,
            "object_entity_id": obj,
            "object_kind": "entity",
            "created_at": (NOW - timedelta(days=age_days)).isoformat(),
        },
    }


class _Conn:
    def __init__(self, entity_facts, facts=()):
        self.entity_facts, self.facts = list(entity_facts), list(facts)

    def is_in_transaction(self):
        return True

    async def fetch(self, query, *args):
        if "public__entities" in query:
            return [
                {
                    "source_id": OWNER,
                    "fields": {"id": OWNER, "canonical_name": "Owner", "entity_type": "person"},
                }
            ]
        if "relationship__entity_facts" in query:
            return self.entity_facts
        if "relationship__facts" in query:
            return self.facts
        raise AssertionError(query)

    async def fetchval(self, *_args):
        return 0


async def _profile(conn):
    context = ReadContext(
        sub="github|1", as_of=NOW - timedelta(seconds=5), now=NOW, cursor_key=b"k" * 32
    )
    return await entity_profile(conn, context, {"entity_id": OWNER, "sections": ["facts"]})


async def test_reverse_direction_knows_collapses_to_the_newest():
    older = _entity_fact(_fid(1, 1), "knows", OWNER, _other(1), 10)
    newer = _entity_fact(_fid(1, 2), "knows", _other(1), OWNER, 2)
    result = await _profile(_Conn([older, newer]))
    assert [f["id"] for f in result["facts"]] == [_fid(1, 2)]
    assert result["coverage"]["facts_complete"] is True


async def test_non_symmetric_predicates_keep_both_directions():
    a = _entity_fact(_fid(2, 1), "manages", OWNER, _other(1), 3)
    b = _entity_fact(_fid(2, 2), "manages", _other(1), OWNER, 4)
    c = _entity_fact(_fid(2, 3), "knows", OWNER, _other(2), 5)  # a different pair
    result = await _profile(_Conn([a, b, c]))
    assert {f["id"] for f in result["facts"]} == {_fid(2, 1), _fid(2, 2), _fid(2, 3)}


async def test_the_cap_still_applies_after_the_collapse():
    rows = []
    for n in range(150):  # 150 pairs, each stored in both directions: 300 rows
        rows.append(_entity_fact(_fid(3, n), "knows", OWNER, _other(n), n))
        rows.append(_entity_fact(_fid(4, n), "knows", _other(n), OWNER, n + 1))
    result = await _profile(_Conn(rows))
    assert len(result["facts"]) == PROFILE_FACT_LIMIT - 50  # 150 unordered pairs, under the cap
    rows += [_entity_fact(_fid(5, n), "friend-of", OWNER, _other(n), n) for n in range(150)]
    result = await _profile(_Conn(rows))
    assert len(result["facts"]) == PROFILE_FACT_LIMIT
    assert result["coverage"]["facts_complete"] is False
    assert len(json.dumps(result)) > 0


def test_the_migration_retires_only_the_legacy_echo_rows():
    path = (
        Path(__file__).parents[2]
        / "roster/relationship/migrations/046_retire_legacy_set_fact_echoes.py"
    )
    spec = spec_from_file_location("_migration_rel_046", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = " ".join(module.RETIRE_STATEMENT.split())
    assert module.down_revision == "rel_045"
    assert sql.startswith("UPDATE relationship.facts SET validity = 'retracted'")
    assert "predicate = 'activity'" in sql and "content LIKE 'Set fact %'" in sql
    assert "DELETE" not in sql.upper()
