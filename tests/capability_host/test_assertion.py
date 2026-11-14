"""The Worker assertion (REQ-public-mcp-authorization-004 to -007)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import rfc8785
from cryptography.hazmat.primitives.asymmetric import ed25519

from butlers.capability_broker.assertions import AssertionSigner, dashboard_assertion_payload
from butlers.core.runtime_probe_control import capability as cap
from butlers.core.runtime_probe_control.keys import (
    parse_signer_document,
    parse_verifier_keyring_document,
)
from butlers.testing.runtime_probe_control import (
    current_entry,
    keyring_document,
    retiring_entry,
    signer_document,
)
from tests.capability_host.helpers import (
    KID,
    NOW,
    SEED,
    SUB,
    Env,
    b64u,
    call_body,
    claims,
    error_code,
    keyring_json,
    public_key,
    rpc,
    sign,
)

pytestmark = pytest.mark.unit

BODY = call_body("entity_lookup", {"q": "Avery"})


def _refused(response) -> bool:
    return json.loads(response.body).get("error", {}).get("code") == -32001


async def _cause(env: Env, assertion: str, body: bytes = BODY, **kw) -> str | None:
    calls, acquired = len(env.calls), env.projection.acquired
    response = await env.send(body, assertion=assertion, **kw)
    assert _refused(response)
    assert env.projection.acquired == acquired and len(env.calls) == calls
    return env.audits()[-1]["cause"]


async def test_a_valid_assertion_is_served(env: Env):
    message = await env.tool("entity_lookup", {"q": "Avery"})
    assert error_code(message) is None
    line = env.audits()[-1]
    assert line["sub"] == SUB and line["cid"] == "claude.ai" and line["outcome"] == "ok"


# -- REQ-004 signature and identity -------------------------------------------------------------


async def test_unknown_key_id_is_refused(env: Env):
    assert await _cause(env, sign(claims(BODY), kid="someone-else")) == "kid"


async def test_bad_signature_is_refused(env: Env):
    assert await _cause(env, sign(claims(BODY), seed=bytes(32))) == "signature"


async def test_non_canonical_payload_is_refused(env: Env):
    payload = json.dumps(dict(reversed(list(claims(BODY).items())))).encode()
    assert await _cause(env, sign(payload)) == "noncanonical"


async def test_escaped_non_ascii_is_non_canonical(env: Env):
    body = call_body("entity_lookup", {"q": "café"})
    payload = json.dumps(claims(body, path="q=café"), sort_keys=True, separators=(",", ":"))
    assert "\\u00e9" in payload
    assert await _cause(env, sign(payload.encode()), body, query="q=café") == "noncanonical"


@pytest.mark.parametrize(
    "header",
    [
        {"alg": "EdDSA", "kid": KID},
        {"alg": "EdDSA", "kid": KID, "typ": "JWT"},
        {"alg": "EdDSA", "kid": KID, "typ": "butlers-mcp-assertion+v1", "jku": "x"},
        {"alg": "none", "kid": KID, "typ": "butlers-mcp-assertion+v1"},
        b'{"kid":"mcp-gateway-test-v1","alg":"EdDSA","typ":"butlers-mcp-assertion+v1",'
        b'"typ":"butlers-mcp-assertion+v1"}',
    ],
)
async def test_header_must_be_the_exact_assertion_shape(env: Env, header):
    assert await _cause(env, sign(claims(BODY), header=header)) == "malformed"


@pytest.mark.parametrize(
    ("override", "cause"),
    [
        ({"v": 2}, "claims"),
        ({"v": True}, "claims"),
        ({"sub": 4242}, "claims"),
        ({"iss": "someone"}, "issuer"),
        ({"aud": "https://mcp.tze.how/other"}, "audience"),
        ({"sub": "999"}, "sub"),
        ({"jti": "short"}, "claims"),
        ({"jti": b64u(bytes(15)) + "AB"}, "claims"),
    ],
)
async def test_claims_are_checked(env: Env, override, cause):
    assert await _cause(env, sign(claims(BODY, **override))) == cause


async def test_a_float_time_is_non_canonical(env: Env):
    payload = rfc8785.dumps(claims(BODY)).replace(b'"iat":%d' % NOW, b'"iat":%d.0' % NOW)
    assert await _cause(env, sign(payload)) == "noncanonical"


async def test_missing_and_extra_claims_are_refused(env: Env):
    payload = claims(BODY)
    del payload["path"]
    assert await _cause(env, sign(payload)) == "malformed"
    assert await _cause(env, sign({**claims(BODY), "x": 1})) == "malformed"


@pytest.mark.parametrize(
    "cid",
    [
        "https://evil.example/claude.ai",
        "https://claude.ai.example.com/meta",
        "http://claude.ai/meta",
        "https://claude.ai:8443/meta",
        "https://user@claude.ai/meta",
        "claude.ai",
        "",
    ],
)
async def test_unknown_or_empty_clients_are_refused(env: Env, cid):
    assert await _cause(env, sign(claims(BODY, cid=cid))) == "client"


async def test_client_domains_compare_lowercased_host(env: Env):
    message = await env.tool("entity_lookup", {"q": "Avery"}, cid="https://ChatGPT.com/x.json")
    assert error_code(message) is None


async def test_a_registered_client_id_is_accepted(tmp_path):
    from dataclasses import replace

    env = Env(tmp_path)
    env.settings = replace(env.settings, registered_clients=frozenset({"dcr-abc123"}))
    env.restart()
    message = await env.tool("entity_lookup", {"q": "Avery"}, cid="dcr-abc123")
    assert error_code(message) is None
    assert env.audits()[-1]["cid"].startswith("dcr:")


def test_probe_capabilities_and_assertions_are_not_interchangeable():
    when = datetime.fromtimestamp(NOW, UTC)
    signer = parse_signer_document(
        json.dumps(signer_document(SEED, kid=KID, sign_from=when - timedelta(days=1))).encode()
    )
    keyring = parse_verifier_keyring_document(keyring_json())
    probe = cap.sign_capability(signer, caller="dashboard", catalog_entry_id=uuid4(), now=when)
    cap.verify_capability(probe, keyring=keyring, now=when)  # the positive control
    with pytest.raises(cap.CapabilityRejected, match="protected header"):
        cap.verify_signature(probe, keyring=keyring, now=when, profile=cap.ASSERTION_HEADER)
    assertion = sign(claims(BODY))
    cap.verify_signature(assertion, keyring=keyring, now=when, profile=cap.ASSERTION_HEADER)
    with pytest.raises(cap.CapabilityRejected, match="protected header"):
        cap.verify_capability(assertion, keyring=keyring, now=when)


async def test_a_probe_capability_is_refused_as_an_assertion(env: Env):
    when = datetime.fromtimestamp(NOW, UTC)
    signer = parse_signer_document(
        json.dumps(signer_document(SEED, kid=KID, sign_from=when - timedelta(days=1))).encode()
    )
    probe = cap.sign_capability(signer, caller="dashboard", catalog_entry_id=uuid4(), now=when)
    assert await _cause(env, probe) == "malformed"


async def test_broker_assertion_is_not_accepted(env: Env):
    broker = AssertionSigner(KID, ed25519.Ed25519PrivateKey.from_private_bytes(SEED))
    token = broker.sign(
        dashboard_assertion_payload(audience="relationship", capability="x", arguments={}, now=NOW)
    )
    assert await _cause(env, token) == "malformed"
    response = await env.send(BODY, assertion=None, headers=[("X-Butlers-Assertion", token)])
    assert _refused(response) and env.audits()[-1]["cause"] == "missing"


async def test_two_assertion_headers_are_refused(env: Env):
    extra = [("x-mcp-assertion", sign(claims(BODY)))]
    assert await _cause(env, sign(claims(BODY)), headers=extra) == "shape"


# -- REQ-005 freshness, replay, binding ---------------------------------------------------------


async def test_expired_assertion_is_refused(env: Env):
    token = sign(claims(BODY, iat=NOW - 31, exp=NOW - 1))
    env.clock.now = NOW
    assert await _cause(env, token) == "expired"


@pytest.mark.parametrize(
    ("iat", "exp", "cause"),
    [(NOW + 6, NOW + 36, "future"), (NOW, NOW + 61, "lifetime"), (NOW, NOW, "lifetime")],
)
async def test_clock_bounds(env: Env, iat, exp, cause):
    assert await _cause(env, sign(claims(BODY, iat=iat, exp=exp))) == cause


async def test_replayed_assertion_is_refused(env: Env):
    token = sign(claims(BODY))
    first = json.loads((await env.send(BODY, assertion=token)).body)
    assert error_code(first) is None
    assert await _cause(env, token) == "replay"


async def test_assertion_from_before_a_restart_is_refused(env: Env):
    env.clock.now = NOW + 20
    env.restart()
    assert await _cause(env, sign(claims(BODY, iat=NOW + 14, exp=NOW + 44))) == "restart"


async def test_assertion_inside_the_restart_grace_is_accepted_once(env: Env):
    env.clock.now = NOW + 20
    env.restart()
    token = sign(claims(BODY, iat=NOW + 17, exp=NOW + 47))
    assert error_code(json.loads((await env.send(BODY, assertion=token)).body)) is None
    assert await _cause(env, token) == "replay"


async def test_query_string_is_bound_and_path_is_not(env: Env):
    token = sign(claims(BODY, path=""))
    assert await _cause(env, token, query="x=1") == "binding"
    assert error_code(await env.tool("entity_lookup", {"q": "Avery"}, query="x=1")) is None


async def test_body_swap_is_refused(env: Env):
    token = sign(claims(BODY))
    swapped = call_body("finance_ledger", {"q": "Avery"})
    assert await _cause(env, token, swapped) == "binding"


@pytest.mark.parametrize("field", ["mth", "tool"])
async def test_method_and_tool_are_bound(env: Env, field):
    assert await _cause(env, sign(claims(BODY, **{field: "other"}))) == "binding"


# -- REQ-006 uniform refusal --------------------------------------------------------------------


async def test_causes_are_indistinguishable_to_the_caller(env: Env):
    token = sign(claims(BODY))
    await env.send(BODY, assertion=token)
    replayed = await env.send(BODY, assertion=token)
    other = call_body("entity_lookup", {"q": "Avery"}, rid=2)
    bad = await env.send(other, assertion=sign(claims(other), seed=bytes(32)))
    assert replayed.body.replace(b'"id":1', b'"id":2') == bad.body
    assert [a["cause"] for a in env.audits()[-2:]] == ["replay", "signature"]
    assert env.host.metrics.get("auth_failures", cause="replay") == 1


# -- REQ-007 keyring custody and rotation -------------------------------------------------------


def _rotated(new_seed: bytes, *, cutover: datetime, with_retiring: bool) -> bytes:
    current = current_entry(public_key(new_seed), kid="mcp-gateway-new", sign_from=cutover)
    retiring = [
        retiring_entry(
            public_key(SEED),
            kid=KID,
            sign_from=cutover - timedelta(days=30),
            sign_until=cutover,
            overlap=timedelta(minutes=5),
        )
    ]
    return json.dumps(keyring_document(current, retiring if with_retiring else [])).encode()


async def test_rotation_keeps_both_keys_working_then_drops_the_old(env: Env):
    new_seed = bytes(range(1, 33))
    cutover = datetime.fromtimestamp(NOW, UTC)
    env.write("keyring/keyring.json", _rotated(new_seed, cutover=cutover, with_retiring=True))
    env.reload()
    old = sign(claims(BODY))  # iat == cutover: the retiring key still issues at sign_until
    new = sign(claims(BODY), seed=new_seed, kid="mcp-gateway-new")
    assert error_code(json.loads((await env.send(BODY, assertion=old)).body)) is None
    assert error_code(json.loads((await env.send(BODY, assertion=new)).body)) is None
    late_old = sign(claims(BODY, iat=NOW + 1, exp=NOW + 31))
    assert await _cause(env, late_old) == "kid"  # issued after the retiring key's cutover
    env.write("keyring/keyring.json", _rotated(new_seed, cutover=cutover, with_retiring=False))
    env.reload()
    assert await _cause(env, sign(claims(BODY))) == "kid"


async def test_retiring_key_stops_verifying_at_accept_until(env: Env):
    cutover = datetime.fromtimestamp(NOW, UTC)
    env.write(
        "keyring/keyring.json", _rotated(bytes(range(1, 33)), cutover=cutover, with_retiring=True)
    )
    env.reload()
    env.clock.now = NOW + 5 * 60 + 1
    token = sign(claims(BODY, iat=NOW - 20, exp=NOW + 10))
    assert await _cause(env, token) == "kid"


async def test_keyring_changes_need_no_restart(env: Env):
    other = bytes(range(2, 34))
    when = datetime.fromtimestamp(NOW, UTC) - timedelta(days=1)
    env.write(
        "keyring/keyring.json",
        json.dumps(keyring_document(current_entry(public_key(other), kid="k2", sign_from=when))),
    )
    assert error_code(await env.tool("entity_lookup", {"q": "a"})) is None  # not yet reloaded
    env.reload()
    token = sign(claims(BODY), seed=other, kid="k2")
    assert error_code(json.loads((await env.send(BODY, assertion=token)).body)) is None
    assert await _cause(env, sign(claims(BODY))) == "kid"


@pytest.mark.parametrize("content", [None, b"{}", b"not json"])
async def test_a_missing_or_broken_keyring_verifies_nothing(env: Env, content):
    path = env.root / "keyring/keyring.json"
    if content is None:
        path.unlink()
    else:
        path.write_bytes(content)
    env.reload()
    assert await _cause(env, sign(claims(BODY))) == "keyring"


def test_the_live_homelab_keyring_shape_parses():
    document = {
        "version": 1,
        "current": {
            "alg": "EdDSA",
            "kid": "mcp-gateway-2026-11",
            "public_key_b64u": b64u(public_key()),
            "sign_from": "2026-11-09T04:30:00Z",
        },
        "retiring": [],
    }
    keyring = parse_verifier_keyring_document(rfc8785.dumps(document))
    assert keyring.current.kid == "mcp-gateway-2026-11" and keyring.retiring is None


async def test_keyring_symlinks_are_followed(env: Env):
    target = env.root / "keyring" / "..data"
    target.mkdir()
    (target / "keyring.json").write_bytes(keyring_json())
    (env.root / "keyring/keyring.json").unlink()
    (env.root / "keyring/keyring.json").symlink_to(target / "keyring.json")
    env.reload()
    assert error_code(await env.tool("entity_lookup", {"q": "a"})) is None


async def test_exempt_method_with_a_valid_assertion_is_verified_and_recorded(env: Env):
    token = sign(claims(rpc("ping")))
    assert json.loads((await env.send(rpc("ping"), assertion=token)).body)["result"] == {}
    assert await _cause(env, token, rpc("ping")) == "replay"
