"""Tests for Chronicler deterministic projection job handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from butlers.chronicler.adapters.base import AdapterResult
from butlers.chronicler.jobs import _DEFAULT_CALENDAR_SCHEMAS, _DEFAULT_SESSION_SCHEMAS

pytestmark = pytest.mark.unit


async def test_project_sessions_discovers_butler_schemas_and_runs_adapter() -> None:
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(source_name="core.sessions", rows_projected=2)
    seed_registry = AsyncMock()
    configs = [
        SimpleNamespace(db_schema="general", modules={}),
        SimpleNamespace(db_schema="chronicler", modules={}),
        SimpleNamespace(db_schema="general", modules={}),
    ]

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.list_butlers", return_value=configs),
        patch("butlers.chronicler.jobs.CoreSessionsAdapter", return_value=adapter) as adapter_cls,
    ):
        from butlers.chronicler.jobs import run_project_sessions

        result = await run_project_sessions(pool, {"batch_limit": 25})

    seed_registry.assert_awaited_once_with(pool)
    adapter_cls.assert_called_once_with(
        butler_schemas=("general", "chronicler"),
        batch_limit=25,
    )
    adapter.run.assert_awaited_once_with(pool=pool, chronicler_pool=pool)
    assert result["source_name"] == "core.sessions"
    assert result["rows_projected"] == 2


async def test_project_sessions_publishes_live_event_after_material_projection() -> None:
    """The live scheduled handler publishes only after its adapter returns."""
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(
        source_name="core.sessions",
        rows_projected=3,
        point_events=6,
        episodes_opened=2,
        episodes_closed=1,
    )
    seed_registry = AsyncMock()
    publish_event = AsyncMock()

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.list_butlers", return_value=[]),
        patch("butlers.chronicler.jobs.CoreSessionsAdapter", return_value=adapter),
        patch("butlers.chronicler.jobs.publish_fleet_event", publish_event),
    ):
        from butlers.chronicler.jobs import run_project_sessions

        await run_project_sessions(pool, None)

    adapter.run.assert_awaited_once_with(pool=pool, chronicler_pool=pool)
    publish_event.assert_awaited_once_with(
        pool,
        "chronicles",
        {
            "kind": "projection",
            "rows_projected": 3,
            "point_events": 6,
            "episodes_opened": 2,
            "episodes_closed": 1,
        },
    )


async def test_project_sessions_skips_live_event_when_projection_is_empty() -> None:
    """An empty scheduled sweep does not churn all Chronicles query caches."""
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(source_name="core.sessions")
    publish_event = AsyncMock()

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", new=AsyncMock()),
        patch("butlers.chronicler.jobs.list_butlers", return_value=[]),
        patch("butlers.chronicler.jobs.CoreSessionsAdapter", return_value=adapter),
        patch("butlers.chronicler.jobs.publish_fleet_event", publish_event),
    ):
        from butlers.chronicler.jobs import run_project_sessions

        await run_project_sessions(pool, None)

    publish_event.assert_not_awaited()


async def test_project_sessions_skips_live_event_when_adapter_is_skipped() -> None:
    """A skipped source is not a durable projection even if it reports work."""
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(
        source_name="core.sessions",
        rows_projected=1,
        skipped=True,
        skipped_reason="source unavailable",
    )
    publish_event = AsyncMock()

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", new=AsyncMock()),
        patch("butlers.chronicler.jobs.list_butlers", return_value=[]),
        patch("butlers.chronicler.jobs.CoreSessionsAdapter", return_value=adapter),
        patch("butlers.chronicler.jobs.publish_fleet_event", publish_event),
    ):
        from butlers.chronicler.jobs import run_project_sessions

        await run_project_sessions(pool, None)

    publish_event.assert_not_awaited()


async def test_project_calendar_filters_to_calendar_enabled_butlers() -> None:
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(
        source_name="google_calendar.completed",
        rows_projected=3,
    )
    seed_registry = AsyncMock()
    configs = [
        SimpleNamespace(db_schema="general", modules={"calendar": {}}),
        SimpleNamespace(db_schema="travel", modules={"calendar": {"provider": "google"}}),
        SimpleNamespace(db_schema="health", modules={"contacts": {}}),
    ]

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.list_butlers", return_value=configs),
        patch(
            "butlers.chronicler.jobs.CalendarCompletedAdapter",
            return_value=adapter,
        ) as adapter_cls,
    ):
        from butlers.chronicler.jobs import run_project_calendar

        result = await run_project_calendar(pool, None)

    seed_registry.assert_awaited_once_with(pool)
    adapter_cls.assert_called_once_with(
        butler_schemas=("general", "travel"),
    )
    adapter.run.assert_awaited_once_with(pool=pool, chronicler_pool=pool)
    assert result["source_name"] == "google_calendar.completed"
    assert result["rows_projected"] == 3


@pytest.mark.parametrize(
    "job_name,adapter_path,source_name,list_butlers_kwargs,default_schemas",
    [
        # Session job falls back when the roster scan itself raises.
        (
            "run_project_sessions",
            "butlers.chronicler.jobs.CoreSessionsAdapter",
            "core.sessions",
            {"side_effect": RuntimeError("boom")},
            _DEFAULT_SESSION_SCHEMAS,
        ),
        # Calendar job falls back when no butler has calendar enabled.
        (
            "run_project_calendar",
            "butlers.chronicler.jobs.CalendarCompletedAdapter",
            "google_calendar.completed",
            {"return_value": [SimpleNamespace(db_schema="general", modules={})]},
            _DEFAULT_CALENDAR_SCHEMAS,
        ),
    ],
    ids=["sessions-roster-scan-fails", "calendar-none-enabled"],
)
async def test_project_falls_back_to_static_schema_set(
    job_name, adapter_path, source_name, list_butlers_kwargs, default_schemas
) -> None:
    import importlib

    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(source_name=source_name)
    seed_registry = AsyncMock()

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.list_butlers", **list_butlers_kwargs),
        patch(adapter_path, return_value=adapter) as adapter_cls,
    ):
        job = getattr(importlib.import_module("butlers.chronicler.jobs"), job_name)
        await job(pool, None)

    seed_registry.assert_awaited_once_with(pool)
    adapter_cls.assert_called_once_with(butler_schemas=default_schemas)


async def test_project_sessions_raises_when_adapter_reports_error() -> None:
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(source_name="core.sessions", error="fk violation")
    seed_registry = AsyncMock()

    with (
        patch("butlers.chronicler.jobs.list_butlers", return_value=[]),
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.CoreSessionsAdapter", return_value=adapter),
    ):
        from butlers.chronicler.jobs import run_project_sessions

        with pytest.raises(RuntimeError, match="core.sessions projection failed: fk violation"):
            await run_project_sessions(pool, None)

    seed_registry.assert_awaited_once_with(pool)


async def test_project_owntracks_rejects_unsupported_job_args() -> None:
    from butlers.chronicler.jobs import run_project_owntracks

    with pytest.raises(RuntimeError, match="unsupported keys"):
        await run_project_owntracks(object(), {"oops": 1})


async def test_project_owntracks_ssid_reads_owner_mapping_from_state() -> None:
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(
        source_name="owntracks.ssid_presence", rows_projected=2
    )
    seed_registry = AsyncMock()

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch(
            "butlers.chronicler.jobs.state_get",
            new=AsyncMock(return_value={"Corp WiFi": "work", "Home WiFi": "home"}),
        ) as get_state,
        patch(
            "butlers.chronicler.jobs.OwnTracksSsidPresenceAdapter", return_value=adapter
        ) as adapter_cls,
    ):
        from butlers.chronicler.jobs import run_project_owntracks_ssid

        result = await run_project_owntracks_ssid(pool, {"max_gap_minutes": 45})

    get_state.assert_awaited_once_with(pool, "chronicler/owntracks/ssid_places")
    adapter_cls.assert_called_once_with(
        ssid_places={"Corp WiFi": "work", "Home WiFi": "home"},
        max_gap_minutes=45,
    )
    assert result["rows_projected"] == 2


async def test_project_owntracks_ssid_malformed_state_degrades_to_empty_mapping() -> None:
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(source_name="owntracks.ssid_presence")

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", new=AsyncMock()),
        patch(
            "butlers.chronicler.jobs.state_get",
            new=AsyncMock(return_value={"Corp WiFi": "office"}),
        ),
        patch(
            "butlers.chronicler.jobs.OwnTracksSsidPresenceAdapter", return_value=adapter
        ) as adapter_cls,
    ):
        from butlers.chronicler.jobs import run_project_owntracks_ssid

        await run_project_owntracks_ssid(pool, None)

    adapter_cls.assert_called_once_with(ssid_places={})


async def test_project_owntracks_place_cluster_malformed_env_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed ``OWNTRACKS_PLACE_REFERENCES`` must not crash the job — this
    is re-read every scheduled tick (unlike HA_WELLNESS_RULES_EXTRA, which is
    only validated once at daemon startup), so a fail-fast raise here would
    wedge the job on every run until an operator notices."""
    monkeypatch.setenv("OWNTRACKS_PLACE_REFERENCES", "{not valid json")

    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(
        source_name="owntracks.place_cluster", rows_projected=0
    )
    seed_registry = AsyncMock()

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch(
            "butlers.chronicler.jobs.OwnTracksPlaceClusterAdapter", return_value=adapter
        ) as adapter_cls,
    ):
        from butlers.chronicler.jobs import run_project_owntracks_place_cluster

        await run_project_owntracks_place_cluster(pool, None)

    adapter_cls.assert_called_once_with(reference_points=())


async def test_project_owntracks_place_cluster_parses_valid_env() -> None:
    from butlers.chronicler.adapters.owntracks_place_cluster import PlaceReference

    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(
        source_name="owntracks.place_cluster", rows_projected=0
    )
    seed_registry = AsyncMock()

    raw = '[{"label": "home", "lat": 1.3, "lon": 103.8}]'
    with (
        patch.dict("os.environ", {"OWNTRACKS_PLACE_REFERENCES": raw}),
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch(
            "butlers.chronicler.jobs.OwnTracksPlaceClusterAdapter", return_value=adapter
        ) as adapter_cls,
    ):
        from butlers.chronicler.jobs import run_project_owntracks_place_cluster

        await run_project_owntracks_place_cluster(pool, None)

    adapter_cls.assert_called_once_with(
        reference_points=(PlaceReference(label="home", lat=1.3, lon=103.8),)
    )
