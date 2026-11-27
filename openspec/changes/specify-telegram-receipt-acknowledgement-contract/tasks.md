## 1. Draft the goal receipt contract

- [x] 1.1 Re-scope the existing Telegram acknowledgement contract to RFC 0038 goal admission
  and actual reply delivery, using the Telegram receipt collision disposition.
- [x] 1.2 Preserve all four acknowledgement styles, the reaction default and the one-second
  local typing initiation bound. Apply the signed M6 baseline and M7 target distinction.
- [x] 1.3 Specify source and destination authorization, the default-off boundary, replay
  behavior and content-free acknowledgement telemetry.
- [x] 1.4 Preserve the four-second keepalive, active per-chat counts, terminal cleanup, and
  recovery. Keep the parent outcome, progress behavior and switch release open.
- [x] 1.5 Reconcile module and connector duties with the RFC 0038 goal and Messenger path
  while preserving their separate credentials and transport boundaries.

## 2. Approval gates

- [ ] 2.1 Obtain independent exact-head semantic review of this re-scoped draft.
- [ ] 2.2 After review passes, obtain separate owner approval naming the exact reviewed artifact.
  Any semantic edit invalidates that review and requires a fresh pass.
- [ ] 2.3 Keep any implementation, migration, credential, or live Telegram API call blocked until
  this contract is accepted and its own separate authorities are satisfied.

## 3. Future first-receipt foundation after approval (bu-z0b0ah.9)

- [ ] 3.1 Bind admission and the first acknowledgement at the existing trusted control-plane
  construction seam. Add no deployed roster configuration, new receiver or live route.
- [ ] 3.2 Keep a private distinction between a newly committed decision and its durable replays
  while retaining admission equality for existing callers. Dispatch the configured initial
  acknowledgement once after commit and before the original lead launch. Keep launch possible
  after refusal, deadline or uncertain delivery, and preserve supervisory recovery.
- [ ] 3.3 Prove the owner-only transport contract through an injected Messenger boundary with
  no provider calls. No recurring state, progress messages or terminal lifecycle actions in
  this first slice.

## 4. Verification after approval

- [ ] 4.1 Use real PostgreSQL with the existing accepted-ingress fixture and concurrent source
  retries to verify the initial handoff reaches only one new durable goal.
- [ ] 4.2 With a fake Messenger, verify each style, the default, invalid input, and local typing
  dispatch initiation within one second. Verify first initiation before lead launch even on a
  concurrent duplicate.
- [ ] 4.3 Prove zero effects on disabled, refused, joined, non-Telegram, non-owner and failed
  live owner-resolution paths. Include exact source/target mismatch, shared chat, an unavailable
  lookup and a verified-owner positive control.
- [ ] 4.4 Check refusal and ambiguous transport once, and preserve lead/recovery behavior and
  content-free telemetry. Run the owning suites, strict OpenSpec and `make check-guards`.

## 5. Remaining lifecycle and release (bu-z0b0ah.4 and bu-enbrs2)

- [ ] 5.1 Implement and verify fake-clock four-second refresh and per-chat reference counting.
  Remove holds on reply delivery, failure, Stop and lease lapse and test lost/restarted workers.
- [ ] 5.2 Verify one delayed, reply-bound deterministic progress message and its edit bound.
  Settle success, failed/partial, and Stopped states, including restart with a persisted ID.
- [ ] 5.3 Preserve owner approval and the default-off switch. At the separately authorized M6
  release, confirm legacy routing callback removal and record the approved latency baseline.
- [ ] 5.4 After all authorized implementation, merge and verification, sync canonical
  requirements and archive the completed change through the ordinary process.
