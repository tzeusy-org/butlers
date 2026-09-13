## Context

This change reconciles documentation that predates two reviewed, owner-scoped
deliveries: the sleep projection in PR #1216 and the expanded Health-fact
adapters in PR #1489. The required read surface is now migration-tracked by
Health memory `mem_011`, which grants `butler_chronicler_rw` only `SELECT` on
`health.facts` after that table exists. See `proposal.md` for motivation. The
live source boundary is intentionally three-stage: connector → Health fact
writer → migration-tracked read grant → scheduled Chronicler adapter.

## Goals / Non-Goals

**Goals:**

- Make the RFC and capability specs describe the fact-level projection
  contract that existing adapters, schedules, and tests implement.
- Keep an adapter capable of reading a pre-existing `workout_session` fact
  distinct from an upstream Google Health connector that never emits such a
  resource.
- Preserve a read-only, optional-schema source boundary and the existing
  privacy and retention semantics, including source-absence behavior for an
  already-projected record.

**Non-Goals:**

- No code, migration, ACL, connector, credential, deployment, or runtime
  state change.
- No change to the merged PR #3897 or to the authority that grants the
  existing cross-schema read surface.
- No new Google Health workout API resource, wellness envelope, fact predicate
  registration, or dashboard behavior.

## Decisions

### Reconcile as observed documentation, not a new feature proposal

The historical owner-scoped Chronicles decision explicitly promoted health
sources, and the reviewed implementation is already scheduled and regression
tested. The discrepancy is therefore documentation drift, not an unapproved
new behavior. The delta records the existing boundary and its limitation; it
does not authorize implementation work.

### Reconcile only after the read surface lands

The prior documentation pass remained held while the required Health-to-
Chronicler `SELECT` privilege was still pending. `mem_011` is now on the
baseline and establishes that table-specific, read-only prerequisite. The
reconciled `supported` declaration therefore describes an adapter that can use
its approved surface; this documentation change neither grants privileges nor
creates an alternate runtime fallback.

### Describe the fact boundary, not a raw-provider shortcut

The source declaration names the durable `health.facts` predicates and their
projected outputs. This matches RFC 0014's adapter model and avoids implying
that Chronicler receives raw connector payloads or owns a connector. The
alternative — documenting the source as direct Google API evidence — would
contradict the scheduled adapter and raw-payload non-retention boundaries.

### Keep workout capability conditional and upstream ingestion deferred

The scheduled workout adapter has a real `workout_session` projection path.
The current Google Health resource catalog, Health ingest mapping, and wellness
predicate migration contain no workout resource or producer. The documentation
therefore states both facts: a separately present conformant fact can project,
but Google Health workout ingestion remains absent until a future change defines
its source contract and verification.

### Amend RFC 0014 inline using the established amendment convention

RFC 0014's source table is a higher-precedence contract. This change adds an
explicit amendment that updates only the stale Health rows and records the
upstream workout boundary. A parallel new RFC or a source-code change would
either duplicate the contract or broaden the task.

## Risks / Trade-offs

- [A `supported` registry label could be read as proof of live ingestion] →
  The docs name the fact-level surface, its landed `mem_011` read prerequisite,
  and state that connector heartbeat or absence of a fact is separate from
  support.
- [The dormant workout adapter could be mistaken for a connector feature] →
  Every changed contract states that it has no current upstream Google Health
  producer.
- [Source retention could be inferred from a raw payload] → The declaration
  records the existing fact retention class and that no raw connector payload
  is copied into Chronicler.

## Migration Plan

1. Review and merge this documentation-only change.
2. Sync the validated OpenSpec delta into the canonical specs through the
   normal OpenSpec archive workflow.
3. Do not run a migration, restart, credential operation, connector poll, or
   backfill as part of this change.
