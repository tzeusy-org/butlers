## ADDED Requirements

### Requirement: Reminders can be anchored to an event occurrence

`reminder_create` SHALL accept an optional anchor: a provider event identity, the occurrence's
original start and an offset in minutes (negative means before), bounded to 14 days either way. The
anchor SHALL be stored in the reminder's event metadata and the reminder's time derived from it. An
anchor naming an occurrence that is not in this schema's projection SHALL be refused. When the
anchored occurrence moves, the reminder SHALL be rebased in the same transaction with one
`reminder_rebased` receipt, idempotent on the anchor's revision. When the occurrence is cancelled
or the owner declines it, the reminder SHALL be cancelled with reason `anchor_cancelled`. A
recurring anchor SHALL follow one occurrence by original start and leave the others alone. While
the calendar source is stale, no rebase SHALL happen. A rebase or a retirement SHALL produce exactly
one insight candidate through the broker, never a direct notify: a digest line, or a timed line when
the reminder's new time is under 2 hours away. That candidate SHALL carry no premise, because
`occurrence_still_changed` answers a different question (whether an accepted occurrence still
differs from what the owner agreed to), not whether this reminder's time is still right.

ID: REQ-module-calendar-057
Source: bu-5ii4gj.10 slices R-S1, R-S2; design "Reminders that follow their event"
Scope: v1-mandatory

#### Scenario: The reminder follows a move

- **WHEN** a reminder anchored 60 minutes before a 15:00 event is synced after the event moved to 17:00
- **THEN** its time SHALL be 16:00, one `reminder_rebased` receipt SHALL exist, and a second unchanged sync SHALL add none

#### Scenario: A rebase tells the owner once

- **WHEN** an anchored reminder is rebased, and the same sync is replayed
- **THEN** exactly one insight candidate SHALL exist for it, in the digest lane (or the timed lane when under 2 hours away), carrying no premise, and the replay SHALL add none

#### Scenario: The reminder retires with its event

- **WHEN** the anchored event is cancelled
- **THEN** the reminder SHALL be cancelled with reason `anchor_cancelled` and the tick at the old time SHALL send nothing

### Requirement: The tick rechecks the anchor at fire

The reminder tick SHALL recheck an anchored reminder's anchor when it fires. It SHALL send only
while the anchor is still current. When the anchor cannot be determined the reminder SHALL still
fire, with an "as of" suffix, and SHALL NOT be dropped silently.

ID: REQ-module-calendar-058
Source: bu-5ii4gj.10 slice R-S3; design "Reminders that follow their event"
Scope: v1-mandatory

#### Scenario: A missing anchor row fires stamped

- **WHEN** the tick fires an anchored reminder whose anchor row is missing
- **THEN** the reminder SHALL be sent with an "as of" suffix

## MODIFIED Requirements

### Requirement: Dual-Lane Ownership and Authoritativeness

The projection SHALL use a dual-lane model to separate event authority. Each `calendar_sources` row has a `lane` field: `"user"` or `"butler"`. The lane determines which system is authoritative for an event's state.

- **`lane="user"`** — Provider-synced external events (meetings, appointments created by humans on Google Calendar). Google is the source of truth. The local projection faithfully mirrors whatever the provider reports on each sync cycle.
- **`lane="butler"`** — Internal scheduled tasks and reminders managed by the butler. The butler's `calendar_events` rows (for reminders with `source_kind='internal_reminders'`) and `scheduled_tasks` table are the source of truth. These are pushed outbound to Google for visibility but Google is never read back as authoritative for them. An edit made to one of them on Google is never reverted silently: the revert leaves a receipt the owner can see.

ID: REQ-module-calendar-059
Source: bu-5ii4gj.10 slice gesture S2 (revert with receipt); design "The owner's gesture is an instruction"
Scope: v1-mandatory

#### Scenario: Butler-generated events in provider sync projection

- **WHEN** the provider sync processes events returned by an incremental or full sync
- **THEN** all events are persisted to the projection, including butler-generated ones
- **AND** butler-generated metadata (`butler_generated`, `butler_name`) is preserved in the projection row metadata for UI differentiation
- **BECAUSE** butler events created via `calendar_create_event` are user-lane workspace mutations (not internal scheduler items) and should be visible in the provider projection

#### Scenario: Butler overwrites external edits to butler-owned events

- **WHEN** a user manually moves or edits a pushed butler-owned event (a scheduled task or internal reminder) directly on Google Calendar
- **AND** the next sync and push cycle runs
- **THEN** the provider sync persists the edited copy to the projection with its butler-generated metadata, like any other butler-generated event (there is no butler-generated filter)
- **AND** the outbound push of internal events overwrites the Google event with the butler's local state, and writes one receipt row recording what the edit was and that it was reverted, so the owner can be told rather than the event silently snapping back
- **AND** the projection converges on the restored state on the following sync
- **BECAUSE** the butler's database is authoritative for butler-owned events; Google is a mirror for them

#### Scenario: External events faithfully track provider state

- **WHEN** a non-butler event is created or modified on Google Calendar
- **AND** the next sync cycle runs
- **THEN** the event is upserted into the `lane="user"` projection
- **AND** cancelled events are marked cancelled in the projection
- **AND** events no longer returned by a full sync are marked stale/cancelled

#### Scenario: Modifying butler events requires the butler

- **WHEN** a user wants to reschedule or edit a butler-managed event
- **THEN** they must use butler MCP tools (`calendar_update_butler_event`, `calendar_update_event` with the event ID)
- **AND** the butler updates both its local state and the Google Calendar event atomically
- **AND** a direct Google Calendar edit is reverted on the next sync cycle with a receipt, never silently

#### Scenario: A revert tells the owner once

- **WHEN** an edit on Google to a butler-owned event is reverted
- **THEN** one receipt row SHALL exist and exactly one insight candidate SHALL be proposed through the broker (the same line as a reminder rebase: digest lane, or timed when the event is under 2 hours away), and a replayed push SHALL add neither
