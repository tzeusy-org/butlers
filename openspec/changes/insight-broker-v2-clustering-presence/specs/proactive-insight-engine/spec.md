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
timestamps, or `metadata.event_date` as an ISO date normalized to a full UTC
day), into one labeled sub-group within the digest message. Grouping is
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
