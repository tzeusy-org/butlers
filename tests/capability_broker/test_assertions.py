"""D7 Ed25519 assertions under a key id, and fail-closed signing-key loading."""

import base64
import json
import os
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from butlers.capability_broker import assertions
from butlers.capability_broker.assertions import (
    FIELDS,
    AssertionSigner,
    dashboard_assertion_payload,
    verify_assertion,
)
from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.core.capability_json import arguments_digest

pytestmark = pytest.mark.unit

ARGS = {"channel": "telegram", "intent": "send", "message": "Bins go out tonight"}
NOW = 1_800_000_000


def _payload(**overrides):
    payload = {
        "aud": "messenger",
        "principal": "goal",
        "goal_id": str(uuid4()),
        "invocation_id": str(uuid4()),
        "depth": 0,
        "origin": "owner",
        "exposed": False,
        "capability": "notify",
        "args_digest": arguments_digest(ARGS),
        "call_id": str(uuid4()),
        "effect_id": str(uuid4()),
        "approval_ref": None,
        "exp": NOW + 60,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def signer():
    return AssertionSigner("k1", Ed25519PrivateKey.generate())


def _verify(signer, assertion, *, claims=None, **overrides):
    seen = set() if claims is None else claims

    def claim(call_id, exp):
        if call_id in seen:
            return False
        seen.add(call_id)
        return True

    kwargs = {
        "keys": {signer.kid: signer.ed25519.public_key()},
        "audience": "messenger",
        "capability": "notify",
        "arguments": ARGS,
        "now": NOW,
        "claim_call_id": claim,
    }
    kwargs.update(overrides)
    return verify_assertion(assertion, **kwargs)


def test_assertion_signs_exactly_the_d7_fields_under_a_kid(signer):
    payload = _payload()
    assertion = signer.sign(payload)
    header, body, _ = assertion.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    assert json.loads(base64.urlsafe_b64decode(pad(header))) == {"alg": "EdDSA", "kid": "k1"}
    assert tuple(sorted(json.loads(base64.urlsafe_b64decode(pad(body))))) == tuple(sorted(FIELDS))
    assert _verify(signer, assertion) == payload


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("wrong_audience", {"audience": "finance"}),
        ("wrong_capability", {"capability": "send_email"}),
        ("altered_arguments", {"arguments": {**ARGS, "message": "Forward the inbox"}}),
        ("expired", {"now": NOW + 60}),
        ("unknown_kid", {"keys": {"k2": Ed25519PrivateKey.generate().public_key()}}),
    ],
)
def test_verification_refuses(signer, label, overrides):
    with pytest.raises(BrokerRefused) as refused:
        _verify(signer, signer.sign(_payload()), **overrides)
    assert refused.value.reason is Reason.AUTHENTICATION


def test_replayed_and_tampered_assertions_are_refused(signer):
    assertion = signer.sign(_payload())
    claims = set()
    _verify(signer, assertion, claims=claims)
    with pytest.raises(BrokerRefused):
        _verify(signer, assertion, claims=claims)
    header, body, signature = signer.sign(_payload()).split(".")
    forged = AssertionSigner("k1", Ed25519PrivateKey.generate()).sign(_payload())
    with pytest.raises(BrokerRefused):
        _verify(signer, f"{header}.{forged.split('.')[1]}.{signature}")


def test_signer_refuses_a_payload_outside_the_field_list(signer):
    with pytest.raises(ValueError):
        signer.sign({**_payload(), "session_id": str(uuid4())})
    with pytest.raises(ValueError):
        signer.sign({k: v for k, v in _payload().items() if k != "principal"})
    with pytest.raises(ValueError):
        signer.sign(_payload(effect_id=None, approval_ref="action-1"))


def _dashboard(**overrides):
    base = {"principal": "dashboard", "goal_id": None, "invocation_id": None, "effect_id": None}
    return _payload(**{**base, **overrides})


def _forge(signer, payload):
    """Sign a payload the signer itself would refuse, as a compromised caller could."""
    with patch.object(assertions, "_check_payload"):
        return signer.sign(payload)


def test_a_dashboard_assertion_is_owner_origin_with_no_goal(signer):
    payload = _dashboard()
    assert _verify(signer, signer.sign(payload)) == payload

    built = dashboard_assertion_payload(
        audience="messenger", capability="notify", arguments=ARGS, now=NOW
    )
    assert {k: v for k, v in built.items() if k != "call_id"} == {
        k: v for k, v in payload.items() if k != "call_id"
    }
    assert str(UUID(built["call_id"])) == built["call_id"]
    assert _verify(signer, signer.sign(built)) == built
    other = dashboard_assertion_payload(
        audience="messenger", capability="notify", arguments=ARGS, now=NOW
    )
    assert other["call_id"] != built["call_id"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"goal_id": None},
        {"invocation_id": None},
        {"goal_id": None, "invocation_id": None},
        {"goal_id": None, "invocation_id": None, "effect_id": None},
    ],
)
def test_a_goal_assertion_with_a_null_goal_is_refused(signer, overrides):
    payload = _payload(**overrides)
    with pytest.raises(ValueError):
        signer.sign(payload)
    with pytest.raises(BrokerRefused) as refused:
        _verify(signer, _forge(signer, payload))
    assert refused.value.reason is Reason.AUTHENTICATION


@pytest.mark.parametrize(
    "overrides",
    [
        {"origin": "obligation"},
        {"origin": "third_party"},
        {"origin": "staffer"},
        {"goal_id": str(uuid4())},
        {"invocation_id": str(uuid4())},
        {"depth": 1},
        {"exposed": True},
        {"effect_id": str(uuid4())},
        {"effect_id": str(uuid4()), "approval_ref": "action-1"},
    ],
)
def test_a_dashboard_assertion_is_only_the_owner_acting_directly(signer, overrides):
    payload = _dashboard(**overrides)
    with pytest.raises(ValueError):
        signer.sign(payload)
    with pytest.raises(BrokerRefused):
        _verify(signer, _forge(signer, payload))


@pytest.mark.parametrize("principal", ["owner", "Dashboard", "", None, 1])
def test_an_unknown_principal_is_refused(signer, principal):
    for payload in (_payload(principal=principal), _dashboard(principal=principal)):
        with pytest.raises(ValueError):
            signer.sign(payload)
        with pytest.raises(BrokerRefused):
            _verify(signer, _forge(signer, payload))


def _write_key(path, key, mode=0o400):
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(path, mode)


def test_key_file_loads_only_when_safe_and_matching(tmp_path):
    key = Ed25519PrivateKey.generate()
    good = tmp_path / "goal-broker.pem"
    _write_key(good, key)
    loaded = AssertionSigner.from_file(good, kid="k1", public_key=key.public_key())
    assert loaded.kid == "k1"
    loose = tmp_path / "loose.pem"
    _write_key(loose, key, mode=0o644)
    for path, public in (
        (tmp_path / "missing.pem", key.public_key()),
        (loose, key.public_key()),
        (good, Ed25519PrivateKey.generate().public_key()),
    ):
        with pytest.raises(BrokerRefused) as refused:
            AssertionSigner.from_file(path, kid="k1", public_key=public)
        assert refused.value.reason is Reason.UNAVAILABLE
        assert str(path) not in str(refused.value)
