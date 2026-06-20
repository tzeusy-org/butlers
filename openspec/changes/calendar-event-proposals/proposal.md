## Why

Butlers' core promise is to "act autonomously on incoming messages while keeping
the human in the write loop." Today a butler that infers a calendar-worthy event
from an incoming signal (an email confirming a flight, a Telegram message
agreeing on a dinner, a finance charge for a subscription renewal) has only two
options: silently write a real event via `calendar_create_event` (violating the
human-in-the-write-loop rule), or do nothing. There is no surface for "I think
this is an event — confirm it and I'll add it."

The existing approval surfaces are the wrong shape for this:

- **`/api/approvals/suggestions` (`autonomy_suggestions`)** promotes a recurring
  tool-call pattern into an **auto-approve RULE** — its scope strings read
  `"Auto-approve send_telegram when chat_id = 'mom_123'"` (verified
  `approvals.py:1682`, `_generate_scope_description`). It is a policy store, not
  an event store; accepting a suggestion changes future autonomy, it does not
  create one event.
- **`pending_actions`** gates a *specific already-decided butler tool call*
  awaiting human approval before the butler completes it. A proposal is not a
  blocked butler action — it is a butler-authored *recommendation* with
  provenance and a confidence score that the user may edit before it becomes an
  action at all.

The calendar workspace already renders a `user` lane and a `butler` lane
(`UnifiedCalendarSourceType` = `provider_event | scheduled_task |
butler_reminder | manual_butler_event`). A third, **proposals** lane completes
the agentic loop: butler-inferred events render as dashed ghost blocks with a
provenance link and a confidence chip, and the user accepts, edits, or dismisses
them inline. Accepted proposals route through `calendar_create_butler_event` onto
the dedicated **Butlers** subcalendar — never silently onto the user's real
Google Calendar.

## What Changes

- **New `calendar_event_proposals` table.** A new per-schema projection table
  holds butler-inferred event proposals: an event-shaped payload (title,
  start_at, end_at, timezone, body, location), plus proposal-specific provenance
  (`source_event_id` linking to the originating `public.ingestion_events` row,
  `source_snippet` the human-readable excerpt that triggered the inference,
  `confidence` 0.0-1.0, `entity_ids` the resolved participants) and a lifecycle
  `status` (`pending | accepted | dismissed`). It deliberately does **not** reuse
  `autonomy_suggestions` (auto-approve RULE promotion) nor `pending_actions`
  (gating a decided butler tool call).

- **New `calendar_propose_event` producer.** A programmatic, idempotent
  entry-point that ingestion handlers call when they extract a calendar-relevant
  signal during their existing session. It inserts a `pending` row; it never
  writes to the provider. Re-proposing the same `source_event_id` is a no-op
  (idempotent on the originating ingestion event).

- **New `proposals` workspace view.** `GET /api/calendar/workspace?view=proposals`
  projects `calendar_event_proposals` rows with `status='pending'` into the
  unified entry shape, tagged `source_type="proposed_event"` — a new value added
  to the `UnifiedCalendarSourceType` literal. Proposal entries are non-editable
  in place (`editable=false`) and carry `confidence`, `source_snippet`, and the
  provenance link in `metadata`.

- **New accept / dismiss endpoints.** `POST
  /api/calendar/workspace/proposals/{id}/accept` routes the proposal's payload
  through `calendar_create_butler_event` to the Butlers subcalendar and flips the
  row to `accepted`; `POST /api/calendar/workspace/proposals/{id}/dismiss` flips
  the row to `dismissed` without any provider write. Both are idempotent on the
  current status.

## Capabilities

### New Capabilities

- `calendar-event-proposals`: the proposals store, the `calendar_propose_event`
  producer, the `proposals` workspace projection view, and the accept/dismiss
  endpoints.

### Modified Capabilities

_None in this change. The `proposals` view adds a new `source_type` value but
the `module-calendar` provider-routing contract is unchanged; accept reuses the
existing `calendar_create_butler_event` tool and the Butlers-subcalendar routing
landed by `calendar-route-butler-events-to-dedicated-calendar`._

## Impact

- **New migration (`alembic/versions/core/`):** create `calendar_event_proposals`
  in each butler schema (latest core revision is `core_134`; this adds the next
  in chain).
- **Calendar module (`src/butlers/modules/calendar.py`):** add the
  `calendar_propose_event` producer and an accept helper that calls
  `calendar_create_butler_event`.
- **Workspace API (`src/butlers/api/routers/calendar_workspace.py`,
  `src/butlers/api/models/calendar_workspace.py`):** widen the
  `view` query pattern to accept `proposals`; add `"proposed_event"` to
  `UnifiedCalendarSourceType`; add the accept/dismiss routes.
- **Ingestion handlers (email / telegram / finance sessions):** emit
  `calendar_propose_event` when a calendar-relevant signal is extracted (low
  incremental LLM cost — reuses output already produced during ingestion).
- **No change to the user's real Google Calendar.** Proposals never write to the
  provider until accepted, and acceptance routes to the Butlers subcalendar.

## Sequencing

This change **depends on**
`calendar-route-butler-events-to-dedicated-calendar` landing first: accept routes
through `calendar_create_butler_event` to the dedicated Butlers subcalendar, and
that routing/calendar-id-role separation is the prerequisite for "accepted
proposals never land on the user's primary calendar."

## Out of Scope

- The frontend ghost-block / confidence-chip rendering (separate FE bead under
  epic `bu-fh8drm`; this change defines the contract it consumes).
- An auto-accept policy ("always accept flight confirmations") — that is an
  `autonomy_suggestions` concern and is explicitly NOT folded in here.
- Editing a proposal's payload before accept as a distinct persisted draft state
  — v1 accept takes the stored payload (optionally overridden inline by the
  accept request body); there is no separate `edited` status.
- Expiry / garbage collection of stale `pending` proposals (deferred follow-up).
