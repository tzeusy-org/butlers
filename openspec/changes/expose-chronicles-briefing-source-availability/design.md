## Context

Chronicles is a retrospective reader. Its briefing must establish that each
owned input was successfully read before presenting a calm day, a historical
boundary, or cached day-close prose. The canonical `dashboard-chronicles`
contract already requires availability and coverage to win before cache
selection. `bu-imsks` owns the remaining classifier, response shape, and
reader presentation.

Two existing broad exception paths lose that distinction: source-health reads
and the authoritative coverage-floor read return an ordinary empty value for a
real `PostgresError`. The source-state badge query also disappears on a client
request failure. A missing optional relation during cold boot remains a known
non-failure, but it must not be conflated with a failed owned query.

## Goals / Non-Goals

**Goals:**

- Expose a compact, typed availability state for every briefing subquery that
  was available, unavailable, or deliberately not requested.
- Keep the existing `compose_briefing_payload(..., availability=...)`
  precedence hook as the one briefing-state classifier; detector results feed
  that hook rather than creating a second state path.
- On a real owned query failure, preserve the successful per-subquery states,
  mark the failed ones unavailable, add high source-error attention, bypass
  day-close cache prose, and use deterministic degraded/unavailable copy.
- Keep expected optional/cold-boot relation absence non-degraded and avoid
  source-error attention for it.
- Keep retry controls semantic, keyboard-operable, visibly focused through the
  existing button primitives, and announced by the existing alert/status
  semantics.

**Non-Goals:**

- No migration, historical repair, topology, cross-schema read, or source
  registry change.
- No LLM invocation, cache mutation, cache-admission change, or broad
  Chronicles redesign.
- No raw exception text, database relation name, connection detail, or secret
  exposure.
- No change to the meaning of a covered quiet day, `no_data`, or the
  Chronicler source-state endpoint's genuine empty response.

## Decisions

### 1. A briefing-owned availability ledger uses stable concern names

The response adds an additive list of `{subquery, state}` entries. `state` is
one of `available`, `unavailable`, or `not_requested`. The entries cover the
briefing's actual owned concerns: coverage floor/exact-date witness, day
episodes, sleep, health history, open corrections, recent days, and current
source health. A deliberately skipped archive-only concern and an expected
optional/cold-boot relation absence use `not_requested`; a successful empty
query is still `available`.

The ledger deliberately exposes stable presentation labels rather than SQL,
exception text, or provider details. That gives the API and UI a precise
failure boundary without leaking internals.

### 2. Classify expected absence before treating an error as unavailable

The optional source-health and coverage witness reads catch only the known
missing-relation shape and return their normal cold-boot result. Other
`PostgresError` and connection failures remain visible to the composition
orchestrator. The concurrent content reads use individual outcomes so more
than one failed concern is named and already-successful results are not lost.

Using a broad `PostgresError -> empty` fallback was rejected because it makes a
broken source indistinguishable from a healthy empty one. Treating every
missing relation as degraded was rejected because a fresh or intentionally
optional installation has not asserted an operational source failure.

### 3. Degraded state wins before cache and carries source-error attention

An unavailable coverage read yields the existing `unavailable` state because
the archive boundary itself cannot be proven. A failed content or current
source-health read yields `degraded`: some work may have succeeded but the
briefing cannot claim complete evidence. Both use the existing availability
precedence hook, deterministic non-content copy, and cache bypass.

Each failed concern becomes a high `source_error` attention item with safe,
named copy. `ChroniclesPage` maps those rows to the existing `AttentionList`
source-error and retry affordance. A non-content briefing therefore never
falls through to the calm empty attention state, and the back stepper is
disabled with an explicit boundary-unavailable reason when no trustworthy
`earliest_date` exists.

Returning partial KPI or recent-day data was rejected for this vertical. The
existing non-content payload boundary avoids presenting incomplete quantities
as a completed daily reconstruction; the ledger and attention row retain the
useful diagnostic information.

### 4. Source-state query failure marks retained badges as stale

`SourceStateBadgeStrip` reads `isError`, `data`, and `refetch` from its query.
On a failure with no retained data it renders a named alert and retry button,
not a quiet absence. If the cache retains prior rows, it renders those badges
with an explicit stale/unavailable note and retry; it never leaves them looking
like a live healthy source-state response. A successful `data: []` remains the
normal cold-boot empty state.

## Risks / Trade-offs

- [A response gains another typed field] -> keep it additive and make absent
  values default safely in the client type.
- [A brief query failure hides otherwise useful metrics] -> retain only the
  deterministic non-content boundary; the named availability ledger and retry
  make the loss actionable without inventing complete numbers.
- [Failure labels could expose internals] -> use a fixed allowlist of concern
  labels and never serialize exception text.
- [Source-state cached badges can be misread] -> pair retained badges with a
  visible stale/unavailable alert and retry action.

## Migration Plan

Deploy as an additive response field and client type. No stored rows or schema
change is required. Rollback removes the new field and UI treatment; it does
not mutate coverage witnesses or cached prose. If a rollback is needed while a
read fails, the existing deterministic non-content response remains safer than
rendering cached prose.

## Open Questions

None. The named concerns, state vocabulary, cache precedence, and recovery
control are bounded by the existing Chronicles contract and `bu-imsks` scope.
