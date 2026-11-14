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

- [x] 1.1 WhatsApp: add `is_from_me` (bridge `raw.is_from_me`) and `forwarded` (`_is_forwarded`)
  to each `conversation_history` entry in `_build_batch_envelope`
  (`src/butlers/connectors/whatsapp_user_client.py`).
- [x] 1.2 Telegram: add `is_from_me` (`Message.out`) and `forwarded` (`Message.fwd_from is not
  None`) per entry (`src/butlers/connectors/telegram_user_client.py`); omit when the attribute
  is absent.
- [x] 1.3 Tests: extend `tests/connectors/test_whatsapp_user_client.py` and
  `tests/connectors/test_telegram_user_client.py` with mixed owner, counterparty, forwarded and
  reply entries; assert facts and that `text` never inlines the quoted message.

- [x] 1.4 WhatsApp: `payload.raw.peer_identity` for private chats only (bu-jv1moa.93), tested for
  private, mapped LID, group and malformed JIDs. The bu-jv1moa.11 counterparty derivation
  consumes it when history lacks a non-owner sender; that consumption is a separate slice.

## 2. Switchboard projection (REQ-butler-switchboard-011)

- [x] 2.1 Verify the `[Unknown]` caller-authentication seam and bind the projection on the
  strongest live caller authentication. Amended 2026-11-14 (bu-jv1moa.11): the
  `ConfirmAuthorityTopology` kernel-peer composition is not instantiated in any deployment until
  bu-atc3xi, so binding on it would leave the projection unreachable. The projection is served
  only as the `owner_chat.spans` service-plane named relay (RFC 0038 D7): a projected
  workload token with the relay audience, the presented `relationship` daemon on the allowlist,
  and a strict cursor-only schema. Switchboard answers it in-process; it has no MCP tool, so no
  model session or direct MCP client can reach it. The presented daemon narrows the workload
  and is not separately proven.
  This amendment changes what 2.1 required; it does not merely clarify it. The original text
  was: "Verify the `[Unknown]` caller-authentication seam: bind the projection on the trusted
  kernel-peer composition used by `ConfirmAuthorityTopology` with a Relationship issuer and a
  projection audience. Stop if only weaker authentication is possible." The lead's ruling (P2)
  reads it this way: the workload is authenticated and the daemon is only presented, which is
  accepted for this dark, read-only projection.
  The stop rule is moved to an owner act, not overridden: the projection ships dark, and S1
  activation (`COMMITMENT_CANDIDATES_LIVE`, and any use of the `owner_chat.spans` relay) waits on
  owner approval bead bu-dhloi1.281 (PR #5713, the REQ-butler-switchboard-008 workload
  principal) as well as on bu-atc3xi. That approval is the owner decision that accepts
  workload-bound relay authentication as the binding. Until it is given,
  `BUTLERS_SERVICE_PLANE_AUTH` stays off and the projection is unreachable.
- [x] 2.2 Verify the `[Unknown]` single-message envelope path; cover or exclude it explicitly.
  Verified 2026-11-14: WhatsApp `_normalize_single_event_to_ingest_v1` has no caller; Telegram's
  single-message path is live (backfill, ingest retry, chat-id fallback) and carries no
  `conversation_history`. Excluded explicitly in S1 (REQ-butler-switchboard-011 and
  `docs/connectors/overview.md`); covering it is a separate follow-up.
- [x] 2.3 Implement the fixed projection over `switchboard.message_inbox` with the
  `(received_at, inbox_id)` cursor, the normative 60-second in-flight exclusion and the 14-day floor;
  unavailability returns an error.
- [x] 2.4 Tests on migrated PostgreSQL next to `roster/switchboard/tests/test_ingest_api.py`
  (real `ingest_v1` writes, then projection reads): qualifying selection, group exclusion,
  owner-sender mismatch exclusion, cursor resume without gaps, a late-committing row inside the
  60-second window is not skipped, unauthenticated caller refused. DB cases are in
  `roster/switchboard/tests/test_owner_chat_projection_db.py`; caller refusals are relay tests in
  `tests/core/test_service_plane_relay_endpoint.py`.

## 3. Relationship reducer and store (REQ-commitment-lifecycle-011, -012, -015)

- [x] 3.1 Next Relationship migration: `relationship.commitment_candidates` per design.md
  (unique `source_key`, partial unique non-terminal `fingerprint`), plus the sink receipt keyed
  by `confirm_id`; downgrade drops both.
- [x] 3.2 Deterministic job `commitment_candidate_scan` (`dispatch_mode="job"`, every 10 minutes)
  in `roster/relationship/jobs/`: page the projection, resolve the counterparty with
  `resolve_contact_by_channel`, run `detect_commitment` with `now` = event time in
  `resolve_general_timezone`, apply the negatives and dedupe, commit candidates, then write the
  cursor; skip entirely while the signed-confirm composition is absent; lapse and purge sweep in
  the same job. Done 2026-11-14: dark behind `COMMITMENT_CANDIDATES_LIVE` (no span read, no
  cursor write) and registered without a schedule; lapse and purge run on every run so the
  14-day retention holds while dark or after a rollback.
- [x] 3.3 Tests: new `roster/relationship/tests/test_commitment_candidates_db.py` on migrated
  PostgreSQL fed by real projection pages: one attributed candidate for a verified promise; no
  candidate for group, counterparty, forwarded, missing-fact, unresolved, unidentified, owner,
  hedged, over-long, old and active-fingerprint cases; replay, crash-before-cursor and two
  concurrent runs converge; deadline anchored to event time; lapse at 14 days is silent;
  terminal rows hold no content; tombstones purge; no candidate column holds the message text.
  Done 2026-11-14 in `tests/integration/test_owner_chat_commitment_candidates.py`, not the
  roster path: it stages both the Switchboard and the Relationship schema, which a roster test
  may not reach. Unidentified, owner, over-long, old and event-time deadline cases are pinned
  without a database in `roster/relationship/tests/test_commitment_candidates.py`.

## 4. Card and sink (REQ-commitment-lifecycle-013, -014)

- [x] 4.1 Register `ConfirmSource` `relationship.commitment_candidate` (values `track`, `done`,
  `ignore`; no timeout value) in the trusted composition (`src/butlers/core/confirm_authority.py`);
  `prepare` reloads the candidate, posture and owner recipient from Relationship state and builds
  the prompt only from the persisted card snapshot (timeout fixed once). Done 2026-11-14:
  `relationship_confirm_sources(pool)` builds it from
  `roster/relationship/tools/commitment_candidate_cards.py`; the card step is
  `card_step`, which `scan` runs when the switch passes it. A pending candidate whose owner
  recipient changed since its snapshot is refused and stays pending.
- [x] 4.2 Relationship sink: lock candidate, apply disposition, look up an active fingerprint and
  call `create_commitment` for Track only when none exists, with the evidence in design.md, write
  receipt, all in one transaction; first terminal wins. Done 2026-11-14 (`apply_disposition`).
- [x] 4.3 Tests: extend `tests/integration/test_signed_confirms.py` with the Relationship sink:
  Track creates one commitment with evidence; Done and Ignore write no owner condition; retry of
  the same `confirm_id` is idempotent; forged, wrong-chat, replayed and expired taps change
  nothing; lapse vs Track race; card step repeated returns the same confirm, including a retry
  in the final 24 hours before lapse; Track on an active fingerprint leaves its evidence unchanged; expired card is
  not re-sent; posture defers the card; four promises to one counterparty across owner-local
  midnight each receive one card (no cap, REQ-commitment-lifecycle-017).
- [x] 4.4 Tests delta at implementation: report actual `Tests: +a ~b -c`; extend existing files
  before adding new ones. Reported in the PR; the database cases extend
  `tests/integration/test_signed_confirms.py`.

## 5. Coexistence and documentation (REQ-commitment-lifecycle-016)

- [x] 5.1 Keep `roster/relationship/tests/test_commitment_extraction.py` and
  `roster/relationship/tests/test_commitment_extraction_db.py` passing unchanged; add one case
  where a routed commitment and a candidate share a fingerprint and Track yields one active
  commitment with the routed opening evidence. Done 2026-11-14: both files are unchanged;
  `test_owner_chat_track_on_an_active_fingerprint_keeps_its_evidence` is the shared-fingerprint
  case.
- [x] 5.2 Document the registered source in `docs/runtime/signed-confirms.md`, the reducer in
  `roster/relationship/AGENTS.md`, and the connector facts in the connector docs.
- [x] 5.3 File the follow-up for transport retention of the card prompt copy in
  `public.pending_confirms` (design.md, Lapse and retention).
- [x] 5.4 Rollback: disabling the job and the source stops new candidates and cards; existing
  tombstones and receipts are retained so a restart does no duplicate work. Holds by
  construction: with `live` false the scan reads no span and requests no card, and lapse and
  purge still run. Receipts are never deleted, so a re-enabled sink returns the stored receipt
  for a retried `confirm_id`.
