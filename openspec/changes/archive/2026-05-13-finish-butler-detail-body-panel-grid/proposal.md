## Why

The Chrome layer of `/butlers/:name` is complete: `<Page archetype="status-board">`,
`<SiblingButlerNav>`, `<ButlerDetailHeader>`, and `<ButlerDetailFooter>` all merged
as part of epic bu-ja5bt. What remains is a mismatch *inside* the tab body.

The `redesign-detail-resident-tabs-claude-design` change specced the Panel atom,
KPI quartet pattern, and per-tab layout contracts. Several tab bodies were
implemented against that spec as part of bu-iuol4. But five surfaces still use
legacy Card wrappers or inline patterns rather than the Panel grid:

1. **Overview tab** - still uses the seven-card stack established by
   `redesign-detail-tab-overview-card-stack`. No recent-activity/attention
   section exists; the Recent Notifications card was removed but not replaced.
2. **Config tab** - the 2x2 panel-grid restyle was specced but the existing
   implementation still renders `RuntimeConfigCard` + individual Card sections
   for each markdown file.
3. **Memory tab** - `ButlerMemoryTab` wraps KPI cells in `<Card>` primitives
   rather than `<Panel>` atoms, and counts are global (not per-butler).
4. **Switchboard Routing Log and Registry tabs** - both wrap their tables in a
   single Card; neither uses the Panel-grid frame.
5. **Sessions and CRM inline helpers** in `ButlerDetailPage.tsx` - both remain
   as inline sub-components using Cards, outside the resident-mode tab panel
   vocabulary.

This change authors the spec decisions needed before implementation beads can
restyle these five surfaces consistently.

## What Changes

- Add a **compact body frame** requirement: explicit 4-column Panel grid
  contract including border topology, responsive span behavior, and the rule
  that no span arrangement may create implicit columns on mobile.
- **Modify the Overview tab** requirement: replace the seven-card stack with a
  compact panel layout covering the same data (identity/status, process facts,
  heartbeat/eligibility, module health, cost, recent sessions, activity feed).
  Decide the Recent Notifications successor: fold into the activity-feed panel.
- **Modify the Config tab** requirement: ratify the 2x2 panel-grid layout
  (process / schedule / scopes-oauth / integrations) from
  `redesign-detail-resident-tabs-claude-design` as the normative surface and
  add missing scenarios for the accordion doc surface.
- **Modify the Memory tab** requirement: align KPI quartet to use `<Panel>`
  atoms, enforce per-butler scope for episode counts, and specify the
  backend delta for per-butler "+N today" sub-lines.
- **Add Switchboard Routing Log tab** requirement: Panel frame replacing Card
  wrapper; table is the panel body, not a card content.
- **Add Switchboard Registry tab** requirement: Panel frame replacing Card
  wrapper; same principle.
- **Decide Sessions and CRM inline helpers**: document as out of scope with a
  follow-up bead; they are operator-mode tabs, not resident-mode tabs.
- Add **activity-feed client contract**: the backend endpoint
  `GET /api/butlers/{name}/activity-feed` exists
  (`src/butlers/api/routers/activity_feed.py`) but has no matching frontend
  client function in `frontend/src/api/client.ts`. The spec makes this a
  required contract.
- Add **per-butler memory delta contract**: `GET /api/memory/stats` returns
  global counts only; there is no endpoint for per-butler episode/fact/rule
  counts. The spec defines the required backend delta.

## Capabilities

### New Capabilities

None beyond what `redesign-detail-resident-tabs-claude-design` already introduced.
All new requirements are refinements of existing tab surface specifications.

### Modified Capabilities

- `dashboard-butler-management`: Overview, Config, Memory, Routing Log, and
  Registry tab requirements are updated or added to align with the Panel-grid
  frame vocabulary. Backend/API contracts are specified for the activity-feed
  client function and the per-butler memory delta endpoint.

## Impact

- **Specs**: One delta spec under `dashboard-butler-management`.
- **Frontend implementation**: Child beads (see tasks.md) restyle each
  surface against this contract.
- **Backend**: One new API contract needed: per-butler memory stats delta
  at `GET /api/butlers/{name}/memory/stats`. The activity-feed endpoint
  already exists; only the frontend client function is missing.
- **Out of scope**: Sessions tab inline helper and CRM tab inline helper
  restyle (documented as follow-up bead). Operator-mode tabs (Sessions,
  Skills, Schedules, Trigger, MCP, State) are unchanged.

## Source References

- bu-ja5bt: Chrome epic (merged). This spec gates body-panel work.
- bu-iuol4: Resident-tab implementation epic (bu-iuol4.13 atoms, bu-iuol4.20
  MemoryTab, etc.).
- `redesign-detail-resident-tabs-claude-design`: Panel-grid frame, Panel atom,
  KPI quartet, Config 2x2, Memory tab requirements.
- `redesign-detail-tab-overview-card-stack`: Overview seven-card stack (being
  modified by this change).
- `redesign-butler-detail-no-hero`: no Tier 2 hero (preserved).
- `add-butler-process-facts`: process facts surface (referenced, not redefined).
- `src/butlers/api/routers/activity_feed.py`: backend activity-feed endpoint.
