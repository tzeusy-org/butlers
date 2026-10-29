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

Official CI run 37257833164 on `3e815488f31fba553d80704e10ec6f8f58773b5c` completed successfully. Independently inspected JUnit proves all 103 affected cases pass, including the genuine own-role local projection, public/foreign controls and three fault/recovery parameters. The selected Relationship roster has 1280 passes and one existing posture-role skip (no credit); sender-identity SQL has 16 passes. Local Docker setup remains denied. The historical-query control changes only one SQL constant in the current function; full historical-job execution and provider/deployed population remain unproven.

Normal CLI archive applied both complete changed blocks and preserved all 27 original scenarios within the resulting 30-scenario canonical spec, including the bounded Purpose table correction. Only the two changed requirements gain metadata; whole-repository authoring debt remains. This archive completes the amendment tail. Independent review and terminal exact-tree hosted evidence on the final pushed head remain coordinator-owned prerequisites to merge and closure.
