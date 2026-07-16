# Frontend Information Architecture

> **Purpose:** Define the global navigation structure, route map, tab structures, and URL semantics for the dashboard.
> **Audience:** Frontend developers and designers working on dashboard navigation and routing.
> **Prerequisites:** [Purpose and Single-Pane Role](purpose-and-single-pane.md).
>
> **Source of truth:** the sidebar (`src/components/layout/nav-config.ts`), the router
> (`src/router-config.tsx`), and the single command/route registry
> (`src/lib/route-registry.ts`) that reconciles the two and feeds the command
> palette, `g`-chords, and the `?` help sheet. This document is a regenerated
> snapshot of those three files (bu-86c4c.19, JARVIS audit move 14) — when in
> doubt, read the source, not this page.

## Global Shell

All routes render inside a common shell (`RootLayout`) with:

- Responsive sidebar navigation (desktop collapsible, mobile drawer), driven by `nav-config.ts`.
- Header with breadcrumb trail (auto-built from the path) and the one theme toggle.
- Global entity/page finder (`EntityFinder`, opened via `Cmd/Ctrl+K` or `/`) — entities,
  pages (sourced from `route-registry.ts`'s `ALL_ROUTES`, so every route is findable even
  when it isn't in the sidebar), and `g`-chords all live here. There is no separate
  "command palette" component; `EntityFinder` is the one command surface.
- Keyboard shortcut help sheet (`?`).
- Error boundary around route content.
- Toast notifications for mutation feedback.

## Primary Navigation (Sidebar)

Sidebar sections and entries (`navSections` in `nav-config.ts`):

**Main**
- Overview (`/`)
- Butlers (`/butlers`)
- QA (`/qa`, only when the `qa` butler is present)
- Ingestion (`/ingestion`)
- Approvals (`/approvals`)
- Memory (`/memory`)
- Entities (`/entities`)
- Secrets (`/secrets`)
- Settings (`/settings`)

**Dedicated Butlers**
- Education (`/education`, only when the `education` butler is present)
- Health (`/health`, only when the `health` butler is present), with Overview plus child links
  for Measurements, Medications, Conditions, Symptoms, Meals, and Research
- Calendar (`/calendar`)
- Chronicles (`/chronicles`, only when the `chronicler` butler is present)

**Telemetry** (collapsed by default)
- Timeline (`/timeline`)
- Notifications (`/notifications`)
- Issues (`/issues`)
- Sessions (`/sessions`)
- Audit Log (`/audit-log`)
- System (`/system`)

### Routes that exist but are intentionally not in the sidebar

These are reached via the entity finder, `g`-chords, deep links from a parent page, or
direct URL — never orphaned, just not promoted to the rail (`EXTRA_ROUTES` in
`route-registry.ts`):

- Costs (`/costs`)
- Settings sub-pages: Spend Settings (`/settings/spend`), Permissions (`/settings/permissions`),
  Models (`/settings/models`)
- Entities Index (`/entities/index`), Concentration (`/entities/concentration`),
  Circles (`/entities/circles`)
- Contacts (`/entities/index?has=contact`) — indexed directly rather than through the
  `/contacts` redirect so the `c` chord and finder don't bounce through it

## Route Map

| Route | Surface | Notes |
| --- | --- | --- |
| `/` | Overview dashboard | Topology + aggregate health + attention list |
| `/butlers` | Butler roster | Status board for all registered butlers |
| `/butlers/:name` | Butler detail | Multi-tab control and observability surface (see Tab Structures) |
| `/sessions` | Session list | Cross-butler sessions with filters + drawer detail |
| `/sessions/:id` | Session detail | Full metadata/prompt/result/error view |
| `/timeline` | Unified timeline | Cross-butler event stream with filters |
| `/notifications` | Notifications center | Delivery stats + filtered feed |
| `/issues` | Issues center | Active alerts and operator-dismissable issue list |
| `/audit-log` | Audit log | Filterable operation history |
| `/approvals`, `/approvals/:id` | Approvals + Autonomy | Pending queue, decision workflows, and the always-visible Autonomy panel (per-butler × tool trust rules — absorbed the orphaned `/approvals/rules` page, bu-86c4c.12) |
| `/calendar` | Calendar workspace | Dual-view shell with user/butler toggle and range controls |
| `/contacts`, `/contacts/:contactId` | *(compat redirect)* | Forwards to `/entities/index?has=contact` — `public.contacts` was dropped (core_134) |
| `/health` | Health overview | Voice briefing + vitals KPI strip, plus a right-column ledger index and attention list |
| `/health/measurements` \| `/medications` \| `/conditions` \| `/symptoms` \| `/meals` \| `/research` | Health sub-pages | Six Dispatch-language CRUD surfaces over the fact store; reachable from the Health ledger index and sidebar children |
| `/costs` | Costs and usage | Summary stats + chart + butler breakdown |
| `/memory`, `/memory/facts/:factId`, `/memory/rules/:ruleId`, `/memory/episodes/:episodeId` | Memory system | Register pills (Facts/Rules/Episodes) + detail deep links |
| `/entities` | Entities Plex | Force-graph relationship map |
| `/entities/index` | Entities Index | Tabular entity list with filter chips + curation queue rail |
| `/entities/concentration` | Concentration | Relationship-weight balance sheet by predicate |
| `/entities/circles` | Circles | Contact-group lens (retired the standalone `/groups` page, bu-86c4c.19) |
| `/entities/:entityId` | Entity detail | Single activity feed with filter pills (replaced the old Notes/Interactions/Gifts/Loans/Activity tab strip) |
| `/entities/hop`, `/entities/columns`, `/entities/social-map` | *(compat redirects)* | Absorbed into the Plex; forward to `/entities` |
| `/groups` | *(compat redirect)* | Forwards to `/entities/circles` (bu-86c4c.19) |
| `/settings`, `/settings/spend`, `/settings/permissions`, `/settings/models` | Settings console | Local UI preferences, spend posture, permission grants, model routing |
| `/secrets` | Secrets passport | Severity-sorted spine + per-credential evidence pages (System/User/CLI families) |
| `/education` | Education | Butler-specific dashboard (only when the `education` butler is present) |
| `/chronicles` | Chronicles | Retrospective lived-time reconstruction (only when `chronicler` is present) |
| `/qa` | QA overview | Dossier shell: severity/since/state/butler filters (all URL-persisted), KPI strip, patrol pulse strip, case rail + dossier. Folded the standalone `/qa/investigations` flat index in here so there is one canonical case index (bu-86c4c.19) |
| `/qa/patrols/:patrolId` | Patrol detail | One patrol's findings and dispatched investigations |
| `/qa/investigations` | *(compat redirect)* | Forwards to `/qa` (bu-86c4c.19) |
| `/qa/investigations/:attemptId` | Case detail (deep link) | Mounts the same `CaseDossier` as `/qa?case=<id>`, with breadcrumb chrome — kept as a stable per-case URL |
| `/ingestion` | Ingestion timeline | Dispatch ledger (default sub-route); redirects legacy `?tab=` URLs |
| `/ingestion/connectors`, `/ingestion/connectors/:connectorType/:endpointIdentity` | Connectors roster + detail | |
| `/ingestion/filters` | Filters pipeline | |
| `/ingestion/history` | *(compat redirect)* | Forwards to `/ingestion` |
| `/connectors`, `/connectors/:connectorType/:endpointIdentity` | *(compat redirects)* | Forward to `/ingestion/connectors` equivalents |
| `/system` | System | Instance ownership and runtime facts |

## Tab Structures

### Butler Detail Tabs (`/butlers/:name`)

Always-rendered tab triggers: `Overview`, `Activity`, `Approvals`, `Spend`, `Memory`, `System`.

Conditionally rendered (per butler, gated by which modules/roster entry the butler has):

- `Collections`, `Entities` — `general` butler
- `Measurements` (health tab) — `health` butler
- `Routing Log`, `Registry` — `switchboard` butler
- `Reviews` — education-flavored butlers
- `Timelines` — chronicler-flavored butlers
- `Finances` — finance-flavored butlers
- `Devices` — home-flavored butlers
- `Taste` — lifestyle-flavored butlers
- `Conversations` — messenger-flavored butlers
- `Investigations` — QA staffer
- `Contacts` — relationship butler
- `Trips` — travel-flavored butlers

Tab URL semantics: active tab is controlled by `?tab=`; `overview` is the default and
removes the query param.

### Entities Subpage Tabs (`SubpageTabs`, the `/entities/*` family)

- `Plex` (`/entities`, end-matched so it doesn't stay active on sub-routes)
- `Index` (`/entities/index`)
- `Concentration` (`/entities/concentration`)
- `Circles` (`/entities/circles`)

### Memory Register Pills

On `/memory` and the Butler Detail `Memory` tab: `Facts`, `Rules`, `Episodes` register
pills (not a `<Tabs>` shell — a plain pill switcher). When opened inside Butler Detail,
queries are scope-filtered to that butler.

### Entity Detail (`/entities/:entityId`)

A single activity feed with filter pills — the old per-contact `Notes` / `Interactions`
/ `Gifts` / `Loans` / `Activity` tab strip was replaced when contacts were folded into
the entity graph.

### QA Suite (`/qa`)

Not a tab strip — a two-pane dossier: a case rail (filtered by the sticky top bar's
severity/since/state/butler controls, all URL-persisted) and a `CaseDossier` main
column selected via `?case=`. Patrol detail (`/qa/patrols/:patrolId`) and per-case deep
links (`/qa/investigations/:attemptId`) are separate routes linked in from here.

## Approvals + Autonomy Integration

The approvals module is integrated into the single-pane dashboard as one page:

- Sidebar entry: `Approvals` (`/approvals`)
- Route: `/approvals`, `/approvals/:id` — pending queue, filters, decision workflows,
  and the always-visible Autonomy panel (per butler × tool trust spectrum with live use
  counts and inline revoke)

The standalone `/approvals/rules` page (standing-rules CRUD) was merged into `/approvals`
as this Autonomy panel and its route deleted (bu-86c4c.12) — there is no separate rules
route anymore.

## Related Pages

- [Purpose and Single-Pane Role](purpose-and-single-pane.md) -- Why this architecture exists
- [Feature Inventory](feature-inventory.md) -- What is implemented per route
- [Data Access and Refresh](data-access-and-refresh.md) -- How routes fetch and refresh data
- [Backend API Contract](backend-api-contract.md) -- Required backend endpoints per route
