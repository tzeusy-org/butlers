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
the calendar source is stale, no rebase SHALL happen.

ID: REQ-module-calendar-053
Source: bu-5ii4gj.10 slices R-S1, R-S2; design "Reminders that follow their event"
Scope: v1-mandatory

#### Scenario: The reminder follows a move

- **WHEN** a reminder anchored 60 minutes before a 15:00 event is synced after the event moved to 17:00
- **THEN** its time SHALL be 16:00, one `reminder_rebased` receipt SHALL exist, and a second unchanged sync SHALL add none

#### Scenario: The reminder retires with its event

- **WHEN** the anchored event is cancelled
- **THEN** the reminder SHALL be cancelled with reason `anchor_cancelled` and the tick at the old time SHALL send nothing

### Requirement: The tick rechecks the anchor at fire

The reminder tick SHALL recheck an anchored reminder's anchor when it fires. It SHALL send only
while the anchor is still current. When the anchor cannot be determined the reminder SHALL still
fire, with an "as of" suffix, and SHALL NOT be dropped silently.

ID: REQ-module-calendar-054
Source: bu-5ii4gj.10 slice R-S3; design "Reminders that follow their event"
Scope: v1-mandatory

#### Scenario: A missing anchor row fires stamped

- **WHEN** the tick fires an anchored reminder whose anchor row is missing
- **THEN** the reminder SHALL be sent with an "as of" suffix

## MODIFIED Requirements

### Requirement: Dual-Lane Ownership and Authoritativeness

The projection SHALL use a dual-lane model to separate event authority. Each `calendar_sources` row has a `lane` field: `"user"` or `"butler"`. The lane determines which system is authoritative for an event's state.

- **`lane="user"`** — Provider-synced external events (meetings, appointments created by humans on Google Calendar). Google is the source of truth. The local projection faithfully mirrors whatever the provider reports on each sync cycle.
- **`lane="butler"`** — Internal scheduled tasks and reminders managed by the butler. The butler's `calendar_events` rows (for reminders with `source_kind='internal_reminders'`) and `scheduled_tasks` table are the source of truth. These are pushed outbound to Google for visibility. An edit the owner makes to one of them on Google is an instruction and is adopted (below); an edit by anyone else is not.

ID: REQ-module-calendar-055
Source: bu-5ii4gj.10 slices R-S4, gesture S2; design "The owner's gesture is an instruction"
Scope: v1-mandatory

#### Scenario: Butler-generated events in provider sync projection

- **WHEN** the provider sync processes events returned by an incremental or full sync
- **THEN** all events are persisted to the projection, including butler-generated ones
- **AND** butler-generated metadata (`butler_generated`, `butler_name`) is preserved in the projection row metadata for UI differentiation
- **BECAUSE** butler events created via `calendar_create_event` are user-lane workspace mutations (not internal scheduler items) and should be visible in the provider projection

#### Scenario: The owner's drag of a butler-owned one-off is adopted

- **WHEN** the owner moves or deletes a pushed one-off reminder or task on Google Calendar, and the owner is the only writer on the Butlers calendar
- **THEN** the local row SHALL take the provider's time (or the deleted reminder SHALL be dismissed), a receipt `owner_edit_adopted` with the prior state SHALL be written, and the owner SHALL get one notice with an undo that restores the prior state
- **AND** the outbound push SHALL NOT revert it, and SHALL send only on a difference, conditioned on the provider's etag so a concurrent owner edit is adopted instead of overwritten

#### Scenario: Butler overwrites external edits to butler-owned events

- **WHEN** a pushed butler-owned event is edited on Google by anyone but the sole-writer owner (the calendar has another writer, or its ACL cannot be read)
- **THEN** nothing SHALL be adopted, the push SHALL restore the butler's state with a receipt row, and the owner SHALL get a card asking whether to keep the edit

#### Scenario: A recurring item asks

- **WHEN** one occurrence of a recurring (cron or RRULE) butler-owned item is moved on Google
- **THEN** the owner SHALL be asked whether the change is for this occurrence or the whole series, and nothing SHALL be written until they answer

#### Scenario: External events faithfully track provider state

- **WHEN** a non-butler event is created or modified on Google Calendar
- **AND** the next sync cycle runs
- **THEN** the event is upserted into the `lane="user"` projection
- **AND** cancelled events are marked cancelled in the projection
- **AND** events no longer returned by a full sync are marked stale/cancelled

#### Scenario: Modifying butler events requires the butler

- **WHEN** a user wants to reschedule or edit a butler-managed event
- **THEN** they use butler MCP tools (`calendar_update_butler_event`, `calendar_update_event` with the event ID), which update both the local state and the Google Calendar event atomically
- **AND** a direct Google Calendar edit by anyone other than the sole-writer owner is not adopted
