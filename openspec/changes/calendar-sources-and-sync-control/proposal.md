## Why

Two gaps make the calendar workspace unsafe to operate once a calendar drifts or
once the owner connects more than one Google account:

1. **No cursor recovery.** `_sync_calendar` only performs a full re-sync when
   Google returns `410 Gone` (the incremental sync token expired). There is no
   operator-driven "recover" path: `calendar_force_sync` and
   `POST /api/calendar/workspace/sync` always run an *incremental* sync from the
   stored token. When a projection silently diverges, or after a token-expiry
   that already burned its 410, the only freshness signals are `sync_state`
   (`fresh`/`stale`/`syncing`/`failed`) and `last_error` — there is no
   `error_kind` to tell the UI whether a stale source needs a **Recover**
   (force full sync) or a **Reconnect** (re-authorize the account).

2. **No multi-account / per-calendar control plane.** `provider.list_calendars()`
   already enumerates every calendar on the authenticated account
   (`src/butlers/modules/calendar.py:2011`) but it is **not exposed** as an MCP
   tool, so the dashboard cannot offer a per-event calendar selector or a sources
   drawer. There is also no read surface for the connected `public.google_accounts`
   plus connector health, and no way to enable/disable an individual calendar as a
   sync source without editing `butler.toml` and restarting the daemon.

The owner wants: real per-source sync health with one-click recovery and a
Reconnect CTA on token expiry; an accounts/sources control plane; and a
per-event calendar override so a user with personal + work accounts can choose
where an event lands. `calendar_create_event` already accepts an explicit
`calendar_id`, so the per-event override is already wired at the tool layer —
this change exposes the calendar list that populates the selector.

## What Changes

- **New `calendar_list_calendars` MCP tool.** Wraps the existing
  `provider.list_calendars()` and returns each calendar's id, summary/display
  name, primary flag, access role, and whether it is the dedicated "Butlers"
  calendar. This is the source for the dashboard's per-event calendar selector
  and the sources drawer. (Tool surface impact: this change ADDS exactly one
  tool. The normative "16 MCP tools total" count pin in the
  `Calendar Event CRUD Tools` requirement is **not** re-pinned here to avoid a
  conflicting number — the in-flight `calendar-recurrence-scope-editing` change
  owns the re-pin of that count, and `calendar-availability-find-time` also
  adds one tool; the final total is reconciled when those changes apply.)
- **`calendar_force_sync` gains a `full` flag for cursor recovery.** When
  `full=true`, the sync runs against `sync_token=None` (a full re-sync over the
  configured window) instead of the stored incremental token, and the recovery
  is logged. This is the **Recover** action. The default (`full=false`) preserves
  today's incremental behavior.
- **Per-source `error_kind` in freshness.** `_projection_freshness_metadata`
  (and therefore `calendar_sync_status`) classifies a failed/stale source's
  error into a coarse `error_kind` (e.g. `token_expired`, `auth`, `not_found`,
  `transient`, `none`) so the UI can show **Reconnect** vs **Recover** vs a
  retry hint. Token-expiry recovery MUST be logged when it runs.
- **`POST /api/calendar/workspace/sync` accepts `full`.** The flag is forwarded
  to `calendar_force_sync(full=...)` for the targeted source(s); the response
  reports per-target whether a full recovery ran.
- **`GET /api/calendar/workspace/meta` carries `error_kind`.** The existing
  per-source freshness objects gain `error_kind` so the workspace can render the
  fresh/stale/syncing/failed chip with the right recovery CTA.
- **`GET /api/calendar/accounts` (new).** Returns the connected
  `public.google_accounts` rows joined with the Google Calendar connector's
  per-account health (status, error_kind, last ingest), backing a Sources drawer
  of account cards.
- **`POST /api/calendar/sources` (new).** Enables or disables a single calendar
  as a sync source (toggling whether `_sync_calendar` polls it) without editing
  `butler.toml` or restarting — backed by the existing `calendar_sources`
  projection rows; no new table.

## Capabilities

### New Capabilities

_None — this extends existing calendar MCP tools and dashboard endpoints._

### Modified Capabilities

- `module-calendar`: adds the `calendar_list_calendars` MCP tool; extends
  `calendar_force_sync` with a `full` recovery flag (token-expiry recovery is
  logged); adds per-source `error_kind` classification to sync-status freshness.
- `dashboard-api`: adds `GET /api/calendar/accounts` and
  `POST /api/calendar/sources`; extends `POST /api/calendar/workspace/sync` with
  a `full` flag and `GET /api/calendar/workspace/meta` with per-source
  `error_kind`.

## Impact

- **Calendar module (`src/butlers/modules/calendar.py`):**
  - New `calendar_list_calendars` tool wrapping `provider.list_calendars()`
    (`:2011`).
  - `calendar_force_sync(full: bool = False)` and a `full` path into
    `_sync_calendar` that forces `sync_token=None`; recovery logged.
  - `_projection_freshness_metadata` emits `error_kind` per source.
- **Dashboard API (`src/butlers/api/routers/calendar_workspace.py`):**
  - `CalendarWorkspaceSyncRequest` gains `full: bool`; forwarded to the MCP tool.
  - `CalendarWorkspaceSourceFreshness` gains `error_kind`.
  - New `GET /api/calendar/accounts` and `POST /api/calendar/sources` routes
    (plus request/response models in
    `src/butlers/api/models/calendar_workspace.py`).
- **Spec (`openspec/specs/module-calendar/spec.md`):** the
  `Calendar Event CRUD Tools` and `Calendar Sync Tools` requirements are
  modified; a new `Calendar Source Listing Tool` requirement is added.
- **Spec (`openspec/specs/dashboard-api/spec.md`):** the `Calendar Workspace`
  requirement is modified for the new endpoints and fields.
- **No DB schema change, no migration, no LLM call.** Source enable/disable
  reuses existing `calendar_sources` rows; account health reuses the existing
  connector health surface and `public.google_accounts`.

## Out of Scope

- Adding/removing Google **accounts** (OAuth connect/disconnect) — that flow
  lives in the Google accounts surface (`/api/connectors/google-health`,
  `dashboard-google-accounts`); this change only reads account state.
- Changing the default write-target routing (butler-authored events →
  dedicated "Butlers" calendar) — owned by the in-flight
  `calendar-route-butler-events-to-dedicated-calendar` change.
- Re-pinning the normative MCP tool count — owned by
  `calendar-recurrence-scope-editing` (16 → 18) and
  `calendar-availability-find-time` (16 → 17).
- A new sources DB table — source enable/disable reuses `calendar_sources`.
