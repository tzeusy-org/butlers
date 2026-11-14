"""Shared helpers for the public MCP host: a synthetic gateway key, files and a fake projection.

The Ed25519 seed below is a published synthetic test key (also the golden-vector key); it signs
nothing outside tests.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.hazmat.primitives.asymmetric import ed25519

from butlers.capability_host import CapabilityHost, FileLayers, HostSettings, Metrics, Tool
from butlers.capability_host.config import BudgetLimits
from butlers.knowledge_read.core import Arguments
from butlers.knowledge_read.manifest import load_manifest
from butlers.testing.runtime_probe_control import current_entry, keyring_document

SEED = bytes(range(32))
KID = "mcp-gateway-test-v1"
AUD = "https://mcp.tze.how/butlers"
SUB = "4242"
CID = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CID2 = "https://chatgpt.com/connector/oauth/client.json"
BEARER = "hop-bearer-synthetic-value"
NEXT_BEARER = "hop-bearer-synthetic-next"
NOW = 1_794_640_000  # 2026-11-14T08:26:40Z
CANARY = "CANARY-bs-7f3a9c2e1d"
MANIFEST = load_manifest()


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def public_key(seed: bytes = SEED) -> bytes:
    return ed25519.Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()


def keyring_json(*entries: dict[str, Any], retiring: list | None = None) -> bytes:
    current = entries[0] if entries else current_entry(public_key(), kid=KID, sign_from=_from())
    return json.dumps(keyring_document(current, retiring)).encode()


def _from() -> datetime:
    return datetime.fromtimestamp(NOW, UTC) - timedelta(days=5)


def claims(body: bytes, **overrides: Any) -> dict[str, Any]:
    message = json.loads(body) if body else {}
    params = message.get("params") if isinstance(message, dict) else None
    tool = params.get("name", "") if isinstance(params, dict) else ""
    base = {
        "v": 1,
        "iss": "mcp-gateway",
        "aud": AUD,
        "sub": SUB,
        "cid": CID,
        "mth": message.get("method", "") if isinstance(message, dict) else "",
        "tool": tool if message.get("method") == "tools/call" else "",
        "path": "",
        "bsha": b64u(hashlib.sha256(body).digest()),
        "jti": b64u(os.urandom(16)),
        "iat": NOW,
        "exp": NOW + 30,
    }
    base.update(overrides)
    return base


def sign(
    payload: dict[str, Any] | bytes,
    *,
    seed: bytes = SEED,
    kid: str = KID,
    header: dict[str, Any] | bytes | None = None,
) -> str:
    head = (
        header
        if isinstance(header, bytes)
        else rfc8785.dumps(
            header or {"alg": "EdDSA", "kid": kid, "typ": "butlers-mcp-assertion+v1"}
        )
    )
    body = payload if isinstance(payload, bytes) else rfc8785.dumps(payload)
    signing_input = f"{b64u(head)}.{b64u(body)}"
    signature = ed25519.Ed25519PrivateKey.from_private_bytes(seed).sign(signing_input.encode())
    return f"{signing_input}.{b64u(signature)}"


def rpc(method: str, params: dict | None = None, *, rid: Any = 1) -> bytes:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        message["id"] = rid
    if params is not None:
        message["params"] = params
    return json.dumps(message).encode()


def call_body(tool: str, arguments: dict | None = None, *, rid: Any = 1) -> bytes:
    return rpc("tools/call", {"name": tool, "arguments": arguments or {}}, rid=rid)


class Clock:
    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now


@dataclass
class Projection:
    """What the fake projection answers to the host's status and probe queries."""

    shipped: str = MANIFEST.digest
    replicated: list[str] | None = None
    applied: dict[str, Any] | None = None
    as_of_age: timedelta | None = timedelta(seconds=30)
    disabled: list[str] = field(default_factory=list)
    projection_canary: bool = False
    fail: BaseException | None = None
    fail_once: BaseException | None = None  # raised by the next read only
    probe_gate: asyncio.Event | None = None
    after_probe: Callable[[Projection], None] | None = None  # e.g. an apply landing mid-call
    probe_timeouts: list[float | None] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    acquired: int = 0

    def __post_init__(self) -> None:
        if self.replicated is None:
            self.replicated = [self.shipped]
        if self.applied is None:
            self.applied = {
                "digest": self.shipped,
                "canonical": MANIFEST.canonical,
                "applied_at": datetime.fromtimestamp(NOW - 3600, UTC),
                "copy_complete": True,
            }


class FakeConn:
    def __init__(self, projection: Projection, clock: Clock):
        self.p, self.clock, self.depth = projection, clock, 0

    def is_in_transaction(self) -> bool:
        return self.depth > 0

    @asynccontextmanager
    async def transaction(self, **kw):
        self.depth += 1
        self.p.queries.append(f"BEGIN {sorted(kw.items())}")
        try:
            yield
        finally:
            self.depth -= 1
            self.p.queries.append("END")

    async def execute(self, sql: str, *args):
        self.p.queries.append(sql)  # session settings only; injected failures hit the reads
        return "SET"

    def _note(self, sql: str) -> None:
        self.p.queries.append(sql)
        if self.p.fail_once is not None:
            failure, self.p.fail_once = self.p.fail_once, None
            raise failure
        if self.p.fail is not None:
            raise self.p.fail

    async def fetch(self, sql: str, *args):
        self._note(sql)
        if "mcp_surface__manifest_state" in sql:
            return [{"digest": d} for d in self.p.replicated or []]
        if "mcp_surface__controls" in sql:
            return [{"domain": d} for d in self.p.disabled]
        return [{"row": 1}]

    async def fetchrow(self, sql: str, *args):
        self._note(sql)
        return self.p.applied if "manifest_applied" in sql else None

    async def fetchval(self, sql: str, *args, **kw: Any):
        self._note(sql)
        if "replication_as_of" in sql:
            age = self.p.as_of_age
            return None if age is None else datetime.fromtimestamp(self.clock(), UTC) - age
        if "strpos" in sql:
            self.p.probe_timeouts.append(kw.get("timeout"))  # asyncpg's client-side bound
            if self.p.probe_gate is not None:
                await self.p.probe_gate.wait()
            if self.p.after_probe is not None:
                self.p.after_probe(self.p)
            return self.p.projection_canary
        return 1


class FakePool:
    def __init__(self, projection: Projection, clock: Clock):
        self.p, self.clock = projection, clock
        self.refuse: BaseException | None = None

    @asynccontextmanager
    async def acquire(self):
        self.p.acquired += 1
        if self.refuse is not None:
            raise self.refuse
        yield FakeConn(self.p, self.clock)


def fake_reader(domain: str | None, result: Any, *, calls: list | None = None):
    """A reader with closed ``q``/``cursor`` arguments that touches the DB once."""

    async def read(conn, context, arguments):
        args = Arguments(arguments, {"q", "cursor", "limit"})
        args.text("q", max_length=64)
        if domain:
            context.require(domain)
        await conn.fetchval("SELECT data")
        if calls is not None:
            calls.append(context)
        return result(context, args) if callable(result) else json.loads(json.dumps(result))

    return read


def item(domain: str, n: int, text: str = "plain") -> dict[str, Any]:
    return {
        "id": f"row-{n}",
        "text": text,
        "source": {"kind": "fact", "uri": f"butlers://{domain}/fact/row-{n}", "as_of": None},
    }


def default_fake_tools(calls: list | None = None) -> dict[str, Tool]:
    tools = {}
    for name, domain in (
        ("entity_lookup", "relationship"),
        ("entity_profile", "relationship"),
        ("finance_ledger", "finance"),
        ("finance_summary", "finance"),
        ("health_series", "health"),
        ("trips", "travel"),
        ("propose_fact", "proposals"),
    ):
        tools[name] = Tool(
            name, domain, fake_reader(domain, {"items": [item(domain, 1)]}, calls=calls)
        )

    async def describe(conn, context, arguments):
        Arguments(arguments, set())
        await conn.fetchval("SELECT describe")
        return {
            "manifest_digest": MANIFEST.digest,
            "domains": {d: "disabled" for d in sorted(context.disabled_domains)},
        }

    tools["describe"] = Tool("describe", None, describe, dlp=False)
    return tools


class Env:
    """One host over files in ``root`` and a fake projection."""

    def __init__(self, root: Path, *, tools=None, static=None, budgets=None, settings=None):
        self.root = root
        self.clock = Clock()
        self.mono = Clock(1000.0)
        self.projection = Projection()
        self.pool = FakePool(self.projection, self.clock)
        self.lines: list[str] = []
        self.calls: list = []
        for sub in ("bearer", "keyring", "switches", "canaries"):
            (root / sub).mkdir(exist_ok=True)
        self.write("bearer/digest", hashlib.sha256(BEARER.encode()).hexdigest() + "\n")
        self.write("keyring/keyring.json", keyring_json())
        self.write("canaries/canaries.json", json.dumps([CANARY]))
        self.write("switches/disabled_domains", "# nothing disabled\n")
        self.settings = settings or HostSettings(
            audience=AUD,
            allowed_subs=frozenset({SUB, "5151"}),
            root=root,
            dashboard_base="https://dash.example",
            budgets=budgets or BudgetLimits(),
        )
        self.static = static
        self.tools = tools if tools is not None else default_fake_tools(self.calls)
        self.host = self.restart()

    def restart(self) -> CapabilityHost:
        self.host = CapabilityHost(
            self.settings,
            pool=self.pool,  # type: ignore[arg-type]
            tools=self.tools,
            static=self.static,
            metrics=Metrics(sink=self.lines.append),
            clock=self.clock,
            files=FileLayers(self.settings, clock=self.mono),
        )
        return self.host

    def write(self, name: str, content: str | bytes) -> None:
        path = self.root / name
        path.write_bytes(content.encode() if isinstance(content, str) else content)

    def reload(self) -> None:
        self.mono.now += 10

    async def send(
        self,
        body: bytes,
        *,
        bearer: str | None = BEARER,
        assertion: str | None | bool = True,
        query: str = "",
        method: str = "POST",
        headers: list[tuple[str, str]] | None = None,
        **claim_overrides: Any,
    ):
        hdrs = list(headers or [])
        if bearer is not None:
            hdrs.append(("Authorization", f"Bearer {bearer}"))
        if assertion is True:
            at = int(self.clock.now)
            timing = {"iat": at, "exp": at + 30}
            assertion = sign(claims(body, path=query, **{**timing, **claim_overrides}))
        if isinstance(assertion, str):
            hdrs.append(("X-MCP-Assertion", assertion))
        self.body_reads = 0

        async def read_body(limit: int) -> bytes:
            self.body_reads += 1
            return body[:limit]

        return await self.host.handle(method, hdrs, query, read_body)

    async def tool(self, name: str, arguments: dict | None = None, **kw: Any) -> dict[str, Any]:
        response = await self.send(call_body(name, arguments), **kw)
        assert response.status == 200
        return json.loads(response.body)

    def audits(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.lines]


def error_code(message: dict[str, Any]) -> str | None:
    result = message.get("result", {})
    return result["structuredContent"]["error"]["code"] if result.get("isError") else None
