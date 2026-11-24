## 1. Slice A: write overlap check across owner time

- [x] 1.1 Check creates, updates and replan moves against all owner and butler calendars
- [x] 1.2 Refuse with "conflict check unavailable" when the check cannot be read
- [x] 1.3 Tests: provider double keyed by calendar_id, real-Postgres role ledger query

## 2. Later slices

- [ ] 2.1 Slice C: failed proposals and overlay reads render an error state
- [ ] 2.2 Slice B: mirror failures, butler task mirrors, fleet-wide primary calendar
- [ ] 2.3 Slice B2: no standing rule for always_per_event tools
- [ ] 2.4 Slice D: span-true grid, suppression disclosure, truncation
