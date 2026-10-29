## 1. Spec Draft

- [x] Read current sync, interaction_log, store_fact, owning capability and RFC 0013 D4.
- [x] Propose one source-aware receipt-time identity with all five acceptance criteria and the day-boundary scenario.
- [x] Retain existing message scenario headings and identify the explicit RFC D4 amendment.
- [ ] Run named strict validation and applicable source hygiene/pre-push checks.

## 2. Review and Adoption Gate

- [ ] Obtain independent review of the exact head and resolve its findings.
- [ ] Record the owner's explicit adoption answer naming that reviewed head before merge or implementation.
- [ ] After adoption, apply the capability delta and the RFC D4 amendment together, through the reviewed workflow.

## 3. Implementation After Adoption Only

- [ ] Design the minimal trusted sync writer/key/cutover change, preserving calendar/manual behavior and all existing authorization boundaries.
- [ ] Prove real timestamp anchors, channels/directions/chats, batch counts, replay/concurrent uniqueness, collision refusal and day-boundary behavior using actual consumers.
- [ ] Prove legacy-history/read/scoring survival and cutover/rollback without silent retiming, loss or shadow duplication; obtain review for any required migration before data effects.
- [ ] Run proportional owning software, relevant real-Postgres and hosted gates on the actual implementation tree; do not substitute this spec validation for those proofs.
