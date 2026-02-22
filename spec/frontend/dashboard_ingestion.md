# Dashboard Ingestion Page Specification

Status: Normative (Target State)
Last updated: 2026-02-22
Primary owners: Dashboard + Switchboard

## 1. Purpose
This specification defines a unified ingestion control surface at `/ingestion` that consolidates source visibility, routing policy, and historical replay operations into one page.

The page models the ingestion lifecycle:

1. Source health and throughput (`Overview`)
2. Source-level operational detail (`Connectors`)
3. Deterministic policy and filtering (`Filters`)
4. Historical replay/backfill execution (`History`)

This spec supersedes the prior standalone connectors navigation flow by re-homing it under `/ingestion` while preserving backward-compatible redirects.

## 2. Frontend Conventions and Stack Alignment
The `/ingestion` page SHALL follow existing dashboard conventions used across `frontend/src/**`:

- UI primitives: `shadcn/ui` components (`Tabs`, `Card`, `Badge`, `Table`, `Drawer`, `Dialog`, `Button`, `Skeleton`).
- Layout and styling: Tailwind utility classes, existing spacing/typography tokens, and shared shell (`RootLayout` + `Sidebar`).
- Routing: React Router (`createBrowserRouter`, query-parameter controlled tab state).
- Server state: TanStack Query hooks with typed API client calls from `frontend/src/api/client.ts`.
- Charts: Recharts for trend/cost/time-series visualization (consistent with current dashboard chart usage).

## 3. Route, Sidebar, and Shortcut Contract

### 3.1 Primary route
- New page route: `/ingestion`
- Default tab: `overview` (no `?tab=` parameter)
- Supported tabs via query parameter:
  - `/ingestion?tab=connectors`
  - `/ingestion?tab=filters`
  - `/ingestion?tab=history`

### 3.2 Connector detail route
- New detail route under ingestion namespace:
  - `/ingestion/connectors/:connectorType/:endpointIdentity`

### 3.3 Redirect compatibility
- `/connectors` SHALL permanently redirect to `/ingestion?tab=connectors`.
- `/connectors/:connectorType/:endpointIdentity` SHALL redirect to `/ingestion/connectors/:connectorType/:endpointIdentity`.
- Redirects SHOULD preserve relevant query string parameters when present (`period`, date filters).

### 3.4 Sidebar changes
- Remove `Connectors` sidebar item.
- Add `Ingestion` sidebar item in the same slot where `Connectors` existed.
- Sidebar icon SHOULD use inbox/download semantics (e.g., inbox tray glyph), not a butler-specific icon.

### 3.5 Keyboard shortcut
- Add `g` then `e` navigation to `/ingestion`.
- Keep `g` then `i` mapped to `/issues` to avoid regressions in existing issue workflows.
- Keep `g` then `c` mapped to `/contacts` to avoid regressions in existing contact workflows.
- Shortcut hint dialog SHALL include the new `g` -> `e` mapping.

## 4. Tab Information Architecture
The page SHALL render four top-level tabs:

1. `Overview`
2. `Connectors`
3. `Filters`
4. `History`

Top-level tab state is URL-backed with `?tab=` and mirrors current query-param tab behavior used in `ButlerDetailPage`.

## 5. Tab Specifications and Wireframes

## 5.1 Overview tab
The Overview tab provides aggregate ingestion telemetry and high-level routing economics.

Required sections:
- Aggregate stat row:
  - total ingested (24h)
  - total skipped
  - total metadata-only
  - LLM calls saved
  - active connectors
- Volume trend chart with `24h | 7d | 30d` toggle.
- Tier breakdown donut:
  - Tier 1 (full)
  - Tier 2 (metadata)
  - Tier 3 (skip)
- LLM classification cost chart with daily cost split:
  - classification
  - pass-through/deterministic handling
- Connector x butler fanout matrix (shared with Connectors tab data model).
- Quick health badge row for connector liveness.

ASCII wireframe:
```text
+--------------------------------------------------------------------------------+
| Ingestion > Overview                                            [24h][7d][30d] |
+--------------------------------------------------------------------------------+
| [Ingested 24h] [Skipped] [Metadata-only] [LLM Calls Saved] [Active Connectors] |
+--------------------------------------------------------------------------------+
| Volume Trend (line/area)                      | Tier Breakdown (donut)         |
|                                                | T1 full / T2 metadata / T3 skip |
+--------------------------------------------------------------------------------+
| LLM Cost / Day (stacked bars: classify vs deterministic path)                  |
+--------------------------------------------------------------------------------+
| Fanout Matrix (rows=connectors, cols=butlers, cells=message count)             |
+--------------------------------------------------------------------------------+
| Health Row: [gmail online] [telegram stale] [api online] ...                   |
+--------------------------------------------------------------------------------+
```

## 5.2 Connectors tab
The Connectors tab is a full re-home of the existing connectors experience with no functional regression.

Contract:
- Absorb all existing `/connectors` content unchanged:
  - connector cards grid with liveness and health badges
  - volume time-series chart (`24h | 7d | 30d`)
  - fanout distribution table
  - error log panel
  - cross-connector summary stats
- Connector card enhancement for this ingestion rollout:
  - show `backfill active` badge when a running backfill exists for that connector.
- Row/card drill-down navigates to:
  - `/ingestion/connectors/:connectorType/:endpointIdentity`
  - this route re-homes existing connector detail page behavior.

ASCII wireframe:
```text
+--------------------------------------------------------------------------------+
| Ingestion > Connectors                                          [24h][7d][30d] |
+--------------------------------------------------------------------------------+
| Summary: total | online | stale | offline | ingested | failed | error rate     |
+--------------------------------------------------------------------------------+
| [Connector Card: gmail:user@x.com  online healthy  backfill active]            |
| [Connector Card: telegram:user      stale degraded]                              |
| [Connector Card: api:webhook        offline error ]                              |
+--------------------------------------------------------------------------------+
| Ingestion Volume by Connector (timeseries)                                      |
+--------------------------------------------------------------------------------+
| Fanout Distribution Table (connector x butler)                                  |
+--------------------------------------------------------------------------------+
| Error Log: timestamp | connector | state | message                               |
+--------------------------------------------------------------------------------+
```

## 5.3 Filters tab
The Filters tab manages deterministic ingestion policy before LLM classification.

Structure:
- Nested module section tabs inside Filters:
  - `Email` (default active)
  - `Telegram` (placeholder for future parity)

Email section requirements:
- Rules table (primary surface):
  - columns: `Priority`, `Condition`, `Action`, `Matches (7d)`, `Enabled`, `Actions`
  - priority ascending sort
  - drag-reorder priority
  - inline enabled toggle
  - row click opens editor drawer
  - empty state includes `Import defaults` CTA
- Rule editor drawer:
  - rule type selector (`sender domain`, `sender address`, `email header`, `MIME attachment type`)
  - dynamic condition fields by type
  - action selector:
    - `Route to <butler>`
    - `Skip (tier 3)`
    - `Metadata only (tier 2)`
    - `Low priority queue`
  - priority input
  - `Test` button (dry-run against recent messages)
  - save/cancel controls
- Thread affinity panel:
  - enabled toggle
  - TTL input in days (default `30`)
  - affinity hit-rate stat
- Gmail label filters panel:
  - include labels editable tags
  - exclude labels editable tags
- `Import defaults` flow with preview-before-confirm dialog.

ASCII wireframe:
```text
+--------------------------------------------------------------------------------+
| Ingestion > Filters                                               [Email][Tg]   |
+--------------------------------------------------------------------------------+
| Rules                                                 [Import defaults] [New]   |
| Pri | Condition                    | Action           | Match7d | On | Actions |
|  10 | sender_domain=chase.com      | route:finance    |  42     | ON | e d t   |
|  20 | header List-Unsubscribe      | metadata_only    | 318     | ON | e d t   |
|  30 | mime_type=text/calendar      | route:calendar   |  11     | ON | e d t   |
+--------------------------------------------------------------------------------+
| Thread Affinity: [ON] TTL(days): [30]   Hit rate: 67%                          |
+--------------------------------------------------------------------------------+
| Gmail Labels: Include [INBOX][IMPORTANT]  Exclude [PROMOTIONS][SOCIAL]         |
+--------------------------------------------------------------------------------+
| Drawer (on row click): type, condition fields, action, priority, test, save    |
+--------------------------------------------------------------------------------+
```

API dependencies:
- `GET /api/switchboard/triage-rules`
- `POST /api/switchboard/triage-rules`
- `PATCH /api/switchboard/triage-rules/:id`
- `DELETE /api/switchboard/triage-rules/:id`
- `POST /api/switchboard/triage-rules/test`

## 5.4 History tab
The History tab manages backfill jobs and replay cost controls.

Required sections:
- Per-module backfill cards:
  - Finance default date range: last 7 years
  - Health default date range: all time
  - Relationship default date range: last 2 years
  - Travel default date range: last 2 years
  - each card includes date-range picker and `Start Backfill`
- Active backfill panel:
  - progress bar per active job (`processed / estimated`)
  - cost so far and estimated total cost
  - rate (`emails/hour`)
  - pause/cancel controls
- Configuration panel:
  - max emails/hour slider (default `100`)
  - daily cost cap currency input (default `$5`)
- Completed backfills table:
  - date range
  - module
  - emails processed
  - cost
  - duration
  - status (`completed|cancelled|cost_capped|error`)
- First-run consent dialog on first start action:
  - estimated spend
  - privacy notice
  - explicit user confirmation

Connector-liveness gating:
- `Start Backfill` SHALL be disabled when the target connector is offline.
- Disabled state message: `Start a connector to enable backfill`.

ASCII wireframe:
```text
+--------------------------------------------------------------------------------+
| Ingestion > History                                                            |
+--------------------------------------------------------------------------------+
| Backfill Cards                                                                  |
| [Finance      from:2019-01-01 to:today   Start] [disabled if connector offline]|
| [Health       from:all-time to:today     Start]                                |
| [Relationship from:2024-01-01 to:today   Start]                                |
| [Travel       from:2024-01-01 to:today   Start]                                |
+--------------------------------------------------------------------------------+
| Active Backfills                                                                |
| finance-job-1  [##########----] 12,420/18,000  $2.10/$3.80  620/hr [Pause][X] |
+--------------------------------------------------------------------------------+
| Config: Max emails/hr [---|----] 100    Daily cost cap [$5.00]                 |
+--------------------------------------------------------------------------------+
| Completed Log                                                                    |
| Range              | Module | Processed | Cost | Duration | Status             |
| 2025-01..2025-12   | travel | 8,404     | 1.22 | 2h14m    | completed          |
+--------------------------------------------------------------------------------+
```

## 6. Query-State and URL Behavior

### 6.1 Top-level query state
- `tab` controls active top-level tab.
- `overview` is default and SHOULD omit `tab` from the URL.

### 6.2 Tab-local query state
Tab-local filters SHOULD be URL-backed to preserve deep links and shareable views:

- `period` for Overview/Connectors charts (`24h|7d|30d`).
- `filterModule` for Filters (`email|telegram`).
- `rulesEnabled` filter (`all|enabled|disabled`) for Filters table.
- `historyStatus` filter (`active|paused|completed|all`) for History log.
- `historyFrom`, `historyTo` for History date constraints.

Recommended URL examples:
- `/ingestion?tab=overview&period=7d`
- `/ingestion?tab=connectors&period=30d`
- `/ingestion?tab=filters&filterModule=email&rulesEnabled=enabled`
- `/ingestion?tab=history&historyStatus=active`

## 7. TanStack Query Contract
Shared query keys SHALL be used where data overlaps across tabs.

Recommended key families:
- `ingestionKeys.connectorsList(period)`
- `ingestionKeys.connectorsSummary(period)`
- `ingestionKeys.fanout(period)`
- `ingestionKeys.connectorDetail(connectorType, endpointIdentity, period)`
- `ingestionKeys.triageRules(module, filters)`
- `ingestionKeys.triageRuleTest()` (mutation)
- `ingestionKeys.backfillJobs(filters)`
- `ingestionKeys.backfillJob(jobId)`

Data-sharing rules:
- Overview and Connectors SHALL reuse connector list/summary/fanout keys.
- Switching from Overview to Connectors SHOULD reuse warm cache instead of forcing fresh load.
- Tab-specific data SHALL lazy-load on first activation (`enabled: activeTab === ...`).

Staleness targets:
- Overview: `30s`
- Connectors: `30s`
- Filters: `60s`
- History: `10s` (active job progress)

## 8. Backend Dependency Notes
This frontend spec depends on Option B MCP-mediated backfill architecture.

Required backend dependencies:

1. `switchboard.backfill_jobs` table and lifecycle state model (bead `butlers-0bz3.8`).
2. Switchboard MCP tools (bead `butlers-0bz3.13`):
   - `create_backfill_job`
   - `backfill.pause`
   - `backfill.cancel`
   - `backfill.resume`
   - `backfill.poll`
   - `backfill.progress`
3. Connector polling loop integration (bead `butlers-0bz3.12`) so connectors claim and report backfill work.

Dashboard read APIs required:
- `GET /api/switchboard/backfill-jobs`
- `GET /api/switchboard/backfill-jobs/:id`

Dashboard write APIs required:
- `POST /api/switchboard/backfill-jobs` (start new backfill; maps to `create_backfill_job`)
- `POST /api/switchboard/backfill-jobs/:id/pause` (maps to `backfill.pause`)
- `POST /api/switchboard/backfill-jobs/:id/cancel` (maps to `backfill.cancel`)
- `POST /api/switchboard/backfill-jobs/:id/resume` (maps to `backfill.resume`)

Dashboard write behavior:
- Dashboard API SHALL call Switchboard MCP tools for backfill lifecycle writes.
- History-tab writes MUST NOT update backfill tables directly from frontend.

Filters backend contract dependency:
- Triage rule API contract from pre-classification spec (`butlers-0bz3.3`) is required for Filters tab readiness.

## 9. Responsive and Accessibility Contract

Desktop:
- Full top tab bar visible.
- Table-heavy layouts for rules/history.
- Two-column chart and summary compositions where available.

Mobile:
- Horizontally scrollable top tab bar.
- Card-first rendering for connector and backfill content.
- Simplified/stacked table representations for rule/history rows.

Accessibility:
- Tab controls MUST be keyboard-navigable and ARIA-compliant.
- Badge-only states MUST include text labels for screen readers.
- Dialog/drawer flows (rule editor, consent) MUST trap focus and support escape-close semantics.

## 10. Out of Scope
This spec does not define:
- implementation of backfill engine internals,
- triage evaluator internals,
- connector protocol changes beyond UI-visible status contracts.

## 11. Acceptance Mapping

1. `/ingestion` with four tabs defined: covered in Sections 3-5.
2. Connectors tab full absorption of `/connectors`: covered in Section 5.2.
3. Filters tab rule table/editor/thread affinity/labels: covered in Section 5.3.
4. History tab backfill cards/progress/cost caps: covered in Section 5.4.
5. Routing redirects from `/connectors`: covered in Section 3.3.
6. Sidebar navigation changes: covered in Section 3.4.
7. Wireframe/ASCII per tab: included in Sections 5.1-5.4.
8. Existing frontend conventions (shadcn/ui, Tailwind, Router, Query): covered in Section 2.
