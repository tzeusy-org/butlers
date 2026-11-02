## 0. Adoption gates (this change)

Spec-first: nothing in groups 1 to 5 is authorized until these close and this change merges. Groups 1 to 5 are the
dispatch packet for bu-jv1moa.11.

- [ ] 0.1 Independent conformance review of this change: additive coexistence with
  REQ-commitment-lifecycle-007, privacy (no raw body outside the Switchboard inbox), exact
  callback authority (signed confirm transport only, Track-only promotion).
- [x] 0.2 Owner card-cap decision recorded 2026-11-02 on bu-jv1moa.43 (option C, no cap in S1;
  revisit at S2); its exact text is REQ-commitment-lifecycle-017 with a scenario spanning
  owner-local midnight.
- [ ] 0.3 Copy the adopted interfaces, matrix, tests and producer edges into bu-jv1moa.11.

## 1. Connector transport facts (REQ-connector-base-spec-013)

- [ ] 1.1 WhatsApp: add `is_from_me` (bridge `raw.is_from_me`) and `forwarded` (`_is_forwarded`)
  to each `conversation_history` entry in `_build_batch_envelope`
  (`src/butlers/connectors/whatsapp_user_client.py`).
- [ ] 1.2 Telegram: add `is_from_me` (`Message.out`) and `forwarded` (`Message.fwd_from is not
  None`) per entry (`src/butlers/connectors/telegram_user_client.py`); omit when the attribute
  is absent.
- [ ] 1.3 Tests: extend `tests/connectors/test_whatsapp_user_client.py` and
  `tests/connectors/test_telegram_user_client.py` with mixed owner, counterparty, forwarded and
  reply entries; assert facts and that `text` never inlines the quoted message.

## 2. Switchboard projection (REQ-butler-switchboard-011)

- [ ] 2.1 Verify the `[Unknown]` caller-authentication seam: bind the projection on the trusted
  kernel-peer composition used by `ConfirmAuthorityTopology` with a Relationship issuer and a
  projection audience. Stop if only weaker authentication is possible.
- [ ] 2.2 Verify the `[Unknown]` single-message envelope path; cover or exclude it explicitly.
- [ ] 2.3 Implement the fixed projection over `switchboard.message_inbox` with the
  `(received_at, inbox_id)` cursor, the 60-second in-flight exclusion and the 14-day floor;
  unavailability returns an error.
- [ ] 2.4 Tests on migrated PostgreSQL next to `roster/switchboard/tests/test_ingest_api.py`
  (real `ingest_v1` writes, then projection reads): qualifying selection, group exclusion,
  owner-sender mismatch exclusion, cursor resume without gaps, unauthenticated caller refused.

## 3. Relationship reducer and store (REQ-commitment-lifecycle-011, -012, -015)

- [ ] 3.1 Next Relationship migration: `relationship.commitment_candidates` per design.md
  (unique `source_key`, partial unique non-terminal `fingerprint`), plus the sink receipt keyed
  by `confirm_id`; downgrade drops both.
- [ ] 3.2 Deterministic job `commitment_candidate_scan` (`dispatch_mode="job"`, every 10 minutes)
  in `roster/relationship/jobs/`: page the projection, resolve the counterparty with
  `resolve_contact_by_channel`, run `detect_commitment` with `now` = event time in
  `resolve_general_timezone`, apply the negatives and dedupe, commit candidates, then write the
  cursor; skip entirely while the signed-confirm composition is absent; lapse and purge sweep in
  the same job.
- [ ] 3.3 Tests: new `roster/relationship/tests/test_commitment_candidates_db.py` on migrated
  PostgreSQL fed by real projection pages: one attributed candidate for a verified promise; no
  candidate for group, counterparty, forwarded, missing-fact, unresolved, unidentified, owner,
  hedged, over-long, old and active-fingerprint cases; replay, crash-before-cursor and two
  concurrent runs converge; deadline anchored to event time; lapse at 14 days is silent;
  terminal rows hold no content; tombstones purge; no candidate column holds the message text.

## 4. Card and sink (REQ-commitment-lifecycle-013, -014)

- [ ] 4.1 Register `ConfirmSource` `relationship.commitment_candidate` (values `track`, `done`,
  `ignore`; no timeout value) in the trusted composition (`src/butlers/core/confirm_authority.py`);
  `prepare` reloads the candidate, posture and owner recipient from Relationship state.
- [ ] 4.2 Relationship sink: lock candidate, apply disposition, `create_commitment` for Track with
  the evidence in design.md, write receipt, all in one transaction; first terminal wins.
- [ ] 4.3 Tests: extend `tests/integration/test_signed_confirms.py` with the Relationship sink:
  Track creates one commitment with evidence; Done and Ignore write no owner condition; retry of
  the same `confirm_id` is idempotent; forged, wrong-chat, replayed and expired taps change
  nothing; lapse vs Track race; card step repeated returns the same confirm; expired card is
  not re-sent; posture defers the card; four promises to one counterparty across owner-local
  midnight each receive one card (no cap, REQ-commitment-lifecycle-017).
- [ ] 4.4 Tests delta at implementation: report actual `Tests: +a ~b -c`; extend existing files
  before adding new ones.

## 5. Coexistence and documentation (REQ-commitment-lifecycle-016)

- [ ] 5.1 Keep `roster/relationship/tests/test_commitment_extraction.py` and
  `roster/relationship/tests/test_commitment_extraction_db.py` passing unchanged; add one case
  where a routed commitment and a candidate share a fingerprint and Track yields one active
  commitment with the routed opening evidence.
- [ ] 5.2 Document the registered source in `docs/runtime/signed-confirms.md`, the reducer in
  `roster/relationship/AGENTS.md`, and the connector facts in the connector docs.
- [ ] 5.3 Rollback: disabling the job and the source stops new candidates and cards; existing
  tombstones and receipts are retained so a restart does no duplicate work.
