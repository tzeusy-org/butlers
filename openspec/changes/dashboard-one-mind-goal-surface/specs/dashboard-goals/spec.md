## Purpose

This capability specifies the future D19 dashboard goals owner experience. It preserves the complete source-bound functional contracts below, remains subject to RFC 0038 and owner-controlled M6 integration, and conveys no current runtime or capability authority.

## ADDED Requirements

### Requirement: Goal list and truthful source coverage

After the signed D19 design and the existing program M6 cutover, the owner dashboard SHALL list goals from the single authoritative goal ledger, using cursor pagination, stable ordering and explicit filters for origin, region, outcome and time. It MUST distinguish a genuinely empty result from unavailable or partial evidence. Domain record fan-out remains a separate read concern. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goals-001
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Filter and deep link a goal

- **WHEN** the owner filters an authenticated goal list
- **THEN** the URL retains the filters and range; each row links to its durable goal ID, shows origin, participating regions, current D11 state and last observed time, and never counts a child as a second goal.

#### Scenario: A goal ledger read is unavailable

- **WHEN** the authoritative ledger cannot be read
- **THEN** the list shows a named unavailable state with retry; it never shows zero goals, a healthy summary or fabricated cached freshness.

#### Scenario: Independent breakdown coverage

- **WHEN** a scalar total is complete but its origin or cause breakdown is unavailable
- **THEN** the total remains visible with its own coverage; no dominant origin, cause or exposure cluster is inferred from the incomplete breakdown.

#### Scenario: Empty, loading and stale states

- **WHEN** the list is empty, loading or retains stale last-good data
- **THEN** Page owns a distinct state; stale data names its observation time and unavailable source, and a skeleton never claims a real count.


### Requirement: Goal origin cause and monotonic exposure

After the signed D19 design and the existing program M6 cutover, goal detail SHALL show server-stored origin, admission cause, caused_by_goal and caused_by_effect when present, and goal/invocation exposure with the recorded fact that established it. It MUST preserve unknown or unavailable provenance, distinguish producer origin from legacy approval origin, and never derive authority from displayed text. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goals-002
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Third-party admission

- **WHEN** a goal originated from a non-owner inbound message
- **THEN** detail identifies third_party and exposed-at-admission with the recorded cause; email is not labeled owner merely because it arrived through email.

#### Scenario: Exposure after a read

- **WHEN** an owner goal receives an untrusted or stamped read that establishes exposure
- **THEN** detail identifies the capability and invocation that changed exposure using stored content-free provenance; it never displays protected payloads or clears exposure on a replacement lead.

#### Scenario: Caused admission

- **WHEN** an effect caused a later subscription goal
- **THEN** detail links the stored causing goal/effect and shows the inherited exposure and intersected ceiling without inventing a new admission rule.

#### Scenario: Missing exposure explanation

- **WHEN** the source cannot provide what exposed a goal
- **THEN** detail says explanation unavailable; false, missing and unavailable remain distinguishable and no guessed explanation is offered.


### Requirement: Lead children and invocation evidence

After the signed D19 design and the existing program M6 cutover, goal detail SHALL show the current lead and all child, replacement and escalation invocation receipts under the same goal. It SHALL preserve parentage, depth, invocation D11 state, model selection, purpose, token/call use, timing and receipt availability. A region is never displayed as an independent reasoning agent. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change. Goal detail SHALL include a read-only research-call ledger supplied by its actual read-model owner: durable research-call/goal/invocation/depth identity, the recorded outbound question, server-selected live or cached mode, URLs actually opened from the producer event ledger, citations with recorded plain-text labels, status/timestamps and available usage/cost/digest facts. It SHALL expose stable pagination and independent source coverage without equating absent, empty, partial, stale and unavailable evidence. This display SHALL neither launch research nor choose mode, grant authority, clear exposure, reset budgets or resume a stopped/old-epoch invocation. Availability remains conditional on the research amendment and backend contracts being adopted and implemented by their owners; D19 adopts none of their unsettled policy.

ID: REQ-dashboard-goals-003
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md; bu-6qaju2.4 original audit contract and D19 owner note (2026-10-15); bu-0rbswe read-model contract; design.md
Scope: post-v1

#### Scenario: A child returns

- **WHEN** a child completes and the lead continues
- **THEN** the tree retains its invocation and result receipt under its lead; the child is not counted as another goal, and further child work is a new child under D9 rather than resumption.

#### Scenario: Replacement or escalation

- **WHEN** the runner replaces or escalates a lead
- **THEN** the goal remains the same; earlier invocations remain visible, exposure persists, and replacements/escalations consume the stored goal budgets.

#### Scenario: Invocation inspection

- **WHEN** the owner opens an invocation disclosure
- **THEN** the existing owner authentication protects model/token/duration facts and bounded safe tool receipts; unknown evidence is labeled and raw provider/error/private payloads are not dumped.

#### Scenario: Protected prompt door

- **WHEN** the owner explicitly requests an invocation effective prompt
- **THEN** the authenticated on-demand projection supplies only an actually retained verified prompt/digest/byte count and ordered content-free provenance; ordinary list/detail responses contain no prompt content; absent, corrupt and unavailable evidence remain distinct.

#### Scenario: Inspect a recorded research call

- **WHEN** the owner opens the research disclosure for an actual stored call under this goal
- **THEN** question, recorded live/cached mode, actually opened URLs, citations and call status are readable with its exact goal/invocation/depth lineage and observation time; the record states what left Butlers and what was read, without treating citations as verified truth.
- **AND** no mode selector, research launch, answer-text retention/reuse control or third-party ceiling exception is inferred from the display; an absent mode reason stays absent rather than guessed.

#### Scenario: Research population and unavailable sources

- **WHEN** research calls are paginated, none exist, the read fails, or only a stale/partial ledger is available
- **THEN** stable cursor order/has-more and source/period coverage remain explicit; complete empty evidence says no recorded calls, failure says unavailable with retry, and partial or last-good records never claim a complete zero or current freshness.

#### Scenario: Stopped and old-epoch research remains historical

- **WHEN** a call is stopped, failed, refused, budget-limited, or its result was discarded after a lead epoch change
- **THEN** detail displays the actual retained call receipt and missing-result reason without inventing a delivered answer, new grant, resumed provider or retry permission; exposure and consumed budgets remain the stored values.

#### Scenario: Research read preserves payload custody

- **WHEN** the read model returns research question, URLs, citation labels and digest
- **THEN** owner authentication precedes the read, those fields render as untrusted data rather than instructions, and private-custody bodies/raw provider payloads are excluded.
- **AND** the original digest-only contract does not establish answer-text retention; newer reuse/third-party/mode-reason/citation-handle suggestions remain with their source owners and pending D20 approval, and telemetry contains only identifiers/digests rather than question, URL or answer payloads.


### Requirement: Effect receipt state and uncertainty

After the signed D19 design and the existing program M6 cutover, goal detail SHALL show every effect with its D11 state and exact invocation lineage. Requested, parked, approved, dispatched, committed, failed, unknown, refused, rejected, expired and cancelled MUST remain distinct. Text, approval or dispatch acceptance is never a commitment receipt. Unknown is an effect state, not an invented goal state. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goals-004
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Approved but undispatched

- **WHEN** an effect was approved but has no dispatch receipt
- **THEN** the dossier reads approved, awaiting dispatch and retains the parked/approved lineage; it never appears success-green or claims execution.

#### Scenario: Unknown effect

- **WHEN** dispatch may have occurred but no durable outcome proves commitment or failure
- **THEN** detail shows unknown with safe cause and observation time; automatic retry and replay are unavailable, and fresh approval under D12 cannot be mistaken for re-dispatching the same call.

#### Scenario: Failed effect and partial goal

- **WHEN** one effect failed or an owner reply was not delivered
- **THEN** the effect failure and recorded goal partial/failed state are separate; the dashboard never promotes accepted transport to completed owner-message work.

#### Scenario: Known commitment

- **WHEN** a capability returns a verified durable commitment receipt
- **THEN** detail identifies that receipt and its effect; model text and a successful UI request alone do not establish it.


### Requirement: Goal budgets and outcomes

After the signed D19 design and the existing program M6 cutover, goal detail SHALL project the stored finite budget and consumed/remaining values for invocations, replacements, escalations, concurrent children, capability calls, tokens, spend and deadline, with per-invocation turns/tokens. It SHALL show precisely the D11 goal and invocation outcomes and the limiting budget, without editing or replenishing budgets. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-goals-005
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Budget exhausted

- **WHEN** the ledger records budget_exhausted
- **THEN** detail names the exhausted dimension and shows no Continue or reset control; already dispatched effects keep their own settled or unknown receipts.

#### Scenario: Budget source unavailable

- **WHEN** a remaining budget cannot be read
- **THEN** the dashboard labels that dimension unavailable; it does not render unlimited or zero and does not extend a deadline.

#### Scenario: Owner reply completion

- **WHEN** an owner-message goal reports completed
- **THEN** the completion view links the durable delivered reply receipt; partial delivery remains partial rather than a completed goal.

#### Scenario: Historical spend and unpriced use

- **WHEN** a usage or ledger source is incomplete or a model is unpriced
- **THEN** totals identify coverage, period, requested-versus-served model provenance and unpriced usage; estimates remain labeled and no hidden zero fills the gap.


### Requirement: Whole-goal Stop and confirmed outcome

After the signed D19 design and the existing program M6 cutover, a running or awaiting/recovering/settling goal SHALL expose owner Stop through the existing authenticated control plane. The client MUST wait for durable cancellation outcome and retain idempotent goal/message identities. Stop follows D14: revoke tokens, stop owned invocations, cancel parked effects, and settle already-dispatched effects without claiming undo. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change. The Overview Stop-all operation SHALL apply the existing D14 Stop once per nonterminal admitted/running/awaiting_approval/recovering/settling goal, skip terminal goals and preserve per-goal/effect settlement and idempotence. It SHALL not suspend a goal, add a new state, undo a committed effect or derive authority from the global held banner.

ID: REQ-dashboard-goals-006
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md; bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md
Scope: post-v1

#### Scenario: Stop a running goal

- **WHEN** the owner activates Stop on a stoppable goal
- **THEN** one authenticated idempotent Stop request targets the whole goal; the UI shows requested/unconfirmed until the durable result is known and then shows the exact recorded outcome.

#### Scenario: Stop while approval is parked

- **WHEN** a goal is stopped before dispatch
- **THEN** its parked approvals become cancelled and every approval/edit/dispatch entry point refuses them; read-only lineage remains accessible.

#### Scenario: Stop after dispatch

- **WHEN** an effect crossed dispatch before Stop
- **THEN** the goal can be stopped while that effect settles committed, failed or unknown; the UI states Stop does not undo a committed action.

#### Scenario: Disconnect or unknown Stop

- **WHEN** the browser disconnects or cannot prove cancellation
- **THEN** history remains addressable by the same goal and immutable message ID; unconfirmed cancellation is never rendered confirmed and is not a replay grant.

#### Scenario: Stop-all retains each goal outcome

- **WHEN** a separately confirmed owner Stop-all reaches the signed service operation
- **THEN** each eligible nonterminal goal receives its D14 transaction, terminal goals are skipped, repeated requests reconcile idempotently, and the dashboard shows actual per-goal stopped/unconfirmed/refused results with committed, unknown and cancelled effect receipts.
- **AND** incomplete enumeration or transport failure remains partial/unconfirmed rather than all-stopped; Hold status is independent and does not become Released because Stop-all failed.


### Requirement: Goal navigation and trace chain

After the signed D19 design and the existing program M6 cutover, contextual links SHALL connect the exact ingress receipt, goal, invocation, effect, approval, domain record and reply/delivery receipt when stored lineage establishes them. A link MUST NOT guess identity from prose, timing, sticky routing, model name or a current region. Legacy receipt IDs remain immutable historical evidence. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change. Hold/Release/Stop-all audit doors SHALL use actual control-event and per-goal receipt identities. Research doors SHALL use actual research-call/goal/invocation relationships, and server-normalized audit URLs SHALL establish navigation only, not authority, source truth or a D20 delivery exception.

ID: REQ-dashboard-goals-007
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md; bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md; bu-6qaju2.4 original audit contract and D19 owner note (2026-10-15); bu-0rbswe read-model contract; design.md
Scope: post-v1

#### Scenario: Follow a receipt chain

- **WHEN** the owner opens an ingress, QA, education, calendar, notification or audit receipt
- **THEN** the projection links its stored goal/invocation/effect identifiers and returns to the exact originating message or record; missing lineage renders an unlinked fact.

#### Scenario: Historical session receipt

- **WHEN** the owner follows a retained legacy session link
- **THEN** the read-only historical receipt remains identifiable without fabricating a goal or reviving per-butler runtime controls; not-found and source-unavailable remain distinct.

#### Scenario: Safe internal and external links

- **WHEN** an answer or receipt contains a normalized citation
- **THEN** known internal routes use client navigation and visible focus, HTTPS external links use noopener noreferrer and a distinct label, and unlinked/invalid citations never become fabricated or verified evidence.

#### Scenario: Navigation warmup

- **WHEN** a goal, region or receipt door is hovered or keyboard-focused
- **THEN** the existing unified navigation-intent contract governs bounded read-only prefetch and cancellation; prefetch never submits Stop, approval or a capability call.

#### Scenario: Research navigation uses stored lineage

- **WHEN** the owner follows a research-call or research-source door
- **THEN** a stored call relation opens that exact goal/invocation/call disclosure; a missing relation stays unlinked and a malformed, credential-bearing, non-HTTPS or unsafe external URL stays inert.
- **AND** eligible normalized HTTPS audit links are labeled external with noopener noreferrer, internal doors use actual allowed routes, and neither path authorizes live exposed research or changes stricter notify/Drive Docs link contracts.

#### Scenario: Global control receipts link to exact goals

- **WHEN** a Hold, Release or Stop-all receipt lists deferred work or affected goals
- **THEN** only stored event/deferral/goal relationships become links, independent unavailable counts stay named, and a deferred admission with no goal row never acquires a fabricated goal ID.


### Requirement: Dashboard read projection and response contracts

After the signed D19 design and the existing program M6 cutover, the future dashboard read API SHALL publish its actual owner-authenticated request/response inventory through OpenAPI and retain standard ApiResponse, paginated/cursor, error and TypeScript/Pydantic mirror contracts. Goal, invocation and effect fields are nullable only where the authoritative receipt can actually be absent; unavailable evidence is separate from absence. Existing domain-only fan-out and unwrapped stream/typed Stop exceptions remain explicit. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change. The actual Hold owner SHALL define GET status, POST engage with reason/optional until, POST release and POST goals Stop-all on the service listener for the signed dashboard client; literal routes/DTOs/error codes remain that owner’s published contract, not invented implemented endpoints here. The research read-model owner SHALL supply its actual call ledger under goal detail with stable cursor and independent coverage, retaining owner authentication, standard envelopes, safe payload boundaries and content-free telemetry.

ID: REQ-dashboard-goals-008
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md; bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md; bu-6qaju2.4 original audit contract and D19 owner note (2026-10-15); bu-0rbswe read-model contract; design.md
Scope: post-v1

#### Scenario: Standard response and mirror types

- **WHEN** a single-resource, aggregate, paginated or error read returns
- **THEN** the existing data/meta or documented cursor envelope, has-more/total coverage and typed error status remain in matching backend/frontend models; a stream or raw typed Stop exception is named rather than silently wrapped.

#### Scenario: Read projection fields

- **WHEN** GET /api/goals or GET /api/goals/{goal_id} reads an admitted goal
- **THEN** the projection exposes stored goal identity, D11 state, origin/cause/caused-by lineage, exposure and content-free explanation availability, participating regions, invocation/effect/approval references, finite budget and outcome receipts; it creates no new kernel fields or inferred authority.

#### Scenario: Domain fan-out and search

- **WHEN** a calendar, state, entity, contact or other existing domain read fans out
- **THEN** bounded domain-pool membership/filtering and exact record identity remain; partial sources are named, structural failure is unavailable, and missing optional source structure is not mistaken for ordinary query failure. New goal search uses one ledger rather than per-unit session aggregation.

#### Scenario: Not-found versus unavailable

- **WHEN** a detail ID is malformed, absent or its owning source cannot be read
- **THEN** existing validation/404/503 semantics remain distinct; unavailable data cannot become a fabricated not-found, empty response or permission grant, and no protected body is read before owner authentication.

#### Scenario: Hold and research projections use their owning contracts

- **WHEN** the future dashboard integrates Hold operations or research-call reads
- **THEN** published owner-authenticated OpenAPI and mirrored types carry the actual operations/nullable facts/coverage/errors, only the signed dashboard control client can mutate Hold/Release/Stop-all, and no model catalog entry, grant, session listener or caller-asserted actor confers those decisions.
- **AND** unavailable backend or unadopted policy remains unavailable/default-off; proposing the UI creates no backend-design blocking edge and proves no runtime delivery.


## Source References

- Non-Negotiable Rules 1, 3, 4 and 6 in about/heart-and-soul/vision.md.
- RFC 0038 D19 and owner decision 11; further exact decisions and current source boundaries remain the per-requirement Source fields.
- Requirement-level source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
- Amendment source: bu-6qaju2.4 original audit contract and D19 owner note (2026-10-15); bu-0rbswe read-model contract; design.md
- Amendment source: bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md
- Amendment source: bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md; bu-6qaju2.4 original audit contract and D19 owner note (2026-10-15); bu-0rbswe read-model contract; design.md
- Amendment source: bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md; bu-6qaju2.4 original audit contract and D19 owner note (2026-10-15); bu-0rbswe read-model contract; design.md
