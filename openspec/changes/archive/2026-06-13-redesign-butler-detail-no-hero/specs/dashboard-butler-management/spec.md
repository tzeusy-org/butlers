## MODIFIED Requirements

### Requirement: Butler detail page tab body vocabulary

The Butler detail page tab body SHALL map to the four-tier archetype vocabulary as follows:

- **Primary slot:** The `<Tabs>` block, containing `TabsList` (all visible tab
  triggers) and `TabsContent` for each tab. This is the entire primary body
  surface for the butler workspace.
- **No hero slot:** The Butler detail page MUST NOT render a Tier 2 page-level
  Hero, body-level identity card, or identity/action strip above the tabs. Per
  Gate A option A2 (bu-rx6c2), Dispatch status pills and ActionBar controls
  belong in the Tier 1 `<Page>` shell actions area, alongside `<ChatPanel />` and
  without replacing the shell's title or breadcrumbs. The butler identity (name,
  status, description, port, eligibility, heartbeat, and related operational
  facts) is rendered inside the Overview tab's identity/card stack, not in a
  separate page-level hero tier. The Overview tab IS the identity surface; no
  separate hero tier is needed at the page layer.
- **No drawer slot:** Credential and advanced configuration content lives inside
  individual tabs (Config tab, State tab). No top-level practical drawer is needed.
- **Tabs are NOT a candidate for page-level archetype expansion:** The ten
  spec-mandated base tabs plus existing non-spec operator tabs such as Models are
  the correct answer for this workspace-grade record. Future tab consolidation
  (if needed) is a separate audit concern, not a `<Page>` slot concern.

#### Scenario: Base and operator tabs present on all butler pages

- **WHEN** any butler detail page loads
- **THEN** the following ten spec-mandated base tab triggers MUST be visible:
  Overview, Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM,
  Memory
- **AND** the existing non-spec operator tab Models MUST remain visible as a
  capability tab outside the ten-tab base list
- **AND** these are rendered inside the primary slot's `<TabsList>`, unchanged from
  the current implementation

#### Scenario: Gate A A2 controls render in Tier 1 Page actions

- **WHEN** the Butler detail page renders status pills or action controls adapted
  from the Dispatch Hero prototype
- **THEN** those controls MUST be supplied through the `<Page>` shell actions
  area with the existing Butler detail header controls
- **AND** the page MUST NOT render a Tier 2 Hero, body-level identity card, or
  identity/action strip between the Page header and the `<Tabs>` block
- **AND** the Overview tab MUST remain the surface for butler identity fields and
  operational facts
