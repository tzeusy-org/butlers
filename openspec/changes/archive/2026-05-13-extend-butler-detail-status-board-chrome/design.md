## Context

`openspec/specs/dashboard-butler-management/spec.md` currently requires
`<Page archetype="detail">` on `/butlers/:name` (from the
`detail-page-archetype` change). The `/butlers/` index uses
`<Page archetype="status-board">`, giving the two surfaces a mismatched feel.

Epic bu-iuol4 redesigned the tab content (Overview card stack, bespoke tabs,
resident/operator vocabulary) without touching the page chrome. The chrome
still ships with a fleet-wide `<ButlerHeartbeatTile />` in the pulse slot,
no sibling-butler navigation, and no per-butler KPI footer.

The mockup in `pr/overview/specific-butler-page-redesign/` establishes the
visual target: status-board chrome with a sibling-nav strip, per-butler KPI
footer, and the tab body unchanged. This design document establishes the
normative decisions for translating that mockup into spec requirements.

## Goals

- Make the status-board archetype the outer shell for every `/butlers/:name`
  page.
- Encode the sibling-butler nav strip, per-butler KPI footer, and heartbeat
  tile removal as normative requirements before implementation begins.
- Keep the mode toggle contract (Gate B B2), tab vocabulary, and all existing
  tab content clauses intact.
- Keep the no-Tier-2-hero rule (Gate A A2) intact by referencing
  `redesign-butler-detail-no-hero` rather than redefining it.
- Keep the process facts contract (`add-butler-process-facts`) intact; this
  change does not spec chrome data that overlaps with Overview tab content.

## Non-Goals

- Do not redefine Gate A A2 (owned by `redesign-butler-detail-no-hero`).
- Do not redefine the mode toggle contract (owned by
  `redesign-detail-page-tab-vocabulary`).
- Do not spec bu-iuol4 in-flight work (.6 latency-stats, .19 ButlerSpendTab,
  .20 ButlerMemoryTab, .37 reconciliation).
- Do not spec connector detail-page status-board conformance (separate change
  `connector-detail-archetype-conformance`).
- Do not introduce per-butler chrome customization beyond existing tab
  visibility rules.
- Do not surface pid, port, uptime, or container_name in chrome (those are
  Overview tab process facts, owned by `add-butler-process-facts`).
- Do not add new API endpoints, database fields, or backend changes.

## Decisions

### Status-board archetype replaces detail archetype

`<Page archetype="status-board">` is the correct outer shell for
`/butlers/:name` because the page is a monitoring-first workspace: the owner
reads it to confirm butler health and investigates when something is wrong.
The status-board archetype gives the page header/footer slots suitable for
chrome that the detail archetype lacks. The tab body is unchanged.

This is a shell swap, not a content change: breadcrumbs, title, description,
and actions remain in the Tier 1 shell props exactly as `redesign-butler-detail-no-hero`
and `detail-page-archetype` established.

### Sibling-butler nav goes in the header slot

The sibling-butler nav strip belongs in the Tier 1 Page header slot, not
between the header and the tabs. Placing it below the header but above the
tabs would create a Tier 2 body element, violating Gate A A2. Placing it in
the header slot keeps the chrome entirely in Tier 1 and is consistent with
the status-board archetype's header slot contract.

The strip lists all real-roster butlers from `useButlers()` in the same sort
order as the `/butlers/` index (sessions_24h descending, name ascending). The
active butler is marked `aria-current="page"`.

The strip MUST use only neutral CSS variable tokens (`--border`, `--foreground`,
`--muted-foreground`, `--background`). The butler hue appears only on
`<ButlerMark size="sm">` inside each entry, consistent with the design-language
doctrine on butler-hue scope.

### Per-butler KPI band goes in the footer slot

The footer KPI band is scoped to the active butler only. It contains four
cells: sessions 24h, spend today, load%, and last activity. It reuses the
`<KpiCell>` atom from bu-iuol4.13. Data comes from `useButlerStatusBoard()`
filtered to the active butler's row.

Fleet aggregates MUST NOT appear in the detail-page footer. The detail page is
a single-butler workspace, not a fleet dashboard.

### ButlerHeartbeatTile is removed from detail page, preserved on SystemPage

`<ButlerHeartbeatTile />` is a fleet-wide component that shows heartbeat status
for all butlers. It belongs on the SystemPage, not on a per-butler detail page
where the heartbeat row already lives inside the Overview tab (via
`useButlerHeartbeats`). Removing it from the detail page eliminates the
redundancy and reduces chrome weight.

SystemPage MUST continue to render `<ButlerHeartbeatTile />` unchanged. This
is an explicit preservation clause.

### Mode-aware tab rail overflow

Operator mode has at least 11 tab triggers (10 base + Models + per-butler
bespoke). Under the status-board chrome the tab rail MUST support horizontal
scroll in operator mode so all triggers remain keyboard-reachable. Resident
mode (7 base + per-butler bespoke) MUST fit without a horizontal scrollbar
at md+ breakpoints.

### Chrome token policy

All new JSX in this epic MUST use CSS variable tokens only. This is the
design-language Non-negotiable 1 ("One token system or none"). Butler hue
from the categorical palette MUST appear only on `<ButlerMark>`. No em-dashes
in any JSX strings (design-language Non-negotiable 6). No fictional butler
names from the mockup; real roster only from `useButlers()`.

## Risks

- Implementers may copy the sibling-nav strip placement from the mockup and
  render it between the Page header and the `<Tabs>` block, creating a Tier 2
  element. The requirement explicitly places the nav in the header slot.
- Implementers may add fleet KPI aggregates to the footer band by analogy with
  the `/butlers/` index footer. The requirement scopes the band to the active
  butler only.
- Implementers may leave `<ButlerHeartbeatTile />` on the detail page. The
  requirement and acceptance criteria include an explicit grep assertion.
- Implementers may use butler-hue tokens on nav strip chrome states (hover,
  active border). The token policy requirement and `<SiblingButlerNav>`
  acceptance criteria prohibit this.
