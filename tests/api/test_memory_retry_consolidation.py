"""Tests for POST /api/butlers/{name}/memory/episodes/{episode_id}/retry-consolidation.

Covers bu-6t8ix.2's acceptance criteria at the router boundary:
1. The endpoint resets a dead_letter episode's consolidation_status.
3. An episode not in dead_letter is rejected (409) with feedback, not silently
   reset — verifies neither the row nor the audit log changed.

Criterion 4 ("actually reconsidered, not merely relabelled") is covered at the
storage/consolidation-pipeline seam in
tests/modules/memory/test_consolidation_lifecycle.py, against a real Postgres
transaction, rather than re-asserted here against a mock.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest

from butlers.api.routers.memory import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


def _make_episode_row(
    *,
    episode_id: uuid.UUID,
    consolidation_status: str = "dead_letter",
) -> dict:
    return {
        "id": episode_id,
        "butler": "atlas",
        "session_id": None,
        "content": "Episode content",
        "importance": 5.0,
        "reference_count": 0,
        "consolidated": False,
        "consolidation_status": consolidation_status,
        "created_at": _NOW,
        "last_referenced_at": None,
        "expires_at": None,
        "metadata": None,
    }


def _make_record(row: dict) -> MagicMock:
    """Return a MagicMock that behaves like an asyncpg Record.

    Supports both item access (``r["id"]``, used by ``_row_to_episode``) and
    ``dict(record)`` (used by ``storage.retry_dead_letter_episode`` to return
    a plain dict) via the mapping protocol's ``keys()`` + ``__getitem__``.
    """
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    m.keys = MagicMock(side_effect=lambda: row.keys())
    return m


class _NullTransaction:
    """No-op async-context-manager mimicking asyncpg's ``conn.transaction()``."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _AcquireCtx:
    """Async-context-manager mimicking asyncpg's ``pool.acquire()``."""

    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _RetryPool:
    """Fake pool for the retry-consolidation endpoint.

    Holds at most one episode row keyed by id. The pre-flight status check
    (top-level ``pool.fetchrow``) and the guarded reset ``UPDATE ...
    RETURNING`` plus the ``memory_events`` audit ``INSERT`` (both run against
    ``conn`` under ``pool.acquire()``/``conn.transaction()``) all resolve
    against this same fake object, exercising
    ``storage.retry_dead_letter_episode``'s real transactional shape rather
    than stubbing it out.
    """

    def __init__(self, *, episode: dict | None) -> None:
        self.episode = episode
        self.execute_calls: list[tuple] = []

    async def fetchrow(self, query: str, *args: object):
        if self.episode is None or args[0] != self.episode["id"]:
            return None
        if "SELECT consolidation_status FROM" in query:
            return _make_record({"consolidation_status": self.episode["consolidation_status"]})
        if "UPDATE episodes" in query:
            if self.episode["consolidation_status"] != "dead_letter":
                return None
            self.episode["consolidation_status"] = "pending"
            return _make_record(self.episode)
        raise AssertionError(f"Unexpected query: {query}")

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return "INSERT 0 1"

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self)

    def transaction(self) -> _NullTransaction:
        return _NullTransaction()


class _RetryDB:
    """DatabaseManager stand-in returning a distinct _RetryPool per butler."""

    def __init__(self, pools: dict[str, _RetryPool]) -> None:
        self._pools = pools
        self.butler_names = list(pools)

    def pool(self, name: str) -> _RetryPool:
        if name not in self._pools:
            raise KeyError(f"No pool for butler: {name}")
        return self._pools[name]


async def test_retry_consolidation_resets_dead_letter_episode(app) -> None:
    """POST resets a dead_letter episode to pending and audits the reset."""
    episode_id = uuid.uuid4()
    pool = _RetryPool(episode=_make_episode_row(episode_id=episode_id))
    db = _RetryDB({"atlas": pool})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/butlers/atlas/memory/episodes/{episode_id}/retry-consolidation"
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == str(episode_id)
    assert data["consolidation_status"] == "pending"
    # An audit event was recorded for the reset (one execute call: the
    # memory_events INSERT; the RETURNING reset itself is a fetchrow).
    assert len(pool.execute_calls) == 1
    assert "memory_events" in pool.execute_calls[0][0]
    assert "episode_consolidation_retry_requested" in pool.execute_calls[0][0]


async def test_retry_consolidation_404_unknown_butler(app) -> None:
    """POST returns 404 when the named butler is not registered."""
    db = _RetryDB({"atlas": _RetryPool(episode=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/butlers/ghost/memory/episodes/{uuid.uuid4()}/retry-consolidation"
        )

    assert resp.status_code == 404


async def test_retry_consolidation_400_malformed_episode_id(app) -> None:
    """POST returns 400 when the path id is not a valid UUID."""
    db = _RetryDB({"atlas": _RetryPool(episode=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/butlers/atlas/memory/episodes/not-a-uuid/retry-consolidation"
        )

    assert resp.status_code == 400


async def test_retry_consolidation_404_unknown_episode(app) -> None:
    """POST returns 404 when the butler exists but has no episode with this id."""
    db = _RetryDB({"atlas": _RetryPool(episode=None)})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/butlers/atlas/memory/episodes/{uuid.uuid4()}/retry-consolidation"
        )

    assert resp.status_code == 404


async def test_retry_consolidation_409_when_not_dead_letter(app) -> None:
    """An episode outside dead_letter is rejected (409) with feedback, not reset."""
    episode_id = uuid.uuid4()
    pool = _RetryPool(
        episode=_make_episode_row(episode_id=episode_id, consolidation_status="pending")
    )
    db = _RetryDB({"atlas": pool})
    app.dependency_overrides[_get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/butlers/atlas/memory/episodes/{episode_id}/retry-consolidation"
        )

    assert resp.status_code == 409
    assert "dead_letter" in resp.json()["detail"]
    # Nothing was reset — status is unchanged and no audit event was recorded.
    assert pool.episode["consolidation_status"] == "pending"
    assert pool.execute_calls == []
