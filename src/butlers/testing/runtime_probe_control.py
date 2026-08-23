"""Synthetic runtime-probe control key fixtures for tests.

Production never generates deployment key material --- the operator provisions
it outside the application (REQ-core-credentials-002).  Tests still need
*something* to sign with, so this module builds throwaway Ed25519 keys and the
two approved documents around them, and writes them into a caller-owned
directory (normally ``tmp_path``) at the modes the loader demands.

Nothing here is importable into a production path: it lives beside
``butlers.testing.migration`` for the same reason, so every test tree can share
one definition of "an obviously synthetic key" instead of hand-rolling one.

The generated material never leaves the calling test: it is returned, never
logged, printed, or written anywhere but the fixture files.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519

from butlers.core.runtime_probe_control.keys import (
    ALGORITHM,
    SIGNER_FILE_MODE,
    format_utc_second,
)

#: Mode for the shared keyring fixture: non-secret, but not group/world writable.
KEYRING_FILE_MODE = 0o444

#: Default retirement overlap, comfortably inside the accepted 70s..5m bound.
DEFAULT_RETIREMENT_OVERLAP = timedelta(seconds=120)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def synthetic_keypair() -> tuple[bytes, bytes]:
    """A throwaway Ed25519 keypair as ``(seed, raw public key)``."""
    seed = os.urandom(32)
    return seed, ed25519.Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()


def signer_document(
    seed: bytes,
    *,
    kid: str = "probe-test-current",
    sign_from: datetime | None = None,
    sign_until: datetime | None = None,
) -> dict[str, Any]:
    """The private signer document for ``seed``."""
    return {
        "version": 1,
        "alg": ALGORITHM,
        "kid": kid,
        "private_key_b64u": _b64u(seed),
        "sign_from": format_utc_second(sign_from or datetime.now(UTC) - timedelta(days=1)),
        "sign_until": None if sign_until is None else format_utc_second(sign_until),
    }


def current_entry(
    public_key: bytes,
    *,
    kid: str = "probe-test-current",
    sign_from: datetime | None = None,
) -> dict[str, Any]:
    """The keyring's one current entry."""
    return {
        "alg": ALGORITHM,
        "kid": kid,
        "public_key_b64u": _b64u(public_key),
        "sign_from": format_utc_second(sign_from or datetime.now(UTC) - timedelta(days=1)),
    }


def retiring_entry(
    public_key: bytes,
    *,
    kid: str = "probe-test-retiring",
    sign_from: datetime,
    sign_until: datetime,
    overlap: timedelta = DEFAULT_RETIREMENT_OVERLAP,
) -> dict[str, Any]:
    """A retiring keyring entry bounded at ``sign_until`` plus ``overlap``."""
    return {
        "alg": ALGORITHM,
        "kid": kid,
        "public_key_b64u": _b64u(public_key),
        "sign_from": format_utc_second(sign_from),
        "sign_until": format_utc_second(sign_until),
        "accept_until": format_utc_second(sign_until + overlap),
    }


def keyring_document(
    current: dict[str, Any],
    retiring: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The shared non-secret keyring document."""
    return {"version": 1, "current": current, "retiring": list(retiring or [])}


def write_document(path: Path, document: Any, *, mode: int) -> Path:
    """Write ``document`` as strict JSON at ``mode``, replacing any existing file."""
    path.unlink(missing_ok=True)
    path.write_bytes(json.dumps(document).encode("utf-8"))
    path.chmod(mode)
    return path


def write_key_fixture(
    directory: Path,
    *,
    kid: str = "probe-test-current",
    sign_from: datetime | None = None,
) -> tuple[Path, Path]:
    """Write a matched signer/keyring pair into ``directory``.

    Returns ``(signer_path, keyring_path)``.  The seed is discarded here on
    purpose: a test that needs it should build the documents explicitly.
    """
    seed, public_key = synthetic_keypair()
    signer_path = write_document(
        directory / "runtime_probe_control_signing_key",
        signer_document(seed, kid=kid, sign_from=sign_from),
        mode=SIGNER_FILE_MODE,
    )
    keyring_path = write_document(
        directory / "runtime_probe_control_verifiers",
        keyring_document(current_entry(public_key, kid=kid, sign_from=sign_from)),
        mode=KEYRING_FILE_MODE,
    )
    return signer_path, keyring_path
