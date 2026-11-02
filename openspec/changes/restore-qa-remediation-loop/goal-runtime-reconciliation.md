# Goal-runtime reconciliation (bu-6a9xxw.8)

Status: conforming reconciliation of the adopted QA repair loop with
[RFC 0038](../../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md)
D18 and [RFC 0015](../../../about/legends-and-lore/rfcs/0015-qa-staffer-discovery-investigation-pipeline.md).
Baseline reviewed: main `acb1d04f7`. This file is specification and allocation
work. It implements nothing, activates nothing, adds no publisher, credential,
concurrency or spending, and makes no deployment or recovery claim.

Authority: owner decision `bu-gjmx8w` adopted the corrected payload at
`d5c889e6278cabb4f4aeb5e0d024c6ce1f88e2e5`. Its predecessor
`62625c30c980b982265f9f7867d9ce11d2dbbe96` is history, not current evidence.
The amendments below conform the adopted text to already adopted RFC 0038
decisions and to the `generalist-lead-focused-delegation` deltas. They introduce
no new owner-level choice. A predecessor that later changes one of the cited
contracts returns here, not to worker invention.

## 1. Which RFC 0038 kind each QA operation is

RFC 0038 D18 names QA investigation agents as **engineering executors** and QA
triage reasoning as a **staffer goal**. Everything else in the repair loop is
deterministic infrastructure. The loop is therefore not one goal, and QA is not
an unrestricted lead.

| Kind | QA operations | Contract that governs it |
|---|---|---|
| Deterministic QA infrastructure (DI) | discovery sources and source progress, triage fast paths, finding persistence, incident correlation, pending-work lease/ack, admission gates, incident budget reservation, PR/feedback polling, journal, deployment and recovery observation, summary/case projections | RFC 0015 as amended by `contract-amendments.md`; QA-owned migration-managed storage; sanctioned views only |
| Engineering executor (EE) | initial and follow-up investigation sessions (`trigger_source = "qa"`) | RFC 0038 D18; core-spawner delta (Trigger Source Tracking, Credential Isolation, Goal Invocation MCP Config Names Only the Broker, System Prompt Composition); RFC 0015 sandbox and anonymization |
| Restricted publisher (RP) | branch/PR creation and permitted updates, publication receipts, ambiguity holds | `confine-qa-publication-authority` (bu-mc01y5); unchanged here |
| Service-plane relay (SR) | reactive `report_error` to `report_finding`, dashboard report delivery | RFC 0038 D7 service-plane relays; staffer-qa REQ-staffer-qa-003 and the durable-dashboard change |
| Staffer goal (SG) | `qa_triage_v1` judgment over one acknowledged canonical finding, only in `ISOLATED_CANDIDATE` and `SWITCHED_RELEASE` | RFC 0038 D5/D10; generalist staffer-qa delta, Dashboard Report Durable Inbox Lifecycle |

Rules that follow from the table and are now carried by the amended clauses:

- **An executor launch is not a goal invocation.** It enters no goal ledger as an
  invocation, holds no invocation token, broker grant or MCP server, and starts
  from the surviving Spawner with trigger source `qa`. Its session record stays
  in the QA session store. Its credentials are only those the QA executor
  contract declares; that set excludes every provider or remote-repository
  credential, which stays with the restricted publisher.
- **Incident admission is not goal admission.** The goal admission/ledger is
  the only logical journal for the `qa_triage_v1` staffer goal, and only for it.
  Incident generations, pending work, fences and launch reservations stay
  QA-owned deterministic records, as REQ-staffer-qa-003 already requires
  ("investigation admission SHALL remain infrastructure operations under their
  existing owners").
- **Two budgets, never merged.** The incident work budget (three launched
  executor sessions and sixty cumulative active minutes per incident
  generation) bounds executor work. The D10 staffer-goal budget bounds the
  triage goal's invocations. A triage goal neither reserves nor consumes the
  incident budget, and an incident reservation is not a goal budget. Both fail
  closed when unavailable.
- **A goal cannot launch an executor.** A `qa_triage_v1` judgment is exposed
  data. It cannot admit an incident revision, reserve budget, launch or resume
  an executor, or change a publication state. Investigation stays with the
  deterministic gates.
- **Invocation-bound evidence keeps its meaning.** In the QA repair loop,
  "invocation" means one engineering-executor session bound by trusted dispatch
  metadata. Failures of goal invocations reach QA as findings through the
  sanctioned `v_qa_recent_failures` view, keyed by `invocation_id`, with verified
  goal origin driving recursion checks.
- **Relays start no goal.** Reactive findings use the Switchboard service-plane
  relay, never the deleted generic `route` tool or the session listener.

## 2. Requirement mapping

Disposition: **Retained** means the adopted text already conforms and is
unchanged. **Amended** means a conforming clause or scenario was added in this
change; no adopted clause was removed. Owner is the allocation letter and bead.
The seam is the one real verification seam the owner extends; it is a
requirement for the owner, not executed evidence (Tests: +0 ~0 -0 here).

| Requirement | Kind | Disposition | Reason | Owner | Verification seam |
|---|---|---|---|---|---|
| REQ-qa-dashboard-001 QA Cases API | DI | Retained | Projection of QA records; no goal-runtime surface | B `bu-6a9xxw.2` | `tests/api/test_api_qa_cases.py` |
| REQ-qa-dashboard-002 QA Summary KPI Extension | DI | Amended | Usage covers executor sessions only; staffer-goal usage is goal-ledger accounting and is never folded in or shown as zero | F `bu-6a9xxw.6` | `tests/api/test_api_qa.py` |
| REQ-qa-dashboard-003 QA Overview Page | DI | Retained | Consumes the summary contract; no routing or classifier surface | F `bu-6a9xxw.6` | `frontend/src/pages/QaOverviewPage.test.tsx` |
| REQ-qa-dashboard-004 Investigation Detail Page | DI | Retained | Same dossier component; no goal-runtime surface | B `bu-6a9xxw.2` | `frontend/src/components/qa/__tests__/dossier.test.tsx` |
| REQ-qa-dashboard-005 Case Dossier Layout | DI | Amended | Failing goal invocations are ledger identities, not `/sessions/:id` rows; no dead door | B `bu-6a9xxw.2` | `frontend/src/components/qa/__tests__/session-doors.test.tsx` |
| REQ-qa-dashboard-006 QA API Endpoints | DI | Retained | Force patrol, breaker, dismissals stay QA control surfaces; their MCP descriptors are ungranted per REQ-staffer-qa-003 | C `bu-6a9xxw.3` | `tests/api/test_api_qa.py` |
| REQ-qa-investigation-dispatch-004 Investigation Creation from QA Finding | DI | Retained | Pending work and attempt creation are deterministic infrastructure | C `bu-6a9xxw.3` | `tests/integration/test_qa_pipeline.py` |
| REQ-qa-investigation-dispatch-005 QA Investigation Agent Prompt | EE | Retained | Already excludes `bd`, push and PR authority; core-spawner delta removes situational context | E `bu-6a9xxw.5` | `tests/core/qa/test_prompts.py` |
| REQ-qa-investigation-dispatch-006 Phased Investigation Workflow | EE + DI | Retained | Phase sessions are executor sessions; the lifetime cap is the incident budget, distinct from D10 | A `bu-6a9xxw.1` (lineage), C `bu-6a9xxw.3` (budget) | `tests/daemon/test_qa_jobs.py` (A); `tests/integration/test_qa_pipeline.py` (C) |
| REQ-qa-investigation-dispatch-007 Investigation Outcome Recording | DI | Retained | Outcome axes and breaker events concern executor sessions | B `bu-6a9xxw.2` | `tests/core/qa/test_dispatch.py` |
| REQ-qa-investigation-dispatch-008 Investigation Notes Artifact | EE | Retained | Binding is to the executor invocation; no MCP or broker path | B `bu-6a9xxw.2` | `tests/core/qa/test_investigation_notes.py` |
| REQ-qa-investigation-dispatch-009 Commit-Time Diff Snapshot | EE | Retained | Pinned base..head within the executor checkout | B `bu-6a9xxw.2` | `tests/core/qa/test_diff.py` |
| REQ-qa-investigation-dispatch-010 Journal Event Emission | DI | Retained | Deterministic journal | B `bu-6a9xxw.2` | `tests/core/qa/test_journal.py` |
| REQ-qa-investigation-dispatch-011 QA-Owned Runtime and Launch Authority | EE | Amended | Names the launch as a D18 engineering executor from the surviving Spawner, outside goal ledger, broker and D10 | A `bu-6a9xxw.1` | `tests/daemon/test_qa_jobs.py` |
| REQ-qa-investigation-dispatch-012 Evidence-Driven PR Follow-Up | EE + RP | Amended | A follow-up is an executor launch, never a goal; review feedback is untrusted data and cannot widen authority | E `bu-6a9xxw.5` | `tests/core/qa/test_github_pr.py` |
| REQ-qa-investigation-dispatch-013 Gate Sequence Preservation | DI | Retained | Gate 1 already uses verified goal origin or executor provenance | C `bu-6a9xxw.3` | `tests/core/qa/test_dispatch.py` |
| REQ-qa-log-scanner-001 Temporal Filtering | DI | Retained | Deterministic source; successor log backend owned by `bu-dss7he.13` | D `bu-6a9xxw.4` | `tests/core/qa/test_log_scanner.py` or its predecessor-produced successor |
| REQ-qa-log-scanner-002 Scan Performance Guardrails | DI | Retained | Deterministic caps | D `bu-6a9xxw.4` | `tests/core/qa/test_log_scanner.py` or its predecessor-produced successor |
| REQ-qa-triage-001 Cooldown Awareness | DI | Retained | Deterministic gate | C `bu-6a9xxw.3` | `tests/core/qa/test_triage.py` |
| REQ-qa-triage-002 Finding Persistence | DI | Retained | Already carries verified goal origin for goal-invocation failures | C `bu-6a9xxw.3` | `tests/core/qa/test_findings.py` |
| REQ-qa-triage-003 Incident Correlation and Reasoning Admission | DI | Amended | Separates incident admission from `qa_triage_v1` goal admission and keeps the budgets apart | C `bu-6a9xxw.3` | `tests/core/qa/test_provenance_and_dedup_linkage.py` |
| REQ-qa-triage-004 Three-Source Deduplication | DI | Retained | Deterministic fast path | C `bu-6a9xxw.3` | `tests/core/qa/test_triage.py` |
| REQ-qa-triage-005 Dismissal Management | DI | Retained | Server-derived actor; dashboard owner surface | C `bu-6a9xxw.3` | `tests/api/test_actor_attribution_sweep.py` |
| REQ-staffer-qa-011 Patrol Loop Lifecycle | DI + SR | Amended | Carries the generalist relay clause so the same-name collision converges (section 3) | D `bu-6a9xxw.4` | `tests/modules/test_module_qa.py` |
| REQ-staffer-qa-012 Bounded Discovery Progress and Coverage | DI | Amended | Goal-ledger invocation rows use `invocation_id` as their stable occurrence key | D `bu-6a9xxw.4` | `tests/core/qa/test_session_records.py` |

All 25 requirement IDs are mapped. Every original outcome and failure scenario
is retained; the seven amendments only add clauses.

## 3. Behavior matrix conformance

| Behavior | Where it is required | Goal-runtime agreement |
|---|---|---|
| Initial and follow-up work bind to a durable invocation and QA configuration | REQ-qa-investigation-dispatch-006, -011, -012 | Executor session lineage in the QA store; no goal or invocation token |
| Duplicate admissions share the incident budget | REQ-qa-investigation-dispatch-006, REQ-qa-triage-003 | Incident budget only; a triage goal never draws on it |
| Cancellation and stale launch ownership cannot orphan or double-execute | REQ-qa-investigation-dispatch-006, -011, -013 | Lifecycle-fenced capability; reservations reconcile against durable lineage |
| Malformed or inherited evidence is never fresh diagnosis | REQ-qa-investigation-dispatch-007, -008 | Executor-bound notes; goal output is never diagnosis evidence |
| Publisher ambiguity stays held | REQ-qa-investigation-dispatch-012, REQ-qa-triage-003 | Restricted publisher remains sole remote-mutation owner |
| Partial discovery, unknown usage or deployment never becomes recovered or free | REQ-staffer-qa-011, -012, REQ-qa-dashboard-002 | Unknown stays unknown, including staffer-goal usage |
| Replay and rollback retain receipts and never fall back to another butler | REQ-qa-investigation-dispatch-011, design Migration and rollback | No fallback to a reporting butler, goal runner or session host |

## 4. Patrol Loop Lifecycle collision

Both this change and `generalist-lead-focused-delegation` modify `staffer-qa`
**Patrol Loop Lifecycle**. The generalist block's normative change is the
service-plane relay (requirement sentence, `Source: RFC 0038 D7`, and the
Reactive findings scenario). This change's adopted block replaces the
not-durably-queued crash clause, the all-empty clean clause and the
unconditional mini-patrol coverage clause (recorded in
`intentional-replacements.md`).

Resolution, without overwriting either approved requirement:

1. This change's block now carries the generalist requirement sentence and
   `RFC 0038 D7` source verbatim, and already carried the relay scenario. It is a
   superset of the generalist block except for the clauses the owner adopted as
   intentional replacements under `bu-gjmx8w`.
2. If the generalist switch archives first, this change rebuilds against that
   baseline before its own archive (per `contract-amendments.md`, Application
   and overlapping-change order) and keeps the relay clauses.
3. If this change archives first, task 4.2 rebuilds the generalist block from
   that baseline, which already contains the relay clause, so the generalist
   delta's surviving content is preserved and nothing else is added.

The collision ledger row records this. No new normative conflict remains, so no
amendment needs a new approval route and no implementation hold is added.

## 5. Provenance

- `release-manifest.json` is the adopted payload record. Its
  `normative_file_sha256` values pin the adopted bytes and are kept unchanged
  as history. Six files had already drifted from those hashes before this
  reconciliation (the five spec deltas and `design.md`, through `#4911`
  path migration and `#5017` Retry/healing retirement). This change edits
  `proposal.md`, `design.md`, `tasks.md`, `coverage.md`, `allocation-plan.md`
  and five spec deltas as well. The manifest therefore attests the adopted
  payload, and this file plus the git history attest every later conforming
  change.
- Archived evidence stays readable with
  `git show d5c889e6278cabb4f4aeb5e0d024c6ce1f88e2e5:docs/redesigns/2026-10-14-qa-remediation-pursuit-data.json`.
- The month packet lives at `docs/plans/2026-10-14-qa-month-packet.md`.

## 6. Retained holds and external predecessors

Serial order stays A, B, C, D, E, F, then G. No owner release erases these:

| Bead | Waits on (open at reconciliation) |
|---|---|
| A `bu-6a9xxw.1` | `bu-mc01y5.7` launch-seam isolation; `bu-2jtfw.5` generic termination evidence. The surviving Spawner's permission check for the `qa` launcher is an open owner question in the generalist `deletion-inventory.md` section 5; A does not decide it. |
| B `bu-6a9xxw.2` | A |
| C `bu-6a9xxw.3` | B; `bu-ratgnb` canonical inbox acceptance; `bu-u5euwb.17` durable-dashboard re-scope |
| D `bu-6a9xxw.4` | C; `bu-dss7he.13` log backend; `bu-u5euwb.17` |
| E `bu-6a9xxw.5` | D; `bu-mc01y5.2` and `bu-mc01y5.4` restricted publisher; `bu-u5euwb.17` |
| F `bu-6a9xxw.6` | E; `bu-u5euwb.17` |
| G `bu-6a9xxw.7` | A to F |

Consumed and closed: `bu-mc01y5.3` credential strip, `bu-dss7he.1` Retry
retirement, `bu-w24vr3` patrol-evidence authority, `bu-s11n0s.6` served-model
receipts, `bu-gjmx8w` owner release.
