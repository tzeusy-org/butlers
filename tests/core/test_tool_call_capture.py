"""Unit tests for runtime tool-call capture helpers."""

from __future__ import annotations

from butlers.core.tool_call_capture import (
    capture_tool_call,
    consume_runtime_session_tool_calls,
    ensure_runtime_session_capture,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
)


def test_capture_and_consume_runtime_session_tool_calls():
    ensure_runtime_session_capture("sess-1")
    token = set_current_runtime_session_id("sess-1")
    try:
        capture_tool_call(
            tool_name="route_to_butler",
            module_name="core",
            input_payload={"butler": "relationship"},
        )
    finally:
        reset_current_runtime_session_id(token)

    calls = consume_runtime_session_tool_calls("sess-1")
    assert calls == [
        {
            "name": "route_to_butler",
            "module": "core",
            "input": {"butler": "relationship"},
        }
    ]


def test_capture_without_runtime_session_is_ignored():
    capture_tool_call(tool_name="route_to_butler", module_name="core", input_payload={})
    calls = consume_runtime_session_tool_calls("unknown-session")
    assert calls == []


def test_capture_persists_outcome_result_and_error():
    ensure_runtime_session_capture("sess-2")
    token = set_current_runtime_session_id("sess-2")
    try:
        capture_tool_call(
            tool_name="route_to_butler",
            module_name="core",
            input_payload={"butler": "general"},
            outcome="error",
            result_payload={"payload": b"abc", "nested": (1, 2)},
            error="RuntimeError: routing failed",
        )
    finally:
        reset_current_runtime_session_id(token)

    calls = consume_runtime_session_tool_calls("sess-2")
    assert calls == [
        {
            "name": "route_to_butler",
            "module": "core",
            "input": {"butler": "general"},
            "outcome": "error",
            "result": {"payload": "abc", "nested": [1, 2]},
            "error": "RuntimeError: routing failed",
        }
    ]
