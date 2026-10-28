# Signed confirm interaction on the goal runtime

## Status and authority

Phase A of bu-ow5a4 was prepared against source baseline 6b5f474c9 on 2026-10-28.
The owner adopted independently reviewed commit
`537046f73f9d7d41ed70af0bb662241867c9dc25` before Phase B implementation.
The source map below records that baseline; current implementation custody and activation
limits are documented in `docs/runtime/signed-confirms.md`.
The original bounded-choice, durable answer, recipient identity, replay, timeout,
concurrency, channel fallback, and approval-separation outcomes are retained.
Collision-dispositions row 56 and RFC 0038 D7/D13/D18 govern the replacement.
The task also inherits simple fact/claim/quote request-card rendering from bu-jv1moa.27.

## Source reconciliation

| Concern | Current source | Custody and implementation step |
| --- | --- | --- |
| Signing convention | `src/butlers/core/approval_callbacks.py::sign_callback_payload` | Reuse HMAC primitive with `cfm1` domain separation; preserve apr1 format and authority. |
| Connector identity | `src/butlers/connectors/telegram_bot.py` approval callback handler | Reuse authenticated Telegram sender/recipient lookup; verify confirm token before record lookup; never borrow owner API keys. |
| API admission | `src/butlers/api/owner_auth/http.py::OwnerAuthMiddleware`; `src/butlers/api/middleware.py::_is_approval_callback_route` | Add an exact independently authenticated confirm service route. Do not open arbitrary API routes or turn connector authority into a dashboard principal. |
| Goal admission | `src/butlers/core/goal_admission.py::GoalAdmission.admit_submission`, `EvidenceProvider`, `SourceRef` | Implement trusted confirm-source evidence reader and durable source identity. Model envelopes cannot construct Evidence. |
| Prompt binding | `src/butlers/core/goal_ledger.py::GoalLedger.reply_hint`; `goal_admission.py::_admit` | Use server-held delivery references, recipient entity/channel and input expiry. Preserve F3 terminal-goal and exposure handling. Existing admission is delivered but consumer wiring remains required. |
| Registered sinks | No confirm registry exists | Add closed code-owned producer-to-sink registrations, immutable references and durable application receipts. This is new production code, not an existing helper to call. |
| Chronicler consumer | `src/butlers/chronicler/gap_interview_job.py::ask_gap_interview`; `gap_interview.py::resolve_gap_interview_callback` | bu-jjy9p consumes the new service and adds domain idempotency keyed by confirm ID; retain domain mutation inside Chronicler. |
| Simple request cards | `src/butlers/core/delivery_rendering.py`; `tests/modules/approvals/test_approval_card_exact_call.py` | Reuse authoritative fact/assistant claim/inert quote conventions in Telegram and dashboard; do not introduce effect-set approval semantics. |
| Existing seams | `tests/switchboard/test_goal_admission.py`; `tests/connectors/test_telegram_bot_approval_callbacks.py` | Extend actual source seams. Removed decision_callbacks/decision_prompts APIs are not available. |

## Decisions proposed for exact-artifact adoption

### D1: Service authority and immutable prompts

Messenger owns delivery. The confirm service owns a restricted durable prompt/answer store;
connectors authenticate/forward and do not mutate that store or domain schemas.
The logical record is `public.pending_confirms`, with forced role restrictions evaluated in
migration tests; public placement is not a privilege boundary. Persist immutable question,
ordered options, resolved recipient sender/entity/chat/channel, producer, expiry, prompt
version, delivery reference, and optional goal/effect or registered sink reference.
Do not persist arbitrary sink URLs or model-selected goal authority.

The producer selects one mode: conversational goal answer or registered deterministic
sink. Deterministic service code need not create a goal merely to ask a question.
A goal-originated delivery retains its lineage through the normal authenticated producer.
Non-owner sends retain per-event approval or an owner-created eligible standing rule;
confirm does not create another standing-rule kind.

### D2: Bounds, signing and identity

Offer 2-4 immutable unique nonempty values and bounded labels. This follows bu-ow5a4's
current dispatch contract, narrowing the stale draft's 2-8 range; adoption must include
this explicit change. Keep timeout [30, 86400] seconds with default 600 and optional
on_timeout_value membership validation. Email uses numbered plain text, no resolvable row.

Use `cfm1:<uuid>:<index>:<16-hex-HMAC>` (60 bytes for a single-digit index), signing
`cfm1|<uuid>|<index>|<recipient_chat_id>` through the existing HMAC helper. The connector
has the chat from its authenticated Telegram update, so it can verify before any DB read.
Do not require requested_at from an unread record to perform that pre-read verification,
which was impossible in the previous draft. A UUID identifies one immutable version;
editing question/options requires a new UUID and invalidating the old record. The service
rechecks the signature and sender plus chat against its stored recipient binding.
A group chat alone is not proof of the intended recipient. apr1 and cgi are not signed
confirm tokens and are rejected by the prefix parser, not described as sharing a verifier.
No new secret or third cryptographic implementation is needed.

### D3: Answer acceptance, application and rendering are distinct

Lock/conditionally transition the pending prompt once; record its first choice and a durable
dispatch obligation atomically. Concurrent choices cannot both win. A retry never changes
the accepted choice. Expiry competes under the same lock and cannot override an answer;
expiry is a system outcome, not fabricated recipient speech. Acceptance rechecks
`expires_at > clock_timestamp()` after acquiring the row lock, so a late tap cannot win
merely because the sweep is delayed. Such a tap records the single expiry obligation and
returns an expired toast without accepting or applying a choice.

Goal dispatch uses a stable source identity and admission's existing attempt/source dedupe.
Domain dispatch uses a closed registration and stable confirm ID. The domain endpoint
must commit its idempotency receipt atomically with its mutation; transport cannot promise
exactly-once RPC. If the reply is lost, reconcile/retry that same ID. Record application
pending/applied/error separately from answered/expired. A failure toast cannot claim
application success. Retry after restart resumes the same obligation and choice.
Message-edit failure retries only rendering, never application or prompt sending.

### D4: Goal admission replaces pinned routing

Trusted source evidence supplies reply_delivery, verified owner entity/channel and exposure;
only `GoalAdmission.admit_submission` decides join versus new intent. An accepting live
goal can join via the exact stored delivery reference; terminal/nonjoinable goals follow
F3 new-owner-goal handling with inherited exposure. Never revive a goal, use proximity,
or trust a model-supplied goal ID. Non-owner replies remain third_party/exposed.
Timeout is a system occurrence, not an owner message. No ingest.v1/pinned_target bridge or
revived decision_request API is introduced. Goal-runtime emission remains dark until the
canonical switch; disabled use returns an explicit unavailable result.

### D5: Domain answers cannot approve effects

Registered sinks are deterministic fixed endpoints; no model, label or callback selects a
sink URL or grants. Chronicler may apply the existing correction semantics in its own
service transaction. Neither resolution nor a sink can approve/reject pending actions,
invoke an approved-action executor, or mint grants. Subsequent goal effects require their
normal broker/approval checks. Approval prompt transport and apr1 remain separate.

### D6: One stored card across Telegram and dashboard

Both surfaces consume the same immutable question/options and server facts. Clearly label
assistant claims; quotes are escaped/inert. Labels and claims cannot change recipient,
prompt, sink or authority. Dashboard answers use owner auth and the same resolver; rendering
text is not callback identity. Persist the chosen label and remove keyboard on resolution
or expiry; distinguish application pending/error from applied. Unknown HTTP/commit outcomes
remain unknown until reconciled. Do not report accepted transport as applied domain state.

## Phase B implementation plan under the same bead

1. After adoption, add the restricted prompt/answer/dispatch schema and role migration.
   Test migrated PostgreSQL, real SET ROLE identities, immutable prompt fields and races.
2. Add confirm schema validation, token helper and producer service/registry; reuse current
   HMAC and identity primitives. Add immutable rendering read model for both surfaces.
3. Wire Messenger send/edit receipt custody, quiet-hours behavior and channel fallback.
   Use durable delivery idempotency; reconcile ambiguous delivery without blind resend.
4. Wire connector and mounted API auth, trusted evidence reader and admission adapter.
   Verify real middleware admission, wrong sender/chat, forged token and terminal goal.
5. Add recoverable sink dispatch and expiry/render workers with stable identities; prove
   crash-after-domain-commit and restart/replay behavior through actual service seams.
6. Wire dashboard read/answer and Telegram rendering using shared fact/claim/quote cases.
   Run scoped tests, planner, PG/contract/API/roster lanes and hosted CI per AGENTS.md.
7. Only then bu-jjy9p registers Chronicler, proves parity and retires cgi/direct sends.
   Its owner-directed bu-24lu6.8 closeout dependency remains in force.

No fixed future RFC number is reserved; 0024 is already historical. Any additional RFC
needed by implementation must use the repository's actual current allocation.

## Review and adoption gates

Independent review must examine the exact commit and source custody, all preserved outcomes,
new sink/application semantics, identity, and the goal-runtime switch boundary. Then the
owner must adopt that exact reviewed artifact before Phase B. Any semantic contract edit
requires renewed review/adoption. That adoption is recorded above; Phase B remains subject
to independent implementation review and the repository's verification gates. Runtime
emission stays dark, with no deployment, live message or callback replay. The older live
approval E2E remains a separate owner action.
