## Tasks

Spec only. No code, migration or provider call is part of this change. Items 2 onward are verify
tasks for the `[Unknown]`s and the follow-on implementation work that adoption unblocks.

### 1. Draft and adopt

- [x] 1.1 Draft proposal, design, and the `connector-event-clock`, `connector-source-coverage` and
      `connector-silent-source` deltas under posture A.
- [ ] 1.2 Independent review of the exact head reports no blocking findings.
- [ ] 1.3 Owner adopts or declines. On adoption bu-h40h2b.12 unblocks; on decline close bu-h40h2b.12
      as declined.

### 2. Verify the unknowns before implementation

- [ ] 2.1 Heartbeat history horizon: how long `connector_heartbeat_log` partitions are kept, and
      whether `docs/operations/data-retention.md` covers them (it lists `public.ingestion_events`
      only). Needed for `connector_offline` evidence and for REQ-connector-source-coverage-004.
- [ ] 2.2 Per-connector source-time offset and precision: confirm for each connector in design.md
      whether the stated time carries a UTC offset (Gmail `internalDate` epoch ms; Telegram `date`;
      WhatsApp bridge `timestamp` string form; Steam `unlocktime`; Spotify `played_at`; Drive
      `modifiedTime`; Google Health interval and daily-summary times).
- [ ] 2.3 Whether any connector declares an event-cadence expected signal in
      `public.expected_signals` today; REQ-connector-silent-source-001 needs one per evaluated source.
- [ ] 2.4 Whether operator-paused or maintenance state exists per connector ("where supported" in
      connector-base-spec) and where it is read.
- [ ] 2.5 Which table `roster/switchboard/api/router.py:1132` orders by `observed_at` from.
- [ ] 2.6 Whether the Telegram user client is heartbeating while silent (the bead reports it dark
      since 2026-07-18). Needs a separately authorized content-blind operational packet; do not infer
      a Telegram lapse case from this spec.
- [ ] 2.7 How the Telethon `{pts, qts, date, seq}` state maps to a time, if at all, for a
      `time_cursor` assertion.

### 3. Follow-on implementation (separate beads, not this change)

- [ ] 3.1 Slice A after bu-xmueeq's envelope change: contract fields and validators, ingest write,
      `public.ingestion_events` columns, one connector adopting with synthetic fixtures for
      source-stated, connector-derived and receipt-only, plus duplicate, late and corrected events.
- [ ] 3.2 Slice B: coverage read model and API with real-Postgres migration, constraint, merge and
      rollback tests; UI tests that a cursor alone never renders complete and unavailable never renders
      an all-clear.
- [ ] 3.3 Slice D after bu-h40h2b.8 approves the key and evidence kinds: Switchboard scheduled job and
      idempotency tests.
- [ ] 3.4 Reconcile ownership with existing backfill controls, expected-signals and fleet-case work,
      bu-h40h2b.8 situations, and connector-runtime authority work.
