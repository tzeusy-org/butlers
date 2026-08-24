"""Inert trust representation for the private runtime-probe control plane.

This package carries the trust representation --- strict deployment-key
parsing, immutable startup snapshots, durable replay receipts, and the narrow
verification-persistence surface --- plus the control plane built on it: the
capability, the coordinator, Switchboard's private route, and the dedicated
Dashboard/Scheduler client.

Since bu-0uqgo.11 it also carries the production mount and the activation
gate: the signer is mounted to the Dashboard alone, the verifier keyring to
every process that must check a capability, and Model Test, verify-all, and
the hourly sweep reach a runtime only through this plane.  Every signing path
goes through :func:`activated_signer_snapshot`, so an image that still holds a
dashboard-local probe signs nothing even with the mount present.

See ``docs/operations/runtime-probe-control-keys.md`` for the operator contract.
"""

from __future__ import annotations

from butlers.core.runtime_probe_control.activation import (
    DEFERRED_LOCAL_PROBE_MODULES,
    GUARDED_MODULES,
    LOCAL_PROBE_PRESENT_REASON,
    LOCAL_PROBE_SYMBOLS,
    SWITCHBOARD_CONTROL_PORT,
    activated_signer_snapshot,
    local_model_probe_callsites,
    probe_client,
    switchboard_control_base_url,
)
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
    "DEFERRED_LOCAL_PROBE_MODULES",
    "GLOBAL_CONCURRENCY",
    "GUARDED_MODULES",
    "HTTP_STATUS",
    "LOCAL_PROBE_PRESENT_REASON",
    "LOCAL_PROBE_SYMBOLS",
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
    "SWITCHBOARD_CONTROL_PORT",
    "SignerKey",
    "SignerSnapshot",
    "VERIFIER_KEYRING_PATH",
    "VERIFY_ERROR_TRUNCATE_LEN",
    "VerifiedCapability",
    "VerifierKey",
    "VerifierKeyring",
    "VerifierSnapshot",
    "activated_signer_snapshot",
    "build_runtime_probe_control_route",
    "build_runtime_probe_readiness_route",
    "load_signer",
    "local_model_probe_callsites",
    "load_verifier_keyring",
    "match_signer_to_keyring",
    "nonce_digest",
    "parse_signer_document",
    "parse_verifier_keyring_document",
    "probe_client",
    "read_signer_snapshot",
    "read_verifier_snapshot",
    "sign_capability",
    "signer_snapshot",
    "switchboard_control_base_url",
    "verifier_snapshot",
    "verify_capability",
]
