## 1. Producer attendance

- [x] 1.1 Apply the shared owner-attendance rule to calendar producer candidates
- [x] 1.2 Pin declined-only, transparent-only, overlap-wins and retained-eligibility cases in producer unit tests
- [x] 1.3 Extend the producer integration test with declined and free overlaps through real SQL and public context readback

## 2. Event-type consumers (bu-s11n0s.4 S2)

- [x] 2.1 Out-of-office clears meeting/focused as calendar context only, with no `away` write or mode entry (bu-u33sb7)
- [x] 2.2 Typed focusTime asserts focused; status events are never meeting candidates
- [x] 2.3 Status events add no day-load hours or conflicts and are excluded from prep and meeting-card selectors
- [x] 2.4 The Google connector never scans status events as starting soon
- [ ] 2.5 Working-location signal (deferred to S3)
