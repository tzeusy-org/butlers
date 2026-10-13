## Context

**Status: DRAFT / NOT ADOPTED.** This design specifies a future contract; the only authorized work here is authoring and opening a draft PR. See proposal.md for motivation and scope.

Baseline: `fb0dad17316c0b20869e7b4de97ea0ba97fbb1a6`. PR #3960 established channel-agnostic conversation anchors/provider resume. PR #4203 completed durable approval-delivery reconciliation. This draft preserves both contracts and RFC 0023's notification-only recovery authority.

The current Dashboard Message Intent Lanes contract requires ACTION REQUESTs to park through the routed owner's normal approval-gated tool before a write or completion claim. `ButlerDaemon._apply_approval_gates` validates registered commands and captures original handlers; `ApprovalsModule._dispatch_approved_action_by_id` invokes the owning executor without re-entering the gate. `execute_approved_action` holds the action row lock across handler execution and terminal/audit persistence. `/approvals` already distinguishes approved-but-unrun from executed.

Missing today: a proposal core tool/card contract, eligibility metadata, verified proposal-to-turn binding, and durable evidence distinguishing a pre-invocation failure from an uncertain post-start effect. An exception or persistence failure can currently leave `approved`/null-result eligible for explicit retry. The existing in-process lock and committed success replay do not prove exactly-once external effects across a crash.

## Goals / Non-Goals

**Goals:** A closed owner eligibility rule, one immutable proposal identity, the existing approval spine, shared attempt fencing, honest receipts, and truthful reversal controls. Concierge continues to read and present; Switchboard retains routing ownership.

**Non-Goals:** No generic execution endpoint, REST/page action registry, new consent exemption, bypass of enabled gate/risk rules, staffer/domain ownership transfer, or Telegram confirmation protocol. No implementation, migration, API/UI change, runtime invocation, deployment, archive, or merge in this PR.

## Decisions

### 1. Eligibility is an intersection at the owning daemon

`chat_eligibility` is trusted metadata on the existing owner-registered command, with the closed values `gated`, `page_authority`, `self_scoped`, and `never`. Missing metadata means `never`. Enabled configuration can disable admission or narrow it to `never`; it cannot create a command, change its owner, or convert one admitted class into another. Concierge, Messenger, arbitrary browser requests, and LLM text cannot register action authority.

All eligible classes require an existing domain butler, a live enabled registered command, valid typed arguments, current permission/risk bounds, and the owner's normal approval spine producing a pending action without invoking the domain effect. Page context is descriptive data. `page_authority` preserves the existing owner's resource authority; `self_scoped` preserves the owner's existing session/resource restriction. Neither class exempts an ACTION REQUEST from the current proposal-before-effect gate.

A tool that cannot park without immediate execution is unavailable for this feature, even if a standing rule ordinarily auto-executes it. This contract does not disable or reinterpret standing rules globally. Future admission must preserve the explicit chat proposal boundary; automatic paths must not consume a newly chat-linked action before its explicit confirmation. No generic wrapper silently creates missing authority.

Rejected: a browser/Concierge REST registry, aliases inferred from a page URL, default eligibility by tool-name shape, or a foreign-butler fallback. These would grant authority outside the ordinary owner contract. This narrower gate-preserving choice is part of the exact draft awaiting owner adoption.

### 2. Reuse the parked action; persist presentation, not permission

The proposed domain-only core tool is `conversation_propose_action(action_id: UUID)`. Its owner is the calling daemon; conversation, user-message, request, and session bindings come from the verified active dashboard turn. There are no caller-supplied owner, actor, schema, tool, conversation, or argument overrides. Only a matching pending, unexecuted approval from that owner/turn is admitted. Invalid admission returns a safe error and creates no proposal or conversation message.

A proposal binds `(owning_butler, action_id, revision, tool_name, canonical_args_digest)` and an immutable proposal UUID. Revision identifies the exact reviewable snapshot, not a new execution authority. Initial revision is 1; a legitimate argument/risk/reversibility change requires a new revision and explicit review before decision. Existing approval edits cannot leave an old Confirm valid. The initial presentation is unique for `(owner, action_id, revision, conversation_id, user_message_id)`; replay returns its stored message/proposal identity. The same pending action shown on another surface stays the same action and decision state.

The owner retains pending-action/attempt/audit authority in its schema. The conversation store retains only a bounded redacted projection and a trusted owner reference. MCP through Switchboard is the cross-butler path; no peer pool, DSN, or table write is granted to Concierge. An owner unavailable during a projection refresh yields unavailable evidence, not a fabricated state.

A projection commit precedes any `action_proposed` SSE event. A crash between owner parking and conversation commit leaves the owner action intact and allows idempotent presentation repair. A lost SSE event is repaired by the ordinary message read, which never dispatches an action.

### 3. Exact proposed projection and decision shapes

Conversation message reads add nullable `action_proposals`; legacy/plain-text messages keep null. Each projected card carries:

| Field | Contract |
| --- | --- |
| `proposal_id`, `revision` | Immutable UUID and positive revision for this stored snapshot |
| `owning_butler`, `action_id`, `tool_name` | Server-resolved owner and existing command identity; never authority on their own |
| `conversation_id`, `user_message_id`, `request_id`, `session_id` | Verified source binding; never browser/LLM assertions |
| `summary`, `why`, `evidence`, `impact` | Bounded, redacted display data; links/read evidence confer no authority |
| `eligibility` | One of the three admitted classes |
| `reversibility` | `reversible`, `compensable`, or `irreversible`; unsupported/unknown is conservatively irreversible |
| `approval_status`, `expires_at` | Existing pending-action lifecycle value and owner expiry, kept separate from effect truth; presentation cannot extend expiry |
| `receipt` | Latest durable `{state, reason, occurred_at, attempt_ref, result_ref, safe_detail}` or null before evidence exists |
| `controls` | Closed boolean fields `confirm`, `deny`, `inspect`, `retry`, `undo`, `compensate`, `irreversible_ack_required`; all effect controls false when source/state is unknown |

Projection text limits are measured in Unicode code points: summary 240; why and impact 2,000 each; safe_detail 1,000; at most 10 evidence references of 512 each. References resolve through existing server-authorized read surfaces; they cannot carry executable URLs or raw provider payloads. Owner field-sensitivity redaction runs before storage/streaming. A still-oversized or unredactable projection is refused before conversation persistence, with the parked owner action preserved. IDs are typed UUIDs, revision is a positive integer, and command/owner names are taken from the registry.

Closed receipt states and allowed reasons: `queued` (`awaiting_approval`, `owner_unavailable`, `command_unavailable`, `not_started`, `running`); `failed` (`rejected`, `expired`, `abandoned`, `stale_proposal`, `proven_no_effect`); `executed` (`execution_succeeded`); `ambiguous` (`effect_unresolved`). Only applicable non-secret references are populated; missing evidence never synthesizes a receipt.

`action_proposed` and `action_receipt` SSE events carry the stored message/proposal identity and current revision/read projection, after commit. The browser deduplicates by proposal UUID/revision and refetches authoritative history after a reconnect. The existing turn completion, citation attribution, anchor, and resume semantics remain unchanged.

Chat Confirm/Deny use the existing authenticated approval decision family and hooks. Confirm adds `{chat_proposal_ref: {proposal_id, revision}, irreversible_ack?: "CONFIRM"}` to the existing approve payload; it never sends actor/owner/tool/args authority. Deny adds the same `chat_proposal_ref` to its existing optional reason payload, with no irreversible acknowledgment because it cannot execute an effect. For a chat card, edits are not part of Confirm. Edits through the ordinary approval dossier invalidate the prior digest/revision and require an updated reviewed card. The server resolves the proposal, authenticated principal, owner, and locked action; duplicate/foreign UUIDs cannot use the first schema found by a fan-out query. A concurrent loser returns current state and stale/transition failure, with no second dispatch.

A chat-linked irreversible proposal requires the exact typed `CONFIRM` acknowledgment and current snapshot on the shared manual decision seam. Other decision surfaces must satisfy that same predicate or direct the owner to a compatible detail surface; an old callback or browser client cannot bypass it. This is an additional confirmation predicate on the existing decision, not an alternative approval state machine or a grant to the presentation service.

### 4. Durable owner attempt evidence fences all retry entrances

For chat-linked actions, a durable owning-executor attempt fence is committed before calling the original handler. It binds the approved action, reviewed argument digest, command registration/config generation, and an attempt identity. Claiming and consulting it are shared by manual dispatch, auto-approved execution where otherwise authorized, owning-daemon MCP dispatch, and both approval retry routes. A process-local lock is an optimization, not the cross-process authority.

The fence has the closed evidence states `not_started`, `started`, `proven_no_effect`, `succeeded`, and `unresolved`. Only the owning registered executor/adapter can append outcome evidence. Once `started` is committed, a crash, timeout, unknown provider acknowledgment, partial effect, or failed result/audit commit yields `unresolved` until trusted evidence proves otherwise. A stale attempt cannot clear a successor's fence or overwrite terminal evidence.

`executed` requires the stored success result and immutable success audit append. `failed` requires durable evidence proving no effect, or a rejection/expiry/abandonment before invocation. An approved action with no reachable owner or a handler not yet started is `queued`, not executed. A running invocation is `queued`/`running`; loss of its executor becomes `ambiguous`/`effect_unresolved`.

Approved/null-result is necessary but insufficient for retry of a chat-linked action. A new attempt is allowed only with owner proof of no effect, or an existing registered provider/command idempotency contract proving the same-key replay harmless. That proof is specific to the same command, arguments, resource, and attempt; the UI/LLM cannot assert it. Otherwise every entrance refuses dispatch and preserves ambiguous evidence. No new approval lifecycle status is introduced, and a retry refusal does not erase the existing approved row.

This is a required future shared-executor amendment, not a claim that present source already fences crashes. The implementation task must cover both existing Retry routes and the owning MCP dispatcher; a chat-only check would be bypassable. Existing notification delivery intents remain notification-only and cannot resolve execution uncertainty.

### 5. One decision, truthful reversal

Approval CAS/row locking is the decision boundary shared with `/approvals`. Decision, verified source attribution, and immutable audit commit together; a downstream execution receipt is a separate owner result. Expiry, rejection, or a competing decision never invokes the tool. Stop, conversation closure, SSE disconnect, or card removal cannot retract an approved/executing effect and cannot claim to do so.

`reversible` requires an existing same-owner registered inverse command plus current resource/version preconditions. Undo proposes that inverse through its normal permission/risk/approval path; it never edits history or toggles the original action backward. `compensable` offers a separately described compensation proposal with explicit residual effects and its own decision/audit, never a control labeled Undo. `irreversible` has no Undo; the shared decision requires the typed second acknowledgment described above. Unknown reversal support is irreversible. Ambiguous originals offer investigation/read evidence, not reversal or blind retry.

The approval page's existing pre-send undo window only cancels a scheduled decision before it reaches the backend. It is distinct from reversal of an executed domain effect. Shared hooks retain that delay/cancel behavior and cards label it accurately.

### 6. Concierge remains read-only in every contract

The full canonical Read-Only Tool Surface requirement is carried forward and extended. `roster/concierge/MANIFESTO.md` already excludes write tools, domain data, Operator controls, and peer-schema access; `butler.toml` remains staffer-typed with dashboard_read/self_healing only. `cross_butler_access = ["*"]` is not action authority. The staffer routing exclusion and RFC 0030 read exception remain binding.

No roster config or manifesto is changed by this draft. Future source alignment must preserve these boundaries in the capability, infrastructure contract, runtime registry, and prompt documents. Chat may classify/route through the existing Switchboard and present owner projections; Concierge may supply read evidence/presentation, never own a pending action, execute/approve it, or register the proposed action tool. Core session logging/telemetry does not become domain write authority.

## Current-baseline and overlap reconciliation

| Surface | Treatment |
| --- | --- |
| Channel-Agnostic Conversation Anchor / Provider Resume Ledger (PR #3960) | Read and preserved; no MODIFIED delta is forked |
| RFC 0023 / durable approval-delivery recovery (PR #4203) | Read and preserved; worker authority, lifecycle fencing, and safe history isolation unchanged |
| Concierge Read-Only Tool Surface | Complete current baseline block plus additive restrictions; no competing active delta found |
| Module Shared Executor Path | Complete current baseline block plus chat-linked attempt qualification; no competing active delta found |
| Dashboard Safe retry refreshes confirmed approval state | Complete current baseline block plus shared chat-linked fence predicate; no competing active delta found |
| Active durable-dashboard-terminal-action-recovery Conversation Pydantic Response Models | Add a uniquely named integration requirement; do not fork/rewrite the active block |
| Active define-dashboard-fast-answer-runtime | No question-lane/fast-answer requirement or runtime source changes |

The overwrite guard must pass without updating its ratchet. If these requirements move before adoption/landing, rebuild the three MODIFIED blocks from the new full baseline, retain every scenario/body, and repeat strict validation and semantic review of the resulting revision.

## Future real-seam verification

These are required future checks, not execution evidence from this authoring PR. Each invariant has one authoritative gate species; smaller unit checks may diagnose but cannot substitute for the named proof.

| Invariant / requirements | One gate species | Actual seam and required observation |
| --- | --- | --- |
| Closed owner eligibility / 001 | Owning-daemon contract test | `ButlerDaemon._apply_approval_gates`, module tool metadata, `modules/approvals/command_contracts.py`, `tests/daemon/test_approval_metadata_wiring.py` and `test_non_messenger_approval_command_contracts.py`: enumerate actual registered handlers/config; reject absent/disabled/foreign/never/auto-executing admission before row insertion |
| Immutable binding / 002 | Real-Postgres action contract test | Owner pending_actions plus proposed projection; locked owner command/args/revision mismatch rejects stale edits and same UUID in different owner schemas cannot cross-dispatch |
| Verified provenance / 003 | Authenticated API authorization test | `core_tools/_conversation_reply.py` routed/session context, `api/audit_emit.py`, `routers/approvals.py::approve_owning_action`, `tests/api/test_api_approvals.py`: forged/cross-turn actor/source/owner fields fail before decision/projection persistence |
| Durable projection/replay / 004 | Real-Postgres conversation composition test | `api/conversations.py`, `routers/conversations.py` SSE/read path, `tests/integration/test_conversation_reply_db.py`: park -> commit projection -> lost SSE -> history reload returns one card and zero handler calls |
| One shared decision / 005 | Real-Postgres concurrent approval test | `modules/approvals/operations.py`, `routers/approvals.py`, `ApprovalsModule._dispatch_approved_action_by_id`: chat approve races /approvals deny/expiry/edit; exactly one valid transition/audit and no foreign dispatch |
| Honest outcome/fence / 006 | Owning-executor crash-boundary integration test | `modules/approvals/executor.py`, daemon original handler, both retry routes, owner persistence/approval_events: terminate after attempt-start/effect/before result commit; fresh process shows ambiguous and cannot repeat effect; committed success replays result |
| Reversal/compensation / 007 | Real registered-owner contract test | Existing owner's forward/inverse handlers and resource preconditions: supported inverse parks new approval; changed resource refuses; compensation records residuals; no inverse is invented |
| Card truth / 008 | Frontend interaction test | `MessageThread.tsx`, `use-approval-decisions.ts`, approval dossier: keyboard/focus/typed confirm, concurrent stale update, pending delay, queued/failed/ambiguous labels, supported controls; no optimistic execution |
| Compatibility/rollback / 009 | Real-Postgres migration outcome test | Conversation reads, owning action/attempt/audit retention and rollback: legacy null projections work; disabling new admission retains unresolved rows and evidence |
| Concierge authority / Concierge delta | Actual-roster registry contract test | `roster/concierge/{MANIFESTO.md,butler.toml,AGENTS.md}`, dashboard_read registry, staffer classification: zero action/write registrations and no domain mutation calls |

Future tests cite the exact REQ IDs. They are derived and scoped after adoption using the repository test ladder, fresh inventory/budgets, and terminal hosted CI. This PR adds/runs no test bodies.

## Risks / Trade-offs

- **External effects are not database transactions.** Conservative ambiguous evidence can block retry even when a crash happened before the call. Trusted owner evidence or an existing same-key idempotency contract is required to recover.
- **Existing tools can be unavailable for chat.** Missing safe park/owner metadata cannot be fixed by a permissive presentation fallback; surface the unavailable command honestly.
- **Multiple surfaces can become stale.** Revision/digest and shared action CAS prevent stale cards/clients from acting on changed arguments; response refresh retains evidence.
- **Redaction can reduce explanation detail.** Bounded projections expose safe summaries and references; raw secrets/approval payloads never enter generic conversation history or telemetry.
- **Owner absence delays execution.** Approval stays queued under its original owner; another butler does not inherit authority.

## Adoption and migration plan

1. Independent reviewer performs semantic/security review of this exact draft, including current-baseline compatibility and every bypass/race seam above. Record reviewed commit/tree, findings and dispositions. Green syntax/CI is not this review.
2. Owner explicitly adopts the exact reviewed contract revision (`chat-actions-v1-scope`, this changeset, reviewed commit/tree). Any substantive edit invalidates the adoption receipt and requires re-review/re-adoption. Retain status quo until that receipt exists.
3. Coordinator lands the adopted specification through the normal PR/merge queue. Only after review, exact adoption, and landing may `bu-0ynlk.8` be made runnable. This draft PR does not authorize merge.
4. Future implementation proceeds additively: owner representation/attempt evidence and nullable projection; propagation/SSE; shared enforcement; card affordances. Keep new admission off until every executing gate passes and the designated owner authorizes activation. No activation is attempted here.
5. Rollback disables new proposal admission/controls first. Preserve parked actions, unresolved attempt fences, receipt/audit evidence, and existing legacy messages; do not erase or retry them as cleanup. Existing owning approval readers remain available within their normal authority.
