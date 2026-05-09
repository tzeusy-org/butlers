## MODIFIED Requirements

### Requirement: Butler Detail Page Structure

The `/butlers/:name` page SHALL be a tabbed detail view where each butler is
treated as a first-class navigable entity. The page MUST support two visibility
modes: resident mode for the Dispatch-default vocabulary and operator mode for
the full administrative vocabulary.

#### Scenario: URL-driven tab routing

- **WHEN** a user navigates to `/butlers/:name?tab=<value>`
- **THEN** the active tab is set to the `tab` query parameter value when that
  value is available in the stored mode or can be made available by resolving a
  mode-exclusive tab to its owning mode
- **AND** when `tab` is absent or invalid, the default tab is `overview`
- **AND** tab changes update the URL via `replaceState` with no history entry

#### Scenario: Breadcrumb navigation

- **WHEN** the butler detail page renders
- **THEN** a breadcrumb trail is shown with the displayed, titleized butler
  name: Overview > Butlers > {Titleized butler name}

#### Scenario: Resident mode is the default tab vocabulary

- **WHEN** any butler detail page loads with no persisted mode
- **THEN** the selected mode is `resident`
- **AND** the following resident base tabs are visible: Overview, Activity,
  Logs, Approvals, Spend, Config, Memory
- **AND** the resident mode is persisted in `localStorage` under
  `butlers.detail.mode` when the user selects it explicitly

#### Scenario: Operator mode preserves the administrative vocabulary

- **WHEN** the user switches the butler detail page to operator mode
- **THEN** the following ten spec-mandated base tabs are visible: Overview,
  Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM, Memory
- **AND** these tabs are rendered inside the primary slot's `<TabsList>`
- **AND** the operator mode is persisted in `localStorage` under
  `butlers.detail.mode`

#### Scenario: Stored mode is restored

- **WHEN** `localStorage["butlers.detail.mode"]` is `resident`
- **THEN** the page loads in resident mode
- **WHEN** `localStorage["butlers.detail.mode"]` is `operator`
- **THEN** the page loads in operator mode
- **WHEN** the stored value is absent or any other value
- **THEN** the page loads in resident mode

#### Scenario: Deep links to operator-only tabs auto-promote mode

- **WHEN** the page would otherwise load in resident mode
- **AND** the `tab` query parameter is one of `sessions`, `skills`,
  `schedules`, `trigger`, `mcp`, `state`, `crm`, or `models` while Models is
  exposed by the current implementation
- **THEN** the page switches to operator mode
- **AND** the requested tab is selected instead of falling back to Overview
- **AND** the promoted operator mode is persisted in `localStorage` under
  `butlers.detail.mode`

#### Scenario: Deep links to resident-only tabs select resident mode

- **WHEN** the page would otherwise load in operator mode
- **AND** the `tab` query parameter is one of `activity`, `logs`, `approvals`,
  or `spend`
- **THEN** the page switches to resident mode
- **AND** the requested tab is selected instead of falling back to Overview
- **AND** the resident mode is persisted in `localStorage` under
  `butlers.detail.mode`

#### Scenario: Non-spec Models tab is operator-only while exposed

- **WHEN** current code exposes a Models tab on the butler detail page
- **THEN** Models MUST NOT appear in resident mode
- **AND** Models MAY appear in operator mode after the ten spec-mandated base
  tabs
- **AND** Models MUST NOT be counted as one of the ten spec-mandated base tabs
- **AND** `?tab=models` follows the operator auto-promotion behavior while the
  tab remains exposed

#### Scenario: Conditionally shown tabs -- switchboard

- **WHEN** the butler name is `switchboard`
- **THEN** two additional tabs are shown after the active mode's base tabs:
  "Routing Log" and "Registry"
- **AND** the tabs are visible in both resident and operator modes

#### Scenario: Conditionally shown tabs -- health

- **WHEN** the butler name is `health`
- **THEN** one additional tab is shown after the active mode's base tabs:
  "Health"
- **AND** the tab is visible in both resident and operator modes

#### Scenario: Conditionally shown tabs -- general

- **WHEN** the butler name is `general`
- **THEN** two additional tabs are shown after the active mode's base tabs:
  "Collections" and "Entities"
- **AND** the tabs are visible in both resident and operator modes

#### Scenario: Conditionally shown tabs -- education

- **WHEN** the butler name is `education`
- **THEN** one additional tab is shown after the active mode's base tabs:
  "Reviews"
- **AND** the tab is visible in both resident and operator modes

#### Scenario: Lazy-loaded tabs for performance

- **WHEN** a tab whose body is implemented as a lazy-loaded component is
  selected for the first time
- **THEN** its component is loaded on demand via React `lazy()` with a centered
  "Loading {tab}..." fallback
- **AND** this requirement applies only to tab bodies intentionally implemented
  with React `lazy()`, rather than forcing every non-default tab to become lazy

#### Scenario: Tab URL semantics and deep-linking

- **WHEN** the active tab is controlled by the `?tab=` query parameter
- **THEN** `overview` is the default tab and removes the query parameter from
  the URL
- **AND** accepted deep-link values include resident base tab keys
  (`overview`, `activity`, `logs`, `approvals`, `spend`, `config`, `memory`),
  operator base tab keys (`sessions`, `skills`, `schedules`, `trigger`, `mcp`,
  `state`, `crm`), conditional tab keys (`health`, `collections`, `entities`,
  `reviews`, `routing-log`, `registry`), and `models` while current code
  exposes it
- **AND** deep links to operator-only tab keys auto-promote to operator mode
- **AND** deep links to resident-only tab keys switch to resident mode when the
  stored mode is operator
- **AND** invalid `tab` values fall back to `overview` without forcing a mode
  switch
- **AND** tab changes update the URL via `replaceState` without creating
  browser history entries

### Requirement: Butler detail page tab body vocabulary

The Butler detail page tab body SHALL map to the four-tier archetype vocabulary
as follows:

- **Primary slot:** The `<Tabs>` block, containing `TabsList` for all visible
  tab triggers in the active mode and `TabsContent` for each reachable tab. This
  is the entire interactive surface for the butler workspace.
- **No additional body-level hero slot:** The `<Page>` / `<DetailPage>` shell
  still owns the record identity chrome: its title or `record.title` MUST be the
  displayed, titleized butler name and shell actions remain in the Tier 1 page
  header. The tab body MUST NOT add a second hero, identity strip, or action
  strip above the tabs. Detailed butler identity fields (status, description,
  port, eligibility, heartbeat, and related operational facts) are rendered
  inside the Overview tab's identity card.
- **No drawer slot:** Credential and advanced configuration content lives
  inside individual tabs (Config tab, State tab). No top-level practical drawer
  is needed.
- **Mode-specific tabs are NOT a candidate for page-level archetype expansion:**
  resident and operator tab sets are projections of the same workspace-grade
  record, not new `<Page>` slots.

#### Scenario: Resident base tabs present by default

- **WHEN** any butler detail page loads in resident mode
- **THEN** the following seven tab triggers MUST be visible: Overview, Activity,
  Logs, Approvals, Spend, Config, Memory
- **AND** these are rendered inside the primary slot's `<TabsList>`

#### Scenario: Operator base tabs present in operator mode

- **WHEN** any butler detail page loads in operator mode
- **THEN** the following ten tab triggers MUST be visible: Overview, Sessions,
  Config, Skills, Schedules, Trigger, MCP, State, CRM, Memory
- **AND** these are rendered inside the primary slot's `<TabsList>`
- **AND** no spec-mandated operator tab may be removed by the resident-mode
  projection

#### Scenario: Conditional tabs remain mode-independent

- **WHEN** a butler has conditional tabs defined by name
- **THEN** those conditional tab triggers are appended after the active mode's
  base tabs in both resident and operator modes
- **AND** selecting or deep-linking to a conditional tab does not require
  operator mode
