# Design — Calendar sources & sync-control

## Context

- `_sync_calendar` (`src/butlers/modules/calendar.py:6778`) loads the saved
  incremental `sync_token` and calls `provider.sync_incremental(...)`. A full
  re-sync (`sync_token=None`) happens **only** inside the
  `except CalendarSyncTokenExpiredError` branch (a Google `410 Gone`). There is
  no operator-initiated full re-sync.
- `calendar_force_sync(calendar_id=None)` (`:4374`) triggers `_sync_calendar`
  for one or all registered calendars but always uses the stored token.
- `_projection_freshness_metadata` (`:6663`) already derives a per-source
  `sync_state` ∈ {`fresh`,`stale`,`failed`} from `calendar_sync_cursors`
  (`last_synced_at`, `last_success_at`, `last_error_at`, `last_error`,
  `full_sync_required`). It does **not** classify the *kind* of error.
- `provider.list_calendars()` (`:2011`) returns raw Google `calendarList`
  entries (id, summary, primary, accessRole, …) but is not exposed as a tool.
- `calendar_sources` projection rows already exist per calendar; the workspace
  meta endpoint builds writable-calendar and freshness views from them.

## Decisions

### D1 — `full` recovery flag on the sync path, not a new tool

Add `full: bool = False` to `calendar_force_sync` and a `full` parameter to the
internal sync entry so that `full=true` forces `sync_token=None` (full re-sync
over `config.sync.full_sync_window_days`). Rationale: the recovery action is
"re-run sync ignoring the cursor", which is the existing 410 fallback path —
reusing it keeps one code path for full re-sync. A separate
`calendar_recover_sync` tool was rejected: it would inflate the tool count
(already contested by three in-flight changes) for what is one boolean.

Token-expiry recovery (whether triggered by a 410 or by `full=true`) MUST emit a
log line so operators can see a Recover happened (the bead requires "must log on
recover").

### D2 — `error_kind` taxonomy (coarse, UI-facing)

Add an `error_kind` field to each source in `_projection_freshness_metadata`
derived from the cursor's `last_error` / `full_sync_required` state. Values:

- `none` — healthy or no error recorded.
- `token_expired` — the incremental sync token expired (`full_sync_required` set,
  or the recorded error is a `CalendarSyncTokenExpiredError`/410). UI → **Recover**.
- `auth` — credential/authorization failure (`CalendarAuthError`, 401/403,
  refresh-token failure). UI → **Reconnect**.
- `not_found` — the calendar returned 404 (deregistered). UI → remove/Reconnect.
- `transient` — rate-limit / 5xx / network. UI → retry hint.

This is a classification of an already-recorded error string + flags; it adds no
new persistence. The dashboard chip maps `error_kind` → CTA (Recover vs
Reconnect vs retry).

### D3 — `calendar_list_calendars` returns a normalized, butler-aware view

Wrap `provider.list_calendars()` and project each entry to a stable shape:
`{calendar_id, summary, primary, access_role, is_butlers_calendar, selectable}`,
where `is_butlers_calendar` is true for `_resolved_calendar_id` (the dedicated
"Butlers" calendar) and `selectable` reflects an `accessRole` of `writer`/`owner`
(so the per-event selector cannot offer a read-only calendar). Returning the raw
Google payload was rejected — the dashboard would have to re-derive these flags
and the tool would leak provider-specific field names.

### D4 — Source enable/disable reuses `calendar_sources`, no new table

`POST /api/calendar/sources` toggles whether a calendar is polled by
`_sync_calendar`. Encode the enabled/disabled state on the existing
`calendar_sources` row (e.g. a metadata/`writable`-adjacent flag) rather than a
new table; a disabled source is skipped by the sync loop and surfaced in the
sources drawer as off. This keeps the "no new table" constraint from the bead and
reuses the dedup/fan-out logic already in `_fetch_sources`.

### D5 — Accounts read surface reuses existing health

`GET /api/calendar/accounts` joins `public.google_accounts` (the connected
accounts, granted scopes, last token refresh) with the Google Calendar
connector's per-account health (`MultiAccountHealthStatus` /
`AccountHealthStatus` in `src/butlers/connectors/google_calendar.py`), surfacing
`status` + `error_kind` per account. It is **read-only**; connect/disconnect stay
in the Google accounts surface.

## Risks / Trade-offs

- **Full re-sync cost.** A `full=true` recovery re-fetches the whole window and
  re-projects it. Mitigated: it is operator-initiated (a Recover click), scoped
  to the targeted calendar, and bounded by `full_sync_window_days`.
- **`error_kind` misclassification.** Coarse buckets derived from an error
  string can mislabel an edge case. Mitigated: `error_kind` only drives which CTA
  is *suggested*; the raw `last_error` remains available, and an unknown error
  defaults to `transient` (retry) rather than a destructive CTA.
- **Disabled source drift.** A disabled source stops syncing, so its projection
  goes stale by design; the chip shows it as off (not failed) so the staleness is
  not read as an error.

## Test Strategy

- Unit: `calendar_force_sync(full=true)` calls the sync path with
  `sync_token=None` and logs a recovery line; `full=false` uses the stored token.
- Unit: `_projection_freshness_metadata` emits `error_kind` for token-expired,
  auth, not-found, transient, and healthy sources.
- Unit: `calendar_list_calendars` flags the Butlers calendar and marks
  read-only calendars non-selectable.
- API: `POST /api/calendar/workspace/sync` with `full=true` forwards the flag and
  the response marks the target as full-recovered; `GET .../meta` includes
  `error_kind`; `GET /api/calendar/accounts` returns accounts + health;
  `POST /api/calendar/sources` toggles a source's enabled state.
