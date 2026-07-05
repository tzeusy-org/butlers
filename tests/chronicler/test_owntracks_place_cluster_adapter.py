"""Tests for the OwnTracks GPS place-cluster Chronicler projection adapter.

Covers:
- Pure ``cluster_points`` clustering: radius/dwell edges, singleton points,
  teleport outliers, endpoint changes, gap-based new clusters.
- Cross-batch carryover continuation (and rejection when discontinuous).
- Reference-point labeling (``parse_place_references`` + ``_label_for``),
  including the ``place_unknown`` fallback.
- Adapter-level ``project()`` orchestration: missing-table degrade, watermark
  advance, dwell-gated emission, evidence_refs lookup.
- Source-scan guardrail: no LLM imports in adapters/owntracks_place_cluster.py.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.adapters.owntracks_place_cluster import (
    DEFAULT_MAX_GAP_MINUTES,
    DEFAULT_MIN_DWELL_MINUTES,
    DEFAULT_RADIUS_METERS,
    EPISODE_TYPE_PLACE,
    PLACE_UNKNOWN_LABEL,
    SOURCE_NAME,
    ClusterSpan,
    OwnTracksPlaceClusterAdapter,
    PlaceReference,
    cluster_points,
    haversine_meters,
    parse_place_references,
)
from butlers.chronicler.models import Episode

_NOW = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)
_ENDPOINT = "owntracks:alice"

# Home coordinates (Singapore-ish) — used as a stable cluster centroid.
_HOME_LAT = 1.30000
_HOME_LON = 103.80000

# ~1.1km away — well outside the default 150m radius.
_AWAY_LAT = 1.31000
_AWAY_LON = 103.80000


def _row(
    ts: datetime,
    *,
    lat: float = _HOME_LAT,
    lon: float = _HOME_LON,
    endpoint_identity: str = _ENDPOINT,
    row_id: str = "row",
) -> dict:
    return {
        "id": row_id,
        "ts": ts,
        "lat": lat,
        "lon": lon,
        "endpoint_identity": endpoint_identity,
        "recorded_at": ts,
    }


# ---------------------------------------------------------------------------
# Source-scan guardrail: no LLM imports
# ---------------------------------------------------------------------------


def test_no_llm_imports_in_place_cluster_adapter() -> None:
    """The place-cluster adapter module must not import any LLM client packages."""
    import butlers.chronicler.adapters.owntracks_place_cluster as mod

    source_path = mod.__file__
    assert source_path is not None

    with open(source_path) as fh:
        tree = ast.parse(fh.read(), filename=source_path)

    forbidden_prefixes = ("anthropic", "openai", "langchain", "litellm", "llm")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden_prefixes:
                    assert not alias.name.startswith(prefix), (
                        f"LLM import detected in place-cluster adapter: {alias.name!r}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for prefix in forbidden_prefixes:
                    assert not node.module.startswith(prefix), (
                        f"LLM import detected in place-cluster adapter: {node.module!r}"
                    )


# ---------------------------------------------------------------------------
# haversine_meters
# ---------------------------------------------------------------------------


def test_haversine_zero_distance_for_identical_points() -> None:
    assert haversine_meters(_HOME_LAT, _HOME_LON, _HOME_LAT, _HOME_LON) == pytest.approx(0.0)


def test_haversine_distance_roughly_matches_known_separation() -> None:
    # ~0.01 deg latitude separation at the equatorial-ish region is ~1.1km.
    dist = haversine_meters(_HOME_LAT, _HOME_LON, _AWAY_LAT, _AWAY_LON)
    assert 1000 < dist < 1200


# ---------------------------------------------------------------------------
# cluster_points — pure clustering function
# ---------------------------------------------------------------------------


def test_singleton_point_forms_a_zero_dwell_cluster() -> None:
    """A single isolated point forms a cluster, but with zero dwell — the
    caller's dwell-threshold gate (not cluster_points itself) is what
    prevents a place_episode from being fabricated for it."""
    rows = [_row(_NOW)]
    spans, carryover = cluster_points(
        rows, radius_m=DEFAULT_RADIUS_METERS, max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES)
    )
    assert len(spans) == 1
    assert spans[0].dwell == timedelta(0)
    assert spans[0].point_count == 1
    assert _ENDPOINT in carryover


def test_contiguous_points_within_radius_form_one_cluster_meeting_dwell() -> None:
    rows = [_row(_NOW + timedelta(minutes=m)) for m in range(0, 25, 5)]  # 0..20 min, 25 min dwell
    spans, _ = cluster_points(
        rows, radius_m=DEFAULT_RADIUS_METERS, max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES)
    )
    assert len(spans) == 1
    span = spans[0]
    assert span.point_count == 5
    assert span.dwell == timedelta(minutes=20)
    assert span.start_at == _NOW
    assert span.end_at == _NOW + timedelta(minutes=20)


def test_gap_exceeding_max_gap_starts_a_new_cluster() -> None:
    rows = [
        _row(_NOW),
        _row(_NOW + timedelta(minutes=10)),
        # Gap > DEFAULT_MAX_GAP_MINUTES (60) — same place, but a new visit.
        _row(_NOW + timedelta(hours=3)),
        _row(_NOW + timedelta(hours=3, minutes=25)),
    ]
    spans, _ = cluster_points(
        rows, radius_m=DEFAULT_RADIUS_METERS, max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES)
    )
    assert len(spans) == 2
    assert spans[0].dwell == timedelta(minutes=10)
    assert spans[1].dwell == timedelta(minutes=25)


def test_move_away_and_back_forms_two_clusters() -> None:
    """A genuine departure (two consecutive out-of-radius points) closes the
    first cluster and starts a fresh one, rather than merging both visits."""
    rows = [
        _row(_NOW),
        _row(_NOW + timedelta(minutes=20)),
        _row(_NOW + timedelta(minutes=40), lat=_AWAY_LAT, lon=_AWAY_LON),
        _row(_NOW + timedelta(minutes=45), lat=_AWAY_LAT, lon=_AWAY_LON),
        _row(_NOW + timedelta(minutes=50), lat=_AWAY_LAT, lon=_AWAY_LON),
    ]
    spans, _ = cluster_points(
        rows, radius_m=DEFAULT_RADIUS_METERS, max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES)
    )
    assert len(spans) == 2
    assert spans[0].point_count == 2
    assert spans[0].centroid_lat == pytest.approx(_HOME_LAT)
    assert spans[1].point_count == 3
    assert spans[1].centroid_lat == pytest.approx(_AWAY_LAT)


def test_teleport_outlier_is_absorbed_as_noise() -> None:
    """A single glitchy point far from the cluster, surrounded by in-radius
    points before and after, must not fragment the dwell — the glitch point
    itself is excluded from the cluster (not counted, not its own cluster)."""
    rows = [
        _row(_NOW),
        _row(_NOW + timedelta(minutes=10)),
        # Glitch: momentarily "teleports" far away, then resumes at home.
        _row(_NOW + timedelta(minutes=20), lat=_AWAY_LAT, lon=_AWAY_LON, row_id="glitch"),
        _row(_NOW + timedelta(minutes=30)),
        _row(_NOW + timedelta(minutes=40)),
    ]
    spans, _ = cluster_points(
        rows, radius_m=DEFAULT_RADIUS_METERS, max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES)
    )
    assert len(spans) == 1
    span = spans[0]
    # 4 real home points; the glitch point is excluded from centroid/count.
    assert span.point_count == 4
    assert span.dwell == timedelta(minutes=40)
    assert span.centroid_lat == pytest.approx(_HOME_LAT)


def test_endpoint_change_forces_cluster_boundary() -> None:
    rows = [
        _row(_NOW, endpoint_identity="owntracks:alice"),
        _row(_NOW + timedelta(minutes=10), endpoint_identity="owntracks:alice"),
        _row(_NOW + timedelta(minutes=15), endpoint_identity="owntracks:bob"),
        _row(_NOW + timedelta(minutes=25), endpoint_identity="owntracks:bob"),
    ]
    spans, carryover = cluster_points(
        rows, radius_m=DEFAULT_RADIUS_METERS, max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES)
    )
    assert len(spans) == 2
    assert {s.endpoint_identity for s in spans} == {"owntracks:alice", "owntracks:bob"}
    assert set(carryover) == {"owntracks:alice", "owntracks:bob"}


def test_carryover_continues_a_dwell_across_a_batch_boundary() -> None:
    first_batch = [_row(_NOW), _row(_NOW + timedelta(minutes=10))]
    _, carryover = cluster_points(
        first_batch,
        radius_m=DEFAULT_RADIUS_METERS,
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    second_batch = [_row(_NOW + timedelta(minutes=15)), _row(_NOW + timedelta(minutes=25))]
    spans, _ = cluster_points(
        second_batch,
        radius_m=DEFAULT_RADIUS_METERS,
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
        prior_carryover=carryover,
    )
    assert len(spans) == 1
    span = spans[0]
    # Original start_at preserved across the batch boundary.
    assert span.start_at == _NOW
    assert span.end_at == _NOW + timedelta(minutes=25)
    assert span.point_count == 4


def test_carryover_not_continued_when_batch_resumes_far_away() -> None:
    first_batch = [_row(_NOW), _row(_NOW + timedelta(minutes=10))]
    _, carryover = cluster_points(
        first_batch,
        radius_m=DEFAULT_RADIUS_METERS,
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    second_batch = [_row(_NOW + timedelta(minutes=15), lat=_AWAY_LAT, lon=_AWAY_LON)]
    spans, _ = cluster_points(
        second_batch,
        radius_m=DEFAULT_RADIUS_METERS,
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
        prior_carryover=carryover,
    )
    assert len(spans) == 1
    # A fresh cluster, not a continuation — start_at is the new batch's row.
    assert spans[0].start_at == _NOW + timedelta(minutes=15)
    assert spans[0].point_count == 1


def test_carryover_not_continued_when_gap_too_large() -> None:
    first_batch = [_row(_NOW), _row(_NOW + timedelta(minutes=10))]
    _, carryover = cluster_points(
        first_batch,
        radius_m=DEFAULT_RADIUS_METERS,
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    second_batch = [_row(_NOW + timedelta(hours=5))]
    spans, _ = cluster_points(
        second_batch,
        radius_m=DEFAULT_RADIUS_METERS,
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
        prior_carryover=carryover,
    )
    assert spans[0].start_at == _NOW + timedelta(hours=5)


def test_malformed_carryover_is_discarded_not_raised() -> None:
    rows = [_row(_NOW)]
    spans, _ = cluster_points(
        rows,
        radius_m=DEFAULT_RADIUS_METERS,
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
        prior_carryover={_ENDPOINT: {"garbage": True}},
    )
    assert len(spans) == 1
    assert spans[0].start_at == _NOW


def test_empty_rows_returns_no_spans() -> None:
    spans, carryover = cluster_points(
        [], radius_m=DEFAULT_RADIUS_METERS, max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES)
    )
    assert spans == []
    assert carryover == {}


def test_cluster_span_source_ref_is_deterministic() -> None:
    span = ClusterSpan(
        endpoint_identity=_ENDPOINT,
        start_at=_NOW,
        end_at=_NOW + timedelta(minutes=30),
        sum_lat=_HOME_LAT,
        sum_lon=_HOME_LON,
        point_count=1,
    )
    expected = f"connectors.owntracks_points:place:{_ENDPOINT}:{int(_NOW.timestamp())}"
    assert span.source_ref() == expected
    assert span.source_ref() == span.source_ref()  # idempotent


# ---------------------------------------------------------------------------
# parse_place_references
# ---------------------------------------------------------------------------


def test_parse_place_references_empty_string_returns_empty_tuple() -> None:
    assert parse_place_references("") == ()
    assert parse_place_references("   ") == ()


def test_parse_place_references_valid_json() -> None:
    raw = (
        '[{"label": "home", "lat": 1.3, "lon": 103.8}, '
        '{"label": "work", "lat": 1.28, "lon": 103.85, "radius_m": 100}]'
    )
    refs = parse_place_references(raw)
    assert len(refs) == 2
    assert refs[0] == PlaceReference(label="home", lat=1.3, lon=103.8)
    assert refs[1].radius_m == 100


def test_parse_place_references_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_place_references("{not json")


def test_parse_place_references_rejects_non_list() -> None:
    with pytest.raises(ValueError, match="must be a JSON list"):
        parse_place_references('{"label": "home", "lat": 1, "lon": 2}')


def test_parse_place_references_rejects_missing_label() -> None:
    with pytest.raises(ValueError, match="label"):
        parse_place_references('[{"lat": 1.0, "lon": 2.0}]')


def test_parse_place_references_rejects_non_finite_lat() -> None:
    with pytest.raises(ValueError, match="lat"):
        parse_place_references('[{"label": "home", "lat": "nan", "lon": 2.0}]')


def test_parse_place_references_rejects_non_positive_radius() -> None:
    with pytest.raises(ValueError, match="radius_m"):
        parse_place_references('[{"label": "home", "lat": 1.0, "lon": 2.0, "radius_m": 0}]')


# ---------------------------------------------------------------------------
# Adapter._label_for
# ---------------------------------------------------------------------------


def test_label_for_matches_nearest_reference_within_radius() -> None:
    adapter = OwnTracksPlaceClusterAdapter(
        reference_points=(PlaceReference(label="home", lat=_HOME_LAT, lon=_HOME_LON, radius_m=200),)
    )
    label, matched = adapter._label_for(_HOME_LAT, _HOME_LON)
    assert label == "home"
    assert matched is not None
    assert matched.label == "home"


def test_label_for_returns_place_unknown_with_no_reference_match() -> None:
    adapter = OwnTracksPlaceClusterAdapter(
        reference_points=(PlaceReference(label="home", lat=_HOME_LAT, lon=_HOME_LON, radius_m=50),)
    )
    label, matched = adapter._label_for(_AWAY_LAT, _AWAY_LON)
    assert label == PLACE_UNKNOWN_LABEL
    assert matched is None


def test_label_for_returns_place_unknown_with_no_reference_points_configured() -> None:
    adapter = OwnTracksPlaceClusterAdapter()
    label, matched = adapter._label_for(_HOME_LAT, _HOME_LON)
    assert label == PLACE_UNKNOWN_LABEL
    assert matched is None


# ---------------------------------------------------------------------------
# Adapter.project() orchestration (mocked pools)
# ---------------------------------------------------------------------------


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _pool_returning(*rows: dict) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table-exists check
    conn.fetch = AsyncMock(return_value=[_make_mock_row(r) for r in rows])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _pool_table_missing() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _make_mock_row(r: dict) -> MagicMock:
    return MagicMock(**r, **{"__getitem__": lambda s, k, _r=r: _r[k]})


def _chronicler_pool(point_event_ids: list | None = None) -> AsyncMock:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{"id": i} for i in (point_event_ids or [])])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


@pytest.mark.asyncio
async def test_missing_evidence_table_degrades_gracefully() -> None:
    adapter = OwnTracksPlaceClusterAdapter()
    pool = _pool_table_missing()
    cp = _chronicler_pool()

    result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert "owntracks_points" in (result.skipped_reason or "")


@pytest.mark.asyncio
async def test_short_dwell_does_not_emit_episode() -> None:
    """A cluster below DEFAULT_MIN_DWELL_MINUTES is not upserted this run —
    it should still be preserved via carryover for a later batch to extend."""
    rows = [_row(_NOW), _row(_NOW + timedelta(minutes=2))]
    adapter = OwnTracksPlaceClusterAdapter(min_dwell_minutes=DEFAULT_MIN_DWELL_MINUTES)
    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks_place_cluster.get_carryover", return_value={}),
        patch(
            "butlers.chronicler.adapters.owntracks_place_cluster.save_carryover"
        ) as mock_save_carryover,
        patch(
            "butlers.chronicler.adapters.owntracks_place_cluster.upsert_episode"
        ) as mock_upsert_episode,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    mock_upsert_episode.assert_not_called()
    assert result.rows_projected == 0
    assert result.episodes_closed == 0
    mock_save_carryover.assert_awaited_once()
    saved_carryover = mock_save_carryover.await_args.args[2]
    assert _ENDPOINT in saved_carryover


@pytest.mark.asyncio
async def test_dwell_meeting_threshold_emits_labeled_episode() -> None:
    rows = [_row(_NOW + timedelta(minutes=m)) for m in range(0, 25, 5)]  # 20 min dwell
    adapter = OwnTracksPlaceClusterAdapter(
        min_dwell_minutes=DEFAULT_MIN_DWELL_MINUTES,
        reference_points=(
            PlaceReference(label="home", lat=_HOME_LAT, lon=_HOME_LON, radius_m=200),
        ),
    )
    pool = _pool_returning(*rows)
    cp = _chronicler_pool(point_event_ids=["evt-1", "evt-2"])

    upserted: list[Episode] = []

    async def _capture_upsert(conn: object, episode: Episode) -> Episode:
        episode.id = "episode-1"
        upserted.append(episode)
        return episode

    with (
        patch("butlers.chronicler.adapters.owntracks_place_cluster.get_carryover", return_value={}),
        patch("butlers.chronicler.adapters.owntracks_place_cluster.save_carryover"),
        patch(
            "butlers.chronicler.adapters.owntracks_place_cluster.upsert_episode",
            side_effect=_capture_upsert,
        ),
        patch("butlers.chronicler.adapters.owntracks_place_cluster.upsert_owner_episode_entity"),
        patch(
            "butlers.chronicler.adapters.owntracks_place_cluster.resolve_owner_entity_id",
            return_value=None,
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.episodes_closed == 1
    assert len(upserted) == 1

    ep = upserted[0]
    assert ep.source_name == SOURCE_NAME
    assert ep.episode_type == EPISODE_TYPE_PLACE
    assert ep.payload["label"] == "home"
    assert ep.payload["point_count"] == 5
    assert ep.evidence_refs  # populated from the mocked point_events lookup


@pytest.mark.asyncio
async def test_no_valid_rows_still_advances_nothing_and_skips_carryover() -> None:
    bad_row = _row(_NOW)
    bad_row["lat"] = float("nan")
    adapter = OwnTracksPlaceClusterAdapter()
    pool = _pool_returning(bad_row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.owntracks_place_cluster.save_carryover"
    ) as mock_save_carryover:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    assert len(result.warnings) == 1
    assert "lat must be finite" in result.warnings[0]
    mock_save_carryover.assert_not_called()


@pytest.mark.asyncio
async def test_no_rows_since_watermark_returns_unchanged_watermark() -> None:
    adapter = OwnTracksPlaceClusterAdapter()
    pool = _pool_returning()  # empty
    cp = _chronicler_pool()

    result = await adapter.project(pool, chronicler_pool=cp, since=_NOW)

    assert result.watermark == _NOW


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"radius_m": 0},
        {"radius_m": -1},
        {"min_dwell_minutes": -1},
        {"max_gap_minutes": 0},
        {"clock_skew_threshold_hours": -1},
    ],
)
def test_constructor_rejects_invalid_parameters(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        OwnTracksPlaceClusterAdapter(**kwargs)
