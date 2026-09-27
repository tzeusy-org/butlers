# Frontend Information Architecture

> **Purpose:** Explain how the dashboard's navigation is organized and why: shell, sidebar
> grouping, off-rail routes, tab and URL semantics.
> **Audience:** Frontend developers and designers working on dashboard navigation and routing.
> **Prerequisites:** [Purpose and Single-Pane Role](purpose-and-single-pane.md).
>
> **Source of truth:** the shell capability manifest (`frontend/src/lib/shell-capability.ts`
> `SHELL_CAPABILITIES`), from which the router (`frontend/src/router-config.tsx`), the rail
> (`frontend/src/components/layout/nav-config.ts`), the entity finder, `g`-chords, and the `?`
> help sheet (`frontend/src/lib/route-registry.ts`) are all derived. The route inventory and its
> topology live there and in [about/lay-and-land/frontend.md](../../about/lay-and-land/frontend.md)
> §Routing Surface; this page carries only the rationale.

## Global Shell

All routes render inside a common shell (`RootLayout`) with:

- Responsive sidebar navigation (desktop collapsible, mobile drawer), driven by `nav-config.ts`.
- Header with breadcrumb trail (auto-built from the path) and the one theme toggle.
- Global entity/page finder (`EntityFinder`, opened via `Cmd/Ctrl+K` or `/`) — entities,
  pages (sourced from `route-registry.ts`'s `ALL_ROUTES`, so every route is findable even
  when it isn't in the rail), and `g`-chords all live here. There is no separate
  "command palette" component; `EntityFinder` is the one command surface.
- Keyboard shortcut help sheet (`?`).
- Error boundary around route content.
- Toast notifications for mutation feedback.
- Shell scroll memory: PUSH navigation starts the persistent main surface at the
  top, while POP navigation restores the saved history-entry offset after the
  destination paints. Calendar and chat declare their inner scrollers as the
  scroll owner; a fresh reload starts at the top.

## Primary Navigation (Sidebar)

The rail (each capability's `placement.section`) has three groups, ordered by how often an operator
needs them:

- **Main** — the fleet-wide control surfaces an operator visits daily (overview, butlers,
  ingestion, approvals, memory, entities, secrets, settings).
- **Dedicated Butlers** — pages owned by one butler's domain (health, calendar, education,
  chronicles). An entry that declares `placement.butler` renders only when that butler is in
  the roster, so the rail never advertises a surface with no backing daemon.
- **Telemetry** (collapsed by default) — observability views (timeline, notifications, issues,
  sessions, spend, audit log, system). They are for diagnosis, not the daily loop, so they stay
  out of the way until expanded.

### Off-rail routes

Some routes are intentionally not promoted to the rail: settings sub-pages, the secondary
entity lenses, and deep-link detail pages. They are capabilities with no `placement`, so they are
never orphaned: the entity finder and `g`-chords still reach them. Index
a destination directly rather than through a compatibility redirect so the finder and chords
don't bounce through it.

### Compatibility redirects

When a surface is absorbed into another, its old path stays as a `<Navigate replace>` in
`router-config.tsx` so bookmarks and deep links keep working. A redirect is never a nav entry.

## Tab and URL Semantics

- **Butler detail** (`/butlers/:name`): a fixed set of always-rendered tabs plus tabs gated on
  the butler's modules or roster entry (`ButlerDetailPage.tsx`). The active tab is `?tab=`;
  `overview` is the default and removes the param.
- **Entities** (`/entities/*`): `SubpageTabs` switch between lenses (Plex, Index,
  Concentration, Circles) as real routes, so each lens is linkable. Entity detail is a single
  activity feed with filter pills rather than per-type tabs.
- **Memory**: `Facts` / `Rules` / `Episodes` register pills (a plain pill switcher, not a
  `<Tabs>` shell). Inside Butler Detail the same view is scope-filtered to that butler.
- **QA** (`/qa`): a two-pane dossier, not a tab strip — a case rail filtered by URL-persisted
  controls and a `CaseDossier` selected via `?case=`. Per-case and per-patrol deep links are
  separate routes so a case has one stable URL.
- **Approvals** (`/approvals`): one page holding the pending queue, decision workflow, and the
  always-visible Autonomy panel (per butler × tool trust rules). Standing rules are managed in
  that panel, not on a separate route.

Filter state that an operator would want to share or return to is URL-persisted.

## Related Pages

- [Purpose and Single-Pane Role](purpose-and-single-pane.md) -- Why this architecture exists
- [Data Access and Refresh](data-access-and-refresh.md) -- How routes fetch and refresh data
- `openspec/specs/dashboard-*` -- Required behavior per page
