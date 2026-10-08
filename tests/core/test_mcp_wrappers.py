"""Tests for MCP wrapper tool-call capture metadata."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from butlers.core.tool_call_capture import (
    reset_current_runtime_trigger_source,
    set_current_runtime_trigger_source,
)
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

    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.chronicler.location_tool_copies import _current_tool_copy, matched_tool_records
    from butlers.core.copy_lifetime import _current_copy_invocation
    from butlers.core.fact_authority import _Invocation, _invocations
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    # This is a software fixture for the real private hook/order, not SQL or
    # registered outer-guard authentication evidence.
    class NativePool:
        committed = True
        receipt = None

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            trace.append("begin")
            yield
            trace.append("commit")

        async def fetchval(self, sql, *args):
            if "context_dispositions" in sql:
                return False
            if "tool_results" in sql:
                trace.append("result_readback")
                return self.receipt if self.committed else None
            if "tool_intents" in sql:
                trace.append("input_readback")
                return self.committed
            return True

        async def execute(self, sql, *args):
            writes.append((sql, args))
            if "tool_results" in sql:
                self.receipt = args[3]
                trace.append("result_capture")
            else:
                trace.append("input_capture")

    async def lock_domain(conn):
        assert conn is pool
        trace.append("policy_lock")

    pool, trace, writes = NativePool(), [], []
    runtime = SimpleNamespace(
        domain=pool, active=True, name="relationship", lock_domain=lock_domain
    )
    _runtimes[pool] = runtime
    import time

    invocation = _Invocation("relationship", str(uuid4()), None, None, time.monotonic() + 60)
    _invocations["native-tool-unit"] = invocation
    token = _current_copy_invocation.set(invocation)
    try:
        from butlers.chronicler.location_retention import PolicyUnavailableError

        for kind in (_ToolCallLoggingMCP, _SpanWrappingMCP):
            trace.clear()
            writes.clear()
            proxy = kind(mock_mcp, "relationship", module_name="memory")

            @proxy.tool(name="memory_catalog_search")
            async def native_search(query: str):
                assert _current_tool_copy.get() is not None
                assert _current_tool_copy.get().runtime is runtime
                trace.append("handler")
                return [{"summary": query}]

            with patch("butlers.mcp_wrappers.capture_tool_call") as capture:
                assert await native_search(query="selected full body") == [
                    {"summary": "selected full body"}
                ]
            assert (
                trace.index("input_capture") < trace.index("commit") < trace.index("input_readback")
            )
            assert trace.index("input_readback") < trace.index("handler")
            assert (
                trace.index("handler")
                < trace.index("result_capture")
                < trace.index("result_readback")
            )
            assert _current_tool_copy.get() is None
            record = capture.call_args.kwargs
            result_args = next(args for sql, args in writes if "tool_results" in sql)
            input_args = next(args for sql, args in writes if "tool_intents" in sql)
            assert input_args[-1].hex() == record["input_fingerprint"]
            witness = {
                "tool_name": record["tool_name"],
                "module_name": record["module_name"],
                "input_digest": input_args[-1],
                "outcome": result_args[1],
                "result_digest": result_args[2],
            }
            call = {
                "name": record["tool_name"],
                "module": record["module_name"],
                "input_fingerprint": record["input_fingerprint"],
                "outcome": "success",
                "result": record["result_payload"],
            }
            assert result_args[2].hex() == fingerprint_tool_call_payload(call["result"])
            assert matched_tool_records([call], [witness])
            assert not matched_tool_records([{**call, "result": []}], [witness])
            assert not matched_tool_records([call, call], [witness])
            assert not matched_tool_records([call], [{**witness, "outcome": None}])
            pool.committed = False
            trace.clear()
            with pytest.raises(PolicyUnavailableError, match="Committed native tool input"):
                await native_search(query="interrupted admission")
            assert "handler" not in trace and _current_tool_copy.get() is None
            pool.committed = True
            _invocations.pop("native-tool-unit")
            with pytest.raises(PolicyUnavailableError, match="invocation differs"):
                await native_search(query="stopped invocation")
            assert "handler" not in trace
            _invocations["native-tool-unit"] = invocation
    finally:
        _current_copy_invocation.reset(token)
        _invocations.pop("native-tool-unit", None)
        _runtimes.pop(pool)


async def test_span_wrapper_captures_day_close_date_and_timezone_binding() -> None:
    """The audited day-close witness retains only its safe target binding."""
    mock_mcp = MagicMock()
    mock_mcp.tool = _passthrough_tool_decorator
    proxy = _SpanWrappingMCP(mock_mcp, "chronicler", module_name="chronicler")

    @proxy.tool()
    async def chronicler_day_close_bundle(
        date_label: str,
        timezone: str,
        private_context: str,
    ) -> dict[str, str]:
        return {"date": date_label}

    side_effect_called = False

    @proxy.tool()
    async def chronicler_submit_correction() -> dict[str, str]:
        nonlocal side_effect_called
        side_effect_called = True
        return {"status": "changed"}

    with patch("butlers.mcp_wrappers.capture_tool_call") as capture:
        trigger_token = set_current_runtime_trigger_source("api:day_close_refresh:2026-03-08")
        try:
            bundle_result = await chronicler_day_close_bundle(
                date_label="2026-03-08",
                timezone="America/Los_Angeles",
                private_context="must-not-be-persisted",
            )
            blocked_result = await chronicler_submit_correction()
        finally:
            reset_current_runtime_trigger_source(trigger_token)

    captured = capture.call_args_list[0].kwargs
    assert captured["input_payload"] == {
        "date_label": "2026-03-08",
        "timezone": "America/Los_Angeles",
    }
    assert bundle_result == {"date": "2026-03-08"}
    assert blocked_result["status"] == "suppressed"
    assert side_effect_called is False
    assert capture.call_args_list[1].kwargs["outcome"] == "suppressed"


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
