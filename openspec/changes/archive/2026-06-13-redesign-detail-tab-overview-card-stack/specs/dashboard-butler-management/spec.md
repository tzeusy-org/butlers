## MODIFIED Requirements

### Requirement: Overview Tab

The Butler detail Overview tab SHALL be the identity surface for the selected
butler and SHALL render a dense card stack composed of exactly seven ordered
units: identity card, process facts card, heartbeat row, module health card,
cost card, recent sessions card, and eligibility row. The stack SHALL use
existing butler detail, system heartbeat, module health, cost, sessions, and
switchboard eligibility data surfaces, plus the process-facts fields specified
by `add-butler-process-facts`. To prevent layout shifts, the tab SHALL
maintain a unified loading state by combining loading flags from all data
sources with a logical OR.

#### Scenario: Identity card

- **WHEN** the Overview tab loads for a butler
- **THEN** the first card SHALL display the `ButlerMark` identity component,
  butler name, status badge, and description when present
- **AND** the card SHALL source its detail data from the existing `useButler`
  hook (`frontend/src/hooks/use-butlers.ts:26-33`) and the butler detail
  endpoint response
- **AND** the card SHALL remain inside the Overview tab rather than above the
  tabs as a Tier 2 Hero, per `redesign-butler-detail-no-hero`
  (`openspec/changes/redesign-butler-detail-no-hero/specs/dashboard-butler-management/spec.md:10-18`)

#### Scenario: Process facts card

- **WHEN** the Overview tab renders the process facts card
- **THEN** the card SHALL show `container_name`, `port`,
  `registered_duration_seconds` rendered as a human-readable liveness duration,
  and `config_path`
- **AND** the card SHALL follow the sibling process-facts contract and SHALL NOT
  render, type, or request a `pid` field
  (`openspec/changes/add-butler-process-facts/specs/dashboard-butler-management/spec.md:3-38`)
- **AND** missing source data SHALL render as explicit unavailable values rather
  than hiding the row

#### Scenario: Heartbeat row

- **WHEN** the Overview tab renders heartbeat data for the selected butler
- **THEN** the heartbeat row SHALL show `last_heartbeat_at` and
  `heartbeat_age_seconds` from the system heartbeat surface
- **AND** the backend source SHALL be `GET /api/system/butlers/heartbeat`, which
  reads switchboard `butler_registry.last_seen_at` and computes heartbeat age
  (`src/butlers/api/routers/system.py:639-699`)
- **AND** the frontend SHALL consume that data through `useButlerHeartbeats` or
  an equivalent hook over the same endpoint (`frontend/src/hooks/use-system.ts:71-78`)
- **AND** unavailable heartbeat data SHALL render as an explicit stale/unknown
  row state rather than removing the row

#### Scenario: Module health card

- **WHEN** the butler reports active modules
- **THEN** a Module Health card SHALL render one badge per module, colored by
  status: `connected`/`ok` as emerald, `degraded` as amber, `error` as
  destructive, and other statuses as secondary
- **AND** if no modules are registered, the card SHALL show "No modules
  registered"
- **AND** module health SHALL be sourced through the existing MCP status path:
  `_get_module_health_via_mcp` and the `/api/butlers/{name}/modules` endpoint
  (`src/butlers/api/routers/butlers.py:549-670`)

#### Scenario: Cost card

- **WHEN** cost summary data is available for today
- **THEN** a Cost Today card SHALL show the butler's USD cost, its percentage
  share of the global total, and the global total
- **AND** costs below $0.01 SHALL display as "$0.00"
- **AND** the card SHALL use the existing `useCostSummary("today")` data path
  (`frontend/src/hooks/use-costs.ts:31-47`)

#### Scenario: Recent sessions card

- **WHEN** the Overview tab renders recent activity for the selected butler
- **THEN** a Recent sessions card SHALL show up to five newest sessions for that
  butler
- **AND** the card SHALL use `useButlerSessions(butlerName, { limit: 5 })` or an
  equivalent query over the same butler-scoped sessions endpoint
  (`frontend/src/hooks/use-sessions.ts:27-35`)
- **AND** if no recent sessions exist, the card SHALL show an explicit empty
  state rather than falling back to the old recent notifications feed

#### Scenario: Eligibility row

- **WHEN** the butler has a registry entry from the switchboard
- **THEN** the eligibility row SHALL show `Active` as an emerald badge,
  `Quarantined` as a destructive clickable badge, or `Stale` as an amber
  clickable badge
- **AND** clicking a `Quarantined` or `Stale` badge SHALL trigger a
  `setEligibility(name, "active")` mutation to restore the butler
- **AND** when a quarantine reason exists, it SHALL be shown as muted text next
  to the badge
- **AND** the row SHALL include the existing "24h History" timeline using the
  `EligibilityTimeline` component semantics: segments colored by state
  (`active` emerald-600, `stale` amber-500, `quarantined` red-600), data from
  `GET /switchboard/registry/{name}/eligibility-history?hours=24`, native
  `title` tooltips, window start/now labels, and 60-second refresh cadence
- **AND** the frontend SHALL continue to use the existing registry,
  eligibility-history, and set-eligibility hooks
  (`frontend/src/hooks/use-general.ts:24-53`)
