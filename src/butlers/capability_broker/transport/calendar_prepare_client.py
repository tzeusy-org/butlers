"""Fixed owner-preparation client and closed wire values, without the owning server."""

from __future__ import annotations

import asyncio
import re
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from butlers.core.capability_json import canonical_bytes

from .client import PrivateClient, PrivateHTTPTransport
from .config import OPERATION_SECONDS, REQUEST_LIMIT
from .protocol import ProtocolError, exact, rpc_request, strict_json, uuid_text

PROTOCOL = "calendar-owner-prepare-v1"

TOOL = "calendar_owner_prepare"


def prepare_request(value, domain):
    exact(
        value,
        (
            "protocol",
            "domain",
            "root_id",
            "parent_id",
            "parent_digest",
            "expected_args_digest",
            "edits",
        ),
    )
    if value["protocol"] != PROTOCOL or value["domain"] != domain:
        raise ProtocolError("calendar_prepare_target")
    uuid_text(value["root_id"])
    uuid_text(value["parent_id"])
    for field in ("parent_digest", "expected_args_digest"):
        if type(value[field]) is not str or not re.fullmatch(r"[0-9a-f]{64}", value[field]):
            raise ProtocolError("calendar_prepare_digest")
    edits = value["edits"]
    if (
        type(edits) is not dict
        or not edits
        or set(edits) - {"entry_id", "response_status", "send_updates"}
    ):
        raise ProtocolError("calendar_prepare_edits")
    if "entry_id" in edits:
        uuid_text(edits["entry_id"])
    if "response_status" in edits and (
        type(edits["response_status"]) is not str
        or edits["response_status"]
        not in {
            "accepted",
            "declined",
            "tentative",
        }
    ):
        raise ProtocolError("calendar_prepare_status")
    if "send_updates" in edits and (
        type(edits["send_updates"]) is not str
        or edits["send_updates"] not in {"none", "all", "externalOnly"}
    ):
        raise ProtocolError("calendar_prepare_updates")
    return value


def prepare_reply(value, outgoing):
    exact(value, ("root_id", "candidate_id", "candidate_digest"))
    uuid_text(value["candidate_id"])
    if (
        value["root_id"] != outgoing["root_id"]
        or type(value["candidate_digest"]) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", value["candidate_digest"])
    ):
        raise ProtocolError("calendar_prepare_binding")
    return value


def prepare_rpc(value, domain):
    if type(value) is dict and value.get("method") == "tools/call":
        exact(value, ("jsonrpc", "id", "method", "params"))
        if value["jsonrpc"] != "2.0" or type(value["id"]) not in {str, int}:
            raise ProtocolError("rpc_shape")
        params = exact(value["params"], ("name", "arguments"))
        if params["name"] != TOOL:
            raise ProtocolError("tool_binding")
        prepare_request(params["arguments"], domain)
        return value
    return rpc_request(value, domain)


def prepare_rpc_result(raw, outgoing, domain):
    value = exact(strict_json(raw), ("jsonrpc", "id", "result"))
    if (
        value["jsonrpc"] != "2.0"
        or type(value["id"]) is not type(outgoing["id"])
        or value["id"] != outgoing["id"]
    ):
        raise ProtocolError("rpc_binding")
    result = value["result"]
    if outgoing["method"] == "tools/call":
        result = exact(result, ("content", "structuredContent", "isError"))
        app = prepare_reply(result["structuredContent"], outgoing["params"]["arguments"])
        if result["isError"] is not False or result["content"] != [
            {"type": "text", "text": canonical_bytes(app).decode()}
        ]:
            raise ProtocolError("conflicting_content")
    elif outgoing["method"] == "tools/list":
        exact(result, ("tools",))
        if (
            type(result["tools"]) is not list
            or len(result["tools"]) != 1
            or result["tools"][0].get("name") != TOOL
        ):
            raise ProtocolError("tool_catalog")
    elif outgoing["method"] == "initialize":
        exact(result, ("protocolVersion", "capabilities", "serverInfo"))
        if result["protocolVersion"] != outgoing["params"]["protocolVersion"]:
            raise ProtocolError("protocol_version")
    else:
        raise ProtocolError("unexpected_rpc")
    return value


class CalendarPrepareHTTPTransport(PrivateHTTPTransport):
    def _validate_rpc(self, value):
        return prepare_rpc(value, self.config.peer.domain)

    def _validate_result(self, raw, outgoing):
        return prepare_rpc_result(raw, outgoing, self.config.peer.domain)


class CalendarPrepareClient(PrivateClient):
    def __init__(self, config, context):
        self.config = config
        self.transport = CalendarPrepareHTTPTransport(config, context)
        self.http = httpx.AsyncClient(
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(9, connect=3, write=3, pool=1),
        )
        self.lock = asyncio.Lock()

    async def prepare(self, value):
        deadline = time.monotonic() + OPERATION_SECONDS
        encoded = canonical_bytes(value)
        if len(encoded) > REQUEST_LIMIT:
            raise ProtocolError("request_limit")
        value = prepare_request(strict_json(encoded), self.config.peer.domain)
        async with asyncio.timeout_at(deadline):
            async with asyncio.timeout_at(deadline - 1):
                await self.lock.acquire()
            try:
                self.transport.backend.deadline = deadline
                self.transport.backend.active = True
                async with (
                    asyncio.timeout_at(deadline - 1),
                    streamable_http_client(
                        self.config.endpoint, http_client=self.http, terminate_on_close=False
                    ) as (read, write, _),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    await session.list_tools()
                    result = await session.call_tool(TOOL, value)
                    return prepare_reply(result.structuredContent, value)
            except BaseException:
                self.transport.abort()
                cleanup = asyncio.create_task(self.transport.aclose())
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                try:
                    cleanup.result()
                except BaseException:
                    self.transport.abort()
                raise
            finally:
                self.transport.backend.active = False
                self.lock.release()
