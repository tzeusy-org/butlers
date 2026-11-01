## ADDED Requirements

### Requirement: Safe branch cleanup requires bound recovery and approval
Branch cleanup SHALL default to dry-run and SHALL refuse live deletion without an author-approved exact manifest, current head/PR/protection/custody checks, and independently verified durable recovery. It SHALL preserve open/live/foreign/uncertain refs and use expected-head deletion and non-overwriting recovery. The under-100 outcome SHALL remain mandatory without granting permission to delete excluded refs.

ID: REQ-testing-041
Source: bu-ly3lv5.3 released run17 intent and complete original/folded clause map; engineering-bar Change Hygiene; development; security-and-secrets
Scope: v1-mandatory

#### Scenario: Unreviewed dry-run cannot delete
- **WHEN** inventory includes a merged candidate and open, live, protected and uncertain companions
- **THEN** only recoverable reviewed candidates are proposed and every remote ref remains intact
- **AND** missing approval or incomplete pagination refuses apply

#### Scenario: Closed PR and changed head do not prove safety
- **WHEN** a closed PR name matches a different current head or an unreconciled unmerged ref
- **THEN** the ref remains excluded until exact history, fourteen-day stale evidence where applicable and recovery are verified
- **AND** a safely bound merged companion remains a candidate

#### Scenario: Apply drift cannot erase new work
- **WHEN** an approved ref changes or gains a PR/lease before deletion
- **THEN** apply refuses that row and preserves the new ref
- **AND** unchanged exact approved rows can be deleted with durable stage receipts

#### Scenario: Recovery and unknown acknowledgment are truthful
- **WHEN** delete acknowledgment is lost or the operator restores a deleted exact head
- **THEN** read-only reconciliation reports actual absent/same/changed/UNKNOWN and restore refuses any replacement ref
- **AND** isolated recovery reproduces the original object bytes without relying on deleted checkouts

#### Scenario: Cleanup order preserves workers
- **WHEN** an attached or dirty active worktree exists or owned worktree removal fails
- **THEN** branch cleanup remains unavailable
- **AND** ordinary owned clean terminal cleanup retains evidence outside the checkout and removes the worktree before branch references

### Requirement: Repository setting and cache pressure have independent evidence
The authorized operator SHALL enable delete_branch_on_merge, independently verify the setting, inventory/evict only approved non-main node caches, preserve genuine main-scoped node/uv cache semantics and report actual usage below ten decimal gigabytes. Cache eviction SHALL be reported as irreversible rebuildable loss, never as branch restoration or proof of a cache hit.

ID: REQ-testing-042
Source: bu-ly3lv5.3 released run17 intent and complete original/folded clause map; engineering-bar Change Hygiene; development; security-and-secrets
Scope: v1-mandatory

#### Scenario: Main survivor is positively witnessed
- **WHEN** approved non-main node caches are evicted with main and Playwright companions present
- **THEN** the exact main node-cache key/version remains readable and measured usage is below 10,000,000,000 bytes
- **AND** main, active/uncertain refs and non-node caches survive

#### Scenario: Setting alone is insufficient
- **WHEN** delete_branch_on_merge becomes true while old branches or pressure remain
- **THEN** setting success is recorded separately and prune/cache outcomes remain incomplete
- **AND** failure does not broaden an eviction list

#### Scenario: Cache race and rebuild preserve honesty
- **WHEN** a cache ID/ref/key changes or a needed main entry is unavailable
- **THEN** mutation refuses drift and main-presence remains unproven until real native rebuild/readback
- **AND** key existence alone does not claim frontend hit or wall-clock improvement

### Requirement: QA new branches use exact successfully refreshed main
Trusted QA preparation SHALL resolve successfully refreshed origin/main before new investigation work, record and use that exact commit, and refuse failed refresh before checkout/spawn. It SHALL preserve existing follow-up head bindings, active-worktree data and the governing QA sandbox/publication holds; routine main movement SHALL NOT trigger rebase of an active PR.

ID: REQ-testing-043
Source: bu-ly3lv5.3 released run17 intent and complete original/folded clause map; engineering-bar Change Hygiene; development; security-and-secrets
Scope: v1-mandatory

#### Scenario: Successful new preparation records the real base
- **WHEN** origin/main is successfully fetched and resolved before a new QA investigation
- **THEN** the new checkout uses the resolved immutable commit and source/test receipts retain it
- **AND** no existing worker checkout is reset

#### Scenario: Failed fetch does not start stale work
- **WHEN** fetch or exact commit verification fails while stale local main exists
- **THEN** no new worktree or runtime launches and a categorical refusal is retained
- **AND** a restored successful refresh reaches ordinary launch

#### Scenario: Follow-up and foreign publication contracts survive
- **WHEN** a bound existing PR needs follow-up or foreign QA isolation/publication remains unavailable
- **THEN** its original expected head and all retained source/evidence/credential boundaries remain in force
- **AND** this freshness repair supplies neither sandbox proof nor remote-deletion authority

### Requirement: Session-link guard uses validated live PR metadata
The mandatory PR session-link guard SHALL scan validated live title/body from the exact fixed repository/PR at execution rather than the frozen event copy. It SHALL fail closed on mandatory read/identity errors, preserve current trigger-head commit/trailer and review-source policies, avoid raw metadata diagnostics and retain required verdict routing without widened token/event authority.

ID: REQ-testing-044
Source: bu-ly3lv5.3 released run17 intent and complete original/folded clause map; engineering-bar Change Hygiene; development; security-and-secrets
Scope: v1-mandatory

#### Scenario: Corrected live body changes rerun verdict
- **WHEN** an affected author-owned PR removes its existing forbidden link and current guards rerun on unchanged source
- **THEN** the live body is scanned and passes while a private synthetic forbidden-body companion fails
- **AND** no empty commit or new public forbidden-link canary is required

#### Scenario: Stale clean payload cannot hide a new live link
- **WHEN** frozen event body is clean but the validated current private API fixture contains a forbidden link
- **THEN** the actual workflow scan fails
- **AND** a clean live companion succeeds

#### Scenario: Mandatory API failure cannot use stale fallback
- **WHEN** live metadata fetch fails, response is malformed or repository/PR identity differs
- **THEN** the mandatory guard and required verdict fail before scan success is credited
- **AND** no raw body, matched link or provider error is printed

#### Scenario: Existing metadata surfaces and permissions survive
- **WHEN** the current PR guard scans title/body and trigger-bound commits with best-effort review comments
- **THEN** title, non-trailer commit and comment positives remain detectable with exact terminal commit trailer exemption
- **AND** read-only permissions and ordinary pull_request/merge_group checkout policy are retained

### Requirement: Storm-free elapsed windows retain complete evidence
After authorized apply the operator SHALL retain both seven-day and fourteen-day complete UTC CI push-event windows with no non-main runs, under-100 remote-head readback and cache/main-survivor evidence. Incomplete queries or elapsed time SHALL remain UNKNOWN/incomplete. The selected timing route SHALL explicitly claim no wall-clock gain while preserving actual before/after state; merge_group SHALL remain the full terminal gate and every moved/removed check SHALL retain its named survivor.

ID: REQ-testing-045
Source: bu-ly3lv5.3 released run17 intent and complete original/folded clause map; engineering-bar Change Hygiene; development; security-and-secrets
Scope: v1-mandatory

#### Scenario: Seven days do not discharge fourteen
- **WHEN** seven complete days after declared apply T0 have no non-main push
- **THEN** the seven-day criterion passes and the fourteen-day criterion stays pending until its own end
- **AND** the original cannot close from source publication or a short sample

#### Scenario: Truncation and empty pages cannot produce green
- **WHEN** a query exceeds provider result limits or returns no rows without complete successful enumeration
- **THEN** the interval is UNKNOWN until bounded subdivision and complete pagination reconcile IDs/counts
- **AND** a genuine main-push positive proves the same reader path

#### Scenario: One non-main event fails the actual window
- **WHEN** a failed, cancelled, queued or successful CI push run has a non-main branch during either interval
- **THEN** that interval fails with its exact source/time identity retained
- **AND** no evidence deletion or silent T0 restart converts it to PASS

#### Scenario: No gain claim retains assurance
- **WHEN** source and approved cleanup reduce refs/cache pressure without five before/five after timing samples
- **THEN** the move explicitly claims no wall-clock gain and retains real state evidence
- **AND** required check/guards/frontend, full merge_group population and named enforcing survivors remain unchanged
