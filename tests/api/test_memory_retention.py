"""Tests for memory retention policy and compaction log endpoints (Phase 8 §10.5).

Covers:
- GET /api/memory/retention-policies returns all rows from public.memory_retention_policies.
- PUT /api/memory/retention-policies bulk-updates and calls audit.append per change.
- PUT returns 400 on invalid kind.
- GET /api/memory/compaction-log returns rows ordered ts DESC.
- GET /api/memory/inspect returns paginated results; honours kind filter.
- Cleanup job consults memory_retention_policies for 'event' and 'fact' kinds.
- Cleanup job logs to memory_compaction_log via _log_compaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.memory import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_retention_row(
    kind: str = "event",
    ttl_days: int | None = None,
    max_rows: int | None = 10000,
    updated_by: str = "system",
) -> MagicMock:
    """Build a mock asyncpg Record for memory_retention_policies."""
    m = MagicMock()
    data = {
        "kind": kind,
        "ttl_days": ttl_days,
        "max_rows": max_rows,
        "updated_at": _NOW,
        "updated_by": updated_by,
    }
    m.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return m


def _make_compaction_row(
    id: int = 1,
    kind: str = "event",
    rows_removed: int = 100,
    bytes_freed: int | None = None,
) -> MagicMock:
    """Build a mock asyncpg Record for memory_compaction_log."""
    m = MagicMock()
    data = {
        "id": id,
        "ts": _NOW,
        "kind": kind,
        "rows_removed": rows_removed,
        "bytes_freed": bytes_freed,
    }
    m.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return m


def _make_inspect_row(
    id: str = "00000000-0000-0000-0000-000000000001",
    butler: str = "memory",
    content: str = "Test content",
) -> MagicMock:
    """Build a mock asyncpg Record for an episode row in inspect queries."""
    import uuid

    m = MagicMock()
    data = {
        "id": uuid.UUID(id),
        "butler": butler,
        "source_butler": butler,
        "content": content,
        "created_at": _NOW,
        "metadata": None,
    }
    m.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return m


def _wire_memory_mock(
    app,
    *,
    retention_rows: list[MagicMock] | None = None,
    compaction_rows: list[MagicMock] | None = None,
    inspect_rows: list[MagicMock] | None = None,
    fetchrow_result: MagicMock | None = None,
) -> tuple[AsyncMock, MagicMock]:
    """Wire a FastAPI app with a mock pool for memory retention tests."""
    if retention_rows is None:
        retention_rows = []
    if compaction_rows is None:
        compaction_rows = []
    if inspect_rows is None:
        inspect_rows = []

    mock_pool = AsyncMock()

    async def _fetch(sql: str, *args):
        if "memory_retention_policies" in sql:
            return retention_rows
        if "memory_compaction_log" in sql:
            return compaction_rows
        if "episodes" in sql or "facts" in sql or "rules" in sql:
            return inspect_rows
        return []

    async def _fetchrow(sql: str, *args):
        if fetchrow_result is not None:
            return fetchrow_result
        # Simulate INSERT ... ON CONFLICT DO UPDATE RETURNING for retention policy update
        if "INSERT INTO public.memory_retention_policies" in sql and args:
            kind = args[0]
            return _make_retention_row(kind=kind, ttl_days=args[1], max_rows=args[2])
        return None

    mock_pool.fetch = AsyncMock(side_effect=_fetch)
    mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock(return_value="OK")

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool = MagicMock(side_effect=lambda name: mock_pool)
    mock_db.butler_names = ["memory"]

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_pool, mock_db


# ---------------------------------------------------------------------------
# GET /api/memory/retention-policies
# ---------------------------------------------------------------------------


async def test_get_retention_policies_returns_all_rows(app):
    """GET returns all rows from the policy table."""
    rows = [
        _make_retention_row("event", None, 10000),
        _make_retention_row("fact", 7, None),
    ]
    _wire_memory_mock(app, retention_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/retention-policies")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 2
    kinds = {p["kind"] for p in data}
    assert kinds == {"event", "fact"}


async def test_get_retention_policies_empty_table(app):
    """GET returns an empty list when no policies exist."""
    _wire_memory_mock(app, retention_rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/retention-policies")

    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_get_retention_policies_503_when_table_missing(app):
    """GET returns 503 when the retention table is unavailable."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=Exception("relation does not exist"))

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool = MagicMock(return_value=mock_pool)
    mock_db.butler_names = ["memory"]

    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/retention-policies")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# PUT /api/memory/retention-policies
# ---------------------------------------------------------------------------


async def test_put_retention_policies_invalid_kind_returns_400(app):
    """PUT with an invalid kind returns 400."""
    _wire_memory_mock(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/api/memory/retention-policies",
            json={"policies": [{"kind": "nonexistent", "ttl_days": 7, "max_rows": None}]},
        )

    assert resp.status_code == 400
    assert "Invalid kind" in resp.json()["detail"]


async def test_put_retention_policies_empty_list_returns_400(app):
    """PUT with an empty policies list returns 400."""
    _wire_memory_mock(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/api/memory/retention-policies",
            json={"policies": []},
        )

    assert resp.status_code == 400


async def test_put_retention_policies_returns_updated_rows(app):
    """PUT returns the updated policy rows from the DB."""
    _wire_memory_mock(app)

    with patch("butlers.api.routers.memory._audit.append") as mock_append:

        async def _fake_append(pool, actor, action, **kwargs):
            return 1

        mock_append.side_effect = _fake_append

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/memory/retention-policies",
                json={"policies": [{"kind": "event", "ttl_days": None, "max_rows": 9999}]},
            )

    assert resp.status_code == 200
    updated = resp.json()["data"]
    assert len(updated) == 1
    assert updated[0]["kind"] == "event"
    assert updated[0]["max_rows"] == 9999


# ---------------------------------------------------------------------------
# GET /api/memory/compaction-log
# ---------------------------------------------------------------------------


async def test_get_compaction_log_returns_rows(app):
    """GET returns compaction log entries."""
    rows = [
        _make_compaction_row(id=2, kind="event", rows_removed=50),
        _make_compaction_row(id=1, kind="fact", rows_removed=20),
    ]
    _wire_memory_mock(app, compaction_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/compaction-log")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 2
    assert data[0]["kind"] == "event"
    assert data[1]["kind"] == "fact"


async def test_get_compaction_log_empty(app):
    """GET returns empty list when no events logged."""
    _wire_memory_mock(app, compaction_rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/compaction-log")

    assert resp.status_code == 200
    assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# GET /api/memory/inspect
# ---------------------------------------------------------------------------


async def test_inspect_returns_results(app):
    """Inspect returns episode rows when available."""
    rows = [_make_inspect_row(content="Hello world")]
    _wire_memory_mock(app, inspect_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect?limit=10")

    assert resp.status_code == 200


async def test_inspect_kind_filter_restricts_queries(app):
    """When kind=episode only the episodes table is queried, not facts/rules."""
    rows = [_make_inspect_row(content="An episode")]
    mock_pool, _ = _wire_memory_mock(app, inspect_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect?kind=episode&limit=5")

    assert resp.status_code == 200
    fetched_sqls = [str(call.args[0]) for call in mock_pool.fetch.call_args_list]
    assert all("facts" not in sql and "rules" not in sql for sql in fetched_sqls)


async def test_inspect_invalid_kind_returns_400(app):
    """Inspect with an invalid kind returns 400."""
    _wire_memory_mock(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect?kind=unknown")

    assert resp.status_code == 400
    assert "Invalid kind" in resp.json()["detail"]


async def test_inspect_pagination_meta_present(app):
    """Inspect response includes pagination meta."""
    rows = [
        _make_inspect_row(
            id=f"00000000-0000-0000-0000-{i:012d}",
            content=f"item {i}",
        )
        for i in range(1, 4)
    ]
    _wire_memory_mock(app, inspect_rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/inspect?kind=episode&limit=2")

    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert "total" in meta
    assert meta["limit"] == 2


# ---------------------------------------------------------------------------
# Cleanup job: honors per-kind policy (§10.5)
# ---------------------------------------------------------------------------


async def test_episode_cleanup_uses_policy_max_rows():
    """_run_memory_episode_cleanup_job reads max_rows from memory_retention_policies."""
    from butlers.scheduled_jobs import _run_memory_episode_cleanup_job

    policy_row = MagicMock()
    policy_row.__getitem__ = MagicMock(side_effect=lambda k: {"ttl_days": None, "max_rows": 500}[k])

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=policy_row)

    with patch(
        "butlers.modules.memory.consolidation.run_episode_cleanup",
        new=AsyncMock(return_value={"expired_deleted": 0, "capacity_deleted": 0, "remaining": 500}),
    ) as mock_cleanup:
        await _run_memory_episode_cleanup_job(pool, None)

    mock_cleanup.assert_awaited_once()
    _, kwargs = mock_cleanup.call_args
    assert kwargs.get("max_entries") == 500


async def test_purge_superseded_uses_policy_ttl_days():
    """_run_memory_purge_superseded_job reads ttl_days from memory_retention_policies."""
    from butlers.scheduled_jobs import _run_memory_purge_superseded_job

    policy_row = MagicMock()
    policy_row.__getitem__ = MagicMock(side_effect=lambda k: {"ttl_days": 30, "max_rows": None}[k])

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=policy_row)

    with patch(
        "butlers.modules.memory.storage.purge_superseded_facts",
        new=AsyncMock(return_value={"deleted": 5, "deleted_ha_state": 0}),
    ) as mock_purge:
        with patch("butlers.scheduled_jobs._table_size_bytes", new=AsyncMock(return_value=None)):
            await _run_memory_purge_superseded_job(pool, None)

    mock_purge.assert_awaited_once()
    _, kwargs = mock_purge.call_args
    assert kwargs.get("older_than_days") == 30


async def test_episode_cleanup_falls_back_to_default_when_policy_missing():
    """When the policy table is unavailable, cleanup defaults to 10000."""
    from butlers.scheduled_jobs import _run_memory_episode_cleanup_job

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=Exception("relation does not exist"))

    with patch(
        "butlers.modules.memory.consolidation.run_episode_cleanup",
        new=AsyncMock(return_value={"expired_deleted": 0, "capacity_deleted": 0, "remaining": 100}),
    ) as mock_cleanup:
        await _run_memory_episode_cleanup_job(pool, None)

    _, kwargs = mock_cleanup.call_args
    assert kwargs.get("max_entries") == 10000


async def test_cleanup_logs_compaction_when_rows_removed():
    """_run_memory_episode_cleanup_job calls _log_compaction when rows were deleted."""
    from butlers.scheduled_jobs import _run_memory_episode_cleanup_job

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    with patch(
        "butlers.modules.memory.consolidation.run_episode_cleanup",
        new=AsyncMock(return_value={"expired_deleted": 3, "capacity_deleted": 7, "remaining": 90}),
    ):
        with patch("butlers.scheduled_jobs._table_size_bytes", new=AsyncMock(return_value=None)):
            with patch(
                "butlers.scheduled_jobs._log_compaction",
                new=AsyncMock(),
            ) as mock_log:
                await _run_memory_episode_cleanup_job(pool, None)

    mock_log.assert_awaited_once()
    call_args = mock_log.call_args
    assert call_args.args[1] == "event"  # kind
    assert call_args.args[2] == 10  # rows_removed = 3 + 7


async def test_cleanup_passes_bytes_freed_to_log_compaction():
    """_run_memory_episode_cleanup_job computes bytes_freed from table-size delta."""
    from butlers.scheduled_jobs import _run_memory_episode_cleanup_job

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    # Simulate table shrinking from 8192 → 4096 bytes after cleanup.
    size_sequence = [8192, 4096]

    with patch(
        "butlers.modules.memory.consolidation.run_episode_cleanup",
        new=AsyncMock(return_value={"expired_deleted": 5, "capacity_deleted": 0, "remaining": 95}),
    ):
        with patch(
            "butlers.scheduled_jobs._table_size_bytes",
            new=AsyncMock(side_effect=size_sequence),
        ):
            with patch(
                "butlers.scheduled_jobs._log_compaction",
                new=AsyncMock(),
            ) as mock_log:
                await _run_memory_episode_cleanup_job(pool, None)

    mock_log.assert_awaited_once()
    call_args = mock_log.call_args
    assert call_args.kwargs.get("bytes_freed") == 4096  # 8192 - 4096


async def test_cleanup_bytes_freed_is_none_when_size_unavailable():
    """bytes_freed is None when pg_total_relation_size returns None."""
    from butlers.scheduled_jobs import _run_memory_episode_cleanup_job

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    with patch(
        "butlers.modules.memory.consolidation.run_episode_cleanup",
        new=AsyncMock(return_value={"expired_deleted": 2, "capacity_deleted": 0, "remaining": 98}),
    ):
        with patch(
            "butlers.scheduled_jobs._table_size_bytes",
            new=AsyncMock(return_value=None),
        ):
            with patch(
                "butlers.scheduled_jobs._log_compaction",
                new=AsyncMock(),
            ) as mock_log:
                await _run_memory_episode_cleanup_job(pool, None)

    mock_log.assert_awaited_once()
    call_args = mock_log.call_args
    assert call_args.kwargs.get("bytes_freed") is None


async def test_purge_superseded_passes_bytes_freed_to_log_compaction():
    """_run_memory_purge_superseded_job computes bytes_freed from facts table-size delta."""
    from butlers.scheduled_jobs import _run_memory_purge_superseded_job

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    size_sequence = [16384, 8192]

    with patch(
        "butlers.modules.memory.storage.purge_superseded_facts",
        new=AsyncMock(return_value={"deleted": 10, "deleted_ha_state": 2}),
    ):
        with patch(
            "butlers.scheduled_jobs._table_size_bytes",
            new=AsyncMock(side_effect=size_sequence),
        ):
            with patch(
                "butlers.scheduled_jobs._log_compaction",
                new=AsyncMock(),
            ) as mock_log:
                await _run_memory_purge_superseded_job(pool, None)

    mock_log.assert_awaited_once()
    call_args = mock_log.call_args
    assert call_args.args[1] == "fact"
    assert call_args.args[2] == 12  # 10 + 2
    assert call_args.kwargs.get("bytes_freed") == 8192  # 16384 - 8192


async def test_cleanup_does_not_log_when_nothing_removed():
    """_log_compaction is NOT called when no rows were removed."""
    from butlers.scheduled_jobs import _run_memory_episode_cleanup_job

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    with patch(
        "butlers.modules.memory.consolidation.run_episode_cleanup",
        new=AsyncMock(return_value={"expired_deleted": 0, "capacity_deleted": 0, "remaining": 100}),
    ):
        with patch(
            "butlers.scheduled_jobs._log_compaction",
            new=AsyncMock(),
        ) as mock_log:
            await _run_memory_episode_cleanup_job(pool, None)

    mock_log.assert_not_awaited()


async def test_table_size_bytes_returns_none_on_error():
    """_table_size_bytes returns None when pg_total_relation_size raises."""
    from butlers.scheduled_jobs import _table_size_bytes

    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=Exception("permission denied"))

    result = await _table_size_bytes(pool, "episodes")
    assert result is None


async def test_table_size_bytes_returns_none_for_missing_table():
    """_table_size_bytes returns None when to_regclass resolves to NULL."""
    from butlers.scheduled_jobs import _table_size_bytes

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)  # NULL from pg when table absent

    result = await _table_size_bytes(pool, "no_such_table")
    assert result is None


async def test_table_size_bytes_returns_size():
    """_table_size_bytes propagates the integer size from pg_total_relation_size."""
    from butlers.scheduled_jobs import _table_size_bytes

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=8192)

    result = await _table_size_bytes(pool, "episodes")
    assert result == 8192
