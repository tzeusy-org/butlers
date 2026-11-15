"""The OwnTracks run boundary commits a coverage attempt record for every outcome (bu-s11n0s.7).

``run_owntracks_projection`` (points and place cluster) records the attempt on the run's
transaction: after a failed projection's savepoint rolled back, after a skip for an unavailable
source, and after a success together with discovery's count of uncovered settled rows. The
real-Postgres proof is in ``tests/integration/test_owntracks_retention_coverage_integration.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.chronicler.adapters.base import AdapterResult
from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.owntracks_place_cluster import OwnTracksPlaceClusterAdapter
from butlers.chronicler.storage import run_owntracks_projection
from butlers.testing.fake_pool import AsyncCtx

pytestmark = pytest.mark.unit

_REVISION = OwnTracksPointAdapter().coverage_revision


def _conn(uncovered: int = 0) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.transaction = MagicMock(side_effect=lambda: AsyncCtx(None))
    conn.fetch = AsyncMock(return_value=[{"id": n} for n in range(uncovered)])
    return conn


def _attempts(conn: AsyncMock) -> list[tuple]:
    return [
        call.args
        for call in conn.execute.await_args_list
        if "location_projection_attempts" in call.args[0]
    ]


async def _run(conn: AsyncMock, project: AsyncMock) -> AdapterResult:
    return await run_owntracks_projection(
        conn,
        "owntracks.points",
        project,
        lambda: None,
        coverage_revision=_REVISION,
    )


async def test_a_successful_run_records_its_discovery_count() -> None:
    conn = _conn(uncovered=3)
    result = await _run(conn, AsyncMock(return_value=AdapterResult(source_name="owntracks.points")))
    assert result.success
    ((_sql, *args),) = _attempts(conn)
    assert args == ["owntracks.points", _REVISION, "succeeded", None, 3, False]
    discovery = conn.fetch.await_args.args
    assert discovery[1:3] == ("owntracks.points", _REVISION)


async def test_a_failed_run_records_the_exception_class_after_its_savepoint() -> None:
    conn = _conn()
    result = await _run(conn, AsyncMock(side_effect=TimeoutError("lat=1.3 lon=103.8")))
    assert result.error == "lat=1.3 lon=103.8"
    ((_sql, *args),) = _attempts(conn)
    assert args == ["owntracks.points", _REVISION, "failed", "TimeoutError", None, None]
    conn.fetch.assert_not_awaited()  # no discovery for a failed run


async def test_a_skipped_run_records_an_unavailable_source() -> None:
    conn = _conn()
    skipped = AdapterResult(source_name="owntracks.points", skipped=True, skipped_reason="gone")
    await _run(conn, AsyncMock(return_value=skipped))
    ((_sql, *args),) = _attempts(conn)
    assert args == ["owntracks.points", _REVISION, "failed", "source_unavailable", None, None]


def test_the_points_revision_moves_with_its_settings() -> None:
    assert OwnTracksPointAdapter().coverage_revision == _REVISION
    assert OwnTracksPointAdapter(movement_gap_minutes=45).coverage_revision != _REVISION
    assert OwnTracksPointAdapter(clock_skew_threshold_hours=2).coverage_revision != _REVISION
    # Batch size and settle time change pacing, not decisions.
    assert OwnTracksPointAdapter(batch_limit=10, ingest_settle_seconds=5).coverage_revision == (
        _REVISION
    )


async def test_both_owntracks_run_paths_pass_their_own_revision(monkeypatch) -> None:
    seen: list[tuple[str, str]] = []

    async def fake_run(pool, source_name, project, probe, *, coverage_revision):
        seen.append((source_name, coverage_revision))
        return AdapterResult(source_name=source_name)

    for module in (
        "butlers.chronicler.adapters.owntracks",
        "butlers.chronicler.adapters.owntracks_place_cluster",
    ):
        monkeypatch.setattr(f"{module}.run_owntracks_projection", fake_run)
        monkeypatch.setattr(f"{module}.resolve_owner_entity_id", AsyncMock(return_value=None))
    points, place = OwnTracksPointAdapter(), OwnTracksPlaceClusterAdapter()
    pool = MagicMock()
    await points.run(pool=pool, chronicler_pool=pool)
    await place.run(pool=pool, chronicler_pool=pool)
    assert seen == [
        ("owntracks.points", points.coverage_revision),
        ("owntracks.place_cluster", place.coverage_revision),
    ]
