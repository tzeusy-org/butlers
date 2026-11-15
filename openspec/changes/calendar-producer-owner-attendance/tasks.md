## 1. Producer attendance

- [x] 1.1 Apply the shared owner-attendance rule to calendar producer candidates
- [x] 1.2 Pin declined-only, transparent-only, overlap-wins and retained-eligibility cases in producer unit tests
- [x] 1.3 Extend the producer integration test with declined and free overlaps through real SQL and public context readback

## 2. Event-type consumers (bu-s11n0s.4 S2)

- [x] 2.1 Out-of-office is calendar context only: it suppresses focused, an attended meeting keeps meeting, and there is no `away` write or mode entry (bu-u33sb7)
- [x] 2.2 Typed focusTime asserts focused; status events are never meeting candidates
- [x] 2.3 Status events add no day-load hours or conflicts and are excluded from prep, meeting-card and meeting-debrief selectors
- [x] 2.4 The Google connector never scans status events as starting soon
- [x] 2.5 Working location is a typed read (`current_working_location`), not a signal; publishing it waits for a perception kind (`owner.work_location`) at the M7/.21 switch
