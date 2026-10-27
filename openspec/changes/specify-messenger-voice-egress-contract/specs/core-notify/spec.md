## MODIFIED Requirements

### Requirement: Quiet Hours Delivery Gate
Before constructing the notification envelope, the `notify()` tool SHALL check the butler's `delivery_preferences` for quiet hours enforcement. If the current time (in the user's configured timezone) falls within quiet hours and the notification's priority is not `high`, the notification SHALL be deferred to the `deferred_notifications` table instead of being delivered immediately.

The explicit `voice` channel is the sole exception to the generic defer rule
above. Origin-side `delivery_preferences` SHALL NOT enqueue, coalesce, or later
flush a voice request. It SHALL route the request immediately to Messenger's
authoritative voice gate, where any active Owner Attention Policy quiet window
or `dnd`/`sleeping` context suppresses voice regardless of priority. This
immediate routing is not permission to play; it exists so Messenger can record
one physical-side-effect outcome and ask Switchboard for at most one linked
text-only non-voice fallback under `REQ-messenger-voice-egress-006` and
`REQ-messenger-voice-egress-009`. Non-voice behavior remains unchanged.

#### Scenario: Notification deferred during quiet hours
- **WHEN** `notify(channel="telegram", message="Weekly report", priority="medium")` is called
- **AND** delivery preferences have `quiet_hours_start="22:00"`, `quiet_hours_end="07:00"`, `timezone="America/New_York"`
- **AND** the current time in America/New_York is 23:15
- **THEN** the notification is stored in `deferred_notifications` with `deliver_at` set to the next 07:00 America/New_York
- **AND** the tool returns `{"status": "deferred", "deliver_at": "<ISO timestamp>", "notification_id": "<uuid>"}`

#### Scenario: High-priority bypasses quiet hours
- **WHEN** `notify(channel="telegram", message="Critical alert", priority="high")` is called during quiet hours
- **THEN** the notification is delivered immediately via the standard envelope pipeline
- **AND** quiet hours are NOT applied

#### Scenario: No delivery preferences configured
- **WHEN** `notify()` is called and no `delivery_preferences` row exists for this butler
- **THEN** the notification is delivered immediately regardless of time or priority (backward compatible)

#### Scenario: Quiet hours with channel override
- **WHEN** `notify(channel="email", message="Report", priority="medium")` is called
- **AND** delivery preferences have `override_channels={"email": {quiet_hours_start: "20:00", quiet_hours_end: "09:00"}}`
- **AND** the current time is 21:00 local
- **THEN** the email-specific quiet hours apply and the notification is deferred

#### Scenario: Voice reaches the authoritative suppress-or-play gate without deferral

- **WHEN** `notify(channel="voice", intent="reply", ...)` is called during an origin-side quiet-hours interval with any priority
- **THEN** the originating butler SHALL NOT insert a `deferred_notifications` row
- **AND** it SHALL route the request to Messenger for the unconditional current DND/quiet-hours decision
- **AND** Messenger SHALL suppress the voice attempt rather than playing it later

### Requirement: Channel Validation

The implementation SHALL provide the behavior described by this requirement.
Only `telegram` and `email` channels are currently supported. Unsupported channels produce an immediate error response.

The public `notify()` envelope-construction surface MUST accept only `telegram` and
`email`. Messenger `route.execute` termination MUST accept routed `notify.v1`
delivery through `telegram`, `email`, and `whatsapp`. Each surface MUST reject
channels outside its supported set immediately.

The two baseline statements above remain the complete established non-voice
contract and are extended additively: both surfaces MUST also accept explicit
`voice` only subject to the narrower `messenger-voice-egress` requirements.
Accepting `voice` does
not make it eligible for omitted-channel inference, preference selection,
generic recipient resolution, proactive delivery, defer/coalesce, or provider
handoff without the authenticated gates. All other channels remain unsupported.

#### Scenario: Supported notify channel

- **WHEN** `channel="telegram"` or `channel="email"` is passed to `notify()`
- **THEN** the notify tool proceeds with envelope construction

#### Scenario: Supported routed-delivery channel

- **WHEN** Messenger `route.execute` receives a valid routed `notify.v1` envelope with `channel="telegram"`, `channel="email"`, or `channel="whatsapp"`
- **THEN** it proceeds with channel-specific delivery validation

#### Scenario: Unsupported channel

- **WHEN** `channel="sms"` is passed to `notify()`
- **THEN** the tool returns `{"status": "error", "error": "Unsupported channel 'sms'..."}`

#### Scenario: Supported channel
- **WHEN** `channel="telegram"` or `channel="email"` is passed
- **THEN** the notify tool proceeds with envelope construction

#### Scenario: Explicit voice channel reaches voice-specific validation

- **WHEN** `channel="voice"` is explicitly passed to `notify()` or arrives in authenticated Messenger `route.execute`
- **THEN** envelope construction SHALL proceed to `messenger-voice-egress` initiation, lineage, provider, DND, presence, and replay validation
- **AND** acceptance at this syntax boundary SHALL NOT itself authorize a physical side effect

### Requirement: Preferred-Channel Resolution on Omitted Channel
When the caller omits `channel`, the notify tool SHALL resolve it before any
channel-dependent validation runs, and a caller-forced `channel` SHALL never be
overridden by this resolution:
- if `entity_id` is provided, resolve via `resolve_outbound_channel()` against
  the entity's active `prefers-channel` fact (see the `relationship-facts`
  spec), constrained to the deliverable set (`telegram`, `email`); if the
  preferred channel is not deliverable, or no preference exists, or the entity
  or database is unavailable, fall back to telegram, then email (first
  deliverable and reachable);
- if `entity_id` is not provided, default to `telegram` (the historical
  owner-page channel), preserving behavior for callers that relied on a
  channel always being present.

This requirement adds no new deliverable channels; the deliverable set remains
as defined by the Channel Validation requirement above.

For this requirement, `deliverable set` means the automatic selection set and
remains exactly `telegram` and `email` even though Channel Validation now accepts explicit
`voice`. The voice channel MUST NOT be selected from `prefers-channel`,
`entity_id`, contact facts, reachability fallback, owner default, or any omitted
channel. Voice is available only when the caller explicitly selects it and
satisfies `REQ-messenger-voice-egress-003`.

#### Scenario: Preference honored when deliverable
- **WHEN** a notification targets an `entity_id` whose entity has an active
  `prefers-channel="telegram"` fact, the entity has a telegram handle, and the
  caller did not force a different channel
- **THEN** the notification is sent on telegram

#### Scenario: Preference skipped when not deliverable
- **WHEN** a notification targets an `entity_id` whose entity prefers a channel
  not in the deliverable set (e.g. `prefers-channel="discord"`)
- **THEN** the preference is ignored without error
- **AND** channel selection falls back to telegram, then email

#### Scenario: No preference falls back unchanged
- **WHEN** a notification targets an `entity_id` whose entity has no active
  `prefers-channel` fact
- **THEN** channel selection falls back to telegram, then email, exactly as
  before the `prefers-channel` fact existed

#### Scenario: Explicit channel still wins
- **WHEN** the caller forces a specific deliverable channel and the entity has a
  different `prefers-channel` preference
- **THEN** the forced channel is used and the preference is not consulted

#### Scenario: No entity_id defaults to telegram
- **WHEN** `notify(message="Alert")` is called with no `channel` and no
  `entity_id`
- **THEN** `channel` resolves to `telegram`

#### Scenario: Voice is never inferred

- **WHEN** the caller omits `channel` and a person, entity, owner default, or preference record appears voice-capable
- **THEN** resolution SHALL still consider only telegram and email
- **AND** it SHALL NOT create a voice attempt or inspect the Messenger endpoint registry
