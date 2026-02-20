# Butlers Dashboard — Frontend & API Project Plan

> **Status (Current):** Historical planning document. The canonical, implementation-grounded frontend specifications now live under `docs/frontend/`.

> **Goal:** A "single pane of glass" web dashboard over the deployed butler infrastructure — topology, health, sessions, schedules, state, traces, costs, errors, skills, memory, and butler-specific domain data. Full read/write admin control. Not the primary interaction method (chat is), but the definitive place to understand what the system is doing, what it costs, and what's broken.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                        Browser (React)                         │
│                                                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ Overview  │ │ Butler   │ │ Sessions │ │ Butler-Specific  │  │
│  │ Topology  │ │ Detail   │ │ & Traces │ │ (CRM/Health/Gen) │  │
│  └─────┬────┘ └─────┬────┘ └─────┬────┘ └────────┬─────────┘  │
│        └─────────────┴───────────┴────────────────┘            │
│                              │ HTTP (REST)                     │
└──────────────────────────────┼─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│                     Dashboard API (FastAPI)                     │
│                     src/butlers/api/                            │
│                                                                │
│  ┌─────────────────────┐    ┌──────────────────────────────┐   │
│  │   MCP Client        │    │   Direct DB Reads (asyncpg)  │   │
│  │   (real-time ops)   │    │   (data browsing)            │   │
│  │                     │    │                              │   │
│  │ - trigger()         │    │ - sessions, schedules, state │   │
│  │ - tick()            │    │ - contacts, measurements     │   │
│  │ - status()          │    │ - entities, routing log      │   │
│  └─────────┬───────────┘    └──────────┬───────────────────┘   │
│            │ MCP                       │ SQL                   │
└────────────┼───────────────────────────┼───────────────────────┘
             │                           │
   ┌─────────▼──────────┐    ┌──────────▼───────────────────┐
   │  Butler MCP Daemons │    │  PostgreSQL (per-butler DBs) │
   │  (ports 40100-40199)  │    │  butler_switchboard          │
   └─────────────────────┘    │  butler_general              │
                              │  butler_relationship         │
                              │  butler_health               │
                              │  butler_heartbeat            │
                              └──────────────────────────────┘
```

### Data Access Strategy

The dashboard API uses **two access patterns**:

| Pattern             | When                                                                        | Why                                                                                                   |
| ------------------- | --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| **MCP client**      | Real-time operations: `status()`, `trigger()`, `tick()`, butler discovery   | These need live data from running daemons. Also the authoritative path for write operations.          |
| **Direct DB reads** | Data browsing: sessions, schedules, state, contacts, measurements, entities | Efficient for paginated lists, search, aggregation. Reads from each butler's dedicated PostgreSQL DB. |

> **Note:** Direct DB reads are a pragmatic exception to the "strict DB isolation" rule. The dashboard is an administrative tool, not a butler. It reads but never writes to butler DBs directly — all writes go through MCP tools.

---

## Required Framework Change: Core `notify()` Tool

The dashboard's Notifications view depends on a framework-level addition: a core `notify()` tool on every butler. This solves the **outbound messaging gap** — currently butlers can receive work but cannot push messages to the user (e.g., reminders, alerts).

### The Problem

Butler runtime instances are locked down to their own MCP server. A Health butler runtime instance that discovers "medication due in 30 min" has no way to send a Telegram message — only the Switchboard has the `telegram` module. The current spec hand-waves with "store in state for Switchboard delivery" but there's no actual delivery mechanism.

### The Solution: Core `notify()` Tool

Every butler daemon holds an MCP client connection to the Switchboard. A new core tool, `notify()`, lets any runtime instance send outbound messages:

```
Core Tools (addition)
└── notify(channel, message, recipient?)  → delivery result
```

**Flow:**
```
1. Health scheduler fires → runtime instance spawns
2. Runtime instance checks medications, finds Metformin due
3. Runtime instance calls notify(channel="telegram", message="Time to take Metformin 500mg")
4. Health butler daemon → MCP client → Switchboard.deliver("telegram", message)
5. Switchboard's telegram module sends the message
6. Switchboard logs the notification in its DB
7. notify() returns delivery result to the runtime instance
```

**Implementation notes:**
- Synchronous from the runtime instance's perspective — `notify()` blocks until delivered (or fails)
- The Switchboard gets a new tool: `deliver(channel, message, recipient?, metadata?)` — distinct from `route()` which forwards tool calls to other butlers
- Notification log lives in the Switchboard's DB (it's the delivery authority)
- If Switchboard is unreachable, `notify()` returns an error — Runtime instance can store in state as fallback
- `channel` values: `telegram`, `email` (matches Switchboard's modules)
- `recipient` is optional — defaults to the system owner (personal system, single user for v1)

### Switchboard Notification Schema

```sql
-- Add to butler_switchboard database
CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_butler TEXT NOT NULL,          -- which butler sent this
    channel TEXT NOT NULL,                -- 'telegram', 'email'
    recipient TEXT,                       -- chat_id, email address, or null for default
    message TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}', -- arbitrary context (medication_id, contact_id, etc.)
    status TEXT NOT NULL DEFAULT 'sent',  -- 'sent', 'failed', 'pending'
    error TEXT,                           -- error message if failed
    session_id UUID,                      -- runtime session that triggered this
    trace_id TEXT,                        -- OTel trace ID
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_notifications_source ON notifications(source_butler, created_at DESC);
CREATE INDEX idx_notifications_channel ON notifications(channel, created_at DESC);
CREATE INDEX idx_notifications_status ON notifications(status);
```

> **This is a framework change, not a dashboard change.** It needs to be added to `PROJECT_PLAN.md` and implemented before the dashboard's notification features work. The dashboard just reads from this table and provides visibility.

---

## Tech Stack

### Frontend (`frontend/`)

| Layer          | Choice                       | Why                                              |
| -------------- | ---------------------------- | ------------------------------------------------ |
| Framework      | React 18+ with TypeScript    | Standard, wide ecosystem                         |
| Build          | Vite                         | Fast dev server, good defaults                   |
| Router         | React Router v7              | Standard, well-documented                        |
| Server state   | TanStack Query (React Query) | Caching, refetching, optimistic updates          |
| UI components  | shadcn/ui + Radix primitives | Copy-paste components, full control, modern      |
| Styling        | Tailwind CSS                 | Utility-first, pairs with shadcn                 |
| Topology graph | React Flow                   | Interactive node-based diagrams                  |
| Charts         | Recharts                     | Health measurements, session trends, cost charts |
| Date handling  | date-fns                     | Lightweight, tree-shakeable                      |
| Search         | cmdk (Command Menu)          | Global search palette (Cmd+K)                    |

### Backend API (`src/butlers/api/`)

| Layer      | Choice                              | Why                                                 |
| ---------- | ----------------------------------- | --------------------------------------------------- |
| Framework  | FastAPI                             | Already in the Python ecosystem, async-native       |
| Database   | asyncpg (direct pool per butler DB) | Already used by the butler framework                |
| MCP Client | FastMCP client                      | Same library used by butlers for inter-butler calls |
| Validation | Pydantic v2                         | FastAPI native, shared with butler config models    |

---

## Pages & Views

### 1. Overview Dashboard (`/`)

The landing page. At-a-glance system health. Answers: "Is everything OK? What just happened? What's it costing me?"

**Components:**

- **Topology graph** (React Flow): Interactive node diagram showing:
  - Switchboard node (center) with edges to each butler
  - Heartbeat node with dashed edges to all butlers
  - Each node shows: name, port, status badge (healthy/degraded/down), module count
  - **Active session indicator**: Node pulses/glows when a runtime instance is currently running
  - Click a node → navigate to butler detail
  - Edge labels: "MCP route", "tick"
  - Module dependency health: small colored dots per module on each node (green=connected, red=unreachable)

- **Aggregate stats bar**: Total butlers, active/healthy count, total sessions today, failed sessions, avg response time, **estimated cost today**

- **Issues panel** (prominent, top of page when non-empty):
  - Unreachable butlers (status: down)
  - Failing scheduled tasks (N consecutive failures)
  - Module dependency failures (e.g., "telegram: bot token invalid", "email: IMAP connection refused")
  - Cost anomalies (butler spending >2x its 7-day average)
  - Each issue: severity badge (critical/warning), butler name, short description, link to detail
  - Dismissable per-issue (stores dismissed state locally)

- **Cost summary widget**: Today's spend, 7-day trend sparkline, top spender butler. Links to full cost view.

- **Recent activity feed**: Last N events across all butlers — not just sessions but a **unified timeline** mixing:
  - runtime sessions (with success/fail, duration, token count)
  - Scheduled task dispatches
  - Routing decisions (Switchboard)
  - Heartbeat ticks (collapsed — "Heartbeat: 5 butlers ticked")
  - Errors and failures
  - State changes (key set/deleted)
  - Each entry: timestamp, butler badge, event type icon, one-line summary

### 2. Butler Detail (`/butlers/:name`)

Tabbed detail view for a single butler.

**Tabs:**

#### 2a. Overview Tab
- Butler identity: name, description, port, uptime
- **Active session indicator**: "Currently running runtime session" with elapsed time, or "Idle"
- Module badges with health status (e.g., `telegram ●` green, `email ●` red)
- **Cost card**: Sessions today, tokens used today, estimated cost today, 7-day trend sparkline
- Error summary: recent failures count, link to filtered session view

#### 2f. Config Tab
- Full `butler.toml` rendered in a clean property view (not raw TOML — structured display of name, port, DB, schedules, module configs)
- Full `CLAUDE.md` rendered as markdown (the butler's personality/instructions)
- `AGENTS.md` rendered as markdown (runtime agent notes)
- Module credential status: for each module, show required env vars and whether they're set (checkmark) or missing (warning). Never show actual values.
- Raw config toggle: show the raw `butler.toml` text with syntax highlighting

#### 2g. Skills Tab
- List of skills from the butler's `skills/` directory
- Each skill card: name, description (first line of SKILL.md), last used (searched from session prompts)
- Click skill → expanded view: full SKILL.md rendered as markdown, list of files in the skill directory
- "Trigger with skill" button: pre-fills the trigger prompt with "Run the {skill} skill"

#### 2b. Sessions Tab
- Table: timestamp, trigger source, prompt (truncated), duration, tokens (in/out), estimated cost, success/fail
- Click row → session detail drawer/page:
  - Full prompt text
  - Tool calls timeline (ordered, with arguments and results)
  - Result text
  - Error details (if failed)
  - Token breakdown: input tokens, output tokens, total cost
  - Trace ID (links to trace view)
- Filters: date range, trigger source, success/fail
- Pagination

#### 2c. Schedules Tab
- Table: name, cron expression (with human-readable next run), source (toml/db), enabled, last run, last result
- **Write ops:**
  - Create new schedule (name, cron, prompt)
  - Edit schedule (inline or modal)
  - Toggle enable/disable
  - Delete schedule (with confirmation)

#### 2d. State Store Tab
- Key-value browser: key, value (JSON pretty-print), last updated
- Search/filter by key prefix
- **Write ops:**
  - Set key (JSON editor)
  - Delete key (with confirmation)
- Expandable rows for large JSONB values

#### 2e. Trigger Tab
- Prompt textarea to trigger the butler ad-hoc
- History of manual triggers with results
- "Run now" button for any scheduled task

### 3. Butler-Specific Views

These tabs appear conditionally based on the butler's identity.

#### 3a. Relationship Butler (`/butlers/relationship/...`)

**Contacts Sub-Tab:**
- Searchable table: name, company, labels, last interaction
- Click → Contact detail page:
  - Header card: name, photo placeholder, job title, company, pronouns
  - Contact info (emails, phones, social links)
  - Important dates (with countdown to next occurrence)
  - Quick facts
  - Relationships (visual mini-graph or list)
  - Tabbed lower section:
    - Notes (chronological, with emotion badge)
    - Interactions (timeline: calls, meetings, messages)
    - Gifts (pipeline: idea → bought → given)
    - Loans (outstanding vs settled)
  - Activity feed (polymorphic timeline of all changes)

**Groups Sub-Tab:**
- Groups list with member count
- Click → group detail with member list

**Routing Log Sub-Tab** (Switchboard only):
- Table: timestamp, source channel, source ID, routed to, prompt summary

#### 3b. Health Butler (`/butlers/health/...`)

**Measurements Sub-Tab:**
- Chart dashboard: line charts for weight, blood pressure, heart rate over time
- Date range selector
- Log new measurement form
- Table view with raw data

**Medications Sub-Tab:**
- Active medications list: name, dosage, frequency, schedule
- Dose log: recent doses taken/skipped
- Adherence stats (% taken on time this week/month)

**Conditions Sub-Tab:**
- Condition cards: name, status badge (active/managed/resolved), diagnosed date, notes

**Symptoms Sub-Tab:**
- Log: timestamp, name, severity (1-10 bar), notes, linked condition
- Frequency heatmap or trend chart

**Meals Sub-Tab:**
- Daily log: type (breakfast/lunch/dinner/snack), description, nutrition summary
- Simple calendar/timeline view

**Research Sub-Tab:**
- Searchable list: topic, title, tags
- Click → full content view with source link

#### 3c. General Butler (`/butlers/general/...`)

**Collections Sub-Tab:**
- Collection list with entity count and schema hint preview
- Click → filtered entity view for that collection

**Entities Sub-Tab:**
- Searchable table: title, collection, tags, created/updated
- Click → JSON viewer/editor for the entity's `data` field
- Tag filtering

### 4. Cross-Butler Sessions (`/sessions`)

Aggregate view of all runtime sessions across all butlers.

- Table: timestamp, butler, trigger source, prompt (truncated), duration, success/fail
- Filters: butler, date range, trigger source, success/fail
- Click row → session detail (same as butler-specific session view)

### 5. Trace Viewer (`/traces`)

Simplified distributed trace viewer for common use cases within the dashboard (complementary to Grafana/Tempo UI for advanced analysis).

**Trace List:**
- Table: trace ID (short), start time, duration, entry point (channel + butler), span count
- Filters: date range, butler, channel

**Trace Detail:**
- Vertical timeline/waterfall showing span hierarchy:
  ```
  ── switchboard.receive (telegram, chat_id=123)  [0ms]
     └── switchboard.classify                      [200ms]
         └── switchboard.route → health            [5ms]
             └── health.trigger                    [50ms]
                 └── health.llm_session             [3200ms]
                     ├── health.tool.measurement_log  [15ms]
                     ├── health.tool.state_set        [8ms]
                     └── health.tool.sessions_log     [12ms]
  ```
- Each span shows: name, duration, key attributes
- Click span → expanded attributes panel

### 6. Global Search (`Cmd+K`)

A command palette / search bar accessible from anywhere via `Cmd+K` (or `/`).

**Searches across:**
- Sessions (by prompt content, result text)
- State store keys and values (across all butlers)
- Scheduled task names and prompts
- Contacts (Relationship butler: name, company, notes)
- Entities (General butler: title, tags, data content)
- Research notes (Health butler: topic, title, content)
- Skills (name, SKILL.md content)

**Behavior:**
- Results grouped by category with icons (session, contact, entity, etc.)
- Each result: title, subtitle (butler name + category), snippet with match highlighted
- Enter → navigate to the detail view for that result
- Debounced search (300ms), shows recent searches when empty
- Backed by a single API endpoint that fans out across butler DBs

### 7. Unified Timeline (`/timeline`)

A chronological "what happened?" view mixing all event types across all butlers. The system's activity log.

**Event types (each with distinct icon and color):**
- runtime session started/completed (with success/fail, duration, cost)
- Scheduled task dispatched
- Routing decision (Switchboard: message arrived → routed to butler)
- **Notification sent** (butler → Switchboard → telegram/email, with delivery status)
- Heartbeat tick (collapsible — "Heartbeat: 5 butlers ticked, 0 failures")
- State change (key set/deleted, with butler name and key)
- Error/failure (session failure, module error, unreachable butler, **notification delivery failure**)

**Controls:**
- Butler filter (multi-select)
- Event type filter (multi-select)
- Date range picker
- Auto-refresh toggle (poll every 10s)
- Infinite scroll (load older events on scroll)

**Design:** Vertical timeline with alternating left/right event cards (or single-column for density). Each card: timestamp, butler badge, event icon, one-line summary, expandable details.

### 8. Cost & Usage (`/costs`)

Token usage and cost tracking across the system.

**Components:**
- **Daily/weekly/monthly spend chart** (Recharts area chart): total cost over time, stacked by butler
- **Butler breakdown table**: butler name, sessions count, total tokens (in/out), estimated cost, % of total, trend arrow (up/down vs previous period)
- **Top sessions table**: most expensive individual sessions (by token count), with butler, prompt snippet, cost
- **Schedule cost analysis**: each scheduled task's average cost per run, runs per day, projected monthly cost
- **Anomaly indicators**: sessions that cost >3x the butler's average get flagged

**Data source:** Token counts stored in the sessions table (requires schema addition — see Schema Changes below). Cost estimates derived from token counts × per-model pricing (configurable in dashboard settings).

### 9. Notifications (`/notifications`)

Cross-butler notification history — every outbound message the system has sent to you.

**Components:**

- **Notification feed**: Reverse-chronological list of all notifications across butlers
  - Each entry: timestamp, source butler badge, channel icon (telegram/email), message text, delivery status badge (sent/failed/pending)
  - Failed notifications highlighted in red with error details
  - Click → expanded view with full message, metadata (medication name, contact info, etc.), linked session
- **Filters**: source butler, channel, status (sent/failed), date range
- **Stats bar**: total sent today, failure rate, most active butler, most used channel

**Also appears on:**
- **Butler detail page** — notification sub-section on the Overview tab: "Recent notifications from this butler" (last 5)
- **Overview page** — recent notifications in the activity feed, failed deliveries surfaced in the Issues panel
- **Unified timeline** — notification events mixed into the cross-butler event stream

### 10. Memory System (`/butlers/:name/memory`) — *Contingent on memory plan finalization*

> The memory system is still in planning (see `MEMORY_PROJECT_PLAN.md`). This section will be implemented once the memory schema and MCP tools are built. Included here for completeness.

**Components:**
- **Tier overview cards**: Eden / Mid-Term / Long-Term, each showing entry count, capacity %, oldest entry, newest entry
- **Promotion/eviction activity**: recent promotions (Eden → Mid, Mid → Long) and evictions, shown as a timeline
- **Memory browser**: searchable table of memory entries across tiers
  - Columns: tier badge, content (truncated), tags, reference count, last referenced, created
  - Filters: tier, tag, date range
  - Click → full content view with metadata
- **Memory health indicators**:
  - Eden eviction rate (are we losing too many memories?)
  - Long-term saturation (approaching capacity?)
  - Promotion rate (are memories graduating?)

### 11. Layout & Navigation

**Sidebar navigation:**
```
🔍 Search                (Cmd+K — global search palette)
─────────────
Overview                 (topology + stats + issues)
Timeline                 (unified cross-butler event log)
─────────────
Butlers
  ├── switchboard  ●
  ├── general      ●
  ├── relationship ●
  ├── health       ●
  └── heartbeat    ●
─────────────
Sessions                 (cross-butler)
Traces                   (cross-butler)
Notifications            (outbound messages)
Costs                    (usage & spending)
```

- Collapsible sidebar
- Butler sidebar items show colored status dot (green/yellow/red)
- **Issues badge**: red count badge on Overview when there are active issues
- **Cost indicator**: today's spend shown subtly in sidebar footer
- Dark mode toggle (Tailwind dark mode)
- Responsive: sidebar collapses on mobile

---

## API Endpoints

### Butler Discovery & Status

```
GET    /api/butlers                    → list all butlers with live status + health
GET    /api/butlers/:name              → single butler detail + live status
GET    /api/butlers/:name/config       → full butler.toml, CLAUDE.md, AGENTS.md content
GET    /api/butlers/:name/skills       → list skills from skills/ directory with SKILL.md content
GET    /api/butlers/:name/modules      → module list with dependency health status
POST   /api/butlers/:name/trigger      → trigger runtime instance with prompt {prompt: string}
POST   /api/butlers/:name/tick         → force scheduler tick
```

### Sessions

```
GET    /api/sessions                   → cross-butler sessions (paginated, filterable)
GET    /api/butlers/:name/sessions     → sessions for one butler
GET    /api/butlers/:name/sessions/:id → session detail with tool calls
```

Query params: `?limit=20&offset=0&trigger_source=...&success=true&from=...&to=...`

### Schedules

```
GET    /api/butlers/:name/schedules            → list schedules
POST   /api/butlers/:name/schedules            → create schedule {name, cron, prompt}
PUT    /api/butlers/:name/schedules/:id        → update schedule
DELETE /api/butlers/:name/schedules/:id        → delete schedule
PATCH  /api/butlers/:name/schedules/:id/toggle → enable/disable
```

### State Store

```
GET    /api/butlers/:name/state           → list state entries (?prefix=...)
GET    /api/butlers/:name/state/:key      → get value
PUT    /api/butlers/:name/state/:key      → set value {value: any}
DELETE /api/butlers/:name/state/:key      → delete key
```

### Traces

```
GET    /api/traces                     → list traces (paginated, filterable)
GET    /api/traces/:trace_id           → trace detail with span tree
```

### Relationship Butler

```
GET    /api/butlers/relationship/contacts              → list/search (?q=..., ?label=...)
GET    /api/butlers/relationship/contacts/:id          → contact detail + related data
GET    /api/butlers/relationship/contacts/:id/feed     → activity feed
GET    /api/butlers/relationship/contacts/:id/notes    → notes
GET    /api/butlers/relationship/contacts/:id/interactions → interactions
GET    /api/butlers/relationship/contacts/:id/gifts    → gifts
GET    /api/butlers/relationship/contacts/:id/loans    → loans
GET    /api/butlers/relationship/groups                → list groups
GET    /api/butlers/relationship/groups/:id             → group detail + members
GET    /api/butlers/relationship/labels                → list labels
GET    /api/butlers/relationship/upcoming-dates         → upcoming important dates
```

### Health Butler

```
GET    /api/butlers/health/measurements       → list (?type=weight&from=...&to=...)
GET    /api/butlers/health/medications         → list (?active=true)
GET    /api/butlers/health/medications/:id/doses → dose log
GET    /api/butlers/health/conditions          → list
GET    /api/butlers/health/symptoms            → list (?name=...&from=...&to=...)
GET    /api/butlers/health/meals               → list (?from=...&to=...)
GET    /api/butlers/health/research            → list/search (?topic=...&q=...)
```

### General Butler

```
GET    /api/butlers/general/collections        → list collections
GET    /api/butlers/general/collections/:id    → collection detail
GET    /api/butlers/general/entities           → list/search (?collection=...&tag=...&q=...)
GET    /api/butlers/general/entities/:id       → entity detail
```

### Switchboard

```
GET    /api/butlers/switchboard/routing-log    → routing decisions (?from=...&to=...)
GET    /api/butlers/switchboard/registry       → butler registry
```

### Notifications

```
GET    /api/notifications                  → cross-butler notification history (paginated, filterable)
GET    /api/notifications/stats            → summary: total today, failure rate, by butler, by channel
GET    /api/butlers/:name/notifications    → notifications sent by a specific butler
```

Query params: `?limit=20&offset=0&butler=health&channel=telegram&status=sent&from=...&to=...`

Data source: `notifications` table in the Switchboard's DB (all outbound messages flow through the Switchboard).

### Global Search

```
GET    /api/search?q=...               → cross-butler search (sessions, state, contacts, entities, skills)
```

Returns results grouped by category:
```json
{
  "results": {
    "sessions": [...],
    "contacts": [...],
    "entities": [...],
    "state": [...],
    "skills": [...],
    "research": [...]
  },
  "total": 42
}
```

### Unified Timeline

```
GET    /api/timeline                    → cross-butler event stream (paginated)
```

Query params: `?limit=50&before=<cursor>&butlers=health,relationship&types=session,error,schedule`

Returns mixed event types with a common envelope:
```json
{
  "events": [
    {"type": "session", "butler": "health", "timestamp": "...", "data": {...}},
    {"type": "notification", "butler": "health", "timestamp": "...", "data": {"channel": "telegram", "status": "sent", ...}},
    {"type": "routing", "butler": "switchboard", "timestamp": "...", "data": {...}},
    {"type": "error", "butler": "relationship", "timestamp": "...", "data": {...}}
  ],
  "next_cursor": "..."
}
```

### Cost & Usage

```
GET    /api/costs/summary              → aggregate cost data (today, 7d, 30d by butler)
GET    /api/costs/daily?from=...&to=.. → daily cost breakdown
GET    /api/costs/top-sessions?limit=10 → most expensive sessions
GET    /api/costs/by-schedule          → average cost per scheduled task
```

### Issues / Error Aggregation

```
GET    /api/issues                     → current active issues (unreachable butlers, failing tasks, module errors, cost anomalies)
```

### Memory (contingent on memory plan)

```
GET    /api/butlers/:name/memory/stats         → tier counts, capacity, health
GET    /api/butlers/:name/memory/entries        → browse/search (?tier=eden&tag=...&q=...)
GET    /api/butlers/:name/memory/entries/:id    → single memory detail
GET    /api/butlers/:name/memory/activity       → recent promotions/evictions
```

---

## Data Flow Examples

### Viewing Butler Status (Read)

```
Browser → GET /api/butlers
  → API reads butler.toml configs to discover butlers
  → API calls status() via MCP client on each butler
  → Returns aggregated list with live health
```

### Triggering a Butler (Write)

```
Browser → POST /api/butlers/health/trigger {prompt: "Log weight 75kg"}
  → API calls trigger(prompt) via MCP client on health butler
  → Health butler spawns runtime instance, runtime instance runs, returns result
  → API returns session result to browser
```

### Browsing Contacts (Read)

```
Browser → GET /api/butlers/relationship/contacts?q=alice
  → API connects directly to butler_relationship DB
  → SELECT * FROM contacts WHERE first_name ILIKE '%alice%' ...
  → Returns paginated results
```

### Viewing Notifications (Read)

```
Browser → GET /api/notifications?channel=telegram&from=2026-02-09
  → API connects to butler_switchboard DB
  → SELECT * FROM notifications WHERE channel='telegram' AND created_at >= ...
  → Returns paginated list with source butler, message, delivery status
```

### Sending a Reminder (Framework Flow — shown for context)

```
Heartbeat ticks Health butler at 08:00
  → Health scheduler: medication-reminder-check is due
  → runtime instance spawns, checks medications table, finds Metformin due
  → Runtime instance calls notify(channel="telegram", message="Time to take Metformin 500mg")
  → Health daemon → MCP client → Switchboard.deliver("telegram", message)
  → Switchboard telegram module sends message
  → Switchboard logs to notifications table: {source: "health", channel: "telegram", status: "sent"}
  → Dashboard shows it in /notifications and /timeline
```

### Viewing a Trace (Read)

```
Browser → GET /api/traces/abc123
  → API queries sessions table across butler DBs WHERE trace_id='abc123'
  → API queries routing_log from switchboard DB WHERE trace_id='abc123'
  → Assembles span tree from session data + routing log entries
  → Returns hierarchical span structure
```

---

## Project Structure

```
butlers/
├── frontend/                          # React dashboard
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── components.json                # shadcn/ui config
│   ├── index.html
│   ├── public/
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/                       # API client (fetch wrappers, types)
│       │   ├── client.ts
│       │   ├── types.ts
│       │   └── hooks/                 # TanStack Query hooks
│       │       ├── useButlers.ts
│       │       ├── useSessions.ts
│       │       ├── useSchedules.ts
│       │       ├── useContacts.ts
│       │       ├── useMeasurements.ts
│       │       ├── useCosts.ts
│       │       ├── useTimeline.ts
│       │       ├── useSearch.ts
│       │       ├── useIssues.ts
│       │       ├── useNotifications.ts
│       │       └── ...
│       ├── components/                # Shared UI components
│       │   ├── ui/                    # shadcn/ui components
│       │   ├── layout/
│       │   │   ├── Sidebar.tsx
│       │   │   ├── PageHeader.tsx
│       │   │   ├── Shell.tsx
│       │   │   └── CommandPalette.tsx  # Cmd+K global search
│       │   ├── topology/
│       │   │   └── TopologyGraph.tsx  # React Flow graph
│       │   ├── sessions/
│       │   │   ├── SessionTable.tsx
│       │   │   └── SessionDetail.tsx
│       │   ├── schedules/
│       │   │   ├── ScheduleTable.tsx
│       │   │   └── ScheduleForm.tsx
│       │   ├── state/
│       │   │   └── StateBrowser.tsx
│       │   ├── traces/
│       │   │   ├── TraceList.tsx
│       │   │   └── TraceTimeline.tsx
│       │   ├── costs/
│       │   │   ├── CostSummary.tsx
│       │   │   ├── CostChart.tsx
│       │   │   └── TopSessions.tsx
│       │   ├── timeline/
│       │   │   └── UnifiedTimeline.tsx
│       │   ├── issues/
│       │   │   └── IssuesPanel.tsx
│       │   ├── skills/
│       │   │   ├── SkillsList.tsx
│       │   │   └── SkillDetail.tsx
│       │   ├── config/
│       │   │   ├── ConfigViewer.tsx
│       │   │   └── CredentialStatus.tsx
│       │   ├── notifications/
│       │   │   ├── NotificationFeed.tsx
│       │   │   └── NotificationStats.tsx
│       │   ├── memory/
│       │   │   ├── MemoryTierCards.tsx
│       │   │   ├── MemoryBrowser.tsx
│       │   │   └── MemoryActivity.tsx
│       │   ├── relationship/
│       │   │   ├── ContactTable.tsx
│       │   │   ├── ContactDetail.tsx
│       │   │   └── ...
│       │   ├── health/
│       │   │   ├── MeasurementChart.tsx
│       │   │   ├── MedicationTracker.tsx
│       │   │   └── ...
│       │   └── general/
│       │       ├── EntityBrowser.tsx
│       │       └── JsonViewer.tsx
│       ├── pages/
│       │   ├── Overview.tsx
│       │   ├── ButlerDetail.tsx
│       │   ├── Sessions.tsx
│       │   ├── Traces.tsx
│       │   ├── TraceDetail.tsx
│       │   ├── Timeline.tsx
│       │   ├── Costs.tsx
│       │   ├── Notifications.tsx
│       │   ├── relationship/
│       │   │   ├── ContactsPage.tsx
│       │   │   ├── ContactDetailPage.tsx
│       │   │   └── GroupsPage.tsx
│       │   ├── health/
│       │   │   ├── MeasurementsPage.tsx
│       │   │   ├── MedicationsPage.tsx
│       │   │   └── ...
│       │   └── general/
│       │       ├── CollectionsPage.tsx
│       │       └── EntitiesPage.tsx
│       ├── lib/
│       │   └── utils.ts               # Tailwind cn() helper, formatters
│       └── styles/
│           └── globals.css            # Tailwind imports + shadcn theme
│
├── src/butlers/
│   ├── api/                           # FastAPI dashboard API
│   │   ├── __init__.py
│   │   ├── app.py                     # FastAPI app factory
│   │   ├── deps.py                    # Dependency injection (DB pools, MCP clients)
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── butlers.py             # /api/butlers endpoints (status, config, skills, modules)
│   │   │   ├── sessions.py            # /api/sessions endpoints
│   │   │   ├── schedules.py           # /api/butlers/:name/schedules
│   │   │   ├── state.py               # /api/butlers/:name/state
│   │   │   ├── traces.py              # /api/traces endpoints
│   │   │   ├── search.py              # /api/search (global cross-butler search)
│   │   │   ├── timeline.py            # /api/timeline (unified event stream)
│   │   │   ├── costs.py               # /api/costs (usage & spending)
│   │   │   ├── issues.py              # /api/issues (error aggregation)
│   │   │   ├── notifications.py       # /api/notifications (outbound message log)
│   │   │   ├── memory.py              # /api/butlers/:name/memory (contingent)
│   │   │   ├── relationship.py        # /api/butlers/relationship/*
│   │   │   ├── health.py              # /api/butlers/health/*
│   │   │   ├── general.py             # /api/butlers/general/*
│   │   │   └── switchboard.py         # /api/butlers/switchboard/*
│   │   ├── models/                    # Pydantic response/request models
│   │   │   ├── __init__.py
│   │   │   ├── butler.py
│   │   │   ├── session.py
│   │   │   ├── schedule.py
│   │   │   ├── state.py
│   │   │   ├── trace.py
│   │   │   ├── notification.py
│   │   │   ├── relationship.py
│   │   │   ├── health.py
│   │   │   └── general.py
│   │   └── db.py                      # Multi-DB connection manager
│   └── ...                            # existing butler code
│
├── docker-compose.yml                 # add: dashboard-api, frontend services
└── docs/FRONTEND_PROJECT_PLAN.md           # this file
```

---

## Schema Changes Required

The following additions to the butler framework's database schema are required to support dashboard features:

### Sessions table — add token tracking

```sql
-- Add to sessions table (core schema)
ALTER TABLE sessions ADD COLUMN input_tokens INT;
ALTER TABLE sessions ADD COLUMN output_tokens INT;
ALTER TABLE sessions ADD COLUMN model TEXT;                    -- e.g., 'claude-sonnet-4-5-20250929'
ALTER TABLE sessions ADD COLUMN trace_id TEXT;                 -- OTel trace ID for correlation
ALTER TABLE sessions ADD COLUMN parent_session_id UUID;        -- for trace reconstruction
```

The Claude Agent SDK returns token usage in its response. The spawner should capture and store this alongside the session record. `trace_id` enables cross-butler trace correlation.

### Switchboard — notifications table

See the `notify()` core tool section above for the full schema. The `notifications` table lives in the Switchboard's database and logs every outbound message sent by any butler.

### Cost estimation

Cost is **derived, not stored** — computed at query time from `input_tokens × input_price + output_tokens × output_price`. Model pricing is configured in the dashboard API (a simple config file or env var), not in the database. This avoids schema changes when pricing changes.

---

## Milestones

### M0: Project Scaffolding

Set up both the frontend and API projects, verify they build and connect.

**API tasks:**
- [ ] Create `src/butlers/api/app.py` — FastAPI app with CORS, health endpoint
- [ ] Create `src/butlers/api/deps.py` — butler config discovery, DB pool manager
- [ ] Create `src/butlers/api/db.py` — multi-DB connection manager (one asyncpg pool per butler DB)
- [ ] Add `butlers dashboard` CLI command (starts the FastAPI server via uvicorn)
- [ ] Add FastAPI + uvicorn + httpx to dependencies

**Frontend tasks:**
- [ ] Scaffold `frontend/` with Vite + React + TypeScript
- [ ] Install and configure Tailwind CSS
- [ ] Install and configure shadcn/ui (init, add Button, Card, Table, Badge, Tabs)
- [ ] Create `api/client.ts` — base fetch wrapper pointing at API
- [ ] Create basic app shell: sidebar layout, React Router setup, dark mode toggle
- [ ] Install cmdk for command palette
- [ ] Verify: frontend dev server proxies to API, renders hello world

**Docker tasks:**
- [ ] Add `dashboard-api` service to docker-compose.yml
- [ ] Add `frontend` dev service to docker-compose.yml (optional, for containerized dev)

---

### M1: Butler Discovery & Overview Page

The landing page with topology graph, stats, and issues panel.

**API tasks:**
- [ ] `GET /api/butlers` — discover butlers from config dirs, call `status()` via MCP client on each
- [ ] `GET /api/butlers/:name` — single butler detail with live status
- [ ] `GET /api/issues` — aggregate active issues (unreachable butlers, failing tasks, module errors)
- [ ] Handle butler unreachable states gracefully (timeout, connection refused → status: "down")

**Frontend tasks:**
- [ ] Overview page with aggregate stats bar (total butlers, healthy count, sessions today, cost today)
- [ ] **Issues panel**: prominent alert section at top of overview page. Unreachable butlers, failing tasks, cost anomalies. Dismissable.
- [ ] Topology graph (React Flow):
  - Switchboard as center node
  - Each butler as a node with status badge
  - Heartbeat with dashed connections to all
  - Active session indicator (pulsing node when runtime instance running)
  - Module health dots on each node
  - Edge labels showing connection type
  - Click node → navigate to `/butlers/:name`
- [ ] Butler sidebar navigation populated from API
- [ ] Status dots in sidebar (green/yellow/red)
- [ ] Recent activity feed (last 10 events, cross-butler — preview of unified timeline)

---

### M2: Butler Detail — Sessions, Schedules, Config, Skills

Core visibility into what a butler is, what it has, and what it's been doing.

**API tasks:**
- [ ] `GET /api/butlers/:name/sessions` — paginated session list from direct DB read
- [ ] `GET /api/butlers/:name/sessions/:id` — session detail with tool calls
- [ ] `GET /api/butlers/:name/schedules` — schedule list from direct DB read
- [ ] `GET /api/butlers/:name/config` — read butler.toml, CLAUDE.md, AGENTS.md from disk
- [ ] `GET /api/butlers/:name/skills` — list skills/ directory, read SKILL.md files
- [ ] `GET /api/butlers/:name/modules` — module list with credential/dependency health status
- [ ] `GET /api/sessions` — cross-butler session aggregation

**Frontend tasks:**
- [ ] Butler detail page with tab navigation
- [ ] Overview tab: identity, modules with health badges, active session indicator, cost card, error summary
- [ ] Sessions tab:
  - Paginated table with trigger source, prompt preview, duration, tokens, cost, status
  - Session detail drawer: full prompt, tool call timeline, result, error, token breakdown, trace link
  - Filter by date range, trigger source, success/fail
- [ ] Schedules tab:
  - Table: name, cron, next run, source, enabled, last result
  - Human-readable cron descriptions
- [ ] **Config tab**: full butler.toml (structured view + raw toggle), CLAUDE.md (rendered markdown), AGENTS.md, credential status per module
- [ ] **Skills tab**: skill cards with name/description, click → full SKILL.md, "trigger with skill" button
- [ ] Cross-butler sessions page (`/sessions`)

---

### M3: Write Operations — Schedules, State, Trigger

Interactive admin capabilities.

**API tasks:**
- [ ] `POST /api/butlers/:name/trigger` — trigger via MCP client
- [ ] `POST /api/butlers/:name/tick` — force tick via MCP client
- [ ] Schedule CRUD: POST, PUT, DELETE, PATCH toggle — via MCP client
- [ ] State CRUD: GET list, GET key, PUT key, DELETE key — via MCP client for writes, DB for reads

**Frontend tasks:**
- [ ] Trigger tab: prompt textarea, submit, show result (with token usage + cost)
- [ ] Schedule CRUD:
  - Create form (name, cron expression builder, prompt textarea)
  - Edit modal
  - Enable/disable toggle
  - Delete with confirmation dialog
  - "Run now" button per task
- [ ] State store tab:
  - Key-value table with expandable JSON values
  - Prefix filter/search
  - Set key modal (key input + JSON editor)
  - Delete key with confirmation

---

### M4: Cost & Usage Tracking

Understanding what the system costs.

**Schema tasks:**
- [ ] Add `input_tokens`, `output_tokens`, `model` columns to sessions table
- [ ] Update LLM CLI spawner to capture token usage from SDK response and store in session record
- [ ] Alembic migration for the new columns

**API tasks:**
- [ ] `GET /api/costs/summary` — aggregate cost data (today, 7d, 30d totals; per-butler breakdown)
- [ ] `GET /api/costs/daily` — daily cost time series for charts
- [ ] `GET /api/costs/top-sessions` — most expensive sessions
- [ ] `GET /api/costs/by-schedule` — per-scheduled-task average cost and projected monthly spend
- [ ] Cost estimation logic: token counts × configurable per-model pricing

**Frontend tasks:**
- [ ] **Cost page** (`/costs`):
  - Daily/weekly/monthly spend area chart (stacked by butler)
  - Butler breakdown table (sessions, tokens, cost, % of total, trend)
  - Top expensive sessions table
  - Per-schedule cost analysis table
- [ ] **Cost widget on overview page**: today's spend, 7-day sparkline, top spender
- [ ] **Cost card on butler detail overview tab**: sessions today, tokens, cost, trend
- [ ] Cost column in session tables throughout the app
- [ ] Anomaly badges on sessions that cost >3x the butler's average

---

### M5: Simplified Trace Viewer

Inline trace visualization for common use cases within the dashboard (LGTM stack provides full tracing via Grafana UI).

**Schema tasks:**
- [ ] Add `trace_id` and `parent_session_id` columns to sessions table (if not done in M4)
- [ ] Update spawner to propagate and store trace context

**API tasks:**
- [ ] `GET /api/traces` — aggregate trace data from sessions + routing_log across butler DBs
- [ ] `GET /api/traces/:trace_id` — assemble span tree from session records and routing log
- [ ] Trace data model: reconstruct parent-child span relationships from trigger_source, trace_id, parent_session_id

**Frontend tasks:**
- [ ] Trace list page: trace ID, start time, total duration, entry point, span count, total cost
- [ ] Trace detail page:
  - Waterfall/timeline view showing span hierarchy
  - Each span bar: name, duration, colored by butler
  - Click span → attributes panel (butler name, tool args, result summary, tokens)
- [ ] Link from session detail → trace view (via trace_id)

---

### M6: Unified Timeline & Global Search

Cross-cutting visibility features.

**API tasks:**
- [ ] `GET /api/timeline` — cross-butler event stream aggregating sessions, routing log, heartbeat ticks, state changes, errors. Cursor-based pagination.
- [ ] `GET /api/search?q=...` — fan-out search across all butler DBs (sessions, state, contacts, entities, skills, research). Returns grouped results.

**Frontend tasks:**
- [ ] **Timeline page** (`/timeline`):
  - Vertical event stream mixing all event types
  - Each event: timestamp, butler badge, event type icon, one-line summary
  - Expandable detail per event
  - Filters: butler (multi-select), event type (multi-select), date range
  - Auto-refresh toggle
  - Infinite scroll
- [ ] **Command palette** (`Cmd+K`):
  - Global search input with debounced API call
  - Results grouped by category (sessions, contacts, entities, state, skills, research)
  - Each result: title, butler badge, snippet with highlight
  - Enter → navigate to detail view
  - Recent searches when empty

---

### M7: Notifications View

Visibility into outbound messages sent by butlers (reminders, alerts, responses).

> **Dependency:** Requires the core `notify()` tool and Switchboard `notifications` table to be implemented in the framework. See "Required Framework Change" section above.

**API tasks:**
- [ ] `GET /api/notifications` — paginated notification history from Switchboard DB (filterable by butler, channel, status, date range)
- [ ] `GET /api/notifications/stats` — summary stats (total today, failure rate, by butler, by channel)
- [ ] `GET /api/butlers/:name/notifications` — notifications filtered by source butler

**Frontend tasks:**
- [ ] **Notifications page** (`/notifications`):
  - Reverse-chronological feed of all outbound notifications
  - Each entry: timestamp, source butler badge, channel icon (telegram/email), message text, delivery status badge (sent/failed/pending)
  - Failed notifications highlighted with error details
  - Click → expanded view: full message, metadata (medication name, contact info, etc.), linked session
  - Filters: source butler, channel, status, date range
  - Stats bar: total sent today, failure rate, most active butler
- [ ] **Butler detail integration**: "Recent notifications" section on Overview tab (last 5 from this butler)
- [ ] **Overview integration**: failed notification deliveries surfaced in Issues panel
- [ ] **Timeline integration**: notification events in the unified timeline (M6)

---

### M8: Relationship Butler Views

CRM data visibility.

**API tasks:**
- [ ] Contacts: list/search, detail with joined data (info, dates, facts, relationships)
- [ ] Contact sub-resources: notes, interactions, gifts, loans, feed
- [ ] Groups: list, detail with members
- [ ] Labels: list
- [ ] Upcoming dates: next N days of important dates

**Frontend tasks:**
- [ ] Contacts table: searchable, filterable by label, sortable
- [ ] Contact detail page:
  - Header card (name, company, labels)
  - Contact info section
  - Important dates with countdown badges
  - Quick facts
  - Relationships list
  - Tabbed content: Notes | Interactions | Gifts | Loans
  - Activity feed timeline
- [ ] Groups page: list with member count, click → detail
- [ ] Upcoming dates widget (could also appear on butler overview)

---

### M9: Health Butler Views

Health tracking data visualization.

**API tasks:**
- [ ] Measurements: list with type filter, date range
- [ ] Medications: list with active filter, dose log per medication
- [ ] Conditions: list
- [ ] Symptoms: list with date range
- [ ] Meals: list with date range
- [ ] Research: list/search

**Frontend tasks:**
- [ ] Measurements dashboard:
  - Line charts (Recharts): weight over time, blood pressure, heart rate
  - Date range picker
  - Type selector tabs
  - Raw data table toggle
- [ ] Medications page:
  - Active medications cards (name, dosage, schedule)
  - Dose log table per medication
  - Adherence percentage indicator
- [ ] Conditions list: status badges (active/managed/resolved)
- [ ] Symptoms log: table with severity bar, optional trend chart
- [ ] Meals log: daily timeline view
- [ ] Research page: searchable list with topic tags

---

### M10: General Butler & Switchboard Views

Freeform entity browsing and routing visibility.

**API tasks:**
- [ ] Collections: list with entity counts
- [ ] Entities: list/search with collection filter, tag filter, full-text search on data
- [ ] Entity detail: full JSONB data
- [ ] `GET /api/butlers/switchboard/routing-log` — paginated routing log
- [ ] `GET /api/butlers/switchboard/registry` — butler registry snapshot

**Frontend tasks:**
- [ ] Collections page: cards with name, description, entity count, schema hint
- [ ] Entities page:
  - Table: title, collection badge, tags, created/updated
  - Search bar, collection filter, tag filter
  - Click → entity detail with collapsible JSON tree viewer
- [ ] JSON viewer component (syntax highlighted, collapsible, copy-to-clipboard)
- [ ] Routing log tab: table of routing decisions (timestamp, source channel, source ID, routed to, prompt summary)
- [ ] Registry tab: table of registered butlers (name, endpoint, modules, last seen)

---

### M11: Memory System — *Contingent on memory plan finalization*

> Blocked on the memory system being built (see `MEMORY_PROJECT_PLAN.md`). Implement this milestone once the `memories` table and MCP tools exist.

**API tasks:**
- [ ] `GET /api/butlers/:name/memory/stats` — tier counts, capacity usage, health indicators
- [ ] `GET /api/butlers/:name/memory/entries` — browse/search with tier, tag, date range filters
- [ ] `GET /api/butlers/:name/memory/entries/:id` — single memory detail
- [ ] `GET /api/butlers/:name/memory/activity` — recent promotions and evictions

**Frontend tasks:**
- [ ] **Memory tab** on butler detail page:
  - Tier overview cards (Eden / Mid-Term / Long-Term): entry count, capacity bar, oldest/newest
  - Promotion/eviction activity timeline
  - Memory browser: searchable/filterable table of entries (tier, content, tags, ref count, last referenced)
  - Click entry → full content + metadata view
  - Health indicators: eviction rate, saturation, promotion rate

---

### M12: Polish & Real-Time Foundation

UX polish and groundwork for real-time updates.

**Tasks:**
- [ ] Dark mode (Tailwind dark mode with toggle in header)
- [ ] Loading states (skeleton loaders for all data tables/charts)
- [ ] Empty states (meaningful messages when no data)
- [ ] Error boundaries + toast notifications for API errors
- [ ] Responsive design: sidebar collapse on small screens
- [ ] SSE endpoint for live butler status + active session updates
- [ ] Auto-refresh for timeline and session lists (polling → SSE migration path)
- [ ] Breadcrumb navigation
- [ ] Keyboard shortcuts (`/` or `Cmd+K` → search, `g o` → overview, `g t` → timeline)
- [ ] Audit log for dashboard-initiated write operations (triggers, schedule edits, state changes)

---

## docker-compose additions

```yaml
  dashboard-api:
    image: butlers:latest
    command: ["butlers", "dashboard", "--host", "0.0.0.0", "--port", "40200"]
    ports:
      - "40200:40200"
    environment:
      DATABASE_URL: postgres://butlers:butlers@postgres/butler_switchboard
      BUTLERS_DIR: /etc/butlers
    volumes:
      - ./butlers:/etc/butlers:ro
    depends_on:
      postgres:
        condition: service_healthy

  # Optional: frontend dev server (for development only)
  # In production, build frontend static files and serve via dashboard-api
  frontend:
    image: node:22-slim
    working_dir: /app
    command: ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
    ports:
      - "40173:40173"
    volumes:
      - ./frontend:/app
    environment:
      VITE_API_URL: http://localhost:40200
```

**Production deployment:** Build frontend with `vite build`, serve the `dist/` static files directly from the FastAPI app using `StaticFiles` mount. Single service, no separate frontend container.

---

## Open Questions

| Question                                         | Notes                                                                                                                                                                                                                                                                               |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Auth for the dashboard?                          | Not needed for v1 (personal system, localhost). Add basic auth or API key later.                                                                                                                                                                                                    |
| How to discover butler MCP endpoints at runtime? | Read butler.toml configs for ports, or use Switchboard's `list_butlers()`. Config-based is simpler and doesn't require a running Switchboard.                                                                                                                                       |
| Trace reconstruction fidelity?                   | Requires `trace_id` and `parent_session_id` on sessions table (added in M4/M5). Without these, traces are reconstructed heuristically from `trigger_source` timestamps — lossy.                                                                                                     |
| Frontend build integration?                      | `butlers dashboard` in production should serve the built frontend. Need a build step that copies `frontend/dist/` to a location the API can serve.                                                                                                                                  |
| How to handle butler DB schema differences?      | API router for each butler type (relationship, health, general) hardcodes the schema knowledge. If a new butler type is added, a new router is needed.                                                                                                                              |
| Real-time: WebSocket vs SSE?                     | SSE is simpler for server-push status updates. WebSocket if we want bidirectional (e.g., streaming runtime session output). Start with SSE.                                                                                                                                              |
| Token usage from Claude Agent SDK?                         | Need to verify the Claude Agent SDK exposes input/output token counts in its response. If not, we may need to instrument at the MCP tool level or parse usage from session transcripts.                                                                                              |
| Cost model pricing config?                       | Where does the per-model pricing live? Likely a simple YAML/TOML config file or env vars on the dashboard API. Needs to be updatable without redeployment.                                                                                                                          |
| Module health checks — how?                      | Modules don't currently expose a `health()` method. We'd need to add an optional `health_check()` to the Module ABC, or the dashboard API probes externally (e.g., Telegram bot API ping, IMAP connect).                                                                            |
| Global search performance?                       | Fan-out search across N butler DBs could be slow. Consider: background indexing, materialized views, or a lightweight search index (SQLite FTS) on the dashboard API side.                                                                                                          |
| Timeline event sourcing?                         | The unified timeline aggregates events from multiple tables across multiple DBs. Some events (state changes, heartbeat ticks) aren't currently logged anywhere. May need a lightweight `events` table in each butler DB, or the dashboard API polls and caches.                     |
| Active session detection?                        | How does the dashboard know a runtime instance is currently running? Options: (1) spawner writes a "running" row to sessions table before spawning, updates on completion; (2) MCP status() tool reports active sessions; (3) SSE push from butler daemon.                               |
| Memory system dependency?                        | M11 is blocked on the memory plan finalizing and the `memories` table + MCP tools being implemented. Track this dependency explicitly.                                                                                                                                              |
| Core `notify()` tool dependency?                 | M7 (Notifications) is blocked on the framework implementing the core `notify()` tool + Switchboard `deliver()` tool + `notifications` table. This is a framework-level change that should be added to `PROJECT_PLAN.md` as a new milestone or task under the Switchboard milestone. |
| Notification delivery guarantees?                | What happens if Switchboard is down when a butler calls `notify()`? Options: (1) fail and let runtime instance decide (store in state, retry later), (2) butler-local queue with retry, (3) accept message loss for v1. Recommend option 1 — simplest, Runtime instance can handle fallback.                    |
