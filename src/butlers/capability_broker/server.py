"""The goal endpoint: MCP streamable HTTP at ``/goals/{goal_id}/mcp`` (RFC 0038 D7).

Authentication runs in the ASGI wrapper before any MCP processing, including
``initialize``. ``tools/list`` is the grant's projection; each ``tools/call`` is
re-authorized by the broker in its own transaction. The endpoint is mounted
only by ``session_app`` when the default-off ``BrokerSwitch`` is on and a
broker was built; otherwise the session listener keeps refusing everything.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool, ToolResult
from pydantic import PrivateAttr
from starlette.responses import JSONResponse

from butlers.core.capability_json import InvalidArguments, parse_json_object

from .contracts import BrokerRefused, Reason, token_hash
from .service import Broker

_PATH = re.compile(r"/goals/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/mcp")


@dataclass(frozen=True, slots=True)
class BrokerSwitch:
    """The M6 switch for the goal endpoint. Off until the cutover release."""

    enabled: bool = False


def _credentials() -> tuple[UUID, str]:
    state = get_http_request().state
    return state.broker_goal, state.broker_token


class _BrokerTool(Tool):
    _broker: Broker = PrivateAttr()

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        goal, token = _credentials()
        try:
            result = await self._broker.call(goal, token, self.name, arguments)
        except BrokerRefused as refused:
            data = {"status": "refused", "reason": refused.reason.value}
            if refused.effect_id is not None:
                data["effect_id"] = str(refused.effect_id)
            return self.convert_result(data)
        except Exception:
            return self.convert_result({"status": "refused", "reason": Reason.UNAVAILABLE.value})
        data = asdict(result)
        data["effect_id"] = str(result.effect_id) if result.effect_id else None
        return self.convert_result(data)


class _GrantedTools(Provider):
    """Lists exactly the token's grant; no cross-invocation cache."""

    def __init__(self, broker: Broker):
        super().__init__()
        self._broker = broker

    async def _list_tools(self):
        goal, token = _credentials()
        tools = []
        for capability in await self._broker.list_tools(goal, token):
            tool = _BrokerTool(
                name=capability.identity,
                description=capability.description,
                parameters=capability.input_schema,
            )
            tool._broker = self._broker
            tools.append(tool)
        return tools


class GoalEndpoint:
    """ASGI wrapper: path, bearer authentication and body fences before MCP."""

    def __init__(self, app, broker: Broker, *, max_bytes: int, timeout: float):
        self.app, self.broker = app, broker
        self.max_bytes, self.timeout = max_bytes, timeout

    @asynccontextmanager
    async def lifespan(self):
        """Run the MCP session manager for a host that does not send lifespan events."""
        async with self.app.lifespan(self.app):
            yield

    @staticmethod
    async def _error(scope, receive, send, status: int, code: str) -> None:
        await JSONResponse({"error": code}, status_code=status)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return
        match = _PATH.fullmatch(scope.get("path", ""))
        if match is None or scope.get("query_string", b""):
            return await self._error(scope, receive, send, 404, "unknown_endpoint")
        if scope["method"] != "POST":
            return await self._error(scope, receive, send, 405, "method_not_allowed")
        goal = UUID(match[1])
        headers = scope.get("headers", [])
        try:
            values = [v for k, v in headers if k.lower() == b"authorization"]
            if len(values) != 1 or not values[0].startswith(b"Bearer "):
                raise BrokerRefused(Reason.AUTHENTICATION)
            token = values[0][7:].decode("ascii")
            token_hash(token)
            await self.broker.authenticate(goal, token)
        except (BrokerRefused, UnicodeError) as error:
            unavailable = isinstance(error, BrokerRefused) and error.reason is Reason.UNAVAILABLE
            return await self._error(
                scope,
                receive,
                send,
                503 if unavailable else 401,
                "broker_unavailable" if unavailable else "authentication_refused",
            )
        chunks, length = [], 0
        try:
            async with asyncio.timeout(5):
                while True:
                    event = await receive()
                    if event["type"] != "http.request":
                        return
                    length += len(event.get("body", b""))
                    if length > self.max_bytes:
                        return await self._error(scope, receive, send, 413, "request_too_large")
                    chunks.append(event.get("body", b""))
                    if not event.get("more_body", False):
                        break
            body = b"".join(chunks)
            # Duplicate member names would be silently merged by the MCP parser,
            # letting the digest and the handler see different arguments.
            parse_json_object(body)
        except (InvalidArguments, TimeoutError):
            return await self._error(scope, receive, send, 400, "invalid_request")
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        child = dict(scope)
        child["state"] = dict(scope.get("state") or {}, broker_goal=goal, broker_token=token)
        child.update(path="/", raw_path=b"/", root_path="")
        try:
            async with asyncio.timeout(self.timeout):
                await self.app(child, replay, send)
        except TimeoutError:
            return await self._error(scope, receive, send, 503, "broker_unavailable")


def create_goal_endpoint(
    broker: Broker, *, max_bytes: int = 1024 * 1024, timeout: float = 45
) -> GoalEndpoint:
    """Build the endpoint app; starts nothing and loads no credentials."""
    mcp = FastMCP("Goal capability broker", mask_error_details=True)
    mcp.add_provider(_GrantedTools(broker))
    app = mcp.http_app(path="/", stateless_http=True, json_response=True)
    return GoalEndpoint(app, broker, max_bytes=max_bytes, timeout=timeout)


def session_app(switch: BrokerSwitch, broker: Broker | None):
    """The session listener's app: the goal endpoint only when switched on."""
    if switch.enabled is not True or broker is None:
        from butlers.control import session_refusal_app

        return session_refusal_app
    return create_goal_endpoint(broker)
