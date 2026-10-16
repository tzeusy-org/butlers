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


@pytest.mark.parametrize("phase", ["before_constructor", "before_verify", "reload_before_verify"])
def test_verifier_privacy_survives_library_logging_reconfiguration(caplog, monkeypatch, phase):
    import importlib
    import logging.config
    import sys

    from fido2 import server

    loggers = [logging.getLogger(name) for name in ("fido2", "fido2.server")]
    saved = [(each, each.level, each.disabled, each.propagate) for each in loggers]
    try:
        if phase != "before_constructor":
            verifier = WebAuthnVerifier(ORIGIN, RP)
            monkeypatch.setattr(sys.modules[__name__], "WebAuthnVerifier", lambda *_: verifier)
        # The standard incremental path re-enables named loggers and their
        # propagation without replacing handlers or disabling ordinary capture.
        logging.config.dictConfig(
            {
                "version": 1,
                "incremental": True,
                "loggers": {
                    name: {"level": "DEBUG", "propagate": True}
                    for name in ("fido2", "fido2.server")
                },
            }
        )
        if phase == "reload_before_verify":
            importlib.reload(server)
        logging.getLogger("fido2.server").warning("library-category-capture-active")
        logging.getLogger("fido2").warning("library-parent-capture-active")
        # Reuse the complete existing real-proof/privacy/capture control. No
        # assertion is retired when exercising the later logging lifecycle.
        test_verifier_suppresses_library_material_and_never_reflects_errors(caplog)
        assert "library-category-capture-active" in caplog.text
        assert "library-parent-capture-active" in caplog.text
        for rsa_key in (False, True):
            key = Passkey.create(rsa_key=rsa_key)
            challenge, handle = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            options = {"publicKey": {"challenge": challenge, "user": {"id": handle}}}
            verifier = WebAuthnVerifier(ORIGIN, RP)
            registered = verifier.verify(
                {"operation": "enroll", "challenge": challenge},
                key.response(options, register=True),
                None,
            )
            stored = {
                "credential_data": registered.credential_data,
                "user_handle": handle,
                "backup_eligible": registered.backup_eligible,
                "counter": registered.counter,
            }
            assertion = key.response(options, register=False, user_handle=handle)
            # Reconfiguration and reload also occur after construction. The
            # cached instance's methods still read the module publisher.
            logging.config.dictConfig(
                {
                    "version": 1,
                    "incremental": True,
                    "loggers": {
                        name: {"level": "DEBUG", "propagate": True}
                        for name in ("fido2", "fido2.server")
                    },
                }
            )
            if phase == "reload_before_verify":
                importlib.reload(server)
            authenticated = verifier.verify(
                {"operation": "login", "challenge": challenge}, assertion, stored
            )
            assert authenticated.credential_id == assertion["rawId"]
            assert authenticated.credential_data is None
            private = (
                assertion["rawId"],
                key.credential_id.hex(),
                assertion["response"]["signature"],
                registered.credential_data,
                handle,
                challenge,
            )
            assertion["response"]["signature"] = encode(b"invalid-signature")
            with pytest.raises(
                InvalidProof, match="^Invalid owner authentication proof$"
            ) as caught:
                verifier.verify({"operation": "login", "challenge": challenge}, assertion, stored)
            for material in private:
                assert material not in caplog.text
                assert material not in str(caught.value)
            assert "capture-active" in caplog.text
    finally:
        for each, level, disabled, propagate in saved:
            each.setLevel(level)
            each.disabled = disabled
            each.propagate = propagate
