"""The capability host over a genuinely replicated projection (RFC 0039 D16, D17; OD14).

The host connects as ``mcp_public_ro`` over TCP, as the server does, and calls the production
readers. Replicated kill switches, the applied-digest record, a disabled subscription, a
cancelled statement and a canary that reached the projection are each produced by the real
primary, publication, subscription and owner step, then restored.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import make_url, text

from butlers.capability_host import CapabilityHost, HostSettings, Metrics
from butlers.knowledge_read.manifest import Manifest, load_manifest
from butlers.migrations.public_mcp import reconcile_connection
from butlers.migrations.public_mcp_owner import render_owner_program
from butlers.testing.public_mcp import public_mcp_topology

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

BEARER = "hop-bearer-pg-test"
CANARY = "CANARY-pg-5e8d1f00aa"


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
            yield topology, projection
        finally:
            projection.dispose()


def _barrier(projected):
    """Wait until a committed write to an already-published control row has replicated."""
    topology, projection = projected
    with topology.engine.begin() as conn:
        changed = conn.execute(
            text(
                "UPDATE mcp_surface.controls SET changed_at=GREATEST(clock_timestamp(),"
                "changed_at+interval '1 microsecond') WHERE domain='all' RETURNING changed_at"
            )
        ).scalar_one()
    _wait(
        lambda: _scalar(
            projection,
            "SELECT changed_at=:c FROM mcp_surface.controls WHERE domain='all'",
            {"c": changed},
        )
    )


async def _pool(topology, **settings):
    url = make_url(topology.container.get_connection_url())
    return await asyncpg.create_pool(
        host=url.host,
        port=url.port,
        user="mcp_public_ro",
        password=topology.role_passwords["mcp_public_ro"],
        database="butlers_public",
        min_size=0,
        max_size=2,
        server_settings={"search_path": "pg_catalog", **settings},
    )


class Harness:
    def __init__(self, root: Path, pool, *, manifest: Manifest | None = None):
        import base64

        from cryptography.hazmat.primitives.asymmetric import ed25519

        from butlers.testing.runtime_probe_control import current_entry, keyring_document

        self.seed = bytes(range(32))
        self.key = ed25519.Ed25519PrivateKey.from_private_bytes(self.seed)
        for sub in ("bearer", "keyring", "switches", "canaries"):
            (root / sub).mkdir(exist_ok=True)
        (root / "bearer/digest").write_text(hashlib.sha256(BEARER.encode()).hexdigest())
        (root / "keyring/keyring.json").write_text(
            json.dumps(
                keyring_document(current_entry(self.key.public_key().public_bytes_raw(), kid="k1"))
            )
        )
        (root / "canaries/canaries.json").write_text(json.dumps([CANARY]))
        (root / "switches/disabled_domains").write_text("")
        self.b64u = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        self.settings = HostSettings(
            audience="https://mcp.tze.how/butlers", allowed_subs=frozenset({"7"}), root=root
        )
        self.lines: list[str] = []
        self.host = CapabilityHost(
            self.settings, pool=pool, manifest=manifest, metrics=Metrics(self.lines.append)
        )

    def assertion(self, body: bytes, method: str, tool: str) -> str:
        import os

        import rfc8785

        now = int(time.time())
        payload = {
            "v": 1,
            "iss": "mcp-gateway",
            "aud": self.settings.audience,
            "sub": "7",
            "cid": "https://claude.ai/meta",
            "mth": method,
            "tool": tool,
            "path": "",
            "bsha": self.b64u(hashlib.sha256(body).digest()),
            "jti": self.b64u(os.urandom(16)),
            "iat": now,
            "exp": now + 30,
        }
        head = rfc8785.dumps({"alg": "EdDSA", "kid": "k1", "typ": "butlers-mcp-assertion+v1"})
        signing = f"{self.b64u(head)}.{self.b64u(rfc8785.dumps(payload))}"
        return f"{signing}.{self.b64u(self.key.sign(signing.encode()))}"

    async def call(self, tool: str, arguments: dict | None = None) -> dict:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            }
        ).encode()
        headers = [
            ("authorization", f"Bearer {BEARER}"),
            ("x-mcp-assertion", self.assertion(body, "tools/call", tool)),
        ]

        async def read_body(limit: int) -> bytes:
            return body[:limit]

        response = await self.host.handle("POST", headers, "", read_body)
        assert response.status == 200
        return json.loads(response.body)["result"]


def _code(result: dict) -> str | None:
    return result["structuredContent"]["error"]["code"] if result["isError"] else None


@pytest.fixture
async def pool(projected):
    topology, _ = projected
    pool = await _pool(topology)
    try:
        yield pool
    finally:
        await pool.close()


async def test_the_host_serves_production_readers_as_the_public_role(projected, pool, tmp_path):
    harness = Harness(tmp_path, pool)
    described = await harness.call("describe")
    assert _code(described) is None
    assert described["structuredContent"]["manifest_digest"] == load_manifest().digest
    lookup = await harness.call("entity_lookup", {"name": "Nobody Like This"})
    assert _code(lookup) is None and lookup["structuredContent"] == {"matches": []}
    assert harness.host.metrics.get("owner_step_pending", change="narrowing") == 0
    assert json.loads(harness.lines[-1])["outcome"] == "ok"


async def test_a_replicated_switch_refuses_its_domain(projected, pool, tmp_path):
    topology, _ = projected
    harness = Harness(tmp_path, pool)
    try:
        with topology.engine.begin() as conn:
            conn.execute(
                text("UPDATE mcp_surface.controls SET state='disabled' WHERE domain='finance'")
            )
        _barrier(projected)
        refused = await harness.call("finance_ledger", {"start": "2026-01-01", "end": "2026-02-01"})
        assert refused["structuredContent"]["error"]["domain"] == "finance"
        assert _code(refused) == "domain_disabled"
        assert _code(await harness.call("entity_lookup", {"name": "x"})) is None
        with topology.engine.begin() as conn:
            conn.execute(
                text("UPDATE mcp_surface.controls SET state='disabled' WHERE domain='all'")
            )
        _barrier(projected)
        assert _code(await harness.call("entity_lookup", {"name": "x"})) == "domain_disabled"
        assert _code(await harness.call("describe")) is None
    finally:
        with topology.engine.begin() as conn:
            conn.execute(text("UPDATE mcp_surface.controls SET state='enabled'"))
        _barrier(projected)


async def test_break_glass_drops_a_domain_from_timeline(projected, pool, tmp_path):
    harness = Harness(tmp_path, pool)
    (tmp_path / "switches/disabled_domains").write_text("travel\n")
    result = await harness.call("timeline", {"start": "2026-01-01", "end": "2026-02-01"})
    assert _code(result) is None
    assert {"domain": "travel", "reason": "disabled"} in result["structuredContent"][
        "sources_degraded"
    ]
    assert _code(await harness.call("trips", {})) == "domain_disabled"


async def test_a_changed_applied_digest_is_pending_then_serves_again(projected, pool, tmp_path):
    _, projection = projected
    harness = Harness(tmp_path, pool)
    original = _scalar(projection, "SELECT digest FROM mcp_local.manifest_applied")
    try:
        with projection.begin() as conn:
            conn.execute(text("UPDATE mcp_local.manifest_applied SET digest=:d"), {"d": "f" * 64})
        result = await harness.call("entity_lookup", {"name": "x"})
        assert _code(result) == "projection_apply_pending"
        assert result["structuredContent"]["error"]["retryable"] is True
        assert _code(await harness.call("describe")) is None
    finally:
        with projection.begin() as conn:
            conn.execute(text("UPDATE mcp_local.manifest_applied SET digest=:d"), {"d": original})
    assert _code(await harness.call("entity_lookup", {"name": "x"})) is None


async def test_a_disabled_subscription_is_never_a_complete_copy(projected, pool, tmp_path):
    _, projection = projected
    harness = Harness(tmp_path, pool)
    try:
        with projection.begin() as conn:
            conn.exec_driver_sql("ALTER SUBSCRIPTION butlers_public_sub DISABLE")
        assert _code(await harness.call("trips", {})) == "projection_apply_pending"
        assert harness.host.metrics.get("owner_step_pending", change="widening") == 1
    finally:
        with projection.begin() as conn:
            conn.exec_driver_sql("ALTER SUBSCRIPTION butlers_public_sub ENABLE")
        _barrier(projected)


async def test_deploy_skew_refuses_every_tool_but_describe(projected, pool, tmp_path):
    shipped = load_manifest()
    other = Manifest(tuple(t for t in shipped.tables if t.source != "travel.trips"))
    harness = Harness(tmp_path, pool, manifest=other)
    assert _code(await harness.call("entity_lookup", {"name": "x"})) == (
        "projection_version_mismatch"
    )
    assert _code(await harness.call("describe")) is None


async def test_a_cancelled_statement_is_unavailable(projected, tmp_path):
    topology, _ = projected
    slow = await _pool(topology, statement_timeout="1")
    try:
        harness = Harness(tmp_path, slow)
        result = await harness.call("entity_lookup", {"name": "x"})
        assert _code(result) == "unavailable"
        assert result["structuredContent"]["error"]["retryable"] is True
        # The probe scanned every view under its own 30 s timeout and was recorded, even
        # though the role's statement timeout cancels the call's own snapshot.
        assert harness.host._probed is not None and not harness.host.latched
    finally:
        await slow.close()


async def test_an_unreachable_projection_is_unavailable(tmp_path):
    dead = await asyncpg.create_pool(
        host="127.0.0.1", port=1, user="mcp_public_ro", database="butlers_public", min_size=0
    )
    try:
        harness = Harness(tmp_path, dead)
        assert _code(await harness.call("entity_lookup", {"name": "x"})) == "unavailable"
    finally:
        await dead.close()


async def test_a_canary_in_the_projection_latches_on_the_probe(projected, pool, tmp_path):
    topology, projection = projected
    with topology.engine.begin() as conn:
        person = conn.execute(
            text(
                "INSERT INTO public.entities(canonical_name,entity_type) "
                "VALUES (:n,'person') RETURNING id"
            ),
            {"n": "Canary Holder " + CANARY},
        ).scalar_one()
    try:
        _wait(
            lambda: _scalar(
                projection,
                "SELECT count(*) FROM mcp_read.public__entities WHERE source_id=:i",
                {"i": str(person)},
            )
        )
        harness = Harness(tmp_path, pool)
        assert _code(await harness.call("entity_lookup", {"name": "x"})) == "blocked"
        assert harness.host.latched and harness.host.metrics.get("canary_hits") == 1
        assert _code(await harness.call("describe")) is None
    finally:
        with topology.engine.begin() as conn:
            conn.execute(text("DELETE FROM public.entities WHERE id=:i"), {"i": person})
        _barrier(projected)
    (tmp_path / "restarted").mkdir()
    restarted = Harness(tmp_path / "restarted", pool)
    assert _code(await restarted.call("entity_lookup", {"name": "x"})) is None
