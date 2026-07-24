## Why

The strict owner-Telegram wake-recovery packet deliberately stops ordinary
cancellation once any participant has durably prepared a cohort. Without a
separate final admission at Messenger, a Scheduler or Health cancellation can
race a commit/release, misread a changed DND generation, or return only part of
a fenced cohort to ordinary work.

This prerequisite defines the smallest durable cancellation boundary before
the wake-recovery implementation can expose a post-prepare cancellation path.
It consumes the canonical DND generation guard; it does not replace that guard
or add another cross-butler state authority.

## What Changes

- Define `wake_recovery.cancel_admit.v1`, origin-facing
  `wake_recovery.cancel_finalize.v1`, and `wake_recovery.cancel_publish.v1` as
  authenticated, Switchboard-mediated request/receipt contracts for one
  complete prepared cohort and one immutable run fence.
- Make Messenger the sole final authority for precommit cancellation admission:
  it records an idempotent accepted, rejected, or ambiguous decision while
  serializing against its local wake-recovery egress state and the canonical
  DND guard.
- Require an exact run/fence/participant/cohort/action binding, durable
  request fingerprints, an immutable per-origin frozen-subset commitment, DND
  generation evidence, and explicit no-egress-intent and no-send-start
  preconditions before cancellation can be accepted or published.
- Define all-cohort cancellation recovery: Switchboard first delivers opaque
  accepted Messenger evidence and the recipient's immutable frozen-subset
  manifest entry through `cancel_finalize.v1`; only matching durable
  finalization receipts for every participant can let the cohort enter the
  prerequisite-defined scheduler-return path. A DND mismatch instead triggers durable
  Switchboard-mediated parent `abort.v1(reason=blocked_dnd)` fanout into every
  origin's retained `release_retained_dnd` state; every other uncertain result
  remains scheduler-ineligible.
- Add a narrowly scoped RFC 0009 consumer rule and an executable future
  PostgreSQL/MCP contract-test matrix. This planning packet introduces no
  runtime migration, Scheduler implementation, Messenger release behavior,
  provider call, or change to draft PR #3513.

## Capabilities

### New Capabilities

- `wake-recovery-cancellation-admission`: Versioned, durable precommit
  cancellation admission for a complete wake-recovery cohort.

### Modified Capabilities

- `butler-messenger`: Messenger gains the future versioned cancellation
  admission and durable effective-egress decision boundary.
- `butler-switchboard`: Switchboard gains authenticated mediation of a
  cancellation request and receipt, without peer-schema access.
- `time-aware-delivery`: Fenced wake-recovery rows gain a future
  all-cohort-only scheduler-return rule after a cancellation admission.
- `database-security`: The future cancellation record and guarded DND read
  path retain private-schema isolation and least-privilege roles.

## Impact

This is an OpenSpec/RFC-only prerequisite. A later implementation will add
Messenger-local records and authenticated MCP tools, origin-local finalization
transitions and receipts, coordinator retry handling, and PostgreSQL
role/concurrency tests.
It depends on the canonical DND generation guard from `bu-12iab`; it neither
implements nor alters the parent wake-recovery protocol in `bu-kqnum.3.4`.
