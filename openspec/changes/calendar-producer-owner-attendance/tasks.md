## 1. Producer attendance

- [x] 1.1 Apply the shared owner-attendance rule to calendar producer candidates
- [x] 1.2 Pin declined-only, transparent-only, overlap-wins and retained-eligibility cases in producer unit tests
- [x] 1.3 Extend the producer integration test with declined and free overlaps through real SQL and public context readback

## 2. Event-type consumers (bu-s11n0s.4 S2)

- [x] 2.1 Out-of-office is calendar context only: it suppresses focused, an attended meeting keeps meeting, and there is no `away` write or mode entry (bu-u33sb7)
- [x] 2.2 Typed focusTime asserts focused; status events are never meeting candidates
- [x] 2.3 Status events add no day-load hours (`ConflictCandidate.counts_toward_day_load`); an opaque one still overlaps and chains back to back (AC D, E7). They are excluded from prep, meeting-card and meeting-debrief selectors
- [x] 2.4 The Google connector never scans status events as starting soon
- [x] 2.5 Working location is a typed read (`current_working_location`), not a signal; publishing it waits for a perception kind (`owner.work_location`) at the M7/.21 switch

## 3. Remaining acceptance (bu-s11n0s.4 AC C-E)

- [x] 3.1 An out-of-office-only calendar holds no broker insight under meeting; an attended meeting still holds (real bus readback)
- [x] 3.2 A prep key left from before an event became a status is pruned by the keep-set, and the ordinary envelope keeps its commitments (real SQL)
- [x] 3.3 A source read failure raises before any set or clear and leaves the bus unchanged (independent readback)
- [x] 3.4 Superseded, not built: one-transaction producer atomicity with a scoped advisory lock (design E8) and AC C's atomic-failed-transition clause. They move to the successor writer (PerceptionStore CAS and per-slot serializer at the RFC 0038 switch). The calendar witness on the retiring bus is keyed by pool and stays as is
- [x] 3.5 RFC 0009 gains a calendar event-type section; the calendar docs match
- [ ] 3.6 Recorded Google payload capture (AC B) is an owner-run step; the synthetic corpus discloses that nothing is captured
