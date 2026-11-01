## 1. Zone support

- [x] 1.1 Parse and validate `timezone` on `[[butler.schedule]]` and pass it to sync.
- [x] 1.2 Sync computes `next_run_at` in the declared zone and stores it on the row.

## 2. Roster anchoring

- [x] 2.1 Anchor `eod-tomorrow-prep` (23:00) to owner-local; keep `upcoming-travel-check` at `0 0 * * *` pinned to `Etc/UTC` (08:00 SGT).
- [x] 2.2 Pin `Etc/UTC` on the infrastructure schedules commented in UTC.

## 3. Always-quiet warning

- [x] 3.1 Warn once per sync for a notifying prompt-mode schedule that only fires inside the quiet window.

## 4. Sentinel guard

- [x] 4.1 Refuse `UTC`/blank `timezone` in the parser and in sync with an error naming `Etc/UTC`.
- [x] 4.2 Update an existing row when only its timezone changes.
