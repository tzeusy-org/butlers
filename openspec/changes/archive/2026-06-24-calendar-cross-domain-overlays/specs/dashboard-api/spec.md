## ADDED Requirements

### Requirement: Calendar Overlay Projection

The dashboard API SHALL extend the calendar workspace read endpoint so `GET /api/calendar/workspace?view=overlays` projects cached overlay contributions from `calendar.v_overlay_contributions` into `UnifiedCalendarEntry` rows tagged with a new `source_type` value `"overlay_contribution"`. The projection MUST be a pure read of the precomputed view — no LLM session and no cross-schema fan-out at request time — and MUST be fail-open: a missing view, a missing contributing specialist `state` table, or a projection-query failure returns `entries: []` with `has_domain_context: false` rather than HTTP 500.

#### Scenario: Overlays view projects cached entries
- **WHEN** `GET /api/calendar/workspace?view=overlays` is called with a `start`/`end` range
- **THEN** each overlay contribution entry whose target date falls within `[start, end]` is returned as a `UnifiedCalendarEntry` with `source_type="overlay_contribution"`, `editable=false`, `start_at` set to the entry's target date, `title` set to the entry's `label`, and `metadata` carrying `kind`, `priority`, `source_butler` (from the view's hardcoded `butler` column), and the entry's `meta`
- **AND** the response includes `has_domain_context: true`
- **AND** no LLM session is invoked while serving the request

#### Scenario: Overlay entries never appear in user or butler views
- **WHEN** `GET /api/calendar/workspace?view=user` or `view=butler` is called
- **THEN** no entries with `source_type="overlay_contribution"` appear in the response
- **BECAUSE** overlays are a read-only domain-context layer, not user-owned or butler-owned calendar events

#### Scenario: Overlays view is fail-open and empty when none
- **WHEN** `calendar.v_overlay_contributions` is absent (pre-migration), a contributing specialist's `state` table is missing, or the projection query fails
- **THEN** the endpoint returns `entries: []` with `has_domain_context: false` rather than HTTP 500
- **AND** the failure is logged at WARNING level

#### Scenario: Malformed contribution skipped
- **WHEN** a row read from the view has a `value->>'butler'` that does not match the view's hardcoded `butler` source column, or is missing required envelope fields (`butler`, `date`, `has_entries`)
- **THEN** that contribution is skipped with a warning log and excluded from `entries`
- **AND** `has_domain_context` reflects only the valid contributions

### Requirement: Meeting-Prep Rail Read

The dashboard API SHALL expose a read endpoint that returns the meeting-prep context (attendees, relationship notes, last-met) for a selected calendar event. This read MUST be sourced exclusively from precomputed contribution data behind the cached-view discipline; it MUST NOT issue a direct cross-butler query (e.g. `SELECT ... FROM relationship.*`) at request time and MUST NOT spawn an LLM session. When prep contributions for the event do not exist, the endpoint SHALL return a structured empty payload (honest empty-state), never HTTP 500.

#### Scenario: Prep rail returns precomputed context
- **WHEN** the meeting-prep rail read is called for an event that has precomputed prep contributions
- **THEN** the response carries the event's attendees, relationship notes, and last-met context drawn from the precomputed contribution data
- **AND** no direct cross-butler read and no LLM session occur while serving the request

#### Scenario: Prep rail honest empty-state
- **WHEN** the prep rail read is called for an event with no precomputed prep contribution (co-attended-edge / contact-link coverage not yet populated)
- **THEN** the endpoint returns a structured empty payload (e.g. empty attendee/notes lists with an availability flag), not HTTP 500
- **BECAUSE** the prep rail renders empty for events lacking coverage rather than fabricating context or reading sibling schemas live

#### Scenario: Prep rail never reads sibling schemas on demand
- **WHEN** the prep rail read is served
- **THEN** it reads only contribution-sourced cached data and issues no on-demand `SELECT` against `relationship.*`, `health.*`, or any other sibling schema
- **BECAUSE** RFC-0020 rejected the on-demand cross-schema read and the per-open LLM synthesis paths

### Requirement: Day-Briefing Card Read

The dashboard API SHALL expose a structured day-briefing ("tomorrow at a glance") card read assembled from the cached overlay view for a target date. The response MUST be structured (grouped overlay entries, not generated prose), MUST be served with NO per-open LLM call, and MUST carry an honest empty-state via a `has_domain_context` boolean so the frontend can distinguish "nothing for this day" from "context unavailable".

#### Scenario: Day-card assembled from the cached view
- **WHEN** the day-briefing card read is called for a target date for which at least one specialist has written a contribution (even with `has_entries=false`)
- **THEN** the response is a structured payload grouping the date's overlay entries by butler/kind with `has_domain_context: true`
- **AND** no LLM session is invoked while serving the request

#### Scenario: Day-card honest empty-state
- **WHEN** no specialist has written any contribution for the target date (jobs have not run, or the view is absent)
- **THEN** the response has `entries: []` and `has_domain_context: false`
- **AND** the frontend renders "No domain context for this day" rather than silently omitting the card section

#### Scenario: Day-card is degraded fail-open, not Prometheus-degraded
- **WHEN** the underlying overlay view query fails or the view is absent
- **THEN** the endpoint returns the honest empty-state (`entries: []`, `has_domain_context: false`) rather than HTTP 500
- **AND** the response does NOT use the `aggregates_available` Prometheus degraded-envelope (the day-card reads no Prometheus metrics)
