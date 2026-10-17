## Purpose

Defines future chat action proposals and truthful receipts whose authority remains with the existing owning domain butler, while Concierge and the dashboard only route and present verified projections. This is an unadopted draft; review, exact owner adoption, and landing precede implementation.

## ADDED Requirements

### Requirement: Closed Existing-Domain Action Eligibility

A chat action SHALL be eligible only when its server-resolved existing domain butler owns the resource and a currently registered, enabled original command, its typed arguments are valid, its ordinary permission/risk/approval bounds permit proposal admission, and its trusted `chat_eligibility` is `gated`, `page_authority`, or `self_scoped`. Missing metadata SHALL mean `never`. Every eligible class MUST park a pending action through its existing owner's normal approval spine before a domain effect; the class, a page URL, chat text, or a client request MUST NOT create authority or exempt approval. Concierge and other staffers SHALL NOT own or execute these actions.

ID: REQ-chat-domain-actions-001
Source: bu-ubqnma clarified chat-actions-v1-scope; heart-and-soul/vision.md Rules 3, 6; heart-and-soul/security.md Approval Gates; dashboard-conversations Dashboard Message Intent Lanes; butler-concierge Read-Only Tool Surface
Scope: v1-mandatory

#### Scenario: Gated command remains with its owner

- **WHEN** a domain butler proposes its enabled registered `gated` command for the verified current action-request turn
- **THEN** its normal gate parks a pending action with zero handler invocations before confirmation
- **AND** the eventual approved action is executed only by that owner through its original registered handler and shared executor.

#### Scenario: Page-authority command preserves ordinary bounds

- **WHEN** a command classified `page_authority` is proposed from a dashboard page
- **THEN** the server checks the existing domain owner's resource authority, registered handler, enabled configuration, typed arguments and normal pending-action admission
- **AND** page context or a browser endpoint cannot confer authority, and the owner remains the only executor.

#### Scenario: Self-scoped command preserves ordinary bounds

- **WHEN** a command classified `self_scoped` targets the owning butler's existing session/resource scope
- **THEN** the owner validates that scope and normal approval admission before persisting a proposal
- **AND** a foreign session/resource is rejected, with no broader scope or handler invocation.

#### Scenario: Missing, disabled, foreign or ineligible commands fail before admission

- **WHEN** the owner/handler is missing, its module/gate is disabled, the command is owned by a different butler, arguments are invalid, metadata is absent/`never`, or the command changes secrets or authorization policy
- **THEN** no proposal or new pending action is persisted and no domain effect occurs
- **AND** a bounded unavailable/ineligible reason is returned without rewriting the owner, arguments or command, or choosing a fallback butler.

#### Scenario: Immediate auto-execution cannot masquerade as a proposal

- **WHEN** the owner's current admission path would execute a command immediately, including under a standing rule, and cannot safely park this chat intent without invocation
- **THEN** the command is unavailable for chat proposal admission
- **AND** no proposal tool or presentation worker runs it, and unrelated standing-rule behavior is unchanged.

#### Scenario: Concierge never becomes a write owner

- **WHEN** Concierge supplies system-plane evidence or chat presents a domain action
- **THEN** Concierge's capability, infrastructure contract, roster type and actual MCP registry retain their existing read-only boundaries
- **AND** Concierge registers neither a domain write tool nor the action proposal/decision/execution tool, and routing remains on the existing Switchboard path.

### Requirement: Immutable Owner Action and Reviewed Revision

Every proposal SHALL bind its immutable proposal UUID and positive revision to the owning butler, existing action UUID, original command, canonical argument digest and verified source turn. Only the owner SHALL resolve this identity from its live action and registered command. A display reference MUST NOT authorize execution; a changed command, arguments, impact or reversibility MUST invalidate the old reviewed revision before a decision. The same action SHALL retain one approval/execution identity across chat and the approval page. The proposal UUID/revision and immutable review binding SHALL originate in the protected owner admission of REQ-chat-domain-actions-010, independently of successful presentation.

ID: REQ-chat-domain-actions-002
Source: module-approvals Shared Executor Path; bu-ubqnma; heart-and-soul/vision.md Rules 3, 4; design.md Decisions 2-3
Scope: v1-mandatory

#### Scenario: Same UUID in two schemas is not interchangeable

- **WHEN** an action UUID exists in two owners' schemas or a caller supplies a foreign owner/action reference
- **THEN** the verified proposal resolves only its original owner and exact live action
- **AND** a first-match fan-out, arbitrary schema, tool alias or foreign handler cannot select the executor.

#### Scenario: Approval edits invalidate a stale card

- **WHEN** the ordinary approval dossier legitimately changes action arguments after a card was projected
- **THEN** the prior card's digest/revision cannot approve or dispatch the changed command
- **AND** the owner must review the updated revision before confirming it.

#### Scenario: Duplicate proposal invocation returns stored identity

- **WHEN** the same owner/action/revision is presented again for the same verified conversation/user turn
- **THEN** the stored proposal and message identities are returned
- **AND** no new action, decision, command invocation or duplicate card is created.

### Requirement: Server-Verified Chat Provenance and Audit

Proposal provenance SHALL be derived from the authenticated principal and server-verified active routing/session context, binding the conversation, user message, request, session, owning butler and action. The pending action MUST belong to that owner and source turn. Browser fields, LLM text, correlation UUIDs, page context and read references MUST NOT assert actor or authority. Verified decision attribution and the immutable approval event MUST commit together; raw secrets and unrestricted action payloads MUST NOT enter conversation history, SSE, telemetry or model context.

ID: REQ-chat-domain-actions-003
Source: heart-and-soul/security.md Session Sandboxing and Approval Gates; module-approvals Authentication and Authorization Model and Immutable Audit Events; dashboard-conversations Assistant Author Attribution; design.md Decisions 2-3
Scope: v1-mandatory

#### Scenario: Owning session presents its parked action

- **WHEN** the domain-only proposal tool receives a pending action UUID from the current verified dashboard turn
- **THEN** it derives the owner and source identifiers from trusted context and validates the matching live pending action
- **AND** only a bounded redacted projection is written to that conversation.

#### Scenario: Forged or unrelated location is refused

- **WHEN** a caller supplies an actor/owner/schema/location override, another conversation's message, an unrelated session action, or absent/unverifiable source context
- **THEN** admission fails before proposal/message persistence or an approval transition
- **AND** no chat-origin attribution or authority is inferred from UUID shape or a caller label.

#### Scenario: Decision audit records verified origin

- **WHEN** the authenticated owner confirms a current proposal
- **THEN** the decision audit records the server-derived actor and verified proposal/source binding in the same transaction as the decision
- **AND** the subsequent execution audit references the same owner/action/revision without changing historic source attribution.

### Requirement: Durable Side-Effect-Free Conversation Projection

The conversation store SHALL project an existing pending owner action using the immutable reviewed snapshot and nullable `action_proposals` message field described in design.md Decision 3. Projection persistence MUST precede `action_proposed` SSE emission. Proposal replay, message reads, history reload, reconnect and presentation recovery SHALL be side-effect-free and SHALL NOT decide, execute, expire, defer or rewrite the owner action. A presentation record MUST NOT become an authority registry.

ID: REQ-chat-domain-actions-004
Source: dashboard-conversations Conversation Reply Channel and SSE Response Streaming; RFC 0023 notification-only recovery boundary; design.md Decisions 2-3
Scope: v1-mandatory

#### Scenario: Lost stream event reloads the committed card

- **WHEN** the proposal projection commits but its SSE event is lost or the browser disconnects
- **THEN** the ordinary conversation read reconstructs the same proposal/revision once
- **AND** it invokes neither the domain handler nor an approval decision endpoint.

#### Scenario: Presentation write fails after owner parking

- **WHEN** the owner action is already parked but the conversation projection transaction fails
- **THEN** the owner action and audit remain intact and no uncommitted card event is emitted
- **AND** a matching verified retry can repair presentation idempotently without parking or executing a second action.
- **AND** the atomic owner designation and immutable source/action/review binding remain protected, with execution-producing decisions and dispatch refused while complete-current-presentation evidence is missing.

#### Scenario: Owner state cannot be refreshed

- **WHEN** the original owner is unreachable during a card read/refresh
- **THEN** the card preserves its stored historical evidence and identifies the current source as unavailable
- **AND** it neither fabricates a fresh status nor enables a decision from unknown state.

### Requirement: One Shared Approval Decision and Owning Executor

Chat Confirm/Deny SHALL use the existing authenticated approval decision spine and the same live owner action as `/approvals`. The locked action MUST match the current proposal revision/argument digest and source binding before decision. Existing status CAS, expiry, delivery-presentation fencing, risk rules and immutable audit SHALL remain binding. Approval acceptance MUST NOT imply execution; only the original owning daemon SHALL invoke the approved original handler through the shared executor, without re-entering the gate or falling back to another owner. A newly chat-linked pending action MUST NOT be automatically consumed before the explicit proposal confirmation. Here chat-linked SHALL mean the durable chat-origin designation established atomically at owner admission under REQ-chat-domain-actions-010; it MUST NOT depend on a later card/projection callback. Both existing approve API families, authenticated callbacks and MCP approval MUST apply its complete-current-presentation predicate before an execution-producing decision.

ID: REQ-chat-domain-actions-005
Source: module-approvals Status Transition Contract and Shared Executor Path; dashboard-approvals Approval Verbs; RFC 0023; design.md Decisions 3-5
Scope: v1-mandatory

#### Scenario: Chat Confirm races approval-page Deny

- **WHEN** chat Confirm and `/approvals` Deny concurrently target one pending action
- **THEN** exactly one valid pending-state transition and its audit commit
- **AND** the losing surface receives current authoritative state without a second decision or invocation.

#### Scenario: Expiry or edits precede confirmation

- **WHEN** the action expires or its reviewed revision changes before Confirm
- **THEN** Confirm refuses the stale/non-pending action and does not dispatch

#### Scenario: Owning executor becomes unavailable after approval

- **WHEN** a valid decision commits but the owning daemon is unavailable or its command is now disabled
- **THEN** the action remains approved and unexecuted with a queued/unavailable receipt, and another daemon cannot execute it.

#### Scenario: Concurrent execute or decision replay

- **WHEN** simultaneous dispatches or a retried Confirm address the same action
- **THEN** the shared owner action/attempt fences serialize execution and replay committed results
- **AND** an already decided/executed action cannot be reapproved, reparked or invoked again merely because the card is replayed.

#### Scenario: Stopping chat does not undo an approved effect

- **WHEN** Stop, a closed conversation, a disconnected stream or a removed card follows a committed approval
- **THEN** those presentation events cannot reverse the decision or claim cancellation of an in-flight domain effect
- **AND** the owning action/receipt remains inspectable under the normal approval contract.

### Requirement: Durable Honest Outcome and Ambiguity Fence

Chat-linked execution SHALL persist an owning-executor attempt fence before original-handler invocation and retain immutable outcome evidence. Receipts SHALL use exactly `queued`, `executed`, `failed` or `ambiguous`, independently of approval lifecycle status, with the closed reason vocabulary in design.md Decision 3. `executed` MUST require a persisted success result and success audit event; `failed` MUST require proven no effect or a pre-invocation terminal refusal; uncertain/partial post-start effects MUST remain `ambiguous`. Every dispatch/retry entrance MUST consult the same durable fence. Approved/null-result alone MUST NOT authorize replay of a chat-linked attempt. Every executor/retry entrance MUST consult the protected admission designation even when presentation is absent; missing/incomplete safeguard, review, presentation or decision evidence SHALL refuse invocation rather than fall back to ordinary/legacy dispatch.

ID: REQ-chat-domain-actions-006
Source: module-approvals Shared Executor Path and Immutable Audit Events; dashboard-approvals Retry Reports Dispatch Failure Class Truthfully; heart-and-soul/vision.md Rule 4; RFC 0023 honest provider uncertainty; design.md Decision 4
Scope: v1-mandatory

#### Scenario: Successful execution is durably confirmed

- **WHEN** the owner executes the registered original command successfully and commits its result and success audit
- **THEN** the receipt is `executed`/`execution_succeeded`
- **AND** future card reads and dispatch replays return that stored evidence with zero handler calls.

#### Scenario: Approval is queued but not run

- **WHEN** approval commits without reaching the owner, a command becomes unavailable before invocation, or the trusted attempt is still running
- **THEN** the receipt is `queued` with `owner_unavailable`, `command_unavailable` or `running` respectively
- **AND** neither an HTTP success, approval state nor a stream completion is presented as execution success.

#### Scenario: Proven pre-effect failure remains honest

- **WHEN** trusted owner evidence proves a rejected/expired/abandoned pre-invocation action or an invocation failure with no effect
- **THEN** the receipt is `failed` with that bounded terminal reason or `proven_no_effect`
- **AND** a new attempt is allowed only within the existing approved-action and ordinary owner/risk bounds.

#### Scenario: Timeout, partial effect or commit gap remains ambiguous

- **WHEN** an attempt started and a crash, timeout, partial effect or failed result/audit persistence prevents trustworthy completion proof
- **THEN** the receipt is `ambiguous`/`effect_unresolved`, even if approval remains approved with a null result
- **AND** chat Confirm, both `/approvals` Retry routes, MCP dispatch, scheduled/automatic paths and a restarted executor cannot blindly invoke the action again.

#### Scenario: Only trusted same-command evidence permits safe recovery

- **WHEN** the owner proves no effect or its existing registered adapter proves a same-key replay harmless for the exact command, arguments and resource
- **THEN** only the ordinary owning executor may authorize the bounded retry and preserve both attempt histories
- **AND** a caller-provided idempotency claim, guessed replacement arguments, unrelated provider key or stale attempt cannot clear the fence.

### Requirement: Supported Reversal Compensation and Irreversible Confirmation

A proposal SHALL declare `reversible`, `compensable` or `irreversible` using trusted existing owner command capabilities; absent/unknown support SHALL be `irreversible`. Reversal or compensation MUST be a new action through the same owner's normal registered-tool, enabled-configuration, permission/risk/approval/audit path. It MUST NOT rewrite the original action/audit or invent an inverse. A chat-linked irreversible manual decision SHALL require the current reviewed proposal and exact typed `CONFIRM` acknowledgment on the shared decision seam; incompatible decision surfaces MUST refuse or direct the owner to a compatible detail surface.

ID: REQ-chat-domain-actions-007
Source: bu-ubqnma reversibility-gated undo; heart-and-soul/security.md Approval Gates; module-approvals Shared Executor Path; dashboard-approvals Keyboard-Driven Triage with Undo; design.md Decisions 3, 5
Scope: v1-mandatory

#### Scenario: True reversal uses an existing inverse

- **WHEN** an executed action has an existing same-owner registered inverse and the current resource/version preconditions still hold
- **THEN** Undo proposes that inverse as a new normally gated action with its own decision/audit
- **AND** the original receipt remains executed and only the inverse's confirmed result establishes reversal.

#### Scenario: Concurrent edits invalidate reversal

- **WHEN** resource state/version changed after the original action or the inverse is missing/disabled
- **THEN** Undo is unavailable or fails before invocation with a safe reason
- **AND** no generic delete/update or foreign handler is substituted.

#### Scenario: Compensation exposes residual effects

- **WHEN** an existing owner command can compensate but cannot restore the original state
- **THEN** the control is labeled as a compensation proposal and explains residual effects before separate confirmation
- **AND** it is never labeled Undo or presented as erasing the original effect/history.

#### Scenario: Irreversible or uncertain action has no fake Undo

- **WHEN** the action is irreversible or its reversibility support is unknown
- **THEN** it has no Undo control and its manual decision requires the displayed impact and typed second acknowledgment bound to the current revision

#### Scenario: Ambiguous original does not offer a corrective effect

- **WHEN** the original effect is ambiguous
- **THEN** neither Undo, compensation execution nor blind retry is offered without trusted owner resolution evidence.

### Requirement: Truthful Accessible Card Controls

Chat cards SHALL render the server-projected owner, reviewed command summary, impact, evidence, reversibility and current receipt distinctly from approval acceptance. Confirm/Deny SHALL reuse existing approval mutation behavior and server predicates, with visible pending feedback and no optimistic executed result. Cards SHALL follow the Dispatch design language, keyboard/focus behavior, and shared pre-send decision cancellation semantics. A pre-send cancellation MUST NOT be labeled as reversal of an executed effect.

ID: REQ-chat-domain-actions-008
Source: dashboard-chat-ui Message Thread Display; dashboard-design-language State Color Discipline and Button Forms; dashboard-approvals Approval Verbs and Keyboard-Driven Triage with Undo; design.md Decisions 3, 5
Scope: v1-mandatory

#### Scenario: Queued, failed and ambiguous remain distinguishable

- **WHEN** a card has a queued, failed or ambiguous receipt
- **THEN** its label and permitted controls reflect that exact server state and safe reason
- **AND** none is styled or described as executed merely because approval/HTTP succeeded.

#### Scenario: Keyboard confirmation and pending cancellation

- **WHEN** an owner navigates a card with the keyboard and opens irreversible confirmation
- **THEN** focus is visible, the confirmation owns focus and Escape cancels it before sending
- **AND** typing does not activate triage shortcuts, and the exact second acknowledgment is required before the shared decision request

#### Scenario: Pre-send cancellation is not domain reversal

- **WHEN** the shared hook's existing pre-send cancellation window is cancelled
- **THEN** no request is sent and the UI describes a cancelled pending decision, not a reversed domain effect.

#### Scenario: Stale or unknown data disables effects

- **WHEN** a concurrent decision, stale revision, unknown owner state or request failure is observed
- **THEN** the card shows current verified evidence/error and allows only server-authorized controls
- **AND** it refetches authoritative state without silently dropping the card or fabricating a receipt.

### Requirement: Additive Compatibility and Evidence-Preserving Rollback

Older conversation messages SHALL remain readable with null action projections and MUST NOT be backfilled into action authority from text or tool-call logs. New admission/control disablement and rollback SHALL preserve existing parked actions, unresolved attempt fences, verified source bindings and receipt/audit evidence. Normal owning approval readers SHALL remain the authority for retained actions; retention MUST NOT delete unresolved evidence as ordinary presentation cleanup. The protected chat-origin designation, immutable source/action/review binding and snapshots MUST survive presentation loss, Stop, refusal, disablement, reload and rollback; none SHALL reclassify the action as ordinary/legacy or waive its shared decision/execution safeguards.

ID: REQ-chat-domain-actions-009
Source: dashboard-conversations Message Data Model compatibility; module-approvals Immutable Audit Events; RFC 0023 additive rollout/rollback; design.md Adoption and migration plan
Scope: v1-mandatory

#### Scenario: Legacy assistant message has no action control

- **WHEN** a legacy/plain-text assistant message is loaded without a trusted stored proposal
- **THEN** it renders normally with a null action projection
- **AND** its text/tool log cannot manufacture a Confirm, owner identity, receipt or Undo.

#### Scenario: Rollback preserves unresolved evidence

- **WHEN** new chat action admission/controls are disabled or the feature is rolled back
- **THEN** previously parked actions and unresolved owner attempt/audit/receipt evidence remain intact and inspectable
- **AND** disabling a card does not decide, delete, retry or abandon its underlying domain action.

### Requirement: Atomic Chat-Origin Safeguard Admission

For an eligible chat action, the existing owning pending-admission transaction MUST atomically commit its pending action, trusted immutable `chat_action_v1` designation, verified source/action binding, admission-allocated proposal UUID/revision, and complete immutable owner review snapshot/digest before any decision or dispatch consumer can observe it. Invalid/incomplete admission material MUST roll back the entire admission. Chat-origin classification SHALL derive from trusted server routing/session context before ordinary/automatic branches and MUST NOT depend on caller labels, conversation persistence, SSE, or a later park/proposal callback. Before an execution-producing decision, the owning seam SHALL verify that designation, committed complete current presentation and authenticated explicit confirmation of the exact revision. Before invocation, it MUST additionally verify the committed valid decision evidence under the same owner approval spine. Missing/unknown presentation or safeguard evidence MUST fail closed, never establish ordinary/legacy eligibility, and MUST NOT be waived by crash, Stop, refusal, disablement, reload, repair or rollback. This safeguard SHALL add no new public approval lifecycle state or Concierge authority.

ID: REQ-chat-domain-actions-010
Source: RFC 0023 atomic admission and rejected later-callback assumption; REQ-chat-domain-actions-001 through 007; independent PR4495 review R1; design.md Decisions 2-5
Scope: v1-mandatory

#### Scenario: Admission and safeguard commit together

- **WHEN** the owner admits a verified chat action request through its existing pending-admission seam
- **THEN** the pending action, durable designation, verified source/action binding and immutable review snapshot commit together with zero handler calls
- **AND** failure of any admission write or unavailable/incomplete binding/snapshot commits none of them; no unprotected pending row is published.

#### Scenario: Dedupe cannot borrow ordinary approval authority

- **WHEN** owner admission finds an ordinary/legacy pending row with matching command/arguments or a protected row with a different source/review binding
- **THEN** that row cannot be reused as the protected chat admission
- **AND** an incompatible dedupe path refuses chat admission rather than relabeling history, bypassing safeguards or selecting another owner.

#### Scenario: Park-before-projection failure races every approval entrance

- **WHEN** protected admission commits but projection has not committed, is incomplete/refused, or the session crashes/stops before the proposal callback, while either existing approve API family, an authenticated callback or MCP approval races to consume the row
- **THEN** every execution-producing decision refuses before approval/audit transition or dispatch because complete-current-presentation evidence is absent
- **AND** the pending row and immutable designation/source/action/review evidence remain protected; a raw action UUID, old client/callback, absence of a card or caller-supplied presentation flag cannot establish legacy authority.

#### Scenario: Missing presentation also fences dispatch and automatic consumers

- **WHEN** Retry through either existing API route, owning MCP dispatch, shared execution or an automatic/standing-rule consumer encounters a protected action without verifiable current presentation/decision evidence
- **THEN** it refuses before invoking the original handler even if status is approved with a null result
- **AND** neither that status, a standing rule nor a missing later callback can waive the admission guard or attempt fence.

#### Scenario: Complete trusted presentation permits only normal exact-revision decision

- **WHEN** a trusted server path durably presents the admission-owned snapshot in chat or a compatible authorized approval detail surface
- **THEN** the owner independently verifies its committed presentation evidence against the immutable source/action/review binding before accepting an authenticated explicit decision for that revision
- **AND** absent/wrong snapshot revision/digest or required typed CONFIRM still refuses through both approve families/callbacks/MCP, with no effect
- **AND** presentation alone does not approve, execute, rewrite the pending action, or grant authority; ordinary permission/risk/CAS/audit and execution fences remain binding.

#### Scenario: Representation loss never downgrades protected origin

- **WHEN** Stop, crash, refusal, reload, repair, feature disablement, rollback or retention removes a presentation or makes it unavailable
- **THEN** the owner retains the designation, source/action/review binding, immutable snapshots and unresolved execution evidence
- **AND** missing presentation keeps undecided protected actions decision-ineligible; previously valid decision evidence is retained rather than erased, and further/recovered invocation requires verifiable decision/presentation evidence and the same attempt fence
- **AND** no consumer infers ordinary/legacy admission from missing guard/card fields or chat text.

## Source References

- Owner clarification `chat-actions-v1-scope`, canonical packets bu-ubqnma and bu-0ynlk.8; historical REST-registry sketches superseded.
- Non-Negotiable Rules 3, 4 and 6 (MCP-only communication, deterministic daemon, manifesto authority).
- heart-and-soul/security.md (external utterance trust, approval gates, consent before effect).
- RFC 0023 (durable approval-delivery recovery; notification-only authority and honest uncertainty), RFC 0030 (system-plane read exception).
- Current canonical butler-concierge, staffer-archetype, module-approvals, dashboard-approvals, dashboard-conversations, dashboard-chat-ui and dashboard-design-language specs.
- This changeset design.md (exact proposed projection, receipt, decision and attempt shapes; future real seams; adoption gates).
