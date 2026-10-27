## MODIFIED Requirements

### Requirement: CI Shard Ordering and Complete Timing Evidence
CI lane groups SHALL preserve every selected test, marker, loadfile fixture boundary within a group, named watchdog/finalizer and healthy-workload calibration. Group balance SHALL come from pytest-split's `duration_based_chunks` over the committed `.test_durations`, which keeps collection order; a test without a recorded duration SHALL use pytest-split's average and still run. Durations SHALL be measured from the actual nightly covered group runs as each test's setup, call and teardown seconds, without exporting raw parameter, environment, credential or provider content beyond the pytest node ids already in the repository. Existing 2x-budget intent SHALL use an evidenced complete job budget including setup and recovery, never an aspirational lane target.

ID: REQ-testing-046
Source: bu-ly3lv5.5 full original seven outcomes and twelve committed folded objects; released bu-7lh5ew; bu-29wkwj.2 (pytest-split over committed durations; owner decision bu-w72da2 = A); AGENTS.md test policy; testing-and-verification; performance-discipline
Scope: v1-mandatory

#### Scenario: Duration ordering preserves membership
- **WHEN** `.test_durations` is current, stale, partial or missing
- **THEN** every selected test still runs in exactly one group of its lane, and only the group sizes change

#### Scenario: Invalid timing evidence has a truthful fallback
- **WHEN** a selected test has no recorded duration
- **THEN** pytest-split assigns it the average duration and the lane's full selection still runs
- **AND** the durations refresh refuses to merge when any group's measured durations are missing

#### Scenario: Scheduler behavior is actually reached
- **WHEN** a real pytest run selects each group of a split with `-p ci_group_count`
- **THEN** the recorded group counts sum to the full selection and each group's measured durations name only its own tests

#### Scenario: Result identity precedes sanitization
- **WHEN** a lane group runs and its JUnit is later sanitized
- **THEN** its collected count and measured durations are recorded from the actual pytest process before sanitization
- **AND** sanitized JUnit counts are not treated as exact identity proof

#### Scenario: Timing records actual boundaries
- **WHEN** the nightly covered groups finish
- **THEN** each group's measured durations are uploaded with its evidence, and the merged refresh is uploaded on every run

#### Scenario: Watchdogs retain complete healthy-workload compatibility
- **WHEN** a grouping or timer change is proposed
- **THEN** existing item, job, UV and browser bounds and finalizers survive until genuinely calibrated replacement evidence exists
- **AND** twice a five-minute aspiration is not adopted

#### Scenario: Timing collection does not repartition the corpus
- **WHEN** durations are measured or refreshed
- **THEN** no lane's selection changes; a refreshed `.test_durations` reaches main only through a reviewed pull request

### Requirement: Composed read-only local pre-push refusal
The repository SHALL provide an installed read-only pre-push path for its applicable deterministic guards that preserves existing Beads hook behavior and exact Git ref-update input. The collection-dependent per-lane test budget SHALL use freshly collected real selected items, not planner/cache/count inference. The hook SHALL execute no test bodies, DB/provider/network calls or tracked-tree rewrites. Installation SHALL preserve exact prior common configuration and managed hook bodies/modes, refuse uncomposable custom inputs, and be reversible. The required CI guards and complete terminal merge-group SHALL remain independently authoritative. The original twenty natural-push p95-under30s target SHALL remain mandatory and unearned until measured.

ID: REQ-testing-052
Source: bu-ly3lv5.10 approved A123/B168 R1; bu-29wkwj.2 (pytest-split over committed durations; owner decision bu-w72da2 = A); about/heart-and-soul/development.md; about/craft-and-care/testing-and-verification.md; AGENTS.md Test Scope Policy
Scope: v1-mandatory

#### Scenario: Actual violation and restored ordinary push
- **WHEN** the owning real push path encounters a planted new em-dash in the mandated doctrine surface
- **THEN** its actual scanner refuses before remote publication with fixed failed-target attribution
- **AND** restoring the owning source makes that same actual path healthy without skipped invariants

#### Scenario: Beads hook input and other hook lifecycles survive
- **WHEN** Git invokes root or worktree hooks with multiple ref updates and actual args
- **THEN** the original managed hook receives the complete stream exactly once and its status propagates
- **AND** post-checkout and post-merge retain BD_IMPORT_AUTO=false outside managed markers

#### Scenario: Unsupported configuration or source refuses before mutation
- **WHEN** a custom/global/worktree-specific hook path, missing asset, unknown ref/tree or applicable unavailable tool cannot be safely composed
- **THEN** installation/check refuses categorically and exact before configuration/assets remain
- **AND** a restored supported path proves positive behavior

#### Scenario: Fresh collection-dependent predicate preserves actual population
- **WHEN** a change can alter test collection
- **THEN** the per-lane budget runs the real collect-only path against the complete fresh population
- **AND** stale receipts, cached counts, AST test counts, planner omissions and side-effect execution cannot claim green

#### Scenario: Natural timing and rollback are separately admitted
- **WHEN** twenty natural installed push envelopes and complete statuses are available
- **THEN** p95 strictly below30s is assessed from their exact source/runtime/plan and full hook boundaries
- **AND** unavailable timing or a missed target remains unmet; rollback restores exact prior config and all enforcing CI survivors
