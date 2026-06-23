## 1. Calendar-id role separation (foundation)

- [ ] 1.1 Introduce a distinct default-target field separate from the immutable Butlers calendar id (`_resolved_calendar_id`); pick a distinct credential key for the user-chosen default target (do NOT reuse `GOOGLE_CALENDAR_ID`)
- [ ] 1.2 Update `calendar_set_primary` to set only the default-target field/cred key; assert `_resolved_calendar_id` is never mutated
- [ ] 1.3 Unit tests: `calendar_set_primary` leaves `_resolved_calendar_id` intact and persists the default target under the new key

## 2. Flip the create default to the Butlers calendar

- [x] 2.1 Change `_resolve_calendar_id(None)` no-override branch to return `_resolved_calendar_id` (Butlers calendar); keep the explicit-override path (validated against discovered calendars) unchanged
- [x] 2.2 Unit tests: no-override create resolves to the Butlers id; explicit `calendar_id` override (primary) resolves to primary; invalid override raises
- [x] 2.3 Integration test (fake provider): `calendar_create_event` with no `calendar_id` lands the event on the Butlers calendar id, branded `BUTLER:` + `butler_generated`

## 3. Home-calendar resolver for update/delete

- [x] 3.1 Implement a resolver keyed by provider event id: explicit override → projection lookup (`calendar_events.origin_ref` JOIN `calendar_sources.calendar_id`) → bounded search across Butlers+primary (+ other discovered) → primary fallback (fail-open not-found)
- [x] 3.2 Wire `calendar_update_event` to use the resolver instead of `_resolve_calendar_id(None)`
- [x] 3.3 Wire `calendar_delete_event` to use the resolver instead of `_resolve_calendar_id(None)`
- [x] 3.4 Unit tests: projection hit targets home calendar; explicit override wins; search fallback locates event; not-found surfaces fail-open (no raise)
- [x] 3.5 Integration test: butler event on Butlers calendar is updated/deleted on the Butlers calendar; a user-lane event on primary is patched/deleted in place on primary

## 4. Promote `create_user_event` to a branded butler-authored write

- [x] 4.1 Stamp `create_user_event` payloads with `_ensure_butler_title` + `_build_butler_private_metadata` and route via the new Butlers-calendar default
- [x] 4.2 Confirm the `calendar.write` permissions-matrix gate still runs before the provider write
- [x] 4.3 Unit test: `create_user_event` targets the Butlers calendar id and stamps butler metadata
- [x] 4.4 Verify health/meal-logging path (`roster/health/modules/__init__.py`) now lands meal events on the Butlers calendar (caller passes through unchanged to the now-branded `create_user_event`)

## 5. Spec + regression + docs

- [x] 5.1 Update existing `module-calendar` spec scenarios that assert "primary calendar is used as the default for tool mutations" to match the new behavior (this change's delta is the source of truth)
- [x] 5.2 Update/replace regression tests that assert the old primary-default create behavior (already reconciled by twb2f.2/.3/.4; verified no stale primary-default-create assertions remain)
- [x] 5.3 Run `openspec validate calendar-route-butler-events-to-dedicated-calendar --strict`
- [x] 5.4 Full quality gate: `ruff check`/`format --check` + targeted calendar test suite (570 passed) before merge
- [x] 5.5 Confirm go-forward-only: no migration that mutates live Google events; note in PR that pre-existing butler events on primary are intentionally left untouched
