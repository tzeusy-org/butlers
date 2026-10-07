## MODIFIED Requirements

### Requirement: Signal Vocabulary
The system SHALL define a fixed vocabulary of context signal types as a Python enum (`ContextSignal`). The vocabulary SHALL include: `traveling`, `sleeping`, `meeting`, `focused`, `exercising`, `sick`, `socializing`, `commuting`, `at_home`, `in_space`, `away`, and `dnd`. Signal types are validated at the application level before database writes.
- The vocabulary SHALL additionally include `working_location`, a separately qualified calendar-declared location. It SHALL NOT imply physical presence, `at_home`, `in_space`, commuting, DND, or delivery suppression.

ID: REQ-context-bus-001
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

#### Scenario: Valid signal type accepted
- **WHEN** `set_context()` is called with `signal_type="traveling"`
- **THEN** the signal is written to the database

#### Scenario: Invalid signal type rejected
- **WHEN** `set_context()` is called with `signal_type="partying"`
- **THEN** a `ValueError` is raised listing valid signal types
- **AND** no database write occurs


#### Scenario: Calendar working location is a distinct signal
- **WHEN** General asserts a valid calendar working location
- **THEN** the active context contains `working_location` with its bounded qualifier
- **AND** the assertion does not fabricate any other signal



### Requirement: TTL Semantics
Every context signal SHALL have an `expires_at` timestamp. There SHALL be no indefinite signals. Each signal type SHALL have a default TTL and a maximum TTL. If `expires_at` is not provided, the default TTL SHALL be applied from `set_at`. If the requested TTL exceeds the maximum, it SHALL be clamped to the maximum. A signal is considered **active** when `superseded_at IS NULL AND expires_at > now()`.
- Default and maximum TTLs:
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
- `working_location` SHALL have a default TTL of 12 hours and maximum of 24 hours. Its calendar producer SHALL request no later than both the event end and the end-exclusive boundary of the current day in the valid event timezone; the existing maximum clamp still applies. Away remains maximum 30 days, meeting maximum 4 hours, and focused maximum 8 hours.

ID: REQ-context-bus-003
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

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


#### Scenario: Working location expires at today boundary
- **WHEN** a current working-location event spans beyond the current local day
- **THEN** the published location expiry is no later than current local midnight, event end and the 24-hour clamp


#### Scenario: Multi-day away is bounded and reasserted
- **WHEN** an active timed OOO event ends more than 30 days after the producer run
- **THEN** away is bounded to the existing 30-day maximum
- **AND** a later successful run reasserts from the same still-active projection



### Requirement: Deterministic Context Producers
The system SHALL populate `public.user_context` with deterministic, zero-LLM
producers that run as scheduled `dispatch_mode="job"` handlers. Each producer
SHALL run on the butler that RFC 0009's write-permission matrix authorizes as
the single writer for the signal it produces. Producers SHALL be idempotent —
upserting the current signal via `set_context()` and clearing it via
`clear_context()` on the reverse transition — and every signal SHALL carry a
bounded TTL so a crashed producer never leaves a signal permanently pinned.
- The following producers SHALL exist:
- **calendar → meeting/focused/away and working_location** (writer `general`): derived from the
  currently-active event in the general butler's `calendar_events`.
- **home → at_home / in_space** (writer `home`): derived from fresh Home
  Assistant `person.*`/`device_tracker.*` presence in `ha_entity_snapshot`,
  scoped to the owner's configured entities; `in_space` additionally resolves
  which room/area the owner is currently in from those same entities.
- **travel → traveling** (writer `travel`): derived from a currently-underway
  trip in `travel.trips`.
- **health → sleeping** (writer `health`): derived from the owner-declared
  end-exclusive Owner Attention Policy window in `public.approvals_policy`.
- The calendar producer SHALL use confirmed active projected events to derive meeting/focused, typed out-of-office away, and separate working location according to Calendar Context Attendance and Typed Truth. It SHALL retain its existing registered zero-LLM General job and cadence. Successful observed absence SHALL retract its calendar assertions; unavailable input SHALL not be treated as absence.

ID: REQ-context-bus-004
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

#### Scenario: Calendar producer publishes meeting for a live event
- **WHEN** the general butler's `calendar_events` contains a confirmed,
  eligible default-family non-all-day event whose `[starts_at, ends_at)` window contains now
- **THEN** the calendar producer sets a `meeting` signal (or `focused` when the
  event title marks a focus block) with `set_by_butler = "general"` and the
  event's `ends_at` as `expires_at`

#### Scenario: Calendar producer clears when no event is live
- **WHEN** no confirmed, non-all-day event is currently active
- **AND** a successful calendar read finds no eligible confirmed default-family non-all-day event active and no eligible typed OOO, focusTime or working-location event
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


#### Scenario: Unavailable calendar input is not clear evidence
- **WHEN** the calendar query fails before a complete result is available
- **THEN** the run fails or reports unavailable without publishing or clearing derived state
- **AND** previous bounded signals can expire naturally



### Requirement: Calendar Producer Provenance Candidate Selection
The calendar `meeting`/`focused` producer SHALL remain the deterministic
general-butler producer added by `context-bus-producers`, but it SHALL derive a
signal only from an active confirmed eligible default-family human meeting candidate. It SHALL exclude
an all-day event, a legacy locally-midnight-aligned event spanning at least 24
hours, and an event with explicit
`metadata.butler_generated=true`. The producer SHALL clear its own meeting and
focused signals when no eligible event is active.
- `metadata.butler_generated` is the only generated-event exclusion authority;
source names, title prefixes, and inferred ownership SHALL NOT substitute for
it. A timed event without that explicit marker SHALL retain normal
meeting/focused behavior. Malformed metadata SHALL be treated as no explicit
generated assertion, and an invalid or missing timezone SHALL make only the
legacy-midnight inference unavailable; neither condition may raise or invent a
new context signal.
- The existing all-day and legacy-midnight exclusions SHALL remain for ordinary meeting/focus candidates. Explicit typed OOO and working-location selection is governed separately by Calendar Context Attendance and Typed Truth; a valid multi-day timed OOO does not become a legacy meeting merely because its boundaries are midnight. All branches SHALL retain the explicit generated-marker exclusion.

ID: REQ-context-bus-005
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

#### Scenario: Butler-generated event does not assert context

- **WHEN** the active projected event has `metadata.butler_generated=true`
- **THEN** the calendar producer does not set `meeting` or `focused` from it
- **AND** it clears any prior signal it owns when no other eligible event is
  active

#### Scenario: Equivalent human timed event remains a meeting candidate

- **WHEN** an active confirmed eligible default-family timed event has no explicit
  `metadata.butler_generated=true` marker
- **THEN** the producer continues to publish `meeting` or `focused` according
  to its title classifier and uses the event end as its expiry

#### Scenario: Legacy midnight event is not a meeting

- **WHEN** an active legacy default-family event has `all_day=false`, lasts at least 24 hours,
  and starts and ends at local midnight in its valid IANA timezone
- **THEN** the producer treats it as a non-meeting and does not assert context

#### Scenario: Malformed provenance degrades without an invented exclusion

- **WHEN** an otherwise valid timed event has malformed metadata or an invalid
  timezone
- **THEN** the producer does not raise
- **AND** malformed metadata alone does not exclude the event as generated
- **AND** an invalid timezone alone does not make the event a legacy all-day
  event



## ADDED Requirements

### Requirement: Calendar Context Attendance and Typed Truth
The deterministic General calendar producer SHALL publish context only from active confirmed eligible projection truth. It SHALL reuse the established self-attendee decline and transparency rule for meeting/focus and OOO, preserving tentative, needsAction, guest-decline and missing/malformed metadata compatibility. Explicit OOO has priority over meeting/focus and asserts away; explicit focusTime asserts focused structurally; valid workingLocation asserts only a separate working_location qualifier. Ordinary default-family events retain the existing title fallback and provenance exclusions. Calendar resource titles SHALL not create typed OOO or working-location authority. Successful absence SHALL clear meeting/focused and clear calendar-marked away/location, preserving unrelated writers and non-calendar away/location assertions.

- The producer SHALL capture one database wall-clock observation after obtaining its scoped serialization lock and reuse that observation for eligibility, expiry, every ordinary set and clear. Structural focusTime SHALL publish fixed `focus time` and omit its source title from context metadata; ordinary and future/default-family title fallback SHALL remain intact.

ID: REQ-context-bus-006
Source: RFC 0009; approved bu-s11n0s.4 Outcome and Behavior matrix; existing RSVP- and Transparency-Honest Conflict Candidate Filter
Scope: v1-mandatory


#### Scenario: Owner decline and transparent clear stale meeting
- **WHEN** a successful read finds only a self-declined or transparent ordinary event
- **THEN** no meeting/focused is asserted and existing producer meeting/focused is cleared


#### Scenario: Attended overlap wins over rejected rows
- **WHEN** a rejected event overlaps an attended opaque ordinary event
- **THEN** the eligible ordinary event supplies meeting or default-title focused


#### Scenario: Only self-decline excludes attendance
- **WHEN** a confirmed event has a guest decline, owner tentative or needsAction, or missing/malformed attendee metadata
- **THEN** the established compatibility rule retains the otherwise eligible event


#### Scenario: Typed OOO wins over an overlapping meeting
- **WHEN** an eligible timed outOfOffice and ordinary meeting are both active
- **THEN** away is asserted with qualifier out of office and meeting/focused are cleared


#### Scenario: Typed focus ignores title
- **WHEN** an eligible focusTime event has a title without focus markers
- **THEN** focused is asserted structurally and meeting is cleared


#### Scenario: Default title fallback and unknown behavior remain
- **WHEN** an ordinary default-family or future unknown-type timed event has a focus title
- **THEN** the existing title classifier still asserts focused


#### Scenario: Location does not assert a meeting
- **WHEN** a valid workingLocation event is active with no ordinary meeting or OOO
- **THEN** only working_location is asserted with the declared home/office/custom qualifier


#### Scenario: Location and attendance coexist
- **WHEN** an eligible ordinary meeting or OOO overlaps a valid location event
- **THEN** the attendance signal and separate working_location coexist without inventing physical presence


#### Scenario: Typed multi-day OOO is not legacy all-day
- **WHEN** an eligible timed OOO spans at least 24 hours with local-midnight boundaries
- **THEN** it remains typed away and never asserts meeting


#### Scenario: Unsupported typed shape cannot fabricate context
- **WHEN** typed OOO/focus has all-day shape, or typed working location has invalid/missing matching properties or timezone
- **THEN** no corresponding typed context assertion is fabricated from title or identifiers


#### Scenario: Observed absence is bounded and source scoped
- **WHEN** a successful read finds no eligible typed or ordinary source events
- **THEN** calendar meeting/focused and calendar-marked away/location are cleared while unrelated source/writer signals remain


#### Scenario: End boundary and deterministic ties are stable
- **WHEN** a selected event reaches its exact end or several same-start eligible rows exist
- **THEN** the ended event is inactive and surviving selection is deterministic under the specified stable ordering
