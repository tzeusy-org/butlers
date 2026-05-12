## Why

The `/butlers/` index already ships the Claude Design status-board language
(`<Page archetype="status-board">`, `BoardHeader`, `StatusBoardCell`,
`BoardFooter`). The `/butlers/:name` detail page still uses the legacy
`<Page archetype="detail">` shell with no per-butler chrome. The two surfaces
feel like different products.

Epic bu-iuol4 redesigned the tab CONTENT (Overview card stack, Activity, Logs,
Approvals, Spend, Memory, and per-butler bespoke tabs). That work left the page
CHROME on the legacy shell: a fleet-wide `<ButlerHeartbeatTile />` still
dominates the pulse slot and there is no sibling-butler navigation strip or
per-butler KPI footer band.

This change extends the status-board archetype down to `/butlers/:name` by
encoding the new chrome requirements before implementation begins.

## What Changes

- Modify the Butler detail page outer chrome requirement so `/butlers/:name`
  uses `<Page archetype="status-board">` instead of `<Page archetype="detail">`.
- Add a Sibling-butler navigation strip requirement: a horizontal nav listing
  all real-roster butlers from `useButlers()`, placed in the Page header slot,
  with `aria-current="page"` on the active entry and all chrome tokens from
  the neutral CSS variable set.
- Add a Per-butler footer KPI band requirement: a four-cell band (sessions 24h,
  spend today, load%, last activity) scoped to the active butler, placed in the
  Page footer slot. No fleet aggregates.
- Add a Heartbeat tile placement requirement: `<ButlerHeartbeatTile />` is
  removed from the detail-page DOM; SystemPage usage is preserved.
- Add a Mode-aware tab rail requirement: operator mode keeps the 10
  spec-mandated base tabs + Models with horizontal scroll; resident mode keeps
  the 7-tab Dispatch vocabulary at md+ without a scrollbar.
- Add a Chrome token policy requirement: all chrome uses CSS variable tokens;
  no hex, oklch, or rgb literals; butler hue restricted to `<ButlerMark>`.

## Capabilities

### New Capabilities

- None. All surfaces remain within the existing butler detail page and existing
  data hooks; no new API endpoints or database fields are introduced by this
  chrome change.

### Modified Capabilities

- `dashboard-butler-management`: Updates the Butler detail page outer chrome
  requirement so the status-board archetype is normative on `/butlers/:name`.
  Adds requirements for sibling-butler nav, footer KPI band, heartbeat-tile
  placement, mode-aware tab rail, and chrome token policy.

## Impact

- **Specs**: One delta spec under `dashboard-butler-management`.
- **Frontend implementation**: Epic bu-ja5bt children (.2/.3/.4 primitives,
  .5 wiring, .6/.7 a11y+responsive, .8 tests, .9 doctrine audit) implement
  against this spec contract.
- **APIs / database / dependencies**: No API, database, or dependency changes.
  All data sources are existing hooks: `useButlers()`, `useButlerStatusBoard()`,
  `useButlerHeartbeats()`, `useCostSummary()`.

## Source References

- bu-rx6c2: Gate A close reason: "Gate A resolved: A2 -- no Tier 2 hero;
  actions migrate into Page shell actions slot."
- bu-41p8z: Gate B close reason: "Gate B resolved: B2 -- operator/resident
  mode toggle; 10-tab operator vocabulary preserved."
- bu-ja5bt: Epic description with doctrine gates, work layers, and sequencing.
- redesign-butler-detail-no-hero: owns the no-Tier-2-hero rule (Gate A A2);
  this change references but does not redefine it.
- redesign-detail-page-tab-vocabulary: owns the mode toggle contract (Gate B
  B2); this change references but does not redefine it.
- add-butler-process-facts: owns process facts in Overview tab; this change
  does not spec process facts or pid.
