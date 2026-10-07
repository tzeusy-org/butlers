"""Registered custody admission in the actual FastMCP tool execution task.

This middleware precedes FunctionTool instrumentation on either MCP transport.
It does not obtain authority from an ASGI ContextVar, session ID or source DTO.
SQL verifies the online source challenge and owns the same committing writer.
No server/daemon deployment integration is implied by this module's existence.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams

from butlers.core.custody_admission import CustodyAdmission
from butlers.core.custody_source import (
    WIRE_LIMIT,
    CustodyError,
    VerifiedCustodyCall,
    canonical_uuid,
    closed_object,
    current_custody_call,
    parse_wire,
    sha256_hex,
)

_RESERVED = frozenset(
    {"custody", "custody_wire", "custody_source", "custody_call", "custody_principal"}
)


@dataclass(frozen=True)
class _AdmittedApply:
    admission: CustodyAdmission
    raw: bytes
    call: VerifiedCustodyCall


@dataclass(frozen=True)
class _AdmittedChallenge:
    admission: CustodyAdmission
    call_ref: str
    operation_digest: str
    receipt: dict


_apply: ContextVar[_AdmittedApply | None] = ContextVar("custody_tool_apply", default=None)
_challenge: ContextVar[_AdmittedChallenge | None] = ContextVar(
    "custody_tool_challenge", default=None
)


@dataclass
class _CaptureFrame:
    tool_name: str
    record: dict | None = None


_capture: ContextVar[_CaptureFrame | None] = ContextVar("custody_capture_after_ack", default=None)


def stage_custody_capture(record: dict) -> bool:
    """Consume only custody core captures; retain no nonce, wire or result body."""
    name = record.get("tool_name")
    if name not in {"custody.apply", "custody.challenge"}:
        return False
    frame = _capture.get()
    if frame is not None and frame.tool_name == name:
        frame.record = {
            key: record[key]
            for key in ("tool_name", "module_name", "input_fingerprint")
            if key in record
        }
    # An unavailable definition/direct call has no admitted frame and cannot
    # produce an execution-success witness. Never retain its private payload.
    return True


@contextmanager
def _capture_after_ack(tool_name: str):
    frame = _CaptureFrame(tool_name)
    token = _capture.set(frame)
    outcome = "error"
    try:
        yield
        outcome = "success"
    except asyncio.CancelledError:
        outcome = "unknown"
        raise
    except CustodyError as exc:
        if str(exc) == "unknown":
            outcome = "unknown"
        raise
    finally:
        _capture.reset(token)
        if frame.record is not None:
            from butlers.core.tool_call_capture import capture_tool_call

            capture_tool_call(**frame.record, outcome=outcome)


def _raw_wire(wire: Any) -> bytes:
    if type(wire) is not str or len(wire) > WIRE_LIMIT:
        raise CustodyError("invalid")
    try:
        raw = wire.encode("utf-8")
    except UnicodeError:
        raise CustodyError("invalid") from None
    parse_wire(raw)
    return raw


def admitted_apply(admission: CustodyAdmission, wire: str) -> VerifiedCustodyCall:
    """Service handler consumes only its guard's exact private active verdict."""
    value = _apply.get()
    if (
        value is None
        or value.admission is not admission
        or value.raw != _raw_wire(wire)
        or current_custody_call() is not value.call
    ):
        raise CustodyError("refused")
    admission.current_verified_writer()
    return value.call


def admitted_challenge(admission: CustodyAdmission, call_ref: str, operation_digest: str) -> dict:
    value = _challenge.get()
    if (
        value is None
        or value.admission is not admission
        or value.call_ref != call_ref
        or value.operation_digest != operation_digest
    ):
        raise CustodyError("refused")
    return dict(value.receipt)


class CustodyToolAdmission(Middleware):
    """Constructor-fixed outer tool guard; COMMIT acknowledgement precedes return.

    A ToolResult with is_error set must unwind the guarded transaction, even
    when FastMCP represents an error as a result rather than raising. Protected
    tools cannot be detached background tasks: their private live writer ends
    with this call, and no queued task can inherit a currentness verdict.
    """

    def __init__(self, admission: CustodyAdmission) -> None:
        self._admission = admission

    @staticmethod
    def _success(result: Any) -> ToolResult:
        if not isinstance(result, ToolResult) or result.is_error:
            raise CustodyError("refused")
        return result

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        message = context.message
        arguments = message.arguments if message.arguments is not None else {}
        meta = message.meta.model_dump() if message.meta is not None else {}
        # FastMCP's public call_tool constructs a fresh params object and does
        # not copy SDK _meta/task fields into it. Use the actual request context
        # as well, so an online client cannot hide a reserved claim there. This
        # metadata supplies NO admission or attribution authority.
        request = (
            context.fastmcp_context.request_context if context.fastmcp_context is not None else None
        )
        if request is not None and request.meta is not None:
            meta |= request.meta.model_dump(exclude_none=True)
        if _RESERVED.intersection(meta) or _RESERVED.intersection(arguments):
            raise CustodyError("refused")
        if message.name not in {"custody.challenge", "custody.apply"}:
            if message.name.startswith("custody."):
                raise CustodyError("refused")
            return await call_next(context)
        if message.task is not None or (request is not None and request.experimental.is_task):
            raise CustodyError("refused")
        with _capture_after_ack(message.name):
            return await self._protected(context, call_next, arguments)

    async def _protected(self, context, call_next, arguments) -> ToolResult:
        message = context.message
        if message.name == "custody.challenge":
            closed_object(arguments, required={"call_ref", "operation_digest"})
            call_ref = canonical_uuid(arguments["call_ref"])
            operation_digest = sha256_hex(arguments["operation_digest"])
            receipt = await self._admission.challenge(uuid.UUID(call_ref), operation_digest)
            token = _challenge.set(
                _AdmittedChallenge(self._admission, call_ref, operation_digest, receipt)
            )
            try:
                return self._success(await call_next(context))
            finally:
                _challenge.reset(token)
        # Bound the INNER UTF-8 wire, not its escaped outer JSON string. An
        # inner wire containing backslashes can be valid at 8192 bytes while
        # its enclosing MCP argument representation is considerably larger.
        if type(arguments) is not dict or arguments.keys() != {"wire"}:
            raise CustodyError("invalid")
        raw = _raw_wire(arguments["wire"])
        async with self._admission.verified(raw) as (_, call):
            token = _apply.set(_AdmittedApply(self._admission, raw, call))
            try:
                result = self._success(await call_next(context))
            finally:
                _apply.reset(token)
        # Exiting verified() acknowledges the actual domain COMMIT and cleanup.
        # A lost acknowledgement raises unknown instead of returning success.
        return result


class CustodyJsonRpcGuard:
    """Reject ambiguous custody JSON-RPC before SDK/REQUEST instrumentation.

    Generic MCP requests retain their existing limits and parsing. This is not
    an authentication verdict or an ASGI-context principal; actual protected
    tool admission occurs in its execution task on the committing writer.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    def __getattr__(self, name: str) -> Any:
        # Preserve Starlette route/lifespan inspection, as the existing
        # runtime-session guard does. HTTP execution still enters __call__;
        # forwarding app metadata grants no source or admission authority.
        return getattr(self._app, name)

    @staticmethod
    def _valid_custody(raw: bytes) -> bool:
        duplicate = False

        def pairs(items):
            nonlocal duplicate
            value = {}
            for key, item in items:
                if key in value:
                    duplicate = True
                value[key] = item
            return value

        try:
            # Classify exactly the encodings the SDK bytes parser accepts. A
            # UTF-16/32 or BOM envelope must not evade the custody classifier.
            # Generic traffic retains that parser's existing behavior.
            envelope = json.loads(raw, object_pairs_hook=pairs)
        except (ValueError, UnicodeError, RecursionError):
            # The SDK rejects its malformed generic envelope. No private
            # admission is minted here, even for a request we cannot classify.
            return True
        if type(envelope) is not dict or envelope.get("method") != "tools/call":
            return True
        params = envelope.get("params")
        if type(params) is not dict or not str(params.get("name", "")).startswith("custody."):
            return True
        try:
            try:
                json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError):
                return False
            if duplicate or len(raw) > WIRE_LIMIT * 6 + 2048:
                return False
            if envelope.keys() != {"jsonrpc", "id", "method", "params"}:
                return False
            if envelope["jsonrpc"] != "2.0" or type(envelope["id"]) not in (str, int):
                return False
            if not {"name", "arguments"} <= params.keys() or params.keys() - {
                "name",
                "arguments",
                "_meta",
            }:
                return False
            if params["name"] not in {"custody.apply", "custody.challenge"}:
                return False
            arguments = params["arguments"]
            meta = params.get("_meta", {})
            if type(meta) is not dict or _RESERVED.intersection(meta):
                return False
            if params["name"] == "custody.apply":
                if type(arguments) is not dict or arguments.keys() != {"wire"}:
                    return False
                _raw_wire(arguments["wire"])
            else:
                closed_object(arguments, required={"call_ref", "operation_digest"})
                canonical_uuid(arguments["call_ref"])
                sha256_hex(arguments["operation_digest"])
        except CustodyError:
            return False
        return True

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or str(scope.get("path", "")).rstrip("/") not in {"/mcp", "/messages"}
        ):
            await self._app(scope, receive, send)
            return
        messages, body = [], bytearray()
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        if not self._valid_custody(bytes(body)):
            payload = (
                b'{"jsonrpc":"2.0","id":null,"error":{"code":-32602,"message":"custody invalid"}}'
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(payload)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return

        async def replay():
            if messages:
                return messages.pop(0)
            return await receive()

        await self._app(scope, replay, send)
