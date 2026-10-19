"""GET /api/memory/inspect reports an exact total, independent of the page.

The row fetch stays bounded at ``offset + limit`` per pool; ``meta.total`` comes from a
per-pool, per-kind ``count(*)`` under the same filters (bu-kqnum.14.5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx

from butlers.api.routers.memory import _get_db_manager

_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


class _Row(dict):
    """asyncpg Record stand-in supporting ``row[key]`` and ``row.get(key)``."""


def _episode(butler: str, n: int) -> _Row:
    return _Row(
        id=uuid.uuid4(),
        butler=butler,
        session_id=None,
        content=f"{butler} episode {n}",
        importance=0.5,
        reference_count=0,
        consolidated=False,
        consolidation_status="pending",
        created_at=_NOW - timedelta(minutes=n),
        last_referenced_at=None,
        expires_at=None,
        metadata=None,
    )


class _Pool:
    """Holds ``size`` matching episodes; records count and row queries."""

    def __init__(self, butler: str, size: int, *, offset_minutes: int = 0) -> None:
        self._rows = [_episode(butler, offset_minutes + i) for i in range(size)]
        self.count_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, query: str, *args: object) -> int:
        self.count_calls.append((query, args))
        return len(self._rows)

    async def fetch(self, query: str, *args: object) -> list[_Row]:
        self.fetch_calls.append((query, args))
        if "FROM episodes" in query:
            return self._rows[: int(args[-1])]
        return []


class _Failing:
    async def fetchval(self, query: str, *args: object) -> int:
        raise RuntimeError("connection reset by peer")

    async def fetch(self, query: str, *args: object) -> list[_Row]:
        raise RuntimeError("connection reset by peer")


class _DB:
    def __init__(self, pools: dict[str, object]) -> None:
        self._pools = pools
        self.butler_names = list(pools)

    def pool(self, name: str) -> object:
        return self._pools[name]

    def relation_observed_since_start(self, name: str, relation: str) -> bool | None:
        return None


async def _get(app, db: _DB, **params: object) -> dict:
    app.dependency_overrides[_get_db_manager] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect", params=params)
    assert resp.status_code == 200
    return resp.json()


async def test_total_is_the_exact_match_count_across_pools_not_the_slice(app) -> None:
    db = _DB({"atlas": _Pool("atlas", 120), "finance": _Pool("finance", 80, offset_minutes=1000)})

    body = await _get(app, db, kind="episode", offset=0, limit=50)

    assert body["meta"]["total"] == 200
    assert len(body["data"]) == 50
    assert body["meta"]["offset"] == 0
    assert body["meta"]["limit"] == 50


async def test_total_is_independent_of_offset_and_limit(app) -> None:
    db = _DB({"atlas": _Pool("atlas", 120), "finance": _Pool("finance", 80, offset_minutes=1000)})

    first = await _get(app, db, kind="episode", offset=0, limit=10)
    deep = await _get(app, db, kind="episode", offset=150, limit=25)

    assert first["meta"]["total"] == deep["meta"]["total"] == 200
    assert len(deep["data"]) == 25


async def test_has_more_follows_the_exact_total(app) -> None:
    db = _DB({"atlas": _Pool("atlas", 30), "finance": _Pool("finance", 30, offset_minutes=1000)})

    middle = await _get(app, db, kind="episode", offset=0, limit=50)
    last = await _get(app, db, kind="episode", offset=50, limit=50)

    assert middle["meta"]["total"] == 60
    assert middle["meta"]["has_more"] is True
    assert len(last["data"]) == 10
    assert last["meta"]["has_more"] is False


async def test_count_query_carries_the_same_filter_and_args_as_the_row_query(app) -> None:
    pool = _Pool("atlas", 3)

    await _get(app, _DB({"atlas": pool}), kind="episode", q="dentist", limit=5)

    count_query, count_args = pool.count_calls[0]
    row_query, row_args = pool.fetch_calls[0]
    assert count_query.startswith("SELECT count(*) FROM ")
    assert "plainto_tsquery('english', $1)" in count_query
    assert count_args == ("dentist",)
    assert row_args[0] == "dentist"


async def test_kind_filter_counts_only_that_kind(app) -> None:
    pool = _Pool("atlas", 3)

    await _get(app, _DB({"atlas": pool}), kind="episode")

    assert len(pool.count_calls) == 1
    assert "FROM episodes" in pool.count_calls[0][0]


async def test_unscoped_search_counts_every_kind_in_each_pool(app) -> None:
    pool = _Pool("atlas", 3)

    await _get(app, _DB({"atlas": pool}))

    counted = " ".join(query for query, _ in pool.count_calls)
    assert len(pool.count_calls) == 3
    for relation in ("FROM episodes", "FROM facts", "FROM rules"):
        assert relation in counted


async def test_failed_pool_is_named_and_excluded_from_the_total(app) -> None:
    db = _DB({"atlas": _Pool("atlas", 7), "finance": _Failing()})

    body = await _get(app, db, kind="episode")

    assert body["meta"]["total"] == 7
    assert body["meta"]["pools_failed"] == ["finance"]
    assert len(body["data"]) == 7


async def test_fact_count_carries_the_query_filter(app) -> None:
    pool = _Pool("atlas", 0)

    await _get(app, _DB({"atlas": pool}), kind="fact", q="dentist")

    count_query, count_args = pool.count_calls[0]
    assert "FROM facts" in count_query
    assert "plainto_tsquery('english', $1)" in count_query
    assert count_args == ("dentist",)


async def test_rule_count_excludes_forgotten_rules_like_the_row_query(app) -> None:
    pool = _Pool("atlas", 0)

    await _get(app, _DB({"atlas": pool}), kind="rule", q="sleep")
    await _get(app, _DB({"atlas": pool}), kind="rule")

    with_q, without_q = pool.count_calls
    forgotten = "(metadata->>'forgotten')::boolean IS NOT TRUE"
    assert "FROM rules" in with_q[0]
    assert "plainto_tsquery('english', $1)" in with_q[0] and forgotten in with_q[0]
    assert with_q[1] == ("sleep",)
    assert forgotten in without_q[0] and without_q[1] == ()


async def test_total_sums_every_kind_in_a_pool(app) -> None:
    class _Counting(_Pool):
        async def fetchval(self, query: str, *args: object) -> int:
            return {"FROM episodes": 4, "FROM facts": 5, "FROM rules": 6}[
                next(k for k in ("FROM episodes", "FROM facts", "FROM rules") if k in query)
            ]

    body = await _get(app, _DB({"atlas": _Counting("atlas", 0)}))

    assert body["meta"]["total"] == 15
