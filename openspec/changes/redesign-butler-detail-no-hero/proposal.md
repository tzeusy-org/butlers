## Why

The Dispatch redesign mockup introduced a Tier 2 Hero block on the Butler detail
page, conflicting with the existing Butler detail no-hero rule and the
workspace-record doctrine landed by bu-ve5re.1. Gate A (bu-rx6c2) resolved the
conflict as option A2: absorb Dispatch status pills and ActionBar buttons into
the `<Page>` shell actions slot while preserving the no Tier 2 hero contract.

## What Changes

- Modify the Butler detail tab/body vocabulary requirement to keep the `<Tabs>`
  block as the primary body slot and explicitly prohibit a Tier 2 page-level hero
  or identity card above the tabs.
- Clarify the A2 placement rule: butler status/action controls that Dispatch
  prototyped inside a Hero move into the Tier 1 `<Page>` header actions area
  alongside `<ChatPanel />`; breadcrumbs and title remain Tier 1 shell props.
- Preserve the existing Overview-tab identity contract: butler name, status,
  description, port, eligibility, heartbeat, module health, cost, and related
  facts remain in Overview-tab cards rather than a page-level identity tier.
- Do not add a `detail-page-archetype` delta. A2 uses the existing Page shell
  action/status primitives and does not sanction the A3 Tier 2 identity-card
  shape.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `dashboard-butler-management`: Updates the Butler detail page tab body
  vocabulary requirement so the chosen Gate A option A2 is normative.

## Impact

- **Specs**: One delta spec under `dashboard-butler-management`.
- **Frontend implementation**: Downstream Epic 01 work hardens the Page detail
  shell and moves Butler detail status/action controls into the Page actions
  slot; downstream Epic 04 work keeps the Overview tab as the identity surface.
- **APIs / database / dependencies**: No API, database, or dependency changes.

## Source References

- bu-rx6c2: Gate A close reason: "Gate A resolved: A2 - absorb Dispatch's
  status pills + ActionBar buttons into the `<Page>` shell's actions slot ...
  Tier 1 header retains title + breadcrumbs; no Tier 2 identity card added.
  Identity stays in the Overview tab card..."
- bu-sfeuw: Epic 01: Harden Page detail shell primitives for Dispatch redesign.
- bu-8hbph: Epic 04: Butler Overview tab redesigned as identity surface.
