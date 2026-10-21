## Purpose

This capability specifies the future D19 dashboard goal continuity owner experience. It preserves the complete source-bound functional contracts below, remains subject to RFC 0038 and owner-controlled M6 integration, and conveys no current runtime or capability authority.

## ADDED Requirements

### Requirement: One assistant voice across messaging

After the signed D19 design and the existing program M6 cutover, telegram, email and dashboard replies SHALL present one assistant voice, with optional helpful region attribution. They MUST NOT prefix replies with an independent butler origin or infer authorship from sticky routing. Server-stored invocation/region provenance remains inspectable separately from the assistant’s public voice. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-continuity-001
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Several regions contribute

- **WHEN** one goal used Health and Calendar regions
- **THEN** the owner receives one coherent assistant reply; a useful source note may name the regions but never a per-butler origin prefix or multiple competing assistant identities.

#### Scenario: Unknown historical author

- **WHEN** legacy message authorship is null or unavailable
- **THEN** history preserves that uncertainty and does not guess from its thread, route, phase event, model name or neighboring message.

#### Scenario: Attribution is server-derived

- **WHEN** an invocation publishes a reply
- **THEN** the authoritative receipt supplies its invocation and region provenance; caller text cannot establish author, owner origin or delivery status.

#### Scenario: Private data and output trust

- **WHEN** a reply or trace refers to protected or untrusted material
- **THEN** normal redaction and safe rendering persist, custody tools remain non-model-facing, and helpful attribution never implies the content is trusted.


### Requirement: Goal-level continuity without runtime resumption

After the signed D19 design and the existing program M6 cutover, the dashboard SHALL retain durable owner message/thread anchors, immutable message identity, chronological history, safe citations and exact-message deep links. A genuine new owner intent or an explicitly new follow-up on a completed outcome SHALL use a separately verified new goal admission with bounded prior result/context. An authentic reply to a goal-produced outbound quiz or confirmation SHALL instead join that PRODUCING GOAL through the server-stored immutable outbound prompt/message binding and a fresh eligible invocation; it SHALL NOT start an unrelated owner goal merely because it is another thread message.
The binding SHALL identify the exact outbound prompt/message, its producing goal and invocation, intended owner/recipient, prompt kind and immutable inbound reply relation under the existing admission/continuity owner's contract. The reply relation and owner identity SHALL be resolved from authenticated channel/server records, never guessed from thread, message text, current region, sticky route, model assertion or session UUID. A repeated immutable reply SHALL reuse its existing accepted binding without duplicate goal/invocation/QA/reply/effect dispatch.
Joining SHALL preserve the producing goal's original origin, exposure and exposure cause, ceiling, causal lineage, consumed budgets, finite deadline, concurrency/replacement limits, Stop state and current epoch. It SHALL use a fresh eligible invocation under that goal's actual grant, never resume a lead, child or provider runtime handle, reopen a terminal/cancelled goal, reset a budget, or bypass an exact parked approval. An owner confirmation reply is input; it does not itself grant an LLM an owner-decision tool or replace an actual per-call approval receipt. A missing/ambiguous binding or ineligible/closed producing goal SHALL produce the existing typed refusal/clarification/reconciliation, without inventing a fresh unrelated goal as a fallback. Actual admission, outbound-prompt storage and producer/client implementation remain held with their current owner before M6. Historical migration/index/downgrade contracts remain owned by their original schema changes. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-continuity-002
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Follow up a completed answer

- **WHEN** the owner expresses genuine new intent or an explicitly new follow-up on a completed outcome in an existing thread
- **THEN** the same channel-stable thread remains, a new goal is admitted with bounded prior context (default last five exchange pairs where that existing policy applies), and the prior goal/result is linked without reusing provider_session_id.

#### Scenario: Concurrent ingress and retry

- **WHEN** the same immutable user message arrives more than once
- **THEN** one durable admission/turn is used and the exact original identity survives retry and Stop; no duplicate logical message, goal, QA case or reply is created.

#### Scenario: Thread history and recall

- **WHEN** the owner searches, renames/archives under an existing contract, or opens a recalled message
- **THEN** pagination, safe text/citation search, stable message ordering, known titles, accessible focus and not-found/unavailable states remain; recall opens the goal/thread receipt rather than selecting a current butler.

#### Scenario: Provider handle retirement

- **WHEN** a retained historical thread contains a provider resume handle
- **THEN** the handle is historical only and never executed after M6; migration snapshots/downgrade evidence are not deleted, backfilled or reinterpreted by this design.



#### Scenario: Reply to an outbound quiz joins its producing goal

- **WHEN** an authentic owner reply resolves the stored immutable quiz/message relation to an eligible producing goal
- **THEN** a fresh eligible invocation joins that same goal with its original ceiling, exposure, consumed budget, deadline, epoch and Stop state, without runtime resumption or unrelated new-goal admission.

#### Scenario: Reply to a confirmation retains the exact parked effect boundary

- **WHEN** an authentic reply resolves a producing goal's stored confirmation prompt
- **THEN** the input joins that producing goal through a fresh eligible invocation while parked effect lineage and actual per-call approval requirements remain unchanged, with no model-facing owner-decision authority.

#### Scenario: Repeated bound prompt reply is not a second admission

- **WHEN** the same immutable owner reply arrives again for an existing accepted prompt binding
- **THEN** the original accepted relation is reused with no duplicate goal, invocation, QA case, reply or effect dispatch.

#### Scenario: Unknown outbound binding is not inferred

- **WHEN** the reply's producing prompt/goal relation cannot be proved from authenticated immutable records
- **THEN** the input receives the existing honest refusal/clarification/reconciliation and neither thread/text similarity nor a session alias selects a goal or invents a new unrelated one.

#### Scenario: Stopped or terminal producing goal remains closed

- **WHEN** a bound quiz/confirmation reply names a stopped, terminal, stale-epoch or otherwise ineligible producing goal
- **THEN** no invocation resumes or starts for that goal, its budget/state remain unchanged, and the existing typed closed/refusal/reconciliation outcome is visible.

#### Scenario: New intent is admitted separately

- **WHEN** an authenticated input is independently verified as genuinely new intent or an explicitly new follow-up on a completed outcome
- **THEN** the existing admission authority may create a new goal with bounded prior context and causal links, without misclassifying an exact outbound-prompt reply or resetting the previous goal.
### Requirement: In-thread failure and observation recovery

After the signed D19 design and the existing program M6 cutover, every dashboard input SHALL retain an in-thread outcome receipt for accepted work, clarification, failure, cancellation or unknown outcome. The owner can disconnect and return to durable history. SSE/phase events are display-only; persisted replies and execution receipts are authoritative. Stream timeout never proves runtime timeout, Stop, failure or replay eligibility. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-continuity-003
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Wait for an answer

- **WHEN** the durable input has answer intent
- **THEN** the existing 45-second answer observer budget, when the fast-answer owner’s accepted contract supplies it, measures from the original observation start; unknown/non-answer/legacy uses 300 seconds, with 15-second keepalive and no deadline restart.

#### Scenario: Late reply after observer closes

- **WHEN** a persisted reply arrives after an observation deadline
- **THEN** history and unread refresh show it normally; the thread stays open and no automatic replay or cancellation occurred.

#### Scenario: Terminal action failed or ambiguous

- **WHEN** QA filing, dead-letter capture, admission or reply settlement cannot establish success
- **THEN** the exact immutable input shows a safe named failed, pending reconciliation, cancelled or ambiguous receipt in-thread; no missing session or route is called successful.

#### Scenario: Client reconciliation

- **WHEN** display tokens, phase events or a stale cache disagree with persisted history
- **THEN** the client replaces its display with canonical persisted content/citations/provenance, preserves draft and immutable identity, and never constructs a citation or outcome from tokens.


### Requirement: Read-only answers as admitted goals

After the signed D19 design and the existing program M6 cutover, a dashboard question SHALL execute as an admitted goal with a read-only grant and the normal D7 broker, D9 invocation, D10 finite budgets, D11 receipt and D14 Stop boundaries. It is not a new D18 model-function category. Domain/system selection, exact live registered reads and typed catalog failures remain explicit; visibility metadata does not authorize tools. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-continuity-004
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Read-only system question

- **WHEN** the question targets known system facts
- **THEN** the admitted goal uses only the registered permitted read capabilities and actual selected catalog model; its answer cites consulted sources or declines honestly without a write.

#### Scenario: Domain question

- **WHEN** the question needs a region’s data
- **THEN** the goal grants that region’s actual read capabilities under the catalog and broker; no per-butler session, direct-handler fallback, route.execute bypass or second reasoning owner is created.

#### Scenario: Unsupported or unavailable evidence

- **WHEN** the read plan, model selection or registered source is unsupported or unavailable
- **THEN** the goal produces a typed safe refusal/unavailable outcome before unauthorized reads; absent, unavailable and malformed catalog evidence remain distinct and no static fallback invents authority.

#### Scenario: Failure after a read began

- **WHEN** an admitted question began a read and later failed
- **THEN** the same durable goal settles; the turn is not restarted through a classifier or another region and no read plan is repeated automatically.


### Requirement: QA filing and replay-eligible dead letters

After the signed D19 design and the existing program M6 cutover, the dashboard SHALL preserve bug filing, clarification and replay-eligible dead-letter recovery with durable identities and in-thread outcome receipts. QA remediation continues as the named D18 engineering executor or an admitted staffer goal under its actual contract; D19 creates neither a new executor kind nor a generic LLM route. Recovery MUST NOT infer that an unknown predecessor did nothing. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-continuity-005
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: File a bug report

- **WHEN** the owner submits an explicit bug report
- **THEN** one durable input and QA case linkage are recorded before detached work; in-thread success requires a real filed-case receipt, and failure/cancellation/ambiguity remains visible without private error payloads.

#### Scenario: Ambiguous input

- **WHEN** no safe requested action or answer can be established
- **THEN** the assistant asks for clarification in-thread with zero domain writes; it does not guess a target, auto-file a bug or claim a safe replay.

#### Scenario: Eligible dead-letter replay

- **WHEN** the owner retries a receipt whose owning policy proves zero targets and no prior execution
- **THEN** exactly one replay is durably queued linked to the predecessor; a second/concurrent retry conflicts, queued is not delivered, and unavailable eligibility prevents the action.

#### Scenario: Unknown predecessor

- **WHEN** recovery cannot prove prior runtime, reply or effect outcome
- **THEN** the receipt becomes explicit unknown/ambiguous under its owner’s reconciliation bound; there is no automatic replay, age-based cleanup or new reconciliation deadline in D19.


### Requirement: Domain workflow receipts survive

After the signed D19 design and the existing program M6 cutover, education, QA, notifications, calendar audit, ingestion timeline, memory episode and other domain receipts SHALL retain their specific factual and recovery guarantees while adding nullable stored goal/invocation/effect links. Dashboard projections MUST NOT invent those links or alter the domain’s state machine, migrations, receipt idempotency, acceptance timing or provider authority. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goal-continuity-006
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Education receipt lifecycle

- **WHEN** a curriculum request is accepted and detached work begins
- **THEN** its immutable receipt precedes work, accepts the existing validated topic/optional goal, and retains accepted/running/completed/failed evidence, no duplicate in-flight work, unavailable-versus-absent reads, evidence-backed calibration/delivery timing and idempotent settlement under the complete dashboard-goal-education contract. Actual request/goal/invocation/notify-dispatch correlation proves delivered or no_record independently of nullable historical session_id; missing/unreadable modern correlation remains unproven. Quiz/confirmation replies use their producing goal's immutable prompt binding and a fresh eligible invocation without goal budget/state reset.

#### Scenario: QA dossier and patrol controls

- **WHEN** the owner inspects a QA case or an existing patrol/reset command
- **THEN** the case’s actual diagnosis, proposed PR, patrol journal, circuit-breaker tri-state, overdue/watch-death evidence and confirmation remain; its invocation/goal trace doors replace only unit-session navigation and do not claim a remediation ran.

#### Scenario: Notification recovery

- **WHEN** the owner inspects a delivered/failed notification or uses an existing eligible retry/escalate
- **THEN** the actual delivery ledger, typed failure predicate, bounded safe interval, alternate owner channel and idempotent concurrent/replay behavior remain; accepted is not delivered and unknown effects cannot be retried as failures.

#### Scenario: Ingestion and domain read provenance

- **WHEN** an ingestion ledger, calendar entry/audit or episode is read
- **THEN** its status taxonomy, channels/range/search/saved-view/filter/cursor behavior and exact domain IDs remain, source degradation stays named, and nullable historical session provenance is preserved as historical rather than forged goal lineage.

## Source References

- Non-Negotiable Rules 1, 3, 4 and 6 in about/heart-and-soul/vision.md.
- RFC 0038 D19 and owner decision 11; further exact decisions and current source boundaries remain the per-requirement Source fields.
- Requirement-level source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
