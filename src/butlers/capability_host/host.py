"""The request host: one transport-agnostic callable per HTTP request (RFC 0039 D1, D6, D7).

``CapabilityHost.handle`` takes the HTTP method, headers, raw query string and a body reader,
and returns the HTTP status and bytes. Order is fixed: the hop bearer before the body is read;
then a single strict JSON-RPC object of at most 1 MiB; then the assertion (required for every
method but the exempt handshake ones, and verified whenever present). Exempt methods answer
from static handlers and never touch the pool. ``tools/call`` runs the D17 precedence:
latch, arguments, budgets, projection state, the reader, canaries, DLP, size.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import asyncpg

from butlers.capability_host.assertion import (
    Assertion,
    AssertionPolicy,
    AuthRefused,
    ReplaySet,
    client_label,
    verify_assertion,
)
from butlers.capability_host.budgets import Budgets
from butlers.capability_host.config import FileLayers, HostSettings
from butlers.capability_host.dlp import MAX_COUNTED, find_canary, scan_result
from butlers.capability_host.observe import Metrics
from butlers.capability_host.projection import (
    ProjectionStatus,
    canary_probe,
    judge,
    read_status,
)
from butlers.knowledge_read import (
    chronology,
    describe,
    entities,
    finance,
    graph,
    health,
    search,
    travel,
)
from butlers.knowledge_read.core import (
    DOMAINS,
    MAX_RESPONSE_BYTES,
    ReadContext,
    ReadError,
    in_snapshot,
    read_context,
)
from butlers.knowledge_read.manifest import Manifest, load_manifest

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1024 * 1024
# The projection probe scans every view: the role's 2 s statement_timeout and the pool's 10 s
# client command_timeout are both raised to this for the probe's own queries.
PROBE_TIMEOUT_S = 30
PROBE_TIMEOUT = f"{PROBE_TIMEOUT_S}s"
EXEMPT = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "notifications/cancelled",
        "ping",
        "tools/list",
        "server/discover",
    }
)
UNAUTHORIZED = {"code": -32001, "message": "Unauthorized", "data": {"reason": "unauthorized"}}
INTERNAL = {"code": -32603, "message": "Internal error"}
_CONTENT_LENGTH = re.compile(r"[0-9]{1,8}\Z", re.ASCII)
_TOP_KEYS = frozenset({"jsonrpc", "id", "method", "params"})
_CALL_KEYS = frozenset({"name", "arguments", "_meta"})
_MAX_AUDIT_IDS = 200

Reader = Callable[[asyncpg.Connection, ReadContext, object], Awaitable[dict[str, Any]]]
StaticHandler = Callable[[Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    domain: str | None  # None: describe and the multi-domain tools
    read: Reader
    dlp: bool = True  # False only for describe, which returns no published data


def default_tools() -> dict[str, Tool]:
    """The 18 knowledge_read tools; ``propose_fact`` registers when its reader lands (5.1)."""
    pairs: list[tuple[Reader, str | None]] = [
        (search.search, None),
        (search.fetch, None),
        (search.search_filtered, None),
        (entities.entity_lookup, "relationship"),
        (entities.entity_profile, "relationship"),
        (graph.graph_neighbors, "relationship"),
        (graph.graph_path, "relationship"),
        (chronology.timeline, None),
        (chronology.time_use, "time"),
        (travel.trips, "travel"),
        (finance.finance_summary, "finance"),
        (finance.finance_ledger, "finance"),
        (finance.finance_obligations, "finance"),
        (finance.finance_records, "finance"),
        (health.health_series, "health"),
        (health.health_profile, "health"),
        (health.health_records, "health"),
    ]
    tools = {r.__name__: Tool(r.__name__, d, r) for r, d in pairs}
    tools["describe"] = Tool("describe", None, describe.describe, dlp=False)
    return tools


@dataclass(frozen=True)
class HostResponse:
    status: int
    body: bytes = b""
    headers: tuple[tuple[str, str], ...] = ()


class _Probe(Exception):
    """Raised by the argument-validation connection the moment a reader touches the DB."""


class _NoDatabase:
    def __getattr__(self, name: str) -> Any:
        raise _Probe


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _strict(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _refuse_constant(_token: str) -> Any:
    raise ValueError("non-standard JSON constant")


def parse_body(body: bytes) -> dict[str, Any]:
    """One strict JSON-RPC 2.0 object: no batch, no duplicate key, no NaN/Infinity."""
    try:
        message = json.loads(
            body.decode("utf-8"), object_pairs_hook=_strict, parse_constant=_refuse_constant
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise AuthRefused("shape") from None
    if (
        not isinstance(message, dict)
        or not set(message) <= _TOP_KEYS
        or message.get("jsonrpc") != "2.0"
        or not isinstance(message.get("method"), str)
        or not isinstance(message.get("params", {}), dict)
        or ("id" in message and (type(message["id"]) not in (str, int)))
    ):
        raise AuthRefused("shape")
    return message


@dataclass
class _Call:
    """Audit facts gathered while one request runs."""

    request_id: str
    started: float
    assertion: Assertion | None = None
    tool: str | None = None
    args_sha256: str | None = None
    outcome: str = "unauthorized"
    cause: str | None = None
    rows: int = 0
    redactions: int = 0
    ids: list[str] = field(default_factory=list)


class CapabilityHost:
    def __init__(
        self,
        settings: HostSettings,
        *,
        pool: asyncpg.Pool | None = None,
        tools: Mapping[str, Tool] | None = None,
        static: Mapping[str, StaticHandler] | None = None,
        manifest: Manifest | None = None,
        metrics: Metrics | None = None,
        clock: Callable[[], float] = time.time,
        files: FileLayers | None = None,
    ):
        self.settings = settings
        self.pool = pool
        self.tools = dict(default_tools() if tools is None else tools)
        self.static = {"ping": lambda _params: {}, **(static or {})}
        self.manifest = manifest or load_manifest()
        self.metrics = metrics or Metrics()
        self.clock = clock
        self.files = files or FileLayers(settings)
        self.replay = ReplaySet()
        self.budgets = Budgets(settings.budgets)
        self.cursor_key = os.urandom(32)
        self.process_start = clock()
        self.latched = False
        self._probed: tuple | None = None
        self._probe_lock = asyncio.Lock()

    # -- HTTP --------------------------------------------------------------------------------

    async def handle(
        self,
        method: str,
        headers: Sequence[tuple[str, str]],
        query: str,
        read_body: Callable[[int], Awaitable[bytes]],
        *,
        request_id: str | None = None,
    ) -> HostResponse:
        files = self.files.current()
        auth = [v for k, v in headers if k.lower() == "authorization"]
        if not self._bearer_ok(auth, files.bearer_digests):
            self.metrics.inc("bearer_refusals")
            return HostResponse(401)
        call = _Call(request_id or uuid.uuid4().hex, time.monotonic())
        try:
            response = await self._admitted(call, method, headers, query, read_body, files)
        except Exception as exc:
            # A host defect: still exactly one audit line (D15), and nothing about the cause.
            logger.error("public MCP request failed: %s", type(exc).__name__)
            call.outcome, call.cause = "internal", None
            response = self._rpc({"jsonrpc": "2.0", "id": None, "error": INTERNAL})
        self.metrics.inc("requests", tool=call.tool or "", outcome=call.outcome)
        self.metrics.audit(
            {
                "request_id": call.request_id,
                "jti": call.assertion.jti if call.assertion else None,
                "sub": call.assertion.sub if call.assertion else None,
                "cid": client_label(call.assertion.cid) if call.assertion else None,
                "tool": call.tool,
                "args_sha256": call.args_sha256,
                "outcome": call.outcome,
                "cause": call.cause,
                "rows": call.rows,
                "bytes": len(response.body),
                "redactions": call.redactions,
                "latency_ms": round((time.monotonic() - call.started) * 1000, 1),
                "ids": call.ids[:_MAX_AUDIT_IDS],
            }
        )
        return response

    @staticmethod
    def _bearer_ok(values: list[str], digests: tuple[bytes, ...]) -> bool:
        if len(values) != 1 or not values[0].startswith("Bearer ") or not digests:
            return False
        presented = hashlib.sha256(values[0][len("Bearer ") :].encode()).digest()
        matched = False
        for digest in digests:  # no early exit: compare against every configured digest
            matched |= hmac.compare_digest(presented, digest)
        return matched and len(values[0]) > len("Bearer ")

    async def _admitted(self, call, method, headers, query, read_body, files) -> HostResponse:
        if method != "POST":
            call.outcome = "method_not_allowed"
            return HostResponse(405, headers=(("allow", "POST"),))
        message: dict[str, Any] = {}
        try:
            lengths = [v for k, v in headers if k.lower() == "content-length"]
            if any(not _CONTENT_LENGTH.match(v) or int(v) > MAX_BODY_BYTES for v in lengths):
                raise AuthRefused("size")
            body = await read_body(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise AuthRefused("size")
            message = parse_body(body)
            rpc_method = message["method"]
            params = message.get("params", {})
            tool = params.get("name", "") if rpc_method == "tools/call" else ""
            if not isinstance(tool, str):
                raise AuthRefused("shape")
            assertions = [v for k, v in headers if k.lower() == "x-mcp-assertion"]
            if len(assertions) > 1:
                raise AuthRefused("shape")
            if assertions:
                call.assertion = verify_assertion(
                    assertions[0],
                    keyring=files.keyring,
                    policy=self._policy(),
                    replay=self.replay,
                    now=self.clock(),
                    body=body,
                    method=rpc_method,
                    tool=tool,
                    query=query,
                )
            elif rpc_method not in EXEMPT:
                raise AuthRefused("missing")
        except AuthRefused as refused:
            call.cause = refused.cause
            self.metrics.inc("auth_failures", cause=refused.cause)
            rid = message.get("id") if isinstance(message.get("id"), (str, int)) else None
            return self._rpc({"jsonrpc": "2.0", "id": rid, "error": UNAUTHORIZED})
        rid = message.get("id")
        if rpc_method == "tools/call" and call.assertion is not None and "id" in message:
            call.outcome = "ok"
            result = await self._tool_call(call, call.assertion, params, files)
            return self._rpc({"jsonrpc": "2.0", "id": rid, "result": result})
        if "id" not in message and rpc_method.startswith("notifications/"):
            call.outcome = "static"
            return HostResponse(202)
        handler = self.static.get(rpc_method)
        if handler is None or "id" not in message:
            call.outcome = "method_not_found"
            error = {"code": -32601, "message": "Method not found"}
            return self._rpc({"jsonrpc": "2.0", "id": rid, "error": error})
        call.outcome = "static"
        return self._rpc({"jsonrpc": "2.0", "id": rid, "result": handler(params)})

    def _policy(self) -> AssertionPolicy:
        s = self.settings
        return AssertionPolicy(
            s.issuer,
            s.audience,
            s.allowed_subs,
            s.client_domains,
            s.registered_clients,
            self.process_start,
        )

    @staticmethod
    def _rpc(message: dict[str, Any]) -> HostResponse:
        return HostResponse(200, _json(message), (("content-type", "application/json"),))

    # -- tools/call --------------------------------------------------------------------------

    async def _tool_call(
        self, call: _Call, who: Assertion, params: Mapping[str, Any], files: FileLayers
    ) -> dict[str, Any]:
        name, arguments = params.get("name"), params.get("arguments", {})
        call.tool = name if name in self.tools else None
        call.args_sha256 = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        try:
            structured = await self._run(call, who, name, arguments, params, files)
        except ReadError as refused:
            return self._error(call, refused)
        return {
            "content": [{"type": "text", "text": _json(structured).decode()}],
            "structuredContent": structured,
            "isError": False,
        }

    def _error(self, call: _Call, refused: ReadError) -> dict[str, Any]:
        call.outcome = refused.code
        error = refused.to_dict()
        return {
            "content": [{"type": "text", "text": _json(error).decode()}],
            "structuredContent": error,
            "isError": True,
        }

    async def _run(self, call, who, name, arguments, params, files) -> dict[str, Any]:
        tool = self.tools.get(name) if isinstance(name, str) else None
        if self.latched and name != "describe":
            raise ReadError("blocked")
        if tool is None or not isinstance(arguments, dict) or not set(params) <= _CALL_KEYS:
            raise ReadError("invalid_argument")
        await self._validate(tool, who, arguments)
        now = self.clock()
        wait = self.budgets.admit(who.sub, who.cid, tool.name, now)
        if wait is not None:
            raise _Budget(wait)
        if self.pool is None:
            raise ReadError("unavailable")
        canaries = files.canaries
        probed = tool.name == "describe"  # D17: describe answers without canaries or probe
        if not probed:
            if canaries is None:
                raise ReadError("unavailable")
            if tool.domain is not None and tool.domain not in DOMAINS:
                raise ReadError("domain_disabled")  # a mis-registered tool never reads
        disabled = files.break_glass

        async def read(conn: asyncpg.Connection) -> dict[str, Any]:
            status = await read_status(conn)
            verdict = judge(status, self.manifest)
            for change in ("narrowing", "widening"):
                self.metrics.set("owner_step_pending", verdict.change == change, change=change)
            if tool.name != "describe":
                if verdict.code is not None:
                    raise ReadError(verdict.code)
                if self._probed != self._marker(status, canaries):
                    raise ReadError("unavailable")  # an apply landed after the probe; retry
            context = await read_context(
                conn,
                sub=who.sub,
                cursor_key=self.cursor_key,
                disabled_domains=disabled | status.disabled,
                now=datetime.fromtimestamp(now, UTC),
                dashboard_base=self.settings.dashboard_base,
            )
            if tool.domain is not None:
                context.require(tool.domain)  # defence in depth: the reader checks it too
            return await tool.read(conn, context, arguments)

        try:
            async with self.pool.acquire() as conn:
                conn = cast(asyncpg.Connection, conn)
                if not probed:
                    await self._probe(conn, cast(tuple[str, ...], canaries))
                result = await in_snapshot(conn, lambda: read(conn))
        except ReadError:
            raise
        except Exception:
            # Connection, timeout and driver errors, and any reader defect: fail closed.
            raise ReadError("unavailable") from None
        return self._release(call, who, tool, result, canaries, now)

    @staticmethod
    def _marker(status: ProjectionStatus, canaries: tuple[str, ...]) -> tuple:
        canary_digest = hashlib.sha256(_json(sorted(canaries))).hexdigest()
        return (status.applied_digest, status.applied_at, canary_digest)

    async def _probe(self, conn: asyncpg.Connection, canaries: tuple[str, ...]) -> None:
        """The D15 projection probe, once per applied manifest and canary set.

        The marker is compared first without the lock, so a probed projection costs one status
        read and no serialization. On a mismatch it is single-flight: concurrent first calls
        wait for one probe instead of each scanning every view, and re-check the marker once
        they hold the lock. The probe runs in its own read-only transaction with a longer
        statement timeout, so a slow scan cannot keep the server unavailable; a failed probe is
        retried by the next call. A pending or skewed projection is left to the snapshot's own
        D17 refusal.
        """
        try:
            status = await read_status(conn)
        except asyncpg.QueryCanceledError:
            status = None  # the role's 2 s budget: re-read under the probe's own timeout below
        if status is not None and (
            self._probed == self._marker(status, canaries)
            or judge(status, self.manifest).code is not None
        ):
            return
        async with self._probe_lock:
            async with conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = '{PROBE_TIMEOUT}'")
                status = await read_status(conn)
                marker = self._marker(status, canaries)
                if self._probed == marker or judge(status, self.manifest).code is not None:
                    return
                if await canary_probe(conn, self.manifest, canaries, query_timeout=PROBE_TIMEOUT_S):
                    self._latch()
                    raise ReadError("blocked")
                self._probed = marker

    async def _validate(self, tool: Tool, who: Assertion, arguments: dict) -> None:
        """Run the reader's closed argument parsing with no database (D17 precedence)."""
        context = ReadContext(
            sub=who.sub,
            as_of=datetime.fromtimestamp(self.clock(), UTC),
            now=datetime.fromtimestamp(self.clock(), UTC),
            cursor_key=self.cursor_key,
        )
        try:
            await tool.read(cast(asyncpg.Connection, _NoDatabase()), context, arguments)
        except ReadError as refused:
            if refused.code == "invalid_argument":
                raise
        except Exception:
            pass  # _Probe: parsing passed; anything else surfaces in the real run

    def _release(self, call, who, tool, result, canaries, now) -> dict[str, Any]:
        if canaries and find_canary(result, canaries):
            self._latch()
            raise ReadError("blocked")
        if not tool.dlp:
            return result
        redacted, scan = scan_result(tool.name, tool.domain, result)
        call.redactions = sum(scan.rules.values())
        for rule, count in scan.rules.items():
            self.metrics.inc("redactions", count, tool=tool.name, rule=rule)
        if scan.luhn_uncontexted:
            self.metrics.inc("dlp_luhn_uncontexted", scan.luhn_uncontexted, tool=tool.name)
        if scan.counted > MAX_COUNTED:
            self.metrics.inc("dlp_blocked", tool=tool.name)
            raise ReadError("blocked")
        if len(_json(redacted)) > MAX_RESPONSE_BYTES:
            raise ReadError("too_large")
        call.rows, call.ids = _rows_and_ids(redacted)
        self.budgets.record_rows(who.sub, who.cid, tool.name, call.rows, now)
        if tool.domain:
            self.metrics.inc(
                "rows_served",
                call.rows,
                tool=tool.name,
                domain=tool.domain,
                client=client_label(who.cid) or "",
            )
        return redacted

    def _latch(self) -> None:
        self.latched = True
        self.metrics.inc("canary_hits")
        self.metrics.set("canary_latched", 1)


class _Budget(ReadError):
    def __init__(self, retry_after: int):
        super().__init__("budget_exhausted")
        self.retry_after = retry_after

    def to_dict(self) -> dict[str, Any]:
        error = super().to_dict()
        error["error"]["retry_after_s"] = self.retry_after
        return error


def _rows_and_ids(result: dict[str, Any]) -> tuple[int, list[str]]:
    """Rows: items in top-level lists of objects (one for ``fetch``). Ids: opaque item ids."""
    rows = sum(
        len(v)
        for k, v in result.items()
        if k != "sources_degraded" and isinstance(v, list) and v and isinstance(v[0], dict)
    )
    if not rows and "id" in result:
        rows = 1
    ids: list[str] = []
    stack: list[Any] = [result]
    while stack and len(ids) < _MAX_AUDIT_IDS:
        node = stack.pop()
        if isinstance(node, dict):
            source = node.get("source")
            if isinstance(source, dict) and isinstance(source.get("uri"), str):
                ids.append(source["uri"])
            elif isinstance(node.get("id"), str):
                ids.append(node["id"])
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(node)
    return rows, ids
