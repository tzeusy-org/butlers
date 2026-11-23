## 1. Slice 1: roles, classifier, predicate, readers

- [ ] 1.1 Migration `core_360` adds `calendar_sources.time_role` and `role_basis` and backfills.
- [ ] 1.2 `butlers.core.temporal.calendar_source_roles`: classifier, role constants, predicate.
- [ ] 1.3 Discovery registers each calendar with its classified role; rediscovery keeps `owner_confirmed`.
- [ ] 1.4 Context producer, radar and day load, working location, meeting card, prep and debrief use the predicate.
- [ ] 1.5 `calendar_find_free_slots` with no ids spans owner-time sources only.

## 2. Deferred

- [ ] 2.1 Owner role picker and `owner_confirmed` write (S2).
- [ ] 2.2 `role_subject_entity_id` and `household_time` read (S3).
- [ ] 2.3 Role on the connector mirror (S4).
