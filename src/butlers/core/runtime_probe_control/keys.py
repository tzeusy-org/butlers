"""Strict parsers and immutable startup snapshots for runtime-probe control keys.

REQ-core-credentials-002 fixes the deployment representation *before* any
signer or verifier is mounted: the operator provisions the material outside the
application, and the application never generates, reconstructs, or persists it.
This module owns only that representation --- reading two files, proving they
match the approved schema, and freezing the result for the life of the process.

Two documents exist, and only Dashboard ever sees both:

``/run/secrets/runtime_probe_control_signing_key``
    The private Ed25519 signer.  Dashboard API (including its registered
    verification scheduler) mounts it; all-butlers, Switchboard, connectors,
    and every runtime child must not.

``/run/secrets/runtime_probe_control_verifiers``
    The non-secret keyring.  Dashboard and all-butlers mount the same source
    read-only, so Dashboard can prove its configured signer is the key
    Switchboard will actually accept.

Neither file has an environment-value, ``CredentialStore``, database, or
generic-Secrets fallback.  A missing, unreadable, malformed, algorithm-
mismatched, permission-unsafe, or mismatched document makes runtime-probe
control *unavailable* --- it never degrades to an unsigned or shared-bearer
command, and it never takes unrelated Dashboard or daemon behaviour down.

Snapshots are immutable and are re-read only when the owning process restarts;
rotation is therefore restart-driven by construction (see
``docs/operations/runtime-probe-control-keys.md``).

Nothing here is wired into a production mount, endpoint, or client: this leaf
lands the representation inert.  Mount activation belongs to bu-0uqgo.11.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixed contract constants
# ---------------------------------------------------------------------------

#: The one audience a runtime-probe control capability may claim.
CONTROL_AUDIENCE: Final = "switchboard.runtime_probe_control.v1"

#: Dashboard-only private signer.  Deployment-secret mount, never a DB row.
SIGNER_PATH: Final = Path("/run/secrets/runtime_probe_control_signing_key")

#: Shared non-secret keyring, mounted read-only by Dashboard and all-butlers.
VERIFIER_KEYRING_PATH: Final = Path("/run/secrets/runtime_probe_control_verifiers")

#: The generic Secrets API must refuse this name instead of shadowing the mount.
RESERVED_SIGNING_KEY_SECRET_NAME: Final = "RUNTIME_PROBE_CONTROL_SIGNING_KEY"

#: The only accepted document version.
DOCUMENT_VERSION: Final = 1

#: The only accepted signature algorithm.  ``none``, symmetric, and every other
#: asymmetric algorithm are rejected at parse time, not at verification time.
ALGORITHM: Final = "EdDSA"

KEY_ID_PATTERN: Final = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")

#: Raw Ed25519 seed / public values, unpadded base64url.  32 bytes is exactly
#: 43 base64url characters with no padding.
_KEY_B64U_PATTERN: Final = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")
ED25519_KEY_BYTES: Final = 32

#: UTC RFC 3339 at second resolution: no fraction, no numeric offset, no ``z``.
_TIMESTAMP_PATTERN: Final = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"

#: The private signer is a read-only regular file owned by its process.
SIGNER_FILE_MODE: Final = 0o400

#: Maximum accepted capability lifetime and the one-sided clock-skew allowance.
MAX_CAPABILITY_LIFETIME: Final = timedelta(seconds=60)
CLOCK_SKEW: Final = timedelta(seconds=5)

#: ``accept_until - sign_until`` bounds for the retiring key.  The lower bound
#: is the 60-second maximum lifetime plus both five-second skew allowances, so
#: every capability issued before cutover can finish; the upper bound stops an
#: operator who misses the removal restart from extending acceptance forever.
MIN_RETIREMENT_OVERLAP: Final = timedelta(seconds=70)
MAX_RETIREMENT_OVERLAP: Final = timedelta(minutes=5)

_SIGNER_FIELDS: Final = frozenset(
    {"version", "alg", "kid", "private_key_b64u", "sign_from", "sign_until"}
)
_KEYRING_FIELDS: Final = frozenset({"version", "current", "retiring"})
_CURRENT_ENTRY_FIELDS: Final = frozenset({"alg", "kid", "public_key_b64u", "sign_from"})
_RETIRING_ENTRY_FIELDS: Final = frozenset(
    {"alg", "kid", "public_key_b64u", "sign_from", "sign_until", "accept_until"}
)


class RuntimeProbeControlKeyError(ValueError):
    """A key document is unusable.

    Every message raised from this module is a fixed string chosen from the
    code below.  No parsed value --- key material, key ID, timestamp, or file
    content --- is ever interpolated into it, because these messages reach logs
    and operator diagnostics.
    """


# ---------------------------------------------------------------------------
# Strict JSON scalars
# ---------------------------------------------------------------------------


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Fail on a repeated field instead of silently keeping the last one."""
    document: dict[str, Any] = {}
    for field, value in pairs:
        if field in document:
            raise RuntimeProbeControlKeyError("key document repeats a field")
        document[field] = value
    return document


def _reject_json_constant(_token: str) -> Any:
    raise RuntimeProbeControlKeyError("key document contains a non-finite number")


def _load_strict_json(raw: bytes, *, exact_fields: frozenset[str]) -> dict[str, Any]:
    """Decode strict UTF-8 JSON with exact top-level fields and no duplicates."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeProbeControlKeyError("key document is not valid UTF-8") from exc
    try:
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_fields,
            parse_constant=_reject_json_constant,
        )
    except RuntimeProbeControlKeyError:
        raise
    except ValueError as exc:
        raise RuntimeProbeControlKeyError("key document is not valid JSON") from exc
    return _exact_object(document, exact_fields)


def _exact_object(value: Any, exact_fields: frozenset[str]) -> dict[str, Any]:
    """Require a JSON object whose field set is exactly ``exact_fields``."""
    if not isinstance(value, dict):
        raise RuntimeProbeControlKeyError("key document member is not an object")
    present = frozenset(value)
    if present != exact_fields:
        raise RuntimeProbeControlKeyError("key document has unknown or missing fields")
    return value


def _exact_version(value: Any) -> int:
    # ``True`` is an ``int`` in Python and would otherwise pass ``== 1``.
    if type(value) is not int or value != DOCUMENT_VERSION:
        raise RuntimeProbeControlKeyError("key document version is not supported")
    return value


def _exact_algorithm(value: Any) -> str:
    if value != ALGORITHM or not isinstance(value, str):
        raise RuntimeProbeControlKeyError("key document algorithm is not EdDSA")
    return value


def _key_id(value: Any) -> str:
    if not isinstance(value, str) or KEY_ID_PATTERN.match(value) is None:
        raise RuntimeProbeControlKeyError("key id does not match the approved grammar")
    return value


def _raw_ed25519_bytes(value: Any) -> bytes:
    """Decode canonical unpadded base64url into exactly 32 raw bytes."""
    if not isinstance(value, str) or _KEY_B64U_PATTERN.match(value) is None:
        raise RuntimeProbeControlKeyError("key material is not unpadded base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=")
    except (ValueError, TypeError) as exc:
        raise RuntimeProbeControlKeyError("key material is not unpadded base64url") from exc
    if len(decoded) != ED25519_KEY_BYTES:
        raise RuntimeProbeControlKeyError("key material is not 32 raw bytes")
    # A 43-character encoding carries four spare low bits; reject any
    # non-canonical spelling of the same 32 bytes.
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise RuntimeProbeControlKeyError("key material is not canonically encoded")
    return decoded


def _utc_second(value: Any) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_PATTERN.match(value) is None:
        raise RuntimeProbeControlKeyError("timestamp is not UTC RFC 3339 seconds")
    try:
        parsed = datetime.strptime(value, _TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise RuntimeProbeControlKeyError("timestamp is not a real UTC instant") from exc
    return parsed.replace(tzinfo=UTC)


def format_utc_second(value: datetime) -> str:
    """Render ``value`` in the one accepted timestamp spelling."""
    return value.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)


# ---------------------------------------------------------------------------
# Parsed representations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifierKey:
    """One entry of the shared non-secret keyring.

    ``sign_until``/``accept_until`` are set only on the retiring entry; the
    current entry has neither bound and stays issuable from ``sign_from``.
    """

    kid: str
    public_key: bytes
    sign_from: datetime
    sign_until: datetime | None = None
    accept_until: datetime | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial redaction
        return "VerifierKey(<redacted>)"

    @property
    def is_retiring(self) -> bool:
        return self.sign_until is not None

    def may_issue_at(self, now: datetime) -> bool:
        """Whether a capability may be *issued* under this entry at ``now``.

        The current entry becomes issuable at ``sign_from``.  The retiring
        entry stays issuable through ``sign_until`` inclusive --- ``iat ==
        sign_until`` is permitted with no cutover-skew extension --- and never
        past ``accept_until``.
        """
        second = _floor_to_second(now)
        if second < self.sign_from:
            return False
        if self.sign_until is not None and second > self.sign_until:
            return False
        return not (self.accept_until is not None and second > self.accept_until)

    def may_accept_at(self, now: datetime) -> bool:
        """Whether Switchboard may still *verify* against this entry at ``now``.

        Unlike issuance this ignores ``sign_until``: a capability issued at or
        before cutover must remain verifiable through its lifetime.  The
        retiring entry dies unconditionally at ``accept_until``.
        """
        second = _floor_to_second(now)
        return not (self.accept_until is not None and second > self.accept_until)


@dataclass(frozen=True, slots=True)
class VerifierKeyring:
    """The immutable ``version: 1`` keyring: one current, zero or one retiring."""

    current: VerifierKey
    retiring: VerifierKey | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial redaction
        return "VerifierKeyring(<redacted>)"

    def entry_for(self, kid: str) -> VerifierKey | None:
        """Resolve ``kid`` from this snapshot only --- never a remote lookup."""
        if kid == self.current.kid:
            return self.current
        if self.retiring is not None and kid == self.retiring.kid:
            return self.retiring
        return None

    def issuable_entry(self, kid: str, now: datetime) -> VerifierKey | None:
        """The entry that may back a *new* capability for ``kid`` at ``now``."""
        entry = self.entry_for(kid)
        if entry is None or not entry.may_issue_at(now):
            return None
        return entry

    def acceptable_entry(self, kid: str, now: datetime) -> VerifierKey | None:
        """The entry that may still *verify* a capability for ``kid`` at ``now``."""
        entry = self.entry_for(kid)
        if entry is None or not entry.may_accept_at(now):
            return None
        return entry


@dataclass(frozen=True, slots=True)
class SignerKey:
    """The Dashboard-only private signer, with its public half derived locally.

    ``public_key`` is derived from the seed rather than read from the document:
    that is what lets startup prove the configured signer really is the key
    Switchboard will accept, instead of trusting a self-asserted pairing.
    """

    kid: str
    private_key: ed25519.Ed25519PrivateKey
    public_key: bytes
    sign_from: datetime
    sign_until: datetime | None

    def __repr__(self) -> str:  # pragma: no cover - trivial redaction
        return "SignerKey(<redacted>)"

    @property
    def is_retiring(self) -> bool:
        return self.sign_until is not None

    def may_issue_at(self, now: datetime) -> bool:
        """A current signer signs from ``sign_from``; a retiring one stops at
        ``sign_until`` inclusive."""
        second = _floor_to_second(now)
        if second < self.sign_from:
            return False
        return not (self.sign_until is not None and second > self.sign_until)


def _floor_to_second(value: datetime) -> datetime:
    """Compare on integer seconds, matching the NumericDate claim resolution."""
    if value.tzinfo is None:
        raise RuntimeProbeControlKeyError("time comparison requires an aware instant")
    return value.astimezone(UTC).replace(microsecond=0)


# ---------------------------------------------------------------------------
# Document parsers
# ---------------------------------------------------------------------------


def parse_signer_document(raw: bytes) -> SignerKey:
    """Parse the private signer document, deriving its public key from the seed."""
    document = _load_strict_json(raw, exact_fields=_SIGNER_FIELDS)
    _exact_version(document["version"])
    _exact_algorithm(document["alg"])
    kid = _key_id(document["kid"])
    seed = _raw_ed25519_bytes(document["private_key_b64u"])
    sign_from = _utc_second(document["sign_from"])

    raw_sign_until = document["sign_until"]
    if raw_sign_until is None:
        sign_until = None
    else:
        sign_until = _utc_second(raw_sign_until)
        if sign_from >= sign_until:
            raise RuntimeProbeControlKeyError("retiring signer must sign before its bound")

    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    public_key = private_key.public_key().public_bytes_raw()
    return SignerKey(
        kid=kid,
        private_key=private_key,
        public_key=public_key,
        sign_from=sign_from,
        sign_until=sign_until,
    )


def _parse_current_entry(value: Any) -> VerifierKey:
    entry = _exact_object(value, _CURRENT_ENTRY_FIELDS)
    _exact_algorithm(entry["alg"])
    return VerifierKey(
        kid=_key_id(entry["kid"]),
        public_key=_raw_ed25519_bytes(entry["public_key_b64u"]),
        sign_from=_utc_second(entry["sign_from"]),
    )


def _parse_retiring_entry(value: Any) -> VerifierKey:
    entry = _exact_object(value, _RETIRING_ENTRY_FIELDS)
    _exact_algorithm(entry["alg"])
    sign_from = _utc_second(entry["sign_from"])
    sign_until = _utc_second(entry["sign_until"])
    accept_until = _utc_second(entry["accept_until"])
    if sign_from >= sign_until:
        raise RuntimeProbeControlKeyError("retiring entry must sign before its bound")
    overlap = accept_until - sign_until
    if overlap < MIN_RETIREMENT_OVERLAP or overlap > MAX_RETIREMENT_OVERLAP:
        raise RuntimeProbeControlKeyError("retiring overlap is outside 70s..5m")
    return VerifierKey(
        kid=_key_id(entry["kid"]),
        public_key=_raw_ed25519_bytes(entry["public_key_b64u"]),
        sign_from=sign_from,
        sign_until=sign_until,
        accept_until=accept_until,
    )


def parse_verifier_keyring_document(raw: bytes) -> VerifierKeyring:
    """Parse the shared keyring: exactly one current entry, zero or one retiring.

    ``retiring`` is always present as an array so "no rotation in flight" is
    written down explicitly (``[]``) rather than inferred from an absent field.
    """
    document = _load_strict_json(raw, exact_fields=_KEYRING_FIELDS)
    _exact_version(document["version"])
    current = _parse_current_entry(document["current"])

    raw_retiring = document["retiring"]
    if not isinstance(raw_retiring, list) or len(raw_retiring) > 1:
        raise RuntimeProbeControlKeyError("keyring must carry zero or one retiring entry")
    if not raw_retiring:
        return VerifierKeyring(current=current)

    retiring = _parse_retiring_entry(raw_retiring[0])
    if retiring.kid == current.kid:
        raise RuntimeProbeControlKeyError("keyring entries must use distinct key ids")
    if retiring.public_key == current.public_key:
        raise RuntimeProbeControlKeyError("keyring entries must use distinct public keys")
    if current.sign_from != retiring.sign_until:
        raise RuntimeProbeControlKeyError("keyring cutover instants disagree")
    return VerifierKeyring(current=current, retiring=retiring)


def match_signer_to_keyring(signer: SignerKey, keyring: VerifierKeyring) -> VerifierKey:
    """Prove the configured signer is exactly one keyring entry.

    A current signer (``sign_until = null``) must match the current entry's key
    id, derived public key, and ``sign_from``.  A retiring signer must match
    every retiring field through ``sign_until``.  Anything else --- including a
    retiring signer offered while the keyring carries no retiring entry --- is
    a mismatch, and a mismatch fails closed.
    """
    if signer.sign_until is None:
        entry = keyring.current
        if (
            signer.kid != entry.kid
            or signer.public_key != entry.public_key
            or signer.sign_from != entry.sign_from
        ):
            raise RuntimeProbeControlKeyError("signer does not match the current verifier")
        return entry

    entry = keyring.retiring
    if entry is None:
        raise RuntimeProbeControlKeyError("retiring signer has no retiring verifier")
    if (
        signer.kid != entry.kid
        or signer.public_key != entry.public_key
        or signer.sign_from != entry.sign_from
        or signer.sign_until != entry.sign_until
    ):
        raise RuntimeProbeControlKeyError("signer does not match the retiring verifier")
    return entry


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------

_MAX_DOCUMENT_BYTES: Final = 8192


def _read_regular_file(path: Path, *, require_mode: int | None) -> bytes:
    """Read a bounded regular file, refusing a symlink or unsafe final mode.

    ``require_mode`` is checked with ``fstat`` on the descriptor that is
    actually read, so the mode belongs to the bytes returned.  It proves the
    *file property* the deployment contract asks for; it is emphatically not a
    boundary against a process running as the same identity, which can open the
    file whatever its mode says.  Isolating same-identity children is the
    launcher's job (task 3.6b), not this check's.
    """
    try:
        # ``O_NONBLOCK`` is inert for a regular file and keeps a FIFO mount
        # from parking startup on ``open`` before the regular-file check runs.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as exc:
        raise RuntimeProbeControlKeyError("key file is missing or unreadable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeProbeControlKeyError("key file is not a regular file")
        mode = stat.S_IMODE(metadata.st_mode)
        if require_mode is None:
            # The shared keyring is non-secret; it only has to be un-writable
            # by anyone but its owner.
            if mode & 0o022:
                raise RuntimeProbeControlKeyError("key file is group or world writable")
        else:
            if mode != require_mode:
                raise RuntimeProbeControlKeyError("key file mode is not owner-read-only")
            if metadata.st_uid != os.geteuid():
                raise RuntimeProbeControlKeyError("key file is not owned by this process")
        if metadata.st_size > _MAX_DOCUMENT_BYTES:
            raise RuntimeProbeControlKeyError("key file is larger than the accepted bound")
        return os.read(descriptor, _MAX_DOCUMENT_BYTES + 1)
    except OSError as exc:
        raise RuntimeProbeControlKeyError("key file is missing or unreadable") from exc
    finally:
        os.close(descriptor)


def load_signer(path: Path = SIGNER_PATH) -> SignerKey:
    """Load the private signer from its deployment-secret mount."""
    return parse_signer_document(_read_regular_file(path, require_mode=SIGNER_FILE_MODE))


def load_verifier_keyring(path: Path = VERIFIER_KEYRING_PATH) -> VerifierKeyring:
    """Load the shared non-secret keyring."""
    return parse_verifier_keyring_document(_read_regular_file(path, require_mode=None))


# ---------------------------------------------------------------------------
# Immutable process snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignerSnapshot:
    """Dashboard's frozen view: its signer plus the entry it must match.

    ``unavailable_reason`` carries the fixed diagnostic string, never key
    material.  When it is set the signed client is unavailable and signs
    nothing; nothing else about Dashboard changes.
    """

    signer: SignerKey | None = None
    keyring: VerifierKeyring | None = None
    matched: VerifierKey | None = None
    unavailable_reason: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial redaction
        return f"SignerSnapshot(available={self.available}, reason={self.unavailable_reason!r})"

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None

    def may_issue_at(self, now: datetime) -> bool:
        """Signing is allowed only when signer and matched verifier both agree."""
        if self.signer is None or self.matched is None or not self.available:
            return False
        return self.signer.may_issue_at(now) and self.matched.may_issue_at(now)


@dataclass(frozen=True, slots=True)
class VerifierSnapshot:
    """The all-butlers / Switchboard frozen view: keyring only, no private half."""

    keyring: VerifierKeyring | None = None
    unavailable_reason: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial redaction
        return f"VerifierSnapshot(available={self.available}, reason={self.unavailable_reason!r})"

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None

    def is_ready_for(self, kid: str, now: datetime) -> bool:
        """Back the readiness probe without disclosing which entry matched."""
        if self.keyring is None or not self.available:
            return False
        return self.keyring.issuable_entry(kid, now) is not None


def read_signer_snapshot(
    *,
    signer_path: Path = SIGNER_PATH,
    keyring_path: Path = VERIFIER_KEYRING_PATH,
) -> SignerSnapshot:
    """Validate both documents once and freeze the result.

    Fail-closed but contained: every failure returns an unavailable snapshot
    with a safe reason instead of raising into unrelated startup.
    """
    try:
        keyring = load_verifier_keyring(keyring_path)
        signer = load_signer(signer_path)
        matched = match_signer_to_keyring(signer, keyring)
    except RuntimeProbeControlKeyError as exc:
        logger.warning("Runtime-probe control signing unavailable: %s", exc)
        return SignerSnapshot(unavailable_reason=str(exc))
    return SignerSnapshot(signer=signer, keyring=keyring, matched=matched)


def read_verifier_snapshot(*, keyring_path: Path = VERIFIER_KEYRING_PATH) -> VerifierSnapshot:
    """Validate the keyring once and freeze the result."""
    try:
        keyring = load_verifier_keyring(keyring_path)
    except RuntimeProbeControlKeyError as exc:
        logger.warning("Runtime-probe control verification unavailable: %s", exc)
        return VerifierSnapshot(unavailable_reason=str(exc))
    return VerifierSnapshot(keyring=keyring)


_signer_snapshot: SignerSnapshot | None = None
_verifier_snapshot: VerifierSnapshot | None = None


def signer_snapshot() -> SignerSnapshot:
    """The process-wide signer snapshot, read at most once.

    Rotation is restart-driven: replacing the file under a running process
    changes nothing, which is precisely the property the readiness-gated
    full-stack restart depends on.
    """
    global _signer_snapshot
    if _signer_snapshot is None:
        # Read the paths as globals rather than defaults so a test can point
        # the process at fixtures; production never rebinds them.
        _signer_snapshot = read_signer_snapshot(
            signer_path=SIGNER_PATH, keyring_path=VERIFIER_KEYRING_PATH
        )
    return _signer_snapshot


def verifier_snapshot() -> VerifierSnapshot:
    """The process-wide keyring snapshot, read at most once."""
    global _verifier_snapshot
    if _verifier_snapshot is None:
        _verifier_snapshot = read_verifier_snapshot(keyring_path=VERIFIER_KEYRING_PATH)
    return _verifier_snapshot


def _reset_snapshots_for_tests() -> None:
    """Drop the cached snapshots.  Tests only --- production reloads by restart."""
    global _signer_snapshot, _verifier_snapshot
    _signer_snapshot = None
    _verifier_snapshot = None
