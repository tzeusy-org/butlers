Reviewed source: b747a55a530364d987fb37457c343193e9362fe7; observed deployment image: c966a1cdde9e.

# QA activity review: 14 September to 14 October 2026

QA is discovering errors, but its autonomous remediation loop delivered no PRs during the reviewed month. There are no outstanding identifiable QA-origin PRs. The largest problems are a wrong runtime owner, stale investigation evidence, misleading terminal classifications, and repeated publication failures. The owner reported correcting the GitHub credential during this audit; the runtime and evidence defects remain.

This packet supports the decision about repairing and improving QA. It is an audit receipt and advisory handoff, not behavioral authority, a new implementation plan, or a claim that any repair is deployed. The owner additionally specified `tzeusy-orca` as the intended QA publication identity and `tzeusy-org/butlers` as its repository. That target is incorporated below.

## Scope, baseline and method

The fixed activity window is **2026-09-14 08:45 UTC inclusive to 2026-10-14 08:45 UTC exclusive**. Live configuration and GitHub open states were checked after that cutoff on 14 October. The review uses the dev namespace only, aggregate database queries, selected private case narratives and saved diff metadata, GitHub PR enumeration and historical diff samples, source inspection, and an independent pipeline review. It did not change live state, publish a canary, install a credential, or run broad tests.

The five pillars are present and conservatively rated structured; the shape scanner's separate repository assessment is **SHAPED**, not Mature. Relevant authority is doctrine and RFCs, then capability specs, engineering standards, topology, and finally observed implementation. The applicable documents are:

- [Development doctrine](../../about/heart-and-soul/development.md): reproducing tests, spec before significant work, RFC before an architectural exception, manifesto scope, durable work tracking.
- [QA manifesto](../../roster/qa/MANIFESTO.md) and [RFC0015](../../about/legends-and-lore/rfcs/0015-qa-staffer-discovery-investigation-pipeline.md): deterministic discovery, scoped investigations, proposed fixes, honest evidence, human merge authority.
- [QA dispatch](../../openspec/specs/qa-investigation-dispatch/spec.md), [triage](../../openspec/specs/qa-triage/spec.md), [staffer](../../openspec/specs/staffer-qa/spec.md), and [scanner](../../openspec/specs/qa-log-scanner/spec.md) requirements.
- [Engineering bar](../../about/craft-and-care/engineering-bar.md), [review obligations](../../about/craft-and-care/review-and-documentation.md), and [test ladder](../../AGENTS.md).
- [Adopted publication change](../../openspec/changes/confine-qa-publication-authority/tasks.md): credential-free investigators and a restricted trusted publisher are an adopted but unfinished target. Existing source and live-operation tasks remain distinct.

The recent owner-adopted generalist lead/focused-child direction in PR #4509 is relevant future direction; it is not evidence of an installed orchestration runtime or permission to bypass current boundaries.

The inspected QA, healing tracking/worktree and tracked-notes paths have no diff between the named image commit and reviewed checkout. Spawn-hook, CLI and relevant spawner ownership behavior also match. This strengthens the source/runtime diagnosis, but does not prove all historical deployments or mounted files were identical.

## Observed monthly funnel

| Stage | Observation |
|---|---:|
| Recorded patrols | 3,313 |
| Patrols classified suppressed | 3,234 |
| Patrols dispatching investigations | 79 |
| Finding observations / distinct fingerprints | 12,107 / 101 (distinct total author-observed, unretained) |
| Infrastructure observations suppressed by an existing condition | 11,823 across 42 fingerprints |
| Investigation attempts / distinct fingerprints | 100 / 47 (distinct total author-observed, unretained) |
| Actually linked investigation sessions | 95 |
| Explicit non-code-bug sentinel outcomes | 42 |
| No-commit outcomes classified unfixable | 38 |
| Publication failures (`git push failed` or `git_auth_failed`) | 15 |
| Restart-recovered attempts without a linked spawned session | 5 |
| PRs created by those attempts | 0 |

[Observed, retained] The aggregate join finds 95 linked sessions in `travel.sessions`, and the retained QA query finds no `qa.sessions` in the window. The author also checked other butler session tables and reported no matching sessions, but that cross-schema exclusion query/result was not retained and is currently unverified. The 95 Travel sessions record about **9.05 hours of aggregate session duration**, 7,427,044 input tokens, 767,531 output tokens and 157,948,032 cached-input tokens. These are stored counters, not normalized billing units. All 95 `cost` fields are null, so a dollar cost cannot be established from these records. The 15 publication-failed sessions account for about **5.13 hours** of execution. [Author-observed, unretained] The reported 10.47-hour sum of 100 attempt lifetimes, including preparation and cleanup, has no retained query/result and is currently unverified; it is not equivalent to inference duration.

[Observed] Models were 61 `gpt-6-sol`, nine `gpt-5.6-sol`, and 25 `ollama/qwen3.5:9b`. [Author-observed, unretained] The reported no-op split of all 25 local-model runs and 13 `gpt-6-sol` runs has no retained model-by-outcome query/result and is currently unverified. The retained model/status grouping cannot establish that split. Neither population is a controlled comparison proving one model caused failure.

[Observed] Suppressed patrols average 1.08 seconds; dispatching patrols average 3.11 seconds. Recorded patrol durations sum to about 62.5 minutes over the month, excluding asynchronously running agents. The five discovery sources are deterministic. Thus 11,823 repeated infrastructure observations are not 11,823 LLM calls.

[Observed] There are 74 intervals over 30 minutes between recorded patrols, with a largest gap of 18 hours 26 minutes (the reported 20-21 September date is author-observed and unretained). This is not proof QA caused every gap; maintenance, downtime and historical rollout details remain unknown. [Author-observed, unretained] The reported 1,007 patrols during 5-11 October, versus 1,008 nominal ten-minute slots, has no retained weekly query/result; the claimed cadence improvement is therefore unverified. The month contains 2,092 scheduled rows with complete-source provenance and 1,221 older rows without that provenance; do not retroactively certify the latter as complete discovery.

The retained SQL groups distinct fingerprints by source/reason or attempt outcome; those groups may overlap and cannot establish the global totals of 101 and 47 above. Its publication category combines `git push failed` with `git_auth_failed`; the count of 15 is retained, but the claim that all 15 were authorization failures was author-observed without a retained sanitized category check and is currently unverified. These evidence limits qualify the precision, not the retained 3,313/12,107/100/95/zero-PR funnel. No missing receipt was reconstructed and no fresh live query was performed for this correction.

## Outstanding PRs and solution quality

[Observed] No identifiable QA-origin PR is open, and none was created or updated in the review window. Enumeration covered all 189 `self-healing` PRs, exactly the same 189 `automated` PRs, and 191 `qa/` branch PRs. The additional two branch matches are manual June follow-ups. The 189 labeled PRs comprise 164 merged and 25 closed unmerged. All 52 currently open repository PRs and 359 PRs updated since 14 September were independently checked for the same markers. No query reached its 1,000-item cap. Unmarked output cannot be attributed conclusively, but the live attempt records independently show zero PRs.

The latest identifiable autonomous PR is [#3957](https://github.com/tzeusy-org/butlers/pull/3957), created and merged 1 September. Its two-file fix serializes concurrent partition creation and includes a real PostgreSQL eight-client regression. That is a sensible, narrow historical repair. [#3885](https://github.com/tzeusy-org/butlers/pull/3885) improved over six commits from an availability preflight to actual grants repair, migration/bootstrap coverage, an RFC boundary update, and preservation of checkpoints during partial calendar fan-out. Its final PR description remained stale. Shared account attribution and absent GitHub review threads do not establish who performed those follow-ups or whether independent review occurred elsewhere.

[Observed] Monthly changes **to QA** are not autonomous output **from QA**: [#4006](https://github.com/tzeusy-org/butlers/pull/4006) repaired repository migration plumbing; [#4253](https://github.com/tzeusy-org/butlers/pull/4253) and [#4256](https://github.com/tzeusy-org/butlers/pull/4256) strengthened scheduling and independent patrol assurance; [#4383](https://github.com/tzeusy-org/butlers/pull/4383) repaired error relay wiring. These are useful engineering improvements but do not establish this month's remediation success.

Related outstanding infrastructure includes [#4474](https://github.com/tzeusy-org/butlers/pull/4474), an open draft for owning-schema structured-log storage, with failing aggregate checks at observation time. It is not QA-origin. Its possible impact on live discovery needs separate proof.

The month did produce plausible candidate patches: saved diff metadata includes model-failover classification, relationship interaction validation, connector heartbeat validation and passive interaction sync. Some include tests and spec edits. Publication failure means these are proposals, not shipped resolutions; this review did not validate every unpublished diff or execute their tests.

## Confirmed findings and remedies

### F1. QA runs under the wrong daemon identity (high)

**[Observed, Confirmed]** Every daemon registers into one process-global spawner slot. `butlers up` runs the daemons in one process; later startup overwrites earlier registration. QA ignores its supplied spawner and retrieves the global slot when dispatching. The result owns the model policy, system prompt, session pool and telemetry identity. All 95 live month sessions being stored under Travel corroborates that path.

Supporting source: `src/butlers/core/spawn_hooks.py:42,61`; `src/butlers/daemon.py:2043`; `src/butlers/cli.py:671`; `src/butlers/modules/qa/__init__.py:1348,1826,1939`; `src/butlers/core/spawner.py:1680,2352,2433`.

Contradictory evidence checked: QA's `trigger_source` still forces an empty MCP map (`spawner.py:2543`). This finding does **not** establish Travel tool access. The global registration comment assumes one startup, contradicted by the supported all-daemon topology. Preliminary model eligibility is also not a pinned launch resolution.

Remedy: a core-owned dispatch capability scoped to QA, with lifecycle fencing and one model/budget decision carried through launch and follow-up. Test QA plus another daemon in both startup orders, asserting prompt identity, session schema, model policy, accounting and empty MCP access. This restores ownership; it does not require inventing a new autonomous supervisor.

### F2. Old notes are presented as evidence for unrelated investigations (high)

**[Observed, Confirmed]** `.qa/investigation_notes.json` is a tracked artifact introduced by PR #2815 on 2 July. Worktrees inherit it. The dispatcher reads that location at cleanup and assigns its parsed contents to the current attempt without freshness or attempt binding. All 38 no-op attempts across 19 fingerprints and ten callsites carry that artifact's same historical headline, including unrelated scheduler, calendar, memory and runtime incidents.

Supporting source: `src/butlers/core/qa/dispatch.py:534,995,1028,1048`; `src/butlers/core/qa/notes.py:44`; tracked artifact history `e8f52ea87`. Counter-evidence checked: schema validation exists, but contains no trusted attempt/session/fingerprint binding; permissive parsing cannot prove provenance. Retention removes some raw evidence and the dispatcher replaces diff snapshots, so whole stored JSON bodies need not remain byte-identical.

Remedy: remove the tracked runtime artifact; use a fresh dispatcher-owned per-attempt output location and trusted invocation/source/base-head binding. Missing or mismatched evidence must remain unknown. Flag contaminated historical cases while preserving original audit material; do not silently rewrite them into new conclusions. Regression: start from a base containing valid stale notes and complete without writing new notes; none may become the new case diagnosis.

### F3. Incomplete work is classified as unfixable (high)

**[Observed, Confirmed]** `no_op_branch` proves only that no commit exists ahead of main. The dispatcher converts it to `unfixable` and marks its phase completed (`dispatch.py:748,2079`). That accounts for 38% of month attempts. Several explicit unfixable narratives also correctly admit the upstream cause is unknown, which is useful restraint but not a proven non-code-bug diagnosis.

Contradictory evidence checked: the explicit sentinel path is separate and asks for an explanation; this does not justify the no-op path. Runtime `success=true` proves successful invocation completion, not completed investigation or repaired behavior.

Remedy: distinguish inconclusive/no output, blocked environment, already fixed with evidence, diagnosed operational issue, and validated patch. Apply outcome-aware retry rules. An unchanged inconclusive result should not repeatedly consume a fresh investigator after the ordinary 60-minute cooldown or count as productive success.

### F4. Publication failures and owner-reported identity correction (historically high)

**[Observed, retained]** The database repository URL, allowlist and inspected code default identify `tzeusy-org/butlers`. **[Author-reported, unretained]** The clone-origin and resolver check reported the same repository and the shared credential source without a QA-local override. The initial content-limited provider probe reportedly returned the old principal `Tzeusy` and repository HTTP 403. No retained timestamped resolver/provider receipt independently establishes those probe results. The requested target remains `tzeusy-orca`; these historical reports do not describe the current credential. No credential material was printed or persisted.

Fifteen month attempts fall into the retained publication-failure category. Specific authorization attribution for all 15 and the latest occurrence date are author-observed without a retained sanitized category/date check. The reported HTTP 403 would establish unavailable repository access at that observation, but cannot distinguish token scopes, organization authorization or provider-policy causes. The earlier repository migration fix alone does not prove publication capability.

**Owner correction and reported recheck:** the owner reported updating the QA secrets to use `tzeusy-orca`. The author reported a subsequent same-resolution-order check returning `/user` HTTP 200 with `login=tzeusy-orca`, and repository HTTP 200 with `full_name=tzeusy-org/butlers`, `push=true`, `pull=true`, `triage=true`, `admin=false`, and `maintain=false`, plus matching author-field checks and updated metadata timestamps. The post-correction observation time, resolver/source binding, HTTP/public metadata and content-blind author/timestamp receipt were not retained. These are owner/author-reported details, currently unverified by the retained evidence, not confirmed effective QA credentials or grants. The owner correction is acknowledged; the old token is not described as current. Branch publication, PR creation, labeling and confinement were not exercised. No new credential action, publisher activation or live requery was performed for this correction.

Remaining remedy: complete the existing restricted-publisher work and obtain bounded end-to-end publication evidence under the corrected identity. Correct target strings, clone refresh and advertised provider permissions alone do not prove those operations or negative permissions. Existing owners are `bu-nh37dt`, `bu-vc90le`, and publication program `bu-mc01y5`. Live Dolt identifies `bu-mc01y5.3` as the blocked implementation task; `.1` was canonical source adoption, not publisher implementation. The current worker-token injection is an explicitly documented implementation gap; this review does not activate a new publisher or claim least-privilege verification. The owner reported the credential update; the author reported a read-only recheck whose receipt is unavailable.

### F5. Saturated dispatch can lose the promised durable queue (high)

**[Observed source, Confirmed]** The initial full-capacity branch in `dispatch_novel_findings` continues before writing `dispatch_queued` or a dedup reason (`dispatch.py:3868`). A later `cap_skipped` branch contains those writes, but cannot repair the earlier skip. Independent execution of the extracted function with a full-capacity synthetic input produced a concurrency-cap result with zero queue writes. The queue contract is explicit in the dispatch and triage specs.

Contradictory evidence checked: recurring errors can be rediscovered within the lookback, and queue helpers exist. Neither preserves one-shot findings beyond that window. Month records contain 31 non-infrastructure findings without an attempt or dedup explanation and zero queued findings, but this review cannot attribute all 31 to this defect.

Remedy: persist every capacity-deferred finding and acknowledge it only after durable handoff. Test full capacity, restart, one-shot events older than the discovery window, and repeated delivery without duplicate attempts.

### Additional confirmed weaknesses

| Finding | Evidence and practical remedy |
|---|---|
| Phase timing is misleading | `dispatch.py:1730,1750` records phase start after awaiting execution; 95 phase rows sum to only 15.47 seconds. Bind session lineage at launch and use real session timestamps. Underlying session records do retain 9.05 hours, so cost data is not wholly absent. |
| Investigation instructions do not enforce the project workflow | `prompts.py:131,201,375,378` says fix/tests/commit and runs unscoped pytest; it lacks an explicit design-needed result for contract changes. Preserve narrow bug-fix autonomy, require the applicable manifesto/spec/RFC and graduated verification, and route new scope to a proposal. Some observed patches do edit specs, so universal noncompliance is not established. |
| PR feedback retrieval has a broken CLI path | `dispatch.py:2504` requests unsupported `gh pr view --json reviewThreads`; fallback omits thread detail and checks. Local gh rejects that field. Use GraphQL thread retrieval and consume feedback once per exact head/thread state, with a shared work budget. This is latent this month because there were no new PRs. |
| Partial log scans can count as complete discovery | Scanner records truncation (`log_scanner.py:690`) but patrol completion checks returned source names/error only (`modules/qa/__init__.py:1627`). Carry partial coverage and watermarks into health claims. Bounded partial scans are intentionally allowed; they should not crash the patrol. |

## Assessment against th-projects

The design intent is sound: deterministic sensing, durable conditions, scoped ephemeral investigators, anonymized proposed changes and human-controlled merging. Those should remain. Historical fixes show the pipeline can identify real defects and produce meaningful regression coverage.

The month does not establish clean autonomous remediation. Wrong runtime identity violates ownership; inherited notes violate evidence integrity; no-op success hides incomplete work; publication repeatedly failed before the owner-reported credential correction. A prompt telling an agent to fix an error is also insufficient governance for a proposed change to permissions, schema or behavioral contracts. Conversely, requiring a complete project-shaping ceremony for every narrow bug would be wasteful: the development doctrine explicitly exempts scoped bug fixes from a new proposal.

Focused scorecard (not a repository-wide average): goal fulfillment 2/5; runtime correctness 1/5; evidence integrity 1/5; failure handling 2/5; operating efficiency 2/5; investigation governance 2/5. Confidence is high for runtime/evidence/outcome findings and medium for governance and optimization impact. Unreviewed domains are omitted. **Verdict: At risk for autonomous remediation.**

## More efficient operating model

**[Recommendation] Keep periodic reconciliation, but make reasoning depend on a meaningful state change.** The sweep is inexpensive; replacing it with a continuously reasoning super-agent would add cost without addressing the defects.

1. Keep deterministic source collectors and independent liveness assurance. Add bounded source cursors with overlap/late-arrival handling, persistent progress, rotation recovery and catch-up after outages. Retain a periodic reconciliation sweep as a backstop. Cursor changes need explicit contract work; current rolling windows are specified behavior.
2. Group observations into a durable incident/root-cause record using source session/request/condition lineage where available. A log message and its corresponding failed session should enrich one case. Retain their separate evidence rather than deduplicating by text similarity alone.
3. Admit reasoning for new evidence, a changed deployment/configuration, worsening severity, or an explicit retry. Persist operational/action-needed diagnoses and capability outages so an unchanged 403 or empty-response case does not repeatedly restart investigation after cooldown. Avoid permanently suppressing genuine new regressions.
4. Give one correctly scoped QA lead accountability for diagnosis through verified outcome. Use bounded focused children only for isolated investigation or independent review. Carry prompt/spec scope, model eligibility and a total work budget explicitly. This fits the adopted lead/child direction without requiring it to be implemented before repairing current QA.
5. Validate the investigation artifact and outcome before publication. Keep candidate patches durable when publication is unavailable; resume publication from a validated artifact instead of paying for rediscovery. Introduce reviewable proposals when a fix changes scope or authority. Use the existing credential-free investigator/restricted publisher contract.
6. Follow PRs using exact feedback/head changes, finite shared budgets and deterministic CI/thread checks. Humans remain in the merge seat under QA's present contract. A merge is intermediate evidence: verify deployed regression recovery and recurrence before calling a case resolved.

Measure unique incidents, observation-to-admission latency, unhandled queue age, inconclusive/blocked rates, validated patch yield, published/merged/deployed outcomes, recurrence, owner interventions and recorded model cost. Track scan coverage separately from scheduler activity. Evaluate models by verified outcome per total cost and elapsed time, not raw token price. Cheaper future inference does not solve wrong ownership, false evidence or duplicated external effects.

## Planning handoff and evidence limits

Recommended order: restore trustworthy runtime ownership and evidence/outcome classification; repair queue survival and launch lineage; complete the already-owned identity/publication boundary; then improve incident grouping, state-aware admission, coverage cursors and feedback-driven follow-through. Existing publication ownership must be reconciled before allocating overlapping work. The report creates no remediation DAG and does not duplicate existing credential tasks.

Required contract decisions before broader implementation: typed investigation outcomes and their retry rules; attempt-bound evidence schema and historical contamination treatment; explicit QA dispatch ownership/model policy; source coverage/cursor semantics; durable publication resume and feedback admission. Direct corrections restoring existing invariants can follow the focused bug-fix lane with meaningful regression tests. Doctrine changes, new powers and changes to human merge authority are not implied.

Deprioritized: increasing sweep frequency, adding LLM triage to every patrol, buying more concurrent investigators, and optimizing token price before correcting ownership and measuring verified yield.

Evidence gaps: dollar billing is unknown; full correctness of 15 unpublished patches is unverified; actual lost-event incidence and historical causes of cadence gaps are unknown; end-to-end publication under the corrected credential is unverified; merge-to-deployment recovery has no month PR population to evaluate. Broader confinement verification remains in its existing source and operational tasks.

Veracity ledger: “no QA sessions ran” was disproved by cross-schema session joins; “all unfixable cases are legitimate operational diagnoses” was disproved by no-op classification and stale notes; “wrong spawner grants Travel MCP tools” was rejected because QA disables MCP; “11,823 infrastructure findings mean 11,823 agent calls” was rejected by deterministic condition suppression; “the organization URL is still wrong” is contradicted by the retained repository configuration and allowlist plus the source default; the additional clone-origin check is author-reported and unretained. None of these rejected claims is a planning input.

Private local receipts, retained for this review: `/home/orca/.tmp/qa-month-review/evidence.sql`, `db-evidence.txt`, `pr-audit.md`, `pr-audit-summary.json`, `pipeline-audit.md`, `shape.txt`, and `project-scan.txt`. SQL receipts contain aggregate outcomes, not credential values or raw personal incident payloads. GitHub links above provide independently accessible publication evidence. Re-run the fixed-window SQL through the dev-debug helper; do not infer missing telemetry is success.
