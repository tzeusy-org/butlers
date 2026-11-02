## Why

Today "I don't know" is terminal: a sourceless decline ends the question, so the owner carries the
job of remembering to re-ask. vision.md measures success by the mental labor the system reliably
absorbs, and the Relationship manifesto promises that "you forget to ask about it next time" never
happens.

RFC 0038 now governs the target. Switchboard classification and the dashboard answer lane have no
successor (D1); an owner question becomes an admitted goal, and D19's read-only answer goal holds
one granted, non-steering Memory gap `record` capability (REQ-dashboard-goal-continuity-004 and
-008). This amendment applies the `re-scope` row for this change in the
[generalist collision dispositions](../generalist-lead-focused-delegation/collision-dispositions.md)
so generalist task 4.2 applies cleanly. It changes no RFC 0038 decision, profile or activation gate.

## What Changes

- An owner decline may record a typed gap (entity, predicate) in the memory schema of the called
  domain, which records only predicates registered there, with the origin taken from server-side provenance, never from
  arguments. Before the switch the only seam is the landed, frozen dashboard answer-lane
  `conversation_reply` decline. After the switch the only seam is the broker-granted,
  non-steering Memory gap `record` capability (`record` class, `owner_authority` false,
  `key_required`) that D19 grants to an owner-authenticated answer goal.
- Capture admits a closed set of origin channels: `dashboard` now, `telegram` once owner-channel
  authorization verifies channel and sender and the canonical owner-delivery path can receipt a
  notice into that thread (`bu-q7vx1q.70`). Every other channel is refused and records nothing.
  Non-owner goals hold no gap `record` grant.
- A later write of a matching (entity, predicate) fact, through memory `store_fact` or
  `relationship_assert_fact` (including a generated inverse edge), marks the open gap answerable
  in the same transaction. Closure attribution comes from the fact's stored authority, so a
  third-party report reads "reported by" and never closes a gap as established fact.
- A deterministic, model-free module-default job posts exactly one notice per origin thread with
  the fact's reference, retries with bounded backoff, records a `failed` attention event and flags `delivery_failed` on
  exhaustion, and
  expires gaps open past their ttl. Dashboard origins post into their own thread; other admitted
  channels deliver only through the canonical owner-delivery subject with receipts, and an
  ambiguous send stays `unknown` instead of being resent.
- `memory_open_gaps` and `GET /api/memory/gaps` let the owner ask what is still unknown, with
  honest coverage states.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-memory`: adds "Owner knowledge gaps" (`REQ-module-memory-018`) and "Knowledge-gap
  capture admits only a verified owner origin" (`REQ-module-memory-019`).
- `relationship-facts`: adds "The central writer answers matching knowledge gaps"
  (`REQ-relationship-facts-009`).

The amendment withdraws this change's `butler-switchboard` delta ("Answer-lane decline may record a
knowledge gap"). Generalist removes the dashboard classification lanes at task 4.2, so an
answer-lane requirement would either be stranded in the baseline or added after its lane is gone.
Its two scenarios move, by name, into `REQ-module-memory-019`.

## Impact

Already landed (bu-q7vx1q.9, .74, .76): memory migration `mem_014`, `core_tools/_conversation_reply.py`,
the answer-block instruction in `core_tools/_switchboard.py`, `modules/memory/knowledge_gaps.py`,
`modules/memory/storage.py`, `relationship_assert_fact.py`, the `memory_knowledge_gap_delivery`
job, `memory_open_gaps` and `GET /api/memory/gaps`. Open: `bu-q7vx1q.70` (goal-runtime capture
seam and Telegram through canonical owner delivery), `bu-q7vx1q.71` (entity-detail "Still
unknown"), `bu-q7vx1q.73` (`/memory` Questions register, `GET /api/memory/gaps/demand` and the
palette verb, PR #5383), `bu-q7vx1q.75` (closure-failure counter). At the switch, the answer-lane
`gap` argument dies with the dashboard lane.

## Re-scope and delivery boundary

Every original outcome and scenario survives; the trace is in design.md. `REQ-module-memory-018`,
`REQ-module-memory-019` and `REQ-relationship-facts-009` are minted here for the three blocks. Goal
admission, the broker refusal of a gap `record` after a matching read, effect identity and the D19
answer goal stay with their own owners (generalist, `dashboard-one-mind-goal-surface`). No code,
tests, baseline spec, RFC, archive or runtime activation belongs to this documentation amendment.
Tests: +0 ~0 -0.
