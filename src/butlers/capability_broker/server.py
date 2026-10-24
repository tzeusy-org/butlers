"""Streamable HTTP factory, deliberately unmounted from the live control plane.

ASGI authentication runs before MCP initialization, discovery and calls. The
dynamic provider has no global cross-invocation tool cache and never adds
service-plane tools. Runner-owned D8 tools require a later real runner provider.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool, ToolResult
from pydantic import PrivateAttr
from starlette.responses import JSONResponse
from starlette.routing import Mount

from butlers.core.capability_json import InvalidArguments, parse_arguments

from .contracts import BrokerRefused, Reason
from .service import Broker


def _credentials() -> tuple[UUID, str]:
    request = get_http_request()
    return request.state.broker_goal, request.state.broker_token


class _BrokerTool(Tool):
    _broker: Broker = PrivateAttr()

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        goal, token = _credentials()
        try:
            result = await self._broker.call(goal, token, self.name, arguments)
        except BrokerRefused as refused:
            # Closed reasons only; never exception detail or raw argument material.
            return self.convert_result({"status": "refused", "reason": refused.reason.value})
        data = asdict(result)
        if result.effect_id is not None:
            data["effect_id"] = str(result.effect_id)
        return self.convert_result(data)


class _GrantedTools(Provider):
    def __init__(self, broker: Broker):
        super().__init__()
        self.broker = broker

    async def _list_tools(self):
        goal, token = _credentials()
        capabilities = await self.broker.list_tools(goal, token)
        tools = []
        for capability in capabilities:
            tool = _BrokerTool(
                name=capability.mcp_name,
                description=capability.description,
                parameters=capability.input_schema,
            )
            tool._broker = self.broker
            tools.append(tool)
        return tools


class _Authenticate:
    def __init__(self, app, broker: Broker):
        self.app, self.broker = app, broker

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        try:
            parts = scope["path"].strip("/").split("/")
            if len(parts) != 3 or parts[0] != "goals" or parts[2] != "mcp":
                raise BrokerRefused(Reason.AUTHENTICATION)
            goal = UUID(parts[1])
            headers = [v for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
            if len(headers) != 1 or not headers[0].startswith(b"Bearer "):
                raise BrokerRefused(Reason.AUTHENTICATION)
            token = headers[0][7:].decode("ascii")
            await self.broker.authenticate(goal, token)
        except (BrokerRefused, ValueError, UnicodeError):
            return await JSONResponse({"error": "authentication_refused"}, status_code=401)(
                scope, receive, send
            )
        except Exception:
            return await JSONResponse({"error": "broker_unavailable"}, status_code=503)(
                scope, receive, send
            )
        if scope["method"] == "POST":
            # MCP's JSON decoder must not erase duplicate argument names first.
            # Pass the exact bytes onward after validation, without normalizing them.
            chunks, length = [], 0
            try:
                async with asyncio.timeout(5):
                    while True:
                        event = await receive()
                        if event["type"] != "http.request":
                            return
                        chunk = event.get("body", b"")
                        length += len(chunk)
                        if length > 16 * 1024 * 1024:
                            raise InvalidArguments("request_too_large")
                        chunks.append(chunk)
                        if not event.get("more_body", False):
                            break
                body = b"".join(chunks)
                parse_arguments(body)
            except (InvalidArguments, TimeoutError):
                return await JSONResponse({"error": "invalid_request"}, status_code=400)(
                    scope, receive, send
                )

            original_receive = receive
            delivered = False

            async def checked_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await original_receive()

            receive = checked_receive
        scope.setdefault("state", {}).update(broker_goal=goal, broker_token=token)
        await self.app(scope, receive, send)


def create_app(broker: Broker):
    """Return an unstarted app; caller must explicitly supply all real adapters."""
    from starlette.applications import Starlette

    mcp = FastMCP("Goal capability broker", mask_error_details=True)
    mcp.add_provider(_GrantedTools(broker))
    http = mcp.http_app(path="/", stateless_http=True, json_response=True)
    app = Starlette(routes=[Mount("/goals/{goal_id}/mcp", app=http)], lifespan=http.lifespan)
    return _Authenticate(app, broker)
