## ADDED Requirements

### Requirement: Calendar ICS Export

The dashboard API SHALL expose `GET /api/calendar/export/ics`, a read-only
data-portability export that streams the calendar workspace entries for a date
range as a `text/calendar` (iCalendar / VCALENDAR) file generated with the
`icalendar` library. The export SHALL reuse the existing workspace
read/projection and accept the same `view`, `butlers`, `sources`, `status`, and
`source_type` filters as `GET /api/calendar/workspace`, so the exported set
matches what the workspace read returns for the same inputs. Each entry SHALL
become a VEVENT whose `SUMMARY` is the entry title verbatim — the `BUTLER:`
prefix on butler-authored events MUST be preserved. The endpoint MUST perform no
provider write, MUST NOT spawn an LLM session, and MUST NOT require a database
migration. ICS subscribe (`webcal`) and `.ics` import are out of scope.

#### Scenario: Range exported as valid VCALENDAR

- **WHEN** `GET /api/calendar/export/ics` is called with a valid `view`, `start`,
  and `end`
- **THEN** the response is `text/calendar` with a `Content-Disposition: attachment`
  header and a body that parses as a valid VCALENDAR containing one VEVENT per
  workspace entry in the range, each with `UID`, `DTSTART`, `DTEND`, and `SUMMARY`

#### Scenario: Butler title prefix preserved

- **WHEN** the exported range includes a butler-authored event whose title begins
  with the `BUTLER:` prefix
- **THEN** that event's VEVENT `SUMMARY` retains the `BUTLER:` prefix verbatim

#### Scenario: Empty range yields an empty calendar

- **WHEN** the requested range contains no entries
- **THEN** the endpoint returns HTTP 200 with a valid VCALENDAR that contains no
  VEVENT components, rather than an error

#### Scenario: Invalid range rejected

- **WHEN** `end` is not after `start`, or the range exceeds the 90-day maximum,
  or a `status`/`source_type` facet value is unknown
- **THEN** the endpoint returns HTTP 400 and writes nothing; a request missing
  the required `start`/`end` parameters returns HTTP 422
