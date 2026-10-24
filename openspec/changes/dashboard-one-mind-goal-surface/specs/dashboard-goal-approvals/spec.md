## Purpose

This capability specifies the future D19 dashboard goal approvals owner experience. It preserves the complete source-bound functional contracts below, remains subject to RFC 0038 and owner-controlled M6 integration, and conveys no current runtime or capability authority.

## ADDED Requirements

### Requirement: Approval dossier with full effect lineage

After the signed D19 design and the existing program M6 cutover, every approval list, history, dossier, notification and banner SHALL preserve stored producer goal ID, invocation ID/depth, origin, caused-by lineage, exposure and its cause, exact capability arguments, canonical digest and D11 effect state. Legacy prepared-origin classification is separate and nullable. Reads preserve redaction and never confer authority. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-approvals-001
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Review a parked effect

- **WHEN** the owner opens an approval
- **THEN** the dossier shows exact recipients and content, effect/capability identity and digest alongside origin/exposure cause and goal/invocation/depth; a partial source names unavailable fields and never guesses them.

#### Scenario: Edited approval

- **WHEN** the owner edits a permitted parked call
- **THEN** the displayed and approved digest identifies precisely the stored edited call; lineage stays linked to the producing goal/invocation and the original proposal is not silently executed.

#### Scenario: Unknown legacy prepared origin

- **WHEN** origin is absent, unrecognized or unavailable
- **THEN** Prepared is established only by the exact stored prepared marker; producer origin and exposure remain separate, and repeated reads neither mutate identity nor expand redaction.

#### Scenario: Whole-population waiting and stalled queues

- **WHEN** a pending, approved-undispatched or historical effect lies beyond the visible page cap
- **THEN** queue counts and stalled eligibility use complete source metadata and distinct waiting/stalled URL lanes; unavailable data never becomes a healthy all-clear or enables an unverified Retry.


### Requirement: Exact owner click communication approval

After the signed D19 design and the existing program M6 cutover, a direct dashboard owner communication form SHALL display the exact recipients and content that will be sent. Its deliberate Submit activation SHALL record server-authenticated owner approval for that exact call through D7, with a signed verified owner assertion and goal_id null. It SHALL be excluded from autonomy-promotion evidence. Caller-supplied actor/origin/approval claims have no authority. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-approvals-002
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Submit an exact owner-authored send

- **WHEN** the authenticated owner reviews and submits explicit recipients and content
- **THEN** the server records approval tied to the same canonical call, derives the actor from the authenticated principal, and forwards only the approved call through its named existing control-plane boundary; a success response is not a delivery receipt.

#### Scenario: Recipients include a non-owner or are indeterminate

- **WHEN** a communication invites or updates any non-owner, unknown, ambiguous or undeterminable party
- **THEN** the form applies D4 recipient resolution and event approval or an actual matching owner-created standing rule; calendar_respond retains its separate approval-every-time contract and transact remains disabled.

#### Scenario: Call changes after review

- **WHEN** recipients or content change before a submission is committed
- **THEN** the original approval cannot authorize the changed call; the current exact call must be displayed and approved, and repeated/concurrent submissions keep their actual idempotency contract.

#### Scenario: No promotional learning

- **WHEN** a direct dashboard communication completes
- **THEN** the approval and execution remain audited but its submit-click approval never contributes to autonomy promotion counts, suggestions, velocity or rule evidence.


### Requirement: Prepared untrusted drafts remain parked

After the signed D19 design and the existing program M6 cutover, a draft produced by an exposed or third-party goal SHALL be presented only as that goal’s parked effect with full lineage and exact-call approval. It MUST NOT prefill a goal-less owner action form or acquire owner-origin authority by copying, navigation, submit, editing or matching a standing rule. Park-only grants always require a per-event owner decision. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-approvals-003
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Open a prepared draft

- **WHEN** an exposed goal proposes a send
- **THEN** Approvals opens the parked effect, retains goal/invocation/depth/origin/exposure cause, and displays the exact recipients/content; it offers review of that effect rather than Send as owner.

#### Scenario: Standing rule matches park-only

- **WHEN** a matching standing rule exists for a third-party or other park-only communication
- **THEN** the effect remains parked pending the owner’s event approval; the rule never auto-approves or dispatches it.

#### Scenario: Owner edits the parked draft

- **WHEN** the owner changes the content of a prepared draft
- **THEN** the edited call remains that parked effect with its producing lineage and fresh digest; it is not converted to a goal-less owner action.

#### Scenario: Promotion excludes exposed provenance

- **WHEN** the owner approves an exposed or third-party prepared call
- **THEN** the action never contributes to autonomy promotion evidence or suggestions, regardless of its final approval source or successful dispatch.


### Requirement: Cancelled decisions and truthful retry

After the signed D19 design and the existing program M6 cutover, approval decisions SHALL remain authenticated, explicit, atomic and idempotent. Reject/defer/edit/approve availability MUST follow the actual effect transition rules. Cancelled, expired, rejected, dispatched, unknown and committed effects cannot be approved or replayed as parked calls. Safe retry requires durable proof of not-received and the approved exact call. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-approvals-004
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Cancelled approval stays readable

- **WHEN** Stop cancelled a parked effect
- **THEN** the dossier retains lineage and cancelled status but all decision, edit and dispatch routes refuse it, including signed callbacks and bulk paths.

#### Scenario: Retry cannot reach service

- **WHEN** a provably-not-received eligible call cannot reach its named executor
- **THEN** the UI reports unavailable dispatch separately from a reachable executor/tool rejection and never presents successful execution.

#### Scenario: Executor rejects approved call

- **WHEN** the named executor is reachable but rejects the exact call
- **THEN** the response contains bounded safe failure detail and preserves the real approved-undispatched receipt; no unknown or committed call is retried.

#### Scenario: Defer and dashboard-only abandonment

- **WHEN** a retained approval workflow permits defer or abandonment
- **THEN** defer atomically resets the actual notification presentation generation; abandonment keeps its reason/eligibility/audit and dashboard-only boundary and is not generalized into cancelling or retrying an unknown effect.


### Requirement: Approval triage and readable decision history

After the signed D19 design and the existing program M6 cutover, the approval workspace SHALL preserve its existing per-approval URL, waiting/stalled/history lanes, expiry-and-blast-radius ranking, keyboard delay-before-fire undo contract, source-aware live refresh, owner attention policy and separate autonomy ledger. A history or cancelled dossier is read-only. Keyboard shortcuts cannot bypass exact-call display or transition eligibility. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-approvals-006
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Keyboard triage and undo

- **WHEN** the owner uses the existing approval triage shortcut
- **THEN** the existing delay-before-fire undo window is retained for scheduled keyboard decisions and cancellation prevents dispatch during that window; a deliberate dossier click uses the exact displayed call and does not imply undo of a committed action.

#### Scenario: Read history or deep link

- **WHEN** the owner opens an approval URL from a notification, history row or beyond the visible queue cap
- **THEN** the same server-authoritative approval identity and complete eligibility verification govern the dossier; confirmed history is readable and stale cache cannot expose an unverified Retry.

#### Scenario: Attention and teaching digest

- **WHEN** an existing owner attention policy, delivery suppression ledger or post-approval digest is shown
- **THEN** its delivery-versus-suppression, expiry/presentation and informational teaching guarantees remain; no exposed/direct-click approval is promoted into autonomy evidence.

#### Scenario: Source-aware refresh

- **WHEN** approval live stream or polling updates the rail and dossier
- **THEN** the authoritative receipt reconciles waiting/stalled/history and suggestion metrics, refresh failure is named, and optimistic removal never claims dispatch or execution.


### Requirement: Promotion families and retired routing policy

After the signed D19 design and the existing program M6 cutover, tool-call autonomy suggestions SHALL retain their authenticated per-suggestion confirm/dismiss, bounded pagination, exact argument scope, lifecycle counts, failure/degradation and reversible rule controls, while excluding direct owner-click and exposed/third-party approvals. The former LLM route_to butler promotion banner and sessions-avoided metric SHALL retire at M6; deterministic admission policy receives no replacement authority in D19. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-approvals-005
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Eligible autonomy suggestion

- **WHEN** a permitted unexposed approval population supports a suggestion
- **THEN** the existing separate autonomy family keeps its own IDs, exact representative arguments and lifecycle; confirm/dismiss are authenticated, audited and non-bulk, and cancelled or excluded approvals provide no evidence.

#### Scenario: Suggestion source unavailable

- **WHEN** any suggestion or metric sub-query fails
- **THEN** its source is named unavailable and counts do not become truthful zeros; no empty banner is used as an all-clear.

#### Scenario: Retired route promotion

- **WHEN** the owner follows a historical route_to promotion link after M6
- **THEN** the read-only historical record retains its decision and rule provenance but offers no confirm/enable controls; the dashboard never relabels sessions avoided as measured goal savings.

#### Scenario: Rule deletion and restore

- **WHEN** an existing permitted Settings rule can be removed
- **THEN** the explicit exact-rule confirmation, single in-flight delete, serialized reorder/restore and limited restore promise remain, without widening the kind of rule D19 authorizes.

## Source References

- Non-Negotiable Rules 1, 3, 4 and 6 in about/heart-and-soul/vision.md.
- RFC 0038 D19 and owner decision 11; further exact decisions and current source boundaries remain the per-requirement Source fields.
- Requirement-level source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
