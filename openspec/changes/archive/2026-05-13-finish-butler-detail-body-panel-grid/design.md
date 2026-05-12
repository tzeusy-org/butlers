## Context

`redesign-detail-resident-tabs-claude-design` established the Panel-grid frame
and the Panel atom as the shared composition vocabulary for resident-mode tab
bodies. `bu-iuol4` implemented Activity, Logs, Approvals, Spend, and the KPI
quartet of MemoryTab. The chrome work from `bu-ja5bt` completed
`<Page archetype="status-board">`, `<SiblingButlerNav>`, `<ButlerDetailHeader>`,
and `<ButlerDetailFooter>`.

Five tab bodies remain on legacy Card patterns. This design document records
the normative decisions for completing the Panel-grid alignment across those
surfaces.

## Goals

- Bring Overview, Config, Memory, Routing Log, and Registry tabs into
  Panel-grid alignment without changing observable data content.
- Resolve the Recent Notifications successor question (Overview tab).
- Resolve the compact body frame contract (explicit vs. implicit mobile columns).
- Specify backend/API contracts the frontend implementation will need.
- Document Sessions and CRM inline helpers as out of scope with a follow-up bead.

## Non-Goals

- Do not change the operator-mode tab vocabulary (Sessions, Skills, Schedules,
  Trigger, MCP, State, CRM).
- Do not add new `ButlerSummary` fields.
- Do not redefine the Panel atom or KPI quartet contracts from
  `redesign-detail-resident-tabs-claude-design`.
- Do not spec the chrome layer (owned by `bu-ja5bt`).
- Do not spec in-flight work: latency-stats (bu-iuol4.6), latency hook wiring
  (bu-r4h6e), tab snap-start polish (bu-0ofvc).

## Decisions

### Compact body frame: explicit span contract, no implicit mobile columns

The 4-column panel grid from `redesign-detail-resident-tabs-claude-design` is
the correct frame. However, the existing spec does not constrain what happens
when all panels in a row span 1 and the viewport is xs (< 640px). A naive
`grid-cols-4` collapses to 4 equal columns that are too narrow to be readable
on mobile.

Decision: the frame MUST use responsive column counts declared in the Tailwind
responsive prefix syntax (e.g., `grid-cols-1 sm:grid-cols-2 md:grid-cols-4`).
A span=2 panel MUST collapse to span=1 at sm and below. A span=4 panel MUST
span the full width at all breakpoints. The implementation may NOT use
`auto-fill` or `auto-fit` because both can create implicit columns that
produce unexpected layouts depending on min column width.

The border topology (frame: `border-top border-left`; panel: `border-right
border-bottom`) is preserved from the existing spec. Responsive collapsing does
not change the border approach because panels only abut each other horizontally
at md+; at sm and below, each panel is full-width and the continuous-grid illusion
does not apply.

### Overview tab: compact panel layout replaces card stack

The seven-card stack from `redesign-detail-tab-overview-card-stack` is functional
but visually inconsistent with the Panel-grid vocabulary used by Activity, Spend,
and Logs. The Overview tab is the first tab the operator sees; it should use the
same atoms.

The data content is unchanged. The seven data units map to panels:

| Card (old) | Panel (new) | Span |
|---|---|---|
| Identity card | identity panel | 2 |
| Process facts card | process panel | 2 |
| Heartbeat row | heartbeat + eligibility panel | 2 |
| Module health card | modules panel | 2 |
| Cost card | cost panel | 1 |
| Recent sessions card | recent sessions panel | 3 |
| (no equivalent) | activity feed panel | 4 |

The identity panel (span=2) carries `ButlerMark`, name, status badge, and
description. The process panel (span=2) carries `container_name`, `port`,
`registered_duration_seconds`, and `config_path`. No `pid` field.

The heartbeat panel (span=2) carries `last_heartbeat_at`, `heartbeat_age_seconds`,
and the eligibility badge/restore control. Folding eligibility into the
heartbeat panel avoids an extra single-cell row and groups liveness signals
together.

The modules panel (span=2) carries the module-health badge list. No change to
data source.

The cost panel (span=1) carries today's USD cost, percent share, and global
total.

The recent sessions panel (span=3) carries the five most-recent sessions. The
span=3 width provides enough room for a compact table row (timestamp, trigger,
duration, status).

The activity feed panel (span=4) is the successor to the removed Recent
Notifications card. It uses the existing
`GET /api/butlers/{name}/activity-feed` endpoint, which merges sessions,
approval actions, and memory writes into a time-ordered event stream. This
resolves the Recent Notifications question: the card is not retained as its own
panel; its data role is subsumed by the activity-feed panel, which is more
complete (covers sessions and memory writes, not only notifications).

Rationale for removing Recent Notifications as a separate panel: the
activity-feed endpoint already merges all three event sources (session_completed,
approval_raised, memory_write). Adding a separate notifications panel alongside
it would be redundant and split operator attention.

### Config tab: ratify 2x2 panel grid from redesign-detail-resident-tabs-claude-design

The `redesign-detail-resident-tabs-claude-design` spec already prescribes the
Config 2x2 panel grid (process / schedule / scopes-oauth / integrations) with a
collapsed accordion for markdown files. The current implementation still uses
the card-per-section layout. This change ratifies those spec decisions as
normative for the restyle bead and adds missing scenarios for the accordion
doc surface.

No design changes relative to `redesign-detail-resident-tabs-claude-design`.
This is a confirmation, not a new decision.

### Memory tab: Panel atoms and per-butler scope

Current `ButlerMemoryTab` wraps KPI cells in `<Card>` primitives (not `<Panel>`
atoms). The KPI counts come from `useMemoryStats()` which returns global counts,
not per-butler counts. The "+N today" sub-lines are not populated (episode delta
is `null`).

Decision: the Memory tab MUST use `<Panel>` atoms for all four KPI cells and
the recent-writes panel. The KPI counts MUST be per-butler. This requires a new
backend endpoint: `GET /api/butlers/{name}/memory/stats` returning per-butler
episode, fact, entity, and rule counts, plus 24h deltas. The existing
`GET /api/memory/stats` is global and MUST NOT be used as the KPI data source
for this tab.

The recent-writes feed (`useMemoryRecentWrites`) is already butler-scoped via
`GET /api/memory/episodes?butler={name}`. No change there.

### Switchboard Routing Log: Panel frame, not Card wrapper

`ButlerRoutingLogTab` wraps `<RoutingLogTable>` in a `<Card>`. The table is
the natural panel body. Decision: remove the `<Card>` wrapper and render the
table inside a `<Panel title="routing log" span={4}>` with `scroll={true}` and
`height="480px"`. The `<RoutingLogTable>` component is not changed; only its
container changes.

### Switchboard Registry: Panel frame, not Card wrapper

Same decision as Routing Log. `ButlerRegistryTab` wraps `<RegistryTable>` in a
`<Card>`. Decision: replace with `<Panel title="butler registry" span={4}>`.

### Sessions and CRM inline helpers: out of scope, follow-up bead

`ButlerSessionsTab` and `ButlerCrmTab` are inline sub-components in
`ButlerDetailPage.tsx`. They are operator-mode tabs, not resident-mode tabs.
Restyling them to Panel-grid vocabulary is a separate, lower-priority concern.
Decision: document as out of scope for this spec. A follow-up bead
(`operator-tab-panel-restyle`) is the correct vehicle.

### Activity-feed client contract: required

The backend route `GET /api/butlers/{name}/activity-feed` exists and is
registered. No matching function exists in `frontend/src/api/client.ts`. The
implementation bead for the Overview activity-feed panel requires this function.
The spec makes it a required contract.

### Per-butler memory stats: required new endpoint

`GET /api/memory/stats` returns global counts across all butlers. The Memory tab
KPI quartet needs per-butler counts. The spec defines the required contract:
`GET /api/butlers/{name}/memory/stats` returning:
- `total_episodes`, `episodes_24h`
- `total_facts`, `facts_24h`
- `total_entities`, `entities_24h`
- `total_rules`, `rules_24h`

Implementation: fan out from the butler's schema tables (`{schema}.episodes`,
`{schema}.facts`, `{schema}.rules`) and the public entity graph filtered by
`butler_name`. This mirrors the pattern in `GET /api/memory/stats` but scoped
to a single butler schema.

## Risks

- Implementers may continue using `useMemoryStats()` (global) for Memory tab
  KPI cells after this spec is merged. The spec explicitly prohibits this and
  requires the new per-butler endpoint.
- Implementers may add `pid` to the Overview process panel by analogy with the
  visual exemplar. The spec cites `add-butler-process-facts` and explicitly
  rejects `pid`.
- Implementers may use `auto-fill`/`auto-fit` on the grid, creating implicit
  mobile columns. The spec requires explicit responsive column classes.
- The activity-feed panel may display fictional butler names if the test
  fixtures are not drawn from the real roster. The spec prohibits hardcoded
  butler names in all render paths.

## Doctrine Compliance Map

| Doctrine rule | How this spec satisfies it |
|---|---|
| Non-negotiable 1 (one token system) | All Panel, KPI, and table cell colors use CSS variable tokens; no hex/oklch/rgb in any scenario. |
| Non-negotiable 2 (Page is a primitive) | Panel grid is the tab body content, not a competing shell. |
| Non-negotiable 4 (Time is typed) | All timestamps in scenarios use `<Time>`; no raw date calls. |
| Non-negotiable 6 (no em-dashes) | All panel titles, empty-state copy, and labels in scenarios are em-dash-free. |
| Voice / copy rules | Sentence case throughout; no exclamation marks; empty states are specific. |
| Butler hue scope | No per-butler hue on any panel chrome; hue restricted to `<ButlerMark>`. |
| Real roster only | No fictional butler names; all render paths source from `useButlers()`. |
