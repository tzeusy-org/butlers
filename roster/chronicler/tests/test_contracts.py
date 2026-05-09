"""Unit tests for Chronicler source compatibility contract registry."""

from __future__ import annotations

import pytest

from butlers.chronicler.contracts import (
    INITIAL_SOURCES,
    deferred_source_names,
    find_source,
    planned_source_names,
    supported_source_names,
)
from butlers.chronicler.models import Compatibility


def test_initial_sources_include_core_sessions() -> None:
    names = {s.source_name for s in INITIAL_SOURCES}
    assert "core.sessions" in names


def test_initial_sources_include_calendar_completed() -> None:
    names = {s.source_name for s in INITIAL_SOURCES}
    assert "google_calendar.completed" in names


def test_spotify_session_summary_is_supported() -> None:
    """PR #1117 (bu-7k61u) promoted spotify.session_summary from DEFERRED → SUPPORTED."""
    state = find_source("spotify.session_summary")
    assert state is not None
    assert state.chronicler_compatibility == Compatibility.SUPPORTED
    assert state.read_surface == "connectors.spotify_listening_sessions"


def test_google_health_is_supported() -> None:
    """PR #1216 (bu-yhs2c) promoted google_health.measurements from DEFERRED → SUPPORTED."""
    state = find_source("google_health.measurements")
    assert state is not None
    assert state.chronicler_compatibility == Compatibility.SUPPORTED
    assert state.read_surface == "health.facts (predicate=sleep_session|workout_session)"


@pytest.mark.parametrize("source_name", ["health.steps", "health.heart_rate"])
def test_google_health_point_event_sources_are_supported(source_name: str) -> None:
    state = find_source(source_name)
    assert state is not None
    assert state.chronicler_compatibility == Compatibility.SUPPORTED
    assert state.read_surface is not None


def test_session_process_logs_marked_not_time_bearing() -> None:
    state = find_source("core.session_process_logs")
    assert state is not None
    assert state.chronicler_compatibility == Compatibility.NOT_TIME_BEARING


def test_supported_names_non_empty_in_v1() -> None:
    supported = supported_source_names()
    assert "core.sessions" in supported
    assert "google_calendar.completed" in supported


def test_deferred_and_planned_are_separate() -> None:
    deferred = set(deferred_source_names())
    planned = set(planned_source_names())
    assert deferred.isdisjoint(planned)


def test_every_supported_source_declares_read_surface() -> None:
    for state in INITIAL_SOURCES:
        if state.chronicler_compatibility == Compatibility.SUPPORTED:
            assert state.read_surface, (
                f"Supported source {state.source_name} MUST declare a read_surface"
            )


def test_find_source_returns_none_for_unknown() -> None:
    assert find_source("no.such.source") is None


@pytest.mark.parametrize("state", list(INITIAL_SOURCES))
def test_every_declaration_has_boundary_semantics_or_is_not_time_bearing(state) -> None:
    if state.chronicler_compatibility in (
        Compatibility.SUPPORTED,
        Compatibility.DEFERRED,
        Compatibility.PLANNED,
    ):
        assert state.boundary_semantics, (
            f"{state.source_name} ({state.chronicler_compatibility.value}) "
            "must declare boundary_semantics"
        )
