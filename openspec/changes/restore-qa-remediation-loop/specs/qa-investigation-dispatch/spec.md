## MODIFIED Requirements

### Requirement: Investigation Creation from QA Finding
The QA dispatcher SHALL create investigations for novel findings, using the existing `healing_attempts` table with a QA-specific source marker and incident linkage. The original finding source remains distinct from the executing QA runtime owner. Journal-event and notes IDs SHALL be UUIDv7 for time-ordered sortability; the reused `healing_attempts.id` and `qa_findings.id` primary keys retain their legacy `gen_random_uuid()` (UUIDv4) defaults.

ID: REQ-qa-investigation-dispatch-004
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Create investigation from finding
- **WHEN** a novel finding passes admission gates and is accepted for investigation
- **THEN** a row is inserted in `public.healing_attempts` with: `id` (UUIDv4, from the table's legacy `gen_random_uuid()` default), `fingerprint` matching the finding, `butler_name` matching the finding's `source_butler`, `status = "investigating"`, `severity` from the finding, `exception_type` and `call_site` from the finding, `sanitized_msg` from the finding's `event_summary`
- **AND** the row includes `qa_patrol_id` linking it to the originating patrol cycle
- **AND** the finding's `qa_findings.healing_attempt_id` is updated with the new attempt ID

#### Scenario: Concurrency cap enforcement
- **WHEN** the number of QA-originated active investigations (`count_active_attempts(pool, qa_only=True)`) reaches `max_concurrent_investigations`
- **THEN** remaining novel findings in this patrol cycle are skipped (not dispatched immediately)
- **AND** skipped findings retain `dedup_reason = "concurrency_cap"` in qa_findings and a linked fenced pending-work record as sole durable backlog authority; the retired dispatch_queued flag is not written
- **AND** subsequent patrols lease deferred findings without deleting or clearing their durable pending state; acknowledgement occurs only in the transaction recording an authoritative attempt link or explicit final suppression disposition
- **AND** a log INFO message indicates skipped findings count

#### Scenario: Saturation and restart preserve a one-shot finding
- **WHEN** every investigation slot is occupied and a persisted novel finding is deferred, or a worker crashes after leasing it
- **THEN** its pending disposition survives beyond the discovery lookback and is eligible after lease expiry
- **AND** two competing claimants cannot create two active investigations for the same admitted incident revision
- **AND** owner holds and unresolved publication lineage remain blocking on every retry

### Requirement: QA Investigation Agent Prompt
The QA investigation agent SHALL receive a prompt that includes the error context from the discovery source, not from a live session. No raw logs or user data are included.

ID: REQ-qa-investigation-dispatch-005
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Agent prompt composition
- **WHEN** the investigation agent is spawned
- **THEN** its prompt includes: error fingerprint, exception type, sanitized event summary, call site (module path), source butler name, occurrence count and time range, discovery source type, and instructions to identify the applicable manifesto, RFC and capability requirements, reproduce the failure, implement a bounded correction, follow the exact-node/file/planner test ladder, and commit with an honest verification receipt
- **AND** the prompt explicitly instructs the agent to NOT include any user data, PII, or sensitive content in commits or PR descriptions
- **AND** the prompt explicitly instructs the agent to ignore unrelated repository workflow instructions and to not run `bd`, push branches manually, or open PRs itself

#### Scenario: Agent uses specialty model tier
- **WHEN** the investigation agent is spawned
- **THEN** it uses `Complexity.SPECIALTY` for model resolution (the legacy `self_healing` tier was retired in migration core_093 and is accepted only as a deprecated alias that remaps to `specialty`)
- **AND** if no model is available in the `specialty` tier, the launch is refused with dispatch reason `"no_model_available"` and no launched execution failure

#### Scenario: Agent context from reactive butler reports
- **WHEN** the finding originated from a `butler_reports` source (via `report_error`) with a non-empty `context` field
- **THEN** the agent's diagnostic reasoning is included in the investigation prompt (after anonymization)
- **AND** this gives the investigation agent a head start on diagnosis

#### Scenario: Diagnosis needs a behavior or authority change
- **WHEN** the proposed remedy changes a governing contract, extends manifesto scope, or requires an unapproved permission
- **THEN** the investigator emits a bound `design_required` outcome with the evidence and proposed contract delta
- **AND** the trusted coordinator retains that proposal for owner review without implementing or publishing the new behavior
- **AND** narrow corrections already defined by the governing spec remain eligible for autonomous implementation and targeted verification

### Requirement: Phased Investigation Workflow
The QA investigation infrastructure SHALL support phase-session tracking for investigations. Phase sessions are recorded and tracked via `record_phase_session` and `update_phase_session_status`. The v1 implementation uses a single combined `investigate` phase; separate diagnose, implement, and verify phases are a future extension enabled by this infrastructure.

ID: REQ-qa-investigation-dispatch-006
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Diagnose, implement, and verify use separate sessions
- **WHEN** QA investigates a finding that requires diagnosis, code changes, and verification
- **THEN** it SHALL record at least one phase session (currently a single `investigate` phase) with QA owner and actual launch timestamp before execution can proceed, so lineage survives interruption
- **AND** each session uses its own per-session timeout within the incident's remaining aggregate work budget
- **AND** the investigation remains open across phases until it reaches a terminal result or the overall deadline expires

#### Scenario: Crash or cancellation during launch
- **WHEN** execution starts but the dispatch coroutine does not return normally
- **THEN** the durable attempt-to-session link and launch receipt remain available to recovery
- **AND** a missing completion is unknown rather than proof that no agent ran
- **AND** phase duration uses actual start/end evidence and never the timestamp of later bookkeeping

#### Scenario: Lifetime work cap spans initial and follow-up sessions
- **WHEN** another investigation or feedback-follow-up session is considered for the same incident generation
- **THEN** persisted total launch count, cumulative running time and active reservations are checked atomically
- **AND** defaults allow at most three launched sessions and sixty minutes cumulative running time, with each phase bounded by the smaller remaining and existing phase limit
- **AND** waiting for human review consumes no running-time allowance, while active watchdogs still enforce individual execution deadlines
- **AND** exhaustion retains evidence and requests operator disposition without silently resetting on cooldown, restart or new feedback

### Requirement: Investigation Outcome Recording
All investigation outcomes SHALL be recorded for dashboard reporting, PR tracking, and trend analysis. Journal-event and notes record IDs SHALL be UUIDv7; the reused `healing_attempts.id` and `qa_findings.id` primary keys retain their legacy `gen_random_uuid()` (UUIDv4) defaults.

ID: REQ-qa-investigation-dispatch-007
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Successful investigation with PR
- **WHEN** the trusted publisher confirms creation of the attempt-bound PR
- **THEN** the `healing_attempts` row is updated: `status = "pr_open"`, `pr_url`, `pr_number`, `branch_name`
- **AND** PR-open records remain non-terminal with `closed_at` unset

#### Scenario: PR status tracking
- **WHEN** a healing attempt has `status = "pr_open"`
- **THEN** the QA staffer periodically consumes the trusted publisher boundary's bound read-only PR projection (on each patrol cycle)
- **AND** only the trusted publisher resolves the dedicated credential; the daemon receives a sanitized exact-repository/PR/head projection through the existing bounded read contract, never credentials or generic provider-operation authority
- **AND** if the PR is merged, status transitions to `pr_merged`, records merged_at and terminal closed_at at the observed merge timestamp, without claiming deployment or recovery
- **AND** if the PR is closed without merge, status transitions to `failed` with `error_detail = "pr_closed_without_merge"` and closed_at at the observed closure time; this is a publication disposition, not an execution failure

#### Scenario: Failed investigation
- **WHEN** the investigation agent fails (tests don't pass, no fix found, crash)
- **THEN** the `healing_attempts` row is updated: `status = "failed"`, `error_detail` with sanitized failure reason
- **AND** the `closed_at` timestamp is set

#### Scenario: Agent determines issue is unfixable
- **WHEN** current bound evidence supports an operational non-code diagnosis and names the actionable reason
- **THEN** the `healing_attempts` row is updated: `status = "unfixable"`, a typed `diagnosis_outcome = "operational"` and sanitized evidence-backed reason
- **AND** the `closed_at` timestamp is set

#### Scenario: Empty branch or unsupported conclusion
- **WHEN** an invocation returns successfully but supplies no supported diagnosis and no validated patch
- **THEN** QA records `diagnosis_outcome = "inconclusive"` with attempt lifecycle status `failed` while retaining the actual session execution outcome and a bounded reason, not `unfixable`
- **AND** this cannot count as recovery or reset an execution failure streak as a successful resolution

#### Scenario: Distinct supported outcomes
- **WHEN** a bound investigation completes
- **THEN** its diagnostic outcome is exactly one of `inconclusive`, `blocked_environment`, `operational`, `already_fixed`, `design_required`, or `patch_ready`
- **AND** `already_fixed` requires the relevant revision plus a passing reproducer, while `patch_ready` requires an exact proposal head and verification receipt
- **AND** diagnostic outcome, execution failure, publication state and observed recovery remain separate facts

#### Scenario: Terminal lifecycle and retention for no-PR results
- **WHEN** a current bound diagnostic result completes without an active PR
- **THEN** operational, already_fixed and design_required map to terminal unfixable, already_fixed and design_required attempt statuses respectively; blocked_environment and inconclusive map to terminal failed with their distinct diagnosis_outcome
- **AND** closed_at is the authoritative completion timestamp for those states and private raw evidence becomes eligible for the existing fourteen-day post-closure purge
- **AND** patch_ready before publication is nonterminal proposal_ready; an anonymization validation refusal retains anonymization_failed and closed_at, while other terminal publisher refusals set attempt failed and closed_at while preserving the immutable proposal, and ambiguous publication remains nonterminal held with closed_at unset until reconciliation
- **AND** session execution_outcome is completed, failed, timeout or unknown independently of attempt status; a prelaunch refusal has no session execution outcome
- **AND** the execution breaker counts actual failed/timeout sessions and completed inconclusive diagnoses, does not count prelaunch or publication-only refusals, and resets only on a completed bound supported diagnosis; unknown or blocked-environment results neither reset nor fabricate an execution failure
- **AND** design_required and operational results create an explicit unresolved operator disposition; already_fixed requires verified evidence and does not imply a PR repair

#### Scenario: One canonical breaker event per completed reasoning session
- **WHEN** a trusted initial or follow-up session completion is classified
- **THEN** it appends one idempotent ordered breaker event keyed by session identity and its authoritative ended_at, with contribution failure, supported_success or neutral under the outcome rules above
- **AND** every admission, summary, detail chip and reset evidence consumer folds the same projection after the latest auditable reset cutoff, ordered by ended_at and stable event identity
- **AND** failure advances the streak, supported_success clears it, neutral leaves it unchanged, and unknown completion cannot manufacture an event or a reset
- **AND** the attempt may remain pr_open when its completed session clears the streak; a failed follow-up contributes even while its PR is open; late PR closure contributes no new execution event
- **AND** reset cutoff applies to authoritative session completion time, so delayed persistence of a pre-reset completion cannot revive the old streak

#### Scenario: Historical outcomes cannot acquire invented certainty
- **WHEN** a legacy outcome lacks current trustworthy provenance or used no-op-as-unfixable classification
- **THEN** readers retain its original record with an explicit legacy/unverified or contaminated annotation and exclude it from verified recovery counts
- **AND** annotation is idempotent and does not replay investigations or overwrite original audit evidence

### Requirement: Investigation Notes Artifact
QA SHALL accept investigation notes only from a fresh output channel bound by trusted dispatch metadata to the exact invocation, attempt, source evidence revision and checkout base/head. Notes remain a portable JSON artifact; runtime-specific final-response schemas are not required.

ID: REQ-qa-investigation-dispatch-008
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Agent emits investigation notes JSON
- **WHEN** an investigator writes the schema-v2 artifact at its dispatcher-provided invocation-fresh output path inside the isolated checkout, outside tracked files and excluded from Git exports
- **THEN** trusted metadata and returned artifact agree on invocation, attempt, evidence revision, base and proposed head before any narrative is promoted to a diagnosis
- **AND** retained content includes schema_version, headline, hypothesis, blurb_segments, claims, evidence_lines, counter_evidence, why_this_fix and diff_snapshot, with explicit diagnosis outcome and verification references
- **AND** claims identify supplied evidence, distinguish observations from hypotheses, and describe patches as proposals until verified deployed
- **AND** persisted notes use UUIDv7 record identity and are linked only to the validated attempt

#### Scenario: Dispatcher reads and persists notes
- **WHEN** the dispatcher consumes an emitted artifact
- **THEN** it validates the same bounded file descriptor against its trusted invocation metadata before persisting notes under the linked finding's structured_evidence.investigation_notes
- **AND** invalid or absent notes produce a visible provenance limitation, without promoting a terminal process result into a supported diagnosis

#### Scenario: Inherited missing mismatched or malicious artifact
- **WHEN** the checkout already contains notes, the agent emits nothing, output is malformed or oversized, or any trusted invocation binding differs
- **THEN** no stale or mismatched narrative becomes current evidence and provenance is recorded as unavailable or rejected
- **AND** symlinks, path traversal and a changed file between validation and consumption cannot substitute another artifact; the writer is quiesced before the trusted collector reads the same bounded descriptor
- **AND** a separately authenticated patch/execution receipt may survive, but no supported diagnosis is inferred from parser success or process exit alone

#### Scenario: Notes fields are agent-authored (not re-anonymized internally)
- **WHEN** validated notes are retained
- **THEN** raw evidence is confined to the existing private evidence_lines field and its existing retention schedule
- **AND** all other narrative fields and every external payload remain subject to the existing anonymization and trusted publisher contracts
- **AND** schema/provenance rejection is observable and no partial parser fallback grants diagnosis authority

#### Scenario: Historical contamination is visible without rewriting history
- **WHEN** a stored diagnosis is proven to originate from the tracked legacy artifact or has no invocation provenance
- **THEN** the original evidence is retained with a derived contaminated or legacy-unverified annotation
- **AND** the case displays the source finding and provenance limitation rather than presenting the inherited headline as its diagnosis
- **AND** the tracked runtime artifact and all producing prompt/skill paths are retired in the same change

### Requirement: Commit-Time Diff Snapshot
The dispatcher SHALL capture a unified diff of the entire proposed change against the dispatcher-pinned base at proposal finalization and persist it as a structured line-kind sequence inside `investigation_notes.diff_snapshot`.

ID: REQ-qa-investigation-dispatch-009
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Diff captured before worktree teardown
- **WHEN** the agent's final commit step succeeds and before the worktree is removed
- **THEN** the dispatcher runs a diff of the immutable recorded base against the exact proposed head, covering every proposal commit
- **AND** the dispatcher parses the unified-diff output into a list of `{ kind, text }` objects where `kind ∈ {"meta", "+", "-", " "}` (meta covers `diff --git` and `@@` hunk headers and filename markers)
- **AND** the parsed snapshot is written into `qa_findings.structured_evidence.investigation_notes.diff_snapshot`

#### Scenario: Large diff truncation
- **WHEN** the diff snapshot exceeds 10 000 lines
- **THEN** the snapshot is truncated to the first 10 000 lines
- **AND** a final `{ kind: "meta", text: "... (truncated, N more lines)" }` marker is appended where `N` is the dropped-line count

#### Scenario: No commit (unfixable verdict)
- **WHEN** the agent terminates without producing a commit (including a separately supported operational diagnosis or an inconclusive invocation)
- **THEN** the dispatcher writes `diff_snapshot: []` (empty array)
- **AND** no `git diff` is invoked

#### Scenario: Display truncation is not publication authority
- **WHEN** the bounded preview is truncated or the proposal contains multiple commits
- **THEN** the preview states its incomplete coverage and never replaces validation of the complete immutable export
- **AND** an inherited base commit cannot be presented as a newly proposed fix

### Requirement: Journal Event Emission — Dispatch
The dispatcher SHALL emit structured journal events into `public.qa_investigation_events` for every dispatch-owned step in a case's lifecycle.

ID: REQ-qa-investigation-dispatch-010
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: drafted event on PR creation
- **WHEN** an investigation creates a PR and transitions to `status = 'pr_open'`
- **THEN** the dispatcher inserts a `qa_investigation_events` row with `step = 'drafted'`, `attempt_id`, `text` summarizing the PR (`"PR #<number> · <branch>"`), and a `detail` containing `+<additions> / −<deletions> · <files_touched> files`
- **AND** the event's `ts` is the PR creation timestamp

#### Scenario: wait event on CI pending
- **WHEN** the PR status poller observes that a PR is `pr_open` with CI status `pending`
- **THEN** the poller inserts a `qa_investigation_events` row with `step = 'wait'`, `text` describing the wait state (`"CI · <N> checks pending"`), and a `detail` listing the pending check names if available
- **AND** wait events are de-duplicated: at most one wait event is recorded per attempt per patrol cycle

#### Scenario: merged event on PR merge
- **WHEN** the PR status poller observes that a PR has transitioned to `pr_merged`
- **THEN** the dispatcher inserts a `qa_investigation_events` row with `step = 'merged'`, text stating only the proven merge fact, with independently sourced CI, deployment and recovery facts when available

#### Scenario: escalated event on unfixable terminal
- **WHEN** an attempt transitions to `status = 'unfixable'` OR a typed terminal outcome requiring operator action
- **THEN** the dispatcher inserts a `qa_investigation_events` row with `step = 'escalated'`, `text` summarizing the reason, and a `detail` referencing the user-action surface (e.g., `"surfaced on /overview attention"`)

#### Scenario: Unknown evidence does not produce a successful journal claim
- **WHEN** merge, CI, deployment or recovery evidence is absent, partial or stale
- **THEN** the journal names the unknown stage without fabricating successful checks, a clean next patrol or a closed incident

### Requirement: Gate Sequence Preservation

The QA dispatcher SHALL preserve the existing admission protections for each
novel finding before investigation. Triage performs a fast non-atomic dedup
check to filter obvious duplicates early; dispatch performs the authoritative
atomic claim only after normal eligibility and active-infrastructure-condition
suppression have both been evaluated.

ID: REQ-qa-investigation-dispatch-013
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Gates applied per-finding after triage
- **WHEN** a novel finding passes triage's fast dedup check
- **THEN** the dispatcher applies normal eligibility checks for recursion,
  opt-in, fingerprint, severity, cooldown, concurrency cap, circuit breaker,
  and model resolution
- **AND** after those checks pass but before `create_or_join_attempt`, an
  `infra_state` finding is matched against an active infrastructure condition
  by explicit canonical source and fingerprint
- **AND** only a finding without a matching active condition proceeds to the
  authoritative atomic novelty claim and then to worktree/session launch
- **AND** findings rejected by a normal eligibility gate or active-condition
  suppression are recorded with their explicit rejection reason in
  `qa_findings.dedup_reason`
- **AND** any rejection before the first investigation session launches is tracked as a
  dispatch decision, not an execution failure

#### Scenario: Active infrastructure condition is checked before attempt claim
- **WHEN** an otherwise eligible `infra_state` finding matches an `open` or
  `aging` infrastructure-condition episode
- **THEN** the dispatcher writes the decision-only `infra_condition_open`
  dispatch event before calling `create_or_join_attempt`
- **AND** it returns without creating, joining, deleting, or changing a
  `healing_attempt`
- **AND** it invokes no LLM, creates no runtime session, and creates no
  worktree

#### Scenario: Incident state qualifies novelty before atomic launch
- **WHEN** ordinary eligibility passes for a previously observed incident generation
- **THEN** dispatch also checks material evidence revision, typed prior outcome, durable pending work, remaining aggregate budget and publication lineage holds
- **AND** these checks cannot bypass recursion, owner policy, severity, cooldown, concurrency, circuit-breaker or active-condition suppression
- **AND** atomic admission revalidates the checked revision and reserves the launch before execution; stale reservations reconcile against durable session lineage rather than blindly relaunching

## ADDED Requirements

### Requirement: QA-Owned Runtime and Launch Authority
Initial and follow-up QA investigations SHALL execute under a core-owned dispatch capability bound to the QA daemon identity and lifecycle generation, with explicit model resolution, budget ownership and launch lineage.

ID: REQ-qa-investigation-dispatch-011
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Multiple daemons start in any order
- **WHEN** QA and other butlers run in one process, restart or change their startup order
- **THEN** QA work uses the QA prompt/configuration, QA session store and QA accounting, retaining the finding source only as diagnostic context
- **AND** a stale capability refuses launch rather than selecting another daemon or a process-global fallback
- **AND** the model eligibility checked at admission is the resolution consumed at launch, or admission is repeated under the new resolution
- **AND** QA invocations retain an empty MCP map and the existing credential-free investigation boundary

#### Scenario: Lifecycle cleanup cannot remove a successor owner
- **WHEN** an older QA daemon generation shuts down after a replacement is registered
- **THEN** its cleanup cannot unregister or mutate the replacement's dispatch capability
- **AND** concurrent launches are charged once to the owning incident and runtime budgets

### Requirement: Evidence-Driven PR Follow-Up
QA SHALL observe PR state, exact head, required-check state and all unresolved feedback through a deterministic complete provider projection, then lease at most one bounded follow-up for each new actionable feedback/head state.

ID: REQ-qa-investigation-dispatch-012
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Inline-only feedback and duplicate pollers
- **WHEN** new unresolved inline feedback exists without a changes-requested review, and timer/patrol pollers race
- **THEN** complete paginated thread evidence produces one durable follow-up claim for the expected PR/head/feedback digest
- **AND** a repeated poll of the same consumed feedback does not launch another agent
- **AND** missing pages, failed reads or an unsupported CLI projection remain unknown and cannot imply no feedback

#### Scenario: Head changed budget exhausted or authority unavailable
- **WHEN** the expected head changes, QA policy is held, the shared budget is exhausted, or trusted publication is unavailable
- **THEN** the claimed operation is revalidated or held without a blind agent/publication retry
- **AND** any unknown remote effect retains its existing publisher lineage hold
- **AND** investigators receive only sanitized feedback and never provider credentials or authority to review, approve, merge or queue

#### Scenario: Candidate artifact survives publication outage
- **WHEN** a validated patch cannot be published
- **THEN** QA retains its immutable proposal and typed publication state through the existing restricted publisher's durable contract
- **AND** resumption reconciles exact bound resources and reuses the retained proposal rather than launching a new diagnosis solely to repeat publication
