## ADDED Requirements

### Requirement: Calendar ICS Subscribe Feed

The dashboard API SHALL expose `GET /api/calendar/subscribe.ics`, a read-only
live ICS feed an external calendar application can subscribe to (for example via
`webcal://`). On each fetch the endpoint SHALL re-render the **current** calendar
workspace entries — over a rolling window relative to the request time (the
default window is `now − 30 days … now + 60 days`, within the 90-day workspace
range cap) — as a `text/calendar` (iCalendar / VCALENDAR) body generated with the
`icalendar` library, reusing the same projection, the `view` / `butlers` /
`sources` / `status` / `source_type` filters, and the `BUTLER:` title-prefix
preservation as `GET /api/calendar/export/ics`. The response SHALL use
`Content-Disposition: inline` so clients treat it as a subscription feed rather
than a one-shot download. The endpoint MUST perform no provider write, MUST NOT
spawn an LLM session, MUST NOT require a database migration, and MUST be served
behind the same network boundary as the other dashboard/calendar endpoints (no
new unauthenticated surface, no per-feed token).

#### Scenario: Feed re-renders current workspace entries

- **WHEN** `GET /api/calendar/subscribe.ics` is fetched
- **THEN** the response is HTTP 200 `text/calendar` with a
  `Content-Disposition: inline` header and a body that parses as a valid
  VCALENDAR containing one VEVENT per current workspace entry in the rolling
  window, each with `UID`, `DTSTART`, `DTEND`, and `SUMMARY`

#### Scenario: Butler title prefix preserved

- **WHEN** the feed window includes a butler-authored event whose title begins
  with the `BUTLER:` prefix
- **THEN** that event's VEVENT `SUMMARY` retains the `BUTLER:` prefix verbatim

#### Scenario: Unknown facet rejected

- **WHEN** a `status` or `source_type` facet value is unknown
- **THEN** the endpoint returns HTTP 400 and writes nothing

### Requirement: Calendar ICS Import With Dedup

The dashboard API SHALL expose `POST /api/calendar/import/ics`, which accepts an
uploaded `.ics` file plus a target `butler_name` (and optional `calendar_id`),
parses its VEVENT components, and creates the events in the user calendar through
the existing `calendar_create_event` MCP path. The import SHALL be **deduplicated
against existing workspace entries** using the read-model's existing
`(title, starts_epoch)` collapse key: an event whose collapse key matches an
existing workspace entry — including every event when the same `.ics` is imported
again — MUST be skipped rather than creating a duplicate. Duplicate VEVENTs within
the uploaded file itself MUST also be collapsed. The endpoint SHALL return the
`parsed`, `imported`, and `skipped_duplicates` counts, where
`imported + skipped_duplicates == parsed`. The endpoint MUST require no database
migration.

#### Scenario: New events imported

- **WHEN** a `.ics` containing events not present in the workspace is imported
- **THEN** each such event is created via `calendar_create_event` and the
  response reports `imported` equal to the number of new events with
  `skipped_duplicates` of 0

#### Scenario: Re-importing the same file is a no-op

- **WHEN** a `.ics` whose events already exist in the workspace (the
  `(title, starts_epoch)` collapse key matches existing entries) is imported
- **THEN** no `calendar_create_event` call is made for those events and the
  response reports `imported` of 0 with `skipped_duplicates` equal to the parsed
  event count

#### Scenario: Empty or invalid payload rejected

- **WHEN** the uploaded file is empty or is not parseable as iCalendar
- **THEN** the endpoint returns HTTP 400 and creates nothing
