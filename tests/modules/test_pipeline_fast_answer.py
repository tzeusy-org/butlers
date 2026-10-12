"""Real pipeline ownership and immutable context handed to the fast runtime."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from butlers.core.dashboard_fast_answer_runtime import FastAnswerResult
from butlers.core.utils import generate_uuid7_string
from butlers.modules.pipeline import MessagePipeline

pytestmark = pytest.mark.unit


async def test_dashboard_fast_runtime_is_selected_without_generic_dispatch_or_domain_bypass(
    monkeypatch,
):
    # REQ-dashboard-fast-answer-001 / -002 / -004 / -006.
    from importlib import import_module

    from butlers.core import fact_authority

    # Synthetic unregistered source: no accepted-source or resolver credit.
    monkeypatch.setattr(fact_authority, "_source_registry", None)

    classify = import_module("butlers.tools.switchboard.routing.classify")
    monkeypatch.setattr(
        classify,
        "_load_available_butlers",
        AsyncMock(
            return_value=[
                {"name": "concierge", "description": "System fleet runtime spend"},
                {"name": "health", "description": "Health data"},
            ]
        ),
    )
    message, conversation, request, session = (
        uuid4(),
        uuid4(),
        UUID(generate_uuid7_string()),
        uuid4(),
    )
    monkeypatch.setattr(
        MessagePipeline,
        "_load_dashboard_context",
        AsyncMock(
            return_value={
                "conversation_id": str(conversation),
                "message_id": str(message),
                "page_context": None,
            }
        ),
    )
    fast = SimpleNamespace(run=AsyncMock(return_value=FastAnswerResult(True, "completed", session)))
    dispatch = AsyncMock()
    pipeline = MessagePipeline(MagicMock(), dispatch, dashboard_fast_answer=fast)
    result = await pipeline.process(
        "How is the fleet?",
        tool_args={
            "source": "dashboard",
            "source_channel": "dashboard",
            "source_identity": "dashboard",
            "source_tool": "ingest",
            "request_id": str(request),
            "dashboard_message_id": str(message),
        },
        message_inbox_id=str(request),
    )
    assert result.target_butler == "concierge"
    assert result.route_result["fast_answer"] == "completed"
    assert dispatch.await_count == 0
    bound = fast.run.await_args.kwargs
    assert (bound["message_id"], bound["request_id"], bound["conversation_id"]) == (
        message,
        request,
        conversation,
    )
    assert bound["catalog_query"] == "How is the fleet?"
    assert bound["prompt"] != bound["catalog_query"]

    fast.run.reset_mock()
    dispatch.return_value = SimpleNamespace(output="", tool_calls=[])
    await pipeline.process(
        "A domain health message",
        tool_args={
            "source": "telegram_bot",
            "source_identity": "telegram",
            "request_id": str(uuid4()),
        },
    )
    assert fast.run.await_count == 0
    assert dispatch.await_count == 1
