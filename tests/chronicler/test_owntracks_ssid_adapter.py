"""Tests for deterministic OwnTracks Wi-Fi SSID presence projection."""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.chronicler.adapters import owntracks_ssid as adapter_module
from butlers.chronicler.adapters.base import AdapterResult
from butlers.chronicler.adapters.owntracks_ssid import (
    DEFAULT_MAX_GAP_MINUTES,
    EPISODE_TYPE_HOME_PRESENCE,
    EPISODE_TYPE_WORK_PRESENCE,
    SOURCE_NAME,
    SSID_PLACE_STATE_KEY,
    OwnTracksSsidPresenceAdapter,
    group_ssid_points,
    parse_ssid_places,
)
from butlers.chronicler.models import Confidence, Layer, Precision, Privacy
from butlers.chronicler.projection_coverage import (
    COVERAGE_REVISION_CARRYOVER_KEY,
    ProjectionCoverage,
)
from butlers.testing.fake_pool import AsyncCtx

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)
_ENDPOINT = "owntracks:alice"


class _TrackingAsyncCtx(AsyncCtx):
    def __init__(self, obj: object) -> None:
        super().__init__(obj)
        self.entered = False
        self.exited = False
        self.exit_type: type[BaseException] | None = None

    async def __aenter__(self) -> object:
        self.entered = True
        return await super().__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        *_: object,
    ) -> None:
        self.exited = True
        self.exit_type = exc_type


def _chronicler_conn() -> AsyncMock:
    """A Chronicler connection whose transaction() is a context, as project() now opens one."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncCtx(None))
    return conn


def _row(
    minute: int,
    *,
    ssid: str | None = "Corp WiFi",
    endpoint_identity: str = _ENDPOINT,
) -> dict:
    raw_payload = {"_type": "location"}
    if ssid is not None:
        raw_payload["SSID"] = ssid
    ts = _NOW + timedelta(minutes=minute)
    return {
        "id": uuid4(),
        "ts": ts,
        "endpoint_identity": endpoint_identity,
        "raw_payload": raw_payload,
        "recorded_at": ts,
    }


def test_parse_ssid_places_accepts_owner_state_mapping() -> None:
    assert parse_ssid_places({"Corp WiFi": "work", "Home WiFi": "home"}) == {
        "Corp WiFi": "work",
        "Home WiFi": "home",
    }


@pytest.mark.parametrize(
    "value,match",
    [
        (["Corp WiFi"], "JSON object"),
        ({"": "work"}, "non-empty SSID"),
        ({"Corp WiFi": "office"}, "home.*work"),
    ],
)
def test_parse_ssid_places_rejects_malformed_owner_state(value: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_ssid_places(value)


def test_contiguous_same_ssid_points_form_one_presence_span() -> None:
    spans, carryover = group_ssid_points(
        [_row(0), _row(10), _row(20)],
        ssid_places={"Corp WiFi": "work"},
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    assert len(spans) == 1
    assert spans[0].place == "work"
    assert spans[0].start_at == _NOW
    assert spans[0].end_at == _NOW + timedelta(minutes=20)
    assert spans[0].point_count == 3
    assert carryover[_ENDPOINT]["ssid"] == "Corp WiFi"


def test_gap_over_threshold_splits_same_ssid_into_two_presence_spans() -> None:
    spans, _ = group_ssid_points(
        [_row(0), _row(10), _row(90), _row(100)],
        ssid_places={"Corp WiFi": "work"},
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    assert [(span.start_at, span.end_at) for span in spans] == [
        (_NOW, _NOW + timedelta(minutes=10)),
        (_NOW + timedelta(minutes=90), _NOW + timedelta(minutes=100)),
    ]


def test_unlabelled_ssid_is_not_emitted_and_breaks_contiguity() -> None:
    spans, _ = group_ssid_points(
        [_row(0), _row(10), _row(20, ssid="Cafe WiFi"), _row(30), _row(40)],
        ssid_places={"Corp WiFi": "work"},
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    assert [(span.start_at, span.end_at, span.place) for span in spans] == [
        (_NOW, _NOW + timedelta(minutes=10), "work"),
        (_NOW + timedelta(minutes=30), _NOW + timedelta(minutes=40), "work"),
    ]


def test_missing_ssid_breaks_contiguity_and_singletons_do_not_claim_presence() -> None:
    spans, carryover = group_ssid_points(
        [_row(0), _row(10, ssid=None), _row(20)],
        ssid_places={"Corp WiFi": "work"},
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    assert spans == []
    assert carryover[_ENDPOINT]["point_count"] == 1


def test_json_string_raw_payload_is_decoded_for_grouping() -> None:
    rows = [_row(0), _row(10)]
    for row in rows:
        row["raw_payload"] = json.dumps(row["raw_payload"])

    spans, _ = group_ssid_points(
        rows,
        ssid_places={"Corp WiFi": "work"},
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    assert [(span.start_at, span.end_at, span.place) for span in spans] == [
        (_NOW, _NOW + timedelta(minutes=10), "work")
    ]


async def test_empty_poll_preserves_endpoint_carryover() -> None:
    prior_carryover = {
        "_source_cursor": {
            "watermark": _NOW.isoformat(),
            "uuid": "00000000-0000-0000-0000-000000000001",
        },
        _ENDPOINT: {
            "ssid": "Corp WiFi",
            "start_at": _NOW.isoformat(),
            "end_at": _NOW.isoformat(),
            "point_count": 1,
        },
    }
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})

    with (
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.get_carryover",
            new=AsyncMock(return_value=prior_carryover),
        ),
        patch.object(adapter, "_fetch_points", new=AsyncMock(return_value=[])),
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.save_carryover",
            new=AsyncMock(),
        ) as save,
    ):
        await adapter.project(AsyncMock(), chronicler_pool=_chronicler_conn(), since=_NOW)

    saved = save.await_args.args[2]
    assert saved[_ENDPOINT] == prior_carryover[_ENDPOINT]


async def test_all_malformed_batch_preserves_endpoint_carryover() -> None:
    prior_carryover = {
        "_source_cursor": {
            "watermark": _NOW.isoformat(),
            "uuid": "00000000-0000-0000-0000-000000000001",
        },
        _ENDPOINT: {
            "ssid": "Corp WiFi",
            "start_at": _NOW.isoformat(),
            "end_at": _NOW.isoformat(),
            "point_count": 1,
        },
    }
    malformed_row = _row(10, endpoint_identity="")
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})

    with (
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.get_carryover",
            new=AsyncMock(return_value=prior_carryover),
        ),
        patch.object(adapter, "_fetch_points", new=AsyncMock(return_value=[malformed_row])),
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.save_carryover",
            new=AsyncMock(),
        ) as save,
    ):
        result = await adapter.project(AsyncMock(), chronicler_pool=_chronicler_conn(), since=_NOW)

    saved = save.await_args.args[2]
    assert saved[_ENDPOINT] == prior_carryover[_ENDPOINT]
    assert saved["_source_cursor"] == {
        "watermark": malformed_row["ts"].isoformat(),
        "uuid": str(malformed_row["id"]),
    }
    assert result.watermark == _NOW + timedelta(minutes=10)


async def test_unavailable_source_does_not_tombstone_mapping_replay_episodes() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "home"})
    prior_carryover = {"_mapping_digest": "previous-mapping"}

    with (
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.get_carryover",
            new=AsyncMock(return_value=prior_carryover),
        ),
        patch.object(adapter, "_fetch_points", new=AsyncMock(return_value=None)),
        patch.object(
            adapter,
            "_tombstone_stale_mapping_episodes",
            new=AsyncMock(),
        ) as tombstone,
    ):
        result = await adapter.project(AsyncMock(), chronicler_pool=_chronicler_conn(), since=_NOW)

    assert result.skipped
    tombstone.assert_not_awaited()


@pytest.mark.parametrize(
    ("mapping_changed", "stored_revision", "voided"),
    [
        (True, "current", True),
        (False, "current", False),
        (False, "other", True),  # only a grouping setting moved: no replay, still void
        (False, None, True),  # spans stored before the revision was persisted
    ],
)
async def test_a_revision_change_voids_the_held_presence_spans(
    mapping_changed: bool, stored_revision: str | None, voided: bool
) -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "home"})
    current = adapter_module._mapping_digest(adapter.ssid_places)
    prior_carryover: dict[str, object] = {
        "_mapping_digest": "previous-mapping" if mapping_changed else current
    }
    if stored_revision is not None:
        prior_carryover[COVERAGE_REVISION_CARRYOVER_KEY] = (
            adapter.coverage_revision
            if stored_revision == "current"
            else OwnTracksSsidPresenceAdapter(
                ssid_places={"Corp WiFi": "home"}, max_gap_minutes=DEFAULT_MAX_GAP_MINUTES + 1
            ).coverage_revision
        )
    saved = AsyncMock()

    with (
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.get_carryover",
            new=AsyncMock(return_value=prior_carryover),
        ),
        patch.object(adapter, "_fetch_points", new=AsyncMock(return_value=[])),
        patch.object(adapter, "_tombstone_stale_mapping_episodes", new=AsyncMock()),
        patch("butlers.chronicler.adapters.owntracks_ssid.save_carryover", new=saved),
        patch.object(ProjectionCoverage, "reset_prefix", autospec=True) as reset,
        patch.object(ProjectionCoverage, "flush", new=AsyncMock()) as flush,
    ):
        await adapter.project(AsyncMock(), chronicler_pool=_chronicler_conn(), since=_NOW)

    resets = {call.args[1] for call in reset.call_args_list}
    assert resets == ({adapter_module.PRESENCE_CARRY_PREFIX} if voided else set())
    flush.assert_awaited_once()
    carryover = saved.await_args.args[2]
    assert carryover[COVERAGE_REVISION_CARRYOVER_KEY] == adapter.coverage_revision


async def test_a_forgotten_fix_never_joins_an_ssid_presence_span_again() -> None:
    """S4b (2): the resurrection floor runs before grouping, carry and coverage."""
    rows = [
        {**_row(m), "idempotency_key": f"owntracks:alice:{m}:location"} for m in (0, 10, 20, 30)
    ]
    forgotten = "connectors.owntracks_points:owntracks:alice:10:location"
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    spans: list[object] = []
    observed: list[object] = []

    async def _forgotten(_conn: object, source_ref: str) -> bool:
        return source_ref == forgotten

    async def _upsert(_conn: object, span: object, *, entity_id: object) -> object:
        spans.append(span)
        return MagicMock(id=uuid4())

    def _observe(_coverage: object, row: dict) -> None:
        observed.append(row["id"])

    with (
        patch(
            "butlers.chronicler.location_forgetting.owntracks_point_forgotten",
            side_effect=_forgotten,
        ),
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.get_carryover",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch.object(adapter, "_fetch_points", new=AsyncMock(return_value=rows)),
        patch.object(adapter, "_upsert_presence_episode", side_effect=_upsert),
        patch.object(ProjectionCoverage, "observe", autospec=True, side_effect=_observe),
        patch.object(ProjectionCoverage, "flush", new=AsyncMock()),
        patch("butlers.chronicler.adapters.owntracks_ssid.save_carryover", new=AsyncMock()) as save,
    ):
        result = await adapter.project(AsyncMock(), chronicler_pool=_chronicler_conn(), since=None)

    assert result.rows_refused_forgotten == 1
    assert rows[1]["id"] not in observed and len(observed) == 3
    assert [span.point_count for span in spans] == [3]
    saved = json.dumps(save.await_args.args[2], default=str)
    assert ":10:location" not in saved
    assert result.warnings == ["OwnTracks forgotten sources not projected again: 1."]


async def test_legacy_timestamp_checkpoint_replays_from_start() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = MagicMock(spec=asyncpg.Pool)
    pool.acquire.side_effect = lambda: AsyncCtx(conn)

    await adapter._fetch_points(pool, _NOW, since_uuid=None)

    sql, *args = conn.fetch.await_args.args
    assert "WHERE" not in sql
    assert args == [adapter.batch_limit]


async def test_uuid_checkpoint_uses_deterministic_tuple_boundary() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = MagicMock(spec=asyncpg.Pool)
    pool.acquire.side_effect = lambda: AsyncCtx(conn)
    since_uuid = UUID("00000000-0000-0000-0000-000000000002")

    await adapter._fetch_points(pool, _NOW, since_uuid=since_uuid)

    sql, *args = conn.fetch.await_args.args
    assert "WHERE (ts, id) > ($1, $2)" in sql
    assert args == [_NOW, since_uuid, adapter.batch_limit]


def test_matching_uuid_cursor_decodes_tiebreaker() -> None:
    expected = UUID("00000000-0000-0000-0000-000000000002")
    carryover = {
        "_source_cursor": {
            "watermark": _NOW.isoformat(),
            "uuid": str(expected),
        }
    }

    assert OwnTracksSsidPresenceAdapter._uuid_tiebreaker(carryover, _NOW) == expected


@pytest.mark.parametrize(
    "cursor",
    [
        {"watermark": "not-a-timestamp", "uuid": "not-a-uuid"},
        {
            "watermark": (_NOW - timedelta(minutes=1)).isoformat(),
            "uuid": "00000000-0000-0000-0000-000000000002",
        },
    ],
    ids=["malformed", "watermark-mismatch"],
)
def test_invalid_uuid_cursor_requests_fail_safe_replay(cursor: dict[str, str]) -> None:
    assert (
        OwnTracksSsidPresenceAdapter._uuid_tiebreaker(
            {"_source_cursor": cursor},
            _NOW,
        )
        is None
    )


async def test_project_outside_run_takes_the_projection_lock_first() -> None:
    """S4b (6): a direct project() call serializes with run() and retention preparation."""
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    order: list[str] = []
    conn = AsyncMock()
    transaction = _TrackingAsyncCtx(None)
    conn.transaction = MagicMock(return_value=transaction)

    async def _execute(query: str, *args: object) -> str:
        order.append("lock" if "advisory" in query and transaction.entered else query)
        return ""

    async def _carryover(*_: object) -> dict:
        order.append("read")
        return {}

    conn.execute = AsyncMock(side_effect=_execute)
    with (
        patch("butlers.chronicler.adapters.owntracks_ssid.get_carryover", side_effect=_carryover),
        patch.object(adapter, "_fetch_points", new=AsyncMock(return_value=[])),
        patch("butlers.chronicler.adapters.owntracks_ssid.save_carryover", new=AsyncMock()),
        patch.object(ProjectionCoverage, "flush", new=AsyncMock()),
    ):
        await adapter.project(AsyncMock(), chronicler_pool=conn, since=None)

    assert order[:2] == ["lock", "read"]
    conn.execute.assert_any_await(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
        "chronicler.projection:" + SOURCE_NAME,
    )
    assert transaction.exited


async def test_run_uses_one_transactional_chronicler_connection() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    adapter.project = AsyncMock(return_value=AdapterResult(source_name=SOURCE_NAME, watermark=_NOW))
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    transaction = _TrackingAsyncCtx(None)
    conn.transaction = MagicMock(return_value=transaction)
    acquire = _TrackingAsyncCtx(conn)
    pool = MagicMock()
    pool.acquire.return_value = acquire
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()

    result = await adapter.run(pool=pool, chronicler_pool=pool)

    assert result.watermark == _NOW
    adapter.project.assert_awaited_once_with(
        conn,
        chronicler_pool=conn,
        since=None,
        since_id=None,
    )
    assert transaction.entered and transaction.exited
    assert acquire.entered and acquire.exited
    # Lock, attempt record, source state, checkpoint: all on the one transaction.
    assert conn.execute.await_count == 4
    attempt = next(call.args for call in conn.execute.await_args_list if "attempts" in call.args[0])
    assert attempt[1:4] == (SOURCE_NAME, adapter.coverage_revision, "succeeded")
    assert "location_projection_coverage" in conn.fetch.await_args.args[0]  # discovery
    pool.execute.assert_not_awaited()


async def test_run_locks_source_before_reading_checkpoint() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    adapter.project = AsyncMock(return_value=AdapterResult(source_name=SOURCE_NAME, watermark=_NOW))
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    transaction = _TrackingAsyncCtx(None)
    conn.transaction = MagicMock(return_value=transaction)
    pool = MagicMock()
    pool.acquire.return_value = _TrackingAsyncCtx(conn)

    await adapter.run(pool=pool, chronicler_pool=pool)

    lock_sql, lock_key = conn.execute.await_args_list[0].args
    assert "pg_advisory_xact_lock" in lock_sql
    # The key the lock has always hashed; retention preparation takes the same one.
    assert lock_key == "chronicler.projection:" + SOURCE_NAME
    assert conn.fetchrow.await_args_list[0].args == (
        "SELECT * FROM projection_checkpoints WHERE source_name = $1 AND subsource = ''",
        SOURCE_NAME,
    )


async def test_run_releases_transaction_and_connection_before_reporting_failure() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    adapter.project = AsyncMock(side_effect=RuntimeError("injected persistence failure"))
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    transaction = _TrackingAsyncCtx(None)
    conn.transaction = MagicMock(return_value=transaction)
    acquire = _TrackingAsyncCtx(conn)
    pool = MagicMock()
    pool.acquire.return_value = acquire

    async def record_failure_after_cleanup(*_: object, **__: object) -> None:
        assert transaction.exited
        assert acquire.exited

    pool.execute = AsyncMock(side_effect=record_failure_after_cleanup)

    result = await adapter.run(pool=pool, chronicler_pool=pool)

    assert result.error == "injected persistence failure"
    assert transaction.exited and transaction.exit_type is RuntimeError
    assert acquire.exited and acquire.exit_type is RuntimeError
    assert "pg_advisory_xact_lock" in conn.execute.await_args_list[0].args[0]
    assert conn.execute.await_count == 1
    # The failed checkpoint and the failed attempt, both after the rollback.
    assert pool.execute.await_count == 2
    attempt = pool.execute.await_args_list[1].args
    assert "location_projection_attempts" in attempt[0]
    assert attempt[1:5] == (SOURCE_NAME, adapter.coverage_revision, "failed", "RuntimeError")


async def test_run_preserves_projection_error_when_failure_metadata_write_fails() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    adapter.project = AsyncMock(side_effect=RuntimeError("original projection failure"))
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_TrackingAsyncCtx(None))
    pool = MagicMock()
    pool.acquire.return_value = _TrackingAsyncCtx(conn)
    pool.execute = AsyncMock(side_effect=RuntimeError("failure metadata unavailable"))

    result = await adapter.run(pool=pool, chronicler_pool=pool)

    assert result.error == "original projection failure"
    # A failed checkpoint write does not stop the attempt record from being tried.
    assert pool.execute.await_count == 2


async def test_a_skipped_run_records_an_unavailable_source_as_a_failed_attempt() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    adapter.project = AsyncMock(
        return_value=AdapterResult(source_name=SOURCE_NAME, skipped=True, skipped_reason="gone")
    )
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_TrackingAsyncCtx(None))
    pool = MagicMock()
    pool.acquire.return_value = _TrackingAsyncCtx(conn)

    await adapter.run(pool=pool, chronicler_pool=pool)

    attempt = next(call.args for call in conn.execute.await_args_list if "attempts" in call.args[0])
    assert attempt[1:5] == (
        SOURCE_NAME,
        adapter.coverage_revision,
        "failed",
        "source_unavailable",
    )


async def test_run_cancellation_rolls_back_and_releases_without_failure_write() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    adapter.project = AsyncMock(side_effect=asyncio.CancelledError())
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    transaction = _TrackingAsyncCtx(None)
    conn.transaction = MagicMock(return_value=transaction)
    acquire = _TrackingAsyncCtx(conn)
    pool = MagicMock()
    pool.acquire.return_value = acquire
    pool.execute = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await adapter.run(pool=pool, chronicler_pool=pool)

    assert transaction.exited and transaction.exit_type is asyncio.CancelledError
    assert acquire.exited and acquire.exit_type is asyncio.CancelledError
    assert "pg_advisory_xact_lock" in conn.execute.await_args_list[0].args[0]
    assert conn.execute.await_count == 1
    pool.execute.assert_not_awaited()


async def test_upsert_work_presence_stamps_minute_activity_and_medium_confidence() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    spans, _ = group_ssid_points(
        [_row(0), _row(15)],
        ssid_places=adapter.ssid_places,
        max_gap=adapter.max_gap,
    )
    conn = AsyncMock()
    owner_id = uuid4()

    async def _return_episode(_conn: object, episode: object) -> object:
        episode.id = uuid4()
        return episode

    with (
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.upsert_episode",
            side_effect=_return_episode,
        ) as upsert,
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.upsert_owner_episode_entity",
            new=AsyncMock(),
        ) as owner_link,
    ):
        episode = await adapter._upsert_presence_episode(conn, spans[0], entity_id=owner_id)

    projected = upsert.await_args.args[1]
    assert projected.episode_type == EPISODE_TYPE_WORK_PRESENCE
    assert projected.precision == Precision.MINUTE
    assert projected.layer == Layer.ACTIVITY
    assert projected.confidence == Confidence.MEDIUM
    assert projected.privacy == Privacy.NORMAL
    assert projected.payload == {
        "place": "work",
        "point_count": 2,
        "endpoint_identity": _ENDPOINT,
    }
    assert "Corp WiFi" not in str(projected.payload)
    assert "Corp WiFi" not in projected.source_ref
    owner_link.assert_awaited_once_with(conn, episode.id, owner_id=owner_id)


async def test_upsert_home_presence_uses_distinct_episode_type() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Home WiFi": "home"})
    spans, _ = group_ssid_points(
        [_row(0, ssid="Home WiFi"), _row(5, ssid="Home WiFi")],
        ssid_places=adapter.ssid_places,
        max_gap=adapter.max_gap,
    )
    conn = AsyncMock()

    async def _return_episode(_conn: object, episode: object) -> object:
        episode.id = uuid4()
        return episode

    with (
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.upsert_episode",
            side_effect=_return_episode,
        ) as upsert,
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
    ):
        await adapter._upsert_presence_episode(conn, spans[0], entity_id=None)

    assert upsert.await_args.args[1].episode_type == EPISODE_TYPE_HOME_PRESENCE


def test_ssid_adapter_has_no_llm_imports() -> None:
    import butlers.chronicler.adapters.owntracks_ssid as mod

    source_path = Path(mod.__file__ or "")
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    forbidden = ("anthropic", "openai", "langchain", "litellm", "llm")
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [name for name in imported if name.startswith(forbidden)]


def test_state_key_is_scoped_to_chronicler_owntracks_config() -> None:
    assert SSID_PLACE_STATE_KEY == "chronicler/owntracks/ssid_places"


def test_source_and_episode_identifiers_are_distinct_from_gps_place_cluster() -> None:
    assert SOURCE_NAME == "owntracks.ssid_presence"
    assert EPISODE_TYPE_HOME_PRESENCE != "place_episode"
    assert EPISODE_TYPE_WORK_PRESENCE != "place_episode"
