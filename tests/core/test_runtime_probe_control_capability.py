"""Compact-JWS runtime-probe control capabilities: signing and strict verification.

Covers REQ-core-credentials-002 (Asymmetric Runtime-Probe Control Capability)
and the rotation half of REQ-dashboard-model-settings-001 (Catalog Test Uses a
Runtime Probe, Not a Dashboard-Local Adapter).

This module is the whole of acceptance criterion 1's rejection surface: every
malformed, misdirected, mistimed, or wrongly-keyed capability has to die here,
because ``verify_capability`` runs strictly before the coordinator's first side
effect.  The negative cases are therefore the point of the file; the single
happy path exists only to prove the negatives are rejecting something that
would otherwise have been accepted.

Every key in this module is synthetic and generated inside the test.  No key
material, capability, signature, or nonce is printed or asserted on positively;
redaction is checked by asserting the *absence* of the generated material.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from butlers.core.runtime_probe_control import capability as cap
from butlers.core.runtime_probe_control.keys import (
    CONTROL_AUDIENCE,
    VerifierKeyring,
    parse_signer_document,
    parse_verifier_keyring_document,
)
from butlers.testing.runtime_probe_control import (
    current_entry,
    keyring_document,
    retiring_entry,
    signer_document,
    synthetic_keypair,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
_CURRENT_KID = "probe-2026-05a"
_RETIRING_KID = "probe-2026-04a"
_ENTRY_ID = UUID("a1b2c3d4-5566-4788-99aa-bbccddeeff01")


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _encode(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


@pytest.fixture
def signer():
    """A current signer whose ``sign_from`` is well before the test clock."""
    seed, public_key = synthetic_keypair()
    parsed = parse_signer_document(
        _encode(signer_document(seed, kid=_CURRENT_KID, sign_from=_NOW - timedelta(days=1)))
    )
    return parsed, public_key


@pytest.fixture
def keyring(signer) -> VerifierKeyring:
    _, public_key = signer
    return parse_verifier_keyring_document(
        _encode(
            keyring_document(
                current_entry(public_key, kid=_CURRENT_KID, sign_from=_NOW - timedelta(days=1))
            )
        )
    )


def _sign(signer_key, **overrides) -> str:
    kwargs: dict[str, object] = {
        "caller": "dashboard",
        "catalog_entry_id": _ENTRY_ID,
        "now": _NOW,
    }
    kwargs.update(overrides)
    return cap.sign_capability(signer_key, **kwargs)  # type: ignore[arg-type]


def _resign(signer_key, *, header: dict | None = None, payload: dict | None = None) -> str:
    """Re-sign an arbitrary header/payload pair with a real key.

    Tampering with a well-formed capability would be rejected by the signature
    check before the shape checks ever ran, which would make every "extra
    claim" case pass for the wrong reason.  Signing the malformed document
    proves the shape check itself is doing the rejecting.
    """
    return cap._sign_segments(signer_key, header=header or {}, payload=payload or {})


def _decoded(compact: str) -> tuple[dict, dict]:
    header_segment, payload_segment, _ = compact.split(".")
    return (
        json.loads(base64.urlsafe_b64decode(header_segment + "==")),
        json.loads(base64.urlsafe_b64decode(payload_segment + "==")),
    )


# ---------------------------------------------------------------------------
# The one accepted shape
# ---------------------------------------------------------------------------


def test_signed_capability_has_exactly_the_approved_header_and_claims(signer, keyring) -> None:
    signer_key, _ = signer

    compact = _sign(signer_key, caller="scheduler")
    header, payload = _decoded(compact)

    assert header == {"alg": "EdDSA", "kid": _CURRENT_KID}
    assert set(payload) == {"aud", "caller", "catalog_entry_id", "iat", "exp", "nonce"}
    assert payload["aud"] == CONTROL_AUDIENCE
    assert payload["caller"] == "scheduler"
    assert payload["catalog_entry_id"] == str(_ENTRY_ID)
    assert payload["exp"] - payload["iat"] == int(cap.DEFAULT_LIFETIME.total_seconds())
    assert len(base64.urlsafe_b64decode(payload["nonce"] + "======")) == cap.NONCE_BYTES

    verified = cap.verify_capability(compact, keyring=keyring, now=_NOW)

    assert verified.kid == _CURRENT_KID
    assert verified.caller == "scheduler"
    assert verified.catalog_entry_id == _ENTRY_ID
    assert len(verified.nonce) == cap.NONCE_BYTES


def test_each_signature_carries_a_fresh_nonce(signer, keyring) -> None:
    """A reused nonce would make the durable receipt unable to tell replay apart."""
    signer_key, _ = signer

    nonces = {
        cap.verify_capability(_sign(signer_key), keyring=keyring, now=_NOW).nonce for _ in range(8)
    }

    assert len(nonces) == 8


def test_verified_capability_repr_discloses_no_material(signer, keyring) -> None:
    signer_key, _ = signer
    verified = cap.verify_capability(_sign(signer_key), keyring=keyring, now=_NOW)

    rendered = f"{verified!r}"

    assert _b64u(verified.nonce) not in rendered
    assert str(verified.catalog_entry_id) not in rendered


# ---------------------------------------------------------------------------
# Structural rejection: envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "compact",
    [
        pytest.param("", id="empty"),
        pytest.param("a.b", id="two-segments"),
        pytest.param("a.b.c.d", id="four-segments"),
        pytest.param("..", id="empty-segments"),
        pytest.param("a b.c.d", id="space-in-segment"),
        pytest.param("YQ==.YQ.YQ", id="padded-segment"),
        pytest.param("a+/b.c.d", id="standard-alphabet"),
        pytest.param("é.b.c", id="non-ascii"),
    ],
)
def test_malformed_envelope_is_rejected(compact: str, keyring) -> None:
    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(compact, keyring=keyring, now=_NOW)


def test_absent_capability_is_rejected(keyring) -> None:
    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(None, keyring=keyring, now=_NOW)


# ---------------------------------------------------------------------------
# Structural rejection: protected header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        pytest.param({"alg": "none", "kid": _CURRENT_KID}, id="none-algorithm"),
        pytest.param({"alg": "HS256", "kid": _CURRENT_KID}, id="symmetric-algorithm"),
        pytest.param({"alg": "RS256", "kid": _CURRENT_KID}, id="other-asymmetric-algorithm"),
        pytest.param({"alg": "Ed25519", "kid": _CURRENT_KID}, id="near-miss-algorithm"),
        pytest.param({"alg": None, "kid": _CURRENT_KID}, id="null-algorithm"),
        pytest.param({"kid": _CURRENT_KID}, id="missing-algorithm"),
        pytest.param({"alg": "EdDSA"}, id="missing-kid"),
        pytest.param({"alg": "EdDSA", "kid": 7}, id="non-string-kid"),
        pytest.param({"alg": "EdDSA", "kid": "has spaces"}, id="kid-outside-grammar"),
        pytest.param(
            {"alg": "EdDSA", "kid": _CURRENT_KID, "typ": "JWT"},
            id="extra-typ-header",
        ),
        pytest.param(
            {"alg": "EdDSA", "kid": _CURRENT_KID, "jku": "https://example.invalid/jwks"},
            id="jku-header",
        ),
        pytest.param(
            {"alg": "EdDSA", "kid": _CURRENT_KID, "x5u": "https://example.invalid/x5u"},
            id="x5u-header",
        ),
        pytest.param(
            {"alg": "EdDSA", "kid": _CURRENT_KID, "jwk": {"kty": "OKP"}},
            id="embedded-jwk-header",
        ),
        pytest.param({"alg": "EdDSA", "kid": _CURRENT_KID, "crit": ["kid"]}, id="crit-header"),
        pytest.param([], id="header-is-array"),
    ],
)
def test_unapproved_protected_header_is_rejected(header, signer, keyring) -> None:
    """No token-selected algorithm and no dynamic key source is trusted."""
    signer_key, _ = signer
    compact = _resign(
        signer_key,
        header=header,
        payload=_decoded(_sign(signer_key))[1],
    )

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(compact, keyring=keyring, now=_NOW)


def test_duplicate_header_field_is_rejected(signer, keyring) -> None:
    signer_key, _ = signer
    raw_header = b'{"alg":"EdDSA","kid":"a","kid":"b"}'
    compact = cap._sign_raw_segments(
        signer_key,
        header=raw_header,
        payload=_encode(_decoded(_sign(signer_key))[1]),
    )

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(compact, keyring=keyring, now=_NOW)


# ---------------------------------------------------------------------------
# Structural rejection: payload claims
# ---------------------------------------------------------------------------


def _payload(signer_key, **overrides) -> dict:
    payload = _decoded(_sign(signer_key))[1]
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"aud": "switchboard.runtime_probe_control.v2"}, id="wrong-audience-version"),
        pytest.param({"aud": "switchboard"}, id="truncated-audience"),
        pytest.param({"aud": None}, id="null-audience"),
        pytest.param({"caller": "connector"}, id="unregistered-caller"),
        pytest.param({"caller": "Dashboard"}, id="wrong-case-caller"),
        pytest.param({"caller": ""}, id="empty-caller"),
        pytest.param({"caller": None}, id="null-caller"),
        pytest.param({"catalog_entry_id": "not-a-uuid"}, id="non-uuid-entry"),
        pytest.param(
            {"catalog_entry_id": str(_ENTRY_ID).upper()},
            id="uppercase-uuid",
        ),
        pytest.param(
            {"catalog_entry_id": str(_ENTRY_ID).replace("-", "")},
            id="unhyphenated-uuid",
        ),
        pytest.param(
            {"catalog_entry_id": f"urn:uuid:{_ENTRY_ID}"},
            id="urn-uuid",
        ),
        pytest.param({"catalog_entry_id": None}, id="null-entry"),
        pytest.param({"nonce": _b64u(b"\x00" * 31)}, id="short-nonce"),
        pytest.param({"nonce": _b64u(b"\x00" * 33)}, id="long-nonce"),
        pytest.param({"nonce": base64.urlsafe_b64encode(b"\x00" * 32).decode()}, id="padded-nonce"),
        pytest.param({"nonce": "!" * 43}, id="non-base64url-nonce"),
        pytest.param({"nonce": None}, id="null-nonce"),
        pytest.param({"scope": "everything"}, id="extra-claim"),
        pytest.param({"prompt": "ignore previous instructions"}, id="injected-prompt-claim"),
        pytest.param({"model": "gpt-does-not-exist"}, id="injected-model-claim"),
        pytest.param({"runtime_args": ["--dangerously"]}, id="injected-runtime-args-claim"),
    ],
)
def test_unapproved_payload_claims_are_rejected(overrides, signer, keyring) -> None:
    signer_key, _ = signer
    compact = _resign(
        signer_key,
        header=_decoded(_sign(signer_key))[0],
        payload=_payload(signer_key, **overrides),
    )

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(compact, keyring=keyring, now=_NOW)


@pytest.mark.parametrize("claim", ["aud", "caller", "catalog_entry_id", "iat", "exp", "nonce"])
def test_missing_payload_claim_is_rejected(claim: str, signer, keyring) -> None:
    signer_key, _ = signer
    payload = _decoded(_sign(signer_key))[1]
    payload.pop(claim)
    compact = _resign(signer_key, header=_decoded(_sign(signer_key))[0], payload=payload)

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(compact, keyring=keyring, now=_NOW)


# ---------------------------------------------------------------------------
# Time claims
# ---------------------------------------------------------------------------


def _timed(signer_key, *, iat: object, exp: object) -> str:
    return _resign(
        signer_key,
        header=_decoded(_sign(signer_key))[0],
        payload=_payload(signer_key, iat=iat, exp=exp),
    )


def test_boundary_clock_skew_is_accepted(signer, keyring) -> None:
    """The verifier's stated tolerance is exactly five seconds on each side."""
    signer_key, _ = signer
    epoch = int(_NOW.timestamp())

    future_iat = _timed(signer_key, iat=epoch + 5, exp=epoch + 65)
    just_expired = _timed(signer_key, iat=epoch - 60, exp=epoch - 5)

    assert cap.verify_capability(future_iat, keyring=keyring, now=_NOW).kid == _CURRENT_KID
    assert cap.verify_capability(just_expired, keyring=keyring, now=_NOW).kid == _CURRENT_KID


@pytest.mark.parametrize(
    ("delta_iat", "delta_exp"),
    [
        pytest.param(6, 66, id="iat-one-second-beyond-skew"),
        pytest.param(3600, 3660, id="far-future-iat"),
        pytest.param(-120, -6, id="exp-one-second-beyond-skew"),
        pytest.param(-7200, -7140, id="long-expired"),
        pytest.param(0, 0, id="zero-lifetime"),
        pytest.param(0, -1, id="negative-lifetime"),
        pytest.param(0, 61, id="lifetime-over-one-minute"),
        pytest.param(0, 86400, id="day-long-lifetime"),
    ],
)
def test_out_of_bounds_time_claims_are_rejected(delta_iat, delta_exp, signer, keyring) -> None:
    signer_key, _ = signer
    epoch = int(_NOW.timestamp())

    compact = _timed(signer_key, iat=epoch + delta_iat, exp=epoch + delta_exp)

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(compact, keyring=keyring, now=_NOW)


def test_maximum_lifetime_boundary_is_accepted(signer, keyring) -> None:
    signer_key, _ = signer
    epoch = int(_NOW.timestamp())

    verified = cap.verify_capability(
        _timed(signer_key, iat=epoch, exp=epoch + 60), keyring=keyring, now=_NOW
    )

    assert verified.expires_at == datetime.fromtimestamp(epoch + 60, UTC)


@pytest.mark.parametrize(
    ("iat", "exp"),
    [
        pytest.param("1777000000", "1777000060", id="string-numericdate"),
        pytest.param(1777000000.0, 1777000060.0, id="float-numericdate"),
        pytest.param(True, 1777000060, id="bool-numericdate"),
        pytest.param(None, 1777000060, id="null-numericdate"),
        pytest.param(-1, 59, id="negative-numericdate"),
        pytest.param(10**18, 10**18 + 60, id="unrepresentable-numericdate"),
    ],
)
def test_non_integer_numericdate_is_rejected(iat, exp, signer, keyring) -> None:
    signer_key, _ = signer

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(_timed(signer_key, iat=iat, exp=exp), keyring=keyring, now=_NOW)


# ---------------------------------------------------------------------------
# Signature and key selection
# ---------------------------------------------------------------------------


def test_signature_from_a_foreign_key_is_rejected(signer, keyring) -> None:
    """A well-formed capability signed by a key the keyring does not hold."""
    signer_key, _ = signer
    foreign_seed, _ = synthetic_keypair()
    foreign = parse_signer_document(
        _encode(signer_document(foreign_seed, kid=_CURRENT_KID, sign_from=_NOW - timedelta(days=1)))
    )

    compact = _sign(foreign)

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(compact, keyring=keyring, now=_NOW)
    # The genuine signer over the same claims still verifies, so the rejection
    # above is about the key rather than about the claims.
    assert cap.verify_capability(_sign(signer_key), keyring=keyring, now=_NOW)


def test_tampered_payload_invalidates_the_signature(signer, keyring) -> None:
    signer_key, _ = signer
    header_segment, payload_segment, signature = _sign(signer_key).split(".")
    payload = json.loads(base64.urlsafe_b64decode(payload_segment + "=="))
    payload["catalog_entry_id"] = str(uuid4())
    forged_payload = _b64u(_encode(payload))

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(
            f"{header_segment}.{forged_payload}.{signature}", keyring=keyring, now=_NOW
        )


def test_unknown_key_id_is_rejected_without_dynamic_lookup(signer, keyring) -> None:
    signer_key, _ = signer
    unknown = parse_signer_document(
        _encode(
            signer_document(b"\x01" * 32, kid="probe-unknown", sign_from=_NOW - timedelta(days=1))
        )
    )

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(_sign(unknown), keyring=keyring, now=_NOW)


def test_current_key_capability_issued_before_sign_from_is_rejected(signer) -> None:
    """A key is not usable before its own activation instant."""
    signer_key, public_key = signer
    late_keyring = parse_verifier_keyring_document(
        _encode(keyring_document(current_entry(public_key, kid=_CURRENT_KID, sign_from=_NOW)))
    )
    epoch = int(_NOW.timestamp())

    early = _timed(signer_key, iat=epoch - 1, exp=epoch + 59)

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(early, keyring=late_keyring, now=_NOW)


# ---------------------------------------------------------------------------
# Rotation: retiring key bounds (acceptance criterion 8)
# ---------------------------------------------------------------------------


@pytest.fixture
def rotation():
    """A keyring mid-rotation, plus the retiring signer bounded at the cutover."""
    old_seed, old_public = synthetic_keypair()
    _, new_public = synthetic_keypair()
    overlap = timedelta(seconds=120)
    old_signer = parse_signer_document(
        _encode(
            signer_document(
                old_seed,
                kid=_RETIRING_KID,
                sign_from=_NOW - timedelta(days=30),
                sign_until=_NOW,
            )
        )
    )
    ring = parse_verifier_keyring_document(
        _encode(
            keyring_document(
                current_entry(new_public, kid=_CURRENT_KID, sign_from=_NOW),
                [
                    retiring_entry(
                        old_public,
                        kid=_RETIRING_KID,
                        sign_from=_NOW - timedelta(days=30),
                        sign_until=_NOW,
                        overlap=overlap,
                    )
                ],
            )
        )
    )
    return old_signer, ring, overlap


def test_pre_cutover_capability_survives_its_whole_lifetime(rotation) -> None:
    """Issued at the last legal second, still verifiable a minute later."""
    old_signer, ring, _ = rotation
    epoch = int(_NOW.timestamp())
    compact = _resign(
        old_signer,
        header={"alg": "EdDSA", "kid": _RETIRING_KID},
        payload={
            "aud": CONTROL_AUDIENCE,
            "caller": "dashboard",
            "catalog_entry_id": str(_ENTRY_ID),
            "iat": epoch,
            "exp": epoch + 60,
            "nonce": _b64u(b"\x02" * 32),
        },
    )

    # iat == sign_until exactly, verified at the far end of its lifetime.
    assert cap.verify_capability(compact, keyring=ring, now=_NOW).kid == _RETIRING_KID
    assert (
        cap.verify_capability(compact, keyring=ring, now=_NOW + timedelta(seconds=60)).kid
        == _RETIRING_KID
    )


def test_retiring_key_issued_after_sign_until_is_rejected_without_skew(rotation) -> None:
    """``iat > sign_until`` gets no cutover-skew exception, unlike request skew."""
    old_signer, ring, _ = rotation
    epoch = int(_NOW.timestamp())
    compact = _resign(
        old_signer,
        header={"alg": "EdDSA", "kid": _RETIRING_KID},
        payload={
            "aud": CONTROL_AUDIENCE,
            "caller": "dashboard",
            "catalog_entry_id": str(_ENTRY_ID),
            "iat": epoch + 1,
            "exp": epoch + 61,
            "nonce": _b64u(b"\x03" * 32),
        },
    )

    # Request-skew alone would have accepted iat = now + 1s.
    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(compact, keyring=ring, now=_NOW + timedelta(seconds=1))


def test_retiring_key_is_dead_after_accept_until(rotation) -> None:
    old_signer, ring, overlap = rotation
    epoch = int(_NOW.timestamp())
    compact = _resign(
        old_signer,
        header={"alg": "EdDSA", "kid": _RETIRING_KID},
        payload={
            "aud": CONTROL_AUDIENCE,
            "caller": "dashboard",
            "catalog_entry_id": str(_ENTRY_ID),
            "iat": epoch,
            "exp": epoch + 60,
            "nonce": _b64u(b"\x04" * 32),
        },
    )

    with pytest.raises(cap.CapabilityRejected):
        cap.verify_capability(compact, keyring=ring, now=_NOW + overlap + timedelta(seconds=1))


# ---------------------------------------------------------------------------
# Signing side
# ---------------------------------------------------------------------------


def test_signer_refuses_an_unregistered_caller_class(signer) -> None:
    signer_key, _ = signer

    with pytest.raises(cap.CapabilityRejected):
        _sign(signer_key, caller="connector")


def test_signer_refuses_to_sign_outside_its_own_bounds(rotation) -> None:
    """A retiring signer stops at ``sign_until``; the client never over-signs."""
    old_signer, _, _ = rotation

    assert cap.sign_capability(old_signer, caller="dashboard", catalog_entry_id=_ENTRY_ID, now=_NOW)
    with pytest.raises(cap.CapabilityRejected):
        cap.sign_capability(
            old_signer,
            caller="dashboard",
            catalog_entry_id=_ENTRY_ID,
            now=_NOW + timedelta(seconds=1),
        )


def test_signer_refuses_a_lifetime_outside_the_protocol_bound(signer) -> None:
    signer_key, _ = signer

    for lifetime in (timedelta(0), timedelta(seconds=-1), timedelta(seconds=61)):
        with pytest.raises(cap.CapabilityRejected):
            _sign(signer_key, lifetime=lifetime)


# ---------------------------------------------------------------------------
# Redaction (acceptance criterion 8)
# ---------------------------------------------------------------------------


def test_rejection_reasons_never_quote_the_capability(signer, keyring, caplog) -> None:
    """Diagnostics are fixed strings, so nothing reconstructible reaches a log."""
    signer_key, _ = signer
    compact = _sign(signer_key)
    header_segment, payload_segment, signature = compact.split(".")
    forged_signature = _b64u(bytes(64))
    forged = f"{header_segment}.{payload_segment}.{forged_signature}"

    with caplog.at_level("DEBUG"), pytest.raises(cap.CapabilityRejected) as excinfo:
        cap.verify_capability(forged, keyring=keyring, now=_NOW)

    haystack = f"{excinfo.value}{caplog.text}"
    for secret in (compact, forged, header_segment, payload_segment, signature):
        assert secret not in haystack
