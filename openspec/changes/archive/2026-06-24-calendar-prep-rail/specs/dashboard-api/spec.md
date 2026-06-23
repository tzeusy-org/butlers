## ADDED Requirements

### Requirement: Meeting-Prep Rail Endpoint

The dashboard API SHALL expose `GET /api/calendar/workspace/prep/{event_id}` returning the meeting-prep context (resolved attendees with relationship letter-marks, relationship notes, and last-met) for a selected calendar event. The endpoint MUST be sourced exclusively from the precomputed `calendar.v_prep_contributions` cached view: it MUST NOT issue a direct cross-schema query (e.g. `SELECT ... FROM relationship.*` / `health.*`) at request time and MUST NOT spawn an LLM session. It MUST merge contributions across contributing butlers by attendee `entity_id` (so a single attendee carries relationship context plus any future message context), skip envelopes whose payload `butler` disagrees with the view's hardcoded source column, and fail open to a structured empty payload (never HTTP 500) when no prep contribution exists.

#### Scenario: Prep rail returns precomputed context
- **WHEN** `GET /api/calendar/workspace/prep/{event_id}` is called for an event that has precomputed prep contributions
- **THEN** the response carries the event's attendees (each with `entity_id`, `name`, `dunbar_tier`, `notes`, `last_met`/`last_met_event`), `has_prep_context: true`, and `source_butlers` listing the contributing schemas
- **AND** no direct cross-butler read and no LLM session occur while serving the request

#### Scenario: Prep rail honest empty-state
- **WHEN** the prep rail read is called for an event with no precomputed prep contribution (co-attended-edge / contact-link coverage not yet populated)
- **THEN** the endpoint returns `has_prep_context: false` with an empty `attendees` list and empty `source_butlers`, not HTTP 500
- **BECAUSE** the prep rail renders "no prep context yet" for events lacking coverage rather than fabricating context or reading sibling schemas live

#### Scenario: Prep rail never reads sibling schemas on demand
- **WHEN** the prep rail read is served
- **THEN** it reads only `calendar.v_prep_contributions` (contribution-sourced cached data) and issues no on-demand `SELECT` against `relationship.*`, `health.*`, or any other sibling schema, and opens no MCP/LLM session
- **BECAUSE** RFC-0020 rejected the on-demand cross-schema read and the per-open LLM synthesis paths

#### Scenario: Prep rail fail-open on missing view
- **WHEN** `calendar.v_prep_contributions` is absent (pre-migration), a contributing specialist's `state` table is missing, or the projection query fails
- **THEN** the endpoint returns `has_prep_context: false` with an empty `attendees` list rather than HTTP 500
- **AND** the failure is logged at WARNING level

#### Scenario: Prep rail merges attendees across butlers
- **WHEN** more than one contributing butler has written a prep envelope for the event with the same attendee `entity_id`
- **THEN** the response merges them into a single attendee carrying the union of their notes and message context, and `source_butlers` lists every contributing schema

#### Scenario: Prep rail skips butler-mismatched envelope
- **WHEN** a row read from the view has a `value->>'butler'` that does not match the view's hardcoded `butler` source column
- **THEN** that contribution is skipped with a warning log and excluded from the response
