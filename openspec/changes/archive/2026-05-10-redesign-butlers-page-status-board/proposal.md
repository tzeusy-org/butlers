## Why

The `/butlers` page currently presents butlers and staffers in two separate grouped
sections with dense cards sorted alphabetically. This structure optimises for
discovery of a known butler by name, not for operational scanning of the whole fleet.

An operator opening the page most often wants to answer: "Is everything running?
What ran recently? What is costing money?" The two-group card list buries those
signals behind a visual sort that provides no activity context.

The status-board archetype answers the operational question directly. A single unified
grid sorted by recent activity surface the most-active butlers first. A header strip
gives fleet health at a glance. A footer KPI band answers cost and load without
drilling into any individual butler. The butler/staffer grouping is removed (see
rationale below); composition counts in the footer satisfy the distinction without
fragmenting the grid.

The `redesign-butler-list-card-density` change (now superseded by this one) improved
the card layout but retained the two-group structure. This change goes further: it
replaces the page archetype entirely, from a grouped list to a status board.

## What Changes

- Replace the existing Butler List Page requirement (dashboard-butler-management
  spec, lines 8-77) with a status-board requirement.
- The page archetype changes from a two-section grouped list to a single-grid
  status board: header strip + 4-column cell grid + footer KPI band.
- The sort order changes from alphabetical within groups to `sessions_24h` descending,
  ties broken by name ascending.
- The butler/staffer grouping is removed. Composition counts (Nb butlers, Ns staffers)
  move to the footer KPI band. See "Grouping Decision" below.
- Activity verb is derived client-side from existing hook signals. The mockup's
  `patrol`, `consolidating`, and `ingesting` verbs are rejected. See "Activity Verb
  Decision" below.
- No new fields are added to `ButlerSummary`. Every cell field is composed from
  existing hooks: `useButlers`, `useRegistry`, `useButlerHeartbeats`,
  `useCostSummary('today').by_butler`, and `useSessions({ since: 24h })` bucketed
  client-side. Per-butler load% is derived client-side from
  `active_session_count / max_concurrent * 100` (read from per-butler runtime-config;
  `'--'` when `max_concurrent` is unknown).
- Loading, stale-data, empty-state, and polling scenarios are preserved with cadence
  clarified.

### Grouping Decision

The current spec asserts: "the page preserves the existing butlers and staffers
grouping" (dashboard-butler-management spec line 40). That constraint is removed by
this change.

Rationale: grouping by type produces two short sorted lists with no activity signal.
The operator must scan both lists to find an anomaly. A unified grid sorted by
`sessions_24h` surfaces anomalies at the top regardless of butler type. The
distinction between butlers and staffers remains surfaced, but in the footer
composition addendum (e.g., "8 butlers, 3 staffers") rather than as a page-level
split. The cell's `ButlerMark` component already encodes type visually, so type
legibility in the grid is preserved.

### Activity Verb Decision

The mockup in `pr/overview/new_butlers_page_js.jsx` introduces activity verbs
`patrol`, `consolidating`, and `ingesting`. These verbs are explicitly rejected for
the following reasons:

1. `patrol`, `consolidating`, and `ingesting` imply butler-specific semantic
   knowledge that the butler list endpoint does not expose and that varies by butler
   type. A general-purpose verb derivation from existing signals is more maintainable.
2. `ButlerSummary` does not carry a task-type field. Deriving verbs from it would
   require either new API fields (rejected) or hardcoded per-butler-name heuristics
   (brittle, requires update on every new butler).
3. The existing `status`, `active_session_count`, and `eligibility_state` signals
   are sufficient to communicate the operational state an operator needs.

The approved derivation is: `status=degraded` maps to `paused` (red rail);
`status=waiting OR eligibility=quarantined` maps to `awaiting` or `quarantined`
(amber/red rail); `active_session_count > 0` maps to `running` (green chip); else
`idle` (dim chip). This is purely client-side and requires no new API fields.

## Capabilities

### New Capabilities

None. The status-board archetype itself (the `<Page archetype="status-board">` shell)
is defined in a sibling bead. This change uses that archetype as a consumer.

### Modified Capabilities

- `dashboard-butler-management`: The Butler List Page requirement changes from a
  two-group dense-card list to a status-board with header strip, unified cell grid,
  and footer KPI band. All other requirements in the spec (Butler Detail Page,
  tabs, data fetching, etc.) are unchanged.

## Impact

- Frontend implementation target: `frontend/src/pages/ButlersPage.tsx` is the sole
  affected page file. No other dashboard-butler-management surfaces change.
- Cell data sources (all existing, no new fields):
  - `useButlers` (butler list, `ButlerSummary.name/status/type/description/sessions_24h/active_session_count`)
    via `src/butlers/api/routers/butlers.py:124-131` and `src/butlers/api/models/__init__.py:101-120`
  - `useRegistry` (eligibility state) via `frontend/src/hooks/use-general.ts:24-30`,
    `frontend/src/api/client.ts:1137-1140`, `frontend/src/api/types.ts:1055-1063`
  - `useButlerHeartbeats` (last-seen / heartbeat age) via `frontend/src/hooks/use-system.ts:71-78`
  - `useCostSummary('today').by_butler` (per-butler spend today) via
    `frontend/src/hooks/use-costs.ts:31-47`
  - `useSessions({ since: 24h })` bucketed client-side to produce the 24h activity
    stripe (no new endpoint; existing sessions endpoint filtered by time window)
  - Per-butler `runtime-config.max_concurrent` (for load% denominator) via
    `GET /api/butlers/{name}/runtime-config` (existing endpoint)
- ButlerMark identity component: `frontend/src/components/ui/ButlerMark.tsx`
  (unchanged).
- No database changes, no new backend endpoints, no new `ButlerSummary` fields.
- The `setEligibility` mutation for quarantined/stale chips remains unchanged:
  `frontend/src/hooks/use-general.ts:36-53`.
