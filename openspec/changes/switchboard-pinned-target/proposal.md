## Why

The `ingest.v1` envelope carries no explicit routing target. Every message is
routed by thread-affinity (email-only), a global ingestion-policy rule, or LLM
classification. A per-butler dashboard chat conversation
(`POST /api/butlers/{name}/conversations`, bu-mj2k2) has no way to force its
messages to the butler the owner is already looking at — classification could
mis-route it, and the dashboard's session poll on `{name}`'s schema would then
never resolve (300s SSE timeout). The owner has confirmed (2026-07-03, epic
bu-p6ey8) the fix is an explicit envelope pin rather than a seeded per-butler
policy rule, since a policy rule cannot express "this specific request, from
this specific caller, regardless of content."

## What Changes

- Add an optional `pinned_target` field to `IngestControlV1` (the `ingest.v1`
  envelope's `control` section). When present, Switchboard ingestion produces a
  deterministic `route_to` triage decision to that butler — no LLM
  classification, no ingestion-policy rule evaluation, no thread-affinity
  lookup.
- `pinned_target` is validated against the live, routable butler registry
  (`butler_registry`, `routable_only=True`, `butler_only=True` — the same
  candidate set LLM classification uses). An unknown, non-butler, or
  non-routable (quarantined/stale) target is rejected with a clear `ValueError`
  at the ingest boundary; the envelope is never accepted and never silently
  misrouted.
- No pipeline changes are needed: `pipeline.process()` already honors any
  `request_context.triage_decision == "route_to"` + `triage_target` pair
  generically (this is the exact mechanism thread-affinity and ingestion-policy
  `route_to:<butler>` rules already use). Embedding the pin's decision through
  the same `PolicyDecision` / `_build_request_context` path means the existing
  bypass in `pipeline.py` (and the `DurableBuffer` plumbing ahead of it) picks
  it up with zero code changes there.
- Precedence: `pinned_target` is checked first, ahead of thread-affinity and
  ingestion-policy rules. In practice this never collides with thread-affinity
  (email-only) since the pin is intended for the `dashboard` channel.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `connector-base-spec`: `ingest.v1` envelope's `IngestControlV1` gains an
  optional `pinned_target` field; the Triage Integration requirement gains a
  pinned-target precedence rule (checked before thread-affinity and ingestion
  rules) with deterministic-routing and rejection-on-unknown-target scenarios.
- `dashboard-conversations`: document that per-butler dashboard conversations
  submit envelopes with `control.pinned_target` set to the target butler so
  the reply lands on the correct butler's session (prerequisite for bu-mj2k2's
  real Switchboard wiring).

## Impact

- `roster/switchboard/tools/routing/contracts.py` — `IngestControlV1.pinned_target`.
- `roster/switchboard/tools/ingestion/ingest.py` — `ingest_v1()` validates and
  applies the pin ahead of thread-affinity/policy evaluation.
- No migration, no pipeline.py change, no dashboard-conversations code change
  in this bead (bu-mj2k2 owns wiring the dashboard conversation POST to set
  `pinned_target`; this bead only makes the envelope/ingest side ready for it).
