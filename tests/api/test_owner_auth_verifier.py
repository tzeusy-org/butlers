"""Strict wire parsing and privacy around the real, maintained verifier."""

import copy
import json
import logging
import secrets

import pytest
from fido2.webauthn import AttestationObject, AttestedCredentialData, AuthenticatorData

from butlers.api.owner_auth.verifier import InvalidProof, WebAuthnVerifier, decode, encode
from tests.api.owner_auth_fixtures import ORIGIN, RP, Passkey

pytestmark = pytest.mark.unit


def vector():
    challenge, handle = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    ceremony = {"operation": "enroll", "challenge": challenge, "user_handle": handle}
    options = {"publicKey": {"challenge": challenge, "user": {"id": handle}}}
    key = Passkey.create()
    return ceremony, key.response(options, register=True)


@pytest.mark.parametrize(
    "mutation",
    [
        "cross_origin",
        "top_origin",
        "challenge",
        "origin",
        "type",
        "client_json",
        "extra_field",
        "extra_response",
        "extensions",
        "padded_base64",
        "id_mismatch",
        "oversized_id",
        "oversized_client",
        "invalid_point",
        "bad_flags",
        "no_up",
        "no_uv",
        "attestation_policy",
        "authenticator_extensions",
    ],
)
def test_registration_rejects_malformed_or_unbound_proof(mutation):
    ceremony, response = vector()
    if mutation in {"cross_origin", "top_origin", "challenge", "origin", "type"}:
        client = json.loads(decode(response["response"]["clientDataJSON"], maximum=4096))
        field, value = {
            "cross_origin": ("crossOrigin", True),
            "top_origin": ("topOrigin", ORIGIN),
            "challenge": ("challenge", secrets.token_urlsafe(32)),
            "origin": ("origin", "https://other.example.test"),
            "type": ("type", "webauthn.get"),
        }[mutation]
        client[field] = value
        response["response"]["clientDataJSON"] = encode(json.dumps(client).encode())
    elif mutation == "client_json":
        response["response"]["clientDataJSON"] = encode(b"{broken")
    elif mutation == "extra_field":
        response["approved"] = True
    elif mutation == "extra_response":
        response["response"]["verified"] = True
    elif mutation == "extensions":
        response["clientExtensionResults"] = {"unknown": True}
    elif mutation == "padded_base64":
        response["response"]["attestationObject"] += "="
    elif mutation == "id_mismatch":
        response["id"] = secrets.token_urlsafe(32)
    elif mutation == "oversized_id":
        response["id"] = response["rawId"] = encode(b"x" * 1024)
    elif mutation == "oversized_client":
        response["response"]["clientDataJSON"] = encode(b"x" * 4097)
    else:
        att = AttestationObject(decode(response["response"]["attestationObject"], maximum=65536))
        data, flags = att.auth_data.credential_data, int(att.auth_data.flags)
        extensions = None
        if mutation == "invalid_point":
            cose = copy.deepcopy(data.public_key)
            cose[-2] = cose[-3] = b"\0" * 32
            data = AttestedCredentialData.create(b"\0" * 16, data.credential_id, cose)
        elif mutation == "bad_flags":
            flags = (flags & ~8) | 16
        elif mutation == "no_up":
            flags &= ~1
        elif mutation == "no_uv":
            flags &= ~4
        elif mutation == "authenticator_extensions":
            flags |= 128
            extensions = {"unknown": True}
        auth = AuthenticatorData.create(att.auth_data.rp_id_hash, flags, 0, data, extensions)
        changed = AttestationObject.create(
            "packed" if mutation == "attestation_policy" else "none", auth, {}
        )
        response["response"]["attestationObject"] = encode(changed)
    with pytest.raises(InvalidProof, match="^Invalid owner authentication proof$"):
        WebAuthnVerifier(ORIGIN, RP).verify(ceremony, response, None)


def test_verifier_suppresses_library_material_and_never_reflects_errors(caplog):
    ceremony, response = vector()
    caplog.set_level(logging.DEBUG)
    logging.getLogger("owner_auth_test_sink").warning("capture-active")
    verifier = WebAuthnVerifier(ORIGIN, RP)
    verified = verifier.verify(ceremony, response, None)
    assert verified.credential_id == response["rawId"]
    response["response"]["attestationObject"] = "synthetic-private-auth-sentinel"
    with pytest.raises(InvalidProof) as caught:
        verifier.verify(ceremony, response, None)
    assert "capture-active" in caplog.text
    for private in (
        response["rawId"],
        decode(response["rawId"], maximum=1023).hex(),
        response["response"]["attestationObject"],
        ceremony["user_handle"],
    ):
        assert private not in caplog.text
        assert private not in str(caught.value)
    assert verified.credential_id not in repr(verified)
