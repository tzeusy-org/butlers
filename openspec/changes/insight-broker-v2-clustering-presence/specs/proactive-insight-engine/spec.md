## MODIFIED Requirements

### Requirement: Context-Bus Gating of the Delivery Cycle
The delivery cycle SHALL consult the situational context bus
(`public.user_context`) for an active `dnd`, `meeting`, `sleeping`, or
`traveling` signal, deterministically, as an additional suppression input
alongside the global Owner Attention Policy. When more than one such signal
is active, precedence is `dnd`, then `meeting`, then `sleeping`, then
`traveling` — the first of these, in that order, with an active
non-max-held instance wins and is reported as the suppression signal.

Each signal type has its own max-hold TTL bounding how long that signal
alone may suppress routine delivery, independent of the signal's own (often
much longer) context-bus expiry: `dnd` 4 hours, `meeting` 2 hours, `sleeping`
10 hours, `traveling` 6 hours. A signal whose `set_at` is older than its
max-hold TTL, relative to the delivery cycle's `now`, no longer suppresses
delivery even while it otherwise remains active on the context bus — this
exists because `traveling` may legitimately stay active for up to 30 days
(per the context-bus module's own TTL clamp), and routine insights must not
silently queue for the length of a trip.

#### Scenario: dnd signal suppresses when no quiet hours are configured
- **WHEN** `public.approvals_policy.quiet_start_hour`/`quiet_end_hour` are NULL
  (quiet hours not configured or not active)
- **AND** `public.user_context` has an active `dnd` signal within its max-hold
  TTL
- **AND** no pending candidate is priority>=90
- **THEN** the cycle is suppressed exactly as if quiet hours were active, with
  `reason="context_bus:dnd"`

#### Scenario: meeting or traveling signal suppresses like dnd/sleeping
- **WHEN** `public.user_context` has an active `meeting` or `traveling`
  signal within its max-hold TTL, and no higher-precedence signal is active
- **AND** no pending candidate is priority>=90
- **THEN** the cycle is suppressed with `reason="context_bus:meeting"` (or
  `"context_bus:traveling"`), exactly as `dnd`/`sleeping` suppress today

#### Scenario: A signal beyond its max-hold TTL no longer suppresses
- **WHEN** `public.user_context` has an active `traveling` signal whose
  `set_at` is more than 6 hours before the delivery cycle's `now`
- **AND** no other suppressing signal is active
- **THEN** the cycle is NOT suppressed by that signal — the context-bus
  consult returns no suppression from it, even though the signal itself
  remains active (not yet expired) on the context bus

#### Scenario: A lower-precedence active signal still suppresses when a higher one has expired its hold
- **WHEN** `public.user_context` has an active `dnd` signal beyond its 4-hour
  max-hold TTL, and an active `meeting` signal within its 2-hour max-hold TTL
- **THEN** the cycle is suppressed with `reason="context_bus:meeting"` — the
  suppression check does not stop at the first (expired-hold) signal in
  precedence order, it falls through to the next eligible one

## ADDED Requirements

### Requirement: Correlated-Candidate Clustering in Digest Formatting
When the delivery cycle composes a multi-candidate digest, it SHALL group
candidates that share a non-null `metadata.entity_id`, or whose event time
windows overlap (`metadata.event_window: {start, end}` as ISO 8601
timestamps, or `metadata.event_date` as an ISO date normalized to a half-open
full UTC-day window `[00:00, next 00:00)`), into one labeled sub-group within
the digest message. An explicit `event_window` is authoritative: `event_date`
is considered only when `event_window` is absent. An explicit `event_window`
SHALL have positive duration (`end > start`); malformed, partial, empty,
wrong-type, or reversed windows resolve no correlation data. Event-window
overlap uses half-open `[start, end)` semantics, so adjacent boundaries alone
do not correlate. Grouping is
transitive: if candidate A links to B and B links to C, all three render as
one group even if A and C share neither an entity nor an overlapping window
directly. This grouping is deterministic and computed without any LLM call.
A candidate with neither field, or with malformed values for either field,
resolves no correlation data and renders as its own singleton entry, in
exactly the same textual form as digest formatting produced before this
requirement existed.

#### Scenario: Candidates sharing an entity render as one correlated group
- **WHEN** the digest includes two candidates whose `metadata.entity_id`
  values are equal and non-null
- **THEN** the digest renders those two candidates under one
  `Correlated (2):` sub-entry instead of two separate flat bullets

#### Scenario: Candidates with overlapping event windows render as one correlated group
- **WHEN** the digest includes two candidates whose `metadata.event_window`
  (or `metadata.event_date`) time ranges overlap
- **THEN** the digest renders those two candidates under one correlated
  sub-entry

#### Scenario: Adjacent UTC event dates remain separate
- **WHEN** the digest includes one candidate with `metadata.event_date`
  `"2026-08-04"` and another with `metadata.event_date` `"2026-08-05"`
- **THEN** the digest renders them as separate entries because their normalized
  half-open UTC-day windows meet at a boundary but do not overlap

#### Scenario: Empty event window fails open to singleton
- **WHEN** a candidate has an explicit `metadata.event_window` whose `start`
  and `end` are equal, alongside a valid window that covers that instant
- **THEN** the empty window resolves no correlation data and both candidates
  render as separate singleton entries

#### Scenario: Invalid explicit event window does not fall back to event date
- **WHEN** a candidate carries a partial, empty, wrong-type, zero-length, or
  reversed explicit `metadata.event_window` together with a valid
  `metadata.event_date` that would overlap another candidate's valid window
- **THEN** the explicit window resolves no correlation data, the event date is
  not used as a fallback, and both candidates render as separate singleton
  entries

#### Scenario: No correlation metadata preserves prior flat-list formatting
- **WHEN** none of the digest's candidates carry `metadata.entity_id`,
  `metadata.event_window`, or `metadata.event_date`
- **THEN** the digest renders as a flat numbered list of
  `[Butler] message` lines, byte-identical in structure to digest formatting
  before this requirement existed

#### Scenario: Malformed correlation metadata fails open to singleton
- **WHEN** a candidate's `metadata.event_window` or `metadata.event_date`
  cannot be parsed as a valid date/timestamp
- **THEN** that candidate is treated as having no correlation data for
  clustering purposes — it does not raise, and does not silently link to an
  unrelated candidate

### Requirement: Held-By Signal Telemetry on Suppressed Ledger Rows
The delivery cycle SHALL record a `held_by` key in the `metadata` of any
`public.attention_ledger` row it writes with `outcome="suppressed"`, naming
the specific suppression signal — the context-bus signal type (`"dnd"`,
`"meeting"`, `"sleeping"`, or `"traveling"`) or the literal string
`"quiet_hours"` — so the specific hold is queryable as structured data
without parsing the free-text `reason` field.

#### Scenario: Suppressed ledger row names the holding signal
- **WHEN** the delivery cycle is suppressed by an active `meeting` signal
- **THEN** the resulting `outcome="suppressed"` ledger row has
  `reason="context_bus:meeting"` and `metadata={"held_by": "meeting"}`

#### Scenario: Quiet-hours suppression names quiet_hours as the holding signal
- **WHEN** the delivery cycle is suppressed by the Owner Attention Policy
  quiet-hours window (not the context bus)
- **THEN** the resulting `outcome="suppressed"` ledger row has
  `reason="quiet_hours"` and `metadata={"held_by": "quiet_hours"}`

### Requirement: Best-Effort LLM Synthesis for Correlated Clusters
The delivery cycle SHALL attempt a best-effort one-sentence LLM synthesis for
each correlated cluster (see "Correlated-Candidate Clustering in Digest
Formatting") with more than one member when composing a multi-candidate
digest, rendering the synthesis inline with the cluster's `Correlated (N):`
label on success. Synthesis SHALL use only the "cheap"
model-catalog complexity tier resolved for the delivering butler via the
direct-API runtime lane (`runtime_type="api"`); when that tier resolves to
any other runtime, is unavailable, is over its token quota, times out, or
returns a blank response, synthesis SHALL fail open and the cluster renders
with its pre-existing plain bullet-list formatting. Synthesis SHALL NOT
introduce a new delivery budget knob — call volume is bounded by the
existing per-day candidate budget alone (at most one call per multi-candidate
cluster within an already-budgeted selection).

#### Scenario: Successful synthesis is rendered inline with the cluster label
- **WHEN** a correlated cluster resolves a non-blank one-sentence LLM
  synthesis
- **THEN** the digest renders `Correlated (N): <sentence>` for that cluster,
  followed by its member bullets unchanged

#### Scenario: Synthesis fails open to the plain cluster label
- **WHEN** the cheap tier resolves to a runtime other than the direct-API
  lane, is unavailable, is over quota, times out, or returns a blank response
- **THEN** the cluster renders as `Correlated (N):` with no inline sentence,
  identical to digest formatting before this requirement existed

#### Scenario: No new budget knob is introduced
- **WHEN** cluster synthesis is attempted
- **THEN** the number of synthesis calls in a cycle never exceeds the number
  of multi-candidate clusters within that cycle's already-computed
  `effective_budget` selection — no separate LLM-call budget setting exists

### Requirement: Hold-Until-First-Active Daily Digest Cadence
The daily (non-urgent) delivery cycle SHALL support a `daily_hold_mode` in
which a fully suppressed cycle with no pending urgent (priority >=
`URGENT_PRIORITY_THRESHOLD`) candidate does not unconditionally skip until
the next scheduled daily cron slot. Instead:
- if the active suppressing signal is `traveling`, the cycle SHALL defer the
  routine digest entirely for that tick (`reason="travel_day_defer"`),
  regardless of how long `traveling` has been active, and SHALL NOT
  force-deliver it via the hard fallback deadline below;
- otherwise (an active `dnd`, `meeting`, `sleeping`, or quiet-hours
  suppression), the cycle SHALL bypass suppression for the full routine
  pending set once the delivery cycle's `now` reaches the hard fallback
  deadline (11:00 UTC), so a held digest is never silently skipped for an
  entire day.

`daily_hold_mode` has no effect on a cycle that is not suppressed, nor on the
`urgent_only` hourly sub-cycle (which already bypasses this suppression
consult unconditionally per RFC 0011 Amendment 1).

#### Scenario: A travel day defers the routine digest without a deadline override
- **WHEN** `daily_hold_mode=True`, the active suppressing signal is
  `traveling`, and no urgent candidate is pending
- **AND** the delivery cycle's `now` is past the hard fallback deadline
- **THEN** the cycle is still skipped with `reason="travel_day_defer"` — the
  hard fallback deadline does not force delivery on a travel day

#### Scenario: The hard fallback deadline force-delivers a held routine digest
- **WHEN** `daily_hold_mode=True`, the active suppressing signal is `dnd`,
  `meeting`, `sleeping`, or quiet-hours, no urgent candidate is pending, and
  the delivery cycle's `now` has reached the hard fallback deadline
- **THEN** the cycle proceeds with the full routine pending candidate set as
  if unsuppressed

#### Scenario: Before the hard fallback deadline, a held digest keeps waiting
- **WHEN** `daily_hold_mode=True`, a non-traveling suppressing signal is
  active, no urgent candidate is pending, and `now` has not yet reached the
  hard fallback deadline
- **THEN** the cycle is skipped with the original suppression reason,
  identical to `daily_hold_mode=False` behavior
