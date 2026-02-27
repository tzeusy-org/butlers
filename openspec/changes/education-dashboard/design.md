## Context

The education butler stores mind maps (concept DAGs), spaced repetition schedules, quiz responses, teaching flow states, and analytics snapshots in the `education` PostgreSQL schema. Seven read-only API endpoints already exist at `/api/education/*`. The dashboard frontend (React 18 + Vite + TanStack Query + shadcn/ui) has no education surface yet. XYFlow (v12.10) is already used for topology graphs, and Recharts (v3.7) for trend charts — both available without new dependencies.

The frontend follows established patterns: flat routes in `router.tsx`, butler-guarded sidebar items, `useQuery` hooks wrapping typed `apiFetch` calls, domain components in `src/components/<domain>/`, and page components in `src/pages/`.

## Goals / Non-Goals

**Goals:**
- Provide a read-heavy dashboard at `/education` showing curriculum progress, mind map topology, review schedule, quiz history, and mastery analytics
- Add 4 thin API endpoints wrapping existing backend tool functions (pending reviews, mastery summary, curriculum request, status change)
- Follow existing dashboard patterns (sidebar entry, hook layer, card-based layout) — no new architectural patterns
- Support the "curriculum request" write path as an async trigger the butler picks up on its next tick

**Non-Goals:**
- Real-time teaching interaction in the browser (teaching happens via Telegram/CLI; the dashboard is observational + management)
- Full curriculum editor (adding/removing/reordering individual nodes) — out of scope for v1
- Session transcript viewer (full LLM conversation logs are not linked to mind maps in the current schema)
- Mobile-optimized responsive design beyond what Tailwind provides by default

## Decisions

### 1. Page structure: single route with tab panels

**Decision:** One route `/education` with internal tab navigation (Curriculum | Reviews | Analytics), not separate routes.

**Rationale:** The health module uses a similar single-page-with-sections pattern. Education data is tightly coupled — switching between mind map view and analytics for the same curriculum should feel instant, not like a page navigation. Internal tabs via shadcn `<Tabs>` keep shared state (selected mind map) without URL coordination.

**Alternative considered:** Separate routes (`/education/curriculum/:id`, `/education/reviews`, `/education/analytics`). Rejected because most views need a "selected mind map" context that would require redundant URL params or a context provider.

### 2. Mind map visualization: XYFlow with Dagre layout

**Decision:** Render the concept DAG using `@xyflow/react` with `dagre` for automatic hierarchical layout (top-to-bottom). Nodes are color-coded by `mastery_status`:

| Status | Color | Hex |
|--------|-------|-----|
| mastered | emerald | `#10b981` |
| reviewing | blue | `#3b82f6` |
| learning | amber | `#f59e0b` |
| diagnosed | slate | `#64748b` |
| unseen | gray | `#d1d5db` |

Frontier nodes (from `/frontier` endpoint) get a pulsing ring indicator. Clicking a node opens a detail panel (quiz history, mastery score, next review date).

**Rationale:** The topology graph already uses XYFlow with a similar node-status-color pattern. Dagre handles DAGs naturally (prerequisite edges flow top-to-bottom). No new dependency needed — dagre is already bundled with the XYFlow setup in `TopologyGraph.tsx`.

**Alternative considered:** D3 force-directed layout. Rejected — force layouts don't respect DAG hierarchy, which is the core structure of a curriculum.

### 3. Spaced repetition view: timeline list, not calendar grid

**Decision:** Show pending and upcoming reviews as a grouped list (Today / This Week / Later), not a calendar widget. Each entry shows: node label, parent mind map title, `next_review_at`, mastery score badge.

**Rationale:** Reviews are sparse events (a few per day at most). A full calendar grid would be mostly empty and wasteful of screen space. A timeline list is information-dense and matches the dashboard's card-based design language. The grouped list also handles "overdue" reviews naturally (group them under "Overdue" above "Today").

**Alternative considered:** Full calendar grid (e.g., a month view). Rejected — too heavyweight for the data density. Could revisit if review volume grows significantly.

### 4. Curriculum request: KV-store trigger pattern

**Decision:** `POST /api/education/curriculum-requests` writes a JSON payload `{topic, goal}` to the butler's KV state store under key `pending_curriculum_request`. The education butler's tick handler checks for this key on each run and, if present, calls `teaching_flow_start()` and clears the key.

**Rationale:** This follows the butler trigger architecture — the dashboard never calls MCP tools directly. The KV store is the established inter-session communication channel. The butler's tick handler already checks for pending work; adding one more key check is trivial.

**Alternative considered:** Direct MCP call from the API layer. Rejected — the API server doesn't have MCP access to butler tools; it only has a database pool. The KV trigger pattern is the sanctioned way for external systems to request butler action.

### 5. API layer: thin wrappers, no new DB queries

**Decision:** All 4 new endpoints call existing tool functions:

| Endpoint | Wraps |
|----------|-------|
| `GET /mind-maps/{id}/pending-reviews` | `spaced_repetition_pending_reviews(pool, mind_map_id)` |
| `GET /mind-maps/{id}/mastery-summary` | `mastery_get_map_summary(pool, mind_map_id)` |
| `PUT /mind-maps/{id}/status` | `mind_map_update_status(pool, mind_map_id, status)` |
| `POST /curriculum-requests` | Direct KV store insert via `pool.execute()` |

**Rationale:** The tool functions already handle validation, error cases, and data shaping. Wrapping them avoids duplicating SQL and business logic. The curriculum request is the only endpoint that writes raw SQL (a single KV upsert), which is simple enough to inline.

### 6. Frontend data layer: one hook file, query key namespacing

**Decision:** All education hooks live in `src/hooks/use-education.ts`. Query keys follow `["education", <resource>, ...params]` pattern. Refetch interval: 30s for lists, 15s for the pending reviews count (shown as a badge).

**Rationale:** Matches the existing pattern (`use-memory.ts`, `use-health.ts`). The 15s interval for pending reviews ensures the "due now" badge stays reasonably current without excessive polling.

## Risks / Trade-offs

**[KV trigger is eventually consistent]** → The curriculum request won't be picked up until the butler's next tick (could be up to 5 minutes depending on schedule). Mitigation: show a "Requested — pending butler processing" state in the UI. The butler clears the key and creates the mind map, which the dashboard picks up on its next refetch.

**[Dagre layout can be slow for large graphs]** → Curriculum DAGs are capped at 30 nodes (enforced by `curriculum_generate`). Mitigation: 30 nodes is well within dagre's instant-layout range. No virtualization needed.

**[No optimistic updates for status changes]** → `PUT /mind-maps/{id}/status` invalidates the mind map query cache but doesn't do optimistic UI updates. Mitigation: acceptable for v1 — status changes are infrequent (abandon/reactivate). The 30s refetch interval ensures consistency.

**[Quiz history can grow large]** → The quiz responses endpoint is already paginated (limit 200). Mitigation: default to 20 per page in the UI, matching the API default. Infinite scroll or "load more" button for viewing deeper history.
