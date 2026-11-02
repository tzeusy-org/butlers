"""Batch-boundary paging for the UUID/composite-keyed Chronicler adapters (bu-f9ob67).

Seven adapters used to page ``ts > watermark ... LIMIT n``, so a run of rows
sharing the batch's last timestamp lost everything past the limit. They now page
by ``(ts, key) > cursor`` with a hard ``LIMIT``.

Each adapter's real ``_fetch_*`` query is executed here on in-memory SQLite (the
generated SQL is portable once the Postgres-only casts and ``ANY`` are
rewritten), draining a table whose same-timestamp run straddles several pages.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from butlers.chronicler.adapters.activitywatch import ActivityWatchWindowAdapter
from butlers.chronicler.adapters.base import cursor_key, key_for_watermark, keyset_page
from butlers.chronicler.adapters.comms import _COMMS_CHANNELS, CommsSocialAdapter
from butlers.chronicler.adapters.home_assistant_sensor_activity import (
    _CONNECTOR_TYPE,
    HomeAssistantSensorActivityAdapter,
)
from butlers.chronicler.adapters.owner_outbound import OwnerOutboundMessageAdapter
from butlers.chronicler.adapters.owntracks_place_cluster import OwnTracksPlaceClusterAdapter
from butlers.chronicler.adapters.spotify import SpotifySessionAdapter
from butlers.chronicler.adapters.steam import SteamPlayAdapter

_T1 = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)
_T2 = _T1 + timedelta(hours=1)  # the same-timestamp run
_T3 = _T1 + timedelta(hours=2)
_BATCH = 3
_RUN = 8  # rows sharing _T2: spans three pages


# ── SQLite stand-in for the asyncpg surface the fetch methods use ──────────


def _uuid(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


sqlite3.register_adapter(datetime, lambda d: d.isoformat())
sqlite3.register_adapter(date, lambda d: d.isoformat())
sqlite3.register_converter("TIMESTAMPTZ", lambda b: datetime.fromisoformat(b.decode()))
sqlite3.register_converter("DATE", lambda b: date.fromisoformat(b.decode()))


class _Ctx:
    def __init__(self, obj: Any = None) -> None:
        self._obj = obj

    async def __aenter__(self) -> Any:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakeConn:
    """Executes the adapters' Postgres SQL on SQLite."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, *_: Any) -> bool:
        return True  # information_schema existence probe

    def transaction(self) -> _Ctx:
        return _Ctx()

    async def fetch(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        self.fetch_calls.append((sql, args))
        sql = re.sub(r"::text::\w+", "", sql)
        sql = re.sub(r"= ANY\(\$(\d+)::text\[\]\)", r"IN (SELECT value FROM json_each($\1))", sql)
        params = {
            str(i + 1): json.dumps(a) if isinstance(a, list) else a for i, a in enumerate(args)
        }
        return self._db.execute(sql, params).fetchall()


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _Ctx:
        return _Ctx(self._conn)


@dataclass
class _Spec:
    name: str
    make_adapter: Callable[[], Any]
    fetch: str
    table: str
    ddl: str
    ts_col: str
    row: Callable[[int, datetime], dict[str, Any]]
    noise: Callable[[int, datetime], dict[str, Any]] | None = None
    pass_connection: bool = False  # OwnTracks adapters read a pinned connection


def _spotify(i: int, ts: datetime) -> dict[str, Any]:
    return {
        "id": _uuid(i),
        "idempotency_key": f"k{i}",
        "endpoint_identity": "e",
        "spotify_user_id": "u",
        "started_at": ts,
        "ended_at": ts,
        "duration_seconds": 1,
        "track_count": 1,
        "track_names": "[]",
        "context_uri": None,
        "context_name": None,
        "recorded_at": ts,
    }


def _comms(i: int, ts: datetime) -> dict[str, Any]:
    return {
        "id": _uuid(i),
        "received_at": ts,
        "source_channel": _COMMS_CHANNELS[0],
        "source_sender_identity": "a@example.com",
        "status": "ingested",
    }


_SPECS = [
    _Spec(
        "spotify",
        lambda: SpotifySessionAdapter(batch_limit=_BATCH),
        "_fetch_sessions",
        "connectors.spotify_listening_sessions",
        "id TEXT, idempotency_key TEXT, endpoint_identity TEXT, spotify_user_id TEXT, "
        "started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, duration_seconds INT, track_count INT, "
        "track_names TEXT, context_uri TEXT, context_name TEXT, recorded_at TIMESTAMPTZ",
        "recorded_at",
        _spotify,
    ),
    _Spec(
        "steam",
        lambda: SteamPlayAdapter(batch_limit=_BATCH),
        "_fetch_rows",
        "connectors.steam_play_history",
        "id TEXT, steam_id INTEGER, steam_account_id TEXT, app_id INTEGER, app_name TEXT, "
        "date DATE, playtime_minutes INT, recorded_at TIMESTAMPTZ",
        "recorded_at",
        lambda i, ts: {
            "id": _uuid(i),
            "steam_id": 76561,
            "steam_account_id": None,
            "app_id": 1,  # (steam_id, app_id, date) repeats; only ``id`` is unique
            "app_name": "g",
            "date": date(2026, 3, 26),
            "playtime_minutes": 1,
            "recorded_at": ts,
        },
    ),
    _Spec(
        "comms",
        lambda: CommsSocialAdapter(batch_limit=_BATCH),
        "_fetch_events",
        "public.ingestion_events",
        "id TEXT, received_at TIMESTAMPTZ, source_channel TEXT, "
        "source_sender_identity TEXT, status TEXT",
        "received_at",
        _comms,
        # Every filter the query applies must be load-bearing.
        noise=lambda i, ts: {**_comms(i, ts), "status": "failed"},
    ),
    _Spec(
        "owner_outbound",
        lambda: OwnerOutboundMessageAdapter(batch_limit=_BATCH),
        "_fetch_events",
        "connectors.owner_outbound_events",
        "id TEXT, channel TEXT, occurred_at TIMESTAMPTZ",
        "occurred_at",
        lambda i, ts: {"id": _uuid(i), "channel": "telegram", "occurred_at": ts},
    ),
    _Spec(
        "activitywatch",
        lambda: ActivityWatchWindowAdapter(batch_limit=_BATCH),
        "_fetch_events",
        "connectors.activitywatch_events",
        "id TEXT, idempotency_key TEXT, ts TIMESTAMPTZ, duration_seconds REAL, app_class TEXT, "
        "browser_domain TEXT, is_afk INT, endpoint_identity TEXT",
        "ts",
        lambda i, ts: {
            "id": _uuid(i),
            "idempotency_key": f"k{i}",
            "ts": ts,
            "duration_seconds": 1.0,
            "app_class": "other",
            "browser_domain": None,
            "is_afk": 0,
            "endpoint_identity": "e",
        },
    ),
    _Spec(
        "owntracks_place_cluster",
        lambda: OwnTracksPlaceClusterAdapter(batch_limit=_BATCH),
        "_fetch_points",
        "connectors.owntracks_points",
        "id TEXT, ts TIMESTAMPTZ, lat REAL, lon REAL, endpoint_identity TEXT, "
        "recorded_at TIMESTAMPTZ, raw_payload TEXT",
        "ts",
        lambda i, ts: {
            "id": _uuid(i),
            "ts": ts,
            "lat": 1.0,
            "lon": 2.0,
            "endpoint_identity": "e",
            "recorded_at": ts,
            "raw_payload": "{}",
        },
        pass_connection=True,
    ),
    _Spec(
        "home_assistant_sensor_activity",
        lambda: HomeAssistantSensorActivityAdapter(batch_limit=_BATCH),
        "_fetch_rows",
        "connectors.filtered_events",
        "id TEXT, received_at TIMESTAMPTZ, full_payload TEXT, connector_type TEXT",
        "received_at",
        lambda i, ts: {
            "id": _uuid(i),
            "received_at": ts,
            "full_payload": "{}",
            "connector_type": _CONNECTOR_TYPE,
        },
        noise=lambda i, ts: {
            "id": _uuid(i),
            "received_at": ts,
            "full_payload": "{}",
            "connector_type": "not-" + _CONNECTOR_TYPE,
        },
    ),
]


def _seed(spec: _Spec) -> tuple[_FakeConn, list[str], list[str]]:
    """Return ``(conn, expected ids in (ts, id) order, noise ids)``."""
    db = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES)
    db.row_factory = sqlite3.Row
    schema, table = spec.table.split(".")
    db.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
    db.execute(f"CREATE TABLE {spec.table} ({spec.ddl})")

    plan = [(0, _T1), *[(n, _T2) for n in range(1, _RUN + 1)], (_RUN + 1, _T3)]
    expected = [_uuid(n) for n, _ in plan]
    # Insert out of order so ordering must come from the query, not the table.
    rows = [spec.row(n, ts) for n, ts in reversed(plan)]
    noise_ids: list[str] = []
    if spec.noise is not None:
        for n in (100, 101, 102):  # noise inside the T2 run and at its edges
            rows.append(spec.noise(n, _T2))
            noise_ids.append(_uuid(n))
    for row in rows:
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        db.execute(f"INSERT INTO {spec.table} ({cols}) VALUES ({marks})", row)
    return _FakeConn(db), expected, noise_ids


def _identity(spec: _Spec, row: sqlite3.Row) -> str:
    return row["id"]


async def _drain(spec: _Spec, conn: _FakeConn) -> list[list[str]]:
    adapter = spec.make_adapter()
    source = conn if spec.pass_connection else _FakePool(conn)
    since: datetime | None = None
    since_key: list[str] | None = None
    pages: list[list[str]] = []
    for _ in range(20):
        rows = await getattr(adapter, spec.fetch)(source, since, since_key)
        if not rows:
            return pages
        pages.append([_identity(spec, r) for r in rows])
        since = rows[-1][spec.ts_col]
        since_key = cursor_key(rows[-1], adapter.keyset_cols)
    raise AssertionError("paging did not terminate")


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.name)
async def test_same_timestamp_run_across_batches_loses_and_repeats_nothing(spec):
    conn, expected, noise = _seed(spec)

    pages = await _drain(spec, conn)

    # Every row exactly once, in (ts, key) order, and no filtered-out row.
    assert [i for page in pages for i in page] == expected
    assert not set(noise) & {i for page in pages for i in page}
    # The cap is real: no page exceeds the batch size, and none is split short.
    assert all(len(page) <= _BATCH for page in pages)
    assert [len(p) for p in pages] == [_BATCH, _BATCH, _BATCH, 1]


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.name)
async def test_checkpoint_without_tiebreaker_resumes_strictly_after_ts(spec):
    """Stored pre-cursor checkpoints (no key) keep the old ``ts > watermark`` read."""
    conn, expected, _ = _seed(spec)
    adapter = spec.make_adapter()
    source = conn if spec.pass_connection else _FakePool(conn)

    rows = await getattr(adapter, spec.fetch)(source, _T2, None)

    assert [_identity(spec, r) for r in rows] == expected[-1:]


def test_cursor_is_only_honoured_for_the_watermark_it_belongs_to():
    cursor = (_T2, ["k"])
    assert key_for_watermark(cursor, _T2) == ["k"]
    assert key_for_watermark(cursor, _T1) is None
    assert key_for_watermark(cursor, None) is None
    assert key_for_watermark(None, _T2) is None


def test_keyset_page_ignores_a_key_of_the_wrong_shape():
    sql, args = keyset_page(
        columns="id",
        table="t",
        ts_col="ts",
        key_cols=(("id", "uuid"),),
        since=_T2,
        since_key=["a", "b"],
        batch_limit=5,
    )
    assert "ts > $1" in sql and "(ts, id) >" not in sql
    assert args == [_T2, 5]


async def test_adapter_run_hands_the_stored_tiebreaker_to_project_and_saves_the_new_one():
    """``ProjectionAdapter.run`` threads ``since_key`` in and ``watermark_key`` out."""
    from unittest.mock import AsyncMock, patch

    from butlers.chronicler.adapters.base import AdapterResult
    from butlers.chronicler.models import ProjectionCheckpoint

    adapter = SpotifySessionAdapter(batch_limit=_BATCH)
    adapter.project = AsyncMock(
        return_value=AdapterResult(
            source_name=adapter.source_name, watermark=_T3, watermark_key=["new"]
        )
    )
    checkpoint = MagicMock(spec=ProjectionCheckpoint, watermark=_T2, watermark_id=None)
    base = "butlers.chronicler.adapters.base"
    with (
        patch(f"{base}.get_checkpoint", AsyncMock(return_value=checkpoint)),
        patch(f"{base}.get_cursor", AsyncMock(return_value=(_T2, ["old"]))),
        patch(f"{base}.mark_source_active", AsyncMock()),
        patch(f"{base}.upsert_checkpoint", AsyncMock()),
        patch(f"{base}.set_cursor", AsyncMock()) as set_cursor,
    ):
        await adapter.run(pool=MagicMock(), chronicler_pool=MagicMock())

    assert adapter.project.await_args.kwargs["since_key"] == ["old"]
    set_cursor.assert_awaited_once()
    assert set_cursor.await_args.args[2:] == (_T3, ["new"])


class _Crash(Exception):
    pass


@pytest.mark.parametrize("crash_at", [0, 1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("crash_in", ["upsert_checkpoint", "set_cursor"])
async def test_crash_between_cursor_and_watermark_writes_loses_no_rows(crash_at, crash_in):
    """A crash at any write of any run, then a retry, still projects every row.

    Two long same-timestamp runs (T2, T3) are drained in pages of 3. The fault
    fires on the ``crash_at``-th run's ``crash_in`` write, i.e. between the
    cursor write and the watermark write (or just before the cursor write).
    """
    from unittest.mock import AsyncMock, patch

    spec = _SPECS[0]  # spotify
    db = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES)
    db.row_factory = sqlite3.Row
    db.execute("ATTACH DATABASE ':memory:' AS connectors")
    db.execute(f"CREATE TABLE {spec.table} ({spec.ddl})")
    plan = [(0, _T1), *[(n, _T2) for n in range(1, 9)], *[(n, _T3) for n in range(9, 17)]]
    for n, ts in plan:
        row = spec.row(n, ts)
        marks = ", ".join(f":{c}" for c in row)
        db.execute(f"INSERT INTO {spec.table} ({', '.join(row)}) VALUES ({marks})", row)
    pool = _FakePool(_FakeConn(db))

    store: dict[str, Any] = {"wm": None, "cursor": None, "run": 0}
    projected: list[str] = []

    async def get_checkpoint(*_: Any) -> Any:
        return MagicMock(watermark=store["wm"], watermark_id=None)

    async def get_cursor(*_: Any) -> Any:
        return store["cursor"]

    async def set_cursor(_conn: Any, _name: str, ts: datetime, key: list[str]) -> None:
        if crash_in == "set_cursor" and store["run"] == crash_at:
            raise _Crash
        store["cursor"] = (ts, key)

    async def upsert_checkpoint(_conn: Any, _name: str, *, watermark: Any, **_: Any) -> None:
        if crash_in == "upsert_checkpoint" and store["run"] == crash_at:
            raise _Crash
        store["wm"] = watermark

    async def project_row(_pool: Any, row: Any, **_: Any) -> None:
        projected.append(row["id"])

    adapter = SpotifySessionAdapter(batch_limit=_BATCH)
    base = "butlers.chronicler.adapters.base"
    sp = "butlers.chronicler.adapters.spotify"
    with (
        patch(f"{base}.get_checkpoint", get_checkpoint),
        patch(f"{base}.get_cursor", get_cursor),
        patch(f"{base}.set_cursor", set_cursor),
        patch(f"{base}.upsert_checkpoint", upsert_checkpoint),
        patch(f"{base}.mark_source_active", AsyncMock()),
        patch(f"{sp}.resolve_owner_entity_id", AsyncMock(return_value=None)),
        patch.object(adapter, "_project_row", project_row),
    ):
        for _ in range(30):
            try:
                result = await adapter.run(pool=pool, chronicler_pool=MagicMock())
            except _Crash:
                result = None
            store["run"] += 1
            if result is not None and result.rows_projected == 0 and store["run"] > crash_at:
                break

    assert set(projected) == {_uuid(n) for n, _ in plan}
