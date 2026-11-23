## 1. Slice 1: roles, classifier, predicate, readers

- [x] 1.1 Migration `core_360` adds `calendar_sources.time_role` and `role_basis` and backfills.
- [x] 1.2 `butlers.core.temporal.calendar_source_roles`: classifier, role constants, predicate.
- [x] 1.3 Discovery registers each calendar with its classified role; rediscovery keeps `owner_confirmed`.
- [x] 1.4 Context producer, radar and day load, working location, meeting card, prep and debrief use the predicate.
- [x] 1.5 `calendar_find_free_slots` with no ids spans owner-time sources only.
- [x] 1.6 Sync and mutation paths register the role they can infer (primary alias, butler calendar).
- [x] 1.7 The radar reports how many unclassified calendars and events it skipped.

## 2. Deferred

- [x] 2.1 `POST /api/calendar/sources/role` and the source list's role fields (S2 backend).
- [x] 2.1b Role picker in the source settings UI (S2 frontend).
- [ ] 2.2 `role_subject_entity_id` and the `household_time` read, with context in the card, radar scan and free slots; prep and debrief only exclude (S3).
- [ ] 2.2a Household read opacity: no title, location or attendee field for any non-owner role.
- [ ] 2.3 Role on the connector mirror (S4).
