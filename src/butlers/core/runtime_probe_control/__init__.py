"""Inert trust representation for the private runtime-probe control plane.

This package carries the representation only --- strict deployment-key parsing,
immutable startup snapshots, durable replay receipts, and the narrow
verification-persistence surface.  It deliberately ships no production key
mount, control endpoint, signed client, or Test/verify caller; those belong to
later leaves of the same change.

See ``docs/operations/runtime-probe-control-keys.md`` for the operator contract.
"""

from __future__ import annotations

from butlers.core.runtime_probe_control.keys import (
    ALGORITHM,
    CLOCK_SKEW,
    CONTROL_AUDIENCE,
    MAX_CAPABILITY_LIFETIME,
    MAX_RETIREMENT_OVERLAP,
    MIN_RETIREMENT_OVERLAP,
    RESERVED_SIGNING_KEY_SECRET_NAME,
    SIGNER_PATH,
    VERIFIER_KEYRING_PATH,
    RuntimeProbeControlKeyError,
    SignerKey,
    SignerSnapshot,
    VerifierKey,
    VerifierKeyring,
    VerifierSnapshot,
    load_signer,
    load_verifier_keyring,
    match_signer_to_keyring,
    parse_signer_document,
    parse_verifier_keyring_document,
    read_signer_snapshot,
    read_verifier_snapshot,
    signer_snapshot,
    verifier_snapshot,
)
from butlers.core.runtime_probe_control.receipts import (
    RECEIPT_RETENTION_SKEW,
    RECEIPTS_TABLE,
    RuntimeProbeControlReceipts,
    nonce_digest,
)
from butlers.core.runtime_probe_control.verification import (
    VERIFY_ERROR_TRUNCATE_LEN,
    RuntimeProbeVerificationPersistence,
)

__all__ = [
    "ALGORITHM",
    "CLOCK_SKEW",
    "CONTROL_AUDIENCE",
    "MAX_CAPABILITY_LIFETIME",
    "MAX_RETIREMENT_OVERLAP",
    "MIN_RETIREMENT_OVERLAP",
    "RECEIPTS_TABLE",
    "RECEIPT_RETENTION_SKEW",
    "RESERVED_SIGNING_KEY_SECRET_NAME",
    "SIGNER_PATH",
    "VERIFIER_KEYRING_PATH",
    "VERIFY_ERROR_TRUNCATE_LEN",
    "RuntimeProbeControlKeyError",
    "RuntimeProbeControlReceipts",
    "RuntimeProbeVerificationPersistence",
    "SignerKey",
    "SignerSnapshot",
    "VerifierKey",
    "VerifierKeyring",
    "VerifierSnapshot",
    "load_signer",
    "load_verifier_keyring",
    "match_signer_to_keyring",
    "nonce_digest",
    "parse_signer_document",
    "parse_verifier_keyring_document",
    "read_signer_snapshot",
    "read_verifier_snapshot",
    "signer_snapshot",
    "verifier_snapshot",
]
