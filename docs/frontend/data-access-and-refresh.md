# Frontend Data Access and Refresh Contracts

## Data Access Model

The frontend talks to the dashboard API over REST through `frontend/src/api/client.ts`.

- Base URL:
  - `import.meta.env.VITE_API_URL` if set
  - otherwise `/api`
- All requests are JSON and typed.
- Non-2xx responses throw `ApiError` with:
  - `code`
  - `message`
  - `status`

## Query and Refresh Behavior

Default QueryClient behavior (`frontend/src/lib/query-client.ts`):

- `staleTime`: 30s
- `retry`: 1

Domain hook refetch intervals:

- 30s:
  - butlers
  - sessions
  - traces
  - timeline (default; can be overridden by page controls)
  - audit log
  - issues
  - general entities/collections/switchboard
  - health datasets
  - memory stats/facts/rules/episodes
  - butler schedules/state
- 60s:
  - cost summary
  - daily costs
  - top sessions (hook exists, not routed)
- 15s:
  - memory activity
- No automatic interval by default:
  - notifications and notification stats
  - contact/group data
  - butler config/skills
  - session/trace detail fetches

User-controlled live refresh exists on:

- Sessions page
- Timeline page

Control supports interval selection (`5s`, `10s`, `30s`, `60s`) and pause/resume.

## Write Operation Surfaces (Current)

The frontend currently performs writes only in these areas:

- Butler Trigger:
  - `POST /butlers/:name/trigger`
- Butler Schedules:
  - create (`POST`)
  - update (`PUT`)
  - delete (`DELETE`)
  - toggle enabled (`PATCH .../toggle`)
- Butler State:
  - set/overwrite (`PUT /state/:key`)
  - delete (`DELETE /state/:key`)

All other route surfaces are currently read-only.

## API Domain Coverage

- System core:
  - butlers, sessions, traces, timeline, audit-log, search
- Operations:
  - notifications (+ stats), costs, issues
- Butler control:
  - config, skills, schedules, state, trigger
- Relationship domain:
  - contacts, groups, labels, contact subresources, upcoming dates
- Health domain:
  - measurements, medications, medication doses, conditions, symptoms, meals, research
- General/Switchboard domain:
  - collections, entities, routing log, registry
- Memory domain:
  - stats, episodes, facts, rules, activity

## Target-State Extension: Approvals Domain

Not implemented in current frontend routes, but expected for single-pane approvals integration:

- Approvals domain:
  - pending/decided action lists
  - action detail
  - approve/reject/expire operations
  - standing rule CRUD and suggestion flows

Suggested refresh behavior for approvals surfaces:

- Pending actions queue: `15s-30s` interval, with manual pause/resume
- Rules and executed audit lists: `30s-60s` interval

## Error, Empty, and Loading Contracts

Across major surfaces, the UX contract is:

- Loading:
  - skeleton placeholders
- Empty:
  - explicit empty-state message with context
- Error:
  - explicit error text
  - in select cases (for example Butlers list), stale/cached data remains visible with warning
