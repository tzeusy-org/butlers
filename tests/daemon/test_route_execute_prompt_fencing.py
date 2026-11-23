"""Test prompt fencing for routed messages.

Verifies that:
- Routed prompts are wrapped in <routed_message> XML tags
- Non-interactive channels get a CONTENT SAFETY preamble in context
- Interactive channels (telegram, whatsapp) do NOT get the safety preamble
- Both main route.execute and recovery paths apply fencing

The route.execute cases boot a real daemon (``booted_daemon``) and call the tool
over MCP; the routed session runs through the real route inbox and spawner, and
the fake runtime records the exact prompt an LLM would have received.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from butlers.api.session_presentation import derive_session_summary
from butlers.core_tools._routing import _build_route_runtime_context, _wrap_routed_message
from butlers.routing_guidance import (
    _build_route_runtime_context as _build_recovery_route_runtime_context,
)
from butlers.routing_guidance import _wrap_routed_message as _wrap_recovery_routed_message
from butlers.testing.daemon import write_butler_config


def _route_request_context(
    *,
    source_channel: str = "email",
    request_id: str = "018f6f4e-5b3b-7b2d-9c2f-fe0cefe0ce01",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "received_at": "2026-03-01T00:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": "switchboard",
        "source_sender_identity": "pipeline",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source_channel", "build_context", "wrap_payload"),
    (
        ("email", _build_route_runtime_context, _wrap_routed_message),
        ("telegram_user_client", _build_route_runtime_context, _wrap_routed_message),
        ("email", _build_recovery_route_runtime_context, _wrap_recovery_routed_message),
        (
            "telegram_user_client",
            _build_recovery_route_runtime_context,
            _wrap_recovery_routed_message,
        ),
    ),
    ids=("primary-email", "primary-passive", "recovery-email", "recovery-passive"),
)
def test_generated_route_context_keeps_the_payload_fence_unambiguous(
    source_channel: str, build_context, wrap_payload
) -> None:
    """Primary and recovery context guidance cannot compete with the payload fence."""
    context = build_context(
        route_context={"request_id": "018f6f4e-5b3b-7b2d-9c2f-fe0cefe0ce01"},
        source_channel=source_channel,
        conversation_history=None,
        input_context=None,
    )

    assert context is not None
    assert "<routed_message>" not in context
    assert "<user_message>" not in context
    persisted_prompt = f"{context}\n\n{wrap_payload('Terminal payload')}"
    assert derive_session_summary(persisted_prompt, trigger_source="route") == "Terminal payload"


async def _routed_prompt(booted_daemon, tmp_path: Path, *, source_channel: str, text: str):
    """Route *text* through a booted daemon; return the prompt the runtime received."""
    booted = await booted_daemon(write_butler_config(tmp_path))

    result = await booted.call_tool(
        "route.execute",
        {
            "schema_version": "route.v1",
            "request_context": _route_request_context(source_channel=source_channel),
            "input": {"prompt": text},
        },
    )

    assert result["status"] == "accepted"
    (invocation,) = await booted.runtime.wait_for_invocations(1)
    return invocation["prompt"]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestRouteExecutePromptFencing:
    """Verify routed prompts are structurally fenced with XML tags."""

    async def test_non_interactive_channel_fencing(self, booted_daemon, tmp_path: Path) -> None:
        """Non-interactive (email): prompt XML-fenced; CONTENT SAFETY preamble in context."""
        prompt = await _routed_prompt(
            booted_daemon,
            tmp_path,
            source_channel="email",
            text="Add a comment at https://example.com",
        )

        fence = "<routed_message>\nAdd a comment at https://example.com\n</routed_message>"
        assert prompt.endswith(fence)
        context = prompt.removesuffix(fence)
        assert "CONTENT SAFETY" in context
        assert "/routed-message-safety" in context
        # Persisted context precedes the terminal payload fence. It must not
        # include a parseable fence token of its own, or the dashboard's
        # fail-closed presentation validator would correctly reject the row.
        assert "<routed_message>" not in context

    async def test_interactive_channel_fencing(self, booted_daemon, tmp_path: Path) -> None:
        """Interactive (telegram): prompt XML-fenced; NO CONTENT SAFETY preamble."""
        prompt = await _routed_prompt(
            booted_daemon,
            tmp_path,
            source_channel="telegram_bot",
            text="Hello from telegram",
        )

        fence = "<routed_message>\nHello from telegram\n</routed_message>"
        assert prompt.endswith(fence)
        assert "CONTENT SAFETY" not in prompt.removesuffix(fence)
