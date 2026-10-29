# ci-shard-assurance Specification

## Purpose
TBD - created by archiving change computed-ci-shard-partition. Update Purpose after archive.

## Requirements

### Requirement: Fresh collected membership and one inventory
The CI system SHALL derive both lane populations from one fresh collection of the exact installed pytest corpus and predicates. Advisory weights, diffs, file globs and cached counts MUST NOT establish membership. A dedicated bounded inventory job SHALL start after route and own collection, both budgets and the exact source/run/attempt-bound inventory/assignment artifact. Backend matrix children SHALL depend on route and successful inventory, independently of static guards. Both test budgets SHALL consume this same inventory; collection and envelope failures SHALL be non-passing.

ID: REQ-ci-shard-assurance-001
Source: bu-ly3lv5.6 P1-P3; original complete S1-S5 and ten folded source objects; adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: Both lane populations preserve their actual selectors
- **WHEN** the source-owned collector processes actual items under both roots
- **THEN** the unit and integration opaque identity sets equal independent unchanged-selector controls, including inherited and mixed markers

#### Scenario: New files are included without registration
- **WHEN** five seeded new files and a conftest-only change alter the collected corpus
- **THEN** all eligible identities appear once in the correct lanes without .github registration edits and conftest causes conservative full inventory

#### Scenario: Unknown collection cannot become a budget pass
- **WHEN** collection fails, is empty unexpectedly or has malformed/stale identity/config evidence
- **THEN** the inventory job refuses both inventory and budget success and required check fails

#### Scenario: Historical counts do not replace current population
- **WHEN** dated17501 or budget-update17522/4935 counts are presented
- **THEN** the actual fresh source population governs equality and historical counts remain provenance only

### Requirement: Deterministic whole-file weighted assignment
The partitioner SHALL assign each freshly selected lane file exactly once to five unit or six integration shards using deterministic LPT. A mixed-marker file SHALL remain whole separately within both lanes. Timing data SHALL influence balancing only and malformed/unknown costs SHALL use explicit deterministic defaults. Admitted cost profiles SHALL bind the actual tracing mode, Python and pytest versions, dependency lock and lane selectors for at most fourteen days. Per-file source digests SHALL retire only the changed files' hints; ordinary product-source changes MUST NOT invalidate all advisory costs. Authoritative inventory, assignment and execution identities SHALL remain exact for the current source and attempt.

ID: REQ-ci-shard-assurance-002
Source: bu-ly3lv5.6 P4/P12; original complete S1-S5 and ten folded source objects; adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: Identical inputs give identical assignment
- **WHEN** the same inventory and weights are supplied in different input iteration orders
- **THEN** canonical output is identical, each required shard is nonempty and union/disjointness match the fresh file and node sets

#### Scenario: Unknown durations cannot drop a file
- **WHEN** an eligible file has missing, zero, NaN, infinite, stale or incompatible cost evidence
- **THEN** the finite median-or-one default assigns it normally and advisory degradation never changes membership

#### Scenario: Source changes preserve compatible cost hints
- **WHEN** product source changes or a test file is added or edited
- **THEN** unchanged test files retain compatible measured costs, changed or new files receive finite median-or-one defaults, and fresh collection still determines every selected item
- **AND** incompatible runtime, dependency, selector, age or tracing profiles never supply hints for the current assignment

#### Scenario: Mixed markers retain their two lane homes
- **WHEN** one whole file contains unit and integration items
- **THEN** the file is whole in each lane while each actual eligible item is executed exactly once in its own lane

#### Scenario: Durations refresh is advisory and read only
- **WHEN** the bounded scheduled observer finishes or fails
- **THEN** it emits provenance-bound candidate weights or UNKNOWN without source commits, test-population changes or authority from missing samples
- **AND** an inventory consumer MAY restore a runtime-compatible profile from the latest successful main-branch observer using bounded reads and matching artifact/receipt/source provenance; absence, expiry or incompatibility leaves the reviewed seed or deterministic fallback and never grants current membership authority

### Requirement: Independent complete-identity execution reconciliation
Every required matrix child SHALL produce source/run/attempt-bound collected and executed opaque item receipts independently of assignment. Check-preflight SHALL reject missing, duplicate, extra, stale, incomplete or wrong-identity evidence before its success; sanitized per-file JUnit counts alone MUST NOT prove exact-once.

ID: REQ-ci-shard-assurance-003
Source: bu-ly3lv5.6 P5; original complete S1-S5 and ten folded source objects; adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: Same counts with different parameters fail
- **WHEN** one carrier substitutes a different parameter identity while preserving file counts and masked JUnit names
- **THEN** the reconciler fails by complete opaque identity comparison

#### Scenario: Duplicate execution fails independently of assignment
- **WHEN** one logical item starts twice or one whole file is forced into two shards
- **THEN** reconciliation and required check fail while the genuine exactly-once companion passes

#### Scenario: Missing and partial children cannot hide behind matrix success
- **WHEN** a child is skipped, missing, cancelled, truncated or emits no complete receipt
- **THEN** all eleven expected child identities are checked and preflight cannot report success

#### Scenario: Retry identity and privacy remain exact
- **WHEN** a later attempt or malicious payload supplies raw parameter/provider strings or mismatched nonce/head/config
- **THEN** the artifact is rejected without logging raw values and ordinary same-attempt sanitized evidence passes

### Requirement: Fail-closed matrix routing and event policy
Routing SHALL remain stdlib-only and conservative. The required check SHALL remain verdict-only and always evaluate all current needed results under explicit event/plan rules. Required check SHALL require inventory success for full and main-push modes and an explicit inventory skip for scoped and docs modes; missing, failed, cancelled, unknown or mode-incompatible inventory results MUST fail. Static guards SHALL remain mandatory in every required final verdict even when test bodies start concurrently. Existing required contexts SHALL survive; merge_group MUST retain full inventory, all eleven backend children, guards, frontend and browser evidence.

ID: REQ-ci-shard-assurance-004
Source: bu-ly3lv5.6 P6-P7; original complete S1-S5 and ten folded source objects; adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: A full terminal tree requires eleven backend carriers
- **WHEN** merge_group or a full backend PR executes
- **THEN** fresh inventory, five unit and six integration children, guards and independent preflight must succeed; an unknown/failed/partial result fails the required verdict

#### Scenario: Scoped and docs skips remain explicitly bounded
- **WHEN** a genuine scoped backend PR or classifier-proven docs-only PR is evaluated
- **THEN** only the matching current skip policy is accepted and preflight never counts as a heavy child

#### Scenario: Main push retains fresh inventory without rerunning heavy lanes
- **WHEN** push targets main after protected queue validation
- **THEN** the dedicated inventory job performs complete inventory/budget while existing heavy/preflight post-queue skips remain explicit

#### Scenario: Unknown inputs widen or fail
- **WHEN** classifier/planner/inventory-change evidence is absent or an added needed job lacks skip permission
- **THEN** selection widens to full when it can be validly executed and final malformed or unknown verdict evidence fails

### Requirement: Smoke proof preserves real execution and release provenance
Scoped execution SHALL preserve the dedicated smoke command and release fields. Full execution MAY reuse shard smoke results only with exact selected-item identity and complete actual outcome proof; it MUST NOT fabricate a dedicated command, permit a missing smoke item or introduce real LLM/E2E dependencies.

ID: REQ-ci-shard-assurance-005
Source: bu-ly3lv5.6 P8; original complete S1-S5 and ten folded source objects; adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: Scoped smoke remains independently executed
- **WHEN** a backend PR is admitted scoped
- **THEN** the actual original tests-only smoke command executes and retains SHA, timing, exit, outcome and skipped-class evidence

#### Scenario: Complete full-lane smoke has truthful derived evidence
- **WHEN** every dedicated smoke-selected item was executed successfully by the same full lane run
- **THEN** the record labels derived-full-lanes and records actual shard commands separately from the smoke selector

#### Scenario: A smoke gap triggers the real command
- **WHEN** the dedicated selector has an item absent from successful lane proof
- **THEN** the existing dedicated smoke command runs or preflight refuses success rather than manufacturing zero or coverage

#### Scenario: Smoke failures and dependencies remain bounded
- **WHEN** a real smoke failure or E2E/LLM dependency is introduced
- **THEN** preflight and check are non-passing and healthy no-LLM/no-E2E companions still run

### Requirement: Complete eleven-input independent coverage reporting
The non-required reporter SHALL validate eleven distinct same-run CoverageData inputs independently before combine/report/badge. Full covered execution SHALL run only in the nightly workflow, on its schedule and on manual dispatch (owner decision bu-w72da2 = A). That run SHALL freshly collect both complete lane populations, execute all five unit and six integration covered shards, independently reconcile all eleven execution receipts, preserve real smoke fallback and release evidence, and upload the combined report as an artifact. The reporter SHALL bind the actual admitted workflow event to inventory and every coverage input; a scheduled or manual run MUST NOT impersonate merge_group. Every prerequisite failure, skip, cancellation, missing input or malformed identity SHALL remain visibly non-passing before publication. Only successful scheduled/manual runs of main MAY update the badge; a manual run on another branch validates a coverage-pipeline change without updating it. Required check MUST NOT install or await reporting, and pull-request and merge-group executions SHALL retain identical selection without coverage instrumentation. Protected merge-group and ordinary PR shard execution SHALL use CI_COVERAGE=0 while preserving every existing selected test, worker, marker, timeout and complete-identity receipt. The nightly coverage jobs SHALL use CI_COVERAGE=1; standalone default covered behavior and strict CI_COVERAGE 0/1 admission SHALL remain.

ID: REQ-ci-shard-assurance-006
Source: bu-ly3lv5.6 P9; original complete S1-S5 and ten folded source objects; adopted testing-and-verification; bu-w72da2 owner decision A (2026-10-21)
Scope: v1-mandatory

#### Scenario: Eleven real valid inputs pass the actual reader
- **WHEN** five unit and six integration inputs have exact current source population/tracing/head/run/attempt/digest and the actual admitted scheduled or manual nightly event
- **THEN** all pass the same installed CoverageData path before combination and report/badge publication

#### Scenario: One bad input refuses publication
- **WHEN** ten valid inputs accompany one corrupt, empty, missing, extra, stale or wrong-identity input
- **THEN** reporting fails visibly before report/badge while test verdicts remain truthful, including when any prerequisite failed or skipped and when event metadata was borrowed from another trigger

#### Scenario: PR opt-out and standalone compatibility survive
- **WHEN** CI_COVERAGE is supplied0/1, malformed or absent in standalone execution
- **THEN** ordinary PR0 and protected merge-group0 have no cov arguments/uploads, the nightly coverage jobs1 retain unique raw files and standalone defaults1; malformed flags refuse execution and a manual run off main cannot publish the main badge

#### Scenario: Historical code is not current source coverage
- **WHEN** a helper executes a historical module outside the present production source namespace
- **THEN** its identity is retained as historical and no extra/missing current population is silently omitted or accepted

### Requirement: Frontend static test and browser evidence is complete
The frontend required context SHALL combine mandatory static/knip/build evidence with both complete Vitest shard verdicts. Vitest children MAY begin directly after successful route admission; browser SHALL still await the source-bound build produced by successful guards. The browser (Playwright e2e) job SHALL be required through the required check (owner decision bu-w72da2 = A): it SHALL run for every merge group and main push and for every pull request the route classifies as frontend (a change under frontend/, .github/workflows/, scripts/ci_ or scripts/check_ci_, or an undeterminable diff), and its failure, cancellation or unexpected skip SHALL fail that check. It SHALL consume the one genuine source-bound build artifact without rebuilding and SHALL preserve all locked browser/OS dependency and finite installer assertions. Every Vitest child SHALL collect one fresh complete unsharded installed item reference concurrently with its actual shard execution over the same immutable source/config/lock/runtime and separate private caches; both phases SHALL finish successfully and reconcile before admission. Its pre-body-emitted declaration tree, logical-ready and terminal-result multiplicities SHALL match that independent CURRENT reference and exact installed whole-file selector, including all parameter and skip/todo distinctions. Required frontend success SHALL independently reconcile both complete full references and both opaque selected/start/result ledgers; file counts, cached previous populations and matrix SUCCESS alone MUST NOT authorize it. Default isolated-fork selectors/configuration SHALL remain unchanged. Full collection plus execution and own cleanup SHALL share one900s total envelope under unchanged finite job caps; the former failed180s full-prepass outcome and subphase relocation SHALL be recorded explicitly without hard-metric or prior-cap credit.

ID: REQ-ci-shard-assurance-007
Source: bu-ly3lv5.6 P10; original complete S1-S5 and ten folded source objects; adopted testing-and-verification; bu-w72da2 owner decision A (2026-10-21)
Scope: v1-mandatory

#### Scenario: Every frontend contributor is required
- **WHEN** static, knip, build, Vitest1 or Vitest2 fails or is unknown
- **THEN** frontend and the required applicable gate refuse success without Vitest hiding the earlier static failure

#### Scenario: Two Vitest shards preserve exact population
- **WHEN** both installed shard selectors run
- **THEN** their current collected test population is complete/disjoint and all old assertions/outcomes remain represented

#### Scenario: A browser failure fails the required check
- **WHEN** the Playwright e2e job fails, is cancelled or is skipped in a merge group, a main push or a pull request the route classifies as frontend (frontend/, workflow or CI-script changes)
- **THEN** the required check refuses success, so the merge group cannot land
- **AND** every other pull request requires the job's explicit skip

#### Scenario: Browser reuses a genuine build only
- **WHEN** the guards-built dist has valid source/run/attempt/config/lock digests and nonempty contents
- **THEN** browser tests use it without a second build while locked browser and OS dependencies remain verified

#### Scenario: Stale or absent build is not readiness
- **WHEN** dist is empty, missing, stale, tampered or belongs to another attempt
- **THEN** the browser proof is unavailable/non-passing rather than a fabricated cache hit

#### Scenario: Fresh files and parameters define current authority
- **WHEN** an eligible file or parameter case is added, deleted or changed
- **THEN** fresh actual unsharded full collection and matching shard declaration trees govern complete current item identity without admitting a previous8280 population or using file enumeration as item proof

#### Scenario: Collection must precede every logical item event
- **WHEN** a ready or result event appears before its actual module collection, or a declared occurrence is missing, duplicated or replaced at equal aggregate count
- **THEN** both the producer and independent frontend reconciler refuse success while a genuine complete declaration/start/result companion passes

#### Scenario: Skipped and todo declarations remain explicit
- **WHEN** declared skip, todo, inherited skip or dynamic skip items are collected
- **THEN** their genuine declaration and logical event multiplicities remain present with truthful outcome distinctions, and pending, failed or unexecuted items cannot be relabelled skipped to pass

#### Scenario: Both current halves cover the independent full source
- **WHEN** both installed shard selectors complete
- **THEN** independent frontend verification checks both fresh complete unsharded references agree, every current full specification exactly once, matching selected per-file collected trees and disjoint item multisets and exact ready/result multiplicities before its required success

#### Scenario: Unknown child and transport evidence are non-green
- **WHEN** a required file, collected item, receipt, final trailer, source identity or result is missing, stale, extra, malformed or failed
- **THEN** the required frontend context fails without revealing names, parameters, raw output or exception text, and bounded cleanup releases only its own invocation resources

#### Scenario: Relocated bounds retain their actual meaning
- **WHEN** one genuine full unsharded collection and actual shard execution spend the same900s total deadline
- **THEN** the former failed180s whole-item prepass stays failed, its subphase relocation is explicit, all original finite job caps and ten-run hard metrics remain binding, and no wall-clock gain or guardsp95 success is inferred

#### Scenario: Future shard-sensitive declarations refuse readiness
- **WHEN** a newly added or changed file declares different items under the actual shard config than its fresh unsharded full reference
- **THEN** both producer and required frontend reconciliation fail despite matching file coverage or aggregate counts, while identical complete current declarations pass

#### Scenario: Dependency prebundling preserves independent item authority
- **WHEN** the supported Vitest dependency optimizer reuses bundled library transforms in node or jsdom workers
- **THEN** every child still performs its own fresh complete unsharded collection and actual isolated-fork shard execution, with unchanged full-reference, declaration, ready and terminal-result reconciliation

### Requirement: Measured efficiency and owned integration remain explicit
The implementation SHALL preserve finite watchdogs, all protected checks and the five-minute lane target. Matrix conversion and frontend expansion SHALL account for actual expanded rows and a concurrent peak no greater than14. Unavailable p95/spread/timing evidence SHALL remain an unfinished observation, never an inferred gain.

ID: REQ-ci-shard-assurance-008
Source: bu-ly3lv5.6 P11-P12; original complete S1-S5 and ten folded source objects; adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: uv setup owns finite attempts and surviving descendants
- **WHEN** a uv installer hangs, fails, succeeds early with surviving children, or receives cancellation
- **THEN** its supervisor kills its owned process group before reaping the reserved leader, including on early shell exits
- **AND** each attempt reserves one second of its 120-second bound for cleanup, with at most five attempts and four ten-second backoffs; cleanup failure aborts without retry
- **AND** only successful installation publishes the executable path, and subsequent frozen environment repair and validation remain mandatory

#### Scenario: Concurrent capacity follows the actual DAG
- **WHEN** guards, eleven backend children, two Vitest children, browser and post jobs become eligible
- **THEN** dependencies bound peak14 and account for20 full-mode expanded rows and21 total declared expanded rows, including the new inventory producer and mutually exclusive affected row without changing required contexts

#### Scenario: Metrics retain their full ten-run windows
- **WHEN** route/guards/spread/lane performance observations are incomplete
- **THEN** the15s/180s/<15%/five-minute promises remain UNMET and no seven-minute amendment or fabricated samples are adopted

#### Scenario: Every moved checker has an enforcing survivor
- **WHEN** planner/manifests/inventory/budget/smoke/static/build/reporting enforcement moves
- **THEN** a command/scope/failure-mutation map identifies its actual mandatory survivor with positive and negative controls

#### Scenario: Parallel changes retain source ownership
- **WHEN** the .5 setup/observer/worker cache work or other current protected/public hunks overlap
- **THEN** root serializes the exact coherent public source/native union, preserving foreign assertions/tasks and taking no peer completion credit
