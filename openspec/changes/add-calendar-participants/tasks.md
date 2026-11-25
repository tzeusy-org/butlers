## 1. Spec and participant record (slice S1)

- [x] 1.1 OpenSpec change `add-calendar-participants` validates.
- [x] 1.2 Migration: `calendar_event_participants` per schema and the `attendee_identity_claims` definer.
- [x] 1.3 Pure resolver and participant derivation; no free text, no fuzzy matching.
- [x] 1.4 Projection writes participants after the event upsert; a resolver failure keeps prior rows.
- [x] 1.5 Quarantined invitations mint no participants; entity rebind repoints them.
- [x] 1.6 Bounded, backfill over existing provider events.
- [x] 1.7 docs/modules/calendar.md.

## 2. Readers (slice S2)

- [ ] 2.1 Prep and workspace people read participants (`exact`, `manual`, not `resource`).

## 3. Debrief and interaction sync (slice S3)

- [ ] 3.1 Both drop their private resolvers and read participants.

## 4. Re-resolution (slice S4)

- [x] 4.1 Periodic re-resolution sweep after a successful provider sync (REQ-module-calendar-050); rebind already repoints (REQ-module-calendar-045), so no event hook and no resolver_version bump.

## 5. Chronicler (slice S5)

- [ ] 5.1 The Chronicler adapter reads participants.
