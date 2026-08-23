"""Inert trust representation for the private runtime-probe control plane.

This package carries the trust representation --- strict deployment-key
parsing, immutable startup snapshots, durable replay receipts, and the narrow
verification-persistence surface --- plus the control plane built on it: the
capability, the coordinator, Switchboard's private route, and the dedicated
Dashboard/Scheduler client.

It deliberately ships no production key mount, so the deployed route is
fail-closed unavailable and the client signs nothing; the whole path is
exercised only against isolated fixture keys.  Cutting Test, verify-all, and
the scheduled sweep over to it belongs to a later leaf of the same change.

See ``docs/operations/runtime-probe-control-keys.md`` for the operator contract.
"""

from __future__ import annotations

from butlers.core.runtime_probe_control.capability import (
    CALLERS,
    DEFAULT_LIFETIME,
    NONCE_BYTES,
    CapabilityRejected,
    VerifiedCapability,
    sign_capability,
    verify_capability,
)
from butlers.core.runtime_probe_control.client import RuntimeProbeControlClient
from butlers.core.runtime_probe_control.coordinator import (
    GLOBAL_CONCURRENCY,
    HTTP_STATUS,
    PER_ENTRY_CONCURRENCY,
    PROBE_TIMEOUT_S,
    ProbeResult,
    ProbeStatus,
    RuntimeProbeCoordinator,
)
from butlers.core.runtime_probe_control.endpoint import (
    CONTROL_PATH,
    READINESS_KEY_ID_PARAM,
    READINESS_PATH,
    READY_STATUS,
    build_runtime_probe_control_route,
    build_runtime_probe_readiness_route,
)
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
    "CALLERS",
    "CLOCK_SKEW",
    "CONTROL_AUDIENCE",
    "CONTROL_PATH",
    "CapabilityRejected",
    "DEFAULT_LIFETIME",
    "GLOBAL_CONCURRENCY",
    "HTTP_STATUS",
    "MAX_CAPABILITY_LIFETIME",
    "MAX_RETIREMENT_OVERLAP",
    "MIN_RETIREMENT_OVERLAP",
    "NONCE_BYTES",
    "PER_ENTRY_CONCURRENCY",
    "PROBE_TIMEOUT_S",
    "ProbeResult",
    "ProbeStatus",
    "READINESS_KEY_ID_PARAM",
    "READINESS_PATH",
    "READY_STATUS",
    "RECEIPTS_TABLE",
    "RECEIPT_RETENTION_SKEW",
    "RESERVED_SIGNING_KEY_SECRET_NAME",
    "RuntimeProbeControlClient",
    "RuntimeProbeControlKeyError",
    "RuntimeProbeControlReceipts",
    "RuntimeProbeCoordinator",
    "RuntimeProbeVerificationPersistence",
    "SIGNER_PATH",
    "SignerKey",
    "SignerSnapshot",
    "VERIFIER_KEYRING_PATH",
    "VERIFY_ERROR_TRUNCATE_LEN",
    "VerifiedCapability",
    "VerifierKey",
    "VerifierKeyring",
    "VerifierSnapshot",
    "build_runtime_probe_control_route",
    "build_runtime_probe_readiness_route",
    "load_signer",
    "load_verifier_keyring",
    "match_signer_to_keyring",
    "nonce_digest",
    "parse_signer_document",
    "parse_verifier_keyring_document",
    "read_signer_snapshot",
    "read_verifier_snapshot",
    "sign_capability",
    "signer_snapshot",
    "verifier_snapshot",
    "verify_capability",
]
