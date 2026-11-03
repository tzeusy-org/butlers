"""Batch-boundary paging for the UUID/composite-keyed Chronicler adapters (bu-f9ob67).

Seven adapters used to page ``ts > watermark ... LIMIT n``, so a run of rows
sharing the batch's last timestamp lost everything past the limit. They now page
by ``(ts, key) > cursor`` with a hard ``LIMIT``.

Each adapter's real ``_fetch_*`` query runs against its real table in a clone
migrated by the ``core`` Alembic chain (the root ``db_pool`` fixture), draining
a table whose same-timestamp run straddles several pages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

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


# ── Seeding the real core-migrated tables ──────────────────────────────────


def _uuid(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


@dataclass
class _Spec:
    name: str
    make_adapter: Callable[[], Any]
    fetch: str
    table: str
    ts_col: str
    row: Callable[[int, datetime], dict[str, Any]]
    noise: Callable[[int, datetime], dict[str, Any]] | None = None
    pass_connection: bool = False  # OwnTracks adapters read a pinned connection
    setup: str | None = None  # SQL run with ``$1 = _T2`` before seeding


def _spotify(i: int, ts: datetime) -> dict[str, Any]:
    return {
        "id": UUID(_uuid(i)),
        "idempotency_key": f"k{i}",
        "endpoint_identity": "e",
        "spotify_user_id": "u",
        "started_at": ts,
        "ended_at": ts,
        "duration_seconds": 1,
        "track_count": 1,
        "track_names": [],
        "context_uri": None,
        "context_name": None,
        "recorded_at": ts,
    }


def _comms(i: int, ts: datetime) -> dict[str, Any]:
    return {
        "id": UUID(_uuid(i)),
        "received_at": ts,
        "source_channel": _COMMS_CHANNELS[0],
        "source_provider": "p",
        "source_endpoint_identity": "e",
        "source_sender_identity": "a@example.com",
        "external_event_id": f"x{i}",
        "dedupe_key": f"d{i}",
        "dedupe_strategy": "s",
        "ingestion_tier": "full",
        "policy_tier": "default",
        "status": "ingested",
    }


def _filtered(i: int, ts: datetime, connector_type: str) -> dict[str, Any]:
    return {
        "id": UUID(_uuid(i)),
        "received_at": ts,
        "connector_type": connector_type,
        "endpoint_identity": "e",
        "external_message_id": f"m{i}",
        "source_channel": "home_assistant",
        "sender_identity": "s",
        "filter_reason": "r",
        "full_payload": {},
    }


_SPECS = [
    _Spec(
        "spotify",
        lambda: SpotifySessionAdapter(batch_limit=_BATCH),
        "_fetch_sessions",
        "connectors.spotify_listening_sessions",
        "recorded_at",
        _spotify,
    ),
    _Spec(
        "steam",
        lambda: SteamPlayAdapter(batch_limit=_BATCH),
        "_fetch_rows",
        "connectors.steam_play_history",
        "recorded_at",
        lambda i, ts: {
            "id": UUID(_uuid(i)),
            "steam_id": 76561,
            "steam_account_id": None,
            "app_id": i,  # the keyset tiebreaker is ``id``, not the natural key
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
        "occurred_at",
        lambda i, ts: {
            "id": UUID(_uuid(i)),
            "idempotency_key": f"k{i}",
            "channel": "telegram_user_client",
            "endpoint_identity": "e",
            "occurred_at": ts,
        },
    ),
    _Spec(
        "activitywatch",
        lambda: ActivityWatchWindowAdapter(batch_limit=_BATCH),
        "_fetch_events",
        "connectors.activitywatch_events",
        "ts",
        lambda i, ts: {
            "id": UUID(_uuid(i)),
            "idempotency_key": f"k{i}",
            "machine_id": "m",
            "bucket_id": "b",
            "app": "a",
            "ts": ts,
            "duration_seconds": 1.0,
            "app_class": "other",
            "browser_domain": None,
            "is_afk": False,
            "endpoint_identity": "e",
        },
    ),
    _Spec(
        "owntracks_place_cluster",
        lambda: OwnTracksPlaceClusterAdapter(batch_limit=_BATCH),
        "_fetch_points",
        "connectors.owntracks_points",
        "ts",
        lambda i, ts: {
            "id": UUID(_uuid(i)),
            "idempotency_key": f"k{i}",
            "ts": ts,
            "lat": 1.0,
            "lon": 2.0,
            "endpoint_identity": "e",
            "recorded_at": ts,
            "raw_payload": {},
        },
        pass_connection=True,
    ),
    _Spec(
        "home_assistant_sensor_activity",
        lambda: HomeAssistantSensorActivityAdapter(batch_limit=_BATCH),
        "_fetch_rows",
        "connectors.filtered_events",
        "received_at",
        lambda i, ts: _filtered(i, ts, _CONNECTOR_TYPE),
        noise=lambda i, ts: _filtered(i, ts, "not-" + _CONNECTOR_TYPE),
        setup="SELECT connectors.connectors_filtered_events_ensure_partition($1)",
    ),
]


async def _insert(pool: Any, table: str, rows: list[dict[str, Any]]) -> None:
    async with pool.acquire() as conn:
        for row in rows:
            marks = ", ".join(f"${n}" for n in range(1, len(row) + 1))
            await conn.execute(
                f"INSERT INTO {table} ({', '.join(row)}) VALUES ({marks})", *row.values()
            )


async def _seed(spec: _Spec, pool: Any) -> tuple[list[str], list[str]]:
    """Seed ``spec.table``; return ``(expected ids in (ts, id) order, noise ids)``."""
    plan = [(0, _T1), *[(n, _T2) for n in range(1, _RUN + 1)], (_RUN + 1, _T3)]
    expected = [_uuid(n) for n, _ in plan]
    # Insert out of order so ordering must come from the query, not the table.
    rows = [spec.row(n, ts) for n, ts in reversed(plan)]
    noise_ids: list[str] = []
    if spec.noise is not None:
        for n in (100, 101, 102):  # noise inside the T2 run and at its edges
            rows.append(spec.noise(n, _T2))
            noise_ids.append(_uuid(n))
    if spec.setup is not None:
        await pool.execute(spec.setup, _T2)
    await _insert(pool, spec.table, rows)
    return expected, noise_ids


def _identity(row: Any) -> str:
    return str(row["id"])


async def _fetch(spec: _Spec, adapter: Any, pool: Any, since: Any, since_key: Any) -> Any:
    if not spec.pass_connection:
        return await getattr(adapter, spec.fetch)(pool, since, since_key)
    async with pool.acquire() as conn:
        return await getattr(adapter, spec.fetch)(conn, since, since_key)


async def _drain(spec: _Spec, pool: Any) -> list[list[str]]:
    adapter = spec.make_adapter()
    since: datetime | None = None
    since_key: list[str] | None = None
    pages: list[list[str]] = []
    for _ in range(20):
        rows = await _fetch(spec, adapter, pool, since, since_key)
        if not rows:
            return pages
        pages.append([_identity(r) for r in rows])
        since = rows[-1][spec.ts_col]
        since_key = cursor_key(rows[-1], adapter.keyset_cols)
    raise AssertionError("paging did not terminate")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.name)
async def test_same_timestamp_run_across_batches_loses_and_repeats_nothing(spec, db_pool):
    expected, noise = await _seed(spec, db_pool)

    pages = await _drain(spec, db_pool)

    # Every row exactly once, in (ts, key) order, and no filtered-out row.
    assert [i for page in pages for i in page] == expected
    assert not set(noise) & {i for page in pages for i in page}
    # The cap is real: no page exceeds the batch size, and none is split short.
    assert all(len(page) <= _BATCH for page in pages)
    assert [len(p) for p in pages] == [_BATCH, _BATCH, _BATCH, 1]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.name)
async def test_checkpoint_without_tiebreaker_resumes_strictly_after_ts(spec, db_pool):
    """Stored pre-cursor checkpoints (no key) keep the old ``ts > watermark`` read."""
    expected, _ = await _seed(spec, db_pool)

    rows = await _fetch(spec, spec.make_adapter(), db_pool, _T2, None)

    assert [_identity(r) for r in rows] == expected[-1:]


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


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("crash_at", [0, 1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("crash_in", ["upsert_checkpoint", "set_cursor"])
async def test_crash_between_cursor_and_watermark_writes_loses_no_rows(crash_at, crash_in, db_pool):
    """A crash at any write of any run, then a retry, still projects every row.

    Two long same-timestamp runs (T2, T3) are drained in pages of 3. The fault
    fires on the ``crash_at``-th run's ``crash_in`` write, i.e. between the
    cursor write and the watermark write (or just before the cursor write).
    """
    from unittest.mock import AsyncMock, patch

    spec = _SPECS[0]  # spotify
    plan = [(0, _T1), *[(n, _T2) for n in range(1, 9)], *[(n, _T3) for n in range(9, 17)]]
    await _insert(db_pool, spec.table, [spec.row(n, ts) for n, ts in plan])
    pool = db_pool

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
        projected.append(str(row["id"]))

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
