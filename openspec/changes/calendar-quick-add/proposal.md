## Why

Creating a calendar event in the workspace today means filling a structured form
or driving the `POST /api/calendar/workspace/user-events` create path with an
already-parsed `payload` (title, start_at, end_at, …). There is no
natural-language entry point: a user who types
`lunch with Sarah Fri 1pm at Tartine` has no way to turn that one string into a
draft event without hand-mapping every field.

The owner wants a quick-add bar that parses a free-text string into a draft
event **and shows it for confirmation before anything is written** — never an
auto-write. The calendar workspace is a single-owner surface where an accidental
LLM misparse must not silently land a real Google event. The confirm step must
reuse the existing, audited create path (`POST .../user-events` → the
`calendar_create_event` MCP tool) with its `request_id` idempotency, not a second
write path.

The LLM model surface already exists: `resolve_model(pool, butler_name,
complexity_tier)` (`src/butlers/core/model_routing.py`) resolves a model per
complexity tier and returns `None` when no enabled model qualifies in any tier.
A one-shot quick-add parse is the cheapest possible LLM call, so it routes to the
**simple ("cheap") tier**. Because `resolve_model` can return `None` (no model
configured/enabled), the endpoint needs an explicit degraded contract that
returns no fabricated event rather than guessing.

## What Changes

- **New parse-only endpoint.** Add `POST /api/calendar/workspace/parse-quick-add`
  to the calendar workspace router
  (`src/butlers/api/routers/calendar_workspace.py`). It accepts a free-text
  string (and optional display `timezone`), LLM-parses it into a **draft event**
  (proposed title, start_at, end_at, optional location/description), and returns
  a **parse-preview**. It performs **no provider or projection write** and
  creates no Google event.
- **Routes through the simple tier.** The parse uses `resolve_model(...,
  Complexity.CHEAP)` (the simple/cheap tier) — one cheap parse per submit.
- **Degraded path returns `parse_available=false`.** When `resolve_model`
  returns `None` (no enabled model in any tier) or the parse otherwise cannot be
  produced, the endpoint returns HTTP 200 with `parse_available=false`, a
  human-readable reason, and **no `draft` object**. It never fabricates an event
  or falls back to a heuristic guess.
- **Confirm reuses the existing create path.** Confirmation is NOT a new write
  endpoint: the frontend submits the (possibly edited) draft to the existing
  `POST /api/calendar/workspace/user-events` with `action="create"` and a
  `request_id`, exactly as the structured create form does today. The
  parse-quick-add response is advisory only.

## Capabilities

### New Capabilities

_None — this adds one read-only HTTP endpoint to the existing dashboard calendar
workspace API surface._

### Modified Capabilities

- `dashboard-api`: the Calendar Workspace HTTP surface gains a parse-only
  natural-language quick-add endpoint that produces a draft event for
  confirmation, with an explicit LLM-unavailable degraded response. Event
  creation continues to flow exclusively through the existing user-events create
  path.

## Impact

- **Calendar workspace router
  (`src/butlers/api/routers/calendar_workspace.py`):** new
  `POST /api/calendar/workspace/parse-quick-add` handler; new request/response
  Pydantic models in `src/butlers/api/models/calendar_workspace.py`.
- **Model routing (`src/butlers/core/model_routing.py`):** consumed read-only via
  `resolve_model(..., Complexity.CHEAP)`. No change to model routing itself.
- **No new MCP tool, no DB schema change, no migration.** Parse is read-only;
  confirm reuses `calendar_create_event` via the existing `/user-events` path.
- **Frontend:** a quick-add toolbar input that calls the new endpoint and renders
  the parse-preview chip; confirm dispatches the existing create call. (FE work
  is out of scope for the contract here.)

## Out of Scope

- Auto-writing the parsed event without an explicit confirm step (rejected: the
  endpoint is parse-only by contract).
- Contact resolution (turning "Sarah" into a linked contact/entity) — quick-add
  emits plain text; contact-linking lands separately (see epic `bu-l3k0zg`).
- Recurrence parsing ("every Friday") — v1 quick-add parses a single timed event;
  recurrence stays on the structured butler-event path.
- A new confirm/write endpoint — confirmation reuses
  `POST /api/calendar/workspace/user-events`.
