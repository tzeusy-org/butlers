## Context

`openspec/specs/dashboard-butler-management/spec.md` already requires the Butler
detail page to use `<Page archetype="detail">`, pass breadcrumbs/title/actions
through the shell, and render the `<Tabs>` block as the primary body slot. The
same spec says there is no hero slot because the Overview tab is the identity
surface.

The Dispatch mockup put status pills, identity metadata, and action buttons in a
body-level Hero between the Page header and the tabs. Gate A (bu-rx6c2) rejected
that shape and chose A2: move controls into the Page shell action area while
preserving no Tier 2 identity card.

## Goals

- Preserve the no Tier 2 hero/body identity-card rule for Butler detail pages.
- Make Gate A option A2 explicit in the `dashboard-butler-management` spec.
- Keep the existing Overview-tab identity clauses intact for Epic 04.
- Keep the change narrow enough that Epic 01 and Epic 04 can implement against
  one unambiguous contract.

## Non-Goals

- Do not introduce Gate A option A3 or a sanctioned Tier 2 identity-card shape.
- Do not add or remove tabs; Gate B owns the tab vocabulary, tab count, and
  conditional-tab behavior in a sibling OpenSpec change.
- Do not specify new process facts, Overview cards, backend fields, or tests in
  detail here; sibling OpenSpec changes and Epic 04 own those surfaces.

## Decisions

### A2 maps Dispatch controls to Tier 1

Status pills and ActionBar buttons from the Dispatch Hero prototype are Tier 1
Page header actions for Butler detail. The Page shell keeps title and
breadcrumbs, and the page supplies the control cluster through the existing
`actions` slot. This keeps action controls visible without creating a body-level
Hero.

### Overview remains the identity surface

The Overview tab retains the butler identity card. That card continues to hold
identity fields and operational facts such as status, description, port,
eligibility, heartbeat, module health, and cost. A header action/status control
is not a substitute identity card.

### No detail-page-archetype delta is needed

A2 fits the existing detail-page archetype contract: Tier 1 is owned by `<Page>`
and accepts action/status controls, while Tier 2 remains optional and unused by
Butler detail. A new detail-page-archetype delta would only be needed for A3,
which was not chosen.

## Risks

- Implementers may accidentally recreate the Dispatch Hero as an unframed body
  block above the tabs. The modified requirement and scenario explicitly forbid
  that.
- Implementers may move identity metadata out of Overview to make the Page
  action cluster richer. The requirement distinguishes controls from identity
  content to prevent that drift.
