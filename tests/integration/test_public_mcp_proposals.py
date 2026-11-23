"""``propose_fact`` over the real topology (REQ-public-mcp-proposals-001, -002, -003, -007).

The production reader pool (``mcp_public_ro`` over TCP) and the real host, against the real
``mcp_inbox.proposals`` table, its column-limited grants and its 1,000-row cap trigger. The
database is the authority on what the role can do; the inbox is read and prepared here through
the owner/bootstrap connection only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import asyncpg
import pytest
import rfc8785
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import make_url, text

from butlers.capability_host import HostSettings, create_pool
from butlers.knowledge_read.manifest import load_manifest
from butlers.mcp_public.proposals import payload_of, proposal_id
from butlers.mcp_public.server import build_host
from butlers.migrations.public_mcp import reconcile_connection
from butlers.migrations.public_mcp_owner import render_owner_program
from butlers.testing.public_mcp import public_mcp_topology
from butlers.testing.runtime_probe_control import current_entry, keyring_document

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

BEARER = "hop-bearer-proposals-test"
AUD = "https://mcp.example.com/butlers"
SUB = "7"
CID = "https://claude.ai/meta"
KEY = ed25519.Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
CANARY = "CANARY-proposals-5e21c9d0"
SUBJECT = "00000000-0000-0000-0000-0000000000a1"
OBJECT = "00000000-0000-0000-0000-0000000000b2"
BASE = {
    "subject_entity_id": SUBJECT,
    "predicate": "works-at",
    "object_entity_id": OBJECT,
    "idempotency_key": "key-1",
}


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


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _assertion(body: bytes, *, sub: str = SUB, cid: str = CID) -> str:
    message = json.loads(body)
    now = int(time.time())
    payload = {
        "v": 1,
        "iss": "mcp-gateway",
        "aud": AUD,
        "sub": sub,
        "cid": cid,
        "mth": message["method"],
        "tool": message["params"]["name"],
        "path": "",
        "bsha": _b64u(hashlib.sha256(body).digest()),
        "jti": _b64u(os.urandom(16)),
        "iat": now,
        "exp": now + 30,
    }
    head = rfc8785.dumps({"alg": "EdDSA", "kid": "k1", "typ": "butlers-mcp-assertion+v1"})
    signing = f"{_b64u(head)}.{_b64u(rfc8785.dumps(payload))}"
    return f"{signing}.{_b64u(KEY.sign(signing.encode()))}"


class Client:
    def __init__(self, host):
        self.host = host

    async def call(self, name: str, arguments: dict, **who) -> dict:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        ).encode()

        async def read_body(_limit: int) -> bytes:
            return body

        response = await self.host.handle(
            "POST",
            [("authorization", f"Bearer {BEARER}"), ("x-mcp-assertion", _assertion(body, **who))],
            "",
            read_body,
        )
        assert response.status == 200
        return json.loads(response.body)["result"]


@pytest.fixture
async def client(projected, tmp_path: Path):
    topology, projection = projected
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
            "MCP_PUBLIC_ALLOWED_SUBS": f"{SUB},8",
            "MCP_PUBLIC_DB_HOST": str(url.host),
            "MCP_PUBLIC_DB_PORT": str(url.port),
        },
        root=tmp_path,
    )
    pool = await create_pool(settings)
    _clear(projection)
    try:
        yield Client(build_host(settings, pool))
    finally:
        await pool.close()
        _clear(projection)


def _clear(projection) -> None:
    with projection.begin() as conn:
        conn.execute(text("DELETE FROM mcp_inbox.proposals"))


def _rows(projection) -> list[dict]:
    with projection.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text("SELECT * FROM mcp_inbox.proposals"))]


def _code(result: dict) -> str | None:
    return result["structuredContent"]["error"]["code"] if result["isError"] else None


async def test_a_valid_proposal_writes_one_inbox_row(client, projected):
    _, projection = projected
    note = "Met at a conference."
    result = await client.call("propose_fact", {**BASE, "note": note})
    assert result["isError"] is False
    out = result["structuredContent"]
    assert out["status"] == "received" and set(out) == {"proposal_id", "status"}
    [row] = _rows(projection)
    assert str(row["proposal_id"]) == out["proposal_id"]
    assert (row["sub"], row["cid"], row["idempotency_key"]) == (SUB, CID, "key-1")
    assert (str(row["subject_entity_id"]), row["predicate"], str(row["object_entity_id"])) == (
        SUBJECT,
        "works-at",
        OBJECT,
    )
    assert (row["evidence_url"], row["note"]) == (None, note)
    payload = payload_of({**BASE, "evidence_url": None, "note": note})
    assert out["proposal_id"] == proposal_id(SUB, CID, "key-1", payload)


async def test_an_identical_retry_gives_the_same_id_and_one_row(client, projected):
    _, projection = projected
    args = {**BASE, "evidence_url": "https://example.org/proof"}
    first = await client.call("propose_fact", args)
    again = await client.call("propose_fact", args)
    assert first["isError"] is False and again["isError"] is False
    assert first["structuredContent"] == again["structuredContent"]
    assert len(_rows(projection)) == 1


async def test_a_changed_payload_under_the_same_key_gets_a_new_id(client, projected):
    _, projection = projected
    first = await client.call("propose_fact", BASE)
    other = await client.call("propose_fact", {**BASE, "object_entity_id": SUBJECT})
    assert other["isError"] is False
    assert other["structuredContent"]["proposal_id"] != first["structuredContent"]["proposal_id"]
    assert len(_rows(projection)) == 2


async def test_the_same_key_from_another_caller_is_a_different_proposal(client, projected):
    _, projection = projected
    mine = await client.call("propose_fact", BASE)
    theirs = await client.call("propose_fact", BASE, sub="8")
    elsewhere = await client.call("propose_fact", BASE, cid="https://chatgpt.com/c.json")
    ids = {r["structuredContent"]["proposal_id"] for r in (mine, theirs, elsewhere)}
    assert len(ids) == 3 and len(_rows(projection)) == 3


@pytest.mark.parametrize(
    "bad",
    [
        {"predicate": "parent-of"},
        {"predicate": "has-email", "object_entity_id": "alice@example.org"},
        {"note": "n" * 501},
        {"evidence_url": "javascript:alert(1)"},
        {"unknown": 1},
    ],
)
async def test_a_refused_proposal_writes_nothing(client, projected, bad):
    _, projection = projected
    result = await client.call("propose_fact", {**BASE, **bad})
    assert _code(result) == "invalid_argument"
    assert _rows(projection) == []


async def test_a_full_inbox_is_unavailable_retryable_and_adds_no_row(client, projected):
    _, projection = projected
    existing = await client.call("propose_fact", BASE)
    with projection.begin() as conn:  # through the cap trigger, as any insert
        conn.execute(
            text(
                "INSERT INTO mcp_inbox.proposals(proposal_id, sub, cid, idempotency_key, "
                "subject_entity_id, predicate, object_entity_id) "
                "SELECT gen_random_uuid(), 'fill', 'fill', 'k' || i, gen_random_uuid(), "
                "'knows', gen_random_uuid() FROM generate_series(1, 999) AS i"
            )
        )
    assert _scalar(projection, "SELECT count(*) FROM mcp_inbox.proposals") == 1000
    refused = await client.call("propose_fact", {**BASE, "idempotency_key": "key-2"})
    assert _code(refused) == "unavailable"
    assert refused["structuredContent"]["error"]["retryable"] is True
    assert _scalar(projection, "SELECT count(*) FROM mcp_inbox.proposals") == 1000
    # A retry of a proposal already in the inbox is a no-op the cap does not refuse.
    again = await client.call("propose_fact", BASE)
    assert again["structuredContent"] == existing["structuredContent"]
    assert _scalar(projection, "SELECT count(*) FROM mcp_inbox.proposals") == 1000
    # Draining frees the slot: the same call now succeeds.
    with projection.begin() as conn:
        conn.execute(text("DELETE FROM mcp_inbox.proposals WHERE sub='fill'"))
    assert (await client.call("propose_fact", {**BASE, "idempotency_key": "key-2"}))[
        "isError"
    ] is False


@pytest.mark.parametrize("switch", ["proposals", "all"])
async def test_a_disabled_proposals_switch_refuses_before_any_write(client, projected, switch):
    topology, projection = projected
    try:
        with topology.engine.begin() as conn:
            conn.execute(
                text("UPDATE mcp_surface.controls SET state='disabled' WHERE domain=:d"),
                {"d": switch},
            )
        _wait(
            lambda: _scalar(
                projection,
                "SELECT count(*) FROM mcp_read.mcp_surface__controls "
                "WHERE fields->>'domain'=:d AND fields->>'state'='disabled'",
                {"d": switch},
            )
        )
        result = await client.call("propose_fact", BASE)
        assert _code(result) == "domain_disabled"
        assert result["structuredContent"]["error"]["retryable"] is False
        assert _rows(projection) == []
        reads = await client.call("entity_lookup", {"name": "x"})
        assert (_code(reads) == "domain_disabled") is (switch == "all")
    finally:
        with topology.engine.begin() as conn:
            conn.execute(text("UPDATE mcp_surface.controls SET state='enabled'"))
        _wait(
            lambda: (
                _scalar(
                    projection,
                    "SELECT count(*) FROM mcp_read.mcp_surface__controls "
                    "WHERE fields->>'state'='disabled'",
                )
                == 0
            )
        )


async def test_the_public_role_still_cannot_read_or_alter_the_inbox(client, projected):
    topology, _ = projected
    await client.call("propose_fact", BASE)
    conn = await topology.reader_connection("mcp_public_ro")
    try:
        for sql in (
            "SELECT count(*) FROM mcp_inbox.proposals",
            "SELECT proposal_id FROM mcp_inbox.proposals",
            "UPDATE mcp_inbox.proposals SET note='x'",
            "DELETE FROM mcp_inbox.proposals",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql)
        # The write path the server uses is the one column-limited INSERT, and it is not
        # able to set received_at.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO mcp_inbox.proposals(proposal_id, sub, cid, idempotency_key, "
                "subject_entity_id, predicate, object_entity_id, received_at) "
                "VALUES ($1,'s','c','k',$2,'knows',$2, now())",
                uuid.uuid4(),
                uuid.uuid4(),
            )
    finally:
        await conn.close()
