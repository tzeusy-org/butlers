## Reconciled implementation boundary

Only the six reconciled capability deltas in this directory are applied to
canonical specs. Core-spawner's three original blocks and session-process-logs'
two original blocks are entirely unlanded and have no archive delta. Within
core-tool-discovery, the catalog, inventory, direct policy accessor and skill
identity portions retain their original IDs; all planning/visibility/receipt
obligations are explicitly transferred below. A checked task means its terminal
disposition is recorded, not that successor implementation has run.

## Original requirement dispositions

| Original ID | Original requirement | Original grounding / scope | Landed subset or successor disposition |
|---|---|---|---|
| REQ-core-modules-001 | Tool Exposure Metadata | RFC 0027 §Tool Metadata Contract; RFC 0002 §Tool Sensitivity Metadata / v1-mandatory | Descriptive validated metadata, compatibility defaults and preserved authorization declarations. |
| REQ-session-process-logs-001 | Process log table schema | [Observed] session-process-logs table schema; RFC 0027 §Observability and Privacy / v1-mandatory | Proposed gen1 extension not implemented; existing canonical baseline unchanged. Entire delta superseded by RFC0038 (bu-r980fk), adapters bu-kiuh8v. |
| REQ-session-process-logs-002 | Bounded Tool Surface Attempt Receipts | RFC 0027 §Observability and Privacy; core-tool-discovery REQ-core-tool-discovery-008 / v1-mandatory | Proposed gen1 extension not implemented; existing canonical baseline unchanged. Entire delta superseded by RFC0038 (bu-r980fk), adapters bu-kiuh8v. |
| REQ-runtime-config-dashboard-ui-001 | Runtime config card displays runtime config from DB | RFC 0007 §Dashboard API Surface; RFC 0027 §Exposure Planning State Machine / v1-mandatory | Existing Management-tab policy editor only; unavailable-policy scenario and native-use promises not delivered. |
| REQ-runtime-config-dashboard-ui-002 | Config tab supports inline editing | RFC 0007 §Dashboard API Surface; RFC 0027 §Exposure Planning State Machine / v1-mandatory | Actual save/error edit-state handling; no invented success confirmation or planning guarantee. |
| REQ-core-daemon-002 | Core Tool Surface | RFC 0002 §Core Tools, §Tool Budget Discipline (superseded by this change), Doctrine Rule #5 (operational tuning is DB-persisted) / v1-mandatory | Registration authority and infrastructure classification only; adapter hiding deferred. |
| REQ-core-daemon-003 | Config loading parses runtime_seed section | RFC 0001 §Startup Phases (phase 1 — config load), Doctrine Rule #5 / v1-mandatory | Actual seed policy field/default alongside current Git/narrowing and catalog-sensitivity corrections. |
| REQ-runtime-config-api-001 | GET runtime config endpoint | RFC 0007 §Dashboard API Surface, §Response Envelope / v1-mandatory | Actual stored policy and API field-tier response, not runtime consumption. |
| REQ-runtime-config-api-002 | PATCH runtime config endpoint | RFC 0007 §Dashboard API Surface; RFC 0027 §Exposure Planning State Machine / v1-mandatory | Enum validation/DB persistence/no restart, with current accepted groups retained; cache mutation and planner use deferred. |
| REQ-core-spawner-001 | Ephemeral MCP Config Generation | RFC 0001 §Trigger Dispatch; RFC 0002 §Ephemeral MCP Config Generation; RFC 0027 §Adapter-Owned Search Corpus and Model Presentation / v1-mandatory | Proposed gen1 extension not implemented; existing canonical baseline unchanged. Entire delta superseded by RFC0038 (bu-r980fk), adapters bu-kiuh8v. |
| REQ-core-spawner-002 | Runtime Failure Classification | [Observed] core-spawner Runtime Failure Classification; RFC 0027 §Failure and Replay Safety / v1-mandatory | Proposed gen1 extension not implemented; existing canonical baseline unchanged. Entire delta superseded by RFC0038 (bu-r980fk), adapters bu-kiuh8v. |
| REQ-core-spawner-003 | Logical Session Attempt Orchestration | [Observed] core-spawner Logical Session Attempt Orchestration; RFC 0027 §Failure and Replay Safety / v1-mandatory | Proposed gen1 extension not implemented; existing canonical baseline unchanged. Entire delta superseded by RFC0038 (bu-r980fk), adapters bu-kiuh8v. |
| REQ-runtime-config-table-001 | Runtime config table exists per butler schema | RFC 0006 §Database Schema, RFC 0001 §Startup Phases / v1-mandatory | Landed core_224 closed enum/default and typed storage only. |
| REQ-core-tool-discovery-001 | Layered Butler Tool Surface | RFC 0027 §Layered Surface Model; heart-and-soul/architecture.md §Why Tool Surface Discipline Matters / v1-mandatory | Immutable final-definition catalog only; per-attempt sets, artifacts and discoverability deferred. |
| REQ-core-tool-discovery-002 | LLM Visibility Preserves Existing Handler Authority | RFC 0027 §LLM Visibility Classification, §Adapter-Owned Search Corpus and Model Presentation; heart-and-soul/security.md §Session Sandboxing / v1-mandatory | Exhaustive classification and non-authority only; adapter visibility, transport projection, pagination and plan binding deferred. |
| REQ-core-tool-discovery-003 | Per-Invocation Exposure Policy | RFC 0027 §Exposure Planning State Machine; heart-and-soul/vision.md #5 / v1-mandatory | Closed DB/control-plane policy and direct accessor only; per-invocation mode selection deferred. |
| REQ-core-tool-discovery-004 | Capability Negotiation and Conservative Fallback | RFC 0027 §Capability Negotiation and Adapter Contract; craft-and-care/interfaces-and-dependencies.md §Compatibility Rules / v1-mandatory | Proposed gen1 extension not implemented; existing canonical baseline unchanged. Entire delta superseded by RFC0038 (bu-r980fk), adapters bu-kiuh8v. |
| REQ-core-tool-discovery-005 | Native Tool Search Loads Typed MCP Definitions on Demand | RFC 0027 §Native Tool Search and Deferred Loading Contract; RFC 0002 §Tool Call Logging Proxy / v1-mandatory | Proposed gen1 extension not implemented; existing canonical baseline unchanged. Entire delta superseded by RFC0038 (bu-r980fk), adapters bu-kiuh8v. |
| REQ-core-tool-discovery-006 | Replay-Safe Discovery Failure Handling | RFC 0027 §Failure and Replay Safety; core-spawner Runtime Failure Classification / v1-mandatory | Proposed gen1 extension not implemented; existing canonical baseline unchanged. Entire delta superseded by RFC0038 (bu-r980fk), adapters bu-kiuh8v. |
| REQ-core-tool-discovery-007 | Skill Loading Cannot Expand Tool Authority | RFC 0027 §Skills and Tools; RFC 0002 §Skills Infrastructure / v1-mandatory | Canonical skill identity/digest and non-authority only; cross-adapter projection deferred. |
| REQ-core-tool-discovery-008 | Content-Blind Discovery Evidence | RFC 0027 §Observability and Privacy; craft-and-care/observability-and-operations.md / v1-mandatory | Proposed gen1 extension not implemented; existing canonical baseline unchanged. Entire delta superseded by RFC0038 (bu-r980fk), adapters bu-kiuh8v. |
| REQ-core-tool-discovery-009 | Native Tool Search Verification Gate | RFC 0027 §Tool Search Conformance and Rollout Gate; craft-and-care/performance-discipline.md §Core Rules / v1-mandatory | Proposed gen1 extension not implemented; existing canonical baseline unchanged. Entire delta superseded by RFC0038 (bu-r980fk), adapters bu-kiuh8v. |

Every deferred portion in this table is superseded by RFC0038 (bu-r980fk), with
runtime adapter work in bu-kiuh8v. This includes original capability negotiation,
native typed search, replay/effect proofs, content-blind attempt receipts,
spawner orchestration and exact-tuple admission. No original requirement is
silently dropped or marked delivered by a catalog-only test.

## Original scenario accounting

Each row accounts for the original scenario at its original requirement ID.
“Transferred” means **superseded by RFC0038 (bu-r980fk)**, adapters `bu-kiuh8v`,
and not delivered or executed by this archive. Existing canonical baseline
behavior inside a MODIFIED block remains in place; only its proposed gen1
extension is transferred. The original wording is not used to certify an
unimplemented adapter or an obsolete fixed inventory count.

| Original ID | Original scenario | Disposition |
|---|---|---|
| REQ-core-modules-001 | Module declares deferred LLM tool metadata | Landed descriptive metadata/default/non-authority only; no runtime loading or discovery mode is implied. |
| REQ-core-modules-001 | Metadata cannot revive an excluded group | Landed descriptive metadata/default/non-authority only; no runtime loading or discovery mode is implied. |
| REQ-core-modules-001 | Approval metadata remains authoritative | Landed descriptive metadata/default/non-authority only; no runtime loading or discovery mode is implied. |
| REQ-core-modules-001 | Existing module without classification uses compatibility posture | Landed descriptive metadata/default/non-authority only; no runtime loading or discovery mode is implied. |
| REQ-session-process-logs-001 | Table created by migration | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-session-process-logs-001 | CASCADE delete on session removal | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-session-process-logs-001 | Existing process log receives an empty receipt array | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-session-process-logs-002 | Failover attempts remain individually visible | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-session-process-logs-002 | Receipt contains no content payload | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-session-process-logs-002 | Receipt expires with its process log | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-runtime-config-dashboard-ui-001 | Card shows DB values | Existing canonical UI behavior and actual policy editor/save handling only. |
| REQ-runtime-config-dashboard-ui-001 | Card distinguishes declaration, decision, and registration | Existing canonical UI behavior and actual policy editor/save handling only. |
| REQ-runtime-config-dashboard-ui-001 | Cold fields show restart badge | Existing canonical UI behavior and actual policy editor/save handling only. |
| REQ-runtime-config-dashboard-ui-001 | Hot exposure policy shows next-session behavior | Current UI renders this guidance, but actual next-session/native behavior is unlanded and transferred; no runtime guarantee synchronized. |
| REQ-runtime-config-dashboard-ui-001 | Unavailable policy is not presented as active | Not implemented: current component uses an eager_filtered missing-field fallback. No new canonical requirement or product change manufactured. |
| REQ-runtime-config-dashboard-ui-002 | Edit and save a field | Existing canonical UI behavior and actual policy editor/save handling only. |
| REQ-runtime-config-dashboard-ui-002 | Restart-required feedback after saving cold field | Existing canonical UI behavior and actual policy editor/save handling only. |
| REQ-runtime-config-dashboard-ui-002 | Hot policy save does not claim restart | Only actual empty restart list/save edit clearing landed; no success-confirmation toast or subsequent-session guarantee claimed. |
| REQ-runtime-config-dashboard-ui-002 | Failed policy save preserves confirmed state | Existing canonical UI behavior and actual policy editor/save handling only. |
| REQ-core-daemon-002 | core_groups filters tool registration | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-002 | Unreasoned runtime row cannot hide a Git capability | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-002 | NULL core_groups enables all tools | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-002 | route.execute always registered | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-002 | Switchboard-only tools name-gated | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-002 | Messenger-only tools name-gated | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-002 | Domain tools excluded from staffers via core_groups | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-002 | Infrastructure visibility is independently projected | Unlanded adapter projection transferred; current infrastructure registration retained. |
| REQ-core-daemon-003 | Parse runtime_seed section | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-003 | Reject old [butler.runtime] section | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-003 | Reject old [butler.seed_configs] section | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-003 | Obsolete top-level [runtime] section is rejected | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-core-daemon-003 | Missing runtime_seed section uses defaults | Current canonical registration/config scenario retained; dated inventory counts and any adapter-presentation additions are not synchronized. |
| REQ-runtime-config-api-001 | Successful read | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-001 | Butler not found | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-001 | Tool declaration snapshot is unavailable | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-001 | Field tiers included in response | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | Accepted fields | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | Catalog read sensitivity updates hot | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | Update cold field | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | Narrowing requires an explicit reason | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | Clearing the reason restores Git authority | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | All managed fields are cold | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | Invalid field value — negative concurrency | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | Invalid core_groups — unknown group name | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | delegation is a known core group | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | Empty PATCH body | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | Butler not found | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-runtime-config-api-002 | Hot exposure policy applies to subsequent sessions | Only DB persistence/no-restart response landed; accessor-cache mutation and planned-session use are unlanded and transferred. |
| REQ-runtime-config-api-002 | Invalid exposure policy is rejected | Current canonical API behavior retained with landed policy storage/validation/tier additions only. |
| REQ-core-spawner-001 | MCP config includes only butler's server | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-001 | Every candidate uses matching presentation capabilities | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-001 | Infrastructure client remains on the complete surface | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-001 | Adapter assets carry the immutable presentation plan | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-002 | Systemic runtime failure is eligible | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-002 | Empty normal return is classified after merging tool-call evidence | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-002 | Captured tool calls make failure ineligible | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-002 | Classifier defaults closed | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-002 | Explicit native failure uses merged effect evidence | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-002 | Shell activity makes presentation replay ineligible | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-003 | Successful fallback completes logical session once | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-003 | Non-eligible failure completes without retry | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-003 | Attempt cap prevents infinite retry | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-003 | Presentation fallback preserves candidate identity | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-core-spawner-003 | Timeout without usage remains visible | Existing canonical behavior stays unchanged; proposed presentation/receipt extension is unlanded and transferred. |
| REQ-runtime-config-table-001 | Table creation via migration | Landed storage/default/constraint; current canonical table and existing scenarios retained. |
| REQ-runtime-config-table-001 | Table has at most one row | Landed storage/default/constraint; current canonical table and existing scenarios retained. |
| REQ-runtime-config-table-001 | Existing row receives conservative policy | Landed storage/default/constraint; current canonical table and existing scenarios retained. |
| REQ-core-tool-discovery-001 | Tool discovery cannot broaden configured ownership | Registered ownership/classification is landed; per-attempt discoverability is unlanded and transferred. |
| REQ-core-tool-discovery-001 | Every exposed tool resolves to one registered handler | Final catalog has immutable definitions without callable aliases; runtime discovery/loading remains transferred. |
| REQ-core-tool-discovery-001 | Mandatory LLM capability remains discoverable | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-001 | Unclassified metadata preserves the safe baseline | Presentable/eager incomplete catalog classification landed; no session projection implementation claimed. |
| REQ-core-tool-discovery-002 | Infrastructure handler is hidden from an LLM session | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-002 | Infrastructure caller retains its endpoint | Existing handler/listing retained; infrastructure classification metadata does not install a filter. |
| REQ-core-tool-discovery-002 | Disabled module is unavailable to new runtime work | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-002 | Visibility metadata cannot grant invocation authority | Landed descriptive non-authority retained; no omitted-plan dispatch behavior claimed. |
| REQ-core-tool-discovery-002 | Multi-page projection reveals no hidden metadata | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-002 | Projection changes invalidate old cursors | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-002 | Supported transports expose the same projection | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-002 | Canonical MCP listing remains complete | Existing registered listing retained; dark-build snapshot entries are explicitly disabled again and are not live tools. |
| REQ-core-tool-discovery-002 | Complete MCP listing is not a presentation fallback | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-003 | Conservative policy preserves eager behavior | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-003 | Auto policy selects only a verified mode | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-003 | QA and healing retain no live MCP surface | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-003 | Failover recomputes the exposure plan | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-004 | Unknown CLI version falls back safely | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-004 | Model or provider lacks required host capability | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-004 | Configuration dialect drift does not reach the subprocess | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-004 | Tool use itself is unsupported | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-004 | Adapter without a verified presentation boundary is ineligible | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-004 | Eager adapter presentation is structurally verified | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-004 | Native granularity represents every load posture | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-005 | Deferred tool is loaded before direct invocation | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-005 | Search returns only LLM-visible matches | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-005 | Search miss can be refined without widening the corpus | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-005 | Approval-sensitive actions keep their normal path | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-005 | Discovery does not bypass execution wrappers | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-006 | Pre-tool discovery initialization failure may fall back | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-006 | Partial tool execution blocks automatic replay | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-006 | Plain-text response is not a discovery failure | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-006 | Non-MCP effect blocks automatic replay | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-006 | Ambiguous effect evidence fails closed | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-007 | Skill references a tool outside the visible set | Skill identity/guidance does not register tools; session-visible set enforcement remains transferred. |
| REQ-core-tool-discovery-007 | Multiple CLIs receive one canonical skill identity | Canonical .agents source/digest and .claude physical-alias validation landed; per-adapter projection remains transferred. |
| REQ-core-tool-discovery-008 | Successful deferred session records bounded evidence | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-008 | Fallback reason is durable | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-008 | Receipt retention follows process-log policy | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-008 | Candidate and presentation attempts remain distinguishable | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-009 | Unverified runtimes use the compatible fallback | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-009 | Infrastructure definitions remain absent from LLM presentation | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-009 | Native search materially reduces initial schema load | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-009 | Native search finds intended tools within its bounded result set | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-009 | Native presentation introduces no behavioral regression | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-009 | Admission calculation is reproducible | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-009 | Runtime changes invalidate prior compatibility proof | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-009 | Every compatibility key field is binding | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |
| REQ-core-tool-discovery-009 | Implementation completion is canary-ready | Unlanded runtime/native/planning/receipt/admission obligation transferred; not synchronized. |

## Landed requirement-to-source and test map

These are existing code/test positions, not fresh native execution receipts.
The reconciliation changes documentation/specs only (Tests: +0 ~0 -0).

| Canonical requirement | Source | Existing meaningful coverage |
|---|---|---|
| core-daemon-002/003 | `src/butlers/daemon.py`; `src/butlers/config.py` | `tests/contracts/test_tool_presentation_inventory.py::test_actual_core_catalog_respects_group_name_and_type_gates`; `tests/core/test_runtime_config.py` |
| core-modules-001 | `src/butlers/modules/base.py`; `src/butlers/core/tool_catalog.py::validate_tool_metadata` | `tests/core/test_tool_catalog.py::test_invalid_or_partial_metadata_is_rejected`; `tests/contracts/test_tool_authorization_catalog.py` |
| core-tool-discovery-001 | `build_tool_catalog`; daemon `_finalize_tool_catalog` | `tests/core/test_tool_catalog.py::test_catalog_freezes_nested_definitions_and_stable_digests`; `test_catalog_reads_post_approval_definition_without_retaining_handler` |
| core-tool-discovery-002 | `tool_presentation_inventory.py`; `validate_presentation_inventory` | `tests/core/test_tool_catalog.py::test_inventory_validation_rejects_duplicates_missing_stale_and_owner_drift`; `tests/contracts/test_tool_presentation_inventory.py` |
| core-tool-discovery-003 | `RuntimeConfigAccessor.get_tool_exposure_policy` | `tests/config/test_runtime_config_policy_migration_integration.py` (real DB/direct accessor, not an implemented planner) |
| core-tool-discovery-007 | `skills.py::resolve_skill_identity` | `tests/features/test_skills.py::test_skill_identity_uses_canonical_agents_source_for_claude_alias`; `test_skill_identity_missing_or_unreadable_source_is_explicit` |
| runtime-config-api-001/002 | `src/butlers/api/routers/runtime_config.py` | `tests/api/test_runtime_config.py` (actual accepted values, tiers, persistence and error responses) |
| runtime-config-dashboard-ui-001/002 | `frontend/src/components/butler-detail/RuntimeConfigCard.tsx` | `frontend/src/components/butler-detail/ButlerManagementTab.test.tsx`; save/error code path clears only an unconfirmed policy edit |
| runtime-config-table-001 | `core_224_runtime_config_tool_exposure_policy.py` | `tests/config/test_runtime_config_policy_migration_integration.py` (constraint/default/independent accessors) |

## Current semantics and conservation

The API labels the stored policy hot and persists it without restart; no runtime
source calls the direct policy getter. The getter bypasses TTL on successful DB
reads but returns a cached policy on DB failure if available and otherwise raises.
A missing row with no cache raises. A missing legacy field uses `eager_filtered`;
closed values are constrained by the DB/API/seed contracts. No cache mutation by
the PATCH or next-session planner is claimed. Dashboard native-use copy is not
proof of an adapter implementation. No unavailable-policy fallback change or
new save-confirmation behavior is manufactured here.

Current canonical group lists and later Git-declaration/narrowing and sensitivity
clauses are carried forward, without claiming an obsolete fixed group/tool count.
Current descriptive authorization fields and dark-build disable behavior survive.
The generalist PATCH block carries the new policy persistence scenarios while
retaining its separate future delegation deletion. No other generalist clause,
RFC 0038 signed text, runtime implementation, test or guard baseline is changed.
The four disjoint published hunks identified at public 53dd remain outside this
allocation (5033 daemon append; 4867/4805/4745 later model-routing sections).

## Historical design retained as successor rationale

The design below is the original approved target, not an implementation or
admission claim. Its per-attempt/native/receipt/evaluation portions remain
superseded by RFC0038 (bu-r980fk) and adapters bu-kiuh8v as accounted above.

## Context

See `proposal.md` for motivation and RFC 0027 for the full design contract.
Today, core/module groups decide which handlers FastMCP registers at daemon
startup, and the spawner gives each ordinary runtime exactly one MCP server URL.
Every MCP client sees the same complete registered list. Runtime adapters share
a coarse `tool_use` declaration but construct MCP configuration, control host
tool availability, and parse tool evidence through different code paths.

The FastMCP 3.4.2 public transform can filter before native pagination but
cannot observe the real request cursor. The same offset-only pagination remains
in 3.4.7, 4.0.0b5, and pinned upstream main
`977ba66c811728aff1522bca48e8cc86eb2aec15`. The dated source/release/search
record in `about/legends-and-lore/reviews/0027/round-4-option-b.md` found no
public commitment as of 2026-08-31. Owner-selected Option B therefore keeps
FastMCP complete, makes adapter-rendered filtering the search-corpus boundary,
and uses verified runtime-native search/load for material context reduction.

The implementation must preserve one canonical wrapped handler per tool,
existing infrastructure clients, one-butler MCP isolation, approval behavior,
and a usable eager path on every supported CLI. It must not rely on the
installed CLI generation matching current upstream documentation.

## Goals / Non-Goals

**Goals:**

- Separate the registered/callable surface from the LLM-presentable surface.
- Make runtime-native Tool Search and deferred full-schema loading the primary
  context-efficiency behavior over a runtime-neutral bounded corpus.
- Make the corpus allowlist and adapter rendering explicit without treating
  filtering alone as the optimization.
- Recompute presentation per runtime attempt, including failover attempts.
- Preserve typed direct MCP calls and all existing execution wrappers.
- Give the owner a DB-backed `eager_filtered`/`auto` control with conservative
  migration behavior.
- Produce content-blind evidence sufficient to compare correctness, token load,
  latency, and fallback behavior.

**Non-Goals:**

- Caller authentication, per-session call authorization, or direct-call denial
  for definitions omitted from the LLM projection.
- Code Mode/programmatic nested tool execution in v1.
- A generic tool-search/invoke gateway for eager-only runtimes.
- Semantic prompt classification in the daemon.
- Automatic CLI upgrades or equal token reduction across runtime families.

## Decisions

### Decision 1: Add a presentation catalog, not another handler registry

The daemon builds immutable descriptors for the handlers FastMCP already owns.
The descriptor contains canonical name, module/group, logical namespace,
`llm_presentable`, `load_posture`, and schema/description digests. It contains
no executable function reference.

Catalog finalization occurs after all core and module tools register and after
approval gates install their final wrappers. Calls always resolve by canonical
name through the final FastMCP registry. This prevents deferred discovery from
retaining a pre-approval or pre-observability callable.

Existing unclassified module tools default to presentable/eager during the
migration. Core tools require a checked-in exhaustive classification before a
butler can use `auto`.

Alternative rejected: registering copies on a second FastMCP server. That risks
wrapper drift, duplicate names, and approval bypass.

### Decision 2: Keep FastMCP listing complete; adapters own the search corpus

FastMCP remains the complete canonical handler registry and `tools/list`
surface over streamable HTTP and SSE. The runtime MCP URL carries only existing
session/trigger correlation. There is no LLM-presentation marker and no second
filtered MCP endpoint.

After runtime/model resolution, the spawner builds an immutable
`ToolSurfacePlan` whose digest binds attempt identity, catalog generation,
enabled-module snapshot, policy, and exact compatibility key. The plan contains
the canonical-name search corpus plus immutable eager definitions or summaries.
The adapter converts canonical names to host-native names, bounds the corpus
through supported public host configuration, and configures verified native
search/load behavior. Filtering determines eligibility; deferring full schemas
until search time produces the material context savings.

Runtime hosts remain opaque MCP clients and may internally enumerate/page the
complete canonical list. Butlers does not own or validate those host cursors.
They must remain invocation-local and non-model-visible. Conformance uses a
paginated hidden-sentinel server to prove the adapter/host combination never
serializes hidden names, schemas, descriptions, parameters, or counts into
model input/search. A tuple that cannot prove this boundary is ineligible; the
complete list is not a fallback presentation.

The host filter is presentation, not authorization. `tools/call`, handler
lookup, approval, module-state, attribution, and caller checks stay canonical.
Private FastMCP handlers, monkeypatches, duplicate servers, proxies, and
JSON-RPC/SSE rewriting are prohibited.

Alternative rejected: signed caller scopes in this change. That requires a
fleet-wide credential and transport migration and changes doctrine beyond the
tool-discovery motif.

### Decision 3: Use two owner policies and three internal modes

`runtime_config.tool_exposure_policy` is a hot enum:

- `eager_filtered`: require a verified eager-capable profile to serialize the
  full LLM-presentable list; otherwise the candidate is ineligible.
- `auto`: select `native_deferred` only for an exact verified tuple whose native
  granularity represents every allowed load posture; otherwise use a separately
  verified eager profile/candidate or declare the tuple ineligible.

Internal plan modes are `none`, `eager_filtered`, and `native_deferred`. QA and
healing always select `none`. Existing DB rows, absent TOML seed fields, and
new migrations default to `eager_filtered`, so neither schema deployment nor
design acceptance activates native behavior.

An `all_deferred` host cannot satisfy native mode when any presentable tool has
`load_posture=eager`, and cannot satisfy strict `eager_filtered` at all.

The accessor cache is invalidated after PATCH. New runtime attempts see the
updated hot policy; in-flight attempts keep an immutable plan.

Alternative rejected: exposing force-native values. Operators must not be able
to assert unverified CLI integration support.

### Decision 4: Negotiate presentation separately from model feature fit

Model-catalog capability descriptors answer whether a candidate can use tools,
produce structured output, or resume. Presentation support is an integration
fact about the runtime binary, adapter profile, configuration dialect, and
model/provider tuple. It therefore uses a separate repo-owned compatibility
registry that model-catalog overrides cannot forge.

The canonical compatibility key contains runtime type, executable artifact
digest/identity/exact version, adapter-profile revision, configuration dialect
and normalized digest, transport/protocol version, and exact provider/model
IDs. Configuration normalization replaces session IDs, temporary paths, tokens,
credentials, and authorization headers with typed sentinels before canonical
sorted-key hashing. The immutable record adds manifest/fixture/result digests
and verification time as evidence fields. Any key mismatch invalidates the
record and makes `auto` choose a verified eager-capable profile/candidate; when
none exists, the tuple is ineligible rather than exposed unfiltered.

The associated profile also records the public allowlist dialect,
canonical-to-host name mapping, whether filtering changes model availability or
only call permission, eager/native controllability, native deferral granularity,
native search result-limit/ordering behavior, host-pagination isolation, and
parser/receipt support. Permission-only controls do not satisfy
model-presentation filtering.

The adapter invocation-preparation step may reuse the existing
`build_config_file()` method or its current runtime-specific writer. It also
renders the plan allowlist and rejects runtime arguments that could override
the generated MCP/filter configuration. The design does not require all
adapters to converge on one file format.

### Decision 5: Build the plan inside each failover attempt

The current spawner constructs one MCP configuration before the model-failover
loop. The new preparation boundary moves presentation planning, host allowlist,
and MCP assets into each attempt after the candidate runtime/model is known.
Environment ownership,
resume-handle isolation and one logical session row remain unchanged. The
existing cap continues to bound candidate/model attempts; each candidate owns
one initial presentation subattempt plus at most one replay-safe eager
subattempt. Adapter-internal transport retries stay inside the same presentation
subattempt and existing process diagnostics.

Native-to-eager retry is allowed at most once and only for a closed native
transport/protocol failure when the same candidate has a separately verified
eager-capable profile and merged evidence is complete and proves zero MCP and
zero non-MCP effect-capable actions. Without that profile, no presentation
fallback occurs. Shell/command, file-edit/apply-patch, browser/computer, app,
unknown, and parser-ambiguous actions block replay. A valid no-tool/plain-text
response is not failure evidence.

### Decision 6: Search first, then load and call the direct typed tool

Native search initially exposes adapter-rendered bounded namespace/server
summaries and selected eager definitions. Its native index contains only the
attempt corpus. For fixed manifest queries, every intended tool must appear
within the profile-declared result limit, every result must belong to the corpus,
and hidden matches must remain absent. Extra eligible matches and precision are
reported diagnostics unless a separate threshold is approved. Search permits
miss refinement without widening the corpus and may load only presentable full
definitions. The eventual call uses the canonical name/schema and crosses the
normal MCP wrapper.

Namespaces reuse module/group concepts and target fewer than ten related tools
where natural. They never rename handlers.

Code Mode remains reserved because current CLI processes can carry filesystem,
shell, configuration, and network authority beyond a nested catalog. A future
design must provide an enforceable narrower capability, not metadata or prompt
guidance alone.

### Decision 7: Persist a bounded attempt array in the existing TTL store

`session_process_logs` remains one TTL-governed row per logical session. An
additive `tool_surface_attempts JSONB NOT NULL DEFAULT '[]'` column stores at
most the spawner's existing candidate-attempt cap. Each candidate item contains
an ordered one-or-two-element `presentation_subattempts` array using the closed
receipt schema from RFC 0027. Updates address candidate and presentation indexes
without discarding earlier failover or discovery-fallback evidence.

Raw queries, prompts, schemas, descriptions, arguments, results, credentials,
provider exception text, and command output are not included. Existing stderr
storage remains governed by its current separate contract.

Alternative rejected: a new unbounded discovery-event table. Attempt-level
receipts share the process log's 14-day lifecycle and do not need independent
query semantics.

### Decision 8: Native Tool Search admission is evidence-gated

The checked-in conformance manifest fixes scenario IDs, expected outcomes,
runtime samples, allowed retries, cache conditions, malformed-schema
dispositions, and admission-report shape. The credential-free lane uses at
least 100 synthetic tools and separately tests complete canonical HTTP/SSE
listing plus adapter-rendered eager/native model input. A paginated hidden-
sentinel server proves invocation-local host pagination cannot leak hidden
definitions, schemas, or counts. It also tests intended-tool recall within the
declared result limit, corpus-only results, hidden-result exclusion, miss refinement, malformed definitions,
on-demand typed loading, canonical invocation, and receipt extraction. The
authorized lane uses representative real runtime tuples and tests task success,
no-tool completion, approval preservation, attribution, tokens, latency, and
cache behavior without persisting sensitive content.

A tuple is eligible only when every mandatory scenario passes with no new task,
approval, attribution, replay, or final-outcome failure, and native mode reduces
canonical compact sorted-key UTF-8 initial tool-definition bytes by at least 50
percent from its eager synthetic baseline. Version strings and feature flags
alone cannot enable it. Latency, cache, and total-token changes are reported but
are not pass/fail claims without a separately approved threshold.

## Risks / Trade-offs

- **[Risk] Eager-only runtimes see modest savings.** → Document behavioral
  parity separately from token reduction; do not broaden surfaces on the
  assumption that eager filtering solves schema cost.
- **[Risk] Native search misses or ranks the required tool poorly.** → Admit an
  exact tuple only after representative top-k recall, corpus/hidden-boundary,
  and miss-refinement cases pass; report precision, extra eligible matches, and
  discovery misses during canary evaluation.
- **[Risk] An opaque host MCP client serializes hidden definitions.** → Bind a
  fresh public host allowlist to every plan and use paginated hidden-sentinel
  conformance to prove hidden definitions, schemas, and counts never enter
  model input/search. Host cursors remain invocation-local and non-model-visible.
- **[Risk] A stale schema remains in model context after module disable.** →
  Snapshot module state for planning and retain the existing call-time guard.
- **[Risk] Discovery events pollute tool-loop or side-effect evidence.** → Keep
  discovery receipts separate from canonical MCP call records and guardrail
  counts.
- **[Risk] Upstream CLI behavior changes without a version bump.** → Bind the
  compatibility key to artifact, adapter-profile, and normalized-configuration
  digests, retain fixture/manifest/result digests as evidence, and on any key
  mismatch select a separately verified eager-capable profile/candidate when
  one is available, otherwise mark the tuple ineligible.
- **[Risk] Hidden presentation is mistaken for security.** → Keep host filters
  presentation-only, leave `tools/call` unchanged, use explicit UI/docs copy,
  and retain separate handler-specific authorization tests.
- **[Risk] Approval wrapping is bypassed by cached callables.** → Store no
  callable in the catalog and finalize descriptors only after gate installation.
- **[Trade-off] Runtime-config scope expands the feature.** → The hot owner
  control is retained because it supplies a safe canary and immediate rollback;
  all associated table/API/UI contracts are included in this changeset.

## Migration Plan

1. Add the runtime-config policy and process-log receipt column with
   conservative defaults; ship API/UI support without changing session
   presentation.
2. Add catalog metadata, exhaustive core classification, complete canonical
   HTTP/SSE listing tests, and eager-filtered receipts. Keep every butler on
   `eager_filtered`.
3. Move per-attempt preparation inside failover and render the immutable
   canonical-name search corpus through each host's supported public
   configuration. Prove hidden sentinels never reach model input/search and
   reject any tuple that cannot enforce this boundary.
4. Produce compatibility records from the synthetic and authorized lanes,
   including top-k recall, corpus/hidden boundaries, miss behavior, precision
   diagnostics, typed loading, initial-byte savings, filter dialect, name
   mapping, eager/native controllability, and host-pagination isolation. CLI
   binary upgrades, if needed, land as separate reviewed dependency changes.
5. Produce a canary-ready compatibility record and rollback runbook. Setting a
   live butler to `auto` requires separate operator authorization.
6. After an authorized canary, expand only if task success, discovery misses,
   retries, tokens, latency, and approval evidence pass the recorded gate.

Rollback sets the affected butler policy to `eager_filtered`. The next attempt
uses a separately verified eager-capable adapter projection only when a matching
profile/candidate is available; otherwise its tuple is ineligible. A tuple that
mandates native deferral is skipped under that policy. Registered handlers,
session history, and in-flight plans are not rewritten. A migration downgrade
removes the new fields only after all rows use verified eager behavior or are
marked ineligible.
