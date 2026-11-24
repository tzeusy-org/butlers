"""Tests for the MCP wrapper chain: tool-call capture and assertion verification.

Assertion verification (bu-pkuny2) is exercised over MCP streamable HTTP against
a domain FastMCP whose tools register through ``_SpanWrappingMCP``, so each
refusal is shown before argument coercion, the wrapper and the handler.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from butlers.capability_broker.assertions import ASSERTION_HEADER, AssertionSigner
from butlers.capability_broker.verification import (
    AssertionVerificationMiddleware,
    DomainVerificationSwitch,
    DomainVerifier,
    attach_domain_verification,
)
from butlers.core import goal_lineage
from butlers.core.capability_json import arguments_digest
from butlers.core.runtime_probe_control.keys import VerifierKey, VerifierKeyring
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


@pytest.mark.parametrize("proxy_cls", [_SpanWrappingMCP, _ToolCallLoggingMCP])
async def test_consolidation_session_suppresses_every_tool(proxy_cls) -> None:
    """Consolidation is output-only: no read or write tool may run (bu-dhloi1.1).

    A read would show an owner-partition session other partitions' content; a
    write would land outside the partition-revalidated executor.
    """
    mock_mcp = MagicMock()
    mock_mcp.tool = _passthrough_tool_decorator
    proxy = proxy_cls(mock_mcp, "relationship", module_name="memory")
    called: list[str] = []

    @proxy.tool()
    async def memory_recall(topic: str) -> dict[str, str]:
        called.append("memory_recall")
        return {"facts": "third-party"}

    @proxy.tool()
    async def memory_store_fact(content: str) -> dict[str, str]:
        called.append("memory_store_fact")
        return {"status": "stored"}

    with patch("butlers.mcp_wrappers.capture_tool_call") as capture:
        token = set_current_runtime_trigger_source("schedule:consolidation")
        try:
            read_result = await memory_recall(topic="anything")
            write_result = await memory_store_fact(content="restated")
        finally:
            reset_current_runtime_trigger_source(token)
        allowed = await memory_recall(topic="anything")

    suppressed = {
        "status": "suppressed",
        "reason": "consolidation_session_tool_policy",
        "retryable": False,
    }
    assert read_result == suppressed
    assert write_result == suppressed
    assert allowed == {"facts": "third-party"}
    assert called == ["memory_recall"]
    outcomes = [c.kwargs["outcome"] for c in capture.call_args_list]
    assert outcomes == ["suppressed", "suppressed", "success"]


# ---------------------------------------------------------------------------
# Broker assertion verification runs first in the wrapper chain (bu-pkuny2)
# ---------------------------------------------------------------------------

_NOW = 1_800_000_000
_ACCEPT = "application/json, text/event-stream"


class _Clock:
    def __init__(self, now: int = _NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return float(self.now)


def _keyring(signer: AssertionSigner) -> VerifierKeyring:
    public = signer.ed25519.public_key().public_bytes_raw()
    return VerifierKeyring(VerifierKey(signer.kid, public, datetime(2026, 1, 1, tzinfo=UTC)))


@pytest.fixture(autouse=True)
def _reset_enforcement():
    yield
    goal_lineage.set_enforcement(False)


def _payload(args: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    payload = {
        "aud": "relationship",
        "principal": "goal",
        "goal_id": str(uuid4()),
        "invocation_id": str(uuid4()),
        "depth": 0,
        "origin": "owner",
        "exposed": False,
        "capability": "relationship_note",
        "args_digest": arguments_digest(args),
        "call_id": str(uuid4()),
        "effect_id": None,
        "approval_ref": None,
        "exp": _NOW + 60,
    }
    payload.update(overrides)
    return payload


class _Domain:
    def __init__(self, client: httpx.AsyncClient, seen: list, signer: AssertionSigner):
        self.client, self.seen, self.signer = client, seen, signer

    async def call(self, args: dict[str, Any], assertions: list[str] | None, name=None):
        headers = [("accept", _ACCEPT)] + [(ASSERTION_HEADER, a) for a in assertions or []]
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name or "relationship_note", "arguments": args},
        }
        response = await self.client.post("/", headers=headers, json=body)
        return response.json()["result"]


@asynccontextmanager
async def _domain(*, verify: bool = True, keyed: bool = True, clock=None, started_at=None):
    signer = AssertionSigner("broker-1", Ed25519PrivateKey.generate())
    server = FastMCP("relationship")
    proxy = _SpanWrappingMCP(server, "relationship", module_name="relationship")
    seen: list[Any] = []

    @proxy.tool()
    async def relationship_note(count: int, text: str) -> dict[str, Any]:
        seen.append(goal_lineage.current_lineage())
        return {"ok": True, "count": count}

    if verify:
        verifier = DomainVerifier(
            "relationship",
            _keyring(signer) if keyed else None,
            clock=clock or _Clock(),
            started_at=_NOW - 30 if started_at is None else started_at,
        )
        server.middleware.insert(0, AssertionVerificationMiddleware(verifier))
        goal_lineage.set_enforcement(True)
    app = server.http_app(path="/", stateless_http=True, json_response=True)
    transport = httpx.ASGITransport(app=app)
    with patch("butlers.mcp_wrappers.capture_tool_call"):
        async with (
            app.lifespan(app),
            httpx.AsyncClient(transport=transport, base_url="http://d") as client,
        ):
            yield _Domain(client, seen, signer)


_ARGS = {"count": 1, "text": "hello"}


def _refused(result: dict[str, Any]) -> bool:
    return result.get("isError") is True and "assertion_refused" in str(result["content"])


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        (
            "forged signature",
            lambda d, p: AssertionSigner("broker-1", Ed25519PrivateKey.generate()).sign(p),
        ),
        ("unknown kid", lambda d, p: AssertionSigner("other", d.signer.ed25519).sign(p)),
        ("wrong aud", lambda d, p: d.signer.sign({**p, "aud": "general"})),
        ("expired", lambda d, p: d.signer.sign({**p, "exp": _NOW})),
        ("capability mismatch", lambda d, p: d.signer.sign({**p, "capability": "other_tool"})),
        ("digest mismatch", lambda d, p: d.signer.sign({**p, "args_digest": "0" * 64})),
    ],
)
async def test_invalid_assertion_is_refused_before_the_handler(label, mutate):
    async with _domain() as domain:
        result = await domain.call(_ARGS, [mutate(domain, _payload(_ARGS))])
        assert _refused(result), label
        assert domain.seen == []


async def test_coercion_cannot_launder_a_digest_mismatch():
    """The digest binds the raw wire arguments, not what coercion makes of them."""
    async with _domain() as domain:
        assertion = domain.signer.sign(_payload(_ARGS))
        result = await domain.call({"count": "1", "text": "hello"}, [assertion])
        assert _refused(result)
        assert domain.seen == []


async def test_replayed_call_id_is_refused():
    async with _domain() as domain:
        assertion = domain.signer.sign(_payload(_ARGS))
        first = await domain.call(_ARGS, [assertion])
        assert first["structuredContent"] == {"ok": True, "count": 1}
        assert _refused(await domain.call(_ARGS, [assertion]))
        assert len(domain.seen) == 1


async def test_missing_or_duplicated_assertion_header_is_refused():
    async with _domain() as domain:
        assertion = domain.signer.sign(_payload(_ARGS))
        assert _refused(await domain.call(_ARGS, None))
        assert _refused(await domain.call(_ARGS, [assertion, assertion]))
        assert domain.seen == []


async def test_valid_assertion_runs_the_handler_with_its_lineage():
    async with _domain() as domain:
        payload = _payload(_ARGS, exposed=True, origin="third_party")
        result = await domain.call(_ARGS, [domain.signer.sign(payload)])
        assert result["structuredContent"] == {"ok": True, "count": 1}
        (lineage,) = domain.seen
        assert str(lineage.goal_id) == payload["goal_id"]
        assert str(lineage.invocation_id) == payload["invocation_id"]
        assert lineage.exposed is True and lineage.origin == "third_party"
        assert goal_lineage.current_lineage() is None  # Bound for that call only.


async def test_keyless_domain_refuses_every_call():
    async with _domain(keyed=False) as domain:
        assert _refused(await domain.call(_ARGS, [domain.signer.sign(_payload(_ARGS))]))
        assert domain.seen == []


async def test_assertion_issued_before_the_process_started_is_refused():
    """Replay protection across a restart: the restarted domain has no seen set."""
    clock = _Clock()
    async with _domain(clock=clock, started_at=_NOW) as domain:
        before_start = domain.signer.sign(_payload(_ARGS, exp=_NOW + 59))
        assert _refused(await domain.call(_ARGS, [before_start]))
        at_start = domain.signer.sign(_payload(_ARGS, exp=_NOW + 60))
        assert (await domain.call(_ARGS, [at_start]))["structuredContent"]["ok"] is True
        assert len(domain.seen) == 1


async def test_wrapper_refuses_a_handler_call_that_skipped_verification():
    """Enforced, the wrapper's first step refuses any call with no lineage for its tool."""
    seen = []
    proxy = _SpanWrappingMCP(FastMCP("relationship"), "relationship", module_name="m")

    @proxy.tool()
    async def relationship_note(count: int) -> dict[str, Any]:
        seen.append(count)
        return {"ok": True}

    with patch("butlers.mcp_wrappers.capture_tool_call"):
        goal_lineage.set_enforcement(True)
        tool = await proxy._mcp.get_tool("relationship_note")
        # Raised, so MCP reports an error result rather than a committed-looking payload.
        with pytest.raises(ToolError, match='"reason": "authentication"'):
            await tool.fn(count=1)
        other = goal_lineage.VerifiedLineage.from_payload(
            _payload({"count": 1}, capability="other_tool")
        )
        with goal_lineage.lineage_scope(other), pytest.raises(ToolError, match="refused"):
            await tool.fn(count=1)
        assert seen == []


async def test_switch_off_keeps_todays_behavior(tmp_path):
    async with _domain(verify=False) as domain:
        result = await domain.call(_ARGS, ["not-an-assertion"])
        assert result["structuredContent"] == {"ok": True, "count": 1}
        assert domain.seen == [None]
    server = FastMCP("relationship")
    before = list(server.middleware)
    assert DomainVerificationSwitch().enabled is False
    off = attach_domain_verification(server, DomainVerificationSwitch(), audience="relationship")
    assert off is None and server.middleware == before
    assert not goal_lineage.enforcement_enabled()
    keyless = attach_domain_verification(
        server,
        DomainVerificationSwitch(enabled=True),
        audience="relationship",
        keyring_path=tmp_path / "absent",
    )
    assert keyless is not None and goal_lineage.enforcement_enabled()
    assert isinstance(server.middleware[0], AssertionVerificationMiddleware)
