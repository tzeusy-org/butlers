## MODIFIED Requirements

### Requirement: Butler List Page
The `/butlers` page SHALL show all registered butler and staffer summaries as
dense cards grouped by the existing butlers and staffers sections, with
fleet-level summary statistics.

Implementation source constraints:

- Card fields MUST come from the existing butler list summary shape. `ButlerSummary`
  is defined in `src/butlers/api/models/__init__.py:101-120`, and the list router
  constructs summaries in `src/butlers/api/routers/butlers.py:124-131` with
  `name`, `status`, `port`, `type`, `description`, and `sessions_24h`.
- The card MUST NOT require new fields on `ButlerSummary`.
- The card identity mark MUST use the existing `ButlerMark` component from
  `frontend/src/components/ui/ButlerMark.tsx`.
- Eligibility MUST come from the existing Switchboard registry hook:
  `frontend/src/hooks/use-general.ts:24-30` (`useRegistry()`),
  `frontend/src/api/client.ts:1137-1140` (`/switchboard/registry`), and
  `frontend/src/api/types.ts:1055-1063` (`RegistryEntry.eligibility_state`).
- The list MUST render API-provided butler and staffer rows only; the Dispatch
  prototype's calendar, memory, and household butlers MUST NOT be hardcoded or
  introduced by this page.

#### Scenario: Fleet summary cards
- **WHEN** the butler list page loads
- **THEN** two summary cards are displayed at the top: "Total agents" (count of
  all API-returned butlers and staffers) and "Healthy" (count of API-returned
  butlers and staffers with status `ok` or `online`, with percentage)

#### Scenario: Dense butler and staffer cards
- **WHEN** butler list rows are loaded from the API
- **THEN** the page preserves the existing butlers and staffers grouping
- **AND** each API-returned butler or staffer is rendered as a dense card showing
  its `ButlerMark`, name linked to the detail page, a status pill, the MCP
  endpoint port, an eligibility chip, and its description when one is present
- **AND** each card shows `sessions_24h` as a count; a sparkline is acceptable
  only if it is derived from client-side polling history because the API
  currently exposes a scalar session count rather than a time series
- **AND** each card includes an affordance to open the detail page
- **AND** butlers and staffers are sorted alphabetically by name within their groups

#### Scenario: Status pill color mapping
- **WHEN** a butler's status is rendered as a pill
- **THEN** `ok`/`online` maps to an emerald "Up" pill,
  `error`/`down`/`offline` maps to a destructive "Down" pill, `degraded` maps to
  an amber outline "Degraded" pill, and any other value renders as a secondary
  pill with the raw status text

#### Scenario: Eligibility chip
- **WHEN** a matching registry entry exists for a butler
- **THEN** the card renders a compact eligibility chip using that entry's `eligibility_state`
- **AND** quarantined registry entries expose the quarantine state without hiding the card
- **AND** a butler or staffer with no matching registry entry or an unavailable
  registry response renders an explicit unknown or unavailable eligibility chip
  without hiding the card

#### Scenario: Loading state
- **WHEN** the butler list API request is in flight
- **THEN** a skeleton loading list of six placeholder cards is displayed

#### Scenario: Error resilience with stale data
- **WHEN** a refresh request fails but prior butler data exists in cache
- **THEN** the stale butler cards remain visible with an error banner explaining that the shown data is from the last successful fetch

#### Scenario: Empty state
- **WHEN** the API returns zero butler list rows
- **THEN** an empty-state message is displayed: "No butlers found" with guidance to check daemon status

#### Scenario: Auto-refresh polling
- **WHEN** the butler list page is mounted
- **THEN** the butler list data is polled every 30 seconds to keep status current
