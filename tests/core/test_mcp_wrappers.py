"""Tests for MCP wrapper tool-call capture metadata."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from butlers.mcp_wrappers import _SpanWrappingMCP, _ToolCallLoggingMCP

pytestmark = pytest.mark.unit


async def test_tool_call_capture_fingerprints_hidden_arguments() -> None:
    """Full inputs affect loop signatures without persisting raw non-allowlisted fields."""

    def tool_decorator(*_args: Any, **_kwargs: Any):
        def decorator(fn):
            return fn

        return decorator

    mock_mcp = MagicMock()
    mock_mcp.tool = tool_decorator
    proxy = _ToolCallLoggingMCP(mock_mcp, "relationship", module_name="relationship")

    @proxy.tool(name="contact_resolve")
    async def contact_resolve(name: str, context: str | None = None) -> dict[str, Any]:
        return {"contact_id": None, "confidence": "none", "candidates": []}

    with patch("butlers.mcp_wrappers.capture_tool_call") as capture:
        await contact_resolve(name="Person A")
        await contact_resolve(name="Person B")

    first = capture.call_args_list[0].kwargs
    second = capture.call_args_list[1].kwargs
    assert first["input_payload"] == {}
    assert second["input_payload"] == {}
    assert first["input_fingerprint"] != second["input_fingerprint"]


def _passthrough_tool_decorator(*_args: Any, **_kwargs: Any):
    def decorator(fn):
        return fn

    return decorator


async def test_tool_call_logging_mcp_emits_structured_error_on_raise(caplog) -> None:
    """A wrapped tool that raises emits an error-level log carrying tool/butler/module."""
    mock_mcp = MagicMock()
    mock_mcp.tool = _passthrough_tool_decorator
    proxy = _ToolCallLoggingMCP(mock_mcp, "finance", module_name="finance")

    @proxy.tool(name="detect_recurring")
    async def detect_recurring() -> dict[str, Any]:
        raise ValueError("boom")

    with (
        patch("butlers.mcp_wrappers.capture_tool_call"),
        caplog.at_level(logging.ERROR, logger="butlers.mcp_wrappers"),
    ):
        with pytest.raises(ValueError, match="boom"):
            await detect_recurring()

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "expected an error-level log record for the failed tool call"
    record = errors[0]
    assert record.exception == "ValueError"  # structured field for log_scanner exc_type
    assert record.butler_name == "finance"


async def test_span_wrapping_mcp_emits_structured_error_on_raise(caplog) -> None:
    """The span-wrapping proxy also emits an error-level log on tool failure."""
    mock_mcp = MagicMock()
    mock_mcp.tool = _passthrough_tool_decorator
    proxy = _SpanWrappingMCP(mock_mcp, "finance", module_name="finance")

    @proxy.tool(name="detect_recurring")
    async def detect_recurring() -> dict[str, Any]:
        raise ValueError("boom")

    with (
        patch("butlers.mcp_wrappers.capture_tool_call"),
        caplog.at_level(logging.ERROR, logger="butlers.mcp_wrappers"),
    ):
        with pytest.raises(ValueError, match="boom"):
            await detect_recurring()

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "expected an error-level log record for the failed tool call"
    record = errors[0]
    assert record.exception == "ValueError"
    assert record.butler_name == "finance"


async def test_failed_tool_call_routes_to_scanned_log_and_parses_as_finding(tmp_path) -> None:
    """End-to-end: the structured error reaches logs/butlers/<butler>.log and the
    log-scanner parses it into a non-suppressed QaFinding.

    This is the regression guard for the QA blind spot: the per-butler file
    handler routes by butler ContextVar, so the wrapper must bind it for the
    error line to land in the scanned file.
    """
    import json

    from butlers.core.logging import configure_logging, set_butler_context
    from butlers.core.qa.sources.log_scanner import LogScannerSource

    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)
    saved_level = root_logger.level
    saved_filters = list(root_logger.filters)
    try:
        configure_logging(level="INFO", fmt="json", log_root=tmp_path, butler_name="finance")
        # Simulate a tool handler running in a context where the butler is unset
        # (mirrors the async-task case where "butler=None" was observed).
        set_butler_context("other")

        mock_mcp = MagicMock()
        mock_mcp.tool = _passthrough_tool_decorator
        proxy = _ToolCallLoggingMCP(mock_mcp, "finance", module_name="finance")

        @proxy.tool(name="detect_recurring")
        async def detect_recurring() -> dict[str, Any]:
            raise RuntimeError("recurring detection exploded")

        with patch("butlers.mcp_wrappers.capture_tool_call"):
            with pytest.raises(RuntimeError):
                await detect_recurring()

        for handler in root_logger.handlers:
            handler.flush()
    finally:
        for handler in list(root_logger.handlers):
            if handler not in saved_handlers:
                handler.close()
        root_logger.handlers = saved_handlers
        root_logger.filters = saved_filters
        root_logger.setLevel(saved_level)

    log_file = tmp_path / "butlers" / "finance.log"
    assert log_file.exists(), "expected logs/butlers/finance.log to be created"
    contents = log_file.read_text()
    assert "detect_recurring" in contents, (
        "structured error must route to the scanned per-butler log file "
        f"despite butler ContextVar being unrelated; got:\n{contents}"
    )

    # Every emitted line must be valid JSON of the shape log_scanner expects.
    error_lines = [
        json.loads(line)
        for line in contents.splitlines()
        if line.strip() and json.loads(line).get("level") == "error"
    ]
    assert error_lines, "expected at least one error-level JSON line"
    entry = error_lines[0]
    assert entry["exception"] == "RuntimeError"
    assert entry["butler"] == "finance"

    findings = await LogScannerSource(log_root=tmp_path, repo_root=tmp_path).discover(
        lookback_minutes=15
    )
    finance_findings = [f for f in findings if "detect_recurring" in f.event_summary]
    assert finance_findings, "log-scanner must surface the failed tool call as a finding"
    finding = finance_findings[0]
    assert finding.source_type == "log_scanner"
    assert finding.exception_type == "RuntimeError"
