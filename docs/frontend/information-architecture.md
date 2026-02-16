# Frontend Information Architecture

## Global Shell

All routes render inside a common shell with:

- Responsive sidebar navigation (desktop collapsible, mobile drawer).
- Header with breadcrumb trail and theme toggle.
- Global command palette (`/` or `Ctrl/Cmd+K`).
- Keyboard shortcut help dialog (`?` floating button).
- Error boundary around route content.
- Toast notifications for mutation feedback.

## Primary Navigation (Sidebar)

Sidebar entries:

- Overview (`/`)
- Butlers (`/butlers`)
- Sessions (`/sessions`)
- Traces (`/traces`)
- Timeline (`/timeline`)
- Notifications (`/notifications`)
- Issues (`/issues`)
- Audit Log (`/audit-log`)
- Approvals (`/approvals`)
- Contacts (`/contacts`)
- Groups (`/groups`)
- Health (`/health/measurements`)
- Collections (`/collections`)
- Connectors (`/connectors`)
- Memory (`/memory`)
- Entities (`/entities`)
- Settings (`/settings`)

Note: `Costs` exists as a route (`/costs`) but is not a sidebar item.

## Route Map

| Route | Surface | Notes |
| --- | --- | --- |
| `/` | Overview dashboard | Topology + aggregate health + failed notifications + active issues |
| `/butlers` | Butler list | Status cards for all registered butlers |
| `/butlers/:name` | Butler detail | Multi-tab control and observability surface |
| `/sessions` | Session list | Cross-butler sessions with filters + drawer detail |
| `/sessions/:id` | Session detail | Full metadata/prompt/result/error view |
| `/traces` | Trace list | Distributed trace index |
| `/traces/:traceId` | Trace detail | Metadata + span waterfall |
| `/timeline` | Unified timeline | Cross-butler event stream with filters |
| `/notifications` | Notifications center | Delivery stats + filtered feed |
| `/issues` | Issues center | Active alerts and operator-dismissable issue list |
| `/audit-log` | Audit log | Filterable operation history |
| `/approvals` | Approvals queue | Pending action queue + filters + decision workflows |
| `/approvals/rules` | Approval rules | Standing rules list/detail/revoke flows |
| `/contacts` | Contacts list | Search/filter contacts with pagination |
| `/contacts/:contactId` | Contact detail | Profile + notes/interactions/gifts/loans/activity tabs |
| `/groups` | Groups list | Relationship groups and membership metrics |
| `/health/measurements` | Health measurements | Measurement trend visualization + filters |
| `/health/medications` | Health medications | Medication cards + dose log/adherence |
| `/health/conditions` | Health conditions | Paginated condition status table |
| `/health/symptoms` | Health symptoms | Severity trend table with filters |
| `/health/meals` | Health meals | Grouped-by-day meals table |
| `/health/research` | Health research | Search/tag-filtered research notes |
| `/collections` | General collections | Collection cards and entity counts |
| `/entities` | General entities | Search/filter entity browser |
| `/entities/:entityId` | Entity detail | Metadata + full JSON payload |
| `/connectors` | Connectors overview | Connector cards + volume chart + fanout matrix + error log |
| `/connectors/:connectorType/:endpointIdentity` | Connector detail | Full stats + timeseries + health history |
| `/costs` | Costs and usage | Summary stats + chart + butler breakdown |
| `/memory` | Memory system | Tier cards + browser + activity timeline |
| `/settings` | Settings | Local UI preferences (theme, live-refresh defaults, search history controls) |

## Tab Structures

## Butler Detail Tabs (`/butlers/:name`)

Always rendered tab triggers:

- `Overview`
- `Sessions`
- `Config`
- `Skills`
- `Schedules`
- `Trigger`
- `State`
- `CRM`
- `Memory`

Conditionally rendered:

- `Health` (only when `name === "health"`)
- `Collections`, `Entities` (only when `name === "general"`)
- `Routing Log`, `Registry` (only when `name === "switchboard"`)

Tab URL semantics:

- Active tab is controlled by `?tab=` query param.
- `overview` is default and removes the query param.
- Accepted deep-link values are currently base tabs + health tabs + general tabs + switchboard tabs.

## Memory Browser Tabs

On `/memory` and Butler Detail `Memory` tab:

- `Facts`
- `Rules`
- `Episodes`

When opened inside Butler Detail, queries are scope-filtered to that butler.

## Contact Detail Tabs

On `/contacts/:contactId`:

- `Notes`
- `Interactions`
- `Gifts`
- `Loans`
- `Activity`

## Approvals Integration

The approvals module is integrated into the single-pane dashboard:

- Sidebar entry: `Approvals` (`/approvals`)
- Route: `/approvals` (pending queue + filters + metrics + decision workflows)
- Route: `/approvals/rules` (standing rules list/detail/create/revoke flows)

The main approvals page provides:
- Metrics dashboard with pending count, approval/rejection/auto-approval stats
- Filterable action queue (by tool, status, butler)
- Action detail dialog with approve/reject/rule creation
- Stale action expiry management

The rules page provides:
- Filterable rules list (by tool, active status, butler)
- Rule detail dialog with constraint inspection
- Rule revocation capability
- Use count and limit tracking
