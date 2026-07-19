# Situational Context Bus

## ADDED Requirements

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
- **home → at_home** (writer `home`): derived from fresh Home Assistant
  `person.*`/`device_tracker.*` presence in `ha_entity_snapshot`.
- **travel → traveling** (writer `travel`): derived from a currently-underway
  trip in `travel.trips`.
- **health → sleeping** (writer `health`): derived from the owner-declared
  end-exclusive Owner Attention Policy window in `public.approvals_policy`.

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

### Requirement: Explicit Context MCP Tools
The system SHALL expose `check_context`, `set_context`, and `clear_context` MCP
tools on the general module so that explicit, user-initiated context signals
(primarily `dnd` and `sick`) that no deterministic producer can infer may be
read and written. Writes SHALL go through the general butler at confidence 1.0
and SHALL be subject to the same vocabulary and write-permission validation as
`set_context()`.

#### Scenario: Explicit dnd set via MCP tool
- **WHEN** the `set_context` MCP tool is called with `signal_type="dnd"`
- **THEN** a `dnd` signal is written with `set_by_butler = "general"` and
  confidence 1.0

#### Scenario: check_context returns active signals
- **WHEN** the `check_context` MCP tool is called
- **THEN** it returns the currently-active context signals as a list (empty when
  none are active)

#### Scenario: Invalid signal type rejected by the tool
- **WHEN** the `set_context` MCP tool is called with an out-of-vocabulary
  `signal_type`
- **THEN** the underlying `set_context()` raises `ValueError` and no signal is
  written
