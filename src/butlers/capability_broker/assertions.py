"""D7 compact JWS, shared by issuer and the separately owned domain verifier.

Header: exactly alg=EdDSA and kid. Payload: exactly the D7 fields. Both use
RFC8785 UTF-8, encoded unpadded base64url; Ed25519 signs ASCII header.payload.
No keys are loaded here. The verifier must supply current/next public keys and
an atomic replay claim that also enforces its restart-window policy.
"""

from __future__ import annotations

import base64
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_arguments

from .contracts import BrokerRefused, Reason, Reservation

FIELDS = frozenset(
    {
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
    }
)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("noncanonical_base64")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _encode(raw) != value:
        raise ValueError("noncanonical_base64")
    return raw


def _payload(value: dict) -> None:
    if set(value) != FIELDS:
        raise ValueError("assertion_shape")
    if type(value["depth"]) is not int or value["depth"] not in (0, 1):
        raise ValueError("assertion_depth")
    if value["origin"] not in {"owner", "obligation", "third_party", "staffer"}:
        raise ValueError("assertion_origin")
    if type(value["exposed"]) is not bool or type(value["exp"]) is not int:
        raise ValueError("assertion_types")
    for key in ("aud", "capability"):
        if type(value[key]) is not str or not value[key]:
            raise ValueError("assertion_identity")
    if not isinstance(value["args_digest"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["args_digest"]
    ):
        raise ValueError("assertion_digest")
    for key in ("goal_id", "invocation_id", "call_id", "effect_id"):
        if key == "effect_id" and value[key] is None:
            continue
        if type(value[key]) is not str or str(UUID(value[key])) != value[key]:
            raise ValueError("assertion_uuid")
    if value["approval_ref"] is not None and (
        type(value["approval_ref"]) is not str
        or not value["approval_ref"]
        or value["effect_id"] is None
    ):
        raise ValueError("assertion_approval")


@dataclass(frozen=True, slots=True)
class AssertionSigner:
    kid: str
    private_key: Ed25519PrivateKey = field(repr=False)

    def __post_init__(self):
        if not self.kid or not isinstance(self.private_key, Ed25519PrivateKey):
            raise BrokerRefused(Reason.UNAVAILABLE)

    @classmethod
    def from_file(
        cls,
        *,
        enabled: bool = False,
        path: Path | None = None,
        kid: str | None = None,
        verification_keys: Mapping[str, Ed25519PublicKey] | None = None,
    ) -> AssertionSigner | None:
        """Read one immutable startup snapshot; never provision or activate keys.

        The trusted control-process constructor supplies a deployment file and
        the configured current/next public verifier keys. This is not a secret
        resolver, environment fallback, process-isolation check or live reload.
        Disabled construction returns before even inspecting those inputs.
        """
        if enabled is False:
            return None
        try:
            if (
                enabled is not True
                or not isinstance(path, Path)
                or not path.is_absolute()
                or type(kid) is not str
                or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", kid) is None
                or not isinstance(verification_keys, Mapping)
            ):
                raise ValueError("invalid_key_configuration")
            public = verification_keys.get(kid)
            if not isinstance(public, Ed25519PublicKey):
                raise ValueError("unrecognized_key")
            # O_NONBLOCK refuses FIFOs without waiting for a writer. fstat
            # checks the descriptor actually read, not a racy pathname probe.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            try:
                metadata = os.fstat(fd)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_IMODE(metadata.st_mode) != 0o400
                    or metadata.st_uid != os.geteuid()
                    or not 0 < metadata.st_size <= 8192
                ):
                    raise ValueError("unsafe_key_file")
                raw = bytearray()
                while len(raw) <= 8192:
                    chunk = os.read(fd, 8193 - len(raw))
                    if not chunk:
                        break
                    raw.extend(chunk)
                after = os.fstat(fd)
                stable = (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_uid",
                    "st_gid",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
                if len(raw) != metadata.st_size or any(
                    getattr(metadata, name) != getattr(after, name) for name in stable
                ):
                    raise ValueError("changed_key_file")
            finally:
                os.close(fd)
            key = serialization.load_pem_private_key(bytes(raw), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("wrong_key_algorithm")
            # Require exactly one canonical unencrypted PKCS8 PEM document:
            # parsers otherwise accept trailing or concatenated key material.
            canonical = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            if canonical != raw or key.public_key().public_bytes_raw() != public.public_bytes_raw():
                raise ValueError("key_mismatch")
            return cls(kid, key)
        except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
            # No path, key ID, parser detail or private bytes enter diagnostics.
            raise BrokerRefused(Reason.UNAVAILABLE) from None

    def sign(self, reservation: Reservation, *, issued_at: int, approval_ref: str | None = None):
        if type(issued_at) is not int or issued_at < 0:
            raise BrokerRefused(Reason.UNAVAILABLE)
        principal, capability = reservation.principal, reservation.capability
        payload = {
            "aud": capability.domain,
            "goal_id": str(principal.goal_id),
            "invocation_id": str(principal.invocation_id),
            "depth": principal.depth,
            "origin": principal.origin,
            "exposed": principal.exposed,
            "capability": capability.name,
            "args_digest": reservation.args_digest,
            "call_id": str(reservation.call_id),
            "effect_id": str(reservation.effect_id) if reservation.effect_id else None,
            "approval_ref": approval_ref,
            "exp": issued_at + 60,
        }
        _payload(payload)
        protected = _encode(canonical_bytes({"alg": "EdDSA", "kid": self.kid}))
        content = _encode(canonical_bytes(payload))
        signing_input = f"{protected}.{content}".encode("ascii")
        return f"{protected}.{content}.{_encode(self.private_key.sign(signing_input))}"


def verify_assertion(
    assertion: str,
    *,
    keys: Mapping[str, Ed25519PublicKey],
    audience: str,
    capability: str,
    arguments: dict,
    now: int,
    claim_call_id: Callable[[UUID, int], bool],
) -> dict:
    """Verify before coercion; atomically claim replay only after every check.

    Supplying a cache does not itself prove restart safety. Domain wiring must
    provide persistent claims or refuse assertions from the pre-start window.
    This pure boundary does not install ambient context or activate enforcement.
    """
    try:
        header_part, body_part, signature_part = assertion.split(".")
        header_raw, body_raw = _decode(header_part), _decode(body_part)
        header, payload = parse_arguments(header_raw), parse_arguments(body_raw)
        if (
            set(header) != {"alg", "kid"}
            or header["alg"] != "EdDSA"
            or type(header["kid"]) is not str
            or canonical_bytes(header) != header_raw
            or canonical_bytes(payload) != body_raw
        ):
            raise ValueError("assertion_header")
        key = keys.get(header["kid"])
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("unknown_key")
        key.verify(_decode(signature_part), f"{header_part}.{body_part}".encode("ascii"))
        _payload(payload)
        if (
            type(now) is not int
            or not now < payload["exp"] <= now + 60
            or payload["aud"] != audience
            or payload["capability"] != capability
            or payload["args_digest"] != arguments_digest(arguments)
        ):
            raise ValueError("assertion_binding")
        if claim_call_id(UUID(payload["call_id"]), payload["exp"]) is not True:
            raise ValueError("assertion_replay")
        return payload
    except (ValueError, TypeError, KeyError, InvalidSignature):
        raise BrokerRefused(Reason.AUTHENTICATION) from None
