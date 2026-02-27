## Why

The education butler has a rich data model (mind maps, spaced repetition, mastery tracking, quiz history, analytics) and a complete set of read-only API endpoints, but no frontend surface. Users must interact with the butler exclusively through Telegram/CLI with no way to visualize learning progress, inspect upcoming reviews, explore curriculum structure, or manage curriculums. A dedicated dashboard tab at `/education` turns this data into an actionable learning cockpit.

## What Changes

- **New dashboard tab** at `/education` with sidebar entry (guarded by `butler: 'education'`)
- **Mind map graph visualization** using XYFlow — renders the concept DAG with nodes color-coded by mastery status, click-to-inspect node details
- **Spaced repetition schedule view** — calendar/timeline of upcoming reviews across all active mind maps, with due-now highlights
- **Curriculum management panel** — list active/completed/abandoned curriculums, request new curriculum (creates a trigger for the butler), abandon or re-activate existing ones
- **Quiz interaction history** — paginated timeline of quiz Q&A per curriculum/node, with quality scores and response types
- **Mastery analytics dashboard** — mastery trend charts (Recharts), cross-topic portfolio view, per-node progress table, struggling nodes callout
- **New API endpoints** to fill gaps:
  - `GET /api/education/mind-maps/{id}/pending-reviews` — nodes due for spaced repetition review
  - `POST /api/education/curriculum-requests` — submit a new curriculum request (topic + optional goal) that the butler picks up on its next tick
  - `PUT /api/education/mind-maps/{id}/status` — change mind map status (abandon, re-activate)
  - `GET /api/education/mind-maps/{id}/mastery-summary` — aggregate mastery stats (existing tool `mastery_get_map_summary`, not yet exposed via API)

## Capabilities

### New Capabilities
- `education-dashboard-ui`: Frontend React components for the education tab — mind map graph, spaced repetition calendar, curriculum list, quiz history, analytics charts, and page routing
- `education-api-write`: New write/mutating API endpoints for curriculum request submission, mind map status changes, and pending review queries

### Modified Capabilities
_(none — existing API endpoints remain unchanged)_

## Impact

- **Frontend:** New route, sidebar entry, page component, ~8 new React components under `src/components/education/`, new hooks in `src/hooks/use-education.ts`, new types in `src/api/types.ts`, new client methods in `src/api/client.ts`
- **Backend API:** 4 new endpoints in `roster/education/api/router.py` + new Pydantic models in `models.py`
- **Backend tools:** No changes — new endpoints wrap existing tool functions (`spaced_repetition_pending_reviews`, `mastery_get_map_summary`, `mind_map_update_status`)
- **Database:** No schema changes — all data already exists in the education schema
- **Dependencies:** No new npm or Python packages — XYFlow and Recharts already installed
