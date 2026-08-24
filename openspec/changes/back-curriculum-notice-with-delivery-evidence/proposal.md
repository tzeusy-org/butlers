# Back the curriculum calibration notice with delivery evidence

## Why

`bu-6jv4m.10` gave the curriculum receipt a `calibration_ready_at` column, set when the
correlated teaching flow reaches `diagnosing`. It was deliberately named for what the data
supports: calibration **began**. It is not, and was never claimed to be, evidence that the
`notify()` the session was instructed to send ever reached the owner.

That leaves a gap the owner feels. The session prompt asks for a Telegram notice ("Starting
your {topic} curriculum..."), and the flow reaching `diagnosing` says nothing about whether
that notice went out. The two facts diverge in exactly the case that matters: calibration is
live, waiting on an answer, and the owner was never told to give one. Today the receipt is
silent about that, so an owner reading "Calibration started" has no way to tell a butler that
messaged them from one that failed to.

The fix is to record what the notification path itself attests, and nothing more.

## What Changes

- `notify()` stamps the runtime session id into its `public.attention_ledger` row metadata, so
  a ledger row can be correlated exactly to the session that produced it instead of guessed at
  by butler and time window.
- A new reader, `find_notify_dispatch_for_session()`, returns the terminal notify dispatch the
  ledger recorded for one session, or `None`.
- The curriculum receipt gains `calibration_notice_outcome` and
  `calibration_notice_accepted_at`, written from that reader and never from flow state.
- The receipt panel states the notice outcome as a line separate from calibration, and claims
  channel acceptance only when the ledger recorded `delivered`.

## What This Deliberately Does Not Claim

The strongest thing the notification path can attest is that a delivery channel **accepted**
the message: `notify()` writes `outcome="delivered"` only after Switchboard's `deliver()`
returned a non-failed status, which happens only after Messenger reported the provider took
it. There is no read receipt anywhere in the path. The column is therefore named
`calibration_notice_accepted_at`, not `..._delivered_at` and not `..._read_at`.

Nor is an absent ledger row treated as proof of non-delivery. `record_attention_event()` is
best-effort and never raises, so a missing row means the evidence is missing, not that the
notice failed. That state gets its own outcome word (`no_record`), distinct from a recorded
failure (`failed`) and from our being unable to consult the ledger at all (`unproven`).

## Impact

- Affected specs: `dashboard-education-api`, `dashboard-education-ui`, `core-notify`
- Affected code: `src/butlers/core/attention_ledger.py`,
  `src/butlers/core_tools/_notifications.py`, `roster/education/api/router.py`,
  `roster/education/api/models.py`, `roster/education/migrations/005_*.py`,
  `frontend/src/api/types.ts`,
  `frontend/src/components/education/CurriculumRequestReceiptPanel.tsx`
- Migration: `education_005` adds the two columns plus CHECK constraints binding the pair, so
  the database refuses a row that carries an acceptance moment without the outcome to justify
  it.
