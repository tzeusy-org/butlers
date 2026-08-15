## Why

The current Codex authority path safely compares raw credential values while a
single process remains alive, but it deliberately cannot prove that a local
`auth.json` rotation found after a crash belongs to the shared authority that
launched it. It therefore restores the shared value conservatively and names
`bu-gg4fo` as the durable-provenance follow-up. A durable, non-secret fence is
needed so a later process can distinguish an eligible completion from an
unprovable local artifact without ever deriving authority from credential
contents, process identity, or timestamps.

## What Changes

- Add a single explicit Codex authority-generation model for the existing
  system-global `cli-auth/codex` credential. Each accepted owner replacement
  and each accepted runtime/device-auth successor receives a fresh opaque
  generation; the generation contains no credential-derived material.
- Bind every Codex subprocess-capable operation to one exact current generation
  through a durable, single-use launch-operation record. A completion may
  promote a successor or attach health only while that exact operation and
  generation remain current.
- Define deterministic precedence between owner/dashboard replacement,
  device-auth completion, runtime rotation, health updates, and revoke; a
  direct owner replacement serializes through the same authority and prevents
  an older operation from changing its replacement.
- Define fail-closed outcomes for missing, malformed, ambiguous, expired, or
  unavailable authority/operation evidence, plus crash, restart, and
  multi-daemon interleavings. Existing local files remain non-authoritative
  after an unprovable operation.
- Separate normal runtime preparation from the privileged first-device-auth
  bootstrap entry point, and install the protected NOLOGIN-owned database
  boundary through a privileged fixed installer before the normal migration
  may invoke it.
- Add additive rollout, retirement, and garbage-collection rules for the
  non-secret provenance records. The migration is intentionally irreversible;
  application rollback is fence-aware fail-closed operation, and any future
  schema removal requires a separately reviewed migration. No rollback
  reconstructs authority from local files.
- Preserve the existing owner-only Codex rotate `{fingerprint, value}`
  raw-value-once response and inventory/detail display fingerprint, while
  adding no credential reveal, generation, operation, lineage, capability,
  raw error, or process-identifying API field.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `core-credentials`: Codex authority resolution, replacement, rotation,
  health fencing, recovery, and redaction gain durable opaque generations and
  launch-operation provenance.
- `core-spawner`: Codex preflight, prewarm, subprocess launch, and finalization
  bind to an exact durable authority generation and fail closed on unprovable
  evidence through a full replacement of the canonical `Pre-Launch and
  Prewarm Codex Auth Synchronization` requirement.
- `core-daemon`: startup restoration and restart recovery use the durable
  system-global authority lineage rather than inferring a successor from a
  local runtime file.
- `dashboard-api`: dashboard CLI-auth save, reauthorization, probe, and revoke
  paths serialize through the same owner-authoritative generation boundary
  without expanding response payloads.
- `database-security`: the provenance tables and guarded operations retain
  least-privilege access and never make raw credential material or
  credential-derived identity queryable.

## Impact

The eventual implementation touches the core credential migration chain,
`CredentialStore`, Codex auth synchronization, the Codex adapter and its
prewarm paths, CLI-auth persistence/session callbacks, dashboard Secrets/API
handlers, lifecycle wiring, and focused migration/adapter/API/concurrency
tests. It also updates the credential-store, CLI-runtime-auth, daemon-lifecycle,
and spawner documentation. It adds no provider dependency, no public endpoint,
and no live operational action in this planning change.

This change composes after the merged implementation change
`harden-runtime-auth-and-breaker-attention`, whose still-active
`core-credentials` delta owns the initial replacement of `Live Codex
Device-Auth Reconciliation`. Implementation allocation must first confirm that
predecessor has been synced/archived or otherwise compose against its exact
canonical wording; this packet does not add a competing replacement of that
requirement.
