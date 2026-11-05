"""D7 ``X-Butlers-Assertion``: a compact Ed25519 JWS under a key id.

Header is exactly ``{"alg": "EdDSA", "kid": ...}``; the payload is exactly the
D7 field list. Both are RFC 8785 canonical JSON, base64url without padding, and
the signature covers ASCII ``header.payload``. ``verify_assertion`` is the
pure check domain verification (bu-pkuny2) wires into the core tool wrapper;
it owns no keys and no replay store.
"""

from __future__ import annotations

import base64
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_json_object

from .contracts import BrokerRefused, Reason

ASSERTION_HEADER = "X-Butlers-Assertion"
LIFETIME_SECONDS = 60
FIELDS = (
    "aud",
    "goal_id",
    "invocation_id",
    "depth",
    "origin",
    "exposed",
    "capability",
    "args_digest",
    "call_id",
    "effect_id",
    "approval_ref",
    "exp",
)
_KID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_MAX_KEY_FILE = 8192


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("noncanonical_base64")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _b64(raw) != value:
        raise ValueError("noncanonical_base64")
    return raw


def _check_payload(payload: dict[str, Any]) -> None:
    if tuple(sorted(payload)) != tuple(sorted(FIELDS)):
        raise ValueError("assertion_shape")
    if type(payload["depth"]) is not int or payload["depth"] not in (0, 1):
        raise ValueError("assertion_depth")
    if payload["origin"] not in ("owner", "obligation", "third_party", "staffer"):
        raise ValueError("assertion_origin")
    if type(payload["exposed"]) is not bool or type(payload["exp"]) is not int:
        raise ValueError("assertion_types")
    if any(type(payload[k]) is not str or not payload[k] for k in ("aud", "capability")):
        raise ValueError("assertion_identity")
    if type(payload["args_digest"]) is not str or not re.fullmatch(
        r"[0-9a-f]{64}", payload["args_digest"]
    ):
        raise ValueError("assertion_digest")
    for key in ("goal_id", "invocation_id", "call_id", "effect_id"):
        value = payload[key]
        if key == "effect_id" and value is None:
            continue
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError("assertion_uuid")
    ref = payload["approval_ref"]
    if ref is not None and (type(ref) is not str or not ref or payload["effect_id"] is None):
        raise ValueError("assertion_approval")


@dataclass(frozen=True, slots=True)
class AssertionSigner:
    kid: str
    ed25519: Ed25519PrivateKey = field(repr=False)

    @classmethod
    def from_file(cls, path: Path, *, kid: str, public_key: Ed25519PublicKey) -> AssertionSigner:
        """Load the file-backed deployment key once at startup (RFC 0038 D7).

        The file must be a regular 0400 file owned by this process holding one
        canonical unencrypted PKCS#8 PEM whose public half equals the
        configured verification key for ``kid``. Any failure refuses with no
        path, key id or parser detail in the error.
        """
        try:
            if not path.is_absolute() or type(kid) is not str or not _KID.fullmatch(kid):
                raise ValueError("invalid_key_configuration")
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            try:
                meta = os.fstat(fd)
                if (
                    not stat.S_ISREG(meta.st_mode)
                    or stat.S_IMODE(meta.st_mode) != 0o400
                    or meta.st_uid != os.geteuid()
                    or not 0 < meta.st_size <= _MAX_KEY_FILE
                ):
                    raise ValueError("unsafe_key_file")
                raw = os.read(fd, _MAX_KEY_FILE + 1)
            finally:
                os.close(fd)
            key = serialization.load_pem_private_key(raw, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("wrong_key_algorithm")
            canonical = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            if canonical != raw or (
                key.public_key().public_bytes_raw() != public_key.public_bytes_raw()
            ):
                raise ValueError("key_mismatch")
            return cls(kid, key)
        except (OSError, ValueError, TypeError, AttributeError, UnsupportedAlgorithm):
            raise BrokerRefused(Reason.UNAVAILABLE) from None

    def sign(self, payload: dict[str, Any]) -> str:
        _check_payload(payload)
        header = _b64(canonical_bytes({"alg": "EdDSA", "kid": self.kid}))
        body = _b64(canonical_bytes(payload))
        signature = self.ed25519.sign(f"{header}.{body}".encode("ascii"))
        return f"{header}.{body}.{_b64(signature)}"


def verify_assertion(
    assertion: str,
    *,
    keys: Mapping[str, Ed25519PublicKey],
    audience: str,
    capability: str,
    arguments: dict[str, Any],
    now: int,
    claim_call_id: Callable[[UUID, int], bool],
) -> dict[str, Any]:
    """Verify one assertion against the raw received arguments, then claim it.

    Order: shape, canonical encoding, known kid, signature, payload fields,
    audience, capability, digest of the raw arguments, expiry; the replay claim
    runs last so a rejected assertion consumes nothing.
    """
    try:
        header_part, body_part, signature_part = assertion.split(".")
        header_raw, body_raw = _unb64(header_part), _unb64(body_part)
        header, payload = parse_json_object(header_raw), parse_json_object(body_raw)
        if (
            set(header) != {"alg", "kid"}
            or header["alg"] != "EdDSA"
            or canonical_bytes(header) != header_raw
            or canonical_bytes(payload) != body_raw
        ):
            raise ValueError("assertion_header")
        key = keys.get(header["kid"])
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("unknown_key")
        key.verify(_unb64(signature_part), f"{header_part}.{body_part}".encode("ascii"))
        _check_payload(payload)
        if (
            type(now) is not int
            or not now < payload["exp"] <= now + LIFETIME_SECONDS
            or payload["aud"] != audience
            or payload["capability"] != capability
            or payload["args_digest"] != arguments_digest(arguments)
        ):
            raise ValueError("assertion_binding")
        if claim_call_id(UUID(payload["call_id"]), payload["exp"]) is not True:
            raise ValueError("assertion_replay")
        return payload
    except (ValueError, TypeError, KeyError, AttributeError, InvalidSignature):
        raise BrokerRefused(Reason.AUTHENTICATION) from None
