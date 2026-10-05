## Why

Interaction sync reads a public calendar table, while real named-schema migrations and the Calendar module project into Relationship's own schema. Existing public fixtures mask the mismatch. Failed calendar queries also advance the shared checkpoint, losing the bounded interval for normal recovery.

## What Changes

- Read `relationship.calendar_events` explicitly and surface query failures through existing errors/logs.
- Retain the shared checkpoint after a failed calendar query; a successful empty read advances normally.
- Replace the affected interaction/co-attended public fixtures with real named-schema chains, actual runtime roles and the existing local projection writer.
- Carry over every existing calendar/checkpoint scenario and clarify the source spelling and failed-read recovery obligation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `passive-interaction-sync`: correct the calendar projection target and clarify visible failed-read/checkpoint behavior.

## Impact

Relationship job, its owning fixtures/tests, existing mocked unit test, data-flow table spelling and Relationship implementation notes. No migrations, grants, bootstrap, Calendar module/provider changes, consent or foreign feature adoption.

## Verification State

The local real-Postgres attempt failed at Docker setup with permission denied before job execution. Real SQL proof remains NOT RUN until official hosted owning tests execute. The historical-query control changes only one SQL constant in the current function; it does not execute the full historical error/checkpoint implementation. Keep this active amendment until real proof permits the reviewed sync/archive tail; do not tick unexecuted SQL.
