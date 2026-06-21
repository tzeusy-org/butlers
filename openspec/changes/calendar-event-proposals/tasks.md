## 1. Owner sign-off (bu-w332j7)

- [ ] 1.1 Confirm the owner approves the design: a NEW `calendar_event_proposals` table, a `calendar_propose_event` producer, and the projection/accept/dismiss surface — explicitly NOT reusing `/api/approvals/suggestions` (`autonomy_suggestions`) nor `pending_actions`
- [ ] 1.2 Confirm the sequencing prerequisite: `calendar-route-butler-events-to-dedicated-calendar` lands first (accept routes through `calendar_create_butler_event` to the Butlers subcalendar)
- [ ] 1.3 Close `bu-w332j7` with approval (which unblocks `bu-fh8drm`) or rejection

## 2. Proposals store (migration)

- [ ] 2.1 Add a core Alembic migration (next in chain after `core_134`) creating `calendar_event_proposals` in each butler schema with the columns in design.md (event-shaped payload + `source_event_id`, `source_snippet`, `confidence`, `entity_ids`, `status`, `accepted_event_id`, timestamps)
- [ ] 2.2 Add the UNIQUE constraint on `(source_event_id)` for producer idempotency and a `status` index for the pending-projection query
- [ ] 2.3 Unit/migration test: table created per schema; upgrade/downgrade round-trips

## 3. `calendar_propose_event` producer

- [ ] 3.1 Implement `calendar_propose_event(butler_name, title, start_at, end_at, ...)` in `src/butlers/modules/calendar.py`: insert a `pending` row, perform NO provider write, return the proposal id
- [ ] 3.2 Make it idempotent on `source_event_id`: re-proposing the same originating ingestion event returns the existing proposal id (no duplicate row, no error)
- [ ] 3.3 Unit tests: insert creates a pending row; duplicate `source_event_id` is a no-op returning the existing id; no provider call is made

## 4. `proposals` workspace projection view

- [x] 4.1 Add `"proposed_event"` to the `UnifiedCalendarSourceType` literal in `src/butlers/api/models/calendar_workspace.py`
- [x] 4.2 Widen the `view` query pattern in `get_workspace` to accept `proposals` and project `calendar_event_proposals` rows with `status='pending'` into `UnifiedCalendarEntry` (tagged `source_type="proposed_event"`, `editable=false`, with `confidence`/`source_snippet`/provenance link in `metadata`)
- [x] 4.3 Make the read fail-open: an absent table or query failure returns an empty entries list, never HTTP 500
- [x] 4.4 Tests: `view=proposals` returns pending proposals only (not accepted/dismissed); fields/metadata populated; fail-open on missing table

## 5. Accept / dismiss endpoints

- [ ] 5.1 Implement `POST /api/calendar/workspace/proposals/{id}/accept`: read the stored payload (with optional inline overrides), call `calendar_create_butler_event` (routes to the Butlers subcalendar), set `status='accepted'` + `accepted_event_id`
- [ ] 5.2 Implement `POST /api/calendar/workspace/proposals/{id}/dismiss`: set `status='dismissed'` with no provider write
- [ ] 5.3 Idempotency: accepting an already-accepted proposal returns the existing `accepted_event_id` with no second provider write; dismissing an already-dismissed proposal is a no-op
- [ ] 5.4 Fail-closed accept: if `calendar_create_butler_event` fails, surface a structured error and leave the row `pending` (no partial `accepted` row without an `accepted_event_id`)
- [ ] 5.5 Audit-log both actions via `log_audit_entry`
- [ ] 5.6 Tests: accept creates an event on the Butlers subcalendar and flips status; dismiss flips status with no provider call; accept retry after a provider failure succeeds from `pending`

## 6. Ingestion producer wiring

- [ ] 6.1 Emit `calendar_propose_event` from the email/telegram/finance ingestion sessions when a calendar-relevant signal is extracted, passing `source_event_id` (the `public.ingestion_events.id`), `source_snippet`, `confidence`, and resolved `entity_ids`
- [ ] 6.2 Apply a confidence floor before proposing to limit lane noise
- [ ] 6.3 Integration test: an ingested signal that contains an event produces exactly one pending proposal linked to its ingestion event

## 7. Spec + validation

- [ ] 7.1 Author the `calendar-event-proposals` capability spec delta (this change's `specs/calendar-event-proposals/spec.md`)
- [ ] 7.2 Run `openspec validate calendar-event-proposals --strict` and fix until green
- [ ] 7.3 Quality gate: `ruff check`/`format --check` + targeted calendar/workspace test suite, then full `pytest` (excluding e2e) before merge
- [ ] 7.4 Confirm sequencing: `calendar-route-butler-events-to-dedicated-calendar` is merged before this change lands
