## ADDED Requirements

### Requirement: Butler detail page outer chrome conforms to the detail-page archetype

The Butler detail page at `/butlers/:name` SHALL adopt `<Page archetype="detail">`
for its outer chrome. The existing tab structure is the `primary` body slot; the inner
tab content is NOT changed by this requirement.

**Changes from the existing requirement (dashboard-butler-management §Requirement:
Butler Detail Page Structure):**

1. **Shell adoption.** The page MUST use `<Page archetype="detail">` as its outer
   shell. Breadcrumbs (currently "Overview > Butlers > {butler name}" via a standalone
   `<Breadcrumbs>` component) MUST be passed via the `breadcrumbs` prop on `<Page>`.
   The standalone `<Breadcrumbs>` component at the page layer MUST be removed.

2. **Title.** The `title` prop on `<Page>` MUST be the butler's name (`name` field
   from the butler record), titleized (e.g., `"relationship"` → `"Relationship"`).

3. **Actions.** The `<ChatPanel />` button, currently pinned right alongside the H1
   in the page header flex row, MUST be migrated to the `actions` prop on `<Page>`.
   The `<ChatPanel />` component itself is unchanged; only its placement moves to the
   shell's header action slot.

4. **Primary slot.** The `<Tabs>` block (containing the `BASE_TABS`, conditional
   tabs, and `TabsContent` sections) becomes the `primary` body slot rendered inside
   the shell's `children`. No content is removed; the tab structure is preserved
   exactly.

5. **Loading state.** The existing per-tab `TabFallback` loading behavior is
   preserved for lazy-loaded tabs. The shell's `loading` prop MUST reflect the top-level
   butler record fetch status; when the butler record is loading, the shell shows
   `DetailSkeleton` before any tab content renders.

6. **Error state.** When the butler record fetch fails (e.g., unknown butler name),
   the `error` prop on `<Page>` MUST be set. The shell renders the destructive error
   card. Individual tab errors remain tab-scoped.

#### Scenario: Breadcrumbs via Page shell prop

- **WHEN** the butler detail page renders for butler `"relationship"`
- **THEN** breadcrumbs MUST be rendered via the `breadcrumbs` prop on `<Page>`:
  `[{ label: "Overview", href: "/" }, { label: "Butlers", href: "/butlers" }, { label: "Relationship" }]`
- **AND** no standalone `<Breadcrumbs>` component MUST be rendered at the page layer

#### Scenario: Butler name as page title

- **WHEN** the butler detail page renders for butler `"relationship"`
- **THEN** the `<h1>` rendered by the `<Page>` shell MUST read "Relationship"
- **AND** it MUST NOT read "Butler" or "Butler Detail"

#### Scenario: ChatPanel in page header actions

- **WHEN** the butler detail page renders for a resolved butler
- **THEN** the `<ChatPanel />` component MUST appear in the page header row (via the
  `actions` prop), to the right of the title
- **AND** it MUST NOT appear only as a sibling div to the page title at the page layer

#### Scenario: Tabs body is the primary slot

- **WHEN** the butler detail page renders the tab group
- **THEN** the complete `<Tabs>` block (TabsList + all TabsContent entries) MUST be
  rendered as the top-level child inside the `<Page>` shell
- **AND** the tab structure, content, and behavior MUST be unchanged from the current
  implementation

#### Scenario: Top-level loading shows shell skeleton

- **WHEN** the butler record fetch is in flight (before the butler name is resolved)
- **THEN** the `<Page>` shell MUST show `DetailSkeleton`
- **AND** no tab content MUST be rendered during this state

#### Scenario: Unknown butler shows shell error

- **WHEN** a user navigates to `/butlers/nonexistent` and the butler record fetch
  returns 404 or an error
- **THEN** the `error` prop on `<Page>` MUST be set
- **AND** the shell MUST render the destructive error card with the butler name in
  the breadcrumbs for navigation context

---

### Requirement: Butler detail page tab body vocabulary

The Butler detail page tab body SHALL map to the four-tier archetype vocabulary as follows:

- **Primary slot:** The `<Tabs>` block, containing `TabsList` (all visible tab
  triggers) and `TabsContent` for each tab. This is the entire interactive surface
  for the butler workspace.
- **No hero slot:** The butler identity (name, status, description, port) is rendered
  inside the Overview tab's identity card, not in a page-level hero tier. The overview
  tab IS the identity surface; no separate hero tier is needed at the page layer.
- **No drawer slot:** Credential and advanced configuration content lives inside
  individual tabs (Config tab, State tab). No top-level practical drawer is needed.
- **Tabs are NOT a candidate for page-level archetype expansion:** The eleven-plus
  tabs are the correct answer for this workspace-grade record. Future tab
  consolidation (if needed) is a separate audit concern, not a `<Page>` slot concern.

#### Scenario: Base tabs present on all butler pages

- **WHEN** any butler detail page loads
- **THEN** the following ten tab triggers MUST be visible: Overview, Sessions, Config,
  Skills, Schedules, Trigger, MCP, State, CRM, Memory
- **AND** these are rendered inside the primary slot's `<TabsList>`, unchanged from
  the current implementation

## Source References

- Non-Negotiable Rule 2 (The Page is a primitive)
- `detail-page-archetype` spec — archetype conformance requirements
- `about/lay-and-land/detail-page-audit.md` §1.1 (ButlerDetailPage analysis), §6.7
  (ButlerDetailPage migration guidance — "Migrating its outer chrome to `<DetailPage>`
  would be a clean win")
- `frontend/src/components/ui/page.tsx` — `<Page archetype="detail">` implementation
