"""Unit tests for butlers.core.session_timing (bu-dhloi1.180).

Stage timings are content-free offsets (ms) from the session clock origin. Stages
the process never observed are absent from the snapshot, never guessed.
"""

from __future__ import annotations

import pytest

from butlers.core import session_timing

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_registry():
    session_timing._reset_for_tests()
    yield
    session_timing._reset_for_tests()


def test_snapshot_reports_offsets_from_origin_and_pops() -> None:
    clock = iter([100.5, 101.0, 101.2, 103.0, 104.0])
    session_timing.begin("s1", origin=100.0, now=lambda: next(clock))
    session_timing.mark("s1", "row_created_ms")  # 100.5 -> 500
    session_timing.mark("s1", "invoke_start_ms")  # 101.0 -> 1000
    session_timing.mark("s1", "first_mcp_request_ms")  # 101.2 -> 1200
    session_timing.mark("s1", "invoke_end_ms")  # 103.0 -> 3000

    timings = session_timing.finish("s1")  # 104.0 -> 4000

    assert timings == {
        "row_created_ms": 500,
        "invoke_start_ms": 1000,
        "first_mcp_request_ms": 1200,
        "invoke_end_ms": 3000,
        "total_ms": 4000,
    }
    assert session_timing.finish("s1") is None  # popped


def test_first_marks_win_and_last_marks_overwrite() -> None:
    clock = iter([1.0, 2.0, 3.0])
    session_timing.begin("s1", origin=0.0, now=lambda: next(clock))
    session_timing.mark("s1", "first_mcp_request_ms")  # reads 1.0
    session_timing.mark("s1", "first_mcp_request_ms")  # already set: ignored, no read
    session_timing.mark_tool_done("s1")  # reads 2.0: first = last = 2.0
    session_timing.mark_tool_done("s1")  # reads 3.0: only last moves

    snap = session_timing.snapshot("s1")

    assert snap["first_mcp_request_ms"] == 1000
    assert snap["first_tool_done_ms"] == 2000
    assert snap["last_tool_done_ms"] == 3000


def test_unknown_session_marks_are_noops_and_not_registered() -> None:
    session_timing.mark("never-began", "first_mcp_request_ms")
    session_timing.mark_tool_done("never-began")
    session_timing.note_adapter("never-began", {"spawn_latency_ms": 5})

    assert session_timing.snapshot("never-began") is None
    assert session_timing.tracked_session_count() == 0


def test_note_adapter_keeps_only_allowlisted_integers() -> None:
    session_timing.begin("s1", origin=0.0, now=lambda: 0.0)
    session_timing.note_adapter(
        "s1",
        {
            "spawn_latency_ms": 1234,
            "attempt_count": 2,
            "mcp_server_count": 1,
            "stderr": "secret-looking text must never be copied",
            "command": "codex exec prompt text",
            "pid": 99,
            "spawn_latency_ms_bad": True,
        },
    )

    snap = session_timing.snapshot("s1")

    assert snap["adapter"] == {
        "spawn_latency_ms": 1234,
        "attempt_count": 2,
        "mcp_server_count": 1,
    }


def test_note_adapter_ignores_non_dict_and_bool_values() -> None:
    session_timing.begin("s1", origin=0.0, now=lambda: 0.0)
    session_timing.note_adapter("s1", None)
    session_timing.note_adapter("s1", {"spawn_latency_ms": True, "attempt_count": "3"})

    assert "adapter" not in session_timing.snapshot("s1")


def test_tool_surface_is_recorded_as_counts_and_sizes_only() -> None:
    session_timing.begin("s1", origin=0.0, now=lambda: 0.0)
    session_timing.note_tool_surface("s1", tool_count=42, schema_bytes=40_000)

    snap = session_timing.snapshot("s1")

    assert snap["tool_surface"] == {
        "tool_count": 42,
        "schema_bytes": 40_000,
        "schema_tokens_est": 10_000,
    }


def test_tool_surface_absent_values_are_not_guessed() -> None:
    session_timing.begin("s1", origin=0.0, now=lambda: 0.0)
    session_timing.note_tool_surface("s1", tool_count=None, schema_bytes=None)

    assert "tool_surface" not in session_timing.snapshot("s1")


def test_registry_is_bounded_and_evicts_oldest() -> None:
    for i in range(session_timing.MAX_TRACKED_SESSIONS + 5):
        session_timing.begin(f"s{i}", origin=0.0, now=lambda: 0.0)

    assert session_timing.tracked_session_count() == session_timing.MAX_TRACKED_SESSIONS
    assert session_timing.snapshot("s0") is None
    assert session_timing.snapshot(f"s{session_timing.MAX_TRACKED_SESSIONS + 4}") is not None


def test_discard_drops_without_snapshot() -> None:
    session_timing.begin("s1", origin=0.0, now=lambda: 0.0)
    session_timing.discard("s1")

    assert session_timing.finish("s1") is None
