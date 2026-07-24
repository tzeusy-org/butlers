## Why

Routine owner-default notifications are now preserved as durable quiet-hours
holds, but the system has no trustworthy, recoverable way to release a held
morning cohort from an owner interaction. The owner selected a deliberately
narrow v1 authority: a durably accepted direct text DM from the canonical owner
through the Telegram bot. The release must remain safe through duplicate
updates, concurrent runs, daemon crashes, and an ambiguous Telegram send; DND
remains an absolute veto under the landed canonical cross-writer guard.

## What Changes

- Define a deterministic, Switchboard-coordinated wake-recovery protocol that
  begins only after a qualifying Telegram event and its acceptance provenance
  are durable.
- Define a window-scoped release run, participant and row fencing, and the
  MCP-only prepare, commit, abort, and release exchanges needed to compose one
  exact-target Telegram send without cross-schema queue reads.
- Preserve the existing durable queue's no-re-gating rule while excluding a
  run-reserved cohort from the normal scheduler pass; rows arriving after the
  cohort freeze stay for a later release.
- Preserve DND as an absolute veto that a direct DM never clears, the
  Health-owned policy-sleep exception, all-or-nothing retry/retention behavior,
  and crash/ambiguous-send recovery.
- Bind final DND admission and any complete-cohort post-prepare ordinary
  cancellation to the
  landed canonical DND-generation and durable cancellation-admission contracts
  without restating or changing either independent protocol.
- Require one stable end-to-end egress action key from the accepted event and
  window through Messenger admission and provider receipt reconciliation.
- Document the intentionally strict v1 exclusions: no Telegram user-client,
  group/channel/service/media event, HA/OwnTracks/location trigger, briefing,
  broker catch-up, cron/schedule change, generic context clear, or partial
  cohort release.

## Capabilities

### New Capabilities

- `owner-telegram-wake-recovery`: The strict owner-authorized recovery state
  machine, durable provenance, cohort fencing, RPC protocol, and verification
  contract.

### Modified Capabilities

- `core-notify`: Qualifying quiet-hours holds retain immutable admission and
  exact-target provenance required for a later fenced release.
- `time-aware-delivery`: The ordinary deferred-notification scheduler excludes
  a run-reserved cohort, does not re-gate or recalculate stored delivery
  decisions, and preserves late rows for a later run.
- `butler-switchboard`: Switchboard recognizes only the post-commit canonical
  owner Telegram-bot wake authority and coordinates trusted cross-origin RPCs.
- `butler-health`: Only Health's deterministic policy-sleep context may be
  superseded by a committed wake run; no generic context signal is cleared.
- `butler-messenger`: Messenger admits the one composed release with a stable
  end-to-end idempotency key and makes ambiguous provider outcomes recoverable.
- `database-security`: Runtime roles retain schema isolation; release
  coordination gets only narrowly authenticated MCP and shared-control ACLs.

## Impact

This is an OpenSpec-only planning change. It specifies future changes to the
Switchboard ingestion/recovery coordinator, origin-butler deferred queues,
Health policy-sleep handling, Messenger delivery admission, role grants, and
their tests. It makes no implementation, migration, live trigger,
notification, schedule, or runtime configuration change.

## Landed Contract Boundary

`canonical-dnd-generation-guard` (`bu-12iab`, RFC 0009's **Canonical DND
Generation Guard** and **DND snapshots and admission**) and
`durable-precommit-cancellation-admission` (`bu-qs702`, RFC 0009's
**Precommit cancellation admission**) are landed normative prerequisites for
this packet. Wake recovery SHALL consume their authenticated, schema-local
guard and cancellation boundaries; it SHALL NOT restate, alter, or substitute
their storage, packet fields, lock ordering, role authority, replay identity,
or internal state machines.

Only after every participant has supplied a compatible durable prepare result
for the complete current-fence cohort may an all-uncommitted
`ordinary_precommit_cancel` use the latter contract's same-fence, all-cohort
`cancel_admit` / `cancel_finalize` / `cancel_publish` path. A partial prepared
cohort remains protocol-bound and cannot enter that cancellation path. Until
Messenger has made its effective no-egress, DND-guarded admission and every
participant has durably finalized the same frozen cohort, no member is
scheduler-visible. Only the matching complete publication may return the exact
cohort together to ordinary `pending` through the prerequisite-defined
scheduler-return transition; no late row, subset, generic scheduler scan,
target re-resolution, or partial send is permitted. A changed, active, missing,
or unprovable DND generation
uses the referenced `rejected_blocked_dnd` path and the parent
`abort.v1(reason=blocked_dnd)` retained outcome, never a scheduler return.

The parent retains its accepted-event authority, owner/window fencing,
origin-local cohort boundary, all-or-nothing recovery, and action-key rules.
It does not claim that either prerequisite's future runtime implementation is
already present.
