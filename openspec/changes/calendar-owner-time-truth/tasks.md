## 0. Spec (this change)

- [ ] 0.1 Review and merge this change before runtime code.

## 1. Changed since you said yes

- [ ] 1.1 A-S1 `calendar_owner_agreements` migration and writer at the RSVP-observation seam.
- [ ] 1.2 A-S3 `public.resolve_calendar_occurrence` definer, grants manifest, probe registration.
- [ ] 1.3 A-S2 `calendar_agreement_drift` job reconciling `owner_conditions`; connectors-schema read grant.
- [ ] 1.4 A-S4 candidate proposer, lane by distance to start, shared daily cap.
- [ ] 1.5 A-S5 dashboard chip from `owner_conditions`.

## 2. Reminders that follow their event

- [ ] 2.1 R-S1 anchor fields on `reminder_create`, validation, derived `due_at`.
- [ ] 2.2 R-S2 rebase and retire at the projection upsert and cancel paths, with receipts.
- [ ] 2.3 R-S3 fire-time anchor check in the tick.
- [ ] 2.4 R-S4 owner-drag adoption: parked on the owner decision, not built.

## 3. The owner's gesture is an instruction

- [x] 3.1 Push-loop honesty: already on main (calendar-mirror-truth).
- [ ] 3.2 Revert with a receipt row and an owner-visible notice when a butler-owned event is edited on Google.
- [ ] 3.3 Adoption, undo, If-Match and the recurring question: parked on the owner decision, not built.
