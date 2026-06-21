## 1. `calendar_list_calendars` MCP tool (bu-6cf3ri)

- [ ] 1.1 Add a `calendar_list_calendars` MCP tool wrapping the existing `provider.list_calendars()` (`src/butlers/modules/calendar.py:2011`)
- [ ] 1.2 Normalize each entry to `{calendar_id, summary, primary, access_role, is_butlers_calendar, selectable}`; flag `_resolved_calendar_id` as the Butlers calendar and mark non-writer/owner calendars as not selectable
- [ ] 1.3 Fail-open: provider errors return an empty list with error metadata rather than raising
- [ ] 1.4 Do NOT re-pin the normative "16 MCP tools total" count in the `Calendar Event CRUD Tools` requirement (count re-pin is owned by `calendar-recurrence-scope-editing`); this change only ADDS one tool
- [ ] 1.5 Unit tests: Butlers calendar flagged; read-only calendar non-selectable; provider failure fails open

## 2. Cursor recovery: `full` flag on force-sync (bu-wwftzj)

- [x] 2.1 Add `full: bool = False` to `calendar_force_sync` and thread a `full` parameter into the internal sync entry so `full=true` forces `sync_token=None` (full re-sync over `config.sync.full_sync_window_days`)
- [x] 2.2 Log a recovery line whenever a token-expiry/full re-sync runs (410 fallback OR `full=true`) — "must log on recover"
- [x] 2.3 Keep `full=false` behavior identical to today's incremental sync
- [x] 2.4 Unit tests: `full=true` syncs with `sync_token=None` and logs recovery; `full=false` uses the stored token

## 3. Per-source `error_kind` in freshness (bu-wwftzj)

- [x] 3.1 Classify each source's error in `_projection_freshness_metadata` into `error_kind` ∈ {`none`, `token_expired`, `auth`, `not_found`, `transient`} from the cursor's `last_error`/`full_sync_required`
- [x] 3.2 Surface `error_kind` in the `calendar_sync_status` MCP tool payload
- [x] 3.3 Unit tests: token-expired, auth, not-found, transient, and healthy sources map to the expected `error_kind`

## 4. Workspace sync endpoint recovery + error_kind (bu-wwftzj)

- [x] 4.1 Add `full: bool` to `CalendarWorkspaceSyncRequest` and forward it to `calendar_force_sync(full=...)` for the targeted source(s)
- [x] 4.2 Report per-target whether a full recovery ran in `CalendarWorkspaceSyncTarget`
- [x] 4.3 Add `error_kind` to `CalendarWorkspaceSourceFreshness` and populate it in `GET /api/calendar/workspace/meta`
- [x] 4.4 API tests: `POST .../workspace/sync` with `full=true` forwards the flag and marks the target full-recovered; `GET .../meta` returns `error_kind`

## 5. Accounts control plane (bu-6cf3ri)

- [ ] 5.1 Add `GET /api/calendar/accounts` returning `public.google_accounts` joined with the Google Calendar connector per-account health (status, error_kind, last ingest)
- [ ] 5.2 Add request/response models in `src/butlers/api/models/calendar_workspace.py`
- [ ] 5.3 Read-only: this endpoint does NOT connect/disconnect accounts (that stays in the Google accounts surface)
- [ ] 5.4 API tests: returns connected accounts with health; degrades gracefully when connector health is unavailable

## 6. Per-calendar source enable/disable (bu-6cf3ri)

- [ ] 6.1 Add `POST /api/calendar/sources` to enable/disable a single calendar as a sync source by toggling state on the existing `calendar_sources` row (NO new table)
- [ ] 6.2 A disabled source is skipped by the sync loop and rendered as off (not failed) in the sources drawer
- [ ] 6.3 API tests: toggling a source updates its enabled state; a disabled source is skipped on the next sync

## 7. Spec + quality gate

- [ ] 7.1 Apply the `module-calendar` delta (modified `Calendar Event CRUD Tools` + `Calendar Sync Tools`; new `Calendar Source Listing Tool`)
- [ ] 7.2 Apply the `dashboard-api` delta (modified `Calendar Workspace` for new endpoints/fields)
- [ ] 7.3 Run `openspec validate calendar-sources-and-sync-control --strict`
- [ ] 7.4 Quality gate: `ruff check`/`format --check` + targeted calendar test suite, then full `pytest` (excluding e2e) before merge
