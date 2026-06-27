# Dashboard Design Language

> Status: **observational draft**, written by a design consultant on first
> contact with the codebase. This document captures what the dashboard's
> design language *appears to be today* (de facto), names what is working,
> names what is drifting, and proposes the doctrine that a future
> `/impeccable` redesign should defend or replace.
>
> **Note:** This document itself is subject to the voice rules it establishes.
> Em-dashes remaining in this file appear only inside fenced code blocks or
> inline code spans, which are exempt from the ban.

The Butlers dashboard is the human window into a system that mostly runs
without humans watching. It is read first, controlled second. The design
language must respect that.

This document does **not** specify components, tokens, or pages. Those belong
in [`about/lay-and-land/frontend.md`](../lay-and-land/frontend.md) (where
things are) and in capability specs (what they do). This document is
**WHY**: the principles a Butlers dashboard must satisfy regardless of
which framework or visual style we land on.

---

## What the Dashboard Is

The dashboard is a **read-mostly observability surface for a personal
multi-agent system**. It exists so the owner can:

1. Trust that butlers are alive and behaving.
2. Investigate when one of them isn't.
3. Override or correct behavior when needed.
4. Understand what the system *did with their data* (episodes, contacts,
   memory facts, notifications, costs).

Every screen is in service of one of those four jobs. If a screen does not
serve them, it does not belong.

The dashboard is **single-tenant**. There is exactly one user. There is no
"team," no "permissions tier," no "workspace switcher." Every design pattern
that assumes multi-user SaaS conventions (avatars in nav, role badges,
"invite teammates" CTAs) is a category error here.

## What the Dashboard Is Not

- **Not a control plane for end users.** The end user of a butler is the
  owner, but their channel is messaging, not the dashboard. The dashboard
  is the *operator's* surface (the same person, but in a different mode).
- **Not a chat app.** Conversational flows belong in the messaging layer.
  The dashboard's job is to *show what happened*, not to *be the place
  things happen*.
- **Not a generic admin template.** The dashboard's structure should be
  legible to its single user, not to an imagined enterprise admin
  persona. Patterns like "user management → roles → permissions" do not
  apply.
- **Not a marketing surface.** Hero illustrations, gradient banners,
  "delight" microinteractions for their own sake. None of these earn
  their pixels here.
- **Not a uniform information feed.** Different butlers produce
  fundamentally different data shapes (timelines, graphs, episodes,
  facts, conversations). Forcing them into one rectangular table view is
  a regression.

---

## De Facto Design Language Today

The system does have a design language. It is just **implicit and
inconsistently applied**. Naming it makes it easier to reason about.

### Stack
- React 18 + Vite + TypeScript
- shadcn/ui (style: `new-york`) on Tailwind v4 with `@theme inline` plumbing
- Radix primitives (Dialog, Sheet, AlertDialog, DropdownMenu, etc.)
- TanStack Query for data, React Router v7 for routing
- `lucide-react` icons, `sonner` toasts, `recharts` charts, `@xyflow/react`
  graphs, `maplibre-gl` maps
- Custom `useDarkMode` hook controlling a `.dark` class on `<html>`

### Visual Tokens
- **Color:** semantic tokens declared in oklch in `src/index.css`
  (`--background`, `--foreground`, `--primary`, `--muted`, `--accent`,
  `--destructive`, `--border`, plus `--chart-1..5` and a parallel
  `--sidebar-*` set). Light is the default, dark is a class override.
- **Radius:** single base `--radius: 0.625rem` with derived
  `--radius-sm/md/lg/xl/2xl/3xl/4xl`.
- **Type:** system stack (`system-ui, -apple-system, …`), no custom
  family, weights 400–700, sizes via Tailwind defaults. There is **no
  type scale documented**.
- **Spacing:** `space-y-6` is the implicit page rhythm. There is no
  documented spacing scale beyond Tailwind's defaults.

### Composition
- **Shell** (`components/layout/Shell.tsx`): fixed-height, full-bleed,
  three-zone: left rail (sidebar), top bar (PageHeader, 56px), main
  scroll region (24px padding). Mobile collapses the rail into a Sheet.
- **PageHeader** (`components/layout/PageHeader.tsx`): auto-generated
  breadcrumbs from URL segments, optional injected title, command-palette
  and dark-mode toggles on the right. Each segment is title-cased
  naively (`/qa/investigations` → `Qa / Investigations`).
- **Sidebar** (`components/layout/Sidebar.tsx`): three sections
  (`Main`, `Dedicated Butlers`, `Telemetry`) configured declaratively in
  `nav-config.ts`, with butler-presence filtering and live badge counts
  hooked up via `useBadgeCounts`. Today's spend lives in the footer.
- **Pages**: each route is a flat component under `src/pages/`. The
  first-contact snapshot found no shared `<Page>` wrapper and a
  hand-rolled page-title pattern on every page. That gap has since been
  closed: a `<Page>` primitive (`frontend/src/components/ui/page.tsx`)
  now owns title, description, breadcrumbs, the actions slot, loading,
  error, and empty states, and renders the H1 as `text-3xl font-bold
  tracking-tight`. Adoption is partial (roughly 18 archetype mounts
  across the page tree as of 2026-06-27); the remaining pages still
  hand-roll their header and are migration targets, not the destination.
- **Cards** are the dominant container; the shadcn `Card` family
  (`Card / CardHeader / CardTitle / CardDescription / CardAction /
  CardContent / CardFooter`) is used everywhere.

### Voice
The voice is **technical, terse, slightly formal, occasionally playful.**
It treats the user as a sysadmin who already understands the domain.
Examples: "Retrospective view of lived past time reconstructed from butler
evidence." "All systems healthy." "QA Staffer not active."

This is the right register for the audience. The drift is mechanical,
not tonal: sentence-case vs Title Case, "Loading…" vs "Loading butlers…".

---

## What Is Working

1. **The token system is honest.** Colors are declared once, in oklch,
   with light/dark parity. The `@theme inline` block in `index.css`
   bridges them cleanly into Tailwind utilities. This is a good
   foundation.
2. **The shell is small and uncluttered.** A 56px top bar, a 256px
   sidebar (collapsible to 64px), and breadcrumbs that are quiet by
   default. The chrome gets out of the way.
3. **Component primitives are consistent.** One `Button`, one `Card`,
   one `Dialog`, one `Sheet`. There are no parallel implementations of
   the same primitive. The divergence is at the page level, not the
   component level.
4. **Navigation is configuration-driven.** `nav-config.ts` is a single
   declarative source of truth, and butler-presence filtering means the
   sidebar reflects the actual instance (a Health butler that isn't
   installed simply doesn't appear). This is a design language
   superpower.
5. **The dark mode is real.** Both themes are designed, not retrofitted.
   The chart palette flips meaningfully (warm in light, cool in dark).

## What Is Drifting

> Reconciliation note (2026-06-27): this list is the first-contact
> snapshot. The `/impeccable` redesign captured in the doctrine sections
> below has since shipped primitives that resolve or shrink several of
> these items. Resolved items are annotated inline; they are kept for the
> historical record, not because they still describe the live UI.

1. **Page chrome is reinvented per page.** Heading sizes vary
   (`text-2xl` on Dashboard and Costs; `text-3xl` on Butlers and
   Chronicles), description placement varies, action-button rows have
   no canonical position. There is no `<Page>` / `<PageHeader>`
   container component to hold the shape.
   *Resolved (partial): the `<Page>` primitive
   (`frontend/src/components/ui/page.tsx`) now owns the page shape and
   the canonical `text-3xl font-bold tracking-tight` H1. Adoption is
   in progress; unmigrated pages still hand-roll the header.*
2. **The token system leaks.** Several pages reach for raw hex when
   they need a non-semantic color: `EntitiesPage.tsx` lines 102–113 hard-code
   six tier colors; `EntityDetailPage.tsx` lines 313/316 inline
   `#7c3aed` and `#f59e0b`; `SymptomsPage.tsx` does the same for
   severity; `GroupsPage.tsx` line 121 keeps a hex palette array. The
   project ran out of semantic tokens at "destructive" and stopped.
   *Resolved (mostly): `index.css` now declares named
   `--severity-*`, `--permanence-*`, and `--role-*` token sets with
   light/dark parity, and `EntitiesPage.tsx` carries no inline hex.
   The remaining raw-hex usage is narrow (a color-input placeholder in
   `GroupsPage.tsx`, plus a few in `CalendarWorkspacePage.tsx` and
   `SettingsSpendPage.tsx`) and is a residual cleanup, not the
   systemic leak the snapshot described.*
3. **Repeated patterns are not yet components.** A `StatsCard` shape is
   reimplemented inline in DashboardPage, CostsPage, QaOverviewPage,
   and others. Loading skeletons are bespoke per page. Empty states
   are split between the shared `EmptyState` component and ad-hoc
   inline divs.
4. **Date and time formatting is anarchy.** Three formatters appear
   across pages: `toLocaleString()` (locale-dependent), `toISOString()`
   (raw), and `date-fns format()` (curated). There is no single
   `<Time />` component or formatter helper, and no decision about
   whether the dashboard renders in the *user's* timezone or the
   *butler's*.
   *Resolved: the `<Time>` primitive
   (`frontend/src/components/ui/time.tsx`) now exists with `mode`
   (`absolute` / `relative` / `smart` / `clock-24h-mono` /
   `relative-compact`) and `precision` props, renders in the owner
   timezone via `formatInTimeZone()`, and is consumed across pages
   (Chronicles, QA, Sessions, Groups, and more). The
   `CalendarWorkspacePage` grid-label exemption is documented under
   Non-negotiable rule 4.*
5. **Visualization is unevenly committed.** `recharts` for bars/pies,
   `@xyflow/react` for topology, `maplibre-gl` for the chronicles map,
   and ad-hoc inline SVG / styled `<div>` widths for everything else
   (progress bars, calendar grid heights). Nothing wrong with three
   libraries, but the seams are visible.
6. **Tone is mostly consistent but capitalization is not.** "Force
   Patrol Now" sits next to "Sync now" sits next to "View all
   notifications." Empty-state copy ranges from terse ("No butlers
   found") to quasi-prose ("Patrol cycles will dispatch investigations
   when novel issues are detected"). Each is fine in isolation; together
   they read like four people writing.
7. **`PageHeader` does too little to claim its name.** It generates
   breadcrumbs from the URL and renders an optional title, but it is
   *invoked once* in `RootLayout` with no title, so every page
   re-implements its own H1. The component name suggests a contract it
   does not deliver.
   *Resolved (partial): the H1 contract now lives in the `<Page>`
   primitive, which owns the title, breadcrumbs, and actions slot for
   migrated pages. `PageHeader.tsx` still exists as the shell breadcrumb
   bar; the per-page H1 duplication remains only on pages not yet
   migrated onto `<Page>`.*
8. **Information density varies wildly without acknowledgment.**
   `DashboardPage` is sparse and lyrical. `QaOverviewPage` is dense
   with five+ regions stacked vertically. `ChroniclesPage` is its own
   information-architecture proposal (scrubber + Gantt + map +
   aggregates). These are not the same kind of page, and the design
   language has not yet decided whether they should look like they
   come from the same product.

---

## Candidate Doctrine for an `/impeccable` Redesign

These are *proposals*, not settled rules. Each should be debated against
the use case before being treated as binding.

### Non-negotiable (proposed)

1. **One token system or none.** Every color, radius, font size, and
   spacing value used in a page must come from the token system. Hex
   literals and inline `style={{ ... }}` for visuals are bugs. If a
   token does not exist for what the page needs (e.g., severity tiers,
   chart category palettes), the right move is to *add a named token*,
   not to inline a value.
   - **Exemption: chart palettes.** The five `--chart-*` tokens in
     `index.css` and recharts color props that consume them are
     intentionally a separate visual axis (categorical data
     differentiation). The hex-purge in vertical C does not touch
     `index.css` chart variables or recharts config; only ad-hoc hex
     in JSX is a bug.
   - **Exemption: typed primitives that own one style prop.** A
     `<Progress value={0.42}/>` whose internal `style={{ width: '42%' }}`
     is unavoidable does not violate the rule. The ban targets ad-hoc
     inline styles, not encapsulated dynamic values inside a typed
     primitive.
2. **The `Page` is a primitive.** Every route renders inside a
   `<Page>` shell that owns title, description, breadcrumbs, action
   bar, loading state, error state, and empty state. Pages compose
   sections inside it; they do not reinvent the chrome.
   - **Standard H1 size is `text-3xl font-bold tracking-tight`.**
     Canonical per `<Page>` as shipped (`frontend/src/components/ui/page.tsx`)
     and the dashboard-shell spec (`openspec/specs/dashboard-shell/spec.md`):
     the `<h1>` is owned by `<Page>` and uses `text-3xl font-bold
     tracking-tight` for all standard (non-editorial) pages. This ratifies
     the shipped shape and supersedes the earlier detail-page-audit choice of
     `text-2xl` (decision bu-23bgb): the heading weight stays `font-bold`
     (700) for standard chrome: the Dispatch "Display weight is 500" rule
     below applies only to the Display/Editorial tier, not the standard H1.
   - **Editorial archetype gets a Display tier.** The Overview opens
     with a Display headline instead of the standard H1. The Display
     tier is reserved for editorial pages where the system is speaking
     in sentences (see [Editorial archetype](#editorial-archetype)).
     It is not available to drilldown, workspace, feed, log, or graph
     archetypes; those keep the standard `text-3xl` H1. Display tier sizing:
     **44px / weight 500 / tracking -0.025em / leading 1.08** (sans).
     Full token table in [`about/lay-and-land/frontend.md`](../lay-and-land/frontend.md)
     under the Type tokens section.
   - **EntityDetailPage Editorial vs Workbench (Amendment 7).** The
     EntityDetailPage has two modes: Editorial and Workbench. Editorial
     is an editorial-archetype page and the Display 44px headline
     carve-out above applies: it renders via `<Page archetype="editorial">`
     which renders the title as a Display 44px headline when breadcrumbs
     or actions are supplied (bu-hm0oe: resolution b, additive, does not
     touch `archetype="detail"`). Workbench is a workspace-grade record
     page: it renders via `<Page archetype="overview">` (the `workspace`
     archetype gap is left to Phase 2 per entity-brief.md R3; `overview`
     is the interim choice), keeps the standard `text-3xl` H1, and does
     not get the Display tier. The Editorial/Workbench toggle is a
     `localStorage`-persisted mode switch in the Page shell's actions
     slot; the two modes share one route and one `<Page>` mount. Per
     Amendment 7 update (2026-05-17-entity-brief.md §6b): the Display
     44px tier satisfies the 1.2 type-ratio floor (44/24 = 1.83, above
     the floor) and requires no further reconciliation.
   - **Workspace-grade record pages do not get a tier-2 hero.**
     Butler detail, contact detail, conversation detail, and similar
     operator record pages keep identity in the `<Page>` shell title
     and in the overview tab's identity card. Status pills and primary
     actions belong in the `<Page>` actions slot when they need page
     reach. Do not introduce a page-level identity strip, letter-mark
     hero, or second header tier between the shell and the tab body.
     This is the settled Gate A A2 rule from `bu-rx6c2`: the tier-1
     header keeps title and breadcrumbs, actions migrate into the
     shell, and the overview tab remains the identity surface.
   - **Type ratio is 1.2** (product-register override of impeccable's
     shared `≥1.25` floor). Per `impeccable/reference/product.md`:
     "tighter scale ratio. 1.125–1.2 between steps is typical for
     product UI." The dashboard is product, not brand.
3. **Information density is a deliberate dial, not an accident.**
   Each page declares its archetype, and the archetype determines
   layout, not the author. The shipped archetype set is the seven-member
   union owned by the `<Page>` primitive (`frontend/src/components/ui/page.tsx`):
   `overview`, `list`, `detail`, `workspace`, `editor`, `editorial`,
   and `status-board`. This supersedes the earlier aspirational naming
   (`drilldown`, `feed`, `log`, `graph`) from the first draft: `detail`
   absorbed `drilldown`, `list` absorbed `feed` and `log`, and the
   graph-heavy pages render inside `workspace` or `status-board` rather
   than a dedicated `graph` archetype. New pages pick from the shipped
   seven; adding an eighth is a `<Page>` change, not a per-page choice.
4. **Time is a typed primitive.** All timestamps render via a single
   `<Time>` component that knows the user's timezone, the butler's
   timezone, the desired precision, and the relative-vs-absolute mode.
   `new Date(x).toLocaleString()` in a page file is a bug.
   - **Exemption: calendar layout helpers.** The `CalendarWorkspacePage`
     uses `date-fns format()` for calendar-grid structural labels:
     navigation headers (`"MMMM yyyy"`, `"EEE, MMM d, yyyy"`), date
     ranges (`"MMM d, yyyy"`), grid cell day labels (`"d"`, `"EEE d"`),
     time-axis labels (`"h a"`), and inline event time ranges
     (`"MMM d, HH:mm"`, `"h:mm a"`). These are grid-coordinate displays
     intrinsic to the calendar layout, not user activity timestamps.
     They are exempt from `<Time>` migration. Do not extend this
     exemption beyond `CalendarWorkspacePage`.
5. **Voice is owner-direct.** Sentence case for everything except
   proper nouns and product names. Active verbs in buttons. No
   chatty marketing language. No empty enthusiasm.
   Full rules are in the [Voice and Copy](#voice-and-copy) section
   under Settled Direction.
6. **No em-dashes in prose.** The em-dash (`—`) is banned from all
   copy written for the dashboard and from all doctrine documents.
   Replacements: a comma, a colon, or parentheses, depending on
   the relationship the dash was carrying. This rule applies to
   JSX strings, `description` props, `CardDescription`, `EmptyState`
   descriptions, toast messages, and doc prose. It does not apply
   to code inside code blocks or to strings used as data values
   (e.g., a null-display fallback `"—"` is acceptable as a
   typographic convention, not prohibited prose).

### Worth debating

- Whether to keep a single `Card` as the dominant container or to
  introduce a flatter "section" pattern for high-density screens like
  QA and Chronicles.
- Whether the sidebar should keep its first-letter-as-icon glyphs or
  commit to real icons (the current approach is honest but cheap).
- Whether the breadcrumb autobuilder is worth keeping (it produces
  awkward output: "Qa / Investigations") or whether each page should
  own its breadcrumbs explicitly.

(The typographic-scale debate item from earlier drafts is resolved.
See [Type system](#type-system) below for the settled three-family
stack and scale.)

---

## Theme commitment

> Status: **settled** (bu-iaw5h.1).

**Physical scene:** I open this dashboard at 10pm from a dim room after
reviewing my day, and again at 8am from a bright kitchen while coffee is
brewing; the evening glance is more deliberate and more frequent than the
morning check.

**Decision: dark-primary with light fallback.**

Dark is the designed-first mode. Every color token, chart palette, and contrast
ratio is tuned against a dark background under dim ambient. Light mode is a
supported fallback, available via the theme toggle, but it is not the primary
design target. If a design decision requires a trade-off, the dark experience
wins.

This is not "dual-with-dark-default." That pattern keeps both modes at equal
weight and treats the default as a preference setting. This pattern treats dark
as the canonical surface. Light degrades gracefully but is not independently
designed from first principles.

---

## Light-mode accessibility floor

> Status: **settled** (bu-h3k9n). Companion to "Theme commitment" above.

Light mode is a supported fallback, not the canonical surface. The minimums
below are what "degrades gracefully" means in concrete, auditable terms. Each
minimum is anchored to a WCAG 2.1 criterion or a stated product rationale.

We promise **WCAG AA contrast minimums** (color contrast and non-text contrast)
for the light-mode fallback. We do not promise full AAA, nor do we promise
every WCAG AA criterion beyond contrast. AAA contrast (7:1 normal, 4.5:1
large) is desirable but not required; if chasing it breaks the palette
coherence established in dark mode, AA wins.

### Body text and primary UI labels

**Minimum: 4.5:1 against the page background (WCAG 1.4.3, AA normal text).**

The token pair that must satisfy this is `--foreground` on `--background`.
Current light values: `--foreground oklch(0.145 0 0)` on `--background
oklch(1 0 0)` (white). Estimated contrast: approximately 15:1. This is well
clear of the floor; do not raise `--foreground` L above 0.35 in light mode
without re-verifying.

**Muted text (`--muted-foreground`) is not body text.** It is a supplemental
label tier (secondary stats, metadata, timestamps). Its light-mode value of
`oklch(0.556 0 0)` yields approximately 3.7:1 on white, which satisfies WCAG
AA for large text (18pt regular / ~24px, or 14pt bold / ~19px) but not for
normal-weight small text. This is an accepted trade-off, with the constraint
that muted-foreground text must never be the primary semantic carrier for a
piece of information.
If a string is the only place where critical meaning appears, it must use
`--foreground`, not `--muted-foreground`.

### Interactive elements and component boundaries

**Minimum: 3:1 against adjacent background colors (WCAG 1.4.11, AA
non-text contrast).**

Button fills, badge backgrounds, input borders, and icon-only controls must
meet 3:1 against their containing surface in light mode. The current
`--primary oklch(0.205 0 0)` on white substantially exceeds this. Borderline
cases are outlined icon buttons and ghost variants where only the border
provides the boundary signal: the border must not drop below 3:1 against the
page background.

### Focus states

**Minimum: 3:1 between the focus indicator and its adjacent color
(WCAG 2.4.7 requires focus visible; the 3:1 ratio is defined in WCAG 2.4.11,
a WCAG 2.2 criterion for Focus Appearance).**

The light-mode focus token is `--ring: oklch(0.708 0 0)`. Its estimated
contrast against white (1.0) is approximately 2.2:1, which does not meet the
3:1 floor on its own. This is a known gap.

Accepted mitigations until the token is corrected:

- shadcn components render `--ring` with a visible outline offset, placing the
  ring against the element's own surface (not the page background). In that
  configuration the effective adjacent color is the component surface, which
  is typically darker than the page background, bringing the contrast above
  3:1.
- Any new component that renders focus as a ring directly against the white
  page background must augment `--ring` with an additional 1px solid dark
  outline or must darken the ring to at least `oklch(0.45 0 0)` in the
  light-mode override.

The preferred long-term fix is to lower `--ring` in light mode to
`oklch(0.45 0 0)` or supply a dedicated `--ring-light` token.

### Semantic and categorical colors used as information carriers

**Minimum: 3:1 on white for any semantic color used as the sole carrier of
meaning (WCAG 1.4.11).**

This applies to severity badges, permanence indicators, role badges, state
badges, and Dunbar tier ramp colors. Estimated values for the current
light-mode tokens against white:

| Token group | Key example | Estimated contrast | Verdict |
|---|---|---|---|
| `--severity-high` (red) | `oklch(0.627 0.257 29.2)` | ~5:1 | Passes AA |
| `--severity-medium` (amber) | `oklch(0.769 0.189 84.0)` | ~3.5:1 | Passes large/UI |
| `--severity-low` (green) | `oklch(0.723 0.198 148.2)` | ~2.6:1 | Needs label or icon |
| `--permanence-permanent` (blue) | `oklch(0.488 0.243 264.4)` | ~6:1 | Passes AA |
| `--role-owner` (violet) | `oklch(0.491 0.270 275.1)` | ~5:1 | Passes AA |
| `--destructive` (red-orange) | `oklch(0.577 0.245 27.325)` | ~5:1 | Passes large/UI |

`--severity-low` (green) in light mode falls below 3:1 on white. The rule:
**severity-low must never be the only visual signal.** It must be paired with
a text label or icon. The color is a reinforcement, not the carrier. This is
the same treatment as WCAG's color-not-alone rule (1.4.1).

`--severity-medium` (amber at L=0.769) passes 3:1 only barely for large/bold
text and UI boundaries; it does not pass 4.5:1 for normal text. Apply the
same pairing rule: always accompany amber severity with a text label.

### Chart distinguishability under common color vision deficiencies

**Minimum: adjacent chart series must differ by at least 0.15 L in OKLCH, or
by hue angle separation exceeding 60 degrees, when rendered in light mode.
Pairs that fail both criteria are permitted only when the chart includes a
legend or direct data labels so that color is not the sole distinguishing
signal. This is not a WCAG criterion; it is a stated product floor anchored
in practical legibility for deuteranopic and protanopic users.**

The light-mode chart palette (`--chart-1` through `--chart-5`) passes this
floor on hue separation:

- `--chart-1` orange (H=41) vs `--chart-2` teal (H=185): 144 degrees apart.
  Under deuteranopia, teal desaturates but remains lighter than orange at
  equivalent chroma. Distinguishable.
- `--chart-3` slate-blue (H=227): clearly distinct under all common
  deficiency types. Blue is largely preserved under both deuteranopia and
  protanopia.
- `--chart-4` soft-yellow (H=84, L=0.828) vs `--chart-5` soft-amber (H=70,
  L=0.769): hue separation is only 14 degrees. These two are the at-risk
  pair. The L difference of 0.06 falls below the 0.15 floor. Rule: when
  `--chart-4` and `--chart-5` appear in the same chart, the chart must include
  a legend or direct label; relying on color alone to distinguish them is not
  allowed in light mode.
- `--chart-1` orange (H=41) and `--chart-5` amber (H=70): 29 degrees apart.
  Both become warm yellows under deuteranopia; the L difference of 0.12 also
  falls below the 0.15 floor. Same rule: use labels or patterns when these two
  series co-appear.

### What is out of scope

- **AAA compliance.** We do not promise 7:1 or 4.5:1 for large text in light
  mode. If AAA is achievable without forcing a palette divergence between dark
  and light modes, take it. If it creates divergence, AA wins.
- **Non-semantic decorative elements.** Dividers, card borders, background
  fills used purely for visual grouping do not carry meaning and are not
  subject to these minimums (they are subject only to the spirit of WCAG 1.4.1
  color-not-alone for adjacent informative elements).
- **Third-party embeds.** Map tiles (maplibre-gl) and external widget surfaces
  are outside our token system. They are excluded from this floor.
- **Print or high-contrast mode.** We do not currently design or test for
  Windows High Contrast or forced-colors media queries. These are future
  backlog items, not covered by this doctrine.

---

## Settled Direction (owner-confirmed)

These were open questions in the first draft. The owner has answered.

1. **Audience.** The dashboard serves the owner today, with possible
   extension to close family members later. There is no team, no
   permission tier. **Both** calm-morning monitoring and incident
   investigation are valid use cases. Calm-morning is more frequent;
   incident is higher-stakes. The design must hold both, never
   sacrificing the second to optimize the first.
2. **Chronicles is the reference implementation.** Every page should
   eventually deliver Chronicles-grade feature richness: a real
   primary visualization, scrubber/control affordances where time
   applies, secondary aggregations, drill-down drawers. The "table
   of rows" archetype is acceptable as a transitional state, not as
   the destination. New work on existing pages should aim toward
   Chronicles, not regress further away from it.
3. **Owner sovereignty gets its own surface.** A new top-level
   **System** page will collect the plumbing-visibility facts:
   instance version, uptime, database size and growth, backup
   recency, "your data has only ever been seen by these endpoints,"
   per-butler last-touch timestamps, etc. Sovereignty becomes a
   page, not a sprinkle.
4. **Operator and resident modes are different projections.**
   Workspace-grade record pages may carry high tab counts when the
   operator surface needs them. Butler detail preserves the ten
   spec-mandated base tabs in operator mode: Overview, Sessions,
   Config, Skills, Schedules, Trigger, MCP, State, CRM, and Memory.
   It also preserves non-spec operator tabs that already carry
   capability, including Models.
   Resident mode may be the default narrow view and may use the
   Dispatch vocabulary, but it is a filtered projection of the
   operator surface, not a replacement for it. Deep links and
   conditional tabs must preserve access to the fuller operator
   surface. This is the settled Gate B B2 rule from `bu-41p8z`.
5. **Hero metric: butler sessions.** The single number that tells
   the owner whether their system is doing its job today is
   **sessions**: how many times butlers spun up to act on the
   owner's behalf. Cost, health, and pending approvals stay on the
   home page as supporting context, but session count is the one
   that gets visual primacy.

## Voice and Copy

> Status: **settled** (bu-scahb.7).

The dashboard is an operator tool. Its copy must be legible,
direct, and unadorned. The rules below govern every string rendered
in JSX (descriptions, labels, empty states, toasts, error messages)
and every prose sentence written in doctrine documents.

### Register

Technical, terse, slightly formal, owner-direct. The owner is a
sysadmin who already knows the domain. Do not explain things they
already know. Do not soften things that are just facts.

| What you want to say | Write this |
|---|---|
| The butler has not synced recently | "Last sync: 3 hours ago." |
| Nothing to show yet | "No sessions today." |
| A dangerous operation | "This will delete the connector and all its history." |

### Tense, person, and address

These rules sharpen the register above. They are part of the settled
voice doctrine, integrated from the editorial archetype work.

- **Past tense for events, present tense for state.** No future tense
  in interface copy: "is" or "did," not "will be" or "is going to."
  The dashboard reports what happened and what is true now; it does
  not promise.
- **No first person.** "I," "we," "us," "our" do not appear in
  rendered copy. The system is a third party. Write "Authentication
  failed," not "I could not authenticate."
- **Avoid "your" when "the" works.** "The calendar is paused," not
  "Your calendar is paused." The owner already knows whose dashboard
  this is. "Your" stays only when contrast matters ("Your timezone
  is Asia/Singapore. The butler runs in UTC.").
- **No hedging adverbs.** Strike: currently, presently, just, simply,
  basically, actually, essentially. Write "Loaded 14 sessions," not
  "Currently showing 14 sessions."
- **No celebration.** No checkmarks, no green-check moments, no
  "Nice work," no "All set." Quiet success is the success state.
  When everything is fine the page says it once and stops.
- **No filler.** "Welcome back" is filler. "Today, in numbers" is a
  fact. Delete the filler, keep the fact.
- **Numbers are exact.** "2 things need you," not "a few things." But
  do not state precision the data does not have: "2.0" when the
  source is integer is wrong, and so is "approximately 2."

### Capitalization

Sentence case for everything except proper nouns and product names.

- Page titles: sentence case. "Knowledge graph", not "Knowledge Graph".
- Section headings: sentence case. "Token leaks", not "Token Leaks".
- Button labels: sentence case. "Sync now", not "Sync Now".
- Proper nouns and product names are always capitalized: Claude,
  Telegram, PostgreSQL, Tailwind.

### Buttons

Active verbs. No marketing language. No punctuation.

| Bad | Good |
|---|---|
| "Force Patrol Now" | "Run patrol" |
| "Enable Smart Sync!" | "Enable sync" |
| "Request New Curriculum" | "Request curriculum" |
| "View All Notifications" | "View all" |
| "Load More Data" | "Load more" |

Destructive buttons are plain: "Delete", not "Delete forever" or
"Remove permanently". The `variant="destructive"` signals danger;
the copy does not need to amplify it.

### Empty states

State the fact, then offer the next action. Avoid prose sentences
that describe what the user could do if they were not there.

**Page-level empty states** use `{Noun} + verb phrase` as the title,
one short sentence of context if needed, and a single action button.

| Bad | Good |
|---|---|
| "No butlers found. Butlers are long-running agents that act on your behalf. Add one to get started!" | Title: "No butlers active." Action: "Open setup guide" |
| "Patrol cycles will dispatch investigations when novel issues are detected." | "No active investigations." |
| `"Browse the knowledge graph — people, organizations, places, and more."` | "Knowledge graph is empty." |

**Inline empty states inside a Voice surface** (the briefing column,
the attention list when there is nothing to attend to, the Next list
when nothing is upcoming) use a single serif italic sentence in muted
color, no period of explanation, no action button. Example:
*"Nothing waiting."* The Voice surface is the place the system
literally speaks; one quiet line is the entire response.

Empty states do not get exclamation marks. They do not use em-dashes.
They do not editorialize.

### Errors

Passive voice for system-side failures. Never blame the user.
Describe what failed, not who failed.

| Bad | Good |
|---|---|
| "You provided an invalid token." | "Authentication failed. Check the token in Settings." |
| "Your request failed." | "Failed to load sessions." |
| `"This butler isn't authenticated — please re-authenticate."` | "{Butler} is not authenticated. Re-authenticate in Settings." |

Error copy ends with a period. If there is an action to take, offer
it as a button or a link, not inline instructions.

### Bans

The following are banned in all dashboard copy and doctrine prose:

1. **Em-dashes (`—`).** Use a comma, colon, or parentheses instead.
   See non-negotiable rule 6.
2. **Exclamation marks.** The owner is not excited by dashboard
   notifications. If something is urgent, the visual treatment
   (destructive color, alert badge) carries that weight.
3. **Emoji** (unless the owner explicitly requests one in a
   specific context). Emoji in UI copy reads as consumer-product
   informality. This is an operator tool.
4. **"Please".** Do not apologize for the system's behavior. State
   what happened and what to do.
5. **Ellipsis as decoration.** Loading states may use "Loading..."
   as a terminal state indicator, but ellipsis is not a substitute
   for a complete sentence.

### Before/after examples from the codebase

The following examples are drawn from `frontend/src/pages/` as of
the bu-scahb.7 audit. They are documentation artifacts only. The
actual code is migrated downstream (bu-scahb.5).

**Example 1: Page description with em-dash (IngestionPage.tsx:50)**

Before:
```
Unified ingestion control surface — source visibility, routing policy, and historical replay.
```

After:
```
Unified ingestion control: source visibility, routing policy, and historical replay.
```

**Example 2: CardDescription with em-dash (QaInvestigationDetailPage.tsx:233)**

Before:
```
Butler sessions whose failures produced this fingerprint — open one to see the original traceback in context.
```

After:
```
Butler sessions whose failures produced this fingerprint. Open one to see the original traceback in context.
```

**Example 3: Page description with em-dash (EntitiesPage.tsx:659)**

Before:
```
Browse the knowledge graph — people, organizations, places, and more.
```

After:
```
Browse the knowledge graph: people, organizations, places, and more.
```

**Example 4: Page description with em-dash (EducationPage.tsx:79)**

Before:
```
Adaptive learning dashboard — track mastery, review schedules, and curriculum progress.
```

After:
```
Adaptive learning dashboard. Track mastery, review schedules, and curriculum progress.
```

**Example 5: Inline string with em-dash as separator (EntityDetailPage.tsx:916)**

Before:
```tsx
<span className="text-muted-foreground"> — {roleText}</span>
```

After:
```tsx
<span className="text-muted-foreground"> ({roleText})</span>
```

**Example 6: Interpolated string with em-dash (SettingsPage.tsx:745)**

Before:
```tsx
? ` — ${problematic[0].health_detail}`
```

After:
```tsx
? `: ${problematic[0].health_detail}`
```

**Example 7: Title attribute with em-dash (CalendarWorkspacePage.tsx:1774)**

Before:
```tsx
title={`${formatEntryWindow(entry)} — ${entry.title}`}
```

After:
```tsx
title={`${formatEntryWindow(entry)}: ${entry.title}`}
```

**Example 8: Button label using title case and excessive words**

Before: `"Force Patrol Now"`

After: `"Run patrol"`

**Example 9: Button label with marketing capitalization**

Before: `"Request New Curriculum"`

After: `"Request curriculum"`

**Example 10: Empty-state description as prose sentence**

Before: `"Patrol cycles will dispatch investigations when novel issues are detected."`

After (EmptyState title): `"No active investigations."`

---

## Type system

> Status: **settled** (resolves the typographic-scale debate from
> Candidate Doctrine and the de-facto observation that there is "no
> type scale documented").

The dashboard adopts a three-family type system. The split is
meaningful: sans is the system speaking in data, serif is the system
speaking in sentences, mono is the system speaking in numerals. A page
may use one, two, or all three families; never invent a fourth.

**Family identity.** Inter Tight is the sans family. Inter *Tight* is
the deliberate pick over plain Inter; the compressed metrics carry the
operator-tool register. Source Serif 4 is the Voice family, used where
the system literally speaks in sentences (briefings, empty-state lines,
process glosses). JetBrains Mono is the numerals family: timestamps,
IDs, deltas, KPI mega-numbers, eyebrows, code, file paths. Generic
stacks (Inter, Roboto, Arial, Helvetica, system-ui as a primary face)
are not in the language.

**Tabular numerals are non-negotiable.** Every numeric value the
dashboard renders uses tabular-nums: costs, counts, deltas, KPI
mega-numbers, mono timestamps, badge digits. Lists of numbers must
align without alignment hacks. Scannability of an operator tool is
defeated when digits jitter as they update.

**Display weight is 500, not 700.** Tight tracking does the work that
weight would do; bold display reads as loud, which violates the calm
contract. This governs the **Display/Editorial tier** (the 44px headline);
it does not override the standard `<Page>` H1, which stays `text-3xl
font-bold` (700). See the `Page` primitive section (decision bu-23bgb).

**Eyebrows title sections in lieu of a heading.** They establish
rhythm without adding shouting weight. They are not subtitles, they
are the section's name. An eyebrow above a list is the list's name; a
heading above the same list would be louder than the list it
introduces.

Token names, the type scale, and the font load path are owned by
[`about/lay-and-land/frontend.md`](../lay-and-land/frontend.md).

---

## Editorial archetype

> Status: **settled** (governs the Overview surface and any future
> page that opens with a system-spoken briefing). Companion to the
> Type system above and to the Voice and Copy section.

The dashboard supports a small set of page archetypes (Non-negotiable
rule 3). The **editorial archetype** is a two-column page whose left
column is the system speaking in sentences and whose right column is
a quiet index of facts. The two columns read as separate documents
that share a page.

### The Voice surface

The Overview headline plus its serif elaboration is a distinct surface
type called the **Voice**. Reserve it for places the system literally
speaks in sentences: the Overview briefing, empty states ("Nothing
waiting."), process glosses where the system explains its own shape.
Voice is serif italic for empty states, serif roman for briefings. It
is never decorative. If a serif paragraph feels like it would "fill"
a quiet page, the page is not actually quiet enough; the serif
paragraph is wrong.

### The status pill

Anywhere the system reports on its own process (cache age, last sync,
model version, briefing source), use a status pill. The pill is
always honest about what is rendering. The three states for the
briefing pill are `composing…`, `llm · cached 5m`, and `templated`;
the pill names what it is showing rather than pretending the source
is invisible.

### Attention list

The attention list is the dashboard's register-aware list primitive:
rule-separated rows, no card chrome. The mark column carries severity
for read-rows and status for scan-rows; the meta column carries
action for read-rows and metric for scan-rows. The list reaches
Bloomberg-grade density at a fraction of Bloomberg's noise. Empty
state for the attention list is the Voice register doing its job:
`Nothing waiting.` in serif italic, no celebration, no illustration.

#### Attention-tint exception

> Status: **single permitted exception** to the state-color-on-background
> prohibition, approved by openspec/changes/redesign-settings-dispatch-console/.

Rows or panels that *demand human attention right now* may carry a
**4–7% alpha background tint** in the state color, paired with a **2px left
rail** in the same color. This is one affordance, not two: the tint and
the rail travel together as a single signal unit.

Permitted use cases:
- Open approval awaiting human decision
- Auth-renewal required for a connected provider
- Model in error or rate-limited state
- Spend within 10% of monthly ceiling
- Webhook failure in the last 24 hours

Constraints:
- The pattern applies **only** to "demands attention now" states. Routine
  status (healthy, idle, neutral) receives no tint, no rail.
- A row already carrying a `Sev` glyph or any other affordance does
  **not** also receive the tint: *one affordance per signal* still applies.
- Two tones only: `red` (4–7% alpha) for critical states, `amber` (4–6%
  alpha) for warning states. No other hues enter the background.

The canonical CSS lives in `frontend/src/index.css` under the OKLCH
palette section. The pattern is implemented by the `.attention-row`
class with `data-tone="red"` or `data-tone="amber"` attributes.

### KPI strip

The KPI strip replaces card chrome with tabular-nums plus hairline
dividers. Numbers align across columns because every numeric cell is
tabular. There are no background fills, no per-cell cards, and no
mega-number that screams; the alignment is the design.

### Butler hue scope

> Status: non-negotiable (with one documented exception, see
> [Attention-tint exception](#attention-tint-exception) above).

Each butler has one hue from the categorical palette. The hue appears
**only on the butler letter-mark** (the colored squircle with the
initial). It does not appear on backgrounds, borders, buttons,
headers, or any other chrome. This rule is what keeps the dashboard
from reading like a SaaS product. It augments the existing token
rule (Non-negotiable 1): named hues only resolve onto the letter-mark.

The sole exception is the attention-tint pattern described above, which
permits a 4–7% alpha state-color tint on rows or panels requiring
immediate human action. That exception is scoped, bounded, and single-
purpose; it does not weaken the general prohibition.

### Motion budget

The editorial archetype obeys the existing motion contract (see
[Motion](#motion)). The briefing introduces two motion events: a
paragraph cross-fade on refresh and a status-pill icon rotation
while loading. No staggered entries, no count-up animations, no
scale-in. Calm is the feature.

Layout values, row anatomies, motion durations, and the source files
that embody these patterns are owned by
[`about/lay-and-land/frontend.md`](../lay-and-land/frontend.md).

---

## Motion

> Status: **settled** (owner-confirmed contract for all interactive components).

### The Contract

Every interactive state transition in the dashboard must honor these rules:

**Duration tiers** (declared in `frontend/src/index.css` as `--duration-fast/base/slow`):

| Tier | Token | Value | When to use |
|------|-------|-------|-------------|
| fast | `duration-fast` | 150 ms | Micro-interactions: hover, focus rings, button press |
| base | `duration-base` | 200 ms | Layout-affecting: sidebar collapse, drawer open |
| slow | `duration-slow` | 250 ms | Transitions spanning > 200 px of travel |

**Easing** (`--ease-out-quart`, `cubic-bezier(0.22, 1, 0.36, 1)`):
- Ease-out exponential family only. Applied via the `ease-out-quart` Tailwind utility.
- No bounce, no elastic, no ease-in-out for state changes.

**Animated properties** (only these are permitted):
- `transform` (translate, scale, rotate)
- `opacity`
- `color`, `background-color`, `border-color`, `box-shadow` (paint-only)

**Banned animated properties** (these cause layout reflow and are forbidden):
- `width`, `height`, `max-height` (use transform or opacity instead)
- `top`, `left`, `right`, `bottom` (use `translate` instead)
- `margin`, `padding`

**Page-load orchestration is banned.** No staggered entry sequences, no cascading
fade-ins on mount. Motion conveys state change, not personality.

**Decorative motion is banned.** If a transition does not communicate a state change
to the user, it does not belong.

### Tailwind Utilities

The `@utility` blocks in `index.css` define compound transition shortcuts with
duration and easing baked in. The `@theme inline` block separately exposes
`ease-out-quart` as a Tailwind timing-function utility:

```
transition-fast  →  all 150ms cubic-bezier(0.22, 1, 0.36, 1)
transition-base  →  all 200ms cubic-bezier(0.22, 1, 0.36, 1)
transition-slow  →  all 250ms cubic-bezier(0.22, 1, 0.36, 1)
```

To limit the animated property, combine with a `transition-[property]` utility
before the duration:

```jsx
// Chevron rotation — transform only, fast tier
<svg className="transition-transform duration-fast ease-out-quart ...">

// Brand fade on sidebar collapse — opacity only, base tier
<span className="transition-opacity duration-base ease-out-quart ...">

// Hover color change — paint-only, no duration needed (browser default is fine)
<button className="transition-colors ...">
```

### Existing Violations (discovered at contract introduction)

The following components animate layout properties and are known violations.
They are tracked for migration but were not fixed in-place to avoid scope creep.

| File | Violation | Recommended migration |
|------|-----------|----------------------|
| `frontend/src/components/layout/Shell.tsx:30` | `transition-[width]` on sidebar `<aside>` | Use `transform: translateX` or accept as a shell-layout exception |
| `frontend/src/components/chronicles/FloatingMapMinimap.tsx:65` | `transition-[width,height]` | Accept as map-widget exception or use `transform: scale` |
| `frontend/src/components/chat/ConversationList.tsx:144` | `transition-all` with width toggle | Use `transform: translateX` or clip with overflow |
| `frontend/src/components/layout/Sidebar.tsx:191,288` | `transition-all` with `max-height` toggle | Use grid row / opacity or accept as a minor exception |

The progress bars in `MedicationTracker.tsx` and `ModelCatalogCard.tsx` use
`transition-all` with `style={{ width: ... }}`. These are covered by the typed-primitives
exemption in the Non-negotiable rules above ("A `<Progress>` whose internal
`style={{ width: '42%' }}` is unavoidable does not violate the rule").

---

## Detail-page canonicalization

The detail-page archetype today has seven divergent
implementations (`ButlerDetailPage`, `ContactDetailPage`,
`EntityDetailPage`, `EpisodeDetailPage`, `FactDetailPage`,
`RuleDetailPage`, `ConnectorDetailPage`). They each invented their
own header, metadata strip, body composition, and action placement.
Their content is legitimately different; their bones should not be.

A `craft-and-care` audit is selecting the cleanest existing
implementation as the canonical template. See
[`about/lay-and-land/detail-page-audit.md`](../lay-and-land/detail-page-audit.md)
for the analysis, the chosen winner, and the migration order. Once
elected, the winner becomes the body of a shared `<DetailPage>`
shell and the other six pages migrate onto it.

---

## How To Use This Document

- **Adding a page or component?** Read this first. If your work
  contradicts a non-negotiable rule, either fix the work or argue
  here for the rule to change. Both are legitimate moves, but
  don't do neither.
- **Doing an `/impeccable` redesign?** Treat this as the
  pre-existing constraint set. Open with the open questions, not
  with mockups.
- **Reviewing a PR?** "Does this drift the design language?" is a
  fair review comment, and this doc is what you point to.

The companion topology document [`frontend.md`](../lay-and-land/frontend.md)
inventories *where* the language is currently embodied so the redesign
knows what it is replacing.
