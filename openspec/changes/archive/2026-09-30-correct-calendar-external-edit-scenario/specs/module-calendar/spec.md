## MODIFIED Requirements

### Requirement: Dual-Lane Ownership and Authoritativeness

The projection SHALL use a dual-lane model to separate event authority. Each `calendar_sources` row has a `lane` field: `"user"` or `"butler"`. The lane determines which system is authoritative for an event's state.

- **`lane="user"`** — Provider-synced external events (meetings, appointments created by humans on Google Calendar). Google is the source of truth. The local projection faithfully mirrors whatever the provider reports on each sync cycle.
- **`lane="butler"`** — Internal scheduled tasks and reminders managed by the butler. The butler's `calendar_events` rows (for reminders with `source_kind='internal_reminders'`) and `scheduled_tasks` table are the source of truth. These are pushed outbound to Google for visibility but Google is never read back as authoritative for them.

#### Scenario: Butler-generated events in provider sync projection

- **WHEN** the provider sync processes events returned by an incremental or full sync
- **THEN** all events are persisted to the projection, including butler-generated ones
- **AND** butler-generated metadata (`butler_generated`, `butler_name`) is preserved in the projection row metadata for UI differentiation
- **BECAUSE** butler events created via `calendar_create_event` are user-lane workspace mutations (not internal scheduler items) and should be visible in the provider projection

#### Scenario: Butler overwrites external edits to butler-owned events

- **WHEN** a user manually moves or edits a pushed butler-owned event (a scheduled task or internal reminder) directly on Google Calendar
- **AND** the next sync and push cycle runs
- **THEN** the provider sync persists the edited copy to the projection with its butler-generated metadata, like any other butler-generated event (there is no butler-generated filter)
- **AND** the outbound push of internal events overwrites the Google event with the butler's local state (title, start/end from `scheduled_tasks` or `calendar_events` with `source_kind='internal_reminders'`)
- **AND** the projection converges on the restored state on the following sync
- **BECAUSE** the butler's database is authoritative for butler-owned events; Google is a read-only mirror for them

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
- **AND** direct Google Calendar edits will be silently reverted on the next sync cycle
