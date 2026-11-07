"""Test request_context injection into runtime session context.

Verifies that:
- Runtime sessions spawned from route.execute receive request_context data
- request_context includes all routing metadata fields
- Non-route triggers (tick, schedule) are unaffected
- Both dict and string input.context values are preserved

Each test boots a real daemon (``booted_daemon``) and calls route.execute over MCP.
route.execute accepts, then a background task processes through the real route
inbox and spawner; the fake runtime records the prompt an LLM would receive, and
the context is everything before the routed-message fence.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from butlers.testing.daemon import write_butler_config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

# The route inbox is idempotent per request_id, so every call needs its own.
_REQUEST_IDS = (f"018f6f4e-5b3b-7b2d-9c2f-{n:012x}" for n in itertools.count(1))


def _route_request_context(
    *,
    source_endpoint_identity: str = "switchboard",
    source_sender_identity: str = "health",
    source_channel: str = "telegram_bot",
    source_thread_identity: str | None = "12345",
    request_id: str | None = None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "request_id": request_id or next(_REQUEST_IDS),
        "received_at": "2026-02-14T00:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }
    if source_thread_identity is not None:
        ctx["source_thread_identity"] = source_thread_identity
    return ctx


async def _routed_context(booted, *, request_context, input_data) -> str:
    """Route one request; return the session context the runtime received."""
    seen = len(booted.runtime.invocations)
    result = await booted.call_tool(
        "route.execute",
        {"schema_version": "route.v1", "request_context": request_context, "input": input_data},
    )
    assert result["status"] == "accepted"
    invocations = await booted.runtime.wait_for_invocations(seen + 1)
    context, fence, _ = invocations[seen]["prompt"].rpartition("<routed_message>")
    assert fence, "routed prompt lost its payload fence"
    return context


class TestRouteExecuteRequestContextInjection:
    """Verify that request_context is injected into runtime session context."""

    async def test_request_context_and_input_context(self, booted_daemon, tmp_path: Path) -> None:
        """REQUEST CONTEXT injected with all fields; INPUT CONTEXT section for both dict and string;
        telegram channel injects INTERACTIVE DATA SOURCE; MCP does not."""
        booted = await booted_daemon(write_butler_config(tmp_path))

        # Basic injection: REQUEST CONTEXT present with key fields
        ctx_arg = await _routed_context(
            booted,
            request_context=_route_request_context(
                source_channel="telegram_bot",
                source_thread_identity="98765",
                source_sender_identity="user123",
                request_id="018f6f4e-5b3b-7b2d-9c2f-aaaaaabbbbbb",
            ),
            input_data={"prompt": "Run health check."},
        )
        assert "REQUEST CONTEXT" in ctx_arg
        assert "018f6f4e-5b3b-7b2d-9c2f-aaaaaabbbbbb" in ctx_arg
        assert "telegram" in ctx_arg
        assert "user123" in ctx_arg
        assert "INPUT CONTEXT" not in ctx_arg

        # Dict input.context → INPUT CONTEXT section
        ctx_arg2 = await _routed_context(
            booted,
            request_context=_route_request_context(),
            input_data={"prompt": "Check.", "context": {"patient_id": "patient-456"}},
        )
        assert "INPUT CONTEXT" in ctx_arg2
        assert "patient-456" in ctx_arg2

        # String input.context → INPUT CONTEXT section
        ctx_arg3 = await _routed_context(
            booted,
            request_context=_route_request_context(),
            input_data={"prompt": "Check.", "context": "Previous reading: BP 120/80"},
        )
        assert "INPUT CONTEXT" in ctx_arg3
        assert "Previous reading: BP 120/80" in ctx_arg3

        # Telegram: INTERACTIVE DATA SOURCE injected
        ctx_tg = await _routed_context(
            booted,
            request_context=_route_request_context(source_channel="telegram_bot"),
            input_data={"prompt": "Track medication."},
        )
        assert "INTERACTIVE DATA SOURCE" in ctx_tg
        assert "notify()" in ctx_tg

        # MCP: no INTERACTIVE DATA SOURCE
        ctx_mcp = await _routed_context(
            booted,
            request_context=_route_request_context(source_channel="mcp"),
            input_data={"prompt": "Internal check."},
        )
        assert "INTERACTIVE DATA SOURCE" not in ctx_mcp

    async def test_conversation_history(self, booted_daemon, tmp_path: Path) -> None:
        """Conversation history injected under CONVERSATION HISTORY before INPUT CONTEXT;
        absent/empty/None → no CONVERSATION HISTORY section."""
        booted = await booted_daemon(write_butler_config(tmp_path))

        history_text = "**user123** (2026-02-16T10:00:00Z):\nTrack my metformin 500mg twice daily"

        ctx_arg = await _routed_context(
            booted,
            request_context=_route_request_context(source_channel="telegram_bot"),
            input_data={
                "prompt": "When?",
                "context": "Extra context",
                "conversation_history": history_text,
            },
        )
        assert "CONVERSATION HISTORY" in ctx_arg
        assert "metformin 500mg" in ctx_arg
        history_pos = ctx_arg.find("CONVERSATION HISTORY")
        input_ctx_pos = ctx_arg.find("INPUT CONTEXT")
        assert history_pos < input_ctx_pos

        # None → no CONVERSATION HISTORY
        ctx_no = await _routed_context(
            booted,
            request_context=_route_request_context(source_channel="telegram_bot"),
            input_data={"prompt": "Do something.", "conversation_history": None},
        )
        assert "CONVERSATION HISTORY" not in ctx_no
