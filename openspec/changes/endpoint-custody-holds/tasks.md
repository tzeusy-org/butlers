## 1. Hold record and owner-resolution gate (slice 1)

- [x] 1.1 Add `public.custody_holds` with digest target key, monotonic generation, one active hold
      per target, append-only history and owner-only row level security (core_293).
- [x] 1.2 Add the content-blind `public.custody_target_held` read and make
      `public.resolve_owner_triple` refuse owner for an actively held matched fact.
- [x] 1.3 Refuse downgrade while a hold is active or unprovable.
- [x] 1.4 Real-PostgreSQL tests: held email, Telegram and WhatsApp-by-phone stop resolving and
      release restores; runtime role cannot read, forge, release or delete; history cannot rewind;
      downgrade refusal and reapply.
- [x] 1.5 Hardening (core_295): every custody function belongs to the database owner; a held phone
      demotes every suffix-equivalent spelling of that owner; legacy exact-predicate mode matches
      only holdable predicates.
- [x] 1.6 Transitive phone hold coverage (core_296): a held phone demotes every same-entity phone
      fact sharing its trailing eight digits, so a 12-digit held spelling covers an unheld 8-digit
      one.

## 2. Remaining slices

- [ ] 2.1 Admitted Switchboard hold engine: cheap owner-resolved LOCK, protected dashboard/host
      hold, release and replaced, command/case/receipt atomicity (REQ-001, -002, -003, -004).
  - [x] 2.1a Protected engine (core_347): dashboard and host hold, release and replace in one
        transaction with the episode's fleet case, content-blind evidence and an immutable
        receipt; session and CSRF checked at commit; replay, read back, convergence, stale
        release and failure-before-commit on real PostgreSQL. Exact targets and owner-selected
        sets only (REQ-001 exact half, REQ-002 peer-DML and caller-string negatives, REQ-003
        protected half, REQ-004).
  - [ ] 2.1b Cheap owner-resolved LOCK (`endpoint_lock`, REQ-003 cheap half) and the closed
        provider-inventory fallback (REQ-001 provider half). Waits for the RFC 0038 cutover:
        the freeze bars a new General-to-Switchboard delegation path.
  - [ ] 2.1c Supported-process source protocol (REQ-002 P-CUSTODY-1c). UNDELIVERED; expected to
        be replaced by RFC 0038 broker assertions and control-plane admission, with the REQ-002
        amendment made at the cutover.
- [ ] 2.2 Direct `resolve_contact_by_channel` routing, memory and session-recheck demotion
      (REQ-005, needs the D3 `endpoint_held` amendment).
- [x] 2.3a Messenger adapter egress fence (slice 4, REQ-007 and the egress half of REQ-006):
      `butlers.core.custody_egress` asks only `public.custody_target_held` and fails closed (no
      pool, lookup error, non-boolean answer or unnameable target refuses with
      `eligibility_unknown`). Fenced paths: email send/reply (recipient and cached bot-scope
      sending account); Telegram send/reply, files, edit and react (recipient chat as
      `has-handle telegram:<chat id>`; a refused ingest lifecycle reaction is logged and
      skipped); the chronicler gap interview, which posts to the owner chat directly (refusal is
      not delivered, so the day is not marked asked). Registered tools, route.execute delivery
      commands, approval-card handoff, approved execution and deferred flush reach Messenger's
      adapters through those methods. Connector replies are guarded by `resolve_owner_triple`
      (slice 1), not by this fence. Since bu-s11n0s.93 the informational confirm edit and
      WhatsApp send/reply are fenced too: WhatsApp checks the recipient (JID handle forms and
      `has-phone` spellings: suffixes of at least eight digits and one- or two-digit prefixed
      forms) and the bridge account from `/status` the same way, including its `has-handle` JID,
      refusing `eligibility_unknown` when either cannot be named. Known unfenced:
      Google Calendar invitations with `sendUpdates=all` (`modules/calendar/google.py`, outside
      REQ-007's Messenger scope; bu-s11n0s.94). Known limits: a hold only on a username-form
      Telegram fact does not fence chat-id sends, and a held phone spelling three or more digits
      longer or differing inside the number is not matched (core_296's suffix rule, length
      difference at most two, does not match it either). Route.execute reports `endpoint_held` as a
      non-retryable policy refusal and `eligibility_unknown` as retryable; the approval-card
      handoff records the refusal as pre-provider `safe_retry`, never ambiguous.
- [x] 2.3b Owner-default fallback (slice 4): a notify with no channel, recipient or entity
      whose owner Telegram target is held or unreadable takes the owner's unheld email; with
      none provably unheld it returns `endpoint_held`/`eligibility_unknown` with no provider
      attempt and a durable `failed` attention-ledger row (the truthful unavailable result).
      A forced channel or recipient never retargets.
- [ ] 2.3c Deferred from slice 4:
      - the approved-executor source-eligibility recheck (REQ-006 executor half) needs the
        P-CUSTODY-1c source protocol, which the RFC 0038 broker supersedes at cutover (bead
        bu-s11n0s.81);
      - message-retaining park and flush on release, which belongs to REQ-010 (task 2.4).
      The Telegram bot account is not an owner channel fact, so the core_347 engine cannot hold
      it.
- [x] 2.4a Case closure (core_348, REQ-010 closure half): a release or replace closes the
      episode's fleet case (outcome `custody_resolved`) only when every generation held in that
      episode is released or replaced; a partial recovery leaves the case active and the result
      reports `remaining_held`. The command locks every active member's target (sorted) before
      the case row, so concurrent last releases serialize and a member joining mid-command
      refuses `membership_changed`. Ordinary case close never reaches the engine and releases
      nothing. Same SECURITY DEFINER authority; one added UPDATE policy on `fleet_cases`. Installed
      once across the per-schema chains with core_347's yielding installer (catalog-read check,
      polled session lock, short lock timeout, retry on sibling conflict).
- [x] 2.4b Park and flush (REQ-007 park path, REQ-010 automatic recovery, S5b): an owner-default
      `send`/`insight` notice with no channel, recipient, entity, attachment or reply prompt whose
      every configured owner target is held is parked in the butler's `deferred_notifications`
      (due at once, `custody_park` envelope section naming the held targets; no episode, since
      `custody_target_held` answers only a boolean) instead of only a `failed` ledger row.
      An unreadable custody (`eligibility_unknown`) and every non-generic notice are still
      refused, never parked. No release-time write: the table is per butler schema, out of the
      public engine's reach, so the flush (`butlers.core.custody_park`) re-reads
      `custody_target_held`: while every target is held or unreadable the row stays pending;
      once one is clear it is sent once (row `FOR UPDATE SKIP LOCKED` across revalidation and
      send) by the owning pipeline with the held-while-lost marker, by the clear target's
      channel. A re-hold before the flush sends nothing; the ordinary 24-hour expiry applies;
      a row not exactly the generic shape is cancelled. No migration.
- [ ] 2.4 Security question and bound answer, Finance and recovery evidence, release and parked
      recovery, protected owner surface (REQ-008 to -011).
