# Design — Calendar event proposals lane

## Context

The calendar workspace (`src/butlers/api/routers/calendar_workspace.py`) serves
two lanes via `GET /api/calendar/workspace?view={user|butler}`. Each row is
normalized into a `UnifiedCalendarEntry` whose `source_type` is one of
`provider_event | scheduled_task | butler_reminder | manual_butler_event`
(`src/butlers/api/models/calendar_workspace.py`).

Ingestion handlers (email, telegram, finance) already run an ephemeral LLM
session per incoming signal and frequently extract calendar-relevant facts
(a flight time, a dinner agreement, a renewal date) as a byproduct. There is no
place to *stage* that inference for human confirmation. Writing it directly to
the calendar would break Non-Negotiable Rule "human kept in the write loop" for
inferred (not user-requested) events.

The sibling change `calendar-route-butler-events-to-dedicated-calendar` makes
`calendar_create_butler_event` route butler-authored events to the dedicated
**Butlers** subcalendar. That is the landing zone for accepted proposals, which
is why this change is sequenced after it.

## Decisions

### D1 — A new `calendar_event_proposals` table, NOT a reuse of existing stores

A proposal is a **butler-authored event recommendation with provenance and a
confidence score, pending human confirmation**. None of the existing stores model
that shape:

- **`autonomy_suggestions`** (`approvals.py:1682`) is a *rule-promotion* store.
  `_generate_scope_description` produces strings like `"Auto-approve
  send_telegram when chat_id = 'mom_123'"`. Accepting a suggestion changes
  **future autonomy policy**; it does not materialize one event. It has no
  event-shaped payload, no start/end, no per-event provenance snippet, and its
  acceptance semantics (widen the auto-approve allowlist) are the opposite of
  what we want (create exactly one event). Folding proposals in here would
  conflate "approve this one inferred event" with "always auto-approve this kind
  of action" — a doctrine error.

- **`pending_actions`** gates *a specific, already-decided butler tool call*
  awaiting approval before the butler finishes it. A proposal is not a blocked
  action: it is a recommendation the user may **edit** before it becomes an
  action, and dismissing it is a first-class outcome, not a denial of an
  in-flight tool call. Reusing `pending_actions` would force a fake "pending tool
  call" for something the butler has deliberately chosen NOT to execute yet.

A dedicated table keeps the three concerns orthogonal: policy (`autonomy_
suggestions`), in-flight action gating (`pending_actions`), and inferred-event
staging (`calendar_event_proposals`).

### D2 — Table shape

`calendar_event_proposals` (per butler schema):

| column | type | purpose |
|---|---|---|
| `id` | UUID PK | proposal id (path param for accept/dismiss) |
| `butler_name` | TEXT NOT NULL | owning butler |
| `title` | TEXT NOT NULL | event-shaped payload |
| `start_at` | TIMESTAMPTZ NOT NULL | event-shaped payload |
| `end_at` | TIMESTAMPTZ | event-shaped payload (nullable; defaults to start+15m on accept) |
| `timezone` | TEXT | event-shaped payload |
| `body` | TEXT | event-shaped payload (long description) |
| `location` | TEXT | event-shaped payload |
| `source_event_id` | UUID | FK-style link to `public.ingestion_events.id` (the originating signal) |
| `source_snippet` | TEXT | human-readable excerpt that triggered the inference (provenance) |
| `confidence` | DOUBLE PRECISION | 0.0-1.0 model confidence |
| `entity_ids` | UUID[] | resolved participant entities |
| `status` | TEXT NOT NULL DEFAULT 'pending' | `pending | accepted | dismissed` |
| `accepted_event_id` | UUID | the `calendar_events` id created on accept (nullable) |
| `created_at` / `updated_at` | TIMESTAMPTZ | bookkeeping |

A UNIQUE constraint on `(source_event_id)` (where `source_event_id IS NOT NULL`)
enforces producer idempotency: one proposal per originating ingestion event.

### D3 — `calendar_propose_event` is a producer, never a provider write

The producer inserts a `pending` row and returns its id. It performs **no**
Google Calendar write. Re-calling with the same `source_event_id` returns the
existing proposal (idempotent). This keeps the human strictly in the write loop:
nothing reaches the user's calendar from inference alone.

### D4 — `proposals` view reuses the unified entry shape

`GET /api/calendar/workspace?view=proposals` returns `UnifiedCalendarEntry`
rows tagged `source_type="proposed_event"` (a new value added to the
`UnifiedCalendarSourceType` literal). Proposals are non-editable in place
(`editable=false`) — they are accepted/edited/dismissed via the dedicated
endpoints, not the user/butler mutate endpoints. `confidence`, `source_snippet`,
and the `source_event_id` provenance link travel in `metadata` so the FE can
render the confidence chip and "why" link without a new top-level field per
concern.

### D5 — Accept routes through `calendar_create_butler_event`

`POST /proposals/{id}/accept` reads the stored payload (with optional inline
overrides from the request body), calls `calendar_create_butler_event` (which —
post `calendar-route-butler-events-to-dedicated-calendar` — lands on the Butlers
subcalendar), stamps `status='accepted'` and `accepted_event_id`, and is
idempotent: accepting an already-accepted proposal returns the existing
`accepted_event_id` without a second provider write. Dismiss flips to
`dismissed` with no provider call and is likewise idempotent.

## Degraded-mode behavior

The `proposals` view is a pure DB projection of `calendar_event_proposals` and
does **not** call Prometheus, so it is not in the aggregate-metrics
degraded-envelope family (`aggregates_available`, per project API conventions).
Its degraded mode is the **butler/provider being unreachable on accept**:

- **Read (`view=proposals`) is fail-open.** If the projection query fails or the
  table is absent (calendar module disabled / pre-migration), the view returns an
  empty entries list rather than HTTP 500 — consistent with the workspace read
  path's existing fail-open posture.
- **Accept is fail-closed and non-destructive.** If the underlying
  `calendar_create_butler_event` MCP call fails (butler unreachable, provider
  error), the endpoint surfaces a structured error and the proposal row stays
  `pending` — it is NOT flipped to `accepted`, so the user can retry. No partial
  state (an `accepted` row without an `accepted_event_id`) is ever persisted.

## Risks / Trade-offs

- **Proposal spam.** A noisy ingestion handler could flood the lane. Mitigated by
  producer idempotency (one per `source_event_id`) and a confidence floor the
  handler applies before proposing; lane GC is a deferred follow-up.
- **Stale payload at accept time.** A proposal accepted days later may have drifted
  (e.g. the meeting moved). Mitigated by inline overrides on the accept request
  and by the `source_snippet`/provenance link letting the user verify before
  accepting.
- **New `source_type` value.** Adding `"proposed_event"` to the literal touches
  the workspace model and any exhaustive `source_type` switch; mitigated by it
  only being emitted on the `proposals` view, never the `user`/`butler` views.
