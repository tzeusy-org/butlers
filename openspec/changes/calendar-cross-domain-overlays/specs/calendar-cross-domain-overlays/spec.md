# Calendar Cross-Domain Overlays

## Purpose

The Calendar Cross-Domain Overlays capability layers read-only domain context
from specialist butlers (finance, travel, relationship, health) onto the calendar
workspace as structured day-overlay entries — bills due, departures, birthdays,
appointments — without any LLM session at render time and without violating
schema isolation.

The implementation follows the RFC 0010 precompute-and-cache pattern sanctioned
by RFC-0020: each specialist runs a deterministic contribution job that writes a
per-day structured envelope into its own state store; a migration-tracked
read-only UNION view aggregates the envelopes; the calendar reads the cached view
at zero LLM cost.

## ADDED Requirements

### Requirement: Overlay Contribution Envelope Schema

Each specialist butler's overlay contribution SHALL be a JSON object conforming
to a standard envelope with fields: `butler` (string, butler name), `date`
(string, ISO date YYYY-MM-DD), `has_entries` (boolean), and `entries` (array of
entry objects). Each entry object SHALL have `kind` (string), `label` (string),
and `priority` (string, one of `"high"`, `"medium"`, `"low"`). An optional `meta`
field (JSONB, null if absent) carries kind-specific structured data for FE
rendering.

#### Scenario: Envelope with entries

- **WHEN** a specialist butler has domain-relevant events for an overlay date
- **THEN** it produces an envelope with `has_entries=true` and a non-empty `entries`
  array
- **AND** entries are ordered by priority descending: `"high"` entries first,
  then `"medium"`, then `"low"`

#### Scenario: Envelope with no entries

- **WHEN** a specialist butler has no domain events for a given date
- **THEN** it produces an envelope with `has_entries=false` and an empty `entries`
  array
- **AND** the envelope is still written to state so the view can distinguish
  "job ran, nothing found" from "job hasn't run" (honest empty-state)

#### Scenario: Invalid envelope rejected

- **WHEN** a contribution entry in the view is missing required fields (`butler`,
  `date`, `has_entries`)
- **THEN** the workspace projection layer SHALL treat it as malformed and skip
  it, logging a warning
- **AND** `has_domain_context` in the response reflects only valid contributions

#### Scenario: Envelope `butler` field validates against source column

- **WHEN** the workspace projection reads a row from `v_overlay_contributions`
- **THEN** it validates that `value->>'butler'` matches the `butler` source
  column (the hardcoded UNION literal)
- **AND** if they do not match, the contribution is treated as malformed and
  skipped with a warning log
- **BECAUSE** the hardcoded source column is the authoritative source
  attribution; a mismatch indicates payload tampering or schema misconfiguration

### Requirement: Contribution State Key Convention

Each specialist butler SHALL write its daily overlay contribution to its state
store under keys matching the pattern `calendar/overlay/<YYYY-MM-DD>` where the
date is the target calendar date in SGT (UTC+8). A single job run MAY write
multiple keys (one per date in the lookahead window).

#### Scenario: Key written for today and lookahead

- **WHEN** the `calendar_overlay_contribution` job runs
- **THEN** overlay envelopes are written under `calendar/overlay/<date>` for each
  date from today through today+30 days (the lookahead window) that has domain
  events
- **AND** dates with no events produce an envelope with `has_entries=false`

#### Scenario: Key upserts stale entry

- **WHEN** the job runs and an envelope for a given date already exists
- **THEN** the existing entry is overwritten via `state_set` (upsert semantics)

#### Scenario: Pruning removes old entries

- **WHEN** the `calendar_overlay_contribution` job completes its writes
- **THEN** it deletes all state entries matching `calendar/overlay/*` where the
  date suffix is more than 30 days before today (SGT)
- **AND** when there are no entries to prune, the prune step completes as a
  no-op

### Requirement: Cross-Schema Overlay View

A SQL view `calendar.v_overlay_contributions` SHALL exist that provides read-only
access to overlay contribution state entries across all four contributing
specialist schemas. The view SHALL union `butler`, `key`, and `value` columns
from the `state` table of each contributing schema (`finance`, `travel`,
`relationship`, `health`) filtered to keys matching `calendar/overlay/%`. Each
UNION term SHALL include an explicit `butler` column as a string literal
identifying the source schema.

This view is a sanctioned exception to schema isolation (RFC 0006), reusing the
RFC 0010 Cross-Butler Briefing Exception under RFC-0020's accepted criteria.
Constraints: the view is read-only (UNION view is not updatable in PostgreSQL),
uses an explicit `butler` source column for auditability (RFC 0010 Guardrail #2),
queries are key-prefix–filtered only (Guardrail #3), and grants are
migration-based (Guardrail #5 — auditable and reversible).

#### Scenario: View returns contributions from available specialists

- **WHEN** multiple specialist butlers have written overlay contributions for a
  given date
- **THEN** querying `calendar.v_overlay_contributions WHERE key = 'calendar/overlay/<date>'`
  returns all contributions with their source schema identifiable via the `butler`
  column
- **AND** the `butler` column value is a string literal set per UNION term (not
  derived from the JSON payload)

#### Scenario: View returns empty when no contributions exist

- **WHEN** no specialist butlers have written overlay contributions
- **THEN** querying `calendar.v_overlay_contributions` returns zero rows

#### Scenario: View is read-only

- **WHEN** an INSERT, UPDATE, or DELETE is attempted on the view
- **THEN** the operation fails (UNION views are not updatable in PostgreSQL)

#### Scenario: Absent specialist state table handled gracefully

- **WHEN** a contributing specialist's `state` table does not exist at migration
  time (specialist butler not yet deployed)
- **THEN** the corresponding UNION term is replaced by a stub that returns zero
  rows (`SELECT NULL::text AS butler, NULL::text AS key, NULL::jsonb AS value WHERE FALSE`)
- **AND** the overall view is still created and queryable

### Requirement: Aggregation View Migration

An Alembic migration SHALL create the `calendar.v_overlay_contributions` view
and grant SELECT on each contributing specialist schema's `state` table to the
database role used by the calendar butler. The migration SHALL be reversible
(downgrade drops the view and revokes grants).

#### Scenario: Migration upgrade

- **WHEN** the Alembic migration is applied
- **THEN** the view `calendar.v_overlay_contributions` exists and is queryable
  from the calendar schema
- **AND** SELECT grants on contributing specialist `state` tables are active
  for the calendar reader role

#### Scenario: Migration downgrade

- **WHEN** the Alembic migration is reverted
- **THEN** the view `calendar.v_overlay_contributions` is dropped
- **AND** cross-schema SELECT grants are revoked

### Requirement: Contributing Butler Set

The contributing specialist set SHALL be exactly: `finance`, `travel`,
`relationship`, `health`. Lifestyle, Home, and Education are excluded because
their domain data is not date-keyed calendar events (retrospective log entries,
real-time sensor state, and daily review counts respectively).

#### Scenario: Finance overlay entries

- **WHEN** the Finance butler runs `calendar_overlay_contribution`
- **THEN** it writes entries of kinds `bill_due` (bills due on that date) and
  `subscription_renewal` (subscriptions renewing on that date)
- **AND** a date with no bills and no renewals produces an envelope with
  `has_entries=false`

#### Scenario: Travel overlay entries

- **WHEN** the Travel butler runs `calendar_overlay_contribution`
- **THEN** it writes entries of kinds `departure`, `arrival`, `check_in`, and
  `check_out` for dates in the lookahead window
- **AND** a date with no travel events produces an envelope with
  `has_entries=false`

#### Scenario: Relationship overlay entries

- **WHEN** the Relationship butler runs `calendar_overlay_contribution`
- **THEN** it writes entries of kinds `birthday` (contact birthdays on that date),
  `important_date` (tagged important dates), and `follow_up` (follow-ups due on
  that date) for dates in the lookahead window
- **AND** a date with no relationship events produces an envelope with
  `has_entries=false`

#### Scenario: Health overlay entries

- **WHEN** the Health butler runs `calendar_overlay_contribution`
- **THEN** it writes entries of kinds `appointment` (health appointments) and
  `medication_reminder` (scheduled medication doses) for dates in the lookahead
  window
- **AND** a date with no health events produces an envelope with
  `has_entries=false`

### Requirement: Contribution Job Scheduling

Each contributing specialist butler SHALL have a `calendar_overlay_contribution`
entry in its `butler.toml` with `dispatch_mode="job"`,
`job_name="calendar_overlay_contribution"`, and cron `50 6 * * *` (06:50 UTC =
14:50 SGT). The job runs before the briefing contribution jobs (`55 6 * * *`) so
that a combined future enhancement can incorporate overlay data in the same daily
cycle.

#### Scenario: Schedule entry present

- **WHEN** a contributing specialist butler daemon starts and syncs TOML schedules
- **THEN** a `calendar_overlay_contribution` scheduled task exists with
  cron `50 6 * * *` and `dispatch_mode="job"`

#### Scenario: Job registered in daemon

- **WHEN** the scheduler dispatches the `calendar_overlay_contribution` job
- **THEN** the job handler is found in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`
  under the specialist butler's name

#### Scenario: Non-contributing butlers excluded

- **WHEN** a butler that is NOT in the contributing specialist set (education,
  home, lifestyle, general, calendar) starts
- **THEN** it does NOT have a `calendar_overlay_contribution` schedule entry
- **AND** it SHALL NOT have a handler in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`
  for `calendar_overlay_contribution`

### Requirement: Overlays Workspace Projection

The calendar workspace endpoint SHALL accept `view=overlays` as a valid `view`
parameter and project overlay contribution entries from
`calendar.v_overlay_contributions` into the `UnifiedCalendarEntry` shape tagged
`source_type="overlay_contribution"`, with `start`/`end` date range filtering.

#### Scenario: Overlays view returns projected entries

- **WHEN** `GET /api/calendar/workspace?view=overlays` is called with a
  `start`/`end` range
- **THEN** each overlay contribution entry whose target date falls within the
  range is returned as a `UnifiedCalendarEntry` with:
  - `source_type = "overlay_contribution"`
  - `editable = false`
  - `start_at` set to the entry's target date (midnight SGT)
  - `title` set to the entry's `label`
  - `metadata` carrying `kind`, `priority`, `source_butler` (from the UNION
    `butler` column), and the entry's `meta` object

#### Scenario: Overlay entries never appear in user or butler views

- **WHEN** `GET /api/calendar/workspace?view=user` or `view=butler` is called
- **THEN** no entries with `source_type="overlay_contribution"` appear in the
  response
- **BECAUSE** overlays are a read-only domain-context layer, not user-owned or
  butler-owned calendar events

#### Scenario: Overlays view is fail-open

- **WHEN** `calendar.v_overlay_contributions` is absent (pre-migration), a
  contributing specialist's state table is missing, or the projection query fails
- **THEN** the endpoint returns `entries: []` with `has_domain_context: false`
  rather than HTTP 500
- **AND** the failure is logged

### Requirement: Briefing Day-Card Read-Model with Honest Empty-State

The workspace response for `view=overlays` SHALL include a top-level
`has_domain_context` boolean field that allows the frontend to distinguish
between "no entries today" (specialists ran but found nothing) and "context
unavailable" (view not yet populated or unreachable).

#### Scenario: Domain context available

- **WHEN** `GET /api/calendar/workspace?view=overlays` is called for a date and
  at least one specialist has written a contribution for that date (even with
  `has_entries=false`)
- **THEN** the response includes `has_domain_context: true`

#### Scenario: Domain context unavailable (honest empty-state)

- **WHEN** no specialist has written any contribution for the requested date
  range (jobs have not run yet, or the view is absent)
- **THEN** the response includes `has_domain_context: false` and `entries: []`
- **AND** the FE SHALL render "No domain context for this day" rather than
  silently omitting the section

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication — this capability
  is the sanctioned RFC 0010 exception, not a Rule 3 violation)
- Non-Negotiable Rule 4 (LLM reasoning is ephemeral — no LLM runs in the overlay
  read path; contributions are deterministic SQL jobs)
- RFC 0006 (Database schema isolation — the cross-schema view and SELECT grants
  are the migration-tracked exception; all other data flows remain schema-isolated)
- RFC 0010 (Cross-Butler Briefing Exception — the overlay view reuses this
  exception under RFC-0020's accepted criteria; the five guardrails are inherited
  verbatim)
- RFC 0020 (Calendar Cross-Domain Overlay Read Exception — the domain RFC that
  evaluates the naive design against RFC 0010 criteria and recommends this path)
