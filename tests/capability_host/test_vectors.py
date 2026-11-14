"""Golden assertion vectors shared with the Worker (RFC 0039 D7; tasks.md 2.2).

``fixtures/assertion-v1.json`` is built by :func:`build` from a published synthetic test seed
with Python ``cryptography`` (Ed25519 is deterministic) and ``rfc8785``. The Worker's unit test
signs the same fixed bodies with the same seed and must produce the same ``assertion`` strings;
this side must reach each vector's ``expect``. Regenerate with
``uv run python -m tests.capability_host.test_vectors`` and review the diff: any change is a
wire-format change.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from cryptography.hazmat.primitives.asymmetric import ed25519

from butlers.capability_host.assertion import (
    AssertionPolicy,
    AuthRefused,
    ReplaySet,
    verify_assertion,
)
from butlers.core.runtime_probe_control.keys import parse_verifier_keyring_document

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).parent / "fixtures" / "assertion-v1.json"
SEED = bytes(range(32))
KID = "mcp-gateway-test-v1"
AUD = "https://mcp.tze.how/butlers"
SUB = "1234567"
NOW = 1_794_640_000
HEADER = {"alg": "EdDSA", "kid": KID, "typ": "butlers-mcp-assertion+v1"}


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _sign(header: bytes, payload: bytes) -> str:
    signing_input = f"{_b64u(header)}.{_b64u(payload)}"
    signature = ed25519.Ed25519PrivateKey.from_private_bytes(SEED).sign(signing_input.encode())
    return f"{signing_input}.{_b64u(signature)}"


def _vector(name: str, body: str, *, query: str = "", expect: str = "accept", **edit: Any):
    message = json.loads(body)
    params = message.get("params", {})
    payload = {
        "v": 1,
        "iss": "mcp-gateway",
        "aud": AUD,
        "sub": SUB,
        "cid": "https://claude.ai/oauth/mcp-oauth-client-metadata",
        "mth": message["method"],
        "tool": params.get("name", "") if message["method"] == "tools/call" else "",
        "path": query,
        "bsha": _b64u(hashlib.sha256(body.encode()).digest()),
        "jti": _b64u(hashlib.sha256(name.encode()).digest()[:16]),
        "iat": NOW,
        "exp": NOW + 30,
    }
    payload.update(edit.pop("claims", {}))
    payload_bytes = edit.pop("payload_bytes", None) or rfc8785.dumps(payload)
    header_bytes = rfc8785.dumps(HEADER)
    assertion = _sign(header_bytes, payload_bytes)
    if edit.pop("tamper", False):
        assertion = assertion[:-2] + ("AA" if not assertion.endswith("AA") else "BA")
    sent = edit.pop("sent_body", body)
    assert not edit
    return {
        "name": name,
        "now": NOW,
        "body": sent,
        "query": query,
        "header_json": header_bytes.decode(),
        "payload_json": payload_bytes.decode(),
        "assertion": assertion,
        "expect": expect,
    }


def build() -> dict[str, Any]:
    search = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search","arguments":{"query":"Avery"}}}'
    escapes = (
        '{"jsonrpc":"2.0","id":"r-é","method":"tools/call","params":{"name":"search",'
        '"arguments":{"query":"caf\\u00e9 \\"quoted\\" \\\\ € </tag>"}}}'
    )
    canonical = rfc8785.dumps(json.loads(_vector("x", search)["payload_json"]))
    unsorted = json.dumps(json.loads(canonical), separators=(",", ":")).encode()
    unsorted = b'{"v":1,' + unsorted[1:].replace(b',"v":1', b"")
    escaped = _vector("escaped-path", search, query="q=é")["payload_json"]
    return {
        "description": "RFC 0039 D7 Worker assertion vectors; synthetic test key only",
        "test_seed_hex": SEED.hex(),
        "public_key_b64u": _b64u(
            ed25519.Ed25519PrivateKey.from_private_bytes(SEED).public_key().public_bytes_raw()
        ),
        "keyring": {
            "version": 1,
            "current": {
                "alg": "EdDSA",
                "kid": KID,
                "public_key_b64u": _b64u(
                    ed25519.Ed25519PrivateKey.from_private_bytes(SEED)
                    .public_key()
                    .public_bytes_raw()
                ),
                "sign_from": "2026-11-09T04:30:00Z",
            },
            "retiring": [],
        },
        "aud": AUD,
        "allowed_subs": [SUB],
        "vectors": [
            _vector("tools-call-search", search),
            _vector("tools-list-exempt", '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'),
            _vector("jcs-escaping", escapes, query='q="a\\b"&x=é€/<>'),
            _vector("query-percent-encoded", search, query="x=caf%C3%A9&y=%22"),
            _vector(
                "refuse-noncanonical-order", search, payload_bytes=unsorted, expect="noncanonical"
            ),
            _vector(
                "refuse-escaped-non-ascii",
                search,
                query="q=é",
                payload_bytes=escaped.replace("é", "\\u00e9").encode(),
                expect="noncanonical",
            ),
            _vector(
                "refuse-expired", search, claims={"iat": NOW - 31, "exp": NOW - 1}, expect="expired"
            ),
            _vector(
                "refuse-body-swap",
                search,
                expect="binding",
                sent_body=search.replace("Avery", "Blake"),
            ),
            _vector("refuse-bad-signature", search, tamper=True, expect="signature"),
        ],
    }


def test_the_checked_in_vectors_are_reproducible():
    assert json.loads(FIXTURE.read_text()) == build()


@pytest.mark.parametrize("vector", build()["vectors"], ids=lambda v: v["name"])
def test_each_vector_reaches_its_expectation(vector):
    document = build()
    keyring = parse_verifier_keyring_document(json.dumps(document["keyring"]).encode())
    policy = AssertionPolicy(
        "mcp-gateway",
        document["aud"],
        frozenset(document["allowed_subs"]),
        frozenset({"claude.ai", "chatgpt.com"}),
        frozenset(),
        process_start=vector["now"] - 3600,
    )
    body = vector["body"].encode()
    message = json.loads(body)
    tool = message.get("params", {}).get("name", "") if message["method"] == "tools/call" else ""
    try:
        verify_assertion(
            vector["assertion"],
            keyring=keyring,
            policy=policy,
            replay=ReplaySet(),
            now=vector["now"],
            body=body,
            method=message["method"],
            tool=tool,
            query=vector["query"],
        )
        outcome = "accept"
    except AuthRefused as refused:
        outcome = refused.cause
    assert outcome == vector["expect"]


def test_the_vectors_sign_jcs_bytes_with_utf8_not_escapes():
    vector = next(v for v in build()["vectors"] if v["name"] == "jcs-escaping")
    payload = vector["payload_json"]
    assert '"path":"q=\\"a\\\\b\\"&x=é€/<>"' in payload
    assert list(json.loads(payload)) == sorted(json.loads(payload))
    assert datetime.fromtimestamp(vector["now"], UTC).year == 2026


if __name__ == "__main__":
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n")
