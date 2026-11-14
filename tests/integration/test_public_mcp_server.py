"""``butlers-public-mcp`` end to end over a replicated projection (REQ-public-mcp-surface-001, -002).

The ASGI app the entrypoint serves runs in process over the production reader pool
(``create_pool``, as ``mcp_public_ro`` over TCP). The MCP SDK's own streamable-HTTP client
drives it through a transport that plays the gateway Worker: it adds the hop bearer and
signs an assertion over each body. The client validates every success result against the
tool's ``outputSchema``, so a schema stricter than a real answer fails here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path

import httpx
import pytest
import rfc8785
from cryptography.hazmat.primitives.asymmetric import ed25519
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import make_url, text

from butlers.capability_host import MCP_PATH, HostSettings, create_pool
from butlers.knowledge_read.manifest import load_manifest
from butlers.mcp_public.app import McpApp
from butlers.mcp_public.catalogue import catalogue
from butlers.mcp_public.server import build_host
from butlers.migrations.public_mcp import reconcile_connection
from butlers.migrations.public_mcp_owner import render_owner_program
from butlers.testing.public_mcp import public_mcp_topology
from butlers.testing.runtime_probe_control import current_entry, keyring_document

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

BEARER = "hop-bearer-e2e-test"
AUD = "https://mcp.tze.how/butlers"
SUB = "7"
PERSON = "Ada Projection-Lovelace"
CANARY = "CANARY-e2e-0b7d4c11"
KEY = ed25519.Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def _wait(predicate, *, seconds=40):
    deadline = time.monotonic() + seconds
    while not (value := predicate()):
        assert time.monotonic() < deadline, "the subscriber did not reach the observation"
        time.sleep(0.05)
    return value


def _scalar(engine, sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar_one()


@pytest.fixture(scope="module")
def projected():
    with public_mcp_topology() as topology:
        projection = topology.projection_engine()
        manifest = load_manifest()
        try:
            with topology.engine.begin() as conn:
                reconcile_connection(conn, manifest)
                person = conn.execute(
                    text(
                        "INSERT INTO public.entities(canonical_name,entity_type) "
                        "VALUES (:n,'person') RETURNING id"
                    ),
                    {"n": PERSON},
                ).scalar_one()
            result = topology.execute(render_owner_program(manifest))
            assert result.exit_code == 0, repr(result.output)[-4000:]
            _wait(
                lambda: _scalar(
                    projection,
                    "SELECT COALESCE(bool_and(copy_complete AND digest=:d), false) "
                    "FROM mcp_read.manifest_applied",
                    {"d": manifest.digest},
                )
            )
            _wait(
                lambda: _scalar(
                    projection,
                    "SELECT count(*) FROM mcp_read.public__entities WHERE source_id=:i",
                    {"i": str(person)},
                )
            )
            yield topology, str(person)
        finally:
            projection.dispose()


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _assertion(body: bytes, query: str) -> str:
    message = json.loads(body)
    method = message.get("method", "")
    now = int(time.time())
    payload = {
        "v": 1,
        "iss": "mcp-gateway",
        "aud": AUD,
        "sub": SUB,
        "cid": "https://claude.ai/meta",
        "mth": method,
        "tool": message["params"]["name"] if method == "tools/call" else "",
        "path": query,
        "bsha": _b64u(hashlib.sha256(body).digest()),
        "jti": _b64u(os.urandom(16)),
        "iat": now,
        "exp": now + 30,
    }
    head = rfc8785.dumps({"alg": "EdDSA", "kid": "k1", "typ": "butlers-mcp-assertion+v1"})
    signing = f"{_b64u(head)}.{_b64u(rfc8785.dumps(payload))}"
    return f"{signing}.{_b64u(KEY.sign(signing.encode()))}"


class Gateway(httpx.AsyncBaseTransport):
    """The Worker's hop: the bearer on every request, an assertion over every POST body."""

    def __init__(self, inner: httpx.AsyncBaseTransport):
        self.inner = inner
        self.posts: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.headers["authorization"] = f"Bearer {BEARER}"
        if request.method == "POST":
            body = await request.aread()
            self.posts.append(json.loads(body).get("method", ""))
            request.headers["x-mcp-assertion"] = _assertion(body, request.url.query.decode())
        return await self.inner.handle_async_request(request)


@pytest.fixture
async def served(projected, tmp_path: Path):
    """The entrypoint's host and pool, configured as the pod is: env-shaped settings, files."""
    topology, person = projected
    url = make_url(topology.container.get_connection_url())
    for sub in ("bearer", "keyring", "canaries", "switches", "db"):
        (tmp_path / sub).mkdir()
    (tmp_path / "bearer/digest").write_text(hashlib.sha256(BEARER.encode()).hexdigest())
    keyring = keyring_document(current_entry(KEY.public_key().public_bytes_raw(), kid="k1"))
    (tmp_path / "keyring/keyring.json").write_text(json.dumps(keyring))
    (tmp_path / "canaries/canaries.json").write_text(json.dumps([CANARY]))
    (tmp_path / "switches/disabled_domains").write_text("# nothing disabled\n")
    fd = os.open(tmp_path / "db/password", os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(topology.role_passwords["mcp_public_ro"])
    settings = HostSettings.from_env(
        {
            "MCP_PUBLIC_AUDIENCE": AUD,
            "MCP_PUBLIC_ALLOWED_SUBS": SUB,
            "MCP_PUBLIC_DB_HOST": str(url.host),
            "MCP_PUBLIC_DB_PORT": str(url.port),
        },
        root=tmp_path,
    )
    pool = await create_pool(settings)
    try:
        yield build_host(settings, pool), person
    finally:
        await pool.close()


async def test_the_reader_pool_pins_search_path(served):
    """bu-y66o00.28: no database- or role-level setting can steer an unqualified name."""
    host, _ = served
    async with host.pool.acquire() as conn:
        assert await conn.fetchval("SHOW search_path") == "pg_catalog, pg_temp"
        assert await conn.fetchval("SELECT current_user") == "mcp_public_ro"


async def test_no_bearer_is_refused_before_the_body_is_read(served):
    host, _ = served
    receives = 0

    async def receive():
        nonlocal receives
        receives += 1
        return {"type": "http.request", "body": b'{"jsonrpc":"2.0"}', "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": MCP_PATH,
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
    }
    await McpApp(host)(scope, receive, send)
    assert sent[0]["status"] == 401 and receives == 0
    assert host.metrics.get("bearer_refusals") == 1


async def _session(host, gateway_box: list):
    gateway = Gateway(httpx.ASGITransport(app=McpApp(host)))
    gateway_box.append(gateway)
    return httpx.AsyncClient(transport=gateway, base_url="http://public-mcp.test")


async def test_an_mcp_client_handshakes_lists_and_calls(served):
    host, person = served
    box: list = []
    client = await _session(host, box)
    calls: dict[str, dict] = {}
    async with (
        client,
        streamable_http_client(f"http://public-mcp.test{MCP_PATH}", http_client=client) as (
            read,
            write,
            session_id,
        ),
    ):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.capabilities.model_dump(exclude_none=True) == {
                "tools": {"listChanged": False}
            }
            assert init.serverInfo.name == "butlers-public-mcp"
            listed = await session.list_tools()
            assert listed.tools == list(catalogue())

            found = await session.call_tool("entity_lookup", {"name": PERSON})
            assert found.isError is False
            assert found.structuredContent is not None
            assert [m["entity_id"] for m in found.structuredContent["matches"]] == [person]
            assert json.loads(found.content[0].text) == found.structuredContent  # type: ignore[union-attr]

            window = {"start": "2026-01-01", "end": "2026-01-31"}
            for name, arguments in {
                "search": {"query": "Ada"},
                "search_filtered": {"query": "Ada"},
                "entity_profile": {"entity_id": person},
                "graph_neighbors": {"entity_id": person},
                "graph_path": {"from_id": person, "to_id": person},
                "timeline": window,
                "time_use": {**window, "bucket": "week", "group_by": "category"},
                "trips": {},
                "describe": {},
                "finance_summary": {**window, "group_by": "category"},
                "finance_ledger": window,
                "finance_obligations": {"horizon_days": 30},
                "finance_records": {"kind": "transactions"},
                "health_profile": {},
                "health_records": {"kind": "conditions"},
            }.items():
                result = await session.call_tool(name, arguments)  # validates outputSchema
                assert result.isError is False, (name, result.structuredContent)
                calls[name] = result.structuredContent or {}
            hits = calls["search"]["results"]
            assert hits, "the seeded entity is searchable"
            fetched = await session.call_tool("fetch", {"id": hits[0]["id"]})
            assert fetched.isError is False, fetched.structuredContent
            refused = await session.call_tool(
                "propose_fact",
                {
                    "subject_entity_id": person,
                    "predicate": "knows",
                    "object_entity_id": person,
                    "idempotency_key": "k",
                },
            )
            assert refused.isError is True
            assert refused.structuredContent is not None
            assert refused.structuredContent["error"]["code"] == "unavailable"
        assert session_id() is None  # stateless: no session to resume or delete
    assert calls["describe"]["manifest_digest"] == load_manifest().digest
    assert "tools/list" in box[0].posts and "notifications/initialized" in box[0].posts
