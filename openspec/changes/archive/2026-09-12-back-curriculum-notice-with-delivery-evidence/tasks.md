# Tasks

## 1. Notification path

- [x] 1.1 Stamp the runtime session id into `public.attention_ledger` metadata at every
  `source="notify"` call site, so a ledger row names the session that produced it.
- [x] 1.2 Add `find_notify_dispatch_for_session()` returning the terminal notify dispatch for
  one session, preferring a `delivered` row, and returning `None` when there is none.
- [x] 1.3 Cover both with unit tests, including that the reader does not fail open.

## 2. Receipt storage

- [x] 2.1 Add migration `education_005` with `calibration_notice_outcome` and
  `calibration_notice_accepted_at`.
- [x] 2.2 Constrain the outcome vocabulary and bind the pair so the acceptance moment exists
  exactly when the outcome is `delivered`.
- [x] 2.3 Prove both constraint directions against real Postgres.

## 3. Receipt settlement

- [x] 3.1 Read notice evidence from the ledger on the completed path only.
- [x] 3.2 Map an unreadable ledger and a missing session id to `unproven`, and a readable
  ledger with no row to `no_record`.
- [x] 3.3 Write the outcome and the acceptance moment as one unit in `_settle_receipt`.
- [x] 3.4 Test that a failed, suppressed, absent or unreadable notice records no acceptance
  moment even while `calibration_ready_at` is set.

## 4. API and UI surfaces

- [x] 4.1 Add both fields to the receipt response model and the frontend type.
- [x] 4.2 Render the notice as a line separate from calibration, claiming channel acceptance
  only for `delivered` and degrading an unknown outcome to "could not be confirmed".
- [x] 4.3 Cover every outcome in the panel test, including the unknown-outcome fallback.
