## MODIFIED Requirements

### Requirement: Fresh collected membership and one inventory
The CI system SHALL derive both lane populations from the exact installed pytest corpus and lane marker predicates at run time. Durations, diffs, file globs and cached counts MUST NOT establish membership. Each backend lane group SHALL select its tests with pytest-split from that fresh selection and record how many tests it collected. Check-preflight SHALL make one fresh `--collect-only` count per lane, and both test budgets SHALL consume those same counts. Lane groups SHALL depend on route only, independently of static guards. Collection failures SHALL be non-passing.

ID: REQ-ci-shard-assurance-001
Source: bu-ly3lv5.6 P1-P3; bu-29wkwj.2 (owner decision bu-w72da2 = A; 2026-10-21 simplicity audit); adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: Both lane populations preserve their actual selectors
- **WHEN** a lane group and the check-preflight count run
- **THEN** both use the lane's one marker expression from `scripts/ci_lanes.py`, so inherited and mixed markers select the same tests in both

#### Scenario: New files are included without registration
- **WHEN** a new test file is added under a lane's roots
- **THEN** pytest collection selects it into exactly one group of each lane it belongs to without any .github registration or durations edit

#### Scenario: Unknown collection cannot become a budget pass
- **WHEN** the fresh collect-only run fails
- **THEN** check-preflight refuses both the totals and the budget, and the required check fails

#### Scenario: Historical counts do not replace current population
- **WHEN** the committed budget baseline or an old count is presented
- **THEN** the fresh collect-only count governs the comparison and historical counts remain provenance only

### Requirement: Deterministic whole-file weighted assignment
Each lane SHALL split its fresh selection into five unit or six integration groups with pytest-split's `duration_based_chunks` algorithm over the committed `.test_durations`. Contiguous chunks in collection order SHALL keep files whole except at group boundaries, under `--dist loadfile` within each group. Durations SHALL influence balancing only: a test missing from the file SHALL receive pytest-split's average duration and still run. The nightly covered groups SHALL measure every test's duration, and a `durations-refresh` job SHALL merge them, refuse a merge with any group missing, upload the merged file, and open a pull request with it on Monday's scheduled run or a manual dispatch on main. It MUST NOT push to main.

ID: REQ-ci-shard-assurance-002
Source: bu-ly3lv5.6 P4/P12; bu-29wkwj.2 (owner decision bu-w72da2 = A; 2026-10-21 simplicity audit); adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: Identical inputs give identical assignment
- **WHEN** the same collected selection and durations file are split twice
- **THEN** each group selects the same tests both times

#### Scenario: Unknown durations cannot drop a file
- **WHEN** a selected test is new, renamed or absent from `.test_durations`
- **THEN** it receives the average duration, lands in exactly one group and runs

#### Scenario: Source changes preserve compatible cost hints
- **WHEN** product source changes or a test file is added or edited
- **THEN** the committed durations still balance the unchanged tests, the changed ones use their recorded or average durations, and fresh collection still determines every selected test

#### Scenario: Mixed markers retain their two lane homes
- **WHEN** one file contains unit and integration items
- **THEN** each lane's marker selection collects its own items from the file, and each item runs once in its own lane

#### Scenario: Durations refresh is advisory and read only
- **WHEN** the durations-refresh job runs
- **THEN** a missing group's durations refuse the merge, the merged file is uploaded, and any change reaches main only through a reviewed pull request
- **AND** durations never change which tests a lane selects

### Requirement: Independent complete-identity execution reconciliation
Every required lane group SHALL record the number of tests it collected after marker deselection and group selection, as `collected=<n>` in its step summary and a `collected.json` evidence file. Check-preflight SHALL read every group's count, refuse a malformed, duplicated, missing or unexpected group, and compare each lane's sum with the fresh `--collect-only` count of the same selection. A lane whose sum differs SHALL fail check-preflight. Per-item execution receipts are no longer produced; pytest-split's deterministic selection over the one fresh collection, checked by these totals, replaces them.

ID: REQ-ci-shard-assurance-003
Source: bu-ly3lv5.6 P5; bu-29wkwj.2 (owner decision bu-w72da2 = A; 2026-10-21 simplicity audit); adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: Same counts with different parameters fail
- **WHEN** a group's tests differ from its share while every count still matches
- **THEN** the totals alone cannot detect it; the guarantee rests on pytest-split selecting deterministically from the same fresh collection, and every drop or duplicate that changes a count fails

#### Scenario: Duplicate execution fails independently of assignment
- **WHEN** a test is selected by two groups of one lane
- **THEN** the lane sum exceeds the collect-only count and check-preflight fails

#### Scenario: Missing and partial children cannot hide behind matrix success
- **WHEN** a group is skipped, cancelled or emits no count
- **THEN** all eleven expected groups are checked, the missing group is named and check-preflight cannot report success

#### Scenario: Retry identity and privacy remain exact
- **WHEN** a group uploads its count evidence
- **THEN** the evidence carries only the lane, group and an integer count, without test names or parameter values, and a negative, boolean, non-integer or duplicated count is refused

### Requirement: Fail-closed matrix routing and event policy
Routing SHALL remain stdlib-only and conservative. The required check SHALL remain verdict-only and always evaluate all current needed results under explicit event/plan rules. Lane groups SHALL depend on route only and start independently of static guards. Static guards SHALL remain mandatory in every required final verdict. Existing required contexts SHALL survive; merge_group MUST retain all eleven backend groups, check-preflight, guards, frontend and browser evidence.

ID: REQ-ci-shard-assurance-004
Source: bu-ly3lv5.6 P6-P7; bu-29wkwj.2 (owner decision bu-w72da2 = A; 2026-10-21 simplicity audit); adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: A full terminal tree requires eleven backend carriers
- **WHEN** merge_group or a full backend PR executes
- **THEN** five unit and six integration groups, guards and check-preflight must succeed; an unknown, failed or partial result fails the required verdict

#### Scenario: Scoped and docs skips remain explicitly bounded
- **WHEN** a genuine scoped backend PR or classifier-proven docs-only PR is evaluated
- **THEN** only the matching current skip policy is accepted and preflight never counts as a heavy child

#### Scenario: Main push retains fresh inventory without rerunning heavy lanes
- **WHEN** push targets main after protected queue validation
- **THEN** the lane groups and check-preflight skip explicitly, because the merge group already ran the totals and budget over the identical tree

#### Scenario: Unknown inputs widen or fail
- **WHEN** classifier/planner/inventory-change evidence is absent or an added needed job lacks skip permission
- **THEN** selection widens to full when it can be validly executed and final malformed or unknown verdict evidence fails

### Requirement: Complete eleven-input independent coverage reporting
The non-required reporter SHALL validate eleven distinct same-run CoverageData inputs independently before combine/report/badge. Full covered execution SHALL run only in the nightly workflow, on its schedule and on manual dispatch (owner decision bu-w72da2 = A). That run SHALL execute all five unit and six integration covered groups, check their collected totals against fresh collect-only counts, run the real smoke command, and upload the combined report as an artifact. The reporter SHALL bind the actual admitted workflow event, head, run, attempt, lane and group to every coverage input; a scheduled or manual run MUST NOT impersonate merge_group. Every prerequisite failure, skip, cancellation, missing input or malformed identity SHALL remain visibly non-passing before publication. Only successful scheduled/manual runs of main MAY update the badge; a manual run on another branch validates a coverage-pipeline change without updating it. Required check MUST NOT install or await reporting. Protected merge-group and ordinary PR group execution SHALL use CI_COVERAGE=0 with the same selection; the nightly coverage jobs SHALL use CI_COVERAGE=1; standalone default covered behavior and strict CI_COVERAGE 0/1 admission SHALL remain.

ID: REQ-ci-shard-assurance-006
Source: bu-ly3lv5.6 P9; bu-w72da2 owner decision A (2026-10-21); bu-29wkwj.2 (owner decision bu-w72da2 = A; 2026-10-21 simplicity audit); adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: Eleven real valid inputs pass the actual reader
- **WHEN** five unit and six integration inputs carry the exact head, run, attempt, lane, group and admitted nightly event
- **THEN** all pass the same installed CoverageData path before combination and report/badge publication

#### Scenario: One bad input refuses publication
- **WHEN** ten valid inputs accompany one corrupt, empty, missing, extra or wrong-identity input
- **THEN** reporting fails visibly before report/badge while test verdicts remain truthful, including when any prerequisite failed or skipped and when event metadata was borrowed from another trigger

#### Scenario: PR opt-out and standalone compatibility survive
- **WHEN** CI_COVERAGE is supplied0/1, malformed or absent in standalone execution
- **THEN** ordinary PR0 and protected merge-group0 have no cov arguments/uploads, the nightly coverage jobs1 retain unique raw files and standalone defaults1; malformed flags refuse execution and a manual run off main cannot publish the main badge

#### Scenario: Historical code is not current source coverage
- **WHEN** a helper executes a historical module outside the present production source namespace
- **THEN** its identity is retained as historical and no extra/missing current population is silently omitted or accepted

### Requirement: Frontend static test and browser evidence is complete
The frontend required context SHALL combine mandatory static/knip/build evidence with both complete Vitest shard verdicts. Vitest children and the browser job MAY begin directly after successful route admission. The browser (Playwright e2e) job SHALL build its own bundle from the same checkout and SHALL NOT wait for guards. It SHALL be required through the required check (owner decision bu-w72da2 = A): it SHALL run for every merge group and main push and for every pull request the route classifies as frontend (a change under frontend/, .github/workflows/, scripts/ci_ or scripts/check_ci_, or an undeterminable diff), and its failure, cancellation or unexpected skip SHALL fail that check. It SHALL preserve all locked browser/OS dependency and finite installer assertions. Every Vitest child SHALL collect one fresh complete unsharded installed item reference concurrently with its actual shard execution over the same immutable source/config/lock/runtime and separate private caches; both phases SHALL finish successfully and reconcile before admission. Its pre-body-emitted declaration tree, logical-ready and terminal-result multiplicities SHALL match that independent CURRENT reference and exact installed whole-file selector, including all parameter and skip/todo distinctions. Required frontend success SHALL independently reconcile both complete full references and both opaque selected/start/result ledgers; file counts, cached previous populations and matrix SUCCESS alone MUST NOT authorize it. Default isolated-fork selectors/configuration SHALL remain unchanged. Full collection plus execution and own cleanup SHALL share one900s total envelope under unchanged finite job caps; the former failed180s full-prepass outcome and subphase relocation SHALL be recorded explicitly without hard-metric or prior-cap credit.

ID: REQ-ci-shard-assurance-007
Source: bu-ly3lv5.6 P10; bu-w72da2 owner decision A (2026-10-21); bu-29wkwj.2 (owner decision bu-w72da2 = A; 2026-10-21 simplicity audit) (browser no longer waits for guards); adopted testing-and-verification
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
- **WHEN** route admits the browser job
- **THEN** it builds the bundle from its own checkout and runs beside guards while locked browser and OS dependencies remain verified

#### Scenario: Stale or absent build is not readiness
- **WHEN** the browser job's own build fails or produces no bundle
- **THEN** the browser job fails, and so does the required check, rather than reusing any earlier build

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
The implementation SHALL preserve finite watchdogs, all protected checks and the lane target: the slowest group of each lane SHALL take 300 s or less, or the gap SHALL be reported with its measured cause. The 420 s lane p95 objective remains the tracked metric. Shard counts SHALL stay at least five unit and six integration groups and change only from measured durations. Every Python job in ci.yml SHALL set up through the one composite action. Matrix conversion and frontend expansion SHALL account for actual expanded rows and their concurrent peak. Unavailable p95/spread/timing evidence SHALL remain an unfinished observation, never an inferred gain.

ID: REQ-ci-shard-assurance-008
Source: bu-ly3lv5.6 P11-P12; bu-29wkwj.2 (owner decision bu-w72da2 = A; 2026-10-21 simplicity audit); adopted testing-and-verification
Scope: v1-mandatory

#### Scenario: uv setup owns finite attempts and surviving descendants
- **WHEN** a uv installer hangs, fails, succeeds early with surviving children, or receives cancellation
- **THEN** its supervisor kills its owned process group before reaping the reserved leader, including on early shell exits
- **AND** each attempt reserves one second of its 120-second bound for cleanup, with at most five attempts and four ten-second backoffs; cleanup failure aborts without retry
- **AND** only successful installation publishes the executable path, and subsequent frozen environment repair and validation remain mandatory

#### Scenario: Concurrent capacity follows the actual DAG
- **WHEN** guards, eleven backend groups, two Vitest children, browser and post jobs become eligible
- **THEN** dependencies bound the peak at fifteen (guards, eleven groups, two Vitest children and browser), with nineteen full-mode expanded rows and twenty total declared rows including the mutually exclusive affected row, without changing required contexts

#### Scenario: Metrics retain their full ten-run windows
- **WHEN** fewer than five merge groups have run the lane groups, or a lane's slowest group exceeds 300 s
- **THEN** the lane target stays UNMET with the median, p95 and cause reported, the route and guards promises stay UNMET until measured, and no gain is inferred from fewer samples

#### Scenario: Every moved checker has an enforcing survivor
- **WHEN** inventory, partition, receipt, budget, smoke or browser-build enforcement moves
- **THEN** each has a named survivor: the check-preflight totals (drops and duplicates), the check-preflight, `make check` and pre-push budget, the check-preflight smoke command, and the guards and browser builds

#### Scenario: Parallel changes retain source ownership
- **WHEN** the .5 setup/observer/worker cache work or other current protected/public hunks overlap
- **THEN** root serializes the exact coherent public source/native union, preserving foreign assertions/tasks and taking no peer completion credit

#### Scenario: One setup path
- **WHEN** a ci.yml job needs Python
- **THEN** it uses the composite action and the workflow carries no inline uv setup block
