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
failed while any are non-zero.

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

#### Scenario: An unchanged mirror sends nothing

- **WHEN** a mirrored task has not changed since its last push
- **THEN** the sync sends no update to the provider

### Requirement: The Default Calendar Is Fleet-Wide

`calendar_set_primary` SHALL write the fleet-shared default-target key before changing anything and
SHALL return an error, changing nothing, when that write fails. Every calendar butler SHALL re-read
that key when it resolves a tool call, ignoring a stored id that is not a discovered calendar and
keeping its last known value when the read fails, so a change made through one butler applies to
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
