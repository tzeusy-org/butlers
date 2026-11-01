# Shared contract coverage and release boundaries

**Contract carrier:** bu-dhloi1.19, proposed exact text dated2026-10-24.
**Adoption:** pending independent HIGH normative/conformance review and reserved
exact-text adoption. No draft or historical direction is labelled adopted here.
**Source baseline:** main `5d4a979aba57b24da0f6c14734197c381d111bbf`.
The common field/state/API home is
[RFC0038 S0–S8](../../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md#proposed-shared-switch-and-m7-interfaces-2026-10-24).
All implementation and test paths below are bounded owners/seams, not a claim
that the future contract is implemented or its tests already pass.

## Actual source baseline and preservation

| Actual carrier | Baseline fact / preserved obligation |
|---|---|
| `roster/switchboard/migrations/047_goal_ledger.py` / `src/butlers/core/goal_ledger.py` | sw_047 in switchboard; goals, invocations, effects, budgets, admission_decisions, goal_inbox; ordinary runtime role/immutability/uniqueness, all statuses and receipt identities |
| `roster/switchboard/migrations/050_admission_attempt_bindings.py` / `roster/switchboard/migrations/051_record_only_admission_reason.py` | Delivered sw_047 → sw_050 → sw_051 chain; immutable attempt/source-to-canonical decision bindings, composite FK/backfill, Switchboard-only runtime RLS, terminal recorded_without_goal, atomic constraint replacement and durable-row downgrade refusals; held048/049 not delivered |
| `src/butlers/core/goal_admission.py` | Verified origin/cause, exact source dedup and current reply/effect binding; joins cannot reset budgets, ceiling or exposure |
| `src/butlers/core/tool_catalog.py` | Current descriptors stay authoritative; .6 categories, .14 depends_on, .12 profile are one serial descriptor evolution |
| `src/butlers/context_bus.py` / RFC0009 | Current context/DND/suppression remains until .9/.21 successor parity; .17's actual PR4980 merged as2be522b2d65fc9bfa907fef4666e8723411b6e5a |
| `src/butlers/core/condition_ledger.py` | Existing condition episodes/receipts are preserved, not mistaken for the new predicate AST or a second canonical evaluator |
| Existing RFC0038 D6 / D5+D18 ASR / approval+selection | Conservative current rule remains until exact successor adoption; no typed laundering, audio self-certification, approval or source-selection bypass |
| PR4872 at109923674372b941184bf2e804cebdf972887481 | All33 existing replacement BEFORE spans, full protected paragraphs/Source lines and16 insertion gaps retained. No D20 text copied/adopted/repaired and no enqueue permission inferred |

The foreign research018/current owner-selection018 collision predates this
carrier. New .19 IDs avoid both; this contract neither repairs foreign adopted
text nor replaces current017/018. Future union must preserve every adopted
research byte and current public contract, under the relevant separate custody.
Distinct held Gemini, fleet/model/collision-ledger, passport-inventory and broker
source hunks are not incorporated by this authoring work.

## First-switch requirement-to-owner coverage

Every listed requirement includes complete S0–S8 fields, permissions and
failure/retry/concurrency/rollback obligations, with executable WHEN/THEN
scenarios in its owning delta. Numeric suffix ranges below are inclusive.

| Requirement IDs | Bounded implementation owner | Source / future behavior seam | First-switch boundary |
|---|---|---|---|
| REQ-capability-authorization-019 | bu-dhloi1.6 | Actual goal ledger/admission, catalog and broker/domain stores; `tests/switchboard/test_goal_ledger_postgres.py`, `tests/switchboard/test_goal_admission.py`, `tests/core/test_tool_catalog.py`, `tests/migrations/test_goal_ledger_migration.py` | Same-transaction labels, clean sibling vs predecessor, unknown legacy/deferred data; no held typed/cited rule |
| REQ-goal-orchestration-009, REQ-goal-orchestration-010, REQ-goal-orchestration-011, REQ-core-sessions-002 | bu-dhloi1.7 | Goal ledger/wait/runner after existing bu-u5s4w8; admission/ledger/migration owner files above | One thread/plan/epoch/inbox; fire/admit/intent atomicity; complete sw_047 → sw_050 → sw_051 mapping, S2.1 discriminated/legacy inbox and S2.2 cumulative-generation rearm with safe rollback |
| REQ-core-spawner-014, REQ-module-memory-016 | bu-dhloi1.8 | One broker-mediated context composer; `tests/modules/memory/test_tools_management.py`, `tests/modules/memory/test_content_authority.py`, `tests/core/test_spawner_memory_context.py` | Read-pure rank fusion, finite whole packets, honest degraded sources, default-sensitive exclusions |
| REQ-perception-registry-001, REQ-perception-registry-002, REQ-perception-registry-003 | bu-dhloi1.9 | Registry/store and restrictive migrations; `tests/core/test_context_bus.py`, `tests/integration/test_owner_conditions_roundtrip.py`, `tests/migrations/test_owner_conditions_migration.py` plus owning new perception role/CAS cases | Representation dark; unique null subject, exact S3.1 kinds/aliases/eight modes, S3.2 immutable confirmation/late/retraction identity and event/CAS authority, all four states; no reader activation |
| REQ-capability-authorization-020 | bu-dhloi1.10 | Actual checked broker call and owner policy API; `tests/modules/approvals/test_prepared_execution.py`, `tests/modules/approvals/test_rule_use_claim.py` plus prerequisite broker owner scope | Tighten-only CAS, versioned holding receipts; .18 owns policy wording |
| REQ-condition-evaluation-001, REQ-condition-evaluation-002, REQ-core-scheduler-001 | bu-dhloi1.11 | One conditions loop/cursor/outbox; `tests/core/test_owner_conditions.py`, `tests/integration/test_owner_conditions_roundtrip.py`, ledger and scheduler owners | Minimal AST only; registration snapshot, duplicate/crash/late/unknown/clock cases; no M7 watches |
| REQ-model-catalog-005 | bu-dhloi1.12 | Owner-turn timestamps, role/profile and capacity consumer; `tests/connectors/test_telegram_bot_connector.py`, `tests/core/test_runtime_adapter.py`, `tests/core/test_runtime_config.py` plus runner admission owners | owner-fast inside current resolution/capacityfive, comparable baseline only; no absolute M7 pass |
| REQ-owner-delivery-001, REQ-owner-delivery-002, REQ-owner-delivery-003, REQ-core-notify-036 | bu-dhloi1.13 | Sole subject/token/delivery lifecycle; `tests/core/test_approval_delivery_worker.py`, `tests/core/test_approval_callback_tokens.py`, `tests/integration/test_approval_delivery_worker.py`, `tests/integration/test_telegram_approval_callback_flow.py` | Existing approval/effect identities, one action across surfaces, honest delivery and source-ID migration |
| REQ-core-notify-037, REQ-owner-delivery-003, REQ-perception-registry-003 | bu-dhloi1.14 | One dependency reducer and canonical pending-ask recovery; `tests/core/test_tool_catalog.py`, `tests/core/test_infra_conditions.py`, `tests/integration/test_infra_conditions_roundtrip.py` plus broker/runner owners | depends_on after .6; Run/Drop not rerun; unknown projection never healthy or authority |
| REQ-capability-authorization-021 | bu-dhloi1.15 | Shared workload gateway/profile and policy union; `tests/config/test_runtime_cli_sandbox_compose.py` and existing egress owners in `tests/config/` | Real workload enforcement, exact authenticated internal-peer identity/port and external host/SNI/private/redirect/rebinding refusal, outage/cursor; unprovisioned static-key requests refuse |
| REQ-perception-registry-002, REQ-perception-registry-003 | Dots bu-jv1moa.17 | Existing owner.place reducer consumes .9 event/CAS; no second observation/presence API | Representation prerequisite; sole Home reducer, original ten-minute retraction/fifteen-minute late and history/reader wire; actual producer activation retains its original switch authority |
| REQ-perception-registry-001, REQ-perception-registry-003, REQ-core-spawner-014 | bu-dhloi1.21 | Clock/modes and named current-context consumer migration; .17 current reader remains until parity | Exact twelve-signal/alias/eight-mode mapping plus late/retracted reader parity and one consumer transition; no alternate owner_clock truth |
| REQ-perception-registry-002 | bu-xxfkhk | Confirmed routine expect.* feeder, with domain source truth | No inferred routine/obligation authority; original feeder/activation gates remain |
| REQ-goal-orchestration-012 | bu-dhloi1.19 / existing M6 bu-enbrs2 | This coverage, approval matrix and exact adopted-source handoff | .19 contract review/adoption is distinct from runtime/queue/operations |

The implementation owner supplies actual new tests at its named production seam,
not a second static scanner. Current REQ-testing-053, full parameter/assertion
preservation, real migrated-Postgres roles and exact-head hosted/protected gates
remain the verification path. The above future seams are not test results.
Existing source-specific reader contracts (including bounded passport inventory
if landed) retain their own authority, read budgets and completeness semantics.

## First-switch versus M7/later allocation

| Stage / existing owner | Required interface / boundary | Evidence and hold |
|---|---|---|
| First switch .6–.15 and named representation/producer successors | Requirements above; minimal conditions, conservative D6, waiting and delivery identity, context/read purity, owner-fast measurement | Actual selected source owners, migrated-role/call/rollback evidence and existing one-release gate; no unbuilt behavior accepted |
| M7 .1.2; owner hold .1.1 and evidence .1.11 | Typed boundary enforcement/source containment consumes I/D/C and the same event registry | Semantic relaxation only after its actual gate measurement and exact text; cited-label rule bu-7ry1sy remains separately held |
| M7 .1.3 | Exact decision sets/evidence-based completion consume subject/action/epoch/plan identity | Richer set UX does not suspend current always-per-event, wait-envelope or selection rules |
| M7 .1.4 | Owner-saved procedures/duplicate recall retirement consume read-pure recall/labels | No procedure steering without owner save tap; no premature storage deletion |
| M7 .1.5 | Approved notify-only watches/advanced conditions consume the one evaluator | Exact RFC0019 narrowing adoption plus owner-confirmed watch; not arbitrary action chains |
| M7 .1.6 / .1.7 | Streaming/Stop/latency and local speech use same goal/delivery/fence | Text retained; no new reasoning path, provider event stream or live audio invoked by this carrier |
| M7 .1.8 | Surface selection/Why consumes typed reason and subject | Desk-first3-minute fallback remains M7; original reply target at first switch |
| M7 .1.9 / .1.10 | Semantic witness/bounded healing and full M7 cutover evidence | No first-switch heal activation; operational repairs/restores keep separate authorization |
| Later push-surface portion of .22 / static-key .25 | REQ-owner-delivery-004 privacy interface; S7 static-key custody interface | Provisioning/platform/urgency/step-up and actual static-key credential use remain later-owned; disabled/refusal behavior suffices before they are enabled |

## Mutation ownership and serialized transition

1. .19 records exact adopted commit after review/adoption; .18 writes its own
   policy terms against that vocabulary. Neither source author mutates the
   other's normative policy.
2. .9 adds registry/schema/role functions and sole writer API; .7 adds its
   switchboard lifecycle schema/FKs and .11 condition cursor/outbox, all with
   restrictive migration/bootstrap behavior and honest conversion receipts.
3. .6 extends descriptors/categories and producer labels, .14 depends_on/health,
   then .12 profile. Original event identities and labels survive backfill,
   deferred/replay adapters, inbox/receipt and recovered predecessor paths.
4. Named source producers (.9/Dots/.21/.14/expect.*), readers (.8/.7/.13) and
   evaluator consume those contracts dark. No competing store/language/callback
   family or permanent dual-writing is permitted. .13 alone owns canonical
   delivery mutation; .10/.14/.22 provide typed policy/cause/urgency inputs.
5. Candidate replay/shadow parity proves counts, FK/uniqueness, role isolation,
   current external contracts, labels/receipts and failure/rollback outcomes.
   Only then may the existing one-release approval activate sole writers,
   readers and enforcement. Rollback holds admission/consumers, keeps evidence
   and never restores unsafe grants, drops unknown effects or reopens tokens.

## Release and approval boundary matrix

| Boundary | Existing authority | What it does not authorize |
|---|---|---|
| Owner directions/two cutovers | bu-09lapg; bu-qlx055; bu-ywqvyh; bu-u33sb7; bu-mq929j; bu-w9z7me; bu-nt2eda; bu-l8y3fn; bu-7ry1sy | No blanket exact amendment adoption, live operation or hidden policy |
| Scheduling release | bu-276pli | No switch/deploy, owner-answer substitution or operational action |
| .17 current context adoption | Closed PR4980 and its narrow recorded routine/perception conformance delegation | No blanket adoption of unrelated .19 RFC text |
| Concrete .19 contract closure | Independent HIGH normative review, exact-text adoption where reserved, immutable adopted commit and M6 handoff under original7AC | No runtime proof or original-bead closure from a draft PR alone |
| Dark source implementation | Named existing owner packets and exact adopted requirement IDs | No ingress/schedule/live dashboard activation or production/secret/host change |
| M6 candidate/rehearsal/release | Existing bu-enbrs2 and preserved original candidate/replay/adversarial/stop/recovery/owner-release sequence | No archive/release while selected first-switch successors are absent; no gate on M7/later consumers |
| M7 semantic/cutover | Existing .1.1/.1.10/.1.11, cited-label hold and named owners | No relaxation inferred from typed values, display, prior baseline or first-switch green |
| Gateway/static keys/push/restore | Existing source and explicit separate provisioning/operational authorization | No credential read/use, live host addition, research, deployment or restore |

## M6 existing-candidate handoff (not a new candidate or gate)

The candidate map above updates the existing M6 coverage, not its original AC or
release sequence. At this draft head the adopted-contract commit and delivered
source SHAs are **pending**, never fabricated from checked historical tasks.
After exact adoption, ROOT applies the same requirement IDs/owners and each
actual delivered source SHA to bu-enbrs2, retaining pending state for absent
successors. Candidate identity and every executing receipt must bind that
candidate; authoring checks are not that evidence.

Real canonical edges observed for this carrier: .19's blocking predecessors
bu-s4dkj6 and .17 are closed; first-switch .6/.9/.15 each have a blocking .19
edge; M7 .1.2 and .1.11 explicitly depend on .19. M6 has real predecessor
paths via .6/.9/.15 to .19. A direct M6-to-.19 edge is not claimed or silently
added. Reverify real edges before .19 closure. Do not let M6 archive/release
an old contract while its selected source successors are absent, and do not
add M7/later-only consumers as M6 prerequisites. Existing M6 edges, including
.22's derived-urgency delivery work, remain intact; the later push/platform
portion is not a reason to delete or enlarge that existing first-switch scope.
