## 1. Marker

- [x] 1.1 Add `drop_context` to the filtered payload helper and strip it on replay.
- [x] 1.2 Stamp every Gmail drop site for known contacts.

## 2. Aggregate and opener

- [x] 2.1 Add `GET /api/ingestion/events/dropped-known` with a degraded envelope.
- [x] 2.2 Render the clause, the door, and the unknown state in the Filters opener.

## 3. Verification

- [x] 3.1 Connector, API, replay-strip, and opener tests; real-Postgres aggregate test.

## 4. Deferred

- [ ] 4.1 Narrowed filtered list, replay verb, and attention episode from the clause.
- [ ] 4.2 Opt-in "except known contacts" rule-editor option.
- [ ] 4.3 WhatsApp and Drive drop sites.
