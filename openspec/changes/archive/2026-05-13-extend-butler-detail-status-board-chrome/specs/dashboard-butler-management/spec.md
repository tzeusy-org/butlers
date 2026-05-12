## MODIFIED Requirements

### Requirement: Butler detail page outer chrome conforms to the status-board archetype

The Butler detail page at `/butlers/:name` SHALL adopt
`<Page archetype="status-board">` for its outer chrome, replacing the previous
`<Page archetype="detail">` shell established by `detail-page-archetype`.

The tab body, mode toggle, breadcrumbs, title, description, and actions props
remain in the Tier 1 shell exactly as established by `redesign-butler-detail-no-hero`
and `redesign-detail-page-tab-vocabulary`. The change is the outer archetype and the
addition of header and footer slots to the shell.

**Changes from the previous requirement (§Requirement: Butler detail page outer chrome
conforms to the detail-page archetype):**

1. **Archetype swap.** The page MUST use `<Page archetype="status-board">` as its
   outer shell. The previous `<Page archetype="detail">` requirement is superseded.

2. **Header slot.** The `<ButlerDetailHeader />` component MUST be passed via the
   `header` prop on `<Page>`. It composes the butler name as H1 title, description,
   status pill, `<ButlerDetailActions>`, and `<SiblingButlerNav>` entirely within the
   Tier 1 header slot. No identity content is placed below the header and above the
   `<Tabs>` block.

3. **Footer slot.** The `<ButlerDetailFooter />` KPI band MUST be passed via the
   `footer` prop on `<Page>`. It renders four cells scoped to the active butler only:
   sessions 24h, spend today, load%, and last activity.

4. **Heartbeat tile removal.** The `<ButlerHeartbeatTile />` component MUST NOT be
   rendered anywhere in the `/butlers/:name` page DOM. SystemPage MUST continue to
   render `<ButlerHeartbeatTile />` unchanged.

5. **No Tier 2 hero.** The Gate A A2 rule from `redesign-butler-detail-no-hero`
   (`openspec/changes/redesign-butler-detail-no-hero/specs/dashboard-butler-management/spec.md:8-18`)
   is preserved: no Tier 2 page-level Hero, body-level identity card, or
   identity/action strip between the Page header and the `<Tabs>` block.

6. **Primary slot.** The `<Tabs>` block (containing `TabsList` for all visible
   tab triggers in the active mode and `TabsContent` for each reachable tab) MUST
   be rendered as the primary body slot inside the `<Page>` shell, unchanged from
   the contract established by `redesign-detail-page-tab-vocabulary`.

#### Scenario: Status-board archetype renders on every butler detail page

- **WHEN** any butler detail page at `/butlers/:name` loads
- **THEN** the outer shell MUST be `<Page archetype="status-board">`
- **AND** the previous `<Page archetype="detail">` shell MUST NOT be used

#### Scenario: Header slot is the sibling-butler nav and detail header

- **WHEN** the butler detail page renders
- **THEN** the `header` prop on `<Page>` MUST receive `<ButlerDetailHeader />`
- **AND** `<ButlerDetailHeader />` MUST be rendered inside the Tier 1 shell
  without any identity content between it and the `<Tabs>` block

#### Scenario: Footer slot is the per-butler KPI band

- **WHEN** the butler detail page renders for a resolved butler
- **THEN** the `footer` prop on `<Page>` MUST receive `<ButlerDetailFooter />`
- **AND** the footer MUST show exactly four KPI cells scoped to the active butler:
  sessions 24h, spend today, load%, and last activity
- **AND** fleet-wide aggregates MUST NOT appear in the footer slot

#### Scenario: Heartbeat tile is absent from detail page

- **WHEN** any butler detail page at `/butlers/:name` renders
- **THEN** `<ButlerHeartbeatTile />` MUST NOT appear in the page DOM

#### Scenario: Heartbeat tile is preserved on SystemPage

- **WHEN** the SystemPage at `/system` renders
- **THEN** `<ButlerHeartbeatTile />` MUST continue to appear in the page DOM
  unchanged

---

## ADDED Requirements

### Requirement: Sibling-butler navigation strip

The Butler detail page SHALL render a sibling-butler navigation strip in the
`<ButlerDetailHeader />` header slot. The strip SHALL list every butler from the
real roster (`useButlers()`) in the same sort order as the `/butlers/` index:
sessions_24h descending, ties broken by name ascending. The strip SHALL be a
Tier 1 chrome element only; it MUST NOT be placed below the Page header or
above the `<Tabs>` block as a body element.

**Doctrine citations** (`about/heart-and-soul/design-language.md`):

- Non-negotiable 1: "One token system or none." All strip chrome MUST use CSS
  variable tokens. No hex, oklch, rgb literals, or inline style outside typed
  primitives on the strip or any entry.
- Non-negotiable 2: "The `Page` is a primitive." The strip lives in the Page
  header slot, not as a freestanding body element.
- Butler hue scope: The butler hue from the categorical palette MUST appear only
  on `<ButlerMark size="sm">` inside each entry. Hover state, active border,
  underline, and any other chrome state MUST use `--border`, `--foreground`,
  `--muted-foreground`, or `--background` tokens only.

#### Scenario: Strip lists all real-roster butlers

- **WHEN** the sibling-butler nav strip renders
- **THEN** it MUST list every butler returned by `useButlers()` in the sort order
  sessions_24h descending, name ascending as a tiebreaker
- **AND** no butler name or type MAY be hardcoded in the strip render path
- **AND** fictional butler names from the visual mockup MUST NOT appear

#### Scenario: Active butler is marked

- **WHEN** the sibling-butler nav strip renders for butler `"relationship"`
- **THEN** the entry for `"relationship"` MUST have `aria-current="page"`
- **AND** all other entries MUST NOT have `aria-current`

#### Scenario: Strip keyboard navigation and ARIA contract

- **WHEN** the sibling-butler nav strip renders
- **THEN** the strip wrapper MUST have `role="navigation"` and
  `aria-label="Navigate to butler"`
- **AND** each entry MUST be a focusable `<Link>` element reachable by Tab key
- **AND** activating an entry with Enter or Space MUST navigate to
  `/butlers/:name` for that butler
- **AND** the strip's tab order in the page MUST come after the actions block
  (`<ButlerDetailActions>`) and before the tab rail

#### Scenario: Strip renders skeleton while butler data loads

- **WHEN** `useButlerStatusBoard()` or `useButlers()` is loading or in error state
- **THEN** the sibling-butler nav strip MUST render a skeleton placeholder
- **AND** the skeleton MUST not crash or render zero-width

#### Scenario: Paused or quarantined sibling butler remains navigable

- **WHEN** a sibling butler has eligibility_state of `quarantined` or status of
  `degraded`
- **THEN** its entry in the sibling nav strip MUST remain a navigable link
- **AND** the entry MUST NOT be aria-disabled or visually removed

#### Scenario: No butler hue on strip chrome states

- **WHEN** a sibling nav entry is hovered, focused, or is the active entry
- **THEN** all chrome state styling MUST use only `--border`, `--foreground`,
  `--muted-foreground`, or `--background` CSS variable tokens
- **AND** butler hue from the categorical palette MUST appear only on
  `<ButlerMark size="sm">` inside the entry

#### Scenario: Query parameters are carried across butler navigation

- **WHEN** the user activates a sibling nav entry while on `/butlers/foo?tab=config&mode=operator`
- **THEN** the navigation MUST carry `?tab=` and `?mode=` query parameters to the
  new butler URL when the target butler supports the same tab and mode
- **AND** unrecognized tab or mode values MUST fall back to defaults rather than
  producing a 404 or broken state

---

### Requirement: Per-butler footer KPI band

The Butler detail page SHALL render a per-butler footer KPI band in the
`<ButlerDetailFooter />` footer slot. The band SHALL show exactly four KPI cells
scoped to the active butler: sessions 24h, spend today, load%, and last
activity. Data SHALL be sourced from `useButlerStatusBoard()` filtered to the
row whose `name` matches the active butler. The band SHALL NOT show fleet-wide
aggregates.

**Data source constraints** (existing surfaces only, no new endpoints):

- Sessions 24h: `sessions_24h` field from the `ButlerSummary` row in
  `useButlers()` or the equivalent `useButlerStatusBoard()` aggregate.
- Spend today: `useCostSummary('today').by_butler[name]` via
  `frontend/src/hooks/use-costs.ts:31-47`.
- Load%: derived client-side as `active_session_count / max_concurrent * 100`
  using `useButlerHeartbeats` for `active_session_count` and
  `GET /api/butlers/{name}/runtime-config` for `max_concurrent`.
- Last activity: last heartbeat timestamp from `useButlerHeartbeats`, rendered
  via `<Time relative>`.

**Token constraint**: All KPI cell chrome MUST use CSS variable tokens. No hex,
oklch, rgb literals, or inline style outside typed primitives.

#### Scenario: Four KPI cells render for the active butler

- **WHEN** the butler detail page footer renders for butler `"relationship"`
- **THEN** exactly four KPI cells MUST be visible: sessions 24h, spend today,
  load%, and last activity
- **AND** all values MUST be scoped to the `"relationship"` butler only
- **AND** fleet-wide totals MUST NOT appear in any cell

#### Scenario: Partial-failure data renders a placeholder glyph

- **WHEN** `max_concurrent` is unknown or zero for the active butler
- **THEN** the load% cell MUST render a neutral placeholder glyph (e.g., `--`)
  rather than collapsing or erroring
- **WHEN** spend data is unavailable for the active butler
- **THEN** the spend cell MUST render a neutral placeholder glyph rather than
  collapsing or erroring

#### Scenario: Last activity uses Time component

- **WHEN** the last activity cell renders a heartbeat timestamp
- **THEN** the timestamp MUST be rendered via `<Time relative>` and MUST NOT use
  `toLocaleString()`, `toISOString()`, or raw `date-fns format()` calls at the
  page layer

#### Scenario: KpiCell atom is reused

- **WHEN** the footer KPI band renders
- **THEN** each cell MUST be composed using the `<KpiCell>` atom from the
  bu-iuol4.13 atoms file (`frontend/src/components/butler-detail/atoms.tsx`)
  rather than an inline reimplementation

---

### Requirement: Mode-aware tab rail overflow under status-board chrome

The Butler detail page tab rail SHALL support two overflow behaviors depending
on the active mode, so that all tab triggers remain keyboard-reachable under
the status-board chrome.

#### Scenario: Operator mode tab rail scrolls horizontally

- **WHEN** the butler detail page renders in operator mode
- **THEN** the `<TabsList>` container MUST have `overflow-x-auto` (or equivalent)
  so that 10 base tabs + Models + any per-butler bespoke tab are reachable via
  horizontal scroll
- **AND** all tab triggers MUST remain in the DOM and be reachable by Tab key
  regardless of whether a horizontal scrollbar is visible

#### Scenario: Resident mode tab rail fits without scroll at md+

- **WHEN** the butler detail page renders in resident mode at viewport width
  >= 768px (md breakpoint)
- **THEN** the `<TabsList>` container MUST NOT show a horizontal scrollbar
- **AND** all seven base resident tabs plus any per-butler bespoke tab MUST be
  visible without scrolling

---

### Requirement: Chrome components SHALL comply with the token policy

New chrome components introduced by this epic SHALL comply with the
design-language doctrine token policy. The components in scope are
`<SiblingButlerNav>`, `<ButlerDetailHeader>`, and `<ButlerDetailFooter>`.

#### Scenario: No hex, oklch, or rgb literals in chrome components

- **WHEN** the new chrome component files are inspected
- **THEN** no hex color literals (e.g., `#7c3aed`), no raw `oklch(...)` values,
  and no raw `rgb(...)` values SHALL appear in the JSX or inline style props
- **AND** all colors SHALL be expressed as CSS variable tokens (e.g.,
  `text-foreground`, `bg-background`, `border-border`)

#### Scenario: Butler hue restricted to ButlerMark

- **WHEN** any sibling nav entry renders in any state (default, hover, active,
  focused)
- **THEN** the butler hue from the categorical palette MUST appear only on
  the `<ButlerMark size="sm">` component inside that entry
- **AND** no other chrome element in `<SiblingButlerNav>`, `<ButlerDetailHeader>`,
  or `<ButlerDetailFooter>` SHALL use a per-butler categorical hue token

#### Scenario: No em-dashes in new JSX strings

- **WHEN** the new chrome component files are inspected
- **THEN** no em-dash character (`--` rendered as `&mdash;` or the Unicode
  character U+2014) SHALL appear in any JSX string literal, description prop,
  label, empty-state text, or toast message
- **AND** commas, colons, or parentheses SHALL be used instead

#### Scenario: Real roster only, no fictional butler names

- **WHEN** the sibling nav strip renders
- **THEN** only butlers returned by `useButlers()` from the real API roster
  SHALL appear
- **AND** no butler name or type MAY be hardcoded in the grid or nav render path
  (per §Requirement: Butler List Page source constraint)

## Source References

- Non-negotiable 1 (One token system or none): prohibits hex/oklch/rgb literals
  in chrome JSX.
- Non-negotiable 2 (The Page is a primitive): status-board archetype as outer
  shell; header/footer slots for chrome, not body elements.
- Non-negotiable 6 (No em-dashes in prose): applies to all new JSX strings.
- bu-rx6c2: Gate A A2 close reason: no Tier 2 hero; Dispatch controls into Page
  actions slot. Referenced by `redesign-butler-detail-no-hero`.
- bu-41p8z: Gate B B2 close reason: operator/resident mode toggle. Referenced
  by `redesign-detail-page-tab-vocabulary`.
- bu-ja5bt: Epic: "Extend Claude Design status-board chrome from /butlers/ index
  to /butlers/{name} detail page."
- redesign-butler-detail-no-hero: owns the no-Tier-2-hero rule.
- redesign-detail-page-tab-vocabulary: owns the mode toggle contract.
- add-butler-process-facts: owns process facts in Overview tab (no overlap with
  chrome).
