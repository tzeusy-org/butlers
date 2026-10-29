## MODIFIED Requirements

### Requirement: Context Preamble for LLM Sessions
The system SHALL provide a `format_context_preamble(signals)` function that formats a list of `ContextEntry` objects into a bracketed text string suitable for prepending to LLM session prompts. The format SHALL be: `[User Context: <signal_type> (<value>, <confidence_label>), ...]` only for values admitted by the producer-bound typed contract below; otherwise each signal SHALL use `<signal_type> (<confidence_label>)` with no value. Confidence labels SHALL be: "explicit" for 1.0, "high confidence" for >= 0.8, "medium confidence" for >= 0.5, "low confidence" for < 0.5. If no active signals exist, the function SHALL return an empty string.

The system SHALL provide `format_context_preamble(signals)` as a deterministic renderer of active, vocabulary-validated context types and confidence labels. It SHALL render a value only when an enumerated typed producer contract admits that exact value and the server-verified producer identity matches; arbitrary strings, event titles, metadata, destinations, room names, tool arguments and source text SHALL NOT become prompt instructions through this preamble. The initial value allowlist SHALL consist only of General's deterministic calendar producer: `meeting` with literal `busy`, or `focused` with literal `focused`. A matching string or a caller-supplied producer label alone SHALL NOT establish producer provenance. Other signals SHALL render type and confidence only. Confidence labels SHALL remain `explicit` for 1.0, `high confidence` for >=0.8, `medium confidence` for >=0.5 and `low confidence` below 0.5. Empty input SHALL produce an empty string. This projection SHALL NOT confer owner authority or remove RFC0038 exposure.

ID: REQ-context-bus-009
Source: RFC 0009 §§Context Preamble for LLM Sessions, Owner Attention Policy Sleep Producer, Bounded Suppression Read Failures (proposed amendment bu-dhloi1.17); RFC 0038 D5
Scope: v1-mandatory

#### Scenario: Single active signal formatted
- **WHEN** there is one active signal: traveling, value="Paris", confidence=1.0
- **THEN** `format_context_preamble()` returns `[User Context: traveling (explicit)]`
- **AND** the destination is omitted because it is not an admitted typed value

#### Scenario: Multiple active signals formatted
- **WHEN** there are two active signals: traveling (Paris, 1.0) and meeting (standup, 0.8)
- **THEN** the preamble includes both signals separated by commas
- **AND** only their type/confidence summaries appear; neither the arbitrary trip destination nor the calendar title appears

#### Scenario: Signal without value
- **WHEN** there is one active signal: dnd, value=None, confidence=1.0
- **THEN** `format_context_preamble()` returns `[User Context: dnd (explicit)]`

#### Scenario: No active signals
- **WHEN** there are no active signals
- **THEN** `format_context_preamble()` returns an empty string

#### Scenario: Typed calendar state can be rendered
- **WHEN** server-verified General calendar output is meeting/busy or focused/focused
- **THEN** only that enumerated value and the usual type/confidence are rendered

#### Scenario: Hostile or forged value is omitted
- **WHEN** a value or metadata contains bracket escapes, newlines, instructions, an event title or a forged producer label
- **THEN** the value/metadata is omitted while a valid signal type may still render
- **AND** escaping or truncation alone does not admit the text

### Requirement: Deterministic Context Producers
The system SHALL populate `public.user_context` with deterministic, zero-LLM
producers that run as scheduled `dispatch_mode="job"` handlers. Each producer
SHALL run on the butler that RFC 0009's write-permission matrix authorizes as
the single writer for the signal it produces. Producers SHALL be idempotent —
upserting the current signal via `set_context()` and clearing it via
`clear_context()` on the reverse transition — and every signal SHALL carry a
bounded TTL so a crashed producer never leaves a signal permanently pinned.

The following producers SHALL exist:
- **calendar → meeting/focused** (writer `general`): derived from the
  currently-active event in the general butler's `calendar_events`.
- **home → at_home / in_space** (writer `home`): derived from fresh Home
  Assistant `person.*`/`device_tracker.*` presence in `ha_entity_snapshot`,
  scoped to the owner's configured entities; `in_space` additionally resolves
  which room/area the owner is currently in from those same entities.
- **travel → traveling** (writer `travel`): derived from a currently-underway
  trip in `travel.trips`.
- **health → sleeping** (writer `health`): derived from the owner-declared
  end-exclusive Owner Attention Policy window in `public.approvals_policy`.
The calendar producer SHALL publish only typed busy/focused state: value `busy` for `meeting`, or `focused` for `focused`, with fixed source provenance. It SHALL NOT copy the event title, description or other free text into the emitted context value, metadata or producer result. Existing title classification MAY select the finite state but SHALL NOT transmit its input. Eligibility, human-event provenance, sibling clearing and end-based bounded expiry SHALL remain unchanged.

ID: REQ-context-bus-011
Source: RFC 0009 §§Context Preamble for LLM Sessions, Owner Attention Policy Sleep Producer, Bounded Suppression Read Failures (proposed amendment bu-dhloi1.17); RFC 0038 D5
Scope: v1-mandatory

#### Scenario: Calendar producer publishes meeting for a live event
- **WHEN** the general butler's `calendar_events` contains a confirmed,
  non-all-day event whose `[starts_at, ends_at)` window contains now
- **THEN** the calendar producer sets a `meeting` signal (or `focused` when the
  event title marks a focus block) with `set_by_butler = "general"` and the
  event's `ends_at` as `expires_at`

#### Scenario: Calendar producer clears when no event is live
- **WHEN** no confirmed, non-all-day event is currently active
- **THEN** the calendar producer clears both the `meeting` and `focused` signals
  it set

#### Scenario: Home producer publishes at_home from fresh presence
- **WHEN** a fresh `person.*` or `device_tracker.*` snapshot reads `home`
- **THEN** the home producer sets an `at_home` signal with
  `set_by_butler = "home"`

#### Scenario: Home producer ignores a stale presence feed
- **WHEN** the only presence snapshots are older than the freshness window
- **THEN** the home producer neither asserts nor clears `at_home` (the existing
  signal expires on its own TTL)

#### Scenario: Home producer resolves in_space when a room is available
- **WHEN** the owner is at_home and a fresh owner-linked entity exposes a
  room/area (via its state or Home Assistant area attributes)
- **THEN** the home producer sets an `in_space` signal with
  `set_by_butler = "home"` and the resolved room as its value

#### Scenario: Home producer clears in_space when the owner leaves
- **WHEN** the owner transitions from at_home to away
- **THEN** the home producer clears both `at_home` and `in_space`

#### Scenario: Home producer never guesses a stale room
- **WHEN** Home Assistant source health is unmeasurable, or no fresh
  owner-linked entity exposes a room
- **THEN** the home producer leaves any existing `in_space` signal untouched
  so it self-heals via its bounded TTL rather than reporting a stale room as
  current

#### Scenario: Travel producer publishes traveling for an underway trip
- **WHEN** a `travel.trips` row is `active`, or today falls within a
  `planned`/`active` trip's `[start_date, end_date]` window
- **THEN** the travel producer sets a `traveling` signal with
  `set_by_butler = "travel"` and the trip destination as its value

#### Scenario: Sleep producer publishes sleeping inside the quiet window
- **WHEN** the current time in `public.approvals_policy.timezone` falls within
  the owner-declared end-exclusive quiet-hours window
- **THEN** the health producer sets a `sleeping` signal with
  `set_by_butler = "health"` and the exact configured window end as
  `expires_at`

#### Scenario: Sleep producer activates the notify deferred-delivery gate
- **WHEN** the sleep producer has set an active `sleeping` signal
- **THEN** the notify owner-page gate's context consult observes a suppressing
  signal and durably defers a routine notification with status `deferred`
- **AND** the stored envelope's `deliver_at` is the latest active suppressing
  signal expiry

#### Scenario: Calendar title cannot propagate into context
- **WHEN** an eligible event title contains hostile instructions
- **THEN** the emitted context value is only busy/focused and its metadata/result contain no title or other event free text

### Requirement: Explicit Context MCP Tools
The system SHALL expose `check_context`, `set_context`, and `clear_context` MCP
tools on the general module so that explicit, user-initiated context signals
(primarily `dnd` and `sick`) that no deterministic producer can infer may be
read and written. Writes SHALL go through the general butler at confidence 1.0
and SHALL be subject to the same vocabulary and write-permission validation as
`set_context()`. These model-mediated writes SHALL be subject to the server-verified owner-invocation requirements below.

The system SHALL retain General's `check_context`, `set_context` and `clear_context` MCP tools and existing vocabulary, writer checks, confidence 1.0 and TTL rules. Each model-mediated set/clear SHALL additionally require the server-verified current runtime invocation binding to an owner-channel request, resolved to the actual owner or authenticated owner device. A third party, unknown/mixed/system provenance, missing binding, unbound runtime-session locator or caller-asserted identity SHALL refuse before writes. Source text, tool arguments, a source-channel label, confidence 1.0 or possession of a request/mutation ID SHALL NOT establish owner authority. Trusted deterministic producers retain their existing role-specific internal paths; this rule does not require them to impersonate a model/owner or alter canonical DND mutation replay/generation controls.

ID: REQ-context-bus-012
Source: RFC 0009 §§Context Preamble for LLM Sessions, Owner Attention Policy Sleep Producer, Bounded Suppression Read Failures (proposed amendment bu-dhloi1.17); RFC 0038 D5
Scope: v1-mandatory

#### Scenario: Explicit dnd set via MCP tool
- **WHEN** the `set_context` MCP tool is called with `signal_type="dnd"` by a verified bound owner with valid existing mutation identity
- **THEN** a `dnd` signal is written with `set_by_butler = "general"` and
  confidence 1.0 through the existing canonical DND mutation gateway

#### Scenario: check_context returns active signals
- **WHEN** the `check_context` MCP tool is called
- **THEN** it returns the currently-active context signals as a list (empty when
  none are active)

#### Scenario: Invalid signal type rejected by the tool
- **WHEN** the `set_context` MCP tool is called with an out-of-vocabulary
  `signal_type`
- **THEN** the underlying `set_context()` raises `ValueError` and no signal is
  written

#### Scenario: Bound owner can clear explicit context
- **WHEN** the actual bound owner request invokes clear_context
- **THEN** the existing authorized clear and DND receipt/generation semantics apply

#### Scenario: Third-party and forged labels cannot steer context
- **WHEN** a third party or caller-asserted owner/channel label invokes set or clear
- **THEN** the call refuses before any row, DND generation or receipt changes

#### Scenario: Missing invocation binding is not owner authority
- **WHEN** a set/clear call has no current verified invocation or only a diagnostic session locator
- **THEN** the call refuses even if its text says it is from the owner

### Requirement: Sleep Context Uses the Owner Attention Policy Anchor
The deterministic health sleep producer SHALL derive both its quiet-window
membership and `sleeping` signal expiry from the shared Owner Attention Policy
predicate and exact-end UTC anchor. It SHALL not duplicate timezone conversion
or add an hour to the configured end. Missing, incomplete, or invalid policy data from a successful read SHALL cause the producer to clear/avoid its owned sleep
signal rather than infer a replacement timezone or wake time.

The deterministic Health sleep producer SHALL derive quiet-window membership and expiry from the existing shared Owner Attention Policy and exact-end UTC anchor. Successfully read absent, disabled, incomplete or invalid policy SHALL clear/avoid Health's owned sleeping signal without inventing a timezone or wake time. A transient read failure SHALL instead remain distinguishable as unavailable: it SHALL NOT clear a still-unexpired confirmed sleep assertion, mint a new sleeping signal, refresh set_at/expires_at or treat unreadable state as a valid policy. Retained state SHALL expire at its original bounded deadline. Recovery SHALL re-read policy before asserting/clearing. End-exclusive hours, equal endpoints as an empty window, stored IANA timezone and existing sleeping TTL clamps SHALL remain unchanged.

ID: REQ-context-bus-013
Source: RFC 0009 §§Context Preamble for LLM Sessions, Owner Attention Policy Sleep Producer, Bounded Suppression Read Failures (proposed amendment bu-dhloi1.17); RFC 0038 D5
Scope: v1-mandatory

#### Scenario: Sleep expires at the exact end-exclusive boundary
- **WHEN** the current local time falls inside the Owner Attention Policy
  `[quiet_start_hour, quiet_end_hour)` interval
- **THEN** the health producer sets `sleeping` with the shared exact
  `quiet_end_hour` UTC anchor as `expires_at`, subject to the existing sleeping maximum TTL
- **AND** the signal is not active at the exact local end
- **AND** any earlier effective expiry from the existing TTL clamp is also enforced; no extra hour or replacement timezone is added

#### Scenario: Invalid persisted zone fails open for sleep context
- **WHEN** the Owner Attention Policy contains an unrecognized persisted IANA
  timezone
- **THEN** the health producer does not publish a derived sleeping signal
- **AND** it does not silently substitute UTC or another timezone

#### Scenario: Absent or malformed policy is not a storage failure
- **WHEN** a successful read returns no policy, disabled hours or malformed hours
- **THEN** Health clears/avoids only its own sleeping assertion and leaves independent active suppressors intact

#### Scenario: Temporary policy outage does not renew sleep
- **WHEN** the policy read fails while a previously confirmed Health sleep assertion exists
- **THEN** its original expiry is retained and routine delivery observes the unavailable-read contract
- **AND** repeated failures do not refresh its timestamps or move expiry

#### Scenario: Sleep outage recovers from current policy
- **WHEN** a later policy read succeeds after an outage
- **THEN** Health re-evaluates current policy and normal TTLs rather than replaying a stale sleep assertion

## ADDED Requirements

### Requirement: Bounded Suppression Read Failures
Routine attention consumers SHALL distinguish a successful no-suppressor/absent-invalid policy result from transient storage-read failure. An unavailable read SHALL hold/refuse the current routine delivery attempt rather than authorize immediate delivery or record a chosen destructive suppression. A durable hold SHALL carry an explicit finite deadline derived only from already-confirmed state: the original suppressor expiry and the existing consumer max-hold (where applicable), or the confirmed policy end; failure SHALL NOT extend that deadline or invent a fallback duration/timezone. If no confirmed finite anchor exists, or it expires while reads remain unavailable, the attempt SHALL finish unavailable without sending or fabricating a queued wake time; it SHALL NOT persist an indefinite hold or silently drop the underlying pending work. Recovery SHALL re-read current policy/context and re-evaluate normal TTLs, deduplication and delivery rules before release. Expiry/recovery SHALL NOT itself prove delivery or authorize a duplicate send. Deterministically authorized safety/urgent bypass SHALL remain ahead of routine suppression, including unavailable reads; source text or a model-assigned label SHALL NOT create safety authority. A failed best-effort attention-ledger write SHALL NOT turn an unavailable read into successful delivery.

ID: REQ-context-bus-016
Source: RFC 0009 §§Context Preamble for LLM Sessions, Owner Attention Policy Sleep Producer, Bounded Suppression Read Failures (proposed amendment bu-dhloi1.17); RFC 0038 D5
Scope: v1-mandatory

#### Scenario: Successful empty read permits ordinary evaluation
- **WHEN** policy/context reads succeed and no valid suppressor is active
- **THEN** normal delivery policy evaluation continues without an outage hold

#### Scenario: Routine store outage holds without delivery
- **WHEN** a routine suppression read fails with a confirmed unexpired hold anchor
- **THEN** the attempt does not send and any durable hold retains that finite anchor
- **AND** read failure is distinguishable from valid quiet hours, empty data and delivered status

#### Scenario: No known wake anchor refuses the attempt
- **WHEN** a routine read fails before any finite policy/context deadline is confirmed
- **THEN** the attempt ends unavailable without immediate delivery or an invented queued wake time

#### Scenario: Repeated outage cannot move the deadline
- **WHEN** routine reads keep failing across retries
- **THEN** the original confirmed deadline is not refreshed and no sleeping/DND assertion is minted

#### Scenario: Expired outage hold is not blind permission
- **WHEN** the retained deadline expires while current reads still fail
- **THEN** the finite hold ends and the attempt remains unavailable without sending or retaining an indefinite hold

#### Scenario: Recovery re-evaluates and releases only eligible work
- **WHEN** current reads recover with no remaining active suppression
- **THEN** normal pending-work evaluation resumes subject to existing expiry and deduplication rules
- **AND** recovery neither resurrects expired work nor duplicates a previously sent notification

#### Scenario: Authorized deterministic safety bypass survives outage
- **WHEN** an existing trusted deterministic safety/urgent path reaches its unchanged bypass contract while routine reads fail
- **THEN** routine hold logic does not block that authorized path
- **AND** third-party/model text cannot select the bypass
