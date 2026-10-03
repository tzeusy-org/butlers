## 1. Slice 1: column, write tool, spec

- [x] 1.1 Migration `core_256` adds `posture`, `posture_since`, `posture_set_by` and the writer-guard trigger.
- [x] 1.2 `entity_set_posture` in the Relationship `entity` tool group, idempotent, audited without the value.
- [x] 1.3 Spec deltas and the Relationship manifesto and prompt amended.

## 2. Slice 2: producers

- [x] 2.1 Briefing birthdays, interaction gaps and the Finance gift-ask count read `posture = 'active'`.
- [x] 2.2 Insight scan (all four categories) and `contacts_overdue` read `posture = 'active'`.
- [x] 2.3 Calendar overlay emits `remembrance` for memorial birthdays and nothing for quiet or no_contact.

## 3. Slice 3: notify egress

- [x] 3.1 `notify(entity_id=...)` refuses `recipient_posture` for memorial, no_contact and unreadable posture.

## 4. Slice 4: deferred

- [ ] 4.1 Finance loan-claim owner question for a memorial counterparty.
- [ ] 4.2 Anniversary-of-passing occasion.
- [ ] 4.3 Dashboard surface and remembrance opt-in.
