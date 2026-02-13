"""Tests for Email module integration with the classification pipeline.

Verifies that:
- EmailModule.process_incoming() classifies and routes emails
- bot_email_check_and_route_inbox fetches unseen emails and routes them
- _build_classification_text builds sensible text for classification
- Pipeline errors are handled gracefully
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.email import EmailModule, _build_classification_text
from butlers.modules.pipeline import MessagePipeline

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helper: create a pipeline with mock classify/route
# ---------------------------------------------------------------------------


def _make_pipeline(
    classify_result: str = "general",
    route_result: dict | None = None,
    classify_error: Exception | None = None,
    route_error: Exception | None = None,
) -> MessagePipeline:
    """Build a MessagePipeline with mock classify/route functions."""

    async def mock_classify(pool, message, dispatch_fn):
        if classify_error:
            raise classify_error
        return classify_result

    async def mock_route(pool, target, tool_name, args, source):
        if route_error:
            raise route_error
        return route_result or {"result": "ok"}

    return MessagePipeline(
        switchboard_pool=MagicMock(),
        dispatch_fn=AsyncMock(),
        source_butler="test-butler",
        classify_fn=mock_classify,
        route_fn=mock_route,
    )


# ---------------------------------------------------------------------------
# _build_classification_text
# ---------------------------------------------------------------------------


class TestBuildClassificationText:
    """Test the classification text builder."""

    def test_subject_and_body(self):
        text = _build_classification_text("Meeting tomorrow", "Let's meet at 3pm")
        assert "Subject: Meeting tomorrow" in text
        assert "Let's meet at 3pm" in text

    def test_subject_only(self):
        text = _build_classification_text("Hello", "")
        assert text == "Subject: Hello"

    def test_body_only(self):
        text = _build_classification_text("", "Just a body")
        assert text == "Just a body"

    def test_both_empty_returns_none(self):
        text = _build_classification_text("", "")
        assert text is None

    def test_body_truncated_at_500(self):
        """Long bodies are truncated to 500 characters."""
        long_body = "x" * 1000
        text = _build_classification_text("", long_body)
        assert text is not None
        assert len(text) == 500


# ---------------------------------------------------------------------------
# set_pipeline
# ---------------------------------------------------------------------------


class TestSetPipeline:
    """Test pipeline attachment on EmailModule."""

    def test_set_pipeline(self):
        mod = EmailModule()
        assert mod._pipeline is None

        pipeline = _make_pipeline()
        mod.set_pipeline(pipeline)
        assert mod._pipeline is pipeline

    def test_replace_pipeline(self):
        mod = EmailModule()
        p1 = _make_pipeline()
        p2 = _make_pipeline(classify_result="health")

        mod.set_pipeline(p1)
        mod.set_pipeline(p2)
        assert mod._pipeline is p2


# ---------------------------------------------------------------------------
# process_incoming
# ---------------------------------------------------------------------------


class TestProcessIncoming:
    """Test EmailModule.process_incoming()."""

    async def test_routes_email_to_classified_butler(self):
        """process_incoming classifies and routes the email."""
        mod = EmailModule()
        mod.set_pipeline(_make_pipeline(classify_result="health"))

        email_data = {
            "message_id": "1",
            "from": "patient@example.com",
            "subject": "Headache",
            "body": "I have a terrible headache since yesterday.",
        }

        result = await mod.process_incoming(email_data)

        assert result is not None
        assert result.target_butler == "health"
        assert result.route_result == {"result": "ok"}

    async def test_returns_none_without_pipeline(self):
        """process_incoming returns None if no pipeline is set."""
        mod = EmailModule()
        result = await mod.process_incoming({"subject": "Test", "body": "Hello"})
        assert result is None

    async def test_returns_none_for_empty_email(self):
        """process_incoming returns None when subject and body are empty."""
        mod = EmailModule()
        mod.set_pipeline(_make_pipeline())
        result = await mod.process_incoming({"subject": "", "body": ""})
        assert result is None

    async def test_includes_email_metadata_in_tool_args(self):
        """process_incoming includes source, from, subject, message_id in route args."""
        captured_args: dict = {}

        async def capture_route(pool, target, tool_name, args, source):
            captured_args.update(args)
            return {"result": "ok"}

        async def mock_classify(pool, message, dispatch_fn):
            return "general"

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=capture_route,
        )

        mod = EmailModule()
        mod.set_pipeline(pipeline)

        email_data = {
            "message_id": "42",
            "from": "sender@example.com",
            "subject": "Important",
            "body": "Please handle this.",
        }
        await mod.process_incoming(email_data)

        assert captured_args["source"] == "email"
        assert captured_args["from"] == "sender@example.com"
        assert captured_args["subject"] == "Important"
        assert captured_args["message_id"] == "42"
        assert "message" in captured_args

    async def test_records_routed_messages(self):
        """process_incoming appends results to _routed_messages."""
        mod = EmailModule()
        mod.set_pipeline(_make_pipeline())

        email = {"subject": "Test", "body": "Body"}
        await mod.process_incoming(email)
        await mod.process_incoming(email)

        assert len(mod._routed_messages) == 2

    async def test_handles_classification_error(self):
        """process_incoming falls back to 'general' on classification failure."""
        mod = EmailModule()
        mod.set_pipeline(_make_pipeline(classify_error=RuntimeError("AI broke")))

        result = await mod.process_incoming({"subject": "Help", "body": "Need help"})

        assert result is not None
        assert result.target_butler == "general"
        assert result.classification_error is not None

    async def test_handles_routing_error(self):
        """process_incoming records routing error."""
        mod = EmailModule()
        mod.set_pipeline(
            _make_pipeline(
                classify_result="health",
                route_error=ConnectionError("unreachable"),
            )
        )

        result = await mod.process_incoming({"subject": "Help", "body": "Need help"})

        assert result is not None
        assert result.target_butler == "health"
        assert result.routing_error is not None

    async def test_subject_only_email(self):
        """Emails with only a subject (no body) are still processed."""
        mod = EmailModule()
        mod.set_pipeline(_make_pipeline(classify_result="general"))

        result = await mod.process_incoming({"subject": "Just a subject", "body": ""})
        assert result is not None
        assert result.target_butler == "general"


# ---------------------------------------------------------------------------
# bot_email_check_and_route_inbox
# ---------------------------------------------------------------------------


class TestCheckAndRouteInbox:
    """Test the bot_email_check_and_route_inbox tool."""

    async def test_no_pipeline_returns_no_pipeline_status(self):
        """Without a pipeline, returns status 'no_pipeline'."""
        mod = EmailModule()
        result = await mod._check_and_route_inbox()
        assert result["status"] == "no_pipeline"

    async def test_routes_unseen_emails(self):
        """Fetches unseen emails and routes each through the pipeline."""
        mod = EmailModule()
        mod.set_pipeline(_make_pipeline(classify_result="health"))

        # Mock _search_inbox to return 2 headers
        async def mock_search(query):
            return [
                {"message_id": "1", "from": "a@b.com", "subject": "S1", "date": "2026-01-01"},
                {"message_id": "2", "from": "c@d.com", "subject": "S2", "date": "2026-01-02"},
            ]

        # Mock _read_email to return full emails
        async def mock_read(message_id):
            return {
                "message_id": message_id,
                "from": "sender@example.com",
                "subject": f"Subject {message_id}",
                "body": f"Body for {message_id}",
            }

        mod._search_inbox = mock_search  # type: ignore[method-assign]
        mod._read_email = mock_read  # type: ignore[method-assign]

        result = await mod._check_and_route_inbox()

        assert result["status"] == "ok"
        assert result["total"] == 2
        assert result["routed"] == 2
        assert len(result["results"]) == 2
        assert all(r["status"] == "routed" for r in result["results"])
        assert all(r["target_butler"] == "health" for r in result["results"])

    async def test_handles_search_error(self):
        """Returns error status when inbox search fails."""
        mod = EmailModule()
        mod.set_pipeline(_make_pipeline())

        async def failing_search(query):
            raise RuntimeError("IMAP connection failed")

        mod._search_inbox = failing_search  # type: ignore[method-assign]

        result = await mod._check_and_route_inbox()
        assert result["status"] == "error"
        assert "IMAP" in result["message"]

    async def test_handles_read_error_per_email(self):
        """Read errors on individual emails are recorded but don't stop processing."""
        mod = EmailModule()
        mod.set_pipeline(_make_pipeline(classify_result="general"))

        async def mock_search(query):
            return [
                {"message_id": "1", "from": "a@b.com", "subject": "S1", "date": ""},
                {"message_id": "2", "from": "c@d.com", "subject": "S2", "date": ""},
            ]

        call_count = 0

        async def flaky_read(message_id):
            nonlocal call_count
            call_count += 1
            if message_id == "1":
                raise RuntimeError("corrupt message")
            return {
                "message_id": message_id,
                "from": "c@d.com",
                "subject": "S2",
                "body": "OK body",
            }

        mod._search_inbox = mock_search  # type: ignore[method-assign]
        mod._read_email = flaky_read  # type: ignore[method-assign]

        result = await mod._check_and_route_inbox()

        assert result["status"] == "ok"
        assert result["total"] == 2
        assert result["routed"] == 1
        # First email errored, second was routed
        assert result["results"][0]["status"] == "error"
        assert result["results"][1]["status"] == "routed"

    async def test_empty_inbox(self):
        """Empty inbox returns ok with zero counts."""
        mod = EmailModule()
        mod.set_pipeline(_make_pipeline())

        async def mock_search(query):
            return []

        mod._search_inbox = mock_search  # type: ignore[method-assign]

        result = await mod._check_and_route_inbox()
        assert result["status"] == "ok"
        assert result["total"] == 0
        assert result["routed"] == 0
        assert result["results"] == []

    async def test_registers_check_and_route_tool(self):
        """register_tools creates a bot_email_check_and_route_inbox tool."""
        mod = EmailModule()
        mcp = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool

        await mod.register_tools(mcp=mcp, config=None, db=None)

        assert "bot_email_check_and_route_inbox" in tools
        assert callable(tools["bot_email_check_and_route_inbox"])
