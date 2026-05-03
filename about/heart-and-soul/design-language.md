# Dashboard Design Language

> Status: **observational draft** — written by a design consultant on first
> contact with the codebase. This document captures what the dashboard's
> design language *appears to be today* (de facto), names what is working,
> names what is drifting, and proposes the doctrine that a future
> `/impeccable` redesign should defend or replace.

The Butlers dashboard is the human window into a system that mostly runs
without humans watching. It is read first, controlled second. The design
language must respect that.

This document does **not** specify components, tokens, or pages. Those belong
in [`about/lay-and-land/frontend.md`](../lay-and-land/frontend.md) (where
things are) and in capability specs (what they do). This document is
**WHY** — the principles a Butlers dashboard must satisfy regardless of
which framework or visual style we land on.

---

## What the Dashboard Is

The dashboard is a **read-mostly observability surface for a personal
multi-agent system**. It exists so the owner can:

1. Trust that butlers are alive and behaving.
2. Investigate when one of them isn't.
3. Override or correct behavior when needed.
4. Understand what the system *did with their data* — episodes, contacts,
   memory facts, notifications, costs.

Every screen is in service of one of those four jobs. If a screen does not
serve them, it does not belong.

The dashboard is **single-tenant**. There is exactly one user. There is no
"team," no "permissions tier," no "workspace switcher." Every design pattern
that assumes multi-user SaaS conventions (avatars in nav, role badges,
"invite teammates" CTAs) is a category error here.

## What the Dashboard Is Not

- **Not a control plane for end users.** The end user of a butler is the
  owner, but their channel is messaging, not the dashboard. The dashboard
  is the *operator's* surface — the same person, but in a different mode.
- **Not a chat app.** Conversational flows belong in the messaging layer.
  The dashboard's job is to *show what happened*, not to *be the place
  things happen*.
- **Not a generic admin template.** The dashboard's structure should be
  legible to its single user, not to an imagined enterprise admin
  persona. Patterns like "user management → roles → permissions" do not
  apply.
- **Not a marketing surface.** Hero illustrations, gradient banners,
  "delight" microinteractions for their own sake — none of these earn
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
- Custom `useDarkMode` hook (not `next-themes`, despite it being a
  dependency) controlling a `.dark` class on `<html>`

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
  three-zone — left rail (sidebar), top bar (PageHeader, 56px), main
  scroll region (24px padding). Mobile collapses the rail into a Sheet.
- **PageHeader** (`components/layout/PageHeader.tsx`): auto-generated
  breadcrumbs from URL segments, optional injected title, command-palette
  and dark-mode toggles on the right. Each segment is title-cased
  naively (`/qa/investigations` → `Qa / Investigations`).
- **Sidebar** (`components/layout/Sidebar.tsx`): three sections
  (`Main`, `Dedicated Butlers`, `Telemetry`) configured declaratively in
  `nav-config.ts`, with butler-presence filtering and live badge counts
  hooked up via `useBadgeCounts`. Today's spend lives in the footer.
- **Pages**: each route is a flat component under `src/pages/`. There is
  **no shared `<Page>` wrapper**. The page-title pattern (h1 +
  description + optional action row) is hand-rolled on every page.
- **Cards** are the dominant container; the shadcn `Card` family
  (`Card / CardHeader / CardTitle / CardDescription / CardAction /
  CardContent / CardFooter`) is used everywhere.

### Voice
The voice is **technical, terse, slightly formal, occasionally playful.**
It treats the user as a sysadmin who already understands the domain.
Examples: "Retrospective view of lived past time reconstructed from butler
evidence." "All systems healthy." "QA Staffer not active."

This is the right register for the audience. The drift is mechanical —
sentence-case vs Title Case, "Loading…" vs "Loading butlers…" — not
tonal.

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
   the same primitive — the divergence is at the page level, not the
   component level.
4. **Navigation is configuration-driven.** `nav-config.ts` is a single
   declarative source of truth, and butler-presence filtering means the
   sidebar reflects the actual instance (a Health butler that isn't
   installed simply doesn't appear). This is a design language
   superpower.
5. **The dark mode is real.** Both themes are designed, not retrofitted.
   The chart palette flips meaningfully (warm in light, cool in dark).

## What Is Drifting

1. **Page chrome is reinvented per page.** Heading sizes vary
   (`text-2xl` on Dashboard and Costs; `text-3xl` on Butlers and
   Chronicles), description placement varies, action-button rows have
   no canonical position. There is no `<Page>` / `<PageHeader>`
   container component to hold the shape.
2. **The token system leaks.** Several pages reach for raw hex when
   they need a non-semantic color — `EntitiesPage.tsx` lines 102–113 hard-code
   six tier colors; `EntityDetailPage.tsx` lines 313/316 inline
   `#7c3aed` and `#f59e0b`; `SymptomsPage.tsx` does the same for
   severity; `GroupsPage.tsx` line 121 keeps a hex palette array. The
   project ran out of semantic tokens at "destructive" and stopped.
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
5. **Visualization is unevenly committed.** `recharts` for bars/pies,
   `@xyflow/react` for topology, `maplibre-gl` for the chronicles map,
   and ad-hoc inline SVG / styled `<div>` widths for everything else
   (progress bars, calendar grid heights). Nothing wrong with three
   libraries — but the seams are visible.
6. **Tone is mostly consistent but capitalization is not.** "Force
   Patrol Now" sits next to "Sync now" sits next to "View all
   notifications." Empty-state copy ranges from terse ("No butlers
   found") to quasi-prose ("Patrol cycles will dispatch investigations
   when novel issues are detected"). Each is fine in isolation; together
   they read like four people writing.
7. **`PageHeader` does too little to claim its name.** It generates
   breadcrumbs from the URL and renders an optional title — but it is
   *invoked once* in `RootLayout` with no title, so every page
   re-implements its own H1. The component name suggests a contract it
   does not deliver.
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
     is unavoidable does not violate the rule — the ban targets ad-hoc
     inline styles, not encapsulated dynamic values inside a typed
     primitive.
2. **The `Page` is a primitive.** Every route renders inside a
   `<Page>` shell that owns title, description, breadcrumbs, action
   bar, loading state, error state, and empty state. Pages compose
   sections inside it; they do not reinvent the chrome.
   - **H1 size is `text-2xl font-bold tracking-tight`.** Settled per
     the detail-page audit and adopted by `<Page>`. Pages currently
     using `text-3xl` get demoted during their migration.
   - **Type ratio is 1.2** (product-register override of impeccable's
     shared `≥1.25` floor). Per `impeccable/reference/product.md`:
     "tighter scale ratio. 1.125–1.2 between steps is typical for
     product UI." The dashboard is product, not brand.
3. **Information density is a deliberate dial, not an accident.**
   Each page declares its archetype (overview, drilldown, workspace,
   feed, log, graph). The archetype determines layout, not the
   author.
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

### Worth debating

- Whether to keep a single `Card` as the dominant container or to
  introduce a flatter "section" pattern for high-density screens like
  QA and Chronicles.
- Whether the sidebar should keep its first-letter-as-icon glyphs or
  commit to real icons (the current approach is honest but cheap).
- Whether the breadcrumb autobuilder is worth keeping (it produces
  awkward output: "Qa / Investigations") or whether each page should
  own its breadcrumbs explicitly.
- Whether dark mode is the *primary* design target rather than light.
  Operators tend to leave dashboards open at night.
- Whether a typographic scale (one or two custom families, defined
  sizes) is worth introducing, or whether the current system stack
  is part of the dashboard's "operator-tool" charm.

---

## Settled Direction (owner-confirmed)

These were open questions in the first draft. The owner has answered.

1. **Audience.** The dashboard serves the owner today, with possible
   extension to close family members later. There is no team, no
   permission tier. **Both** calm-morning monitoring and incident
   investigation are valid use cases. Calm-morning is more frequent;
   incident is higher-stakes. The design must hold both — never
   sacrificing the second to optimize the first.
2. **Chronicles is the reference implementation.** Every page should
   eventually deliver Chronicles-grade feature richness — a real
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
4. **Hero metric: butler sessions.** The single number that tells
   the owner whether their system is doing its job today is
   **sessions** — how many times butlers spun up to act on the
   owner's behalf. Cost, health, and pending approvals stay on the
   home page as supporting context, but session count is the one
   that gets visual primacy.

## Motion

> Status: **settled** — owner-confirmed contract for all interactive components.

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

**Animated properties** — only these are permitted:
- `transform` (translate, scale, rotate)
- `opacity`
- `color`, `background-color`, `border-color`, `box-shadow` (paint-only)

**Banned animated properties** — these cause layout reflow and are forbidden:
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
implementation as the canonical template — see
[`about/lay-and-land/detail-page-audit.md`](../lay-and-land/detail-page-audit.md)
for the analysis, the chosen winner, and the migration order. Once
elected, the winner becomes the body of a shared `<DetailPage>`
shell and the other six pages migrate onto it.

---

## How To Use This Document

- **Adding a page or component?** Read this first. If your work
  contradicts a non-negotiable rule, either fix the work or argue
  here for the rule to change — both are legitimate moves, but
  don't do neither.
- **Doing an `/impeccable` redesign?** Treat this as the
  pre-existing constraint set. Open with the open questions, not
  with mockups.
- **Reviewing a PR?** "Does this drift the design language?" is a
  fair review comment, and this doc is what you point to.

The companion topology document — [`frontend.md`](../lay-and-land/frontend.md)
— inventories *where* the language is currently embodied so the redesign
knows what it is replacing.
