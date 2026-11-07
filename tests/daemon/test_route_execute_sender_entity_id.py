"""Tests for sender entity_id injection via route.execute.

Verifies that when route.execute receives a request_context with
source_sender_entity_id, the value is captured and injected into
_routing_ctx_var before spawner.trigger() is called, so that
memory_store_fact can use it as the default entity_id.

Each test boots a real daemon (``booted_daemon``) and calls route.execute over MCP;
the route inbox, background task and spawner are the production ones. The fake
runtime reads the routing context at invocation time, and route-inbox outcomes
are read back from the clone. Two failure modes the fake runtime cannot produce
(the spawner itself raising, and an opaque unsuccessful result) inject that result
on the booted daemon's spawner; nothing in the lifecycle is patched.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.core.routing_context import _routing_ctx_var
from butlers.testing.daemon import write_butler_config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_REQUEST_ID = "018f6f4e-5b3b-7b2d-9c2f-aabbccddee00"


def _reset_otel_global_state():
    """Fully reset the OpenTelemetry global tracer provider state."""
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None


@pytest.fixture
def otel_provider(monkeypatch: pytest.MonkeyPatch):
    """Record every span the daemon emits, so the content-blind checks can read them."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    _reset_otel_global_state()
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "butler-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()
    _reset_otel_global_state()


def _base_request_context(
    *,
    source_sender_entity_id: str | None = None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "request_id": _REQUEST_ID,
        "received_at": "2026-03-10T00:00:00Z",
        "source_channel": "telegram_bot",
        "source_endpoint_identity": "switchboard",
        "source_sender_identity": "owner",
        "source_thread_identity": "12345",
    }
    if source_sender_entity_id is not None:
        ctx["source_sender_entity_id"] = source_sender_entity_id
    return ctx


async def _route(booted, input_data: dict[str, Any], **request: Any) -> dict[str, Any]:
    return await booted.call_tool(
        "route.execute",
        {
            "schema_version": "route.v1",
            "request_context": _base_request_context(**request),
            "input": input_data,
        },
    )


async def _drain(booted) -> None:
    """Wait for every background route task the daemon started."""
    await asyncio.gather(*tuple(booted.daemon._route_inbox_tasks))


async def _inbox_rows(booted) -> list[dict[str, Any]]:
    rows = await booted.daemon.db.pool.fetch(
        "SELECT id, lifecycle_state, error, route_envelope FROM route_inbox"
    )
    return [dict(row) for row in rows]


def _capture_routing_ctx(captured: list[Any]):
    """A scripted reply that records the routing context the session runs under."""

    def reply(_invocation: dict[str, Any]) -> str:
        captured.append(_routing_ctx_var.get())
        return "ok"

    return reply


class ContentLeak(AssertionError):
    """A content-blind route's identifier or content reached a log record or span."""


# Known production leaks (bu-rvgzmf.9). Strict, and scoped to ContentLeak, so the
# tests' functional assertions still fail normally and a fix makes these XPASS.
_KNOWN_LEAK = pytest.mark.xfail(
    strict=True,
    raises=ContentLeak,
    reason=(
        "bu-rvgzmf.9: route_inbox, spawner and sessions logs (and the background task "
        "name) carry raw ids and runtime error text for content-blind routes"
    ),
)


def _assert_content_blind(
    caplog: pytest.LogCaptureFixture, spans: InMemorySpanExporter | None, *needles: str
) -> None:
    """No captured log record or span attribute carries any of *needles*.

    Failures name each offending record (logger, file:line) or span, so a leak points
    at its source.
    """
    leaks = []
    for needle in needles:
        for record in caplog.records:
            if needle in repr(record.__dict__):
                fields = sorted(k for k, v in record.__dict__.items() if needle in repr(v))
                leaks.append(
                    f"{needle!r} in log {record.name} {record.pathname}:{record.lineno} {fields}"
                )
        for span in spans.get_finished_spans() if spans is not None else ():
            if needle in repr(dict(span.attributes or {})):
                leaks.append(f"{needle!r} in span {span.name}")
    if leaks:
        raise ContentLeak("\n".join(leaks))


class TestRouteExecuteSenderEntityIdInjection:
    """Verify that source_sender_entity_id is injected into _routing_ctx_var."""

    async def test_sender_entity_id_injected_into_routing_ctx_var(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        """_routing_ctx_var is set with source_entity_id when source_sender_entity_id present."""
        booted = await booted_daemon(write_butler_config(tmp_path))
        sender_entity_id = "550e8400-e29b-41d4-a716-446655440000"
        captured_routing_ctx: list[Any] = []
        booted.runtime.script(_capture_routing_ctx(captured_routing_ctx))

        result = await _route(
            booted,
            {"prompt": "Store some info about me."},
            source_sender_entity_id=sender_entity_id,
        )
        await booted.runtime.wait_for_invocations(1)

        assert result["status"] == "accepted"
        assert len(captured_routing_ctx) == 1
        ctx = captured_routing_ctx[0]
        assert ctx is not None
        assert isinstance(ctx, dict)
        assert ctx.get("source_entity_id") == sender_entity_id

    async def test_no_sender_entity_id_leaves_routing_ctx_unset(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        """Without an entity ID, route still propagates the validated source channel."""
        booted = await booted_daemon(write_butler_config(tmp_path))
        captured_routing_ctx: list[Any] = []
        booted.runtime.script(_capture_routing_ctx(captured_routing_ctx))

        result = await _route(booted, {"prompt": "Just a message."})  # no entity_id
        await booted.runtime.wait_for_invocations(1)

        assert result["status"] == "accepted"
        assert captured_routing_ctx == [{"request_context": {"source_channel": "telegram_bot"}}]

    async def test_conceptual_message_is_persisted_and_available_as_structured_context(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        """Spec: REQ-conversation-decomposition-001, REQ-entity-identity-001."""
        from fastmcp import Client

        booted = await booted_daemon(write_butler_config(tmp_path))
        conceptual_message = {
            "signal_type": "health",
            "confidence": "HIGH",
            "excerpts": [
                {
                    "message_id": "m1",
                    "sender": "Known speaker",
                    "sender_identity": "15551234567@s.whatsapp.net",
                    "sender_entity_id": "11111111-1111-1111-1111-111111111111",
                    "text": "My knee hurts",
                    "timestamp": "2026-08-24T10:00:00Z",
                }
            ],
        }
        captured_routing_ctx: list[Any] = []
        booted.runtime.script(_capture_routing_ctx(captured_routing_ctx))

        # The structured context arrives only inside the routed input, never as a
        # separate tool parameter a caller could set.
        async with Client(booted.url) as client:
            (route_tool,) = [t for t in await client.list_tools() if t.name == "route.execute"]
        assert "internal_context" not in route_tool.inputSchema["properties"]

        result = await _route(
            booted,
            {
                "prompt": "Store health facts from the conceptual excerpt.",
                "context": {"conceptual_message": conceptual_message},
            },
        )
        await booted.runtime.wait_for_invocations(1)

        assert result["status"] == "accepted"
        (row,) = await _inbox_rows(booted)
        persisted_envelope = row["route_envelope"]
        assert persisted_envelope["input"]["context"]["conceptual_message"] == conceptual_message
        assert captured_routing_ctx == [
            {
                "conceptual_message": conceptual_message,
                "request_context": {"source_channel": "telegram_bot"},
            }
        ]

    async def test_route_inbox_insert_failure_is_content_blind(
        self,
        booted_daemon,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        otel_provider: InMemorySpanExporter,
    ) -> None:
        sentinel = "222222222222222@lid PRIVATE MESSAGE SQL SELECT"
        booted = await booted_daemon(write_butler_config(tmp_path))
        # Fault injection at the one call under test; the daemon is otherwise real.
        monkeypatch.setattr(
            "butlers.core_tools._routing.route_inbox_insert",
            AsyncMock(side_effect=RuntimeError(sentinel)),
        )

        with caplog.at_level("DEBUG", logger="butlers"):
            result = await _route(booted, {"prompt": "Store a fact."})

        assert result["status"] == "error"
        assert result["error"]["class"] == "internal_error"
        assert sentinel not in result["error"]["message"]
        # Positive control: the failure was logged, so the capture is live.
        assert "route_inbox_insert failed failure_class=RuntimeError" in caplog.text
        _assert_content_blind(caplog, otel_provider, sentinel)

    @_KNOWN_LEAK
    async def test_background_runtime_failure_is_content_blind(
        self,
        booted_daemon,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        otel_provider: InMemorySpanExporter,
    ) -> None:
        sentinel = "15551234567@s.whatsapp.net PRIVATE MESSAGE SQL SELECT"
        booted = await booted_daemon(write_butler_config(tmp_path))
        # The spawner itself raising (not the runtime) is the failure under test.
        monkeypatch.setattr(
            booted.daemon.spawner, "trigger", AsyncMock(side_effect=RuntimeError(sentinel))
        )

        with caplog.at_level("DEBUG", logger="butlers"):
            result = await _route(
                booted,
                {"prompt": "Store a fact.", "context": {"conceptual_message": {"excerpts": []}}},
            )
            await _drain(booted)

        assert result["status"] == "accepted"
        (row,) = await _inbox_rows(booted)
        assert row["lifecycle_state"] == "errored"
        assert row["error"] == "route runtime failed (RuntimeError)"
        assert "route.execute" in caplog.text  # positive control: capture is live
        _assert_content_blind(caplog, otel_provider, sentinel, _REQUEST_ID, str(row["id"]))

    @pytest.mark.parametrize(
        ("runtime_failure", "expected_class"),
        [
            (
                RuntimeError("15551234567@s.whatsapp.net PRIVATE MESSAGE SQL SELECT"),
                "RuntimeError",
            ),
            ("provider returned opaque failure text", "runtime_unsuccessful"),
        ],
        ids=("runtime-raises", "opaque-result"),
    )
    @_KNOWN_LEAK
    async def test_unsuccessful_runtime_result_uses_bounded_failure_class(
        self,
        booted_daemon,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        otel_provider: InMemorySpanExporter,
        runtime_failure: Any,
        expected_class: str,
    ) -> None:
        booted = await booted_daemon(write_butler_config(tmp_path))
        if isinstance(runtime_failure, BaseException):
            # The runtime raises; the real spawner turns it into an unsuccessful
            # result whose error is "RuntimeError: <message>".
            booted.runtime.script(runtime_failure)
        else:
            # An unsuccessful result with opaque provider text cannot come from the
            # fake runtime, so it is injected as the spawner's result.
            monkeypatch.setattr(
                booted.daemon.spawner,
                "trigger",
                AsyncMock(
                    return_value=MagicMock(
                        success=False, error=runtime_failure, session_id=uuid.uuid4()
                    )
                ),
            )

        with caplog.at_level("DEBUG", logger="butlers"):
            await _route(
                booted,
                {"prompt": "Store a fact.", "context": {"conceptual_message": {"excerpts": []}}},
            )
            await _drain(booted)

        (row,) = await _inbox_rows(booted)
        assert row["error"] == f"route runtime returned unsuccessful result ({expected_class})"
        assert "route.execute" in caplog.text  # positive control: capture is live
        _assert_content_blind(
            caplog,
            otel_provider,
            "15551234567@s.whatsapp.net",
            "PRIVATE MESSAGE SQL SELECT",
            "provider returned opaque failure text",
            _REQUEST_ID,
            str(row["id"]),
        )

    async def test_conceptual_route_dedup_log_omits_request_and_session_uuids(
        self,
        booted_daemon,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        booted = await booted_daemon(write_butler_config(tmp_path))
        conceptual = {
            "prompt": "Store a fact.",
            "context": {"conceptual_message": {"excerpts": []}},
        }
        # A first, successful session for this request makes the repeat a dedup.
        await _route(booted, conceptual)
        await booted.runtime.wait_for_invocations(1)
        await _drain(booted)

        # Only the dedup call is under test; the first session's own logs are
        # covered by the bu-rvgzmf.9 cases above.
        caplog.clear()
        with caplog.at_level("INFO", logger="butlers"):
            result = await _route(booted, conceptual)

        assert result["dedup"] is True
        session_uuid = result["existing_session_id"]
        assert "route.execute: dedup" in caplog.text  # positive control
        _assert_content_blind(caplog, None, _REQUEST_ID, session_uuid)
