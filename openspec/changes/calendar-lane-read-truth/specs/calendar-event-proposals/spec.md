## MODIFIED Requirements

### Requirement: Proposals Workspace Projection View

The capability SHALL extend the calendar workspace read endpoint so
`GET /api/calendar/workspace?view=proposals` projects pending proposals into the
unified entry shape, tagged with the `source_type` value `"proposed_event"`.

#### Scenario: Proposals view returns pending proposals

- **WHEN** `GET /api/calendar/workspace?view=proposals` is called with a `start`/`end` range
- **THEN** each `calendar_event_proposals` row with `status="pending"` whose `start_at` falls in the range is returned as a unified entry with `source_type="proposed_event"`
- **AND** the entry is non-editable in place (`editable=false`)
- **AND** the entry's `metadata` carries `confidence`, `source_snippet`, and the `source_event_id` provenance link
- **AND** accepted and dismissed proposals are excluded

#### Scenario: Proposals view is fail-open

- **WHEN** the `calendar_event_proposals` table is absent (calendar module disabled or pre-migration), any butler schema's proposals read fails, or the projection query fails
- **THEN** the endpoint fails open to HTTP 200 (never a 500) with whatever entries could be read
- **AND** `proposals_available` is `false`, so the dashboard renders "Proposals unavailable" and never "Nothing awaiting your review"
- **AND** the failure is logged

#### Scenario: A malformed proposal row is counted, not hidden

- **WHEN** a pending proposal row cannot be normalised (for example it has no start or end timestamp)
- **THEN** it is left out of `entries`, `proposals_dropped_rows` counts it, and the dropped row is logged
