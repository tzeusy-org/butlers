"""Batch-boundary paging for UUID-keyed Chronicler adapters (bu-f9ob67).

Eight adapters page on a single timestamp watermark (``ts > $1 ... LIMIT n``).
When a run of rows shares the batch's last timestamp, the plain form drops
the rows past the limit. ``timestamp_complete_page_sql`` must return the whole
timestamp run instead.

The helper's SQL is plain CTE/subquery SQL, so the semantics are exercised for
real against in-memory SQLite (``$n`` placeholders bind by name). Each adapter
is then pinned to the helper so none can slide back to a bare ``LIMIT``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.chronicler.adapters.activitywatch import ActivityWatchWindowAdapter
from butlers.chronicler.adapters.base import timestamp_complete_page_sql
from butlers.chronicler.adapters.comms import CommsSocialAdapter
from butlers.chronicler.adapters.home_assistant_sensor_activity import (
    HomeAssistantSensorActivityAdapter,
)
from butlers.chronicler.adapters.owner_outbound import OwnerOutboundMessageAdapter
from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.owntracks_place_cluster import OwnTracksPlaceClusterAdapter
from butlers.chronicler.adapters.spotify import SpotifySessionAdapter
from butlers.chronicler.adapters.steam import SteamPlayAdapter

# ── Real-semantics test of the shared helper ───────────────────────────────


def _page(db: sqlite3.Connection, since: int | None, limit: int, flag: int = 1) -> list[tuple]:
    where = "flag = $1" if since is None else "flag = $1 AND ts > $2"
    limit_param = 2 if since is None else 3
    sql = timestamp_complete_page_sql(
        columns="id, ts",
        table="src",
        ts_col="ts",
        order_by="ts ASC, id ASC",
        limit_param=limit_param,
        where=where,
    )
    params = {"1": flag, "2": since if since is not None else limit, "3": limit}
    return db.execute(sql, params).fetchall()


def _drain(db: sqlite3.Connection, limit: int) -> list[str]:
    seen: list[str] = []
    since: int | None = None
    for _ in range(50):
        rows = _page(db, since, limit)
        if not rows:
            return seen
        seen.extend(r[0] for r in rows)
        since = max(r[1] for r in rows)
    raise AssertionError("paging did not terminate")


@pytest.mark.parametrize("run_length", [1, 3, 4, 5, 9])
def test_same_timestamp_run_across_batch_boundary_loses_and_repeats_nothing(run_length):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE src (id TEXT, ts INTEGER, flag INTEGER)")
    expected = []
    for n in range(3):  # leading distinct rows
        expected.append(f"a{n}")
        db.execute("INSERT INTO src VALUES (?, ?, 1)", (f"a{n}", n))
    for n in range(run_length):  # run sharing one ts, straddling the limit of 4
        expected.append(f"b{n}")
        db.execute("INSERT INTO src VALUES (?, 10, 1)", (f"b{n}",))
    expected.append("c0")
    db.execute("INSERT INTO src VALUES ('c0', 11, 1)")
    db.execute("INSERT INTO src VALUES ('skip', 10, 0)")  # filtered out

    assert _drain(db, limit=4) == expected


def test_empty_page_returns_nothing():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE src (id TEXT, ts INTEGER, flag INTEGER)")
    assert _page(db, None, 4) == []


# ── Every affected adapter pages through the helper ────────────────────────


class _Ctx:
    def __init__(self, obj: object) -> None:
        self.obj = obj

    async def __aenter__(self) -> object:
        return self.obj

    async def __aexit__(self, *_: object) -> None:
        return None


def _recording_pool() -> tuple[MagicMock, AsyncMock]:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    conn.transaction = MagicMock(return_value=_Ctx(None))
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx(conn))
    return pool, conn


_SINCE = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)

_FETCHERS = {
    "spotify": (SpotifySessionAdapter, "_fetch_sessions", "recorded_at"),
    "steam": (SteamPlayAdapter, "_fetch_rows", "recorded_at"),
    "comms": (CommsSocialAdapter, "_fetch_events", "received_at"),
    "owner_outbound": (OwnerOutboundMessageAdapter, "_fetch_events", "occurred_at"),
    "activitywatch": (ActivityWatchWindowAdapter, "_fetch_events", "ts"),
    "owntracks": (OwnTracksPointAdapter, "_fetch_points", "ts"),
    "owntracks_place_cluster": (OwnTracksPlaceClusterAdapter, "_fetch_points", "ts"),
    "home_assistant_sensor_activity": (
        HomeAssistantSensorActivityAdapter,
        "_fetch_rows",
        "received_at",
    ),
}


@pytest.mark.parametrize("since", [None, _SINCE], ids=["first-run", "resume"])
@pytest.mark.parametrize("name", sorted(_FETCHERS))
async def test_adapter_page_query_is_timestamp_complete(name, since):
    cls, method, ts_col = _FETCHERS[name]
    pool, conn = _recording_pool()
    adapter = cls(batch_limit=7)

    # The OwnTracks adapters read through a pinned connection, not a pool.
    source = conn if name.startswith("owntracks") else pool
    await getattr(adapter, method)(source, since)

    sql = conn.fetch.await_args.args[0]
    assert "WITH page AS" in sql
    assert f"{ts_col} <= (SELECT max({ts_col}) FROM page)" in sql
    assert 7 in conn.fetch.await_args.args[1:]
    if since is not None:
        assert since in conn.fetch.await_args.args[1:]
        assert f"{ts_col} > $" in sql
