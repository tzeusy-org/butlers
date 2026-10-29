# Owner-control boundary and adoption matrix

Status: concrete .18 amendment draft; exact-text independent conformance is
pending. This matrix traces the governing clauses; it is not a second policy
schema, runtime release or live owner setting. Source baseline: actual public
`2de13c77e2dcbffc4f39e69804580b55d50e9676`, including adopted .19 S1–S6.
The earlier assessment that treated `839ac83e` as current main is superseded;
that older object does not supply this draft's authority.

## Governing text and recorded choices

- [Security Approval Gates](../../../about/heart-and-soul/security.md#approval-gates)
  and [RFC 0038 D4/D7/D13](../../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md)
  define the limits; S1–S6 remain the structural, label, currentness and canonical
  subject contracts. Region means the capability's catalog `owner_domain`.
- [RFC 0021 one-tap decisions](../../../about/legends-and-lore/rfcs/0021-decision-loop-one-tap-approvals-and-decision-memory.md)
  retains the current exact stored-call and signed-callback behavior.
- [Notify Tool Registration, REQ-core-notify-038](specs/core-notify/spec.md),
  [Standing Rules CRUD and Matching, REQ-module-approvals-003](specs/module-approvals/spec.md),
  and [REQ-capability-authorization-006/014/020](specs/capability-authorization/spec.md)
  carry the observable successors. All other compatible requirement/scenario
  headings remain. REQ-core-notify-038 assigns trace identity to the existing
  registration requirement; it does not create another notify requirement.
- `bu-u33sb7` supplies tighten-only owner law, deterministic urgency and reason
  presentation; `bu-k3wfma` supplies the final narrow step-up list and independent
  dashboard factor; `bu-o3638f` supplies slot-principal and sensitive-category
  boundaries. Canonical `bu-dhloi1.18` binds nonce/currentness/race behavior.
- The six S1 categories remain `health`, `finance`, `location`, `routine`,
  `relationship_notes`, `credentials`. D6 typed-value and cited-label choices
  remain held; these clauses do not declassify a slot or a read.

## Cross-surface cases

Every row names future executing proof, not evidence that this spec-only task
ran runtime tests or changed a setting. A later constraint cannot undo an
earlier refusal. `always_ask` parks only an otherwise permitted exact effect.

| Row | Case and required decision | Holding stage / governing clauses | Recorded choice; implementation owner; future proof |
| --- | --- | --- | --- |
| B01 | Missing/stale invocation binding or unknown origin. No fabricated owner/obligation authority; preserve existing fail-closed origin/grant/exposure fences. Urgency fallback cannot authorize a call. | Origin/catalog/grant before policy or delivery. RFC0038 D5/D7/S1; REQ-capability-authorization-003/004 | existing authority preserved; .3 delivery, .10 policy, existing broker; Existing broker origin/currentness and attention suppression controls |
| B02 | Owner-started or third-party model requests high priority. Model-presentable notify has no priority choice; no quiet/sleep bypass from model labels, text or owner-goal provenance. | Urgency producer classification before suppression. Notify Tool Registration; RFC0038 D7 | u33sb7 q3; .3 notify pre-switch, .22 first-switch delivery; test_attention_ledger_suppression.py; actual notify public schema/dispatch |
| B03 | Allowlisted deterministic safety producer. Only genuine code-set producer may claim the authorized suppression exception; every capability, recipient/category, grant and effect restriction still applies. | Trusted producer then independent authorization/delivery fences. Notify Tool Registration; RFC0038 D7 | u33sb7 q3; .3, .22 existing producer owners; Real registered safety producer positive plus spoofed-source refusal |
| B04 | Owner break-through rule or owner-creation flagged obligation. Only current persisted owner-authored rule/creation flag confers its exact urgency exception; no later model self-upgrade. | Server-verified rule/creation provenance before suppression. Notify Tool Registration; RFC0038 D7 | u33sb7 q3; .3, .22 existing obligation owner; Actual persisted provenance positive/stale-revoked/absent negatives |
| B05 | Ordinary approve/reject/edit/dispatch. One tap after existing owner, current action/digest and authorization checks; no blanket D4 dashboard step-up. | Owning exact stored-action transition. REQ-capability-authorization-014; RFC0038 D13; RFC0021 | k3wfma final narrow boundary; .5 approvals, dashboard and Telegram; test_prepared_execution.py; test_approval_callback_tokens.py |
| B06 | Standing-rule creation or promotion. Dashboard step-up with current owner/action-bound nonce; Telegram identity alone cannot create/promote a rule. Rule generation still cannot exceed catalog/exposure/category constraints. | Owner-only rule mutation before durable transition. REQ-module-approvals-003; RFC0038 D13 | k3wfma first slice; .5 rule controls; test_rule_use_claim.py; actual owner rule-create/promote endpoint |
| B07 | Vouch, Contain or mode loosening. Same listed-action step-up contract; no expansion to every owner-only action. Preserve their own implementation/release prerequisites. | Existing owner action boundary before mutation. REQ-capability-authorization-014; RFC0038 D13 | k3wfma second slice; u33sb7/modes; .5 shared verifier, existing Vouch/Contain owners, .21 mode loosening; Later actual action consumer controls; no action executed in this spec task |
| B08 | Missing/invalid/expired/revoked nonce or absent second-factor owner session. Refuse the step-up-requiring transition without consuming another action or invoking an effect. Credential custody/registration unchanged. | Action-bound step-up verification and atomic mutation. REQ-module-approvals-003; REQ-capability-authorization-014; RFC0038 D13 | k3wfma; canonical .18 nonce contract; .5 dashboard verifier, Telegram handoff; Wrong-owner/action/session/expiry/revocation actual transition controls |
| B09 | Valid nonce first use, replay, retry or competing decisions. A valid bound nonce is single-use atomically with the authorized decision; one winner, deterministic readback/retry, no second rule/effect. | Shared owner decision transaction/CAS. REQ-module-approvals-003; RFC0038 D13/S6 | canonical .18 concurrency contract; .5 both surfaces; Concurrent consume/approve-reject and stable outcome readback |
| B10 | Changed action, recipient or argument digest after nonce issuance. Old nonce cannot authorize the changed step-up action. Ordinary edit remains one tap where otherwise allowed; later rule creation/promotion binds its current exact result. | Raw action/digest and current version checks before mutation. REQ-module-approvals-003; REQ-capability-authorization-010; RFC0021/D13 | canonical .18 changed-digest contract; .5 approvals/rules; test_prepared_execution.py; old/new digest and edited-recipient controls |
| B11 | Owner never for capability, region or effect class. Refuse matched otherwise-grantable call; current catalog scope resolution cannot grant a capability or loosen another term. | Owner-law stage after origin/catalog/grant/exposure. REQ-capability-authorization-020; RFC0038 D4/S1 | u33sb7 q1; .10 owner policy, broker; Actual owner policy persistence/CAS and per-call matching for all three selector dimensions |
| B12 | Owner always_ask for otherwise permitted exact call. Park exact call; never grants, clears exposure, replaces selection evidence or satisfies always-per-event. An otherwise refused call stays refused. | Owner-law stage plus owning approval fence. REQ-capability-authorization-020; REQ-capability-authorization-017/018 | u33sb7 q1; .10, .5 broker/approvals; Actual park/refuse distinction; stored exact action replay |
| B13 | Unreadable policy or expired restriction. Unreadable policy refuses. Expiry removes only its own restriction; revoked origin/grant or other restrictions stay revoked. | Policy read and same-call fence. REQ-capability-authorization-020; RFC0038 S1 | u33sb7 q1 within adopted S1; .10; Policy unavailable/expired/other-term intersection controls |
| B14 | Policy tightening races an in-flight call or stale policy retry. Expected-version CAS and existing effect locks serialize the next call; all constraining versions recorded; no retroactive rewrite of committed effects or stale overwrite. | Policy CAS then current effect fence. REQ-capability-authorization-020; RFC0038 D7/S1 | canonical .18 in-flight contract; .10, broker currentness; Concurrent tighten/next-call/readback; same action retry |
| B15 | Owner policy, mode and health restrictions intersect. Use adopted order catalog/origin/grant → exposure/category/slot → owner law → mode tightening → health/admission. A later term cannot undo an earlier refusal. Unknown health is not healthy and does not invent authority; retain exact existing underlying health restrictions. | Each named stage; receipt names all actual restricting terms. REQ-capability-authorization-020; RFC0038 S1/S6 | u33sb7 q1/q10; adopted .19; .10, .21 existing mode owner, .13 receipt rendering; Real multi-term receipt and holding-stage oracle; no new health policy |
| B16 | Owner card/status on private versus shared-room surface. Show actual narrowing term/version on cards and /status subject to the existing privacy redaction for mode/health; no reason string or ACK creates authority. | Existing receipt projection and surface privacy. REQ-capability-authorization-020; RFC0038 S1/S6 | u33sb7 q10; .10 typed receipts, .13 renderer, dashboard/Telegram; Actual typed receipt projection plus shared-surface privacy controls |
| B17 | Slot filled by owner/device/code with valid provenance. Eligible principal is necessary, not sufficient; retain exact recipient/resource/type and every existing D6/exposure rule. | Standing-rule match before rule-use claim. REQ-module-approvals-003; REQ-capability-authorization-006/019 | o3638f q1; .5 rule constraints, .6 label consumer; Actual rule-match provenance/type/refusal controls |
| B18 | Rule creation card explicitly permits its own recipient to fill one slot. Only that rule own recipient may fill that named slot; no arbitrary third-party or unrelated email and no implicit permission for other slots. | Verified principal plus explicit persisted slot permission. REQ-module-approvals-003; REQ-capability-authorization-006 | o3638f q1 narrow exception; .5, .6; Same-recipient positive; unrelated-principal/other-slot negatives |
| B19 | Slot principal mismatch or unknown/missing provenance. Rule does not authorize the call; preserve existing refuse-or-park policy for the exact call. Do not silently coerce caller labels into trusted principals. | Standing-rule eligibility, before use consumption/effect. REQ-module-approvals-003; REQ-capability-authorization-006/019 | o3638f q1; .5, .6; Mismatch/unknown provenance cannot consume rule or dispatch |
| B20 | Non-owner rule send after sensitive-category reads, category absent. Standing rule cannot fire unless it names all applicable read categories. Retain any independently permitted per-event parked path; do not transform absence into new send authority. | Monotonic invocation category and rule-match fence. REQ-module-approvals-003; REQ-capability-authorization-006/016/019 | o3638f q2; .5, .6 broker, non-owner delivery; Each of health/finance/location/routine/relationship_notes/credentials; multiple-category union |
| B21 | Rule explicitly names category. Category permission is only one necessary condition; never clears exposure/taint, relaxes typed-value D6, satisfies selection or overrides always-per-event/transact-disabled/park_only. | All existing policy intersections still apply. REQ-module-approvals-003; REQ-capability-authorization-006/015/017/018/019 | o3638f q2/q3; held bu-4c7nvp.1.1; .5, .6, broker; Named-category positive only within lawful authority; exposed/always-per-event negatives |
| B22 | Stop/revocation races rule use or approved replay. Current epoch/grant/current-policy/digest checks and atomic rule/action/receipt fences remain; neither nonce nor urgency bypasses Stop or replays an ambiguous effect. | Existing same-transaction currentness and dispatch outcome. REQ-module-approvals-002/004/005; REQ-capability-authorization-010/011 | existing D7/D13 fences preserved; .5, existing broker/domain owners; test_rule_use_claim.py; test_prepared_execution.py; Stop/concurrent replay |
| B23 | Presentation ACK, fallback or repeated surface action. ACK changes presentation only, not approval/step-up; same canonical subject/action/nonce has one decision across dashboard/Telegram. Preserve current S6 subject tokens and generation binding. | Canonical delivery subject/decision fence. REQ-core-notify-036; RFC0038 S6; RFC0021 | u33sb7/modes preserved; canonical .18 races; .5, .22, .13; Existing subject token/callback replay and cross-surface winner controls |

## Consumer seams and separation

Existing future proof seams are
[`test_attention_ledger_suppression.py`](../../../tests/core/test_attention_ledger_suppression.py),
[`test_approval_callback_tokens.py`](../../../tests/core/test_approval_callback_tokens.py),
[`test_rule_use_claim.py`](../../../tests/modules/approvals/test_rule_use_claim.py),
and [`test_prepared_execution.py`](../../../tests/modules/approvals/test_prepared_execution.py).
They must exercise actual producer/transition consumers; this document adds no
mirror tests or test-admission machinery. Tests: +0 ~0 -0.

- `.3` owns pre-switch notify urgency; `.22` owns first-switch delivery. Both
  must remove model-chosen high priority while preserving genuine verified
  exceptions and existing recipient approval.
- `.5` owns reusable dashboard nonce verification and rule creation/promotion.
  Existing Vouch, Contain and `.21` mode-loosening owners consume that boundary
  later. Ordinary approve/reject/edit/dispatch remain one tap. Suggestions and
  promotion counts do not authorize rule persistence without step-up.
- `.10` owns persisted owner policy, ordered per-call matching and typed reason
  receipts, consuming existing S1 fields and `.6` labels. Region/class selectors
  are evaluated against the current catalog; they do not mint capabilities.
- Existing `.13`/S6 surfaces present reasons with their privacy limits; mode
  storage/first-switch behavior remains with the existing mode owners. No
  extra mode, host, runner or deployment gate is created by this matrix.

## Exact-text adoption and delivery boundary

The owner amendment at **2026-10-21T03:46:10Z** in the repository-local
`.tmp/owner-questionnaire-2026-10-21/owner-questionnaire.md:8` and
`apply-log.md:76–82` approves text conforming to the recorded answers after an
independent reviewer checks that exact text. The primary records stay outside
the tracked source; the author/review receipts bind their hashes and the seven
before/final blobs. A draft, scheduling release or general decision is not a
substitute for that exact written conformance record. Text beyond the recorded
boundary returns to the owner; conforming text does not repeat the same owner
question. The coordinator records the exact clause/answer matrix, source
commit/tree/blobs and DIFFERENT conformance verdict before adoption/delivery.

The bounded amendment changes security's step-up paragraph, RFC 0038 D4 owner
selectors/slot/category policy, D7 urgency/intersection receipts, D13 step-up
and nonce/race clauses, RFC 0021's one-tap qualifier and the three linked active
specs. Model `priority` and unconditional model-high immediate delivery are
intentional changed-contract successors. The old broader D4-wide step-up and
owner-started-model priority bypass are excluded. S1–S6, always-per-event,
selection, labels, recipient resolution, Stop and bounded rule-use semantics
remain intact. Foreign D20 research/sandbox/budget text is outside this delta.

No runtime, credential, passkey registration, vendor/SMS service, owner rule,
mode setting, provider send, deployment, switch or held owner act is authorized
or performed. Exact normative conformance and ordinary PR/protected delivery
remain separate receipts.
