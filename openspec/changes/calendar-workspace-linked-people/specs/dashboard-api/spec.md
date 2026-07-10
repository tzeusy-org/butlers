## ADDED Requirements

### Requirement: Calendar Workspace Linked-People Hydration

The dashboard API SHALL hydrate the people linked to each **existing** calendar event on the workspace read so linked-people avatars persist beyond creation time. `GET /api/calendar/workspace?view=user` (and `view=butler`) SHALL populate a new additive `linked_people` field on each `UnifiedCalendarEntry` — a list of `{entity_id, display_label}` resolved from the per-butler-schema `calendar_event_entities` junction joined to the shared `public.entities` (the `canonical_name` display label). The resolution MUST be a single batch join per targeted schema (no N+1 lookup per entry), scoped to the events on the returned page and to the schemas that produced those rows.

The read MUST be fail-open and honest: a resolution-query failure SHALL NOT drop an entry or silently omit its people. Instead the response SHALL carry a `people_source_available` boolean (following the fleet degraded-source convention) that is `true` when resolution ran cleanly (including a genuine "no links") and `false` when at least one targeted schema's resolution query failed, so the frontend renders a "people unavailable" indicator rather than reading empty `linked_people` as "no one is linked". No new table or migration is introduced — `calendar_event_entities` already stores the links.

#### Scenario: Existing event carries resolved linked people
- **WHEN** `GET /api/calendar/workspace?view=user&start=...&end=...` is called and a returned event has rows in its schema's `calendar_event_entities`
- **THEN** that entry's `linked_people` lists each linked person as `{entity_id, display_label}`, with `display_label` resolved from `public.entities.canonical_name`
- **AND** the people are resolved via one batch join per schema keyed on `event_id` (not a per-entry query)
- **AND** the response includes `people_source_available: true`

#### Scenario: A link to a missing entity still surfaces
- **WHEN** an event links an `entity_id` whose `public.entities` row is absent or tombstoned (the `LEFT JOIN` yields a NULL name)
- **THEN** the person is still included in `linked_people` with `display_label` set to `"Unknown"` rather than being dropped

#### Scenario: Resolution failure is flagged, not silently empty
- **WHEN** the entity-resolution query fails for at least one targeted schema
- **THEN** the entries still render, `people_source_available` is `false`, and the affected entries' `linked_people` are empty rather than the endpoint returning HTTP 500
- **AND** the frontend shows a "people unavailable" indicator instead of treating empty `linked_people` as "no one is linked"

#### Scenario: Backward-compatible additive shape
- **WHEN** a client that predates this change reads the workspace response
- **THEN** it observes the prior `UnifiedCalendarEntry` shape unchanged — `linked_people` defaults to `[]` and `people_source_available` defaults to `true` (both optional/additive)
