# JARVIS pursuit: run 14 (2026-09-22)

**Reader:** owner deciding what new work to release. **Status:** proposed planning record; not adopted requirements or deployment evidence. Started September 22; completed September 23 (Asia/Singapore).

Six selected fixes are wired in source. Four new moves survive deduplication: one chat correction and three evidence-aware capability specifications.

[Structured evidence](2026-09-22-jarvis-pursuit-data.json)

## Decision in view

Epic bu-p5umi2 is held by owner gate bu-04i7om. Closing that gate releases the bounded chat correction and specification authoring for three capabilities. It does not adopt those features, deploy anything, provision credentials or authorize external actions. Existing prerequisite and owner gates remain.

| Rank | Proposed outcome | Child scope | Cost | Bead |
|---|---|---|---|---|
| 1 | Fail closed while an existing conversation's owner is unresolved | implementation | S | bu-p5umi2.1 |
| 2 | Correct once: recheck the local conclusions learned from a corrected observation | specification | L | bu-p5umi2.2 |
| 3 | Remember the last confirmed custody of a physical item, and close its return explicitly | specification | M | bu-p5umi2.3 |
| 4 | Prepare an evidence-covered packet against a versioned owner checklist | specification | M | bu-p5umi2.4 |

Three of four moves are capability work (75%). The chat error-path correction leads because it prevents an avoidable failed owner action. New capability children draft exact contracts first; they do not implement the target behaviors. The optional stock connector remains an unranked research candidate because the prerequisite source and owner payoff have not been established.

## North star

Five-second fleet verification with earned calm: nothing fabricated, failure never impersonates health, staleness never wears current-data authority, and every consequential clause is a door on an unbroken signal-to-session-to-evidence spine. Keyboard-first Dispatch; specialist ownership; deterministic infrastructure.

## What improved, and what remains unknown

The focused QC traced six selected changes from producer through consumer in the pinned source. These are slice-level source confirmations, not evidence that the running fleet is deployed, populated or healthy. Existing tests were inspected as verification seams; product tests were not run for this report.

| Selected slice | Source verdict | What changed |
|---|---|---|
| fleet tool authority 4160 | source-confirmed-as-designed | The source implementation has landed. Replace the run-13 claim that frozen runtime configuration silently wins with: unexplained widening or narrowing reconciles to Git, an explicit reasoned strict subset may narrow, and the dashboard exposes the three-way tool-surface truth. Keep live tool counts and deployment state unknown. |
| prompt private routing 4167 | source-confirmed-as-designed | The exact prompt receipt, drift projection, purpose lane, and fail-closed private routing are present in source. Do not restate the run-13 remote-routing observation as current live behavior; deployed catalog/provider authority was not checked. |
| prepared approval delivery 4203 | source-confirmed-as-designed | Prepared drafts no longer inherit the ordinary push-failure story, and durable reconciliation is wired in source. No claim is made that a live delivery worker has processed current rows. |
| taste partial summary 4183 | source-confirmed-as-designed | The run-13 all-or-nothing summary defect is fixed in source: one failed query no longer discards successful siblings. Live ledger size and current query health are unknown. |
| chat answer provenance 4205 | source-confirmed-as-designed | Safe answer rendering and canonical provenance have landed end to end in source. This does not prove that any particular live answer contains citations or that a deployed route manifest matches this checkout. |
| ingestion retention live windows 4199 4200 | source-confirmed-as-designed | Both retention and advancing aggregate-window behavior are on the baseline. Preserve the distinction between source behavior and live performance: this audit did not time queries, inspect indexes, or observe polling. |

Evidence: `audits[] | select(.audit_id=="qc-landed")` contains the producer/consumer paths and source lines for each slice.

## Tier board and comparison

Ten route groups cover all 61 registrations, including redirects and nested specialist entry points. Source tiers: **2 solid, 5 functional, 2 weak, 1 broken**. Four cross-cutting sweeps are functional. These are heuristic source judgments; no surface is claimed world-class.

The prior dossier used different cohorts and included live observations. Changed scope or evidence mode prevents a clean movement claim. In particular, source improvements to selected ingestion and configuration slices do not establish whole-surface improvement. Education lifecycle, calendar occurrence/day-load truth, graph completion and spend divergence remain named constraints, not newly discovered work.

| Surface | Prior cohort | This run | Movement / limitation |
|---|---|---|---|
| surface-command | solid | solid | Comparable command/control cohort; solid held. Prepared-origin presentation fix is source-confirmed by QC; no live health claim. |
| surface-chat | solid (run13 QC cohort) | functional | Different scope: current whole chat surface is functional; initial owner-lookup failure is new. Do not label a regression from the prior narrow shipped-work QC. |
| surface-activity | functional | solid | Current source-only audit rates the broader interaction mechanics solid, but open audit/deadline/notification gaps remain. Whole-surface improvement is not established by this run. |
| surface-health | functional (combined cohort) | functional | Split from prior health/education cohort. Functional retained; no new live or clinical verification. |
| surface-education | broken (mastery lens) | broken | Broken retained against the prior mastery lens and active lifecycle contract, despite working UI mechanics. Historical live observations are not repeated as current measurements. |
| surface-life-graph | weak | weak | Weak retained; canonical activity/memory/graph delivery remains existing work. |
| surface-calendar | functional (time-calendar lens) | functional | Functional retained. Existing recurrence/day-load correctness work constrains the holistic flow. |
| surface-ops | weak | functional | Broader source cohort now includes ingestion/System. Selected ingestion fixes are source-confirmed; QA/connector/System gaps remain. Whole-surface improvement not established. |
| surface-settings | weak (config-governance) | functional | Source-only UI cohort is functional; known permission/governance gaps remain. No whole-surface improvement claim. |
| surface-spend | not separately tiered | weak | New explicit surface tier; weak due to known divergence and ceiling-control constraints. Prior deep-spend lens was n/a. |
| cross-shell | functional | functional | Functional held; existing keyword/recovery gaps remain. |
| cross-visual | not separately tiered | functional | New source-only cohort; no rendered visual or contrast claim. |
| cross-speed | not separately tiered | functional | New source-only cohort; no measured latency claim. |
| cross-accessibility | functional | functional | Functional held; loading-only route evidence does not prove populated/browser accessibility. |

## Systemic themes

### The next useful capability is a qualified outcome, not another ledger

Knowing a memory, object or document exists does not prove its derivation remains supported, its location is current, or a checklist is covered. The three new capabilities make those distinctions explicit and preserve unknown states.

Evidence: `src/butlers/modules/memory/storage.py:2783`; `roster/general/tools/items.py:81`; `roster/travel/tools/documents.py:1`.

### Failure must not manufacture ownership

Chat alone yielded a new bounded UI defect: failed initial owner resolution falls through to a fallback endpoint. The backend ownership check contains the durable-write risk; the client still needs a truthful error/retry gate.

Evidence: `frontend/src/pages/ChatPage.tsx:66`; `frontend/src/pages/ChatPage.tsx:145`; `src/butlers/api/routers/conversations.py:1856`.

### Delivery is the larger backlog, and source progress is visible

Most strong ideas already exist across 12 dossiers and 230 non-closed Beads. Six selected fixes now trace end to end in source. Re-filing old work would obscure ownership; existing education, calendar, life-graph and spend constraints remain explicit.

Evidence: `src/butlers/core/runtime_config.py:240`; `roster/lifestyle/api/router.py:103`; `frontend/src/hooks/use-ingestion-events.ts:88`.

### Evidence still has a boundary

Source-connected is not deployed, a source tier is not a browser usability test, and a recorded case outcome is not proof of fulfilled commitments. The two deep addenda preserve those distinctions without widening current execution scope.

Evidence: `src/butlers/core/fleet_cases.py:1`; `src/butlers/core/spawner.py:2989`; `frontend/src/pages/ChatPage.tsx:116`.

## Ranked move packets

The target outcome and verification plan are implementation guidance. **Child deliverable** states what this gate actually releases. Full structures are retained in the JSON and copied into Beads structured fields.

### 1. Fail closed while an existing conversation's owner is unresolved

**Child:** bu-p5umi2.1. **Scope:** implementation. **Cost:** S. **Priority:** P2.

Specialist ownership must resolve before an existing conversation can send. The backend already rejects a mismatched owner before persistence; the client should preserve the draft, name the lookup outage, and retry that lookup instead of clearing input and making a predictably rejected request.

**Child deliverable:** Implement the bounded UI correction against current conversation ownership authority; clarify its existing spec if needed and execute the listed frontend behavior checks. No backend authority change.

#### Outcome

For the initial load of `/chat/:conversationId`, unresolved or failed owner resolution cannot instantiate a send-capable turn against a fallback butler. The thread pane shows a named retryable resolution error, preserves unsent text in component state, and enables history and mutation only after the authoritative owner resolves. A background lookup refetch after an owner has already been established does not disable Stop or discard the bound owner. Bare `/chat` continues to start a Switchboard conversation.

#### Non goals

- No change to bare `/chat` routing
- No new cross-channel thread model or handoff
- No browser draft persistence implementation
- No backend authority widening or cross-schema read
- No change to SSE, Stop, retry identity, citations, or answer rendering

#### Governing intent

- about/heart-and-soul/vision.md specialist ownership and Switchboard routing
- th-design design bar: recovery errors say what to do next and controls cannot repeat an unintended effect
- dashboard failure taxonomy: failure must not degrade to a benign or wrong default

#### Surface map

- **Modules:** - frontend/src/pages/ChatPage.tsx - frontend/src/pages/ChatPage.test.tsx - frontend/src/hooks/use-conversations.ts
- **Interfaces:** - useConversationById(conversationId) - useConversationMessages(butlerName, conversationId) - useConversationTurn({ butlerName, activeConversationId, ... })
- **Schemas:** - No schema change
- **Trust boundaries:** - The cross-butler detail response is the authority mapping conversation ID to owning butler; no client fallback may manufacture that mapping.
- **Consumers:** - full-page conversation header - history query - send/Stop turn machine
- **New paths:** - NEW route-resolution error branch with direct refetch and mutation gate

#### Behavior matrix

- **Happy:** Bare `/chat` uses Switchboard; an existing route resolves owner, then reads and sends through that owner.
- **Failure:** Initial non-404 detail lookup renders a named alert and Retry; composer/send are unavailable while ownership is unknown and typed text remains present. A background refetch failure after successful binding preserves the established owner and active Stop control while naming stale resolution state.
- **Concurrency:** A late response for a previous route ID cannot unlock the currently selected conversation; React Query keys remain conversation-specific.
- **Idempotence:** Repeated Retry only refetches the lookup; it sends no message and preserves the existing message-id retry contract.
- **Rollback:** Removing the UI gate returns current behavior; there is no persisted data migration.

#### Doc impact

Clarify dashboard-chat-ui route-resolution failure behavior if the baseline spec does not already require authoritative owner resolution before send; no doctrine change.

#### Verification

- **Nearest existing tests:** - frontend/src/pages/ChatPage.test.tsx - frontend/src/hooks/use-conversation-turn.test.tsx
- **Seam behaviors:** - 500/transport error shows resolution alert and retry - composer cannot call send while owner is initially unknown and input is not cleared - background owner-lookup refetch failure preserves the established owner and Stop - retry success restores the correct butler and history - route change during retry cannot unlock stale ownership - bare chat remains send-capable through Switchboard
- **Expected net test delta:** Tests: +4 ~0 -0

#### Slice plan

- Add the explicit non-404 resolution branch and retry using the existing detail query refetch.
- Gate history and turn creation on resolved ownership for existing IDs while retaining the draft value.
- Add route-resolution failure, retry-success, stale-route, and bare-chat regression cases.

#### Novelty

Unlike bu-0ynlk.14 and persist-safe-dashboard-unsent-drafts, this move does not redesign or persist the composer. Unlike terminal-action recovery, it acts before any turn exists. It closes the missing authority gate between a conversation deep link and the existing send machine.

#### Evidence

- frontend/src/pages/ChatPage.tsx:66-82
- frontend/src/pages/ChatPage.tsx:116-121
- frontend/src/pages/ChatPage.tsx:272-289
- frontend/src/hooks/use-conversation-turn.ts:292-314
- about/heart-and-soul/vision.md:9-18


### 2. Correct once: recheck the local conclusions learned from a corrected observation

**Child:** bu-p5umi2.2. **Scope:** specification. **Cost:** L. **Priority:** P2.

General promises reliable memory (roster/general/MANIFESTO.md:15-21); vision.md:122-125 measures absorbed mental labor. The owner should not separately discover every inferred preference or rule learned from one mistaken observation. Relationship's immutable confidence versus staleness doctrine (MANIFESTO.md:63) requires a separate evidence-review state, never silent confidence editing.

**Child deliverable:** Draft a reviewable proposed OpenSpec contract, any named manifesto amendment, implementation allocation, and the named future verification plan. Do not change runtime behavior or treat this audit/gate as feature adoption. Current Tests: +0 ~0 -0; the verification estimate below applies to future implementation.

#### Outcome

An explicit local episode correction holds its recorded direct consolidation-derived facts/rules before they can be reused. A bounded specialist review ends in a cited successor, retirement or owner_needed hold. Authenticated owner assertions remain authoritative. The receipt names local recorded-dependency coverage and unresolved counts; it never claims fleet copies or delivered answers were repaired.

#### Non goals

- No new capture system, ontology, contradiction docket, revision ledger, usage weighting or graph viewer.
- No dependencies guessed from prose, entity adjacency, supports/related_to links or shared session membership.
- No recursive or cross-butler cascade in the first contract, and no repair of copied facts without explicit provenance.
- No relationship.entity_facts writes, action reversal, delivered-message erasure or right-to-forget purge.
- No wider read ceiling, recovery of expired raw content, or inferred override of owner assertions.

#### Governing intent

Correction withdraws support, not necessarily the conclusion's truth. Preserve the artifact and its asserted confidence; suspend accepted-knowledge use until reviewed. Retention deletion alone never implies falsity. Reasoning stays in ephemeral sessions (about/heart-and-soul/architecture.md:24-27); all inter-butler coordination remains Switchboard/MCP-only (:51-62).

#### Surface map

- **Producer:** Existing local correct(memory_deletion) path in src/butlers/core/corrections.py:774-937 through core/memory_hooks.py and modules/memory/tools/management.py. Thread authenticated correction identity and structured results through this seam; do not copy its foreign target_pool pattern into the new capability.
- **Storage:** Existing per-butler episodes/facts/rules/memory_links/memory_events. NEW additive memory migration and NEW src/butlers/modules/memory/derivation_review.py proposed: local review state keyed by artifact type/id, derivation revision and correction identity, holding source pointers/status/timestamps. Reuse memory_events for transitions; no fleet belief ledger.
- **Target set:** Only direct derived_from links targeting the corrected episode and originating in demonstrably consolidation-derived local facts/rules. Legacy source_episode_id-only or unknown-provenance rows report uncovered, never receive guessed backfills. Reuse server-validated assertion provenance from bu-djjig; unknown classification cannot authorize automatic rewriting.
- **Consumers:** search.py semantic/keyword/hybrid/recall plus tools/context.py profile/rule selection share the review exclusion. Authorized memory_get keeps historical inspection with evidence_review_required. Catalog discovery and graph traversal suppress held projections; projection writers/backfills must refuse held generations.
- **Review:** NEW bounded review branch in the existing consolidation.py/consolidation_executor.py scheduling pipeline. An ephemeral local specialist session receives only readable surviving evidence and proposes a cited successor or retirement. Persistence validates current review revision, source correction state and owner precedence. Insufficient or withheld evidence remains owner_needed.
- **Owner surface:** Existing conversational correction result gains checked_local_dependencies, held, resolved and owner_needed with authorized artifact references. No new page is required for the first slice. Later inspection can use existing memory detail routes under their owner authority.
- **Boundaries:** Canonical reads/writes stay in the owning schema. Corrections arriving elsewhere route through Switchboard/MCP to that owner. No foreign SELECT, shared raw evidence store, caller-selected sensitivity ceiling or caller-asserted owner provenance.

#### Behavior matrix

- **Happy:** Lock corrected source, identify recorded direct derivatives, create holds and suppress projections atomically. Review commits a newly supported successor or retirement, with a terminal result.
- **Multiple supports:** Surviving citations do not mechanically prove the conclusion: hold then re-evaluate readable evidence. Later authenticated owner assertion remains dominant; inference cannot replace it.
- **Expiry:** Ordinary TTL cleanup preserves readable derived artifacts as specified. Explicit correction followed by cleanup preserves artifact-side holds and content-free correction pointers.
- **Failure:** Malformed evidence, unknown attribution or review failure keeps an honest unresolved hold. Correction/hold/projection failure rolls back with failed status. Specify a bounded direct-dependent cap and transaction timeout; over-cap returns impact_too_large before mutation, never partial success.
- **Concurrency:** Correction and evidence-linked artifact commits lock the source in the same documented order. Consolidation rechecks correction under its existing claim fence. Review uses CAS on artifact/review revision; later correction or owner assertion invalidates stale model output. Guard late catalog write-behind/backfills against resurrecting held artifacts.
- **Idempotence:** One review item per artifact revision/correction identity. Retried correction returns the same impact result; review retry cannot duplicate successors. Resolving one cause cannot clear another outstanding correction hold.
- **Rollback:** Disable automated review while retaining holds and owner inspection. Do not roll back readers to versions that ignore persisted holds, and do not reactivate artifacts merely because the worker is disabled.
- **Typed trigger:** Only a typed epistemic_correction operation activates this direct-dependency hold. Ordinary privacy/forget deletion and TTL expiry do not. Free-text reason or a correction row alone is insufficient.
- **Outer atomicity:** The local correction receipt, source mutation, holds and projection suppression commit in one transaction under a caller-stable operation identity. Propagate hook failure to the outer caller; a bool/None return cannot imply success.

#### Doc impact

- Amend module-memory: explicit correction versus retention, recorded direct-dependency scope, terminal review outcomes and every read consumer.
- Amend error-recovery-corrections: typed local result and atomicity; resolve its foreign-pool wording for this new path in favor of MCP-only doctrine.
- Amend memory-discovery-catalog and graph projection contracts: held-generation suppression and no late resurrection.
- Update docs/modules/memory.md Consolidation, Decay and Hygiene, Shared Discovery Catalog. Existing domain ownership remains unchanged.

#### Verification

- **Nearest existing files:** - tests/modules/memory/test_consolidation_executor.py - tests/modules/memory/test_episode_provenance_deletion.py - tests/modules/memory/test_local_read_ceiling.py - tests/modules/memory/test_tools_reading.py - tests/core/test_corrections.py - tests/core_tools/test_infra_correct.py - tests/core_tools/test_memory_catalog.py - tests/integration/test_entity_graph_walk.py - tests/core/test_spawner_memory_context.py
- **Named seams:** - Real Postgres: consolidate one episode into a fact/rule, correct it, then execute actual recall, context, catalog and graph readers; no derived artifact remains accepted knowledge while history survives. - Same fixture: ordinary TTL deletion leaves derivatives readable; correction followed by cleanup preserves the hold and content-free tombstone. - Drive correct through the real memory hook; correction identity and actual result reach persistence. Inject projection failure and assert no success or partial mutation. - Race correction against consolidation's source-row fence and delayed catalog projection; no accepted artifact resurrects. - Exercise surviving-evidence successor, retirement and owner_needed; stale CAS loses to a new correction or authenticated owner assertion. - Confidential dependencies stay absent from normal-ceiling packets/results. Over-cap fails before mutation. Retries produce one hold/successor; multiple causes remain independently unresolved. - Three-way typed trigger matrix: epistemic correction holds direct derivatives; ordinary forget/privacy deletion and TTL cleanup preserve their distinct existing contracts. Inject failure between outer correction receipt and hold; no partial state commits.
- **Expected net test delta:** Planning estimate Tests +10 ~6 -0 in existing suites with shared real-Postgres fixtures; final collected-case budget requires justification. No tests executed for this read-only audit.
- **Gate scope:** Memory migration/shared core boundary requires real-Postgres integration, core/MCP contracts, retrieval/context coverage, planner/collection and terminal hosted CI. Mocks alone do not prove transaction or role isolation.

#### Slice plan

- S1 Specify trigger/scope, immutable assertion preservation, failure/cap policy and terminal outcomes; reconcile with bu-djjig and provenance contracts.
- S2 Add local holds and atomic correction/projection suppression; typed local core hook returns bounded impact receipt.
- S3 Wire every reuse reader and projection writer, retaining authorized historical inspection and existing read ceilings.
- S4 Add bounded specialist review with exact surviving evidence, CAS and successor/retire/owner_needed outcomes; report through the original conversational correction flow.
- S5 Verify races, retry, cleanup, owner precedence and rollback; reconcile docs without claiming transitive or fleet-wide repair.

#### Novelty

Checked ranked ledger, all 1408 prior unranked titles, relevant full prior moves, matching open Beads and OpenSpec. Revision history preserves past assertions; contradiction dockets compare assertions; right-to-forget purges data; the correction verb replaces one fact; trust mechanics concern usage/corroboration/decay; graph self-heal rebuilds projections. New behavior is suppression and re-evaluation of recorded local conclusions after explicit correction of their evidence.

#### Evidence

- src/butlers/modules/memory/consolidation_executor.py:232-255 and 334-364: exact evidence links and existing persistence/claim fence.
- src/butlers/modules/memory/migrations/001_memory_schema.py:341-363: derived_from relation and target index.
- src/butlers/modules/memory/storage.py:550-598: atomic catalog disownment and projected-edge removal, currently without dependency traversal.
- src/butlers/modules/memory/tools/reading.py:107-130: held sensitivity policy on memory_get.
- openspec/specs/relationship-facts/spec.md:4,108-113: separate Relationship canonical authority.

Dependencies: `bu-djjig`.


### 3. Remember the last confirmed custody of a physical item, and close its return explicitly

**Child:** bu-p5umi2.3. **Scope:** specification. **Cost:** M. **Priority:** P2.

General promises durable memory and finding what was recorded (roster/general/MANIFESTO.md:9-25). The immediate payoff is answering where the spare adapter was last put or which borrowed item is still with the owner, showing the statement and date supporting the answer. This absorbs a small recurring memory task without extending Home into physical work.

**Child deliverable:** Draft a reviewable proposed OpenSpec contract, any named manifesto amendment, implementation allocation, and the named future verification plan. Do not change runtime behavior or treat this audit/gate as feature adoption. Current Tests: +0 ~0 -0; the verification estimate below applies to future implementation.

#### Outcome

Owner says an identified object moved to a cupboard, was lent, borrowed, returned, or retired. A typed update records an evidence-bearing transition on the existing collection item. A later question returns last-reported location/custody, observed_at, source reference and any conflict. A return only closes the named active loan when the owner reports that physical return; it never means the assistant performed it.

#### Non goals

- No visual asset database, inventory connector, universal capture platform, purchase automation, valuation, insurance or legal claims.
- No warranty/return-policy windows, condition-driven maintenance, consumable stock or Grocy overlap.
- No borrower outreach, calendar promise creation, reminders or financial loan claims in the first capability.
- No physical repair, installation, wipe, disposal, device actuation, or automatic removal from Home.
- No assumption that a receipt, missing sensor or elapsed date establishes ownership, location, return or retirement.

#### Governing intent

about/heart-and-soul/vision.md:54-82 mandates domain isolation and deterministic infrastructure; architecture.md:29-62 requires Switchboard MCP coordination. General owns remembered personal-item records; Finance keeps receipt/transaction authority, Relationship keeps canonical person identity, Home keeps connected-device automation and its physical-work refusal (roster/home/MANIFESTO.md:50). Clarify this bounded General responsibility alongside the existing planned ownership-refusal amendment; no new butler. Typed custody workflow requires an explicitly adopted bounded General manifesto/routing amendment, coordinated with bu-2jtfw.9; locate-only owner notes fit existing memory authority. This child drafts that amendment and cannot adopt it.

#### Surface map

- **Producer:** Existing owner conversation routed to General. A NEW typed possession_record verb accepts an existing item identity, operation identity, expected revision and owner statement evidence. No new transport or ingestion lane.
- **Storage:** Reuse general.collection_items and its UUID as physical-instance identity. Propose an opt-in, versioned possession profile inside data containing descriptive fields, current projection and append-only custody transitions. Do not create a separate asset table or fleet ledger for this slice. Two same-model objects remain different item IDs.
- **Existing collection sufficiency:** Generic fields plus source references ARE sufficient for the first locate-only slice: store last_reported_location, observed_at and source_ref and answer with that qualification. They are insufficient for truthful return closure with concurrent edits: existing item_update reads outside a transaction and overwrites without checking revision; arbitrary source links do not supply transition ordering or replay safety. Add the narrow profile transition validator and row-lock/CAS operation over existing storage only when adding custody verbs. A new ledger is not justified by this pass.
- **Modules:** NEW proposed roster/general/tools/possessions.py owns profile validation, locate projection and transitions. Existing roster/general/tools/items.py and collections.py enforce that generic update/delete/export cannot silently bypass an opted-in profile; audit collection deletion as well as item deletion. Register the domain verbs through existing roster/general/modules/tools.py. General API read models need to carry qualified profile data, not a new page.
- **Interfaces:** Proposed possession_locate and possession_record verbs; reuse item_get/item_search and the existing /api/general/entities/{entity_id} item detail read seam (roster/general/api/router.py:366). First consumer is the General conversational reply with stable item/source references.
- **Schemas:** No new schema namespace. Person/place references may point to public.entities after normal resolution; retain unresolved labels as unresolved, never guess a canonical person. Keep object identity local to the collection item, avoiding the global name/type uniqueness conflation of two identical physical objects.
- **Trust boundaries:** A supplied Finance transaction reference is resolved through Switchboard MCP, never direct Finance SQL. Finance receipt details stay Finance-owned. Linking a receipt is optional evidence and cannot auto-create an owned object. Canonical entity references do not publish private object locations into public graph payloads.
- **Terminal consumer:** General returns a recorded transition receipt or a conflict requiring clarification. Returned/retired projections remain searchable historically; the physical outcome is explicitly owner-reported. No external side effect is triggered.

#### Behavior matrix

- **Happy:** Record a last-known location and source; locate answers as of that statement. Borrow records a named active custody episode. Owner-reported return cites that episode and closes it. Retire preserves history and removes the item from active-possession answers.
- **Failure:** Ambiguous item identity prompts selection without a write. Missing source or unknown location stays unknown. Unavailable linked Finance evidence yields an unavailable evidence door while retaining the owner statement; it does not fabricate purchase or ownership certainty.
- **Concurrency:** Lock the existing item and compare expected profile revision. Two competing moves/returns cannot both become the next accepted revision; reject the stale write and show the intervening transition. Historical reports carry event time separately from recorded time and cannot silently replace a later current projection. Every generic whole-JSON update to an opted-in item, even an unrelated key, must share the same row-lock/CAS protocol; namespace write blocking alone cannot protect against stale whole-document replacement.
- **Idempotence:** An operation identity is stored with the transition. Retrying identical content returns the same event/receipt; same identity with different content is refused. Replay never opens a second loan or closes a different loan.
- **Rollback:** Correction appends a superseding transition and recomputes the projection; it never asserts the physical action was undone. Profile opt-out/export must preserve history or require explicit owner deletion, with collection-level deletion included in that boundary.
- **Uncertainty:** Location always says last reported with a date. Borrower identity and physical location are separate fields. Unresolved contradictory observations produce a conflict rather than invented certainty; absence of an observation does not mean absent possession.

#### Doc impact

- Add NEW proposed openspec/specs/personal-item-custody/spec.md for profile, transition, evidence and uncertainty contracts.
- Amend openspec/specs/butler-general/spec.md tool inventory; add bounded personal-item memory to General manifesto and routing guidance, coordinated with the existing collection ownership-refusal proposal.
- Document that Home maintenance remains scheduling/reminder metadata and never authorizes physical work; Finance purchase records remain financial evidence. No expansion of either manifesto is required.

#### Verification

- **Nearest existing:** - tests/tools/test_general_items.py - roster/general/tests/test_tools.py - tests/api/test_general_stats.py - roster/finance/tests/test_tools.py
- **Seam behaviors:** - A locate answer preserves unknown/as-of/source qualification. - Two same-name objects require explicit identity rather than merging. - Real PostgreSQL contention: one of two equal-revision transitions commits; replay of the winner returns the original event. - Return closes only its specified active borrowing episode; stale or wrong-episode return is refused. - Generic item and collection deletion/update cannot erase profile history accidentally. - Receipt-link failure never changes custody; General cannot read Finance schema directly. - Race generic item_update reading r1 against possession_record committing r2; stale whole-document generic write must conflict or preserve r2 history even if it only changes an unrelated key.
- **Expected net test delta:** Approximately +6 to +8 focused behavior tests, including one real-PostgreSQL transition matrix; extend nearest existing files where suitable. No tests executed during this read-only audit.

#### Slice plan

- 1. Specify the opt-in profile and demonstrate locate-only value using existing collection fields plus qualified owner source references; no ledger or new route.
- 2. Add narrow typed record/locate tools, protected namespace handling and honest disambiguation to existing item storage.
- 3. Add versioned custody history with transactional revision check, idempotent move/borrow/lend/return/retire and superseding correction; verify generic write/delete boundaries.
- 4. Add optional canonical person/place and receipt references through existing resolution/delegation seams, preserving unavailable/unknown states.

#### Novelty

Sep05 capture/vocabulary/search solves remembering a submitted note and finding its target; this adds the domain rule for last-reported physical custody and closing one borrowed-item episode. Sep03 Finance windows concern merchant deadlines, not physical loans. Sep03 Home maintenance concerns service due dates and observations, not current custody. Public entity graph provenance is reused, not recreated; there is no proposal to fix generic graph temporal truth here.

#### Evidence

- roster/general/tools/items.py:69-108
- roster/general/modules/tools.py:235-277
- roster/general/api/router.py:366-367
- roster/finance/tools/transactions.py:584-602
- openspec/specs/butler-general/spec.md:21-26

Coordinate the General collection namespace and generic-write protocol with rank4 and bu-2jtfw.9. The two specification tasks have separate deliverables; serialize future implementation if shared item storage is chosen.


### 4. Prepare an evidence-covered packet against a versioned owner checklist

**Child:** bu-p5umi2.4. **Scope:** specification. **Cost:** M. **Priority:** P3.

Absorbs the repeated mental work of comparing documents with a supplied list, grounded in vision.md:120-125 and General's organization/export promises. The owner can ask what is missing and receive exact clause-to-evidence doors instead of repeatedly opening every document.

**Child deliverable:** Draft a reviewable proposed OpenSpec contract, any named manifesto amendment, implementation allocation, and the named future verification plan. Do not change runtime behavior or treat this audit/gate as feature adoption. Current Tests: +0 ~0 -0; the verification estimate below applies to future implementation.

#### Outcome

Producer: an owner-supplied checklist revision plus explicitly selected source references. Consumer: a General preparation session proposes clause/evidence matches, the owner reviews ambiguous matches, and a deterministic evaluator reports missing, needs_review, unavailable, or covered. Terminal outcome: an immutable owner-confirmed preparation receipt and exportable index, naming the exact checklist revision and evidence versions. The receipt means complete against that supplied list only; it never means submitted, accepted, eligible, or compliant.

#### Non goals

- No official requirement discovery, legal advice, eligibility decisions, destination-admissibility gate, or promises about current government/provider requirements.
- No external submissions, forms filled on third-party sites, payments, credentials, or new account access.
- No generic capture/expiry/reminder rewrite, new connector, or duplicate case/deadline/commitment ledger.
- No automatic gathering of all owner documents, no raw financial or travel document duplication into General, and no public-catalog publication of checklist/document contents.
- No inference that an unreadable document covers a clause, or that a filename alone proves its contents.

#### Governing intent

- **General:** Required owner-approved amendment to roster/general/MANIFESTO.md: add organizing owner-supplied administrative checklists, maintaining source-linked evidence coverage, and preparing an owner-reviewed index. Explicitly refuse determining official requirements, eligibility, legal sufficiency, or submitting applications. Do not infer approval from this audit.
- **Finance:** roster/finance/MANIFESTO.md:13-20,31-33: recorded receipts/transactions remain Finance-owned. Request only an authorized source reference and the minimal fact the owner elected to use; any new Finance tool/scope must be reviewed separately.
- **Travel:** roster/travel/MANIFESTO.md:24-26: trip documents remain Travel-owned. Trip-specific preparation stays with Travel; General links or delegates, and cannot become an alternative passport/admissibility registry.
- **Boundary:** about/heart-and-soul/architecture.md:51-62: specialist reads through Switchboard MCP; no direct peer-schema SQL. Cross-domain coordination reuses existing case machinery only when needed.

#### Surface map

- **Existing inputs:** - roster/general/tools/items.py - roster/general/tools/collections.py - roster/general/modules/tools.py - roster/travel/tools/documents.py
- **New module:** NEW proposed opt-in src/butlers/modules/administrative_packets/ module, initially General-only; use the established Module lifecycle, not core scheduler changes.
- **New schema:** NEW General-local packet_revisions, packet_clauses, packet_evidence_links, and packet_preparation_receipts. Store owner-provided clause text, source locator/version, reviewed match state, and immutable receipt identity; never treat a bare source locator as read authority.
- **New interfaces:** NEW packet draft/review/status/prepare MCP tool group. Mutations use server-derived owner attribution, revision comparison, and request idempotency. Keep initial consumer conversation-first; no new dashboard page required.
- **Trust boundaries:** Resolve each selected source through its owning butler under existing read authority. Carry minimal coverage results and opaque locators across MCP. Source unavailable/revoked becomes unavailable, never covered. LLM matching yields proposals only; deterministic evaluation consumes explicit accepted matches.
- **Consumers:** Owner conversation receives missing clauses and source doors; preparation tool emits a frozen index. Existing deadlines may reference the packet for the owner's actual due date; existing commitments may represent an explicitly requested missing-document task, with normal evidence-based closure.
- **Export:** First slice exports a structured index through existing General export conventions. Optional rendered cover sheet can reuse src/butlers/modules/document_renderer/__init__.py after its own source-read/rendering review; PDF is not a prerequisite and no external notify/submission is implied.
- **Dependencies:** Prior capture/collection work is an input integration dependency, not deliverable credit. No dependency on unimplemented Paperless access.

#### Behavior matrix

- **Happy:** Owner supplies checklist r1 with three clauses, selects sources, and confirms proposed matches. All three covered at pinned versions produces one preparation receipt and index. The conversation states complete against your checklist r1.
- **Failure:** Missing/ambiguous clauses remain explicit; extraction failure, inaccessible source, or unresolved cross-domain authorization produces unavailable/needs_review and blocks completeness. A declined clause is excluded only by an owner-confirmed checklist revision, never by silent model waiver.
- **Concurrency:** Checklist edit and prepare race under a revision comparison: only the exact evaluated revision may settle. Stale reviews fail with a recoverable changed-checklist response.
- **Idempotence:** Request identity plus packet revision yields one preparation receipt. Transport retries return it rather than creating tasks or duplicate receipts.
- **Rollback:** Archive/reopen a packet without altering source records. Checklist or evidence revision changes require renewed review of affected matches; old receipts remain historical and are labeled superseded. Withdrawal cannot erase the prior receipt or mark an external application cancelled.
- **Completion boundary:** Preparing an index never completes a submission deadline or a promise to send documents. Only a preparation-specific commitment may close against this receipt; any external outcome requires separately supplied evidence and authority.
- **Version authority:** Each accepted source requires an authoritative immutable version ID, versioned reference or authorized content hash. Filename, fetched_at, mutable URL/blob locator or row ID alone is not a version proof. Unverifiable versions are version_unverifiable/unavailable and cannot produce current completeness.
- **Evidence race:** Prepare fences checklist revision AND accepted evidence versions/read authority. If evidence changes or authority is revoked between evaluation and commit, refuse current completeness; retain old receipts as historical, never silently promote them.

#### Doc impact

- Owner-approved General manifesto amendment is a prerequisite; reconcile with the already proposed capture/vocabulary amendment.
- NEW proposed administrative-packet-preparation OpenSpec capability with coverage states, evidence authority, revision fences, and honest terminal meanings.
- Extend butler-general tool inventory only after module admission; reference existing deadline/commitment contracts rather than replacing their state machines.
- Document source minimization and explicit refusal of official/eligibility assertions in module prompts and tool descriptions.

#### Verification

- **Nearest existing tests:** - tests/tools/test_general_items.py - tests/core/test_deadlines_db.py - tests/core/test_commitments.py - tests/integration/test_commitments_roundtrip.py - tests/modules/test_document_renderer.py
- **Seam behaviors:** - Real-Postgres revision race: a checklist changed during prepare cannot produce a current complete receipt. - Missing, ambiguous, revoked, unreadable, and version-changed evidence remain distinguishable and never produce covered without review. - A retried prepare returns one immutable receipt and no duplicate downstream commitment. - Specialist-source tests deny peer-schema access and prove only the authorized minimal MCP result is retained. - A completed preparation leaves an external submission deadline and submission commitment open. - Receipt/index contract asserts exact checklist and evidence versions, and rejects official accepted/eligible wording as a terminal state. - Keep checklist r1 unchanged while one accepted source changes or is revoked before commit: no current complete receipt may commit. Mutable-locator-only evidence must be version_unverifiable.
- **Expected net test delta:** Approximately +8 focused contract/integration cases; reuse existing fixtures and parametrization. Exact scope and test budget to be set during specification; no tests executed for this read-only audit.

#### Slice plan

- S1: owner scope decision and General manifesto/spec amendment; pin the distinction between evidence coverage and official sufficiency.
- S2: local-only owner checklist revisions, clause coverage, source selection, and conversation status with honest unknown states.
- S3: minimal specialist MCP reference resolution and accepted match review with version invalidation; no new source access.
- S4: immutable preparation receipt/index plus concurrency/idempotence tests; connect an existing deadline or preparation commitment only when explicitly requested.

#### Novelty

- **Searched:** known-ledger.md fully; prior-proposals.md synonyms packet/paperwork/application/checklist/document; targeted openspec/specs and active changes; relevant full prior moves and bu-2jtfw.9 snapshot fields.
- **Distinct from capture:** Sep05 capture/collections makes content durable and retrievable. This move consumes selected content to compute reviewed clause coverage against a particular supplied checklist revision.
- **Distinct from case file:** Sep01 Fleet Case File tracks specialist participation and terminal slices. It does not encode individual checklist requirements, evidence matching, or invalidation of a preparation receipt when the list changes.
- **Distinct from documents:** Jul17 registry, Aug09 Paperless, and Sep05 destination admissibility address sources, expiry, or travel validity. This adds no source or rule authority and does not judge admissibility.

#### Evidence

- about/heart-and-soul/vision.md:120-125
- roster/general/MANIFESTO.md:11-25
- openspec/specs/butler-general/spec.md:21-26
- roster/general/tools/collections.py:43-64
- roster/travel/tools/documents.py:26-58
- src/butlers/core/commitments.py:469-484

Coordinate General namespace/amendment with rank3 and bu-2jtfw.9. Four proposed local tables are a design option, not a required implementation architecture; prefer existing storage where it satisfies version and authority contracts.

## Existing designs deepened, without duplicate Beads

**Situation answers and closure proof:** run 13 already proposed the answerable-question registry. The new design addendum defines a readiness denominator, freshness/snapshot bounds, typed unresolved answers, and owner disposition separate from proved fulfillment. Existing `close_case` outcome text is not automatically evidence of a fulfilled commitment. The related `bu-h40h2b.8` container packet does not authorize all deferred S3-S6 behavior; its scope remains unchanged.

**Interrupted inference recovery:** run 13 already proposed bounded continuation. The addendum starts with preservation-only behavior, then specifies a durable root-level successor claim, cancellation fencing, unknown-effect holds, and fresh spend/tool/deadline admission. `bu-2jtfw.5` preservation work and `bu-h40h2b.9` governance boundaries remain authoritative; no auto-resume or replay is adopted here.

Read `synthesis.design_addenda` for full options, seams, verification and remaining gates. No existing Bead was broadened or released.

## Deduplication and scope discipline

The audit considered 12 prior dossier files (331 audit records), searched 1,408 prior unranked proposal titles, and captured 230 non-closed Beads. The final evidence includes 101 explicit dedup/cut records. Standing inference, cross-butler and proactivity lenses retained zero new moves. This is deliberate, not missing coverage.

Selected existing delivery owners remain:

- `bu-h40h2b.4`: education lifecycle and mastery truth.
- `bu-h40h2b.6`: calendar occurrences and honest day load.
- `bu-h40h2b.7` / `bu-h40h2b.8`: graph and situation work.
- `bu-h40h2b.5.1.4`: spend pseudo-identity divergence.
- `bu-h40h2b.10` / `bu-h40h2b.11`: physical accessibility and discovery.
- `bu-2jtfw.5`: preservation and otherwise-silent work outcomes.
- `bu-2jtfw.9`: General capture and collection ownership, which new General specifications must coordinate with.

## Method, review and verification

24 source-only audit passes: ten route groups covering all 61 registered routes, four cross-cutting UX sweeps, five standing ecosystem lenses, two new rotating lenses, two deep design addenda, and one six-slice landed-work QC. Native collaboration used 11 workers in four dispatch batches (3,3,3,2), never above three concurrent. The last two workers processed their passes sequentially after the service total-thread limit. The owner approved this tool adaptation and removal of hourly scheduling. Outputs were atomically checkpointed before synthesis.

Audited commit: `42c2c9817b16e53ba16fcce4583cb2889ab9878d`. All source citations are pinned to that tree, not whatever is on main when the reader opens this dossier.

This run inspected source at one pinned commit. It did not inspect personal records, run product tests, drive the live dashboard, measure latency/contrast, verify deployment or activate capabilities. Existing test paths are proposed verification seams, not passing test evidence. Prior live observations are historical only. Tier comparisons mark changed cohorts explicitly.

Independent capability review retained all three candidates and required explicit epistemic-correction atomicity, whole-document custody concurrency, and evidence-version/authority fencing. These corrections are incorporated above. The final report receives a separate source/privacy/scope review before publication.

Verification receipts: - **Product tests:** not run; planning/report changes only - **Live runtime:** not inspected - **Report checks:** PASS: JSON/packet completeness, 341 source citation locations, local HTML links/anchors, desktop and 390px headless render inspection, content-blind identifier scan, git diff --check; make check-guards passed including 256 strict OpenSpec items. Source-citation checks establish existence/range, not semantic truth. - **Beads lint:** PASS: 7 issues checked, zero template warnings (four ranked children, reconciliation, epic and owner gate). Structured fields and exact blocking dependencies read back for every child; no child in bd ready; no dependency cycles. - **Final review:** Independent semantic review passed after raw/synthesized chat impact wording was reconciled; final exact-head check follows commit.

## Evidence access

```bash
jq '.audits[] | select(.audit_id=="surface-chat")' docs/redesigns/2026-09-22-jarvis-pursuit-data.json
jq '.synthesis.ranked_moves[] | {rank, title, work_kind, bead_id}' docs/redesigns/2026-09-22-jarvis-pursuit-data.json
jq '.synthesis.design_addenda' docs/redesigns/2026-09-22-jarvis-pursuit-data.json
```

## Execution boundary

Epic bu-p5umi2 is held by owner gate bu-04i7om. Closing that gate releases the bounded chat correction and specification authoring for three capabilities. It does not adopt those features, deploy anything, provision credentials or authorize external actions. Existing prerequisite and owner gates remain.

Epic: `bu-p5umi2`. HOLD gate: `bu-04i7om` (owner assigned). Terminal reconciliation: `bu-p5umi2.5`. Closing the gate is an owner action. Children remain dependency-blocked; existing gates are untouched.
