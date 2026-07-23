## Context

Chronicles is a retrospective owner surface. Its existing day-close cache treats
any non-empty successful session output as renderable prose, its editorial
payload classifies an empty result as `quiet`, and its archive floor is derived
from episodes alone. Those shortcuts can turn an agent/tool trace, a date-mismatched
summary, an uncovered historical day, or an owned-query failure into calm
reader-facing copy.

This change serializes the contract before implementation. It is authorized by
owner decision `bu-re1ip` option A, which permits only focused reader-visible
availability deltas and forbids expansion into authorization, mutation,
retention, privacy, topology, or degraded-flag-registry work. Blocked
`bu-imsks` remains the implementation owner for Chronicles source-availability
classification and its API/frontend surface. The active
`chronicler-telemetry-distillation` change remains separate.

## Goals / Non-Goals

**Goals:**

- Make it impossible for the contract to call unproven absence a quiet day.
- Define a deterministic, auditable cache-admission boundary for human
  day-close prose and a safe read-time containment path for legacy invalid rows.
- Make the archive floor and backward navigation derive only from durable,
  authoritative covered local days in the owner timezone.
- Give later workers exact state precedence and an API boundary that prevent
  stale or invalid LLM prose from overriding `no_data` or degraded truth.
- Preserve a single implementation owner for availability-failure detection.

**Non-Goals:**

- No runtime, migration, data repair, cache deletion, API implementation,
  frontend implementation, notification, LLM, or cross-schema change.
- No redesign of valid day-close prose, no new LLM path, and no change to the
  once-daily retrospective-message boundary.
- No feeder repair, source health heuristic, availability classifier, or
  `SourceStateBadgeStrip` implementation. Those remain with `bu-imsks`.
- No reuse, amendment, task completion, or inferred dependency on the active
  `chronicler-telemetry-distillation` change or its `daily_rollups`.

## Decisions

### 1. A covered-local-day witness, not an operational proxy, is authoritative

The reader contract requires a durable, Chronicler-owned coverage witness that
answers whether a particular owner-timezone local calendar day was successfully
covered. A witness is authoritative only when the owning coverage computation
completed its required Chronicle evidence reads for that local day without an
owned query failure. It must retain enough provenance to audit the local date,
timezone, and successful coverage verdict.

`earliest_date` is the minimum local date with an authoritative `covered`
witness. A selected day may be called `no_data` only when the coverage oracle
positively establishes that the selected local day is outside the historical
coverage floor. An absent row, an incomplete range, or a failed coverage query
is indeterminate and therefore unavailable, not `no_data`.

The following are explicitly prohibited as coverage evidence:

- `source_adapter_state.registered_at` or source registry seeding;
- current source `active`, error, checkpoint, or feeder state;
- a current checkpoint timestamp; and
- trailing or sparse `daily_rollups` rows, flags, or narratives.

Those values describe registration, present operational health, or a later
materialization, not what Chronicle evidence was successfully available for an
older local day. A scalar archive floor alone also does not prove an intervening
day was covered.

**Alternatives considered:** deriving the floor from the first episode is
rejected because a covered quiet day has no episode; reusing `daily_rollups` is
rejected because they can be trailing, sparse, and owned by the unresolved
telemetry-distillation work; using current feeder state is rejected because it
rewrites historical truth from present health.

### 2. Cache admission is deterministic and binds the closed local day

An admissible day-close cache entry is non-empty, owner-facing retrospective
prose bound by its structured `date_label` (the closed local date) to the same
owner-timezone day as its `day_close:{date}` cache key and local-day window. The
cache writer must apply a documented deterministic shape predicate before
persistence; it must reject machine roles, tool-call/protocol payloads,
serialized objects, code fences, and execution-planning or planning-verb
preambles rather than asking another model to judge them. The predicate must
fail closed when the result is ambiguous.

The writer must reject a missing or mismatched structured `date_label`. It must
not try to prove date correctness by guessing from arbitrary natural-language
date mentions. A failure of either the prose-shape check or date binding makes
the row **invalid**, not stale.

The reader repeats the same admission check before returning legacy or newly
read cache content. Invalid rows are retained for audit/recovery but are never
rendered, returned as prose, or silently relabeled as a valid stale summary.
For a covered and available day, absent or invalid cache content selects the
existing deterministic templated fallback.

**Alternatives considered:** accepting any non-empty successful session output
is rejected because it admitted the observed planner/tool trace; deleting an
invalid row is rejected because containment must preserve forensic evidence;
using an LLM to repair or classify it is rejected because a reader path must
remain deterministic and must not create a new LLM call.

### 3. Availability and coverage select the payload before cache lookup

The editorial payload resolves state in this order:

1. If a required owned query fails, coverage cannot be established, or the
   availability input is `unavailable` or `degraded`, return the corresponding
   deterministic unavailable/degraded payload. It is never `no_data` or
   `quiet`.
2. If the authoritative coverage verdict positively says the selected local day
   is outside the archive, return deterministic `no_data`.
3. If the selected day is authoritatively covered, all required owned reads
   succeed, and the reader-visible evidence is empty, return confirmed
   `quiet`.
4. Only a covered, available payload may apply normal editorial classification
   (`urgent`, `busy`, `mild`, or `quiet`) and evaluate eligible cached prose.

`no_data`, `unavailable`, and `degraded` are explicit state classes distinct
from `quiet`. `no_data` and unavailable/degraded payloads must bypass cache
lookup or discard any cache result, including a fresh or stale LLM result, and
must use deterministic state-specific text. A valid stale cache remains a
freshness condition only for a covered, available payload; it cannot override
admission validity or availability/coverage truth.

**Alternatives considered:** treating empty query results as quiet is rejected
because empty is not proof; letting cached prose win first is rejected because
old prose can contradict present availability; collapsing all non-content into
`no_data` is rejected because a query failure is an operational truth, not a
historical absence.

### 4. The raw cache endpoint exposes invalid-without-prose explicitly

`GET /api/chronicler/aggregate/day-close` gains an additive invalid response:

```json
{
  "invalid": true,
  "invalid_reason": "inadmissible_prose | date_mismatch",
  "cache_built_at": "..."
}
```

It is a successful, explicit cache-state response and contains neither `prose`
nor `provenance_refs`. Cache miss remains distinct (`404`); a valid-but-outdated
row remains the existing stale marker. Admission validation precedes staleness,
so a row that is both stale and invalid returns invalid-without-prose. The
briefing endpoint maps an invalid or missing cache to deterministic templated
copy only after it has selected a covered, available payload.

This preserves the existing no-LLM reader invariant and gives clients a
machine-readable cache disposition without exposing unsafe content.

### 5. Ownership stays serialized with `bu-imsks`

This change defines the consumer contract for the availability result, not the
mechanism that detects `PostgresError`, optional-relation absence, source
failure, retry state, or badge rendering. `bu-imsks` owns that classifier and
its `editorial.py`, API model/router, client type/hook,
`SourceStateBadgeStrip`, and `ChroniclesPage` implementation surface.

Future implementation may consume the availability envelope defined by that
lane only after an explicit coordinator reconciliation. It must not create a
second failure classifier or call an availability failure `no_data`. This change
does not alter `chronicler-telemetry-distillation`; in particular, its daily
rollups and feeder-dark behavior cannot be silently extended into a historical
coverage oracle.

## Risks / Trade-offs

- [Coverage ledger is not yet implemented] → The contract intentionally treats
  unknown coverage as unavailable rather than inventing a floor; implementation
  must add durable evidence before it enables confident `no_data`.
- [Stricter cache admission can suppress previously displayed prose] → The
  deterministic fallback preserves a useful reader surface while raw content
  remains auditable and no new LLM call is made.
- [Availability work is blocked] → This delta records the exact handoff and
  keeps detection/classification ownership in `bu-imsks` instead of duplicating
  work in a speculative implementation.
- [An active telemetry change has related vocabulary] → The explicit no-reuse
  boundary prevents a trailing rollup or feeder flag from becoming accidental
  historical evidence.

## Migration Plan

This OpenSpec-only change has no deploy or rollback action. Later implementation
must proceed in this order:

1. Land the cache-admission/read-containment slice with its exact response and
   regression tests.
2. Land the covered-local-day evidence and no-data/archive-navigation slice.
3. Resolve and implement the `bu-imsks` availability envelope and reader UI
   handoff without changing its ownership boundary.
4. Run the contract matrix across all precedence combinations before syncing
   this delta into canonical specs.

Rollback of any later implementation must fail safely to deterministic
unavailable copy rather than render an invalid cache or fabricate quietness.

## Open Questions

None for this contract slice. Physical storage and migration details are
deliberately deferred to the implementation bead that owns the coverage witness.
