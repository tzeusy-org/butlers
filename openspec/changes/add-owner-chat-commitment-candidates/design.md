# Design: owner-chat commitment candidates (promise radar S1)

## Context

Authority, in order: `about/heart-and-soul/vision.md` (mental labor absorption; non-negotiable 3,
cross-domain access through the control plane), RFC 0026 (commitment lifecycle), the adopted
REQ-commitment-lifecycle-007 and -008, RFC 0038 D2 (deterministic jobs and reactions unchanged),
D4 (owner decisions arrive only through the dashboard and signed Telegram callbacks) and D5
(origins; owner words count only on connector-marked spans), the owner release bu-4ap4ap of run 20
move 11, and the permanent personal-account send ban bu-bup68v. Recorded S1 decisions on
bu-jv1moa.11: direction `owner_to_other` only; action of at most 120 characters; evidence
reference, no raw body; dedupe by source message and direction; skip group, unresolved,
non-owner, hedged and active-fingerprint cases; quiet hours and posture defer the card; unconfirmed
candidates lapse silently after 14 days; promotion only through the signed owner tap.

Source baseline: `origin/main` at `61334a9b3`. Every path and symbol below was read there.

## Source map: ingress to Relationship

| Step | Owner | Actual source today | What this change requires |
|---|---|---|---|
| 1. Provider event | Connector | WhatsApp bridge sets `is_from_me` (`whatsapp-bridge/internal/events/mapper.go:66`) and `content.is_forwarded` (`mapper.go:56`, read by `_is_forwarded`, `whatsapp_user_client.py:367`). Telethon `Message.out` and `fwd_from` exist but the connector reads neither. | Unchanged. |
| 2. Batch envelope | Connector | `_build_batch_envelope` (`whatsapp_user_client.py:1873`, `telegram_user_client.py:1124`) emits `ingest.v1` with `payload.raw.conversation_history[]` entries `{message_id, sender_identity, sender, text, timestamp, is_new, reply_to}`, `sender.owner_sender_id`, `sender.chat_type`, `sender.participant_count`, `sender.participants`. Per-entry direction is not present; Telegram owner direction is inferred only from a numeric endpoint identity (`_extract_owner_sender_id`, `telegram_user_client.py:1669`). | Add explicit per-entry `is_from_me` and `forwarded` (REQ-connector-base-spec-013). |
| 3. Admission and storage | Switchboard | `ingest_v1` (`roster/switchboard/tools/ingestion/ingest.py:772`) dedupes (`_compute_dedupe_key`, line 350) and writes `switchboard.message_inbox` (raw payload, `request_context` with `owner_sender_identity`, `chat_type`, `participant_count`, built by `_build_request_context`, line 488) and `public.ingestion_events` in one transaction. | Unchanged. The inbox stays the only durable copy of message text. |
| 4. Projection | Switchboard | None. `relationship_jobs.run_interaction_sync` reads `switchboard.message_inbox` directly under the `core_077` grant for passive interaction sync; that grant is not reused here. | New fixed owner-chat span projection on the authenticated service-plane endpoint (REQ-butler-switchboard-011). |
| 5. Reduction | Relationship | `detect_commitment` (`roster/relationship/tools/commitments.py:560`) is pure; its only caller is the LLM-invoked `capture_commitment` (line 745) behind the `commitment_capture` tool. Counterparty resolution today is by capitalised name (`_resolve_counterparty`, line 704). `resolve_contact_by_channel` (`src/butlers/identity.py:538`) reads `relationship.entity_facts`, Relationship's own schema. | New deterministic reducer job; counterparty from chat identity through `resolve_contact_by_channel`; candidate table in the Relationship schema (REQ-commitment-lifecycle-011, -012). |
| 6. Card | Switchboard/Messenger confirm service | `ConfirmService.create` with a registered sink (`src/butlers/core/confirm_service.py:57`), `cfm1` tokens (`confirm_tokens.py`), `public.pending_confirms` (`core_282_signed_confirms`), composition `ConfirmAuthorityTopology` with `ConfirmSource` (`confirm_authority.py`). Dark until the canonical switch installs the composition (`docs/runtime/signed-confirms.md`). | New `ConfirmSource` named `relationship.commitment_candidate` (REQ-commitment-lifecycle-013). |
| 7. Tap | Telegram bot connector, confirm resolver | `telegram_bot._maybe_handle_confirm_callback` (line 1112) verifies HMAC and forwards to `POST /api/confirms/callback`; the resolver locks the row, matches sender and chat, rechecks the deadline under the lock, and records one answer. | Unchanged. |
| 8. Application | Relationship sink | `dispatch_one` (`confirm_service.py:286`) calls the registered `apply(confirm_id, sink_ref, value)` and retries the same key until a receipt with `status: "applied"` returns. Expiry without `on_timeout_value` records `no_answer` without calling the sink. | Relationship sink performs Track, Done or Ignore with a receipt keyed by `confirm_id` (REQ-commitment-lifecycle-014). |
| 9. Commitment | Core helper over `public.owner_conditions` | `create_commitment` (`src/butlers/core/commitments.py:425`) confirms a duplicate fingerprint in place but replaces `evidence_opened` with the newest observation (module docstring, lines 50-60); `commitment_fingerprint` (line 181); `list_active_commitments` (line 583); escalation in `src/butlers/jobs/commitment_escalation.py`. | Helper unchanged. Track looks up an active fingerprint first and calls `create_commitment` only when none exists, so an existing commitment's opening evidence is kept. |

## Ownership

| Concern | Owner | Notes |
|---|---|---|
| Transport facts (`is_from_me`, `forwarded`) | Each user-client connector | Representation only; no Relationship SQL, no detection. |
| Span selection and cursor order | Switchboard projection | Reads only its own inbox. Excludes rows received within the last 60 seconds so an in-flight transaction cannot commit behind a returned cursor. |
| Cursor (checkpoint) | Relationship state store, key `commitment_candidates:cursor`, value `(received_at, inbox_id)` | Written after the page's candidates commit. Tuple watermark, as in Chronicler's `005_tuple_watermark`. |
| Candidate table, migration, reducer, lapse and purge | Relationship (`relationship.commitment_candidates`, next Relationship migration) | Unique `source_key`; partial unique index on `fingerprint` where state is non-terminal. |
| Card creation, delivery, signature, recipient check, first-answer fence, expiry | Signed confirm transport (bu-ow5a4, linked, not duplicated) | Registered source and sink only. |
| Disposition and receipt | Relationship sink, in one Relationship transaction | Receipt table or column keyed by `confirm_id`. |
| Commitment row | `butlers.core.commitments` over `public.owner_conditions` | Existing source `relationship:commitment`. |

### Candidate row

`id`, `source_key` (sha256 of channel, endpoint, chat, provider message id, direction),
`state` (`pending`, `carded`, `tracked`, `done`, `ignored`, `superseded`, `lapsed`), `kind`,
`direction`, `counterparty_entity_id`, `action_description` (at most 120 characters),
`deadline`, `confidence`, `marker`, `fingerprint`, `channel`, `evidence_ref`
(`{ingestion_event_id, provider_message_id}`), `taint` (`untrusted_origin`), `event_time`,
`lapse_at` (`event_time + 14 days`), `card_snapshot` (rendered question, card spans, recipient
binding, timeout fixed once; written before the first card request), `confirm_id`,
`card_expires_at`, `created_at`,
`terminal_at`, `commitment_fingerprint`. Content columns, including `card_snapshot`, are nulled on any terminal transition.

### Evidence on a tracked commitment

`evidence_opened = {source: "owner_chat_candidate", channel, ingestion_event_id,
provider_message_id, event_time, marker, candidate_id, confirm_id, answered_at,
detected_at}`. There is no `utterance` or `excerpt`, unlike the routed path, because the
message text is never stored outside the Switchboard inbox.

## Behavior matrix

### Production

| Input | Result |
|---|---|
| Private chat, explicit `is_from_me: true`, `forwarded: false`, `is_new`, known counterparty, explicit marker, action at most 120 characters, event within 14 days, no active or pending fingerprint | One `pending` candidate. |
| Group, channel or more than two participants | Nothing. |
| Counterparty-authored, forwarded, or a fact missing | Nothing (fail closed; batches stored before the connector change produce nothing). |
| `is_from_me: true` but sender differs from stored owner sender id | Nothing (projection excludes). |
| Counterparty unresolved, unidentified, merged away or owner | Nothing. |
| Hedge, conditional, negation, question (`_is_vetoed`) | Nothing. |
| Action longer than 120 characters | Nothing; never truncated, because truncation would change the fingerprint and could change meaning. |
| Active commitment with the same fingerprint (including a routed one) | Nothing; the commitment is not touched. |
| Non-terminal candidate with the same fingerprint | Nothing. |
| Event older than 14 days | Nothing. |
| Edited message (same provider message id) | Same `source_key`: first version wins. |
| Message deleted after production | Candidate stays; the owner can Ignore. |
| Source key already present (candidate or tombstone) | No-op. |
| Source previously produced nothing, then replayed | Re-evaluated against current state; at most one candidate. |
| Signed-confirm composition not installed | Reducer reads no spans and does not advance its cursor; no candidate content is created without a surface. When installed, the 14-day floor bounds catch-up. |

### Card

| Situation | Result |
|---|---|
| Pending, counterparty `active`, composition available, no active fingerprint | One confirm (`track`, `done`, `ignore`), producer key = candidate id; state `carded`; `card_expires_at` stored. |
| Active commitment with the fingerprint at card time | `superseded`, no card. |
| Counterparty posture not `active` | Stays `pending`; carded when posture returns to `active` before lapse. |
| Card request refused or composition transiently unavailable | Stays `pending`; retried next run. |
| Quiet or sleep hours | Transport defers delivery (Messenger recheck); the producer does not bypass it. |
| Less than 30 seconds to lapse | Never carded; lapses. |
| Card expires unanswered, or first send lost or ambiguous | No re-send (transport never resends; producer creates one card per candidate). Candidate stays `carded` and lapses at `lapse_at`. |
| Card step repeated after a crash | The prompt is rebuilt from the persisted card snapshot (question, spans, recipient, timeout fixed once), so `ConfirmService.create` sees an identical prompt and returns the existing row; recomputing the timeout would change the immutable prompt and be refused. |

### Dispositions and races

| Event | Candidate | Commitment ledger | Receipt |
|---|---|---|---|
| Track on `carded` | `tracked`, content cleared | `create_commitment` with candidate fields and evidence; if the fingerprint is already active, no write (existing row and evidence unchanged) | `{status: applied, outcome: tracked, fingerprint}` |
| Done on `carded` | `done`, content cleared | none | `{status: applied, outcome: done}` |
| Ignore on `carded` | `ignored`, content cleared | none | `{status: applied, outcome: ignored}` |
| Any answer on a terminal candidate (lapsed, superseded, or already disposed) | unchanged (first terminal wins) | none | `{status: applied, outcome: already_<state>}` |
| Answer for an unknown candidate id (tombstone purged) | none | none | `{status: applied, outcome: already_lapsed}` |
| Same `confirm_id` applied again (lost reply, restart) | unchanged | none | stored receipt returned |
| Sink transaction fails | unchanged | rolled back with it | none; transport retries the same key |
| Double tap, concurrent taps | transport records one answer | at most one application | one receipt |
| Forged signature, wrong sender or chat, expired confirm | unchanged; refused before the sink | none | none |
| Lapse sweep vs Track application | row lock on the candidate serializes them; the first committed terminal state wins | Track writes only if it wins | per the winner |
| Routed REQ-007 create vs Track | Track's lookup finds the routed row and skips the write; if the routed create commits between lookup and `create_commitment`, the helper confirms in place and `evidence_opened` becomes Track's | one active commitment | Track reports `tracked` with the shared fingerprint |
| Counterparty posture changes after carding | unchanged | Track still applies (explicit owner choice) | normal |

Done means "already handled, nothing to track"; Ignore means "not a promise worth tracking".
They differ only in the recorded outcome. Neither resolves an existing commitment: resolving one
would be a second promotion path and a resolution without the owner's explicit resolution intent,
which the Track-only boundary and REQ-008 provenance rule do not allow.

### Lapse and retention

| Time | Effect |
|---|---|
| `lapse_at` with no disposition | `lapsed`, silent, content cleared. |
| Any terminal transition | Content cleared; tombstone keeps `source_key`, state, timestamps, `confirm_id`, `commitment_fingerprint`. |
| `event_time` older than 14 days and terminal | Tombstone deleted. Safe, because the projection never returns events older than 14 days, so a purged tombstone can never be re-hit. |

The 14-day bound covers the Relationship candidate store only. The card's question and quoted
action are also written to `public.pending_confirms` by the signed confirm transport. That
transport redacts a row's prompt to a digest tombstone 14 days after the row settles; backups
taken before then keep the copy until they are pruned (`docs/runtime/signed-confirms.md`
§ Prompt retention, `docs/operations/backup-restore.md`). That is the transport's policy, not a
guarantee of this store.

### Restatement after Done or Ignore

Duplicate suppression looks only at active commitments and non-terminal candidates. After the
owner taps Done or Ignore, the candidate is terminal and its tombstone keeps no fingerprint, so a
later message restating the same promise to the same person creates a new candidate and a new
card. With no S1 cap (REQ-commitment-lifecycle-017) nothing else limits this. It is accepted for
S1 because each card follows a fresh message the owner wrote, and keeping a fingerprint on Done
or Ignore tombstones would retain a per-person action hash beyond what the recorded decisions
allow. The S2 cap revisit is the place to reconsider it.

## Decisions (engineering judgment, recorded)

1. Ingress through a Switchboard-owned authenticated projection pulled by Relationship, over a
   direct cross-schema read, the domain-event bus, a connector outbox, or a Switchboard-hosted
   detector. A direct read (reusing `core_077` or a new view) would be a new RFC 0010-class
   exception; vision non-negotiable 3 and RFC 0020/0030 precedent require its own RFC, and the
   bead forbids a cross-butler SQL shortcut. The domain-event bus wakes a reasoning session per
   delivery (`handle_receive_domain_event`) and would copy message text into
   `public.domain_events`. A connector outbox duplicates ingestion durability and pushes
   selection into connectors. Running `detect_commitment` in Switchboard moves Relationship
   domain inference into infrastructure. Reversible: yes.
2. The projection is the `owner_chat.spans` service-plane named relay (RFC 0038 D7), answered
   in-process by Switchboard with no MCP tool behind it. It is authenticated by a projected
   workload token with the relay audience; the presented `relationship` daemon narrows the
   allowlist and is not separately proven. The arguments are a strict cursor-only schema.
   Amended 2026-11-14 (bu-jv1moa.11, task 2.1): the earlier choice, sharing the kernel-peer
   composition of the signed-confirm topology, could not ship because that composition is not
   instantiated in any deployment until bu-atc3xi. S1 still activates as one unit at the
   canonical switch, because the reducer does not run while the signed-confirm composition is
   absent (decision 9). Reversible: yes.
3. Explicit per-entry connector facts, over inferring direction from `owner_sender_id`.
   Inference fails for Telegram `@username` endpoints and cannot see forwarding per entry.
   Missing facts fail closed. Reversible: yes.
4. Counterparty from the one-to-one chat identity only. Names in the action are ignored; the
   owner sees the action on the card. Reversible: yes.
5. Known contact = resolves through `resolve_contact_by_channel` to a live entity, not
   `metadata.unidentified`, not the owner. This is the identity model's existing definition
   (`docs/concepts/identity-model.md`), not a new policy.
6. Reject, never truncate, actions over 120 characters. Reversible: yes.
7. One card per candidate, no re-card, 24-hour maximum card timeout bounded by `lapse_at` and
   fixed once in a persisted card snapshot. Re-carding would be new attention policy.
   Reversible: yes.
11. Track skips the ledger write when the fingerprint is already active, instead of relying on
    `create_commitment`'s duplicate path, which would overwrite the existing `evidence_opened`.
    No new ledger seam is needed. Reversible: yes.
8. Carded but unanswered candidates lapse at the recorded 14-day horizon, not at card expiry,
   so a delayed application of an answer accepted before the deadline normally lands.
9. Reducer does not run while the signed-confirm composition is absent, so no owner-chat
   derivative is retained without a surface. Reversible: yes.
10. Confidence on Track stays the detector value (0.9 or 0.85), which is already at or above the
    0.8 surfacing threshold of REQ-commitment-lifecycle-004.

## Owner decision: card cap (recorded 2026-11-02, bu-jv1moa.43)

The run-20 dossier promised "a per-sender, per-day candidate cap and a known-contacts-only rule"
to bound spam from third-party text (its S2 half). No adopted text fixed a number, counting unit,
day boundary or overflow behavior, so the cap was put to the owner. Known-contact eligibility is
settled above from the identity model.

The owner chose option C on 2026-11-02 (bu-jv1moa.43): no per-counterparty card cap in S1;
revisit at S2. The adopted text, carried verbatim in REQ-commitment-lifecycle-017:

> S1 candidate cards are not capped per counterparty; the cap applies to counterparty-authored
> candidates when S2 is specified.

S1 candidates come only from the owner's own words, so card volume follows the owner's own
messaging; a very chatty day produces one card per promise. The S2 specification must define
the cap for counterparty-authored candidates.

Rejected alternatives (not live):

- A: at most 3 cards per counterparty per owner-local day, overflow waiting for a later day's
  allowance and lapsing silently at 14 days.
- B: the same limit with overflow dropped immediately.

## Risks

- Telegram endpoints configured as `@username` today carry no owner direction; after the connector
  change, `Message.out` covers them. Until the connector ships, Telegram yields nothing.
- `message_inbox` partitions dropped before a span is read produce nothing (fail closed).
- Resolved (task 2.1): the projection binds on the live service-plane relay authentication
  (decision 2), not on the kernel-peer companion.
- Resolved (task 2.2): Telegram's single-message envelopes (backfill, ingest retry, chat-id
  fallback) are live and carry no per-entry facts. They are excluded in S1, so those owner
  messages never become candidates; covering them is a follow-up.
