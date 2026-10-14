# QA remediation allocation plan

Proposed packet-complete graph; all implementation remains owner-held. One serial lane A → B → C → D → E → F → G. External prerequisites remain real blockers. Closing the owner gate does not waive them. Six implementation outcomes plus one combined reconciliation/report keep this epic within the seven-child ceiling.

## A. Restore QA-owned launch capability and durable session lineage

Dependencies: bu-2jtfw.5, bu-mc01y5.3

Outcome and non-goals: Initial and follow-up investigations run with QA prompt, session store, model resolution and accounting regardless of daemon startup order. Own actual launch/phase receipts; no general spawner rewrite or publication implementation.

Governing intent: about/heart-and-soul/vision.md Rules 2–7; development.md; RFC0015; openspec/changes/restore-qa-remediation-loop/design.md. Baseline b747a55a530364d987fb37457c343193e9362fe7. Keep credentials out of investigators, sanctioned views only, human merge authority. One accountable primary implementation owner per bead; coordinator assigns that owner after gate and dependencies clear. Default one warm serial lane, no competing shared-surface workers. Exact requirements: REQ-qa-investigation-dispatch-006, REQ-qa-investigation-dispatch-011.

Surface map: src/butlers/daemon.py, core/spawn_hooks.py, narrow core/spawner.py launch seam, core/qa/dispatch.py; QA capability registration/generation, public attempt/phase to QA session linkage; module is a caller, not core owner.

Behavior matrix: Happy: QA-bound resolution and launch. Failure: absent/stale owner refuses, prelaunch rejection not execution failure. Concurrency: two startup orders and old shutdown after successor. Replay: uncertain reservation reconciles durable launch identity exactly once. Rollback: hold QA, retain receipts, revert matching owner/caller versions; no fallback to another butler.

Documentation impact: docs/runtime/spawner.md, docs/runtime/session-lifecycle.md, QA operational contract; no doctrine expansion. Apply the affected exact clauses in contract-amendments.md before/alongside behavior; never defer higher-pillar reconciliation to closeout.

## Acceptance Criteria

Extend tests/daemon/test_qa_jobs.py and tests/core/qa/test_dispatch.py at real multi-daemon registration/launch seam: both startup orders, replacement shutdown, initial/follow-up prompt/store/model ownership and empty MCP. Extend tests/integration/test_qa_pipeline.py for durable attempt-session link before cancellation and timestamps enclosing actual execution. Own QA linkage; reuse generic termination evidence from predecessor. Expected net Tests: +4 ~4 -0, refine after inventory.

For each invariant use exactly one gate species: behavior-executing tests below, not duplicate source-string assertions. Start exact affected node with -n 0, widen owning scope; make test-plan BASE=origin/main before escalation. Include real-Postgres/API/runtime lane as appropriate, roster scope if changed, collect tests/ roster/ for fixture/import/topology changes. Push targeted-tested head; hosted merge-group required check/guards/frontend is terminal broad evidence. No broad local suite by default. Reconcile every cited requirement including preserved scenarios; update implementation docs and archive/sync only after whole-epic adoption and delivery. Record actual Tests: +a ~b -c and condense/budget justification in PR; estimates here are not test-count targets.

## B. Bind QA evidence to its invocation and expose truthful outcomes

Dependencies: A, bu-dss7he.1

Outcome and non-goals: No inherited diagnosis can become current evidence. Versioned notes/proposal receipts distinguish inconclusive, environment-blocked, operational, already-fixed, design-required and patch-ready results across existing dossier/API. No runtime price system or new retry route.

Governing intent: about/heart-and-soul/vision.md Rules 2–7; development.md; RFC0015; openspec/changes/restore-qa-remediation-loop/design.md. Baseline b747a55a530364d987fb37457c343193e9362fe7. Keep credentials out of investigators, sanctioned views only, human merge authority. One accountable primary implementation owner per bead; coordinator assigns that owner after gate and dependencies clear. Default one warm serial lane, no competing shared-surface workers. Exact requirements: REQ-qa-investigation-dispatch-007, REQ-qa-investigation-dispatch-008, REQ-qa-investigation-dispatch-009, REQ-qa-investigation-dispatch-010, REQ-qa-dashboard-001, REQ-qa-dashboard-004, REQ-qa-dashboard-005.

Surface map: core/qa/notes.py, outcome/status constraints, canonical session-completion breaker events and every current status/breaker writer/reader/query, prompts.py, dispatch.py, diff.py, journal.py; QA producing skills and tracked .qa artifact; api/routers/qa.py, co-located QA models/helpers in api/routers/qa.py, frontend API types/hooks and components/qa/CaseDossier*, StateTrack, PRPanel, list/detail consumers; private evidence JSON and derived provenance annotations.

Behavior matrix: Happy: fresh dispatcher-bound schema-v2 notes, complete base..head preview with explicit truncation. Failure: absent/malformed/oversized/mismatched/symlink artifact rejected; no-op => inconclusive not unfixable; unknown publication not inferred absent. Concurrency: descriptor validation prevents substitution. Replay: annotation idempotent, original history retained. Rollback: hold reasoning, keep new data readable, never restore stale tracked artifact. Preserve current dismissal and session doors; consume predecessor Retry deletion.

Documentation impact: docs/operations/qa-remediation-loop.md (new canonical QA repair-loop runbook); source-linked evidence/privacy notes; existing frontend data access docs where projection contract changes. Apply the affected exact clauses in contract-amendments.md before/alongside behavior; never defer higher-pillar reconciliation to closeout.

## Acceptance Criteria

Extend tests/core/qa/test_dispatch_investigation_notes.py and test_investigation_notes.py at output descriptor-to-persist seam for inherited/missing/mismatched notes, schema limits and race-safe consumption. Extend test_diff.py for two-commit proposal/base and truncated preview. Extend test_dispatch.py/test_journal.py for every typed terminal/closed_at/breaker result including successful open PR, failed follow-up and late closure across reset, raw-evidence retention eligibility and merge-only journal. Extend tests/api/test_api_qa_cases.py and frontend/src/components/qa/__tests__/dossier.test.tsx for contaminated fallback, no-op copy, ambiguity and action/evidence doors. Real-Postgres annotation read-back via integration pipeline. Expected net Tests: +6 ~8 -0.

For each invariant use exactly one gate species: behavior-executing tests below, not duplicate source-string assertions. Start exact affected node with -n 0, widen owning scope; make test-plan BASE=origin/main before escalation. Include real-Postgres/API/runtime lane as appropriate, roster scope if changed, collect tests/ roster/ for fixture/import/topology changes. Push targeted-tested head; hosted merge-group required check/guards/frontend is terminal broad evidence. No broad local suite by default. Reconcile every cited requirement including preserved scenarios; update implementation docs and archive/sync only after whole-epic adoption and delivery. Record actual Tests: +a ~b -c and condense/budget justification in PR; estimates here are not test-count targets.

## C. Persist incident admission and deferred QA work with finite budgets

Dependencies: B, bu-w24vr3, INBOX_SOURCE_ACCEPTANCE

Outcome and non-goals: One-shot findings survive saturation/restart and reasoning is admitted once per material incident revision under finite lifetime budgets. Keep unrelated source observations distinct without trustworthy correlation. No second dashboard inbox, general workflow engine or budget-reset endpoint.

Governing intent: about/heart-and-soul/vision.md Rules 2–7; development.md; RFC0015; openspec/changes/restore-qa-remediation-loop/design.md. Baseline b747a55a530364d987fb37457c343193e9362fe7. Keep credentials out of investigators, sanctioned views only, human merge authority. One accountable primary implementation owner per bead; coordinator assigns that owner after gate and dependencies clear. Default one warm serial lane, no competing shared-surface workers. Exact requirements: REQ-qa-investigation-dispatch-004, REQ-qa-investigation-dispatch-013, REQ-qa-triage-001, REQ-qa-triage-002, REQ-qa-triage-003, REQ-qa-triage-004, REQ-qa-triage-005, REQ-qa-dashboard-006, REQ-qa-investigation-dispatch-006.

Surface map: core/qa/triage.py, findings.py, dispatch.py; core/healing/tracking.py surviving shared seam; modules/qa module drain; migration-owned QA incident/link/pending-work/lease/reservation tables and role/RLS policy, canonical dashboard report inbox handoff.

Behavior matrix: Happy: trusted lineage joins observations and links one attempt. Failure: persistence unavailable refuses; capacity preserves pending work beyond lookback. Concurrency: atomic claim/fence, reserve before launch, stale claimant cannot ack. Replay: crash before/after launch reconciles receipt, acknowledgement and linked disposition same transaction. Retry: no cooldown/count-only reopen, material changes still preserve owner/circuit/recursion/publication gates; default three launches/60 active minutes shared with followups (REQ-dispatch-006 budget scenario only; A owns launch/phase lineage). Rollback: quiesce reasoning, preserve pending rows and unknown launch/publication reservations.

Documentation impact: docs/data_and_storage/schema-topology.md and migration-patterns where needed; QA admission/operations documentation; active durable-dashboard inbox remains authoritative. Apply the affected exact clauses in contract-amendments.md before/alongside behavior; never defer higher-pillar reconciliation to closeout.

## Acceptance Criteria

Extend tests/core/qa/test_triage.py, tests/api/test_actor_attribution_sweep.py dismissal behavior, and tests/integration/test_qa_pipeline.py with real PostgreSQL consumers: full cap one-shot beyond lookback; lease expiry vs stale ack; two consumers; crash before/after session receipt; exactly-once disposition. Extend tests/core/qa/test_provenance_and_dedup_linkage.py for trusted cross-source linkage versus unrelated equal text and incident generation. Migration tests must replay bootstrap, SET ROLE outsider denial, core-only and separate-connection read-back. Extend dispatch behavior tests for material-change/cooldown/budget accounting and active-condition order; execute budget watchdog rather than snapshot config. Expected net Tests: +6 ~6 -0.

For each invariant use exactly one gate species: behavior-executing tests below, not duplicate source-string assertions. Start exact affected node with -n 0, widen owning scope; make test-plan BASE=origin/main before escalation. Include real-Postgres/API/runtime lane as appropriate, roster scope if changed, collect tests/ roster/ for fixture/import/topology changes. Push targeted-tested head; hosted merge-group required check/guards/frontend is terminal broad evidence. No broad local suite by default. Reconcile every cited requirement including preserved scenarios; update implementation docs and archive/sync only after whole-epic adoption and delivery. Record actual Tests: +a ~b -c and condense/budget justification in PR; estimates here are not test-count targets.

## D. Resume bounded discovery with honest source coverage

Dependencies: C, bu-dss7he.13

Outcome and non-goals: Retained unread events survive outages and progress is fair, bounded and durably tied to persisted findings. Partial/unavailable source coverage cannot impersonate complete patrols or recovery. Consume surviving log-source replacement, do not build a competing file-scanner system.

Governing intent: about/heart-and-soul/vision.md Rules 2–7; development.md; RFC0015; openspec/changes/restore-qa-remediation-loop/design.md. Baseline b747a55a530364d987fb37457c343193e9362fe7. Keep credentials out of investigators, sanctioned views only, human merge authority. One accountable primary implementation owner per bead; coordinator assigns that owner after gate and dependencies clear. Default one warm serial lane, no competing shared-surface workers. Exact requirements: REQ-staffer-qa-011, REQ-staffer-qa-012, REQ-qa-log-scanner-001, REQ-qa-log-scanner-002.

Surface map: modules/qa source loop; core/qa/sources and surviving log_scanner/view source owned by predecessor, session_records.py/tool_call_failures.py/butler_reports.py; QA source-generation cursor/coverage records and patrol/API projections; sanctioned views only.

Behavior matrix: Happy: bounded catch-up plus normal overlap and stable source occurrence key. Failure: cap/rotation/retention/source-error returns partial/gap, no false cursor advance. Concurrency: one fenced checkpoint writer, commit findings before progress; hot source cannot starve others. Replay: overlap/late arrivals idempotent; new generation validated. Rollback: hold scanner progress consumption, keep receipts; do not reset to now or discard unread work. Volatile relay stays explicitly volatile; durable dashboard inbox reused.

Documentation impact: QA discovery/runbook and log-source topology; transfer scanner-specific requirements to successor capability if predecessor retired it, with owner-approved spec reconciliation before coding. Apply the affected exact clauses in contract-amendments.md before/alongside behavior; never defer higher-pillar reconciliation to closeout.

## Acceptance Criteria

Extend tests/core/qa/test_log_scanner.py or its predecessor-produced surviving source test, test_session_records.py, test_tool_call_failures.py, tests/modules/test_module_qa.py: cap before read/materialization, outage beyond lookback, retained rotation versus explicit loss gap, late arrival, fair source continuation, failed persistence cannot advance cursor. Real-Postgres restart/lease test in tests/integration/test_qa_pipeline.py; API coverage in tests/api/test_api_qa.py. Preserve independent liveness evidence rules under restore-butler-control-plane-liveness. Expected net Tests: +4 ~7 -0; do not resurrect deleted test/source topology.

For each invariant use exactly one gate species: behavior-executing tests below, not duplicate source-string assertions. Start exact affected node with -n 0, widen owning scope; make test-plan BASE=origin/main before escalation. Include real-Postgres/API/runtime lane as appropriate, roster scope if changed, collect tests/ roster/ for fixture/import/topology changes. Push targeted-tested head; hosted merge-group required check/guards/frontend is terminal broad evidence. No broad local suite by default. Reconcile every cited requirement including preserved scenarios; update implementation docs and archive/sync only after whole-epic adoption and delivery. Record actual Tests: +a ~b -c and condense/budget justification in PR; estimates here are not test-count targets.

## E. Integrate governed proposals and exact feedback-driven follow-ups

Dependencies: D, bu-mc01y5.4, bu-mc01y5.2

Outcome and non-goals: Current-spec fixes produce bounded verified proposals; design-required changes stop for review. New actionable exact-head feedback gets one finite follow-up. Publication outages reuse retained candidate evidence rather than repeating coding. Existing publisher is sole remote-mutation owner.

Governing intent: about/heart-and-soul/vision.md Rules 2–7; development.md; RFC0015; openspec/changes/restore-qa-remediation-loop/design.md. Baseline b747a55a530364d987fb37457c343193e9362fe7. Keep credentials out of investigators, sanctioned views only, human merge authority. One accountable primary implementation owner per bead; coordinator assigns that owner after gate and dependencies clear. Default one warm serial lane, no competing shared-surface workers. Exact requirements: REQ-qa-investigation-dispatch-005, REQ-qa-investigation-dispatch-012.

Surface map: core/qa/prompts.py, dispatch.py follow-up path, github_pr.py complete provider projection, tracking feedback digest/claim; QA-specific prompt skills; restricted publisher artifact/receipt adapter only, no credentials in investigator or alternate CLI push path.

Behavior matrix: Happy: complete paginated inline feedback with exact PR/head/digest. Failure: missing pages/unsupported reads => unknown; auth outage action condition from trusted receipt. Concurrency: timer/patrol claims one session and revalidates head/policy/budget. Replay: consumed digest no new launch, changed head re-evaluates, uncertain remote outcome stays held. Rollback: stop claims, retain proposal/digest/lineage; do not reset shared incident budget. No approval, auto-merge, deploy or new permissions.

Documentation impact: QA prompt governance, feedback state/runbook, publisher integration docs; preserve RFC0015 rather than restating its operation protocols. Apply the affected exact clauses in contract-amendments.md before/alongside behavior; never defer higher-pillar reconciliation to closeout.

## Acceptance Criteria

Extend tests/core/qa/test_prompts.py for behavior-bound prompt inputs (no duplicate keyword-only spec gate), test_github_pr.py provider contract fixtures for inline-only feedback and pagination/errors; test_dispatch.py and real-Postgres integration for duplicate pollers, exact head changes, shared runtime cap and retained immutable artifact resume. Reuse publisher verifier receipts for isolation; add only integration proof that QA consumes them and never clears ambiguity. Expected net Tests: +4 ~5 -0.

For each invariant use exactly one gate species: behavior-executing tests below, not duplicate source-string assertions. Start exact affected node with -n 0, widen owning scope; make test-plan BASE=origin/main before escalation. Include real-Postgres/API/runtime lane as appropriate, roster scope if changed, collect tests/ roster/ for fixture/import/topology changes. Push targeted-tested head; hosted merge-group required check/guards/frontend is terminal broad evidence. No broad local suite by default. Reconcile every cited requirement including preserved scenarios; update implementation docs and archive/sync only after whole-epic adoption and delivery. Record actual Tests: +a ~b -c and condense/budget justification in PR; estimates here are not test-count targets.

## F. Verify deployed recovery and report an honest QA outcome and usage funnel

Dependencies: E, bu-s11n0s.6

Outcome and non-goals: Operator can distinguish detected, investigated, proposed, published, merged, deployed and verified-recovered work, see regression and evidence-qualified usage. Remove merge-as-repair metrics. No new dashboard page, deployment authority, provider pricing/attestation or cadence tuning.

Governing intent: about/heart-and-soul/vision.md Rules 2–7; development.md; RFC0015; openspec/changes/restore-qa-remediation-loop/design.md. Baseline b747a55a530364d987fb37457c343193e9362fe7. Keep credentials out of investigators, sanctioned views only, human merge authority. One accountable primary implementation owner per bead; coordinator assigns that owner after gate and dependencies clear. Default one warm serial lane, no competing shared-surface workers. Exact requirements: REQ-qa-dashboard-002, REQ-qa-dashboard-003.

Surface map: QA recovery receipt/incident state and deployment projection, api/routers/qa.py and models, frontend API client/hooks, QaKpiStrip/QaVerdictOpener/StateTrack/case list and detail. Trusted deployment receipt + relevant complete source opportunity or safe reproducer; QA sessions/model evidence consumes external attestation owner.

Behavior matrix: Happy: matching deployment plus safe passing reproducer or complete recurrence opportunity proves bounded recovery. Failure: empty idle patrol/partial source/unknown deployment/cost/linkage stays unverified, never zero cost. Concurrency: receipt binds revision/generation; renewed failure creates regression without rewriting past. Replay: receipt and counters idempotent; cohort units and denominators explicit. Rollback: hold recovery promotion, preserve receipts and unknowns; API and consumer removal coordinated.

Documentation impact: QA metric definitions (cohorts/time/unit/coverage), operational recovery policy and frontend evidence doors; document limitations for rare operations. Apply the affected exact clauses in contract-amendments.md before/alongside behavior; never defer higher-pillar reconciliation to closeout.

## Acceptance Criteria

Extend tests/api/test_api_qa.py/test_api_qa_cases.py and real-Postgres pipeline for merged-only, wrong deployment, partial/idle observations, real relevant clean operation, recurrence and duplicate receipts; distinguish initial/followup usage with missing cost. Frontend QaOverviewPage.test.tsx and components/qa dossier/atoms tests verify correct unknown/zero/denominator rendering, keyboard evidence navigation and failures; npm run knip before build/targeted Vitest, relevant axe flow. Expected net Tests: +4 ~7 -0.

For each invariant use exactly one gate species: behavior-executing tests below, not duplicate source-string assertions. Start exact affected node with -n 0, widen owning scope; make test-plan BASE=origin/main before escalation. Include real-Postgres/API/runtime lane as appropriate, roster scope if changed, collect tests/ roster/ for fixture/import/topology changes. Push targeted-tested head; hosted merge-group required check/guards/frontend is terminal broad evidence. No broad local suite by default. Reconcile every cited requirement including preserved scenarios; update implementation docs and archive/sync only after whole-epic adoption and delivery. Record actual Tests: +a ~b -c and condense/budget justification in PR; estimates here are not test-count targets.

## G. Reconcile QA remediation delivery and generate epic report (gen-1)

Dependencies: A, B, C, D, E, F

Outcome and non-goals: independently falsify cross-child delivery and publish one scoped epic report; do not implement unrelated broad QA/readiness work owned by bu-fvw4ap.6. Governing intent: about/heart-and-soul/vision.md Rules 2–7; development.md; RFC0015; openspec/changes/restore-qa-remediation-loop/design.md. Baseline b747a55a530364d987fb37457c343193e9362fe7. Keep credentials out of investigators, sanctioned views only, human merge authority. One accountable primary implementation owner per bead; coordinator assigns that owner after gate and dependencies clear. Default one warm serial lane, no competing shared-surface workers.

Surface map: every requirement in openspec/changes/restore-qa-remediation-loop/specs, full code paths including pre-existing readers/writers, children and their exact landed PR/CI evidence.

Behavior matrix: trace synthetic safe signal to one admission, QA session, bound proposal, trusted publication receipt, deployment and verified recovery; failures, restart/replay, coverage gaps and ambiguity must remain explicit. Universal claims require enumerating all paths; compare consumers, inspect guard scan roots/regex. Reuse executing receipts, do not create duplicate tests. If gaps remain, report dedupe-ready candidates and next reconciliation generation (max gen-3); no lifecycle mutation by dispatched worker. Rollback validation checks retained pending/evidence/remote holds, no lost state.

Documentation impact: use /home/orca/.dotfiles/ai-bootstrap/skills/personal/th-projects/subskills/project-direction/scripts/epic-report-scaffold.sh <epic-id>, fill docs/reports/<epic-id>-<slug>.md, record diagram decision, sync/archive converged specs only when all mandatory outcomes are delivered, update topology/operations and mandate coverage. Milestone-close callback re-enters project-direction milestone synthesis for uncovered mandates.

## Acceptance Criteria

Every mandatory requirement maps to actual code file:line, implementing child and one executing gate species. Audit negative/universal paths and instrument scope rather than accepting a green count. All six children closed on landed PR evidence, required hosted checks green at their exact merge tree; reuse receipts. Synthetic end-to-end journey proves initial and feedback loop plus safe recovery through real storage and isolation verifier, with operator-authorized canary only under existing operational owners. Report unavailable live evidence honestly; it blocks a claim of operational completion. Report exists and is linked to epic; no mandatory Partial/Deferred/Contradicted without approved spec amendment. Classify discoveries and route to coordinator; preserve broader reconciliation owner. Tests: +0 ~0 -0 expected (reuse evidence; missing invariant returns to implementing owner). Milestone-close callback and VISION mandate matrix recorded; no epic closure while mandatory gaps remain.
