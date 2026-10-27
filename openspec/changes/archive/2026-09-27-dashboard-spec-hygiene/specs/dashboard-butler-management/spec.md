## MODIFIED Requirements

### Requirement: Butler detail page outer chrome uses status-board archetype

The Butler detail page at `/butlers/:name` SHALL use `<Page archetype="status-board">`
as its outer chrome, with the tab block as the sole page body. No footer KPI
band, breadcrumb trail, or heartbeat tile renders at the page layer.

- **Title and description.** The page title is the butler's name titleized
  (e.g., `"relationship"` → `"Relationship"`); the description is the butler
  summary's description when available.
- **Header.** The page header is the butler detail header: the butler identity
  block (letter-mark, name, description, activity status, port, uptime, schedule
  facts) with the page actions inside it. The actions are, in order: a status
  pill, the command bar (see Butler Detail Command Bar), a Logs link to
  `?tab=activity&section=logs`, a Config link to `?tab=system&section=config`, a
  Chat button that opens the butler chat panel, and a Pause/Resume control.
- **Pause/Resume.** Pausing sets the butler's eligibility to `quarantined` and
  resuming sets it to `active`, each behind an undo window. While paused, the
  actions show when and why the butler was quarantined. The command palette
  offers the same Pause or Resume verb.
- **Loading and errors.** The page's loading state reflects the butler record
  fetch; tab-level lazy loading is separate. A failed butler fetch sets the page
  error with a retry that refetches the butler record. Tab errors stay
  tab-scoped.

#### Scenario: Page shell uses status-board archetype

- **WHEN** the butler detail page renders for any butler name
- **THEN** `<Page archetype="status-board">` SHALL be the outer shell
- **AND** no breadcrumb trail, heartbeat tile, or footer KPI band SHALL render
  at the page layer

#### Scenario: Butler name as page title

- **WHEN** the butler detail page renders for butler `"relationship"`
- **THEN** the page title SHALL be `"Relationship"` (titleized)
- **AND** the description SHALL come from the butler summary's description
  when available

#### Scenario: Header slot composition

- **WHEN** the butler detail page renders for a resolved butler
- **THEN** the page header SHALL be the butler detail header, with the page
  actions rendered inside the header rather than as a separate page actions slot
- **AND** the actions SHALL render, in order: status pill, command bar (prompt,
  complexity, Run), Logs link, Config link, Chat, and Pause/Resume
- **AND** the Logs link SHALL target `?tab=activity&section=logs` and the Config
  link SHALL target `?tab=system&section=config`

#### Scenario: Tabs body is the page body

- **WHEN** the butler detail page renders the tab group
- **THEN** the complete tab block (tab rail and every tab body) SHALL be the
  direct body content of the page, with no additional page-layer wrapper

#### Scenario: Top-level loading delegates to Page shell

- **WHEN** the butler record fetch is in flight
- **THEN** the page SHALL be in its loading state
- **AND** the Page shell SHALL render its own built-in loading treatment; the
  page component SHALL NOT pass a bespoke skeleton

#### Scenario: Unknown butler shows shell error

- **WHEN** a user navigates to `/butlers/nonexistent` and the butler record fetch
  returns 404 or an error
- **THEN** the page error SHALL be set
- **AND** its retry SHALL invalidate the butler query and trigger a refetch

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
- **AND** the bucket values SHALL be the status-board row's hourly stripe,
  defaulting to 24 zero buckets when unavailable

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

### Requirement: Activity tab

The Activity tab's Analytics sub-section SHALL be the per-butler session
analytics surface: a KPI quartet, a range-switchable activity chart, and a
session-kind breakdown. A range toggle (`24h`, `7d`, `30d`, default `24h`) sets
the window for every panel.

Data comes from the butler-scoped session analytics endpoints
`GET /api/butlers/{name}/analytics/hourly-activity`, `.../daily-activity`,
`.../latency-stats`, and `.../session-kinds`, plus the session aggregate for the
failed-session count.

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

### Requirement: Logs tab

The Activity tab's Logs sub-section SHALL be a structured viewer over the
butler's daemon log, read from `GET /api/butlers/{name}/logs` and polled every
five seconds while visible. It renders one full-width scroll panel with level
filter chips and log lines in fixed timestamp, level, and message columns.

#### Scenario: Log level filter chips

- **WHEN** the Logs sub-section is active
- **THEN** filter chips SHALL render for All, DEBUG, INFO, WARN, and ERROR, with
  All selected by default
- **AND** exactly one chip SHALL be active at a time
- **AND** selecting a level chip SHALL refetch the log list at that level

#### Scenario: Log line column widths

- **WHEN** log lines render
- **THEN** each line SHALL show a fixed-width millisecond-precision timestamp, a
  fixed-width level, and a message filling the remaining width, all in the
  monospace family
- **AND** timestamps and levels SHALL stay column-aligned across lines

#### Scenario: Log level color tokens

- **WHEN** a log line has level WARN
- **THEN** the level text SHALL use the amber token
- **AND** no oklch literal or hex color SHALL be used

- **WHEN** a log line has level ERROR
- **THEN** the level text SHALL use the destructive token

#### Scenario: Logs tab auto-scroll

- **WHEN** auto-scroll is on (the default)
- **THEN** the list SHALL scroll to the newest entry as polls deliver new lines
- **AND** scrolling up SHALL pause following until the operator re-enables
  auto-scroll

#### Scenario: Logs tab empty state

- **WHEN** the logs endpoint returns no entries for the selected level
- **THEN** the panel SHALL display "No logs yet." in muted text

### Requirement: Approvals tab

The Approvals tab SHALL list the pending approval actions for the current
butler in one full-width scroll panel, each row deep-linking into that action.

#### Scenario: Approvals list with pending items

- **WHEN** the Approvals tab loads for a butler with pending approval actions
- **THEN** each pending action SHALL render a severity dot, the tool name as
  title, a sub-line with the agent summary and relative age, and a "Review" link
  to `/approvals/{action_id}`
- **AND** severity SHALL derive from the action's expiry: high (destructive fill)
  when it expires within one hour or has expired, medium (amber fill) within 24
  hours, and low (muted fill) otherwise or with no expiry

#### Scenario: Approvals empty state

- **WHEN** no pending approvals exist for the butler
- **THEN** the panel SHALL display "No items pending review." in muted text

#### Scenario: Approvals age rendering

- **WHEN** a pending approval item is rendered
- **THEN** its age SHALL render through the shared time primitive in compact
  relative form (e.g., "3m ago")
- **AND** no raw locale formatting or manual date arithmetic SHALL be used

### Requirement: Spend tab

The Spend tab SHALL be the per-butler cost surface: a KPI quartet, a daily spend
trend with a range toggle (`24h`, `7d`, `30d`), and a 30-day model breakdown.
When the spend source is unavailable or some model usage is unpriced, the tab
SHALL say so in a degraded-source note rather than presenting partial totals as
complete.

#### Scenario: Spend KPI quartet

- **WHEN** the Spend tab loads
- **THEN** four KPI cells SHALL render: Spend today, Spend 30d, Cost / session ·
  30d, and Tokens today
- **AND** cost values SHALL be formatted as USD (e.g., "$0.04")
- **AND** zero or unavailable spend values SHALL render dimmed

#### Scenario: Spend trend bar chart

- **WHEN** the range is `24h`, `7d`, or `30d`
- **THEN** the trend panel SHALL render one bar per UTC day over the last 1, 7,
  or 30 days respectively
- **AND** a failed request SHALL show "Could not load spend trend."

#### Scenario: Model breakdown KV list

- **WHEN** 30-day model cost data is available
- **THEN** each model SHALL be listed with its cost, sorted by cost descending,
  in tabular numerals
- **AND** a failed request SHALL show "Could not load model breakdown."

#### Scenario: Spend tab empty state

- **WHEN** no spend data exists for the window
- **THEN** the trend panel SHALL show "No spend data for this period." and the
  model breakdown SHALL show "No model usage data available."

### Requirement: Config Tab

The System tab's Config sub-section SHALL provide full transparency into a
butler's configuration: a two-by-two panel grid followed by a collapsed
accordion of the butler's configuration files.

- **Panel grid.** "process" shows container name, port, registered duration,
  and config path (read-only, the same facts as the Overview config panel, never
  a `pid`). "schedule" lists active schedules by name with a relative next-run
  time (empty: "No schedules."). "scopes and oauth" lists each module's OAuth
  status as authorized, unauthorized, or not required (empty: "No modules with
  OAuth."). "integrations" lists enabled modules as badges (empty: "No modules
  enabled.").
- **Accordion.** butler.toml, CLAUDE.md, AGENTS.md, and MANIFESTO.md each render
  as a collapsed item that expands to the file content in a monospace block,
  showing "Not found" when the file is absent. The butler.toml item keeps a
  Formatted/Raw toggle.

#### Scenario: Config 2x2 panel grid

- **WHEN** the Config sub-section loads
- **THEN** four panels SHALL render in two rows of two: process, schedule,
  scopes and oauth, integrations
- **AND** the panels SHALL form a single ruled grid per the shared panel
  vocabulary in dashboard-design-language

#### Scenario: Schedule panel relative timestamps

- **WHEN** the schedule panel renders a schedule's next-run time
- **THEN** the time SHALL be rendered through the shared time primitive in
  relative mode
- **AND** no raw locale formatting or manual date arithmetic SHALL appear

#### Scenario: Config markdown accordion collapsed by default

- **WHEN** the Config sub-section renders
- **THEN** the butler.toml, CLAUDE.md, AGENTS.md, and MANIFESTO.md items SHALL
  be collapsed by default
- **AND** expanding an item SHALL reveal the full file content in a monospace
  block
- **AND** the butler.toml item SHALL keep the "Formatted" / "Raw" toggle, where
  "Formatted" renders the TOML as a structured key-value tree and "Raw" renders
  the JSON representation with 2-space indentation

#### Scenario: Config error and null states

- **WHEN** a config file value is null (e.g., no MANIFESTO.md present)
- **THEN** the accordion item SHALL display "Not found" as its expanded content
- **AND** the item SHALL still be present and expandable
- **AND** when the config API request fails, an error message SHALL be shown with
  the failure reason
- **AND** when the response has no config data, a "No configuration data
  available" message SHALL be displayed

## ADDED Requirements

### Requirement: Butler Detail Tab Set

The `/butlers/:name` page SHALL render one tab vocabulary for every butler: six
base tabs (Overview, Activity, Approvals, Spend, Memory, System) plus the
butler's domain tabs from the Butler Domain Tabs registry. The tab rail shows
Overview, Activity, Approvals, Spend, and Memory first, then the butler's domain
tabs, and System last. No mode toggle exists; every tab is reachable for every
butler that carries it.

The Activity tab groups three sub-sections: Analytics (default, see Activity
tab), Sessions (see Sessions Tab), and Logs (see Logs tab). The System tab
groups seven sub-sections: Config (default, see Config Tab), Skills (see Skills
Section), Schedules (see Schedules Tab (CRUD)), MCP (see MCP Debug Tab), State
(see State Tab (CRUD)), Models, and Manage.

#### Scenario: URL-driven tab routing

- **WHEN** a user navigates to `/butlers/:name?tab=<value>`
- **THEN** the active tab SHALL be the `tab` value when it is a base tab or one
  of that butler's domain tabs
- **AND** when `tab` is absent or not valid for that butler, the active tab
  SHALL be Overview
- **AND** selecting Overview SHALL remove the `tab` parameter from the URL
- **AND** tab changes SHALL replace the current history entry rather than push
  a new one

#### Scenario: Base tabs for every butler

- **WHEN** any butler detail page loads
- **THEN** the tab rail SHALL show Overview, Activity, Approvals, Spend, Memory,
  and System
- **AND** no mode toggle or mode-gated tab SHALL be rendered

#### Scenario: Domain tabs precede System

- **WHEN** a butler with domain tabs is viewed (for example `switchboard`)
- **THEN** its domain tabs (Routing Log, Registry) SHALL appear after Memory and
  before System
- **AND** System SHALL remain the last tab

#### Scenario: Section sub-navigation is URL-driven

- **WHEN** the Activity or System tab is active
- **THEN** the active sub-section SHALL be carried in the `section` query
  parameter
- **AND** an absent or invalid `section` SHALL select the default sub-section
  (Analytics for Activity, Config for System)
- **AND** selecting the default sub-section SHALL remove `section` from the URL
- **AND** selecting Analytics SHALL also clear any `since` and `until` filters

#### Scenario: Deep link into a sub-section

- **WHEN** a user navigates to `/butlers/:name?tab=system&section=schedules`
- **THEN** the System tab SHALL open with the Schedules sub-section active

#### Scenario: Lazy-loaded tab bodies

- **WHEN** a tab or sub-section other than Overview, Activity analytics, or
  System config is selected for the first time
- **THEN** its body SHALL load on demand with a centered "Loading {label}..."
  fallback while it loads

#### Scenario: Keyboard tab navigation

- **WHEN** the butler detail page is focused
- **THEN** the digit keys 1 through 9 SHALL switch to the first nine tabs in
  rail order
- **AND** `[` and `]` SHALL switch to the previous and next tab, wrapping at the
  ends

#### Scenario: Reload from the command palette

- **WHEN** the butler detail page is open
- **THEN** the command palette SHALL offer a "Reload {butler}" command that
  refetches the butler record

### Requirement: Butler Domain Tabs

A butler's detail page SHALL append the domain tabs registered for that butler
in the table below, and no others. Domain tab presence is decided by the butler
name in the frontend tab registry, never by `butler.toml` fields or runtime API
responses. Labels are sentence-case with no punctuation.

| Butler | Domain tabs (tab key: label) |
|---|---|
| chronicler | `timelines`: Timelines |
| education | `reviews`: Reviews |
| finance | `finances`: Finances |
| general | `collections`: Collections; `entities`: Entities |
| health | `health`: Measurements |
| home | `devices`: Devices |
| lifestyle | `taste`: Taste |
| qa | `investigations`: Investigations |
| relationship | `contacts`: Contacts |
| switchboard | `routing-log`: Routing Log; `registry`: Registry |
| travel | `trips`: Trips |

Butlers absent from the table carry no domain tab. Domain tab bodies use the
shared panel vocabulary specified in dashboard-design-language.

#### Scenario: Each butler renders its registered domain tab labels

- **WHEN** a butler listed in the registry is viewed
- **THEN** the tab rail SHALL show exactly that butler's registered domain tab
  labels, in registry order, between Memory and System
- **AND** no butler SHALL render a domain tab registered to another butler

#### Scenario: Butler without a domain tab

- **WHEN** a butler absent from the registry is viewed
- **THEN** the tab rail SHALL show only the six base tabs

#### Scenario: Domain tab deep link

- **WHEN** a user navigates to `/butlers/relationship?tab=contacts`
- **THEN** the Contacts tab SHALL be active
- **AND** navigating to `/butlers/finance?tab=contacts` SHALL fall back to
  Overview

#### Scenario: Domain tab is lazy-loaded

- **WHEN** a domain tab is selected for the first time
- **THEN** its body SHALL load on demand with a "Loading {label}..." fallback
  naming that tab

### Requirement: Butler Detail Command Bar

The butler detail header SHALL provide a prompt-first command bar that starts a
session for the butler: a prompt input, a complexity selector listing the
backend complexity tiers (default workhorse), and a Run button.

#### Scenario: Empty prompt fires the scheduled tick

- **WHEN** the operator presses Run (or Enter) with an empty prompt
- **THEN** the butler SHALL be triggered with its default scheduled-tick prompt
  at the selected complexity
- **AND** a "Force run triggered" confirmation toast SHALL appear

#### Scenario: Custom prompt starts a session

- **WHEN** the operator enters a prompt and presses Run
- **THEN** the butler SHALL be triggered with that prompt at the selected
  complexity
- **AND** a "Prompt sent" confirmation toast SHALL appear

#### Scenario: Run navigates to the new session

- **WHEN** a trigger succeeds and returns a session id
- **THEN** the page SHALL navigate to `/sessions/{session_id}`

#### Scenario: Run failure and in-flight state

- **WHEN** a trigger is in flight
- **THEN** the prompt input, complexity selector, and Run button SHALL be
  disabled and the button SHALL read "Running…"
- **AND** when the trigger fails, a "Failed to run butler" error toast SHALL
  appear

### Requirement: Skills Section

The System tab's Skills sub-section SHALL show all skills available to a butler
with drill-down into each skill's SKILL.md.

#### Scenario: Skill card grid

- **WHEN** skills are loaded
- **THEN** each skill SHALL render as a card in a responsive grid showing the
  skill name, a "skill" badge, and the first non-heading, non-empty line of the
  SKILL.md content as a description, truncated to 120 characters

#### Scenario: Skill detail dialog

- **WHEN** the operator clicks "View" on a skill card
- **THEN** a dialog SHALL open showing the skill name as title and the full
  SKILL.md content in a scrollable monospace block

### Requirement: Butler Memory Tab Surface

The butler detail Memory tab SHALL surface the butler's memory state as a KPI
quartet over memory counts and a recent-writes feed, both scoped to the
current butler. The cross-butler memory browser lives on `/memory`, not in this
tab.

#### Scenario: Memory KPI quartet with "+N today" sub-lines

- **WHEN** the Memory tab loads
- **THEN** four KPI cells SHALL render: Episodes, Facts, Entities, Rules
- **AND** each cell's sub-line SHALL show "+N today", where N is the count added
  in the last 24 hours
- **AND** when the stats request fails, the quartet SHALL show "Could not load
  memory stats."

#### Scenario: Recent-writes feed

- **WHEN** the recent-writes panel renders
- **THEN** it SHALL list the butler's ten most recent episodes, newest first,
  each with a relative timestamp, the butler name, and a one-line content
  preview
- **AND** the panel SHALL be a fixed-height scroll region so no entry is cut off
  without scroll access
- **AND** when the request fails, it SHALL show "Could not load recent writes."

#### Scenario: Memory tab empty state

- **WHEN** no episodes exist for the butler
- **THEN** the recent-writes panel SHALL show "No memory writes recorded yet."
- **AND** the KPI cells SHALL render zero counts with "+0 today" sub-lines

### Requirement: Non-Butler Page Tab Structures

Tabbed structures outside the butler detail view SHALL behave as specified here.

#### Scenario: Memory browser tabs

- **WHEN** the `/memory` page's browser renders
- **THEN** it SHALL offer three registers: Facts, Rules, Episodes

#### Scenario: Contact detail redirect

- **WHEN** `/contacts/:contactId` is visited
- **THEN** the route SHALL replace-navigate to `/entities/index?has=contact`
- **AND** it SHALL NOT render the retired contact-detail page or its former tabs

## REMOVED Requirements

### Requirement: Butler Detail Page Structure

**Reason**: The operator/resident tab list it described no longer ships; the
page has one six-tab vocabulary with Activity and System sub-sections.
**Migration**: See Butler Detail Tab Set and Butler Domain Tabs.

### Requirement: Butler detail page tab body vocabulary

**Reason**: The operator/resident mode toggle and its persisted preference were
deleted; every butler has one tab vocabulary.
**Migration**: See Butler Detail Tab Set; domain tabs are in Butler Domain Tabs.

### Requirement: Tab Structures Reference (Non-Butler Pages)

**Reason**: The approvals navigation it restated is owned by dashboard-approvals,
and the butler detail Memory tab no longer embeds the memory browser.
**Migration**: See Non-Butler Page Tab Structures; approvals navigation is in
dashboard-approvals.

### Requirement: Skills Tab

**Reason**: Skills is a System sub-section and the Trigger tab it linked to no
longer exists.
**Migration**: See Skills Section; manual runs use Butler Detail Command Bar.

### Requirement: Trigger Tab (Manual Session Invocation)

**Reason**: The Trigger tab was folded into the header command bar.
**Migration**: See Butler Detail Command Bar.

### Requirement: Memory Tab

**Reason**: The butler Memory tab no longer carries a mode-gated memory browser;
its recent-writes feed lists episodes.
**Migration**: See Butler Memory Tab Surface; the memory browser is on `/memory`.

### Requirement: CRM Tab (Butler-Specific)

**Reason**: No CRM tab ships; relationship data lives in the relationship
butler's Contacts domain tab and the entities pages.
**Migration**: See Butler Domain Tabs (relationship: Contacts).

### Requirement: Bespoke resident tab per domain butler

**Reason**: Its rules were written against the retired resident/operator modes
and fixed tab positions, and the general butler carries two domain tabs.
**Migration**: See Butler Domain Tabs.

### Requirement: Per-butler bespoke tab label registry

**Reason**: Merged into one domain tab registry that also records the general
butler's Entities tab.
**Migration**: See Butler Domain Tabs.

### Requirement: Panel-grid frame

**Reason**: Visual composition rules belong to the design language, not to a
page spec.
**Migration**: See dashboard-design-language.

### Requirement: Panel atom

**Reason**: Visual composition rules belong to the design language, not to a
page spec.
**Migration**: See dashboard-design-language.

### Requirement: KPI quartet pattern

**Reason**: Visual composition rules belong to the design language, not to a
page spec.
**Migration**: See dashboard-design-language.

### Requirement: RangeToggle vocabulary

**Reason**: Shared control vocabulary belongs to the design language, not to a
page spec.
**Migration**: See dashboard-design-language.

### Requirement: Switchboard Triage Filters

**Reason**: Triage filters are managed on the ingestion console, not the
switchboard detail page.
**Migration**: See dashboard-ingestion-dispatch-console "Filters Pipeline".

### Requirement: Switchboard Backfill Management

**Reason**: No backfill management UI ships on the switchboard detail page.
**Migration**: None.

### Requirement: Data Fetching Architecture

**Reason**: Duplicated the dashboard-wide polling and invalidation contract.
**Migration**: See dashboard-shell "Bus-Aware Poll Architecture".

### Requirement: Loading and Error State Consistency

**Reason**: Duplicated the dashboard-wide loading, empty, and error patterns.
**Migration**: See dashboard-shell "Skeleton Loading Components", "Empty State
Pattern", and "Error Boundary".
