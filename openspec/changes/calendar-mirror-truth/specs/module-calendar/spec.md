## ADDED Requirements

### Requirement: Scheduled-Task Mirrors Keep Their Truth

A scheduled task mirrored to the Butlers calendar SHALL keep the caller's stable handle in
`calendar_link_id` and SHALL reserve `calendar_event_id` for the provider's event id, so a
butler-authored task is created on the provider instead of being patched at an id that does not
exist. Each mirrored task SHALL record `mirror_state` (`pending`, `mirrored`, `failed` or
`retiring`), its last error and attempt count. A failed create or update SHALL set `failed` and a
failed delete of a disabled task's provider copy SHALL set `retiring`; both SHALL keep the provider
id, log at warning and be retried on every sync, and the provider id SHALL be cleared only after the
provider copy is deleted or reported missing. A task whose provider copy matches what was last sent
SHALL send nothing. A provider copy reported missing on update SHALL be created again. Calendar sync
health SHALL report the failed and retiring counts and SHALL mark the internal scheduler source
failed while any are non-zero, and the dashboard workspace source freshness SHALL carry the same
counts and report the internal scheduler source `failed` while any are non-zero.

A failed mirror SHALL be retried with a bounded exponential backoff (a minute doubling to an hour)
and the warning SHALL be logged only when the row enters a failure state, not on every retry. A
failed create or update SHALL park after 8 attempts, no longer retried until what would be sent changes
(not merely until the row is touched), and still count as failed. A retiring row SHALL keep retrying at the capped interval. A delete answered
404 or 410 SHALL count as deleted.

A create SHALL be idempotent: the provider event id SHALL be derived from the task id, passes over
a module's mirrors SHALL NOT overlap, and a create answered 409 SHALL adopt the existing event. A
failure to record one row SHALL NOT stop the remaining rows.

Deleting a scheduled task, through `calendar_delete_butler_event`, `schedule_delete` or
`deadline_delete`, SHALL NOT strand its provider copy. A task that has, or may have, a provider copy
SHALL be retired: disabled, hidden from listings and lookups, and kept as `retiring` until the
mirror has deleted the provider copy, then purged. A task that was never mirrored SHALL be deleted
outright. A retired task SHALL NOT be dispatched, claimed by a tick that read it before the retire,
triggered, edited, re-enabled, listed or projected as an event. A task deleted while its create is in flight SHALL have the just-created event removed.

ID: REQ-module-calendar-048
Source: bu-5ii4gj.4 (run 26 move 4), slice B
Scope: v1-mandatory

#### Scenario: A butler-authored task is mirrored and stays addressable

- **WHEN** a task created with a link id is pushed
- **THEN** the provider event is created, its id is stored apart from the link id, and both resolve the task

#### Scenario: A failed push is visible and retried

- **WHEN** the provider create or update fails
- **THEN** the row is `failed` with its error and attempt count, sync health reports it, and the next sync retries

#### Scenario: A failed delete leaves no unaddressable ghost

- **WHEN** a disabled task's provider delete fails
- **THEN** the provider id stays on the row as `retiring` and is deleted on a later sync before it is cleared

#### Scenario: Deleting a mirrored task leaves no ghost

- **WHEN** a mirrored task is deleted
- **THEN** its row is retired and hidden, the mirror deletes the provider copy, and only then is the row purged
- **AND** a delete that fails or is not yet allowed by the backoff leaves the row `retiring` and counted

#### Scenario: A crash after a create does not duplicate the event

- **WHEN** a create succeeded on the provider but its row was not recorded
- **THEN** the next pass gets 409 for the derived id and adopts the event, so one provider event exists

#### Scenario: A failing mirror backs off and parks

- **WHEN** a mirror keeps failing
- **THEN** it is not retried before its backoff elapses, warns once on entry, parks after 8 attempts, and still counts as failed

#### Scenario: An unchanged mirror sends nothing

- **WHEN** a mirrored task has not changed since its last push
- **THEN** the sync sends no update to the provider

### Requirement: The Default Calendar Is Fleet-Wide

`calendar_set_primary` SHALL write the fleet-shared default-target key before changing anything and
SHALL return an error, changing nothing, when that write fails. Every calendar butler SHALL re-read
that key when it resolves a tool call, re-reading the provider's calendar list (at most every five minutes) when the
stored id is unknown and ignoring it if it is still not a discovered calendar, keeping its last
known value when the read fails and never overwriting a newer local choice with an older read, so a change made through one butler applies to
all without a restart.

ID: REQ-module-calendar-049
Source: bu-5ii4gj.4 (run 26 move 4), slice B
Scope: v1-mandatory

#### Scenario: Another butler follows set_primary

- **WHEN** one butler sets the primary calendar and a second butler then resolves a call
- **THEN** the second butler uses the new default calendar

#### Scenario: A failed save changes nothing

- **WHEN** the shared key cannot be written
- **THEN** the tool returns an error and the in-memory default is unchanged
