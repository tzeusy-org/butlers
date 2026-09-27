## Context

`reconcile_snapshot` already treats a complete snapshot as authoritative for a
source, so a retired v1 fingerprint exits when it is absent from the first v2
snapshot. That remains the lifecycle authority. The missing information is why
the row resolved and which explicitly declared v2 episode superseded it.

## Goals / Non-Goals

**Goals:**

- Preserve an identity-payload version with condition evidence.
- Record a durable predecessor/successor correlation only when the producer
  explicitly names the prior fingerprint and advances its version.
- Make the existing operator panel state that terminal cause without adding a
  generic dashboard redesign.

**Non-Goals:**

- Recompute, migrate, or rewrite historic fingerprints.
- Infer a predecessor from opaque hashes or source-wide snapshot membership.
- Add a new lifecycle state, change ordinary recovery, or resolve partial or
  failed snapshots.
- Backfill historic condition rows or require all producers to change at once.

## Decisions

### Version and correlation are condition metadata

The existing JSONB evidence field stores `identity_payload.version`, with the
resolved predecessor retaining `resolution_reason` and successor reference;
the first successor retains the reciprocal predecessor reference. This is
durable, transactional with reconciliation, additive, and avoids changing the
shared table shape or rewriting historic rows.

An explicit `predecessor_fingerprint` is required for correlation. Hashes are
not reversible and matching every old source row to every v2 observation would
produce false lineage. A higher version without that explicit link remains a
normal complete-snapshot absence resolution.

### Resolution remains source-wide and snapshot-authoritative

The existing source-wide absence pass continues to resolve all missing active
episodes. Provenance decorates only the explicitly linked predecessor after a
strictly higher version is observed in the same complete snapshot; it does not
change state transitions, fingerprint identity, escalation, or completeness
rules.

### Surface the terminal reason in the existing panel

The Standing Conditions row reads the structured metadata for infrastructure
entries. A superseded row says `Superseded by identity version vN`; every other
resolved row retains its established recovery timestamp and duration copy.

## Risks / Trade-offs

- [A producer omits explicit lineage on a version bump] → The lifecycle still
  exits safely, but reports ordinary snapshot absence rather than inventing a
  false correlation.
- [A later v2 confirmation overwrites evidence] → Preserve the established
  predecessor object while refreshing the identity version metadata.
- [Older rows predate version metadata] → Leave them untouched; they resolve
  normally unless a producer has already supplied durable version evidence.
