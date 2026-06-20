## MODIFIED Requirements

### Requirement: [TARGET-STATE] Unified Calendar View

The calendar workspace dashboard SHALL provide a view toggle between user events,
butler-managed schedules/reminders, and read-only domain-context overlays from
specialist butlers, backed by an in-app projection table and the cross-schema
overlay view `calendar.v_overlay_contributions`.

#### Scenario: Projection status tracking

- **WHEN** the projection is queried
- **THEN** a staleness status is returned: `fresh`, `stale` (exceeds 2x sync interval), or `failed`

#### Scenario: Overlays view accepted as a valid `view` parameter

- **WHEN** `GET /api/calendar/workspace?view=overlays` is called
- **THEN** the request is accepted (not a 422 Unprocessable Entity)
- **AND** the response projects overlay contribution entries from
  `calendar.v_overlay_contributions` into the unified entry shape
- **BECAUSE** `"overlays"` is a valid member of the `view` enum alongside
  `"user"`, `"butler"`, and `"proposals"`

#### Scenario: Overlays view response envelope

- **WHEN** `GET /api/calendar/workspace?view=overlays` is called with `start`
  and `end` date parameters
- **THEN** the response includes:
  - `entries`: list of `UnifiedCalendarEntry` objects with
    `source_type="overlay_contribution"` for entries whose target date falls in
    `[start, end]`
  - `has_domain_context`: `true` if the view was reachable and at least one
    specialist contributed for the requested range; `false` otherwise

#### Scenario: Overlay entries are non-editable

- **WHEN** `view=overlays` entries are returned
- **THEN** every entry has `editable=false`
- **AND** no entry with `source_type="overlay_contribution"` appears in
  `view=user` or `view=butler` responses

#### Scenario: Overlays view is fail-open

- **WHEN** `calendar.v_overlay_contributions` is absent (pre-migration) or the
  projection query fails
- **THEN** the endpoint returns `entries: []` with `has_domain_context: false`
  rather than HTTP 500
- **AND** the failure is logged at WARNING level

### Requirement: UnifiedCalendarSourceType — overlay_contribution

The `UnifiedCalendarSourceType` literal SHALL include `"overlay_contribution"` as
a valid value, alongside the existing `provider_event`, `scheduled_task`,
`butler_reminder`, `manual_butler_event`, and `proposed_event` values.

#### Scenario: overlay_contribution source type is valid

- **WHEN** a `UnifiedCalendarEntry` is constructed with
  `source_type="overlay_contribution"`
- **THEN** validation passes without error

#### Scenario: overlay_contribution only emitted by the overlays view

- **WHEN** `view=user`, `view=butler`, or `view=proposals` is queried
- **THEN** no entries with `source_type="overlay_contribution"` are returned
- **BECAUSE** overlay contributions are a domain-context read layer, not user
  calendar events, butler-managed events, or inferred event proposals
