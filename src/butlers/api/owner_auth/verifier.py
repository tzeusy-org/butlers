"""Closed WebAuthn wire adapter around the maintained Yubico verifier.

All values here are transient verification material. Never log responses or
exception arguments. This module is deliberately independent of HTTP and SQL.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass

from fido2.server import Fido2Server
from fido2.webauthn import (
    AttestationObject,
    AttestedCredentialData,
    AuthenticationResponse,
    PublicKeyCredentialRpEntity,
    UserVerificationRequirement,
)

# Yubico's debug messages can contain credential identifiers.
logging.getLogger("fido2").disabled = True
logging.getLogger("fido2.server").disabled = True


class InvalidProof(ValueError):
    """Fixed, nonreflecting verification failure."""

    def __init__(self) -> None:
        super().__init__("Invalid owner authentication proof")


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value: object, *, maximum: int, exact: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or len(value) > (maximum * 4 + 2) // 3:
        raise InvalidProof()
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise InvalidProof()
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        raise InvalidProof() from None
    if encode(raw) != value or len(raw) > maximum or (exact is not None and len(raw) != exact):
        raise InvalidProof()
    return raw


def _shape(value: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or not required <= value.keys():
        raise InvalidProof()
    if value.keys() - required - optional:
        raise InvalidProof()
    return value


def _wire(credential: object, *, registration: bool) -> dict:
    credential = _shape(
        credential,
        {"id", "rawId", "type", "response", "clientExtensionResults"},
        {"authenticatorAttachment"},
    )
    decode(credential["rawId"], maximum=1023)
    if credential["id"] != credential["rawId"] or credential["type"] != "public-key":
        raise InvalidProof()
    if credential["clientExtensionResults"] != {}:
        raise InvalidProof()
    if "authenticatorAttachment" in credential and credential["authenticatorAttachment"] not in (
        "platform",
        "cross-platform",
        None,
    ):
        raise InvalidProof()
    if registration:
        response = _shape(
            credential["response"], {"clientDataJSON", "attestationObject"}, {"transports"}
        )
        decode(response["attestationObject"], maximum=65536)
        transports = response.get("transports", [])
        if (
            not isinstance(transports, list)
            or len(transports) > 6
            or any(
                not isinstance(item, str)
                or item not in {"usb", "nfc", "ble", "internal", "hybrid", "smart-card"}
                for item in transports
            )
        ):
            raise InvalidProof()
    else:
        response = _shape(
            credential["response"],
            {"clientDataJSON", "authenticatorData", "signature", "userHandle"},
        )
        decode(response["authenticatorData"], maximum=16384)
        decode(response["signature"], maximum=16384)
        decode(response["userHandle"], maximum=32, exact=32)
    client_raw = decode(response["clientDataJSON"], maximum=4096)
    try:
        client = json.loads(client_raw)
    except (ValueError, UnicodeError):
        raise InvalidProof() from None
    if not isinstance(client, dict) or client.get("crossOrigin", False) is not False:
        raise InvalidProof()
    if "topOrigin" in client:
        raise InvalidProof()
    return credential


@dataclass(frozen=True, repr=False)
class VerifiedCredential:
    credential_id: str
    credential_data: str | None
    backup_eligible: bool
    backup_state: bool
    counter: int


class WebAuthnVerifier:
    def __init__(self, origin: str, rp_id: str) -> None:
        self.server = Fido2Server(
            PublicKeyCredentialRpEntity(id=rp_id, name="Butlers"),
            attestation="none",
            verify_origin=lambda candidate: candidate == origin,
        )
        self.server.allowed_algorithms = [
            item for item in self.server.allowed_algorithms if item.alg in (-7, -257)
        ]

    def verify(self, ceremony: dict, credential: object, stored: dict | None) -> VerifiedCredential:
        """Return verified internal fields; the repository must recheck its snapshot."""
        try:
            registration = ceremony["operation"] != "login"
            response = _wire(credential, registration=registration)
            state = {
                "challenge": ceremony["challenge"],
                "user_verification": UserVerificationRequirement.REQUIRED,
            }
            if registration:
                attestation = AttestationObject(
                    decode(response["response"]["attestationObject"], maximum=65536)
                )
                if attestation.fmt != "none" or attestation.att_stmt:
                    raise InvalidProof()
                auth = self.server.register_complete(state, response)
                data = auth.credential_data
                if data is None or data.public_key[3] not in (-7, -257):
                    raise InvalidProof()
                if encode(data.credential_id) != response["rawId"]:
                    raise InvalidProof()
                material = encode(bytes(data))
            else:
                if stored is None or response["response"]["userHandle"] != stored["user_handle"]:
                    raise InvalidProof()
                data = AttestedCredentialData(decode(stored["credential_data"], maximum=65536))
                parsed = AuthenticationResponse.from_dict(response)
                self.server.authenticate_complete(state, [data], parsed)
                auth = parsed.response.authenticator_data
                material = None
            be, bs = bool(auth.flags & 8), bool(auth.flags & 16)
            if bs and not be:
                raise InvalidProof()
            if not registration and (
                be != stored["backup_eligible"]
                or (
                    not be
                    and (stored["counter"] > 0 or auth.counter > 0)
                    and auth.counter <= stored["counter"]
                )
            ):
                raise InvalidProof()
            return VerifiedCredential(response["rawId"], material, be, bs, auth.counter)
        except Exception:
            # The HTTP edge must never receive library exception arguments.
            raise InvalidProof() from None
