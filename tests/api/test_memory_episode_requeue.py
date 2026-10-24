"""POST /api/memory/episodes/{episode_id}/requeue (bu-kqnum.14.3).

Router-boundary behavior of the single manual recovery verb for a dead-lettered
episode, against fake pools that mirror the storage function's real SQL shape
(a guarded conditional UPDATE plus a follow-up status read). The concurrency
guarantee is pinned against real Postgres in
``tests/modules/memory/test_episode_requeue_lifecycle.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.deps import get_db

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


def _episode(episode_id: uuid.UUID, status: str = "dead_letter") -> dict:
    return {
        "id": episode_id,
        "butler": "atlas",
        "session_id": None,
        "content": "Episode content",
        "importance": 5.0,
        "reference_count": 0,
        "consolidated": False,
        "consolidation_status": status,
        "created_at": _NOW,
        "last_referenced_at": None,
        "expires_at": None,
        "metadata": None,
        "consolidation_attempts": 5 if status == "dead_letter" else 1,
        "last_consolidation_error": "Consolidation execution failed.",
        "dead_letter_reason": "terminal" if status == "dead_letter" else None,
        "next_consolidation_retry_at": None,
    }


class _Record(dict):
    """asyncpg Record stand-in: item access, ``.get`` and ``dict(...)``."""


class _Tx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _Acquire:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _Pool:
    """Holds at most one episode; records every query it is asked."""

    def __init__(self, *, episode: dict | None = None, fail: bool = False) -> None:
        self.episode = episode
        self.fail = fail
        self.queries: list[str] = []
        self.events: list[tuple] = []

    async def fetchrow(self, query: str, *args: object):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("pool unreachable")
        if self.episode is None or str(args[0]) != str(self.episode["id"]):
            return None
        if "UPDATE" in query:
            if self.episode["consolidation_status"] != "dead_letter":
                return None
            self.episode.update(
                consolidation_status="pending",
                consolidation_attempts=0,
                dead_letter_reason=None,
                last_consolidation_error=None,
                next_consolidation_retry_at=None,
            )
            return _Record(self.episode)
        if "SELECT consolidation_status" in query:
            return _Record({"consolidation_status": self.episode["consolidation_status"]})
        raise AssertionError(f"Unexpected query: {query}")

    async def execute(self, query: str, *args: object) -> str:
        self.queries.append(query)
        self.events.append((query, args))
        return "INSERT 0 1"

    def acquire(self) -> _Acquire:
        return _Acquire(self)

    def transaction(self) -> _Tx:
        return _Tx()


class _DB:
    """DatabaseManager stand-in. ``private`` names butlers with a memory-schema override."""

    def __init__(self, pools: dict[str, _Pool], *, private: set[str] = frozenset()) -> None:
        self._pools = pools
        self._private = set(private)
        self.butler_names = list(pools)
        self.pool_calls: list[str] = []

    def pool(self, name: str) -> _Pool:
        self.pool_calls.append(name)
        if name not in self._pools:
            raise KeyError(name)
        return self._pools[name]

    def schema_for_butler(self, name: str) -> str:
        return name

    def memory_schema_for_butler(self, name: str) -> str:
        return f"{name}_mem" if name in self._private else name


async def _post(app, db: _DB, episode_id: object):
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(f"/api/memory/episodes/{episode_id}/requeue")


async def test_requeue_resets_a_dead_letter_episode_and_says_nothing_was_run(app) -> None:
    episode_id = uuid.uuid4()
    pool = _Pool(episode=_episode(episode_id))

    resp = await _post(app, _DB({"atlas": pool}), episode_id)

    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert data["id"] == str(episode_id)
    assert data["consolidation_status"] == "pending"
    assert data["consolidation_attempts"] == 0
    assert data["dead_letter_reason"] is None
    assert body["meta"]["queued"] is True
    assert body["meta"]["consolidation_started"] is False
    assert "next scheduled" in body["meta"]["message"]
    assert "nothing was run" in body["meta"]["message"]
    assert len(pool.events) == 1
    assert "episode_consolidation_requeued" in pool.events[0][0]
    assert "retry_requested" not in pool.events[0][0]
    update = next(q for q in pool.queries if "UPDATE" in q)
    assert '"atlas"."episodes"' in update


async def test_requeue_never_returns_lease_internals(app) -> None:
    episode_id = uuid.uuid4()
    pool = _Pool(episode=_episode(episode_id))

    resp = await _post(app, _DB({"atlas": pool}), episode_id)

    assert "leased_by" not in resp.text and "leased_until" not in resp.text
    update = next(q for q in pool.queries if "UPDATE" in q)
    returning = update.split("RETURNING", 1)[1]
    assert "leased" not in returning


async def test_requeue_400_for_a_malformed_id_before_any_pool_access(app) -> None:
    db = _DB({"atlas": _Pool()})

    resp = await _post(app, db, "not-a-uuid")

    assert resp.status_code == 400
    assert db.pool_calls == []


async def test_requeue_404_when_every_eligible_source_answers_without_the_row(app) -> None:
    db = _DB({"atlas": _Pool(), "bishop": _Pool()})

    resp = await _post(app, db, uuid.uuid4())

    assert resp.status_code == 404


async def test_requeue_503_names_the_failed_source_when_ownership_is_unresolved(app) -> None:
    db = _DB({"atlas": _Pool(), "bishop": _Pool(fail=True)})

    resp = await _post(app, db, uuid.uuid4())

    assert resp.status_code == 503
    assert "bishop" in resp.json()["detail"]


async def test_requeue_finds_the_row_even_when_an_unrelated_source_failed(app) -> None:
    episode_id = uuid.uuid4()
    db = _DB({"atlas": _Pool(episode=_episode(episode_id)), "bishop": _Pool(fail=True)})

    resp = await _post(app, db, episode_id)

    assert resp.status_code == 200


async def test_requeue_409_leaves_a_non_dead_letter_episode_untouched(app) -> None:
    episode_id = uuid.uuid4()
    pool = _Pool(episode=_episode(episode_id, status="pending"))

    resp = await _post(app, _DB({"atlas": pool}), episode_id)

    assert resp.status_code == 409
    assert "dead_letter" in resp.json()["detail"]
    assert pool.episode["consolidation_status"] == "pending"
    assert pool.episode["consolidation_attempts"] == 1
    assert pool.events == []


async def test_a_second_requeue_after_success_is_a_409_with_one_event(app) -> None:
    episode_id = uuid.uuid4()
    pool = _Pool(episode=_episode(episode_id))
    db = _DB({"atlas": pool})

    first = await _post(app, db, episode_id)
    second = await _post(app, db, episode_id)

    assert (first.status_code, second.status_code) == (200, 409)
    assert len(pool.events) == 1


async def test_a_private_memory_schema_is_neither_read_nor_mutated_and_reads_as_404(app) -> None:
    episode_id = uuid.uuid4()
    private = _Pool(episode=_episode(episode_id))
    db = _DB({"atlas": _Pool(), "chronicler": private}, private={"chronicler"})

    resp = await _post(app, db, episode_id)

    assert resp.status_code == 404
    assert private.queries == []
    assert private.episode["consolidation_status"] == "dead_letter"
    assert private.events == []


async def test_a_private_source_is_never_reported_as_a_failed_pool(app) -> None:
    db = _DB({"chronicler": _Pool(fail=True)}, private={"chronicler"})

    resp = await _post(app, db, uuid.uuid4())

    assert resp.status_code == 503
    assert "chronicler" not in resp.json()["detail"]
    assert "No usable memory source" in resp.json()["detail"]


async def test_requeue_503_when_there_is_no_usable_source_at_all(app) -> None:
    resp = await _post(app, _DB({}), uuid.uuid4())

    assert resp.status_code == 503


async def test_requeue_runs_nothing(app) -> None:
    episode_id = uuid.uuid4()
    pool = _Pool(episode=_episode(episode_id))
    with patch(
        "butlers.modules.memory.consolidation.run_consolidation", new_callable=AsyncMock
    ) as run:
        resp = await _post(app, _DB({"atlas": pool}), episode_id)

    assert resp.status_code == 200
    run.assert_not_awaited()


async def test_the_per_butler_retry_consolidation_route_is_gone(app) -> None:
    episode_id = uuid.uuid4()
    pool = _Pool(episode=_episode(episode_id))
    app.dependency_overrides[get_db] = lambda: _DB({"atlas": pool})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/butlers/atlas/memory/episodes/{episode_id}/retry-consolidation"
        )

    assert resp.status_code in (404, 405)
    assert pool.episode["consolidation_status"] == "dead_letter"


async def test_there_is_no_collection_requeue_route(app) -> None:
    app.dependency_overrides[get_db] = lambda: _DB({"atlas": _Pool()})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/memory/episodes/requeue")

    assert resp.status_code in (404, 405)


def _read_rows(episode_id: uuid.UUID) -> list[dict]:
    return [_Record(_episode(episode_id))]


class _ReadPool:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    async def fetchval(self, query: str, *args: object):
        self.queries.append(query)
        return len(self.rows)

    async def fetchrow(self, query: str, *args: object):
        self.queries.append(query)
        return self.rows[0]

    async def fetch(self, query: str, *args: object):
        self.queries.append(query)
        return self.rows


async def test_detail_and_list_carry_the_dossier_and_never_the_lease(app) -> None:
    episode_id = uuid.uuid4()
    pool = _ReadPool(_read_rows(episode_id))
    db = MagicMock()
    db.butler_names = ["atlas"]
    db.pool.return_value = pool
    db.schema_for_butler.return_value = "atlas"
    db.memory_schema_for_butler.return_value = "atlas"
    app.dependency_overrides[get_db] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        detail = await client.get(f"/api/memory/episodes/{episode_id}")
        listing = await client.get("/api/memory/episodes")

    for payload in (detail.json()["data"], listing.json()["data"][0]):
        assert payload["consolidation_attempts"] == 5
        assert payload["last_consolidation_error"] == "Consolidation execution failed."
        assert payload["dead_letter_reason"] == "terminal"
        assert "next_consolidation_retry_at" in payload
        assert "leased_by" not in payload and "leased_until" not in payload
    assert all("leased" not in q for q in pool.queries)
    assert all("consolidation_attempts" in q for q in pool.queries if "SELECT id" in q)


async def test_the_inspect_embedded_episode_carries_the_dossier_and_never_the_lease(app) -> None:
    episode_id = uuid.uuid4()
    pool = _ReadPool(_read_rows(episode_id))
    db = MagicMock()
    db.butler_names = ["atlas"]
    db.pool.return_value = pool
    db.schema_for_butler.return_value = "atlas"
    db.memory_schema_for_butler.return_value = "atlas"
    app.dependency_overrides[get_db] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect", params={"kind": "episode"})

    assert resp.status_code == 200
    embedded = resp.json()["data"][0]["episode"]
    assert embedded["consolidation_attempts"] == 5
    assert embedded["last_consolidation_error"] == "Consolidation execution failed."
    assert embedded["dead_letter_reason"] == "terminal"
    assert "next_consolidation_retry_at" in embedded
    assert "leased_by" not in embedded and "leased_until" not in embedded
    assert all("leased" not in q for q in pool.queries)
    assert all("consolidation_attempts" in q for q in pool.queries if "SELECT id" in q)
