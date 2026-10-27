# Situational Context Bus

## Purpose
Provides shared situational awareness across butlers via a `public.user_context` table. Butlers read and write context signals (traveling, sleeping, meeting, etc.) with TTL-based expiry, confidence scoring, and per-signal write permissions.

## Requirements

The adopted [bounded-read amendment](../../changes/specify-safe-context-rendering-and-bounded-read-failures/specs/context-bus/spec.md)
additionally governs suppression-read availability (REQ-context-bus-016). Its ADDED
requirement remains in that active change until archival; the implemented behavior
and operational qualifications are described in [context security](../../../docs/concepts/context-security.md).

Note on signatures: every context-bus function that takes a connection `pool` (`get_active_context`, `is_user_in_context`, `set_context`, `clear_context`) is implemented as an `async def` and must be awaited (`src/butlers/context_bus.py`). The function-call notation in the scenarios below omits the `async`/`await` keywords for brevity.

### Requirement: User Context Table Schema
The system SHALL maintain a `public.user_context` table in the shared PostgreSQL schema with the following columns: `id` (UUID PK), `signal_type` (TEXT NOT NULL), `value` (TEXT, nullable), `set_by_butler` (TEXT NOT NULL), `set_at` (TIMESTAMPTZ NOT NULL, default now()), `expires_at` (TIMESTAMPTZ NOT NULL), `confidence` (REAL NOT NULL, default 1.0, range 0.0-1.0), `metadata` (JSONB, nullable), and `superseded_at` (TIMESTAMPTZ, nullable). A UNIQUE constraint on `(signal_type, set_by_butler)` SHALL ensure each butler maintains at most one active signal per type. A partial index on `signal_type` WHERE `superseded_at IS NULL AND expires_at > now()` SHALL optimize active-signal queries.

#### Scenario: Table exists after migration
- **WHEN** the core-chain Alembic migration runs
- **THEN** the `public.user_context` table exists with all specified columns and constraints

#### Scenario: Unique constraint prevents duplicate signals
- **WHEN** a butler inserts a signal with a `(signal_type, set_by_butler)` pair that already exists
- **THEN** the insert uses ON CONFLICT DO UPDATE to overwrite the existing row

#### Scenario: Confidence range enforced
- **WHEN** a signal is inserted with confidence less than 0.0 or greater than 1.0
- **THEN** the database rejects the insert with a CHECK constraint violation

### Requirement: Signal Vocabulary
The system SHALL define a fixed vocabulary of context signal types as a Python enum (`ContextSignal`). The vocabulary SHALL include: `traveling`, `sleeping`, `meeting`, `focused`, `exercising`, `sick`, `socializing`, `commuting`, `at_home`, `in_space`, `away`, and `dnd`. Signal types are validated at the application level before database writes.

#### Scenario: Valid signal type accepted
- **WHEN** `set_context()` is called with `signal_type="traveling"`
- **THEN** the signal is written to the database

#### Scenario: Invalid signal type rejected
- **WHEN** `set_context()` is called with `signal_type="partying"`
- **THEN** a `ValueError` is raised listing valid signal types
- **AND** no database write occurs

### Requirement: Write Permissions
The system SHALL enforce per-signal write permissions at the application level. Each signal type SHALL have a defined set of authorized writer butlers. The `set_context()` function SHALL validate `(butler_name, signal_type)` against the permissions table before writing. Unauthorized writes SHALL raise `PermissionError`.

The permission mapping SHALL be:
- `traveling`: travel, general
- `sleeping`: health, general
- `meeting`: general
- `focused`: general
- `exercising`: health
- `sick`: health, general
- `socializing`: relationship, general
- `commuting`: travel, general
- `at_home`: travel, home, general
- `in_space`: home, general
- `away`: general
- `dnd`: general, switchboard

#### Scenario: Authorized butler writes signal
- **WHEN** the health butler calls `set_context(butler_name="health", signal_type="exercising", ...)`
- **THEN** the signal is written successfully

#### Scenario: Unauthorized butler rejected
- **WHEN** the finance butler calls `set_context(butler_name="finance", signal_type="exercising", ...)`
- **THEN** a `PermissionError` is raised with a message identifying the butler and signal type
- **AND** no database write occurs

#### Scenario: General butler has broad write access
- **WHEN** the general butler calls `set_context()` with any signal type
- **THEN** the write succeeds because general is authorized for all signal types

### Requirement: TTL Semantics
Every context signal SHALL have an `expires_at` timestamp. There SHALL be no indefinite signals. Each signal type SHALL have a default TTL and a maximum TTL. If `expires_at` is not provided, the default TTL SHALL be applied from the time of the assertion. If the requested TTL exceeds the maximum, it SHALL be clamped to the maximum. A signal is considered **active** when `superseded_at IS NULL AND expires_at > now()`.

Default and maximum TTLs:
- `traveling`: default 24h, max 30d
- `sleeping`: default 8h, max 12h
- `meeting`: default 1h, max 4h
- `focused`: default 2h, max 8h
- `exercising`: default 1h, max 3h
- `sick`: default 24h, max 14d
- `socializing`: default 3h, max 12h
- `commuting`: default 45min, max 3h
- `at_home`: default 12h, max 24h
- `in_space`: default 12h, max 24h
- `away`: default 12h, max 30d
- `dnd`: default 2h, max 24h

#### Scenario: Default TTL applied when expires_at omitted
- **WHEN** `set_context(signal_type="meeting")` is called without `expires_at`
- **THEN** `expires_at` is set to `now() + 1 hour` (the default TTL for meeting)

#### Scenario: TTL clamped to maximum
- **WHEN** `set_context(signal_type="meeting", expires_at=now()+timedelta(hours=10))` is called
- **THEN** `expires_at` is clamped to `now() + 4 hours` (the max TTL for meeting)

#### Scenario: Signal expires automatically
- **WHEN** a signal's `expires_at` timestamp is in the past
- **THEN** `is_user_in_context()` and `get_active_context()` exclude it from results
- **AND** the row remains in the table for audit purposes

### Requirement: Read Active Context
The system SHALL provide a `get_active_context(pool)` function that returns all currently active context signals (non-expired, non-superseded), ordered by confidence descending then set_at descending. All butlers SHALL be able to call this function. The return type SHALL be a list of `ContextEntry` dataclass instances containing `signal_type`, `value`, `set_by_butler`, `set_at`, `expires_at`, `confidence`, and `metadata`.

#### Scenario: Active signals returned
- **WHEN** there are two active signals (traveling with confidence 1.0, meeting with confidence 0.8) and one expired signal
- **THEN** `get_active_context()` returns a list of two `ContextEntry` objects
- **AND** the traveling signal appears first (higher confidence)

#### Scenario: No active signals
- **WHEN** all signals are expired or superseded
- **THEN** `get_active_context()` returns an empty list

#### Scenario: Expired signals excluded
- **WHEN** a signal has `expires_at < now()` and `superseded_at IS NULL`
- **THEN** it is not included in the result

#### Scenario: Superseded signals excluded
- **WHEN** a signal has `superseded_at IS NOT NULL` and `expires_at > now()`
- **THEN** it is not included in the result

### Requirement: Check Specific Context
The system SHALL provide an `is_user_in_context(pool, signal_type, min_confidence)` function that returns a boolean indicating whether the user is currently in a specific context. The `min_confidence` parameter SHALL default to 0.5. The check SHALL consider a signal active only if it is non-expired, non-superseded, and has confidence >= `min_confidence`.

#### Scenario: User is in context
- **WHEN** there is an active `traveling` signal with confidence 0.9 and `min_confidence=0.5`
- **THEN** `is_user_in_context(pool, "traveling")` returns `True`

#### Scenario: User is not in context
- **WHEN** there is no active `traveling` signal
- **THEN** `is_user_in_context(pool, "traveling")` returns `False`

#### Scenario: Low confidence signal filtered
- **WHEN** there is an active `meeting` signal with confidence 0.3 and `min_confidence=0.5`
- **THEN** `is_user_in_context(pool, "meeting")` returns `False`

#### Scenario: Custom min_confidence threshold
- **WHEN** there is an active `meeting` signal with confidence 0.3 and `min_confidence=0.2`
- **THEN** `is_user_in_context(pool, "meeting", min_confidence=0.2)` returns `True`

### Requirement: Set Context Signal
The system SHALL provide a `set_context(pool, butler_name, signal_type, expires_at, value, confidence, metadata)` function that writes or updates a context signal. The function SHALL validate write permissions, validate the signal type against the vocabulary enum, clamp the TTL to the maximum, and perform an upsert via `INSERT ... ON CONFLICT DO UPDATE`. The `set_at` field marks the start of the current activation: it SHALL be reset to the current time when the value changes, the row was superseded, or the row had expired, and SHALL be kept on an unchanged re-assertion of an active row. This keep rule applies to every signal type except `dnd`: for `dnd`, `set_context` delegates to the canonical gateway `public.context_dnd_mutate`, which resets `set_at` on every assertion. `expires_at` SHALL be refreshed on every assertion, so the TTL is measured from the latest assertion. If the signal was previously superseded, the upsert SHALL clear `superseded_at` (set to NULL).

#### Scenario: New signal created
- **WHEN** no existing signal exists for the `(signal_type, set_by_butler)` pair
- **THEN** a new row is inserted with the provided values

#### Scenario: Existing signal updated
- **WHEN** a signal already exists for the `(signal_type, set_by_butler)` pair and `set_context()` is called with a different value
- **THEN** the existing row is updated with new `value`, `expires_at`, `confidence`, `metadata`, and `superseded_at = NULL`, and `set_at` is reset to the current time because the value changed

#### Scenario: Unchanged active signal re-asserted
- **WHEN** a signal is active (not superseded, not expired) and `set_context()` is called with the same `value`
- **THEN** `set_at` is kept and `expires_at` is refreshed, so consumers measuring continuous activity are not reset

#### Scenario: Expired signal re-asserted
- **WHEN** a signal's row has expired (`expires_at` is not after now) and `set_context()` is called with the same `value`
- **THEN** `set_at` is reset to the current time, starting a new activation

#### Scenario: Previously cleared signal re-activated
- **WHEN** a signal was cleared (superseded_at is set) and `set_context()` is called again
- **THEN** the row is updated and `superseded_at` is set to NULL

### Requirement: Clear Context Signal
The system SHALL provide a `clear_context(pool, butler_name, signal_type)` function that explicitly clears a signal before its TTL expires by setting `superseded_at = now()`. A butler SHALL only be able to clear signals it set (matched by `set_by_butler`). The function SHALL be idempotent -- clearing an already-cleared or expired signal is a no-op.

#### Scenario: Active signal cleared
- **WHEN** the health butler calls `clear_context(pool, "health", "exercising")`
- **THEN** the signal's `superseded_at` is set to the current time
- **AND** the signal no longer appears in `get_active_context()` results

#### Scenario: Clearing another butler's signal is a no-op
- **WHEN** the general butler calls `clear_context(pool, "general", "exercising")` but the signal was set by health
- **THEN** no rows are updated (the WHERE clause matches `set_by_butler`)

#### Scenario: Clearing a non-existent signal is a no-op
- **WHEN** `clear_context()` is called for a signal that does not exist
- **THEN** no error is raised and no rows are affected

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

### Requirement: Suppressing Context Wake Anchor
The system SHALL require consumers that defer routine owner-default
notifications for `dnd` or `sleeping` to use only active context entries and
compute a wake anchor as the latest `expires_at` among all active suppressing
entries. The selected ledger reason SHALL remain deterministic: `dnd` takes
precedence over `sleeping`, but reason precedence SHALL NOT shorten the wake
anchor. This is a read-only use of existing TTL-bearing context data and SHALL
not add a producer, schema field, or cross-butler write.

#### Scenario: DND reason preserves a later sleeping expiry
- **WHEN** active DND expires at 09:00 UTC and active sleeping expires at 10:00
  UTC
- **THEN** the suppressing-context result selects `dnd` as its reason
- **AND** it returns 10:00 UTC as the wake anchor

#### Scenario: Only active suppressors contribute to the wake anchor
- **WHEN** a DND entry is superseded or expired and a sleeping entry is active
- **THEN** the result ignores the inactive DND entry and uses the sleeping
  entry's expiry

#### Scenario: No suppressor produces no wake anchor
- **WHEN** no active DND or sleeping entry exists
- **THEN** the suppressing-context result is absent and the notify caller can
  continue its normal policy evaluation

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

### Requirement: Calendar Producer Provenance Candidate Selection

The calendar `meeting`/`focused` producer SHALL remain the deterministic
general-butler producer added by `context-bus-producers`, but it SHALL derive a
signal only from an active confirmed human meeting candidate. It SHALL exclude
an all-day event, a legacy locally-midnight-aligned event spanning at least 24
hours, and an event with explicit
`metadata.butler_generated=true`. The producer SHALL clear its own meeting and
focused signals when no eligible event is active.

`metadata.butler_generated` is the only generated-event exclusion authority;
source names, title prefixes, and inferred ownership SHALL NOT substitute for
it. A timed event without that explicit marker SHALL retain normal
meeting/focused behavior. Malformed metadata SHALL be treated as no explicit
generated assertion, and an invalid or missing timezone SHALL make only the
legacy-midnight inference unavailable; neither condition may raise or invent a
new context signal.

#### Scenario: Butler-generated event does not assert context

- **WHEN** the active projected event has `metadata.butler_generated=true`
- **THEN** the calendar producer does not set `meeting` or `focused` from it
- **AND** it clears any prior signal it owns when no other eligible event is
  active

#### Scenario: Equivalent human timed event remains a meeting candidate

- **WHEN** an active confirmed timed event has no explicit
  `metadata.butler_generated=true` marker
- **THEN** the producer continues to publish `meeting` or `focused` according
  to its title classifier and uses the event end as its expiry

#### Scenario: Legacy midnight event is not a meeting

- **WHEN** an active legacy event has `all_day=false`, lasts at least 24 hours,
  and starts and ends at local midnight in its valid IANA timezone
- **THEN** the producer treats it as a non-meeting and does not assert context

#### Scenario: Malformed provenance degrades without an invented exclusion

- **WHEN** an otherwise valid timed event has malformed metadata or an invalid
  timezone
- **THEN** the producer does not raise
- **AND** malformed metadata alone does not exclude the event as generated
- **AND** an invalid timezone alone does not make the event a legacy all-day
  event

### Requirement: Home Presence Source Health Gate

The deterministic Home presence producer SHALL confirm the Home Assistant
source is healthy before deriving `at_home` from `ha_entity_snapshot`.

#### Scenario: HA outage leaves owner presence unmeasurable

- **WHEN** `run_home_presence_context_producer` runs while
  `ha_source_health` is not `healthy` for `home_assistant`, or no recent
  successful-contact timestamp exists
- **THEN** it SHALL return `presence="unmeasurable"` without querying
  `ha_entity_snapshot`
- **AND** it SHALL neither assert nor clear `at_home`, so any prior signal is
  left to expire through its bounded TTL

#### Scenario: Healthy HA source preserves presence derivation

- **WHEN** `ha_source_health` records a recent `status='healthy'` contact for
  `home_assistant`
- **THEN** the producer SHALL continue to apply its configured-owner,
  freshness, and home-versus-away rules to `ha_entity_snapshot`
