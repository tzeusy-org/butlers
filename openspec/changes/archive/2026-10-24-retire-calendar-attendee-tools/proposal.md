## Why

Accepted bu-qozovd.1 slice 1 retires the unused event-payload normalization island and the attendee tool group. The group is an intentional feature removal: default registration currently exposes both tools, so roster omission alone is not retirement authority. The applied design and unchanged AC5 explicitly authorize this slice before later response-owner and mutation-runner work.

## What Changes

- Delete CalendarEventPayloadInput, CalendarNormalizedEventTime, CalendarNormalizedEventPayload and normalize_event_payload after proving their tracked references are internal to the island.
- Remove calendar_add_attendees and calendar_remove_attendees from actual registration and presentation metadata.
- Retire their two dedicated home-calendar tests and update the existing exact surface gate, retaining every other tool and case population.
- Apply the retirement to the living calendar specification, docs and the active party-approval inventory without changing its approval rule.

## Capabilities

### Modified Capabilities

- module-calendar: retire the attendees group and its two standalone tools. Event attendee data, create/update attendee arguments and approval-bound self invitation responses remain.

## Impact

Calendar source, owning module tests, executable presentation metadata and calendar documentation/specification only. No runtime/provider/response/sync protocol, roster, API, scheduler, migration or package-layout change. This is slice 1 of five; the original bead and all six terminal acceptance criteria remain open.
