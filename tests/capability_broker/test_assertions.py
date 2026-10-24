"""Raw-wire and cryptographic boundaries; no deployment key or live verifier."""

import base64
import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from butlers.capability_broker.assertions import AssertionSigner, verify_assertion
from butlers.capability_broker.contracts import (
    BrokerRefused,
    Capability,
    Decision,
    Disposition,
    Principal,
    Reservation,
    mint_token,
    token_hash,
)
from butlers.core.capability_json import (
    InvalidArguments,
    arguments_digest,
    canonical_bytes,
    parse_arguments,
)
from butlers.core.tool_catalog import RecipientParameters

pytestmark = pytest.mark.unit


def test_official_jcs_utf16_sort_and_number_vectors():
    # RFC8785 section 3.2.3 sorts UTF-16 code units, not Python code points.
    sample = {
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "דּ": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "😀": "Emoji: Grinning Face",
        "\u0080": "Control",
        "ö": "Latin Small Letter O With Diaeresis",
    }
    assert list(json.loads(canonical_bytes(sample))) == ["\r", "1", "\u0080", "ö", "€", "😀", "דּ"]
    # RFC8785 section 3.2.2 and Appendix B, with negative zero preserved on input.
    sample = parse_arguments(b'{"n":[333333333.33333329,1E30,4.50,2e-3,1e-27,-0.0]}')
    assert canonical_bytes(sample) == b'{"n":[333333333.3333333,1e+30,4.5,0.002,1e-27,0]}'
    assert arguments_digest({"x": "é"}) != arguments_digest({"x": "e\u0301"})
    assert arguments_digest({"x": "1"}) != arguments_digest({"x": 1})


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":{"k":1,"k":2}}',
        b'{"x":NaN}',
        b'{"x":1e999}',
        b'{"x":9007199254740992}',
        b'{"x":"\\udead"}',
        b"[]",
    ],
)
def test_ambiguous_raw_input_is_refused(raw):
    with pytest.raises(InvalidArguments):
        parse_arguments(raw)


def make_reservation():
    capability = Capability(
        "health", "read", "read", "trusted", (), recipient_params=RecipientParameters()
    )
    principal = Principal(uuid4(), uuid4(), 0, "owner", False, "catalog", ())
    return Reservation(
        principal,
        capability,
        Decision(Disposition.DISPATCH),
        uuid4(),
        None,
        arguments_digest({"amount": 1.0}),
    )


def test_real_signature_exact_d7_shape_and_atomic_replay_claim():
    key = Ed25519PrivateKey.generate()
    signer = AssertionSigner("current", key)
    reservation = make_reservation()
    assertion = signer.sign(reservation, issued_at=100)
    claims = set()

    def claim(call, expiry):
        assert expiry == 160
        if call in claims:
            return False
        claims.add(call)
        return True

    args = dict(
        keys={"current": key.public_key()},
        audience="health",
        capability="read",
        arguments={"amount": 1},
        now=100,
        claim_call_id=claim,
    )
    payload = verify_assertion(assertion, **args)
    assert payload["effect_id"] is None and payload["approval_ref"] is None
    assert payload["exp"] == 160 and payload["call_id"] == str(reservation.call_id)
    with pytest.raises(BrokerRefused):
        verify_assertion(assertion, **args)
    for changes in (
        {"audience": "home"},
        {"capability": "write"},
        {"arguments": {"amount": "1"}},
        {"now": 160},
        {"keys": {}},
        {"keys": {"current": Ed25519PrivateKey.generate().public_key()}},
    ):
        with pytest.raises(BrokerRefused):
            verify_assertion(assertion, **(args | changes | {"claim_call_id": lambda *_: True}))
    header = json.loads(base64.urlsafe_b64decode(assertion.split(".")[0] + "=="))
    assert header == {"alg": "EdDSA", "kid": "current"}


def test_validly_signed_wrong_shape_and_noncanonical_wire_refuse_before_replay():
    key = Ed25519PrivateKey.generate()
    good = AssertionSigner("current", key).sign(make_reservation(), issued_at=100)
    head, body, _ = good.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=="))
    seen = []

    def encode(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    candidates = []
    for changed in (
        payload | {"iss": "invented"},
        payload | {"depth": True},
        payload | {"effect_id": "bad"},
    ):
        content = f"{head}.{encode(canonical_bytes(changed))}"
        candidates.append(f"{content}.{encode(key.sign(content.encode()))}")
    content = f"{head}.{encode(json.dumps(payload).encode())}"
    candidates.append(f"{content}.{encode(key.sign(content.encode()))}")
    candidates.extend([good + "=", good[:-4], good.replace(head, head + "=", 1)])
    for candidate in candidates:
        with pytest.raises(BrokerRefused):
            verify_assertion(
                candidate,
                keys={"current": key.public_key()},
                audience="health",
                capability="read",
                arguments={"amount": 1},
                now=100,
                claim_call_id=lambda *a: seen.append(a),
            )
    assert seen == []


def test_tokens_have_one_256_bit_spelling_and_no_plaintext_persistence_value():
    token, hashed = mint_token()
    assert len(token) == 43 and len(hashed) == 64 and token not in hashed
    assert token_hash(token) == hashed
    for invalid in (token + "=", token[:-1], "!", "a" * 42 + "B"):
        with pytest.raises(BrokerRefused):
            token_hash(invalid)
    signer = AssertionSigner("test", Ed25519PrivateKey.generate())
    with pytest.raises(BrokerRefused):
        signer.sign(make_reservation(), issued_at=True)
    assert "private_key" not in repr(signer)
