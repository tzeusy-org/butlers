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
      (REQ-005, with the D3 `endpoint_held` amendment).
  - [x] 2.2a Ingress demotion (slice 3a): REQ-005 copied verbatim; REQ-module-memory-013
        MODIFIED (a held or unreadable owner endpoint yields `third_party` with the owner
        entity kept and `custody_reason = endpoint_held` in metadata; no sixth value). The
        Switchboard identity step checks custody for an owner-looking sender over the owner
        definer's typed candidates (`butlers.core.custody_ingress`, fail closed) and carries
        `source_sender_custody` in the route envelope; memory stamping, owner-ingress
        engagement and break-through (narrow only) honour it. The other owner-authority stores
        honour it too: the Relationship accepted-source report
        (`FactSourceContextRegistry.capture_accepted_report` freezes `third_party` with the
        owner entity kept, so an identity claim is held, never auto-confirmed), the Finance
        expected-signal producer (`runtime_signal_source`, and so subscriptions'
        `_writer_is_owner`) and the routed prompt preamble, which marks the endpoint held. Approval admission already goes
        through the custody-aware definer. Discretion weighting is unchanged: it grants no
        authority, and demoting it could drop held-chat content REQ-005 keeps as a report.
        The generalist-lead-focused-delegation MODIFIED REQ-module-memory-013 carries the same
        scenario; whichever change archives later must keep the other's text.
  - [x] 2.2b Write-time custody recheck for a session admitted before the hold (slice 3b).
        A routing context that names a sender entity also names its source endpoint
        (`source_endpoint`, set only by the server-side route producers: the Switchboard
        pipeline, `route.execute` and inbox recovery). Memory's `resolve_content_authority`
        rechecks it before stamping `owner`; the Relationship report is rechecked on each MCP
        request (`current_invocation_report`, from the invocation's routing context), so a
        hold that commits after ingress or after the report froze demotes later writes to
        `third_party` with the owner entity kept. A missing endpoint or unreadable custody
        fails closed; the dashboard is not an endpoint. Ingress shares one custody read:
        `capture_accepted_report` records its verdict and the identity step reuses it
        (`owner_custody`), so the frozen report and the route envelope cannot disagree.
  - [ ] 2.2c Server-owned canonical source binding and recovery rehydration (REQ-005
        P-CUSTODY-1c half): deferred to bu-s11n0s.81 (RFC 0038 broker).
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
      (due at once, `custody_park` envelope section naming the held targets and their hold
      episode) instead of only a `failed` ledger row. An unreadable custody
      (`eligibility_unknown`), a failed episode lookup and every non-generic notice are still
      refused, never parked. The episode comes from the new content-blind
      `public.custody_target_episode(predicate, object)` (core_349, granted like
      `custody_target_held`). No release-time write: the table is per butler schema, out of the
      public engine's reach, so the flush (`butlers.core.custody_park`) re-resolves the owner
      default (Telegram chat id, then email); while every target is held or unreadable the row
      stays pending. Once one is provably unheld it is sent by that channel with that exact
      `delivery.recipient` (so the Switchboard deliver path has a recipient and Messenger's
      adapter fence rechecks it) and the held-while-lost marker, under `FOR UPDATE SKIP LOCKED`
      across revalidation and send: once, or at least once on a send-then-commit failure. A
      re-hold before the flush sends nothing; the 24-hour expiry writes an `expired` ledger row;
      a row not exactly the generic shape is cancelled.
- [ ] 2.4 Security question and bound answer, Finance and recovery evidence, release and parked
      recovery, protected owner surface (REQ-008 to -011).
  - [x] 2.4c Security question, dashboard half (S5c, core_350, REQ-008): Switchboard's
        publisher-local outbox (`public.security_questions`, one row per authenticated
        `switchboard.security_event`, admitted at publish and recovered by the scheduled
        `security_question_cycle` from the stored event); one bounded notify with a dashboard link
        that is claimed before the send and never re-sent (a doubtful send ends `ambiguous`);
        unverified events get no question. The owner answers on the protected dashboard
        (`/security/:eventId`, `/api/custody/security-questions`; the `/security` list shows every unanswered question, including `unavailable` and `ambiguous` deliveries, and is in the navigation): `yes` changes nothing, `no`
        binds owner-selected exact targets through `custody_dashboard` in one transaction with the
        recorded answer, and the cycle then opens the canonical case and recovery-door evidence
        through `record_security_answer`. A retained `no` is never erased; no automatic `no`.
        Deferred (bu-s11n0s.81): the Telegram callback and `csq1` question tokens (the last three
        REQ-008 scenarios), the provider inventory fallback for an unresolved affected account
        with `containment unknown`, and source-owned reconciliation of an ingest that committed
        before the publisher crashed (the cycle recovers a published event only). Not built: the
        S5g owner-surface page (only the questions needed to answer); an owner whose Telegram
        recipient is held gets an `unavailable` question (no provider attempt, listed unanswered),
        not the email fallback. A send that may have started without confirmation is `ambiguous`
        and never re-sent.
