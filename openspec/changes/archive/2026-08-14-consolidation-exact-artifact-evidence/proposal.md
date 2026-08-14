## Why

Consolidation currently creates a `derived_from` link from every emitted fact
or rule to every episode in its claimed group. That records batch membership,
not the evidence for an individual artifact, and malformed model output can
leave unrelated durable artifacts persisted before the group is rejected.

## What Changes

- Surface each claimed episode's UUID in the consolidation prompt and require
  every emitted fact or rule to name its supporting `evidence_episode_ids`.
- Treat absent, empty, malformed, duplicate, or out-of-group evidence as a
  group-level consolidation failure before any artifact is persisted.
- Persist each fact or rule and only its validated `derived_from` evidence
  links in one transaction, so a link-write failure cannot leave an
  unproven artifact behind.
- Preserve the existing persisted-target lookup and stale-supersession
  behavior for updated facts, along with the existing episode tombstone and
  provenance-reader contracts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-memory`: consolidation artifacts require exact, validated source
  episode evidence and atomic artifact/link persistence.

## Impact

Affected code is limited to the memory consolidation prompt, parser,
executor, and their focused tests. This change adds no dependency, migration,
dashboard/API surface, retention/backfill operation, cross-schema access, or
owner-gate behavior.
