## MODIFIED Requirements

### Requirement: Overview Tab

The Butler detail Overview tab SHALL be the operational overview for the
selected butler: a responsive panel grid with up to four KPI columns on wide
viewports. The grid contains, in order: four single-column KPI panels (status,
sessions, spend, awaiting), then full-width-pair panels for 24-hour activity,
recent events, awaiting-your-action, and config, followed by the delegations and
domain-events panels. While the butler record is loading, the tab renders a
matching panel-grid skeleton so the layout does not shift; each panel resolves
its own loading and error state independently. Butler identity lives in the page
header, not in this tab.

Activity count placement SHALL follow Count-bucket truth. Compatible receiver-derived historical evidence SHALL be required for any listening verdict beyond UNKNOWN; session counts and current process status SHALL not substitute.

ID: REQ-dashboard-butler-management-003
Source: bu-s11n0s.5 complete-protocol D1-D6; preserved governing requirement at a6f342bd54c207b9e672d7d4e51be6af6d4f2876
Scope: v1-mandatory

#### Scenario: Status KPI panel

- **WHEN** the Overview tab loads for a butler
- **THEN** the "status" panel SHALL show a status dot and label derived from the
  butler status (`ok`/`healthy` → green "online"; `error`/`down` → red; otherwise
  dim), optionally suffixed with the activity verb from the butler's status-board
  row
- **AND** the panel SHALL show a "last run" relative timestamp from the
  status-board row, rendering "--" when unavailable

#### Scenario: Sessions KPI panel

- **WHEN** the "sessions" panel renders
- **THEN** it SHALL show the 24-hour session count from the status-board row,
  falling back to the butler record's 24-hour session count

#### Scenario: Spend KPI panel

- **WHEN** today's spend summary is available
- **THEN** the "spend" panel SHALL show the butler's USD cost for today with a
  per-session cost sub-line, and costs below $0.01 SHALL display as "$0.00"
- **AND** while the spend query is loading the panel SHALL render a skeleton in
  place of the value

#### Scenario: Awaiting KPI panel

- **WHEN** the "awaiting" panel renders
- **THEN** it SHALL show the count of this butler's pending approval actions,
  toned amber when greater than zero, with sub-text "pending review" or "nothing
  pending"

#### Scenario: 24-hour activity stripe panel

- **WHEN** the "activity" panel renders
- **THEN** it SHALL render a 24-bucket activity stripe with a rolling
  relative-time axis (`-24h`, `-12h`, `now`) rather than clock-of-day labels
- **AND** the prior clause "the bucket values SHALL be the status-board row's hourly stripe, defaulting to 24 zero buckets when unavailable" SHALL be explicitly qualified by Count-bucket truth: retain actual UTC keys, and permit count-zero densification only for missing cells inside a successfully read declared source window; an unreadable source SHALL instead render unavailable cells rather than 24 fabricated zeros

#### Scenario: Recent events panel

- **WHEN** the "recent" panel renders
- **THEN** it SHALL show up to five newest activity-feed events for the butler
  from `GET /api/butlers/{name}/activity-feed`, each row showing a relative
  timestamp, the event summary, and an event-kind label
  (session/approval/memory/other)
- **AND** each `session_completed` row SHALL render the API-provided safe summary
  from the same structured-trigger-first projection as Timeline, without a
  client-side raw-prompt or envelope fallback
- **AND** loading SHALL render skeleton rows, errors SHALL render "Could not load
  recent events.", and an empty feed SHALL render "no recent events"

#### Scenario: Awaiting-your-action panel

- **WHEN** the "awaiting your action" panel renders
- **THEN** it SHALL list the pending approval actions (agent summary or tool
  name, relative request time) each with a "review" link to `/approvals`
- **AND** loading SHALL render skeleton rows, errors SHALL render "Could not load
  approvals.", and an empty list SHALL render "no items pending review"
- **AND** the list SHALL come from the same pending-approvals query as the
  awaiting KPI panel

#### Scenario: Config panel

- **WHEN** the "config" panel renders
- **THEN** it SHALL show key/value rows for `port`, `registered` (hours derived
  from `registered_duration_seconds`), `modules` count, `schedules` count, and
  `skills` count, with the panel sub-title set to `config_path` when available
- **AND** the process facts (`port`, `registered_duration_seconds`,
  `config_path`) SHALL NOT render, type, or request a `pid` field, consistent
  with the Config Tab "process" panel
- **AND** missing process-facts source data SHALL render as explicit unavailable
  values ("--") rather than hiding the row

#### Scenario: Unavailable overview counts never impersonate quiet

- **WHEN** the hourly count source is unavailable
- **THEN** the panel SHALL render unavailable count cells with a degraded-source note
- **AND** readable measured zero SHALL remain distinct and may coexist with UNKNOWN listening
- **AND** bucket actions SHALL use the source bucket bounds


### Requirement: Activity tab

The Activity tab's Analytics sub-section SHALL be the per-butler session
analytics surface: a KPI quartet, a range-switchable activity chart, and a
session-kind breakdown. A range toggle (`24h`, `7d`, `30d`, default `24h`) sets
the window for every panel.

Data comes from the butler-scoped session analytics endpoints
`GET /api/butlers/{name}/analytics/hourly-activity`, `.../daily-activity`,
`.../latency-stats`, and `.../session-kinds`, plus the session aggregate for the
failed-session count.

Hourly and daily activity SHALL retain actual returned time keys and independent count/listening availability under Count-bucket truth. Until compatible historical receiver evidence exists, listening SHALL be UNKNOWN for both successful and degraded session-count reads.

ID: REQ-dashboard-butler-management-004
Source: bu-s11n0s.5 complete-protocol D1-D6; preserved governing requirement at a6f342bd54c207b9e672d7d4e51be6af6d4f2876
Scope: v1-mandatory

#### Scenario: Activity tab KPI quartet

- **WHEN** the Activity tab loads
- **THEN** four KPI cells SHALL render: Sessions (the sum of the session-kind
  counts in the window), p50 ms and p95 ms (session latency percentiles for the
  window), and Errors (sessions that did not succeed in the window)
- **AND** the Errors value SHALL use the high-severity tone when greater than
  zero
- **AND** when the metrics fail to load, the quartet SHALL show "Could not load
  activity metrics."

#### Scenario: Activity stripe for 24h range

- **WHEN** the range is `24h`
- **THEN** the activity panel SHALL render a 24-column hourly activity stripe
  from the hourly-activity endpoint
- **AND** a failed request SHALL show "Could not load hourly activity."

#### Scenario: Day bars for 7d or 30d range

- **WHEN** the range is `7d` or `30d`
- **THEN** the activity panel SHALL render 7 or 30 daily bars from the
  daily-activity endpoint
- **AND** a failed request SHALL show "Could not load daily activity."

#### Scenario: Kind breakdown panel

- **WHEN** the session-kinds endpoint returns results
- **THEN** the "By kind" panel SHALL list each session kind with its count in
  tabular numerals
- **AND** a failed request SHALL show "Could not load session kind breakdown."

#### Scenario: Activity tab empty state

- **WHEN** no sessions exist for the butler in the selected window
- **THEN** the kind breakdown SHALL show "No sessions in this window."
- **AND** KPI cells whose value is unavailable SHALL render a placeholder rather
  than a fabricated `0`

#### Scenario: Successful session counts do not establish listening

- **WHEN** a complete hourly session query returns measured zero or positive counts without compatible per-bucket receiver observations
- **THEN** the counts SHALL render at their actual keys with listening UNKNOWN
- **AND** 7d and 30d daily keys and current error/empty/KPI/kind behavior SHALL remain intact
- **AND** wrong receiver endpoint, epoch, stale or partial evidence SHALL not manufacture LIVE
