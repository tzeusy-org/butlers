## Why

`relationship.entity_facts` records *that* a triple is believed but never *why*.
The typed evidence a writer cites is forwarded to the `pending_actions` dossier
and then discarded, so once a fact is active nothing links it back to the
observation that produced it. An owner asking "where did this come from?" gets
`src` and a timestamp — a source name, not a justification.

The same gap makes an empty read ambiguous. Zero active facts for a predicate
means either "no source has ever looked" or "sources looked and there is
genuinely nothing", and a caller that cannot separate those either over-trusts
silence or re-asks forever.

A third, smaller defect falls out of the same area: the owner carve-out parks a
fact write with `src` and `observed_at` inside `pending_actions.tool_args`, and
approval dispatch replays `tool_args` by splatting it into the MCP tool. The
tool has no such parameters, so approving an owner fact raises an
unexpected-keyword `TypeError` that surfaces as "No reachable butler to dispatch
action". Adding those parameters is not an option: a caller-settable `src` is
exactly the owner-carve-out bypass bu-vj46x closed.

## What Changes

- Persist an immutable typed evidence packet in `relationship.fact_evidence` in
  the same transaction as the fact row, for direct writes and for approved
  replays alike. Evidence rows cite sources (`fact`/`entity`/`url`/`text` refs
  capped at 512 characters); they never carry copied source content.
- Stamp per-assertion provenance (`assert_origin`, `assert_session_id`,
  `assert_action_id`) on the fact row, so a fact asserted with no cited evidence
  still records who asserted it and from which session.
- Carry evidence forward on supersession as tagged copies (`carried_from`); an
  append-only trigger makes rewriting an evidence row impossible.
- Record per-`(subject, predicate, src)` coverage receipts in
  `relationship.fact_coverage` and compose them into exactly one of `present`,
  `absent_proven`, `unknown`, or `unavailable`. Missing coverage always composes
  to `unknown`.
- Move the parked write's `src`/`observed_at` out of `tool_args` into
  server-written `relationship.fact_approval_context`, and add a verified
  `approval_action_id` door so an approved write executes with the provenance
  the owner approved instead of re-parking forever.
- Expose the evidence packet and coverage state through Relationship MCP reads.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `relationship-facts`: the central writer persists evidence and coverage
  atomically with the fact, an approved replay executes under server-recorded
  provenance, and predicate reads report an explicit coverage state.

## Impact

- `roster/relationship/migrations/034_fact_evidence_and_coverage.py` (new tables
  `fact_evidence`, `fact_coverage`, `fact_approval_context`; three provenance
  columns on `entity_facts`; repair of in-flight parked actions)
- `roster/relationship/tools/fact_evidence.py`,
  `roster/relationship/tools/fact_coverage.py` (new)
- `roster/relationship/tools/relationship_assert_fact.py` (evidence + receipt
  persistence, approved-action resolution)
- `roster/relationship/tools/relationship_lookup.py` (returns `fact_id`)
- `roster/relationship/modules/tools.py` (three read tools; `src` and
  `observed_at` removed from the assert wrapper's signature)

## Deferred

Bitemporal `effective_from`/`effective_to`, temporal precision, and the
cardinality/overlap rules over valid periods are NOT in this change. They need
their own `entity_facts` schema migration and their own read semantics, and
folding them in here would make one unreviewable change out of two.
