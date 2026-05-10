## MODIFIED Requirements

### Requirement: Butler List Page

The `/butlers` page SHALL render as a status board: a page-level header strip
showing fleet health at a glance, a unified 4-column grid of butler cells
sorted by recent activity, and a footer KPI band summarising fleet state.

The page SHALL use `<Page archetype="status-board">` as its outer shell. The
archetype shell owns the header strip, the cell grid slot, the footer band
slot, and the page-level loading and error states. Pages MUST NOT reinvent
those surfaces.

Implementation source constraints:

- **No new `ButlerSummary` fields.** Every cell field MUST be composed from
  existing data surfaces. `ButlerSummary` is defined in
  `src/butlers/api/models/__init__.py:101-120` and exposes `name`, `status`,
  `port`, `type`, `description`, `sessions_24h`, and `active_session_count`.
  The list router constructs summaries in
  `src/butlers/api/routers/butlers.py:124-131`.
- Cell data MUST be composed exclusively from these five existing hooks:
  1. `useButlers` -- butler list (`ButlerSummary` fields)
  2. `useRegistry` -- eligibility state (`RegistryEntry.eligibility_state`)
     via `frontend/src/hooks/use-general.ts:24-30`,
     `frontend/src/api/client.ts:1137-1140`,
     `frontend/src/api/types.ts:1055-1063`
  3. `useButlerHeartbeats` -- last-seen / heartbeat age via
     `frontend/src/hooks/use-system.ts:71-78`
  4. `useCostSummary('today').by_butler` -- per-butler spend today via
     `frontend/src/hooks/use-costs.ts:31-47`
  5. `useSessions({ since: 24h })` -- session rows bucketed client-side for
     the 24h activity stripe (no new endpoint; existing sessions endpoint
     filtered by time window)
  - Per-butler load% denominator (`max_concurrent`) comes from the existing
    `GET /api/butlers/{name}/runtime-config` endpoint.
- The cell identity mark MUST use the existing `ButlerMark` component from
  `frontend/src/components/ui/ButlerMark.tsx`.
- The list MUST render API-provided butler and staffer rows only. No butler
  names or types may be hardcoded in the grid render path.

**Doctrine citations** (`about/heart-and-soul/design-language.md`):

- Non-negotiable 2: "The `Page` is a primitive." The status-board page uses
  `<Page archetype="status-board">` and does not reinvent its chrome.
- Non-negotiable 1: "One token system or none." No raw `oklch(...)` values,
  hex literals, or ad-hoc inline styles in cell JSX. All colors use design
  tokens.
- Non-negotiable 4: "Time is a typed primitive." The clock display in the
  header strip and all `last` timestamps in cells MUST render via `<Time>`.
- Non-negotiable 6: "No em-dashes in prose." Cell copy, chip labels, KPI
  labels, and empty-state text MUST NOT contain em-dashes.

#### Scenario: Header strip

- **WHEN** the butler list page loads
- **THEN** a header strip is displayed containing:
  - An eyebrow label (e.g., "Fleet status")
  - An `h1` reading "The staff, at a glance" styled `text-2xl font-bold tracking-tight`
  - A healthy/total pill (count of butlers with `status=ok` or `status=online`
    over total registered count)
  - A clock and date display rendered via `<Time>` that ticks every second

#### Scenario: Unified cell grid sorted by activity

- **WHEN** butler and staffer list rows are loaded from the API
- **THEN** all butlers and staffers are rendered in a single 4-column grid of
  butler cells without any grouping by type
- **AND** cells are sorted by `sessions_24h` descending; ties are broken by
  name ascending
- **AND** no butler or staffer is hidden from the grid; unavailable registry
  rows render a dim `--` activity verb without removing the cell

Note: the previous Butler List Page requirement asserted "the page preserves
the existing butlers and staffers grouping." That constraint is removed by
this change. The butler/staffer distinction is preserved in the footer KPI
band composition addendum and visually in each cell's `ButlerMark` component.
See the proposal's "Grouping Decision" section for rationale.

#### Scenario: Butler cell composition

- **WHEN** a butler cell is rendered
- **THEN** the cell SHALL display:
  - `ButlerMark` component representing the butler's identity
  - The butler's name, capitalized
  - A role tagline sourced from `ButlerSummary.description`
  - An activity chip showing the derived activity verb (see Activity Verb
    Derivation scenario)
  - A KPI quartet: sessions in the last 24h (`sessions_24h`), spend today
    (from `useCostSummary('today').by_butler`), load% (derived client-side),
    and last active (last heartbeat timestamp from `useButlerHeartbeats`,
    rendered via `<Time>`)
  - A 24h activity stripe pinned to the bottom of the cell, derived from
    `useSessions({ since: 24h })` bucketed client-side
  - A hover affordance (open arrow or equivalent) linking to the butler detail
    page

#### Scenario: Activity verb derivation

- **WHEN** the activity chip is rendered for a butler cell
- **THEN** the activity verb and chip color SHALL be derived client-side from
  existing signals using this priority order:
  1. If `status = degraded`: verb is `paused`, rail color is red
  2. If `status = waiting` OR `eligibility_state = quarantined`: verb is
     `awaiting` or `quarantined` (prefer `quarantined` when eligibility is
     explicitly quarantined), rail color is amber (awaiting) or red (quarantined)
  3. If `active_session_count > 0`: verb is `running`, chip is green
  4. Otherwise: verb is `idle`, chip is dim
- **AND** the mockup verbs `patrol`, `consolidating`, and `ingesting` MUST NOT
  be used. These verbs imply butler-specific semantic knowledge not carried by
  `ButlerSummary` and are explicitly rejected. See the proposal's "Activity Verb
  Decision" section for full rationale.

#### Scenario: Load percentage

- **WHEN** the KPI quartet's load field is rendered
- **THEN** load% SHALL be derived client-side as
  `active_session_count / max_concurrent * 100`
- **AND** `max_concurrent` comes from the per-butler `runtime-config`
  (`GET /api/butlers/{name}/runtime-config`), which is the existing runtime
  config endpoint
- **AND** when `max_concurrent` is unknown or zero, the load field renders as
  `--` rather than a percentage

#### Scenario: Eligibility state rail

- **WHEN** a butler cell is rendered with registry data available
- **THEN** a left-edge state rail on the cell SHALL be colored by eligibility:
  - `active` eligibility: emerald rail
  - `stale` eligibility: amber rail, chip is clickable to restore
  - `quarantined` eligibility: red rail, chip is clickable to restore
  - No matching registry entry or unavailable registry response: dim rail
- **AND** clicking a `quarantined` or `stale` chip SHALL trigger the existing
  `setEligibility(name, "active")` mutation
  (`frontend/src/hooks/use-general.ts:36-53`)
- **AND** the cell SHALL NOT be hidden for any eligibility state, including
  unavailable

#### Scenario: Footer KPI band

- **WHEN** the butler list page renders
- **THEN** a footer KPI band is displayed below the cell grid containing:
  - Active butler count (with emerald status-tone dot, shown only when count > 0)
  - Paused butler count (with amber status-tone dot, shown only when count > 0)
  - Awaiting butler count (with red status-tone dot, shown only when count > 0)
  - Fleet sessions in the last 24h
  - Fleet spend today (from `useCostSummary('today')`)
  - Fleet average load% (mean of all per-butler load% values where
    `max_concurrent` is known)
  - A composition addendum showing "Nb butlers, Ns staffers" where Nb and Ns
    are the counts of butlers and staffers respectively in the API response

#### Scenario: Loading state

- **WHEN** any butler list data request is in flight on initial load
- **THEN** the page-level skeleton SHALL render:
  - A header strip skeleton line
  - A 2x4 grid of cell skeletons (8 placeholder cells)
  - A footer band skeleton
- **AND** this skeleton is owned by the status-board archetype shell, not by
  the page component directly

#### Scenario: Error resilience with stale data

- **WHEN** a refresh request fails but prior butler data exists in cache
- **THEN** the stale butler cells remain visible in the grid
- **AND** an error banner is displayed explaining that the shown data is from
  the last successful fetch

#### Scenario: Empty state

- **WHEN** the API returns zero butler list rows
- **THEN** an empty-state message is displayed: "No butlers found" with guidance
  to check daemon status

#### Scenario: Auto-refresh polling

- **WHEN** the butler list page is mounted
- **THEN** the following polling cadences SHALL be maintained:
  - Butler list (`useButlers`): every 30 seconds
  - Registry and heartbeats (`useRegistry`, `useButlerHeartbeats`): every 30
    seconds
  - Cost summary (`useCostSummary`): every 60 seconds
  - Header strip clock: ticks every 1 second via a client-side interval

## Source References

- Non-negotiable Rule 1 (one token system, no ad-hoc hex or inline styles)
  `about/heart-and-soul/design-language.md` line 201
- Non-negotiable Rule 2 (`<Page>` is a primitive, no page-level chrome)
  `about/heart-and-soul/design-language.md` line 218
- Non-negotiable Rule 4 (Time is a typed primitive, all timestamps via `<Time>`)
  `about/heart-and-soul/design-language.md` line 251
- Non-negotiable Rule 6 (no em-dashes in prose)
  `about/heart-and-soul/design-language.md` line 269
