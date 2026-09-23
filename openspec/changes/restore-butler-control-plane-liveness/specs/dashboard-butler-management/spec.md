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
  `port`, `type`, `description`, and `sessions_24h`.
  The list router constructs summaries in
  `src/butlers/api/routers/butlers.py:124-131`.
  Note: `active_session_count` is NOT a `ButlerSummary` field; it comes from
  `useButlerHeartbeats` (the `ButlerHeartbeat.active_session_count` field,
  `frontend/src/api/types.ts:3626`).
- Cell data MUST be composed exclusively from these five existing data surfaces:
  1. `useButlers` -- butler list (`ButlerSummary` fields)
  2. `useRegistry` -- eligibility state (`RegistryEntry.eligibility_state`)
     via `frontend/src/hooks/use-general.ts:24-30`,
     `frontend/src/api/client.ts:1137-1140`,
     `frontend/src/api/types.ts:1055-1063`
  3. `useButlerHeartbeats` -- last-seen / heartbeat age / active session count
     via `frontend/src/hooks/use-system.ts:71-78`
  4. `useSpendSummary('today').by_butler` -- per-butler spend today via
     `frontend/src/hooks/use-spend.ts:31-47`
  5. Sessions for the last 24h -- fetched via `useQuery(getSessions({ since: <ISO> }))`
     (no new endpoint; the existing sessions endpoint filtered by a rolling ISO
     timestamp, bucketed client-side for the activity stripe)
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

- **WHEN** the butler list page is not showing an initial request failure with
  no cached board rows
- **THEN** a header strip SHALL be displayed containing:
  - An eyebrow label (e.g., "Fleet status")
  - An `h1` reading "The staff, at a glance" styled `text-2xl font-bold tracking-tight`
  - A healthy/total pill (count of healthy butlers over total registered count,
    where healthy = total minus `offline`, `quarantined`, `overdue`, and
    activity-derived `unknown` counts, derived from `StatusBoardAggregates`)
  - A clock and date display rendered via `<Time mode="clock-24h-mono">` that
    updates every minute (aligned to minute boundaries via a 60-second interval)
- **AND** the `unknown` count SHALL be derived from canonical `BoardRow.activity`
  values, not from registry eligibility availability

#### Scenario: Unified cell grid sorted by activity

- **WHEN** butler and staffer list rows are loaded from the API
- **THEN** all butlers and staffers SHALL be rendered in a single 4-column grid of
  butler cells without any grouping by type
- **AND** cells SHALL be sorted by `sessions_24h` descending; ties are broken by
  name ascending
- **AND** no butler or staffer SHALL be hidden from the grid; unavailable registry
  rows SHALL render a dim `--` activity verb without removing the cell

Note: the previous Butler List Page requirement asserted "the page preserves
the existing butlers and staffers grouping." That constraint is removed by
this change. The butler/staffer distinction is preserved in the footer KPI
band composition addendum and visually in each cell's `ButlerMark` component.

#### Scenario: Butler cell composition

- **WHEN** a butler cell is rendered
- **THEN** the cell SHALL display:
  - `ButlerMark` component representing the butler's identity
  - The butler's name, capitalized
  - A role tagline sourced from `ButlerSummary.description`
  - An activity chip showing the derived activity verb (see Activity Verb
    Derivation scenario)
  - A KPI quartet: sessions in the last 24h (`sessions_24h`), spend today
    (from `useSpendSummary('today').by_butler`), load% (derived client-side),
    and last active (last heartbeat timestamp from `useButlerHeartbeats`,
    rendered via `<Time>`)
  - A 24h activity stripe pinned to the bottom of the cell, derived from
    the sessions query (`getSessions({ since: <ISO> })`) bucketed client-side
  - A hover affordance (open arrow or equivalent) linking to the butler detail
    page

#### Scenario: Activity stripe is its own nested door (bu-27dxl.8.3)

- **WHEN** a butler cell renders
- **THEN** the 24h activity stripe region (label + bars/skeleton/error) is a
  nested `<button>`, keyboard-accessible and semantically valid HTML (never a
  nested anchor), with an accessible name naming the butler and the activity
  destination
- **AND** activating it routes to `/butlers/<name>?tab=activity`, independent
  of the root tile's own destination, and does not also trigger the root
  tile's navigation (the click does not propagate to the root)
- **AND** the root tile's own Enter/Space keyboard activation continues to
  route to `/butlers/<name>` (Overview), unaffected by the nested control
- **AND** this door remains present and reachable even while the stripe's
  underlying data is loading or has errored -- a sparse/incomplete stripe
  stays navigable, it just makes no claim of completeness in what it visually
  shows

#### Scenario: Activity verb derivation

- **WHEN** a butler's board row is assembled by `GET /api/butlers/board`
- **THEN** the activity verb (`BoardRow.activity`) and chip color
  (`BoardRow.cell_tone`) SHALL be derived SERVER-SIDE by the canonical
  `_derive_board_activity` function (`src/butlers/api/routers/butlers.py`) --
  this endpoint is the single source of the liveness verdict for every
  butler-status surface, replacing the former client-side per-cell derivation
  (bu-86c4c.17) -- using this first-match-wins priority order:
  1. `status = down` (the raw MCP probe result): verb `offline`, tone `red`
  2. `eligibility = quarantined`: verb `quarantined`, tone `red`
  3. `heartbeat_unavailable` is true, OR the registry `last_seen_at` is
     clock-skewed more than 5 minutes into the future (`clock_skewed`): verb
     `unknown`, tone `neutral` (an untrustworthy heartbeat is not a confidently
     healthy one, the bu-y1am9 clock-skew fold)
  4. `active_session_count > 0`: verb `running`, tone `green`
  5. `cadence_status = overdue` (silent longer than twice the butler's own cron
     cadence per `_CADENCE_OVERDUE_FACTOR`, or longer than 5 minutes when no
     cadence is known): verb `overdue`, tone `amber`
  6. otherwise: verb `idle`, tone `neutral`
- **AND** the complete verb set is `running` / `idle` / `overdue` / `offline` /
  `quarantined` / `unknown`, and the complete tone set is `green` / `amber` /
  `red` / `neutral` (there is no `dim` tone).
- **AND** `eligibility` may also be `unavailable` when the butler registry source
  errored (`_fetch_board_row` sets it when `registry_source_error` is true or no
  registry row exists); `unavailable` is NOT `quarantined`, so it does not
  trigger rule 2, and the row's verb is derived from the remaining heartbeat /
  session / cadence signals.
- **AND** the raw `_probe_butler` status (`BoardRow.status`) is only `ok` or
  `down`; the richer `overdue` / `unknown` verbs are produced by the derivation
  above, not by the probe, and no `degraded` or `waiting` status value is
  produced.
- **AND** the mockup verbs `patrol`, `consolidating`, and `ingesting` MUST NOT
  be used. These verbs imply butler-specific semantic knowledge not carried by
  the board row, and are explicitly rejected.

#### Scenario: Canonical cadence labels

- **WHEN** the board derives a human-facing cadence label from a butler's
  shortest enabled cron interval
- **THEN** exactly one hour, one day, and seven days SHALL be labeled `hourly`,
  `daily`, and `weekly` respectively
- **AND** any other positive interval, including two hours, SHALL be labeled
  `custom`
- **AND** a butler with no enabled schedule SHALL retain a null cadence label
- **AND** the raw `cadence_seconds` and cadence-overdue calculation SHALL remain
  authoritative and unchanged

#### Scenario: Load percentage

- **WHEN** the KPI quartet's load field is rendered
- **THEN** load% SHALL be derived client-side as
  `active_session_count / max_concurrent * 100`
- **AND** `max_concurrent` comes from the per-butler `runtime-config`
  (`GET /api/butlers/{name}/runtime-config`), which is the existing runtime
  config endpoint
- **AND** when `max_concurrent` is unknown or zero, the load field SHALL render as
  `--` rather than a percentage

#### Scenario: Eligibility state rail

- **WHEN** a butler cell is rendered with registry data available
- **THEN** a left-edge state rail on the cell SHALL be colored by eligibility:
  - `active` eligibility: emerald rail
  - `stale` eligibility: amber rail and informational chip; a successful receiver health check clears this state
  - `quarantined` eligibility: red rail, chip is clickable to restore
  - No matching registry entry or unavailable registry response: dim rail
- **AND** clicking a `quarantined` chip SHALL schedule the existing
  `setEligibility(name, "active")` mutation (`frontend/src/hooks/use-general.ts:36-53`)
  a fixed undo window (5s) out behind an "Undo" toast action, rather than
  firing it instantly (restore-with-reason-and-undo, JARVIS audit move 6,
  bu-86c4c.15) -- clicking Undo before the window elapses cancels the
  mutation entirely; letting the window elapse fires it exactly as before
- **AND** the cell SHALL NOT be hidden for any eligibility state, including
  unavailable
- **AND** a `stale` chip SHALL NOT invoke the administrative policy mutation, because setting policy to `active` cannot produce a healthy receiver observation
- **AND** the board query SHALL reconcile receiver liveness at the 30-second cadence even when the fleet event socket is healthy, so both active-to-stale and stale-to-active changes become visible without a session event

#### Scenario: Footer KPI band

- **WHEN** the butler list page is not showing an initial request failure with
  no cached board rows
- **THEN** a footer KPI band SHALL be displayed below the cell grid containing:
  - Active butler count (with emerald status-tone dot, shown only when count > 0)
  - Offline butler count (with red status-tone dot, shown only when count > 0)
  - Quarantined butler count (with red status-tone dot, shown only when count > 0)
  - Fleet sessions in the last 24h
  - Fleet spend today (from `useSpendSummary('today')`)
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
- **AND** this skeleton SHALL be owned by the status-board archetype shell, not by
  the page component directly

#### Scenario: Error resilience with stale data

- **WHEN** a refresh request fails but prior butler data exists in cache
- **THEN** the stale butler cells SHALL remain visible in the grid
- **AND** an error banner SHALL be displayed explaining that the shown data is from
  the last successful fetch
- **AND** the header strip and footer KPI band SHALL remain visible with the cached
  board context

#### Scenario: Initial request failure does not fabricate board context

- **WHEN** the initial butler list request fails and no cached board rows exist
- **THEN** the Page error surface and retry control SHALL render
- **AND** the header strip and footer KPI band SHALL NOT render around the error

#### Scenario: Empty state

- **WHEN** the API returns zero butler list rows
- **THEN** an empty-state message SHALL be displayed: "No butlers found" with guidance
  to check daemon status

#### Scenario: Auto-refresh polling

- **WHEN** the butler list page is mounted
- **THEN** the following polling cadences SHALL be maintained:
  - Butler list (`useButlers`): every 30 seconds
  - Registry and heartbeats (`useRegistry`, `useButlerHeartbeats`): every 30
    seconds
  - Cost summary (`useSpendSummary`): every 60 seconds
  - Header strip clock: updates every minute via `<Time mode="clock-24h-mono">`,
    which aligns to the next minute boundary then fires a 60-second interval
