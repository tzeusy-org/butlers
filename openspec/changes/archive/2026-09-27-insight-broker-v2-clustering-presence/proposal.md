## Why

The fleet's single convergence point (the insight broker's daily digest) does
zero synthesis: `deduplicate_candidates` collapses identical `dedup_key`s
only, so unrelated-looking candidates that actually describe the same moment
(a trip departure and a deadline landing the same week, a bill and an
anomaly on the same account) arrive as unrelated flat bullets the owner must
assemble by hand. And delivery suppression is clock-only plus a narrow
dnd/sleeping context-bus check — the broader presence signals the context
bus already tracks (`meeting`, `traveling`) are never consulted, so routine
nudges land mid-meeting, and a signal like `traveling` (which can legitimately
stay active for up to 30 days) has no mechanism to stop it from silently
queuing every routine insight for the length of a trip.

Evidence: `openspec/specs/insight-delivery/spec.md` (digest formatting is a
flat numbered list); `openspec/specs/proactive-insight-engine/spec.md`
"Context-Bus Gating of the Delivery Cycle" (dnd/sleeping only, no hold-time
cap); `roster/switchboard/tools/insight/broker.py` `_format_digest` (flat)
and `get_suppressing_context_signal` import (dnd/sleeping only, no `now`
parameter to bound how long a signal may hold).

Source: docs/redesigns/2026-07-25-jarvis-pursuit.md (rank 9, bu-ep4ks.9).

## What Changes

- **Deterministic clustering (zero-LLM):** `_cluster_candidates` groups a
  digest's candidates by shared `metadata.entity_id` or overlapping event
  time window (`metadata.event_window: {start, end}` or `metadata.event_date`),
  using strict positive-duration half-open `[start, end)` bounds. An explicit
  event window is authoritative; event date is a fallback only when that key
  is absent. Union-find folds transitive links into one group.
  `_format_digest` renders each multi-candidate group as one
  labeled `Correlated (N):` sub-list instead of unrelated flat bullets; a
  candidate with no correlation data renders exactly as before this change.
- **Source-grounded producer adoption:** health maps its measurement-door
  `since`/`until` to `event_window`; finance emits only the stored bill due or
  subscription renewal `event_date` (there is no established cross-domain
  entity relation); travel emits its stored `travel.trips.id` plus the stored
  departure or document-expiry `event_date`; and relationship emits resolved
  contact entity IDs plus actual upcoming-occasion dates for upcoming-date and
  pending-gift candidates. Stale-contact and interaction-milestone candidates
  carry their resolved entity only. The adoption deliberately emits no
  synthetic IDs, aggregate/scan-window dates, or stale-path dates.
- **Presence-aware context-bus suppression:** the insight broker's own
  `get_suppressing_context_signal` (previously imported from the shared
  `butlers.core.attention_ledger` helper) becomes a broker-local function
  extending the suppressing signal set from `{dnd, sleeping}` to
  `{dnd, meeting, sleeping, traveling}`, each with an independent max-hold
  TTL bounding how long that signal alone may hold routine delivery
  (dnd 4h, meeting 2h, sleeping 10h, traveling 6h) — decoupled from the
  signal's own, much longer, context-bus TTL. The shared
  `attention_ledger.get_suppressing_context_signal`/`get_suppressing_context`
  (consumed by decision digests, secrets-lifecycle notifications, and
  fleet-halt/model-breaker escalations) is untouched; this is deliberately
  broker-local to keep those subsystems' behavior unchanged.
- **`held_by` ledger telemetry:** the suppressed `public.attention_ledger`
  row now carries a structured `metadata.held_by` field (the specific
  signal name, or `"quiet_hours"`) alongside the existing free-text
  `reason`, so "held by \<signal\>" is queryable without parsing `reason`.

## Out of Scope (as of the original slices 1-2 PR #3583)

- LLM one-sentence synthesis per cluster (slice 3) — needs its own budget/
  cost-accounting design under the existing delivery budget; not cheap
  enough to land alongside slices 1-2 without ballooning this diff.
- Conflict-cluster routing to the Owner Decision Desk (slice 4) — an
  integration with `bu-ckkpz` Decision Desk machinery, not a broker-local
  change; needs its own scoped design for what makes a cluster a "tension"
  versus an ordinary correlation.
- Hold-until-first-active briefings with travel-day skip/defer (slice 5) —
  a scheduling-cadence change orthogonal to clustering/suppression, better
  scoped on its own.
**bu-iq8as / bu-0rflx follow-up update:** slice 3, slice 5, health's
`event_window` adoption, and the source-grounded Finance/Travel/Relationship
producer adoption above are now implemented (see `tasks.md` section 4).
Slice 4 (Decision Desk conflict routing) remains deferred: the decision-bead
convention/dashboard/cron (`bu-ckkpz.1/.2/.4`) is landed, but no runtime
write path exists anywhere in this codebase for application code to file a
decision bead programmatically, and `bu-ckkpz.3` (the attention-ledger
routing slice this would most naturally build on) is still `blocked` —
inventing an unreviewed write pattern for this bead was judged out of scope.

## Impact

- Affected specs: `proactive-insight-engine` (context-bus gating requirement
  broadened; new clustering requirement; new `held_by` telemetry
  requirement).
- Affected code: `roster/switchboard/tools/insight/broker.py` and the
  Finance/Travel/Relationship insight jobs. No migration, no new table, and
  no cross-butler schema change.
- Affected tests: `tests/modules/test_insight_engine.py` (cluster boundary
  regression), the Finance/Travel/Relationship job tests (source-grounded
  metadata), `tests/modules/test_insight_context_bus_suppression.py` (new
  file), and `tests/modules/test_insight_attention_ledger.py` (extended
  `TestContextBusGating`).
