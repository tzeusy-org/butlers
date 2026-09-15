## MODIFIED Requirements

### Requirement: Scheduled curation jobs

The relationship butler SHALL run its curation work as independent
`dispatch_mode="job"` scheduled tasks on staggered crons: prose→edge proposal,
entity dedup, contradiction sweep (fact retraction), approval-expiry surfacing,
and episodic-predicate cleanup. Each job is headless; a job that produces
proposals or auto-applied changes MUST communicate its results via `notify()`.

#### Scenario: Each scheduled job runs on its own cron
- **WHEN** a curation job's cron fires (for example `fact-retraction-curation` on Mondays 05:00)
- **THEN** that job MUST run its task independently of the other curation jobs
- **AND** a job that produced any proposal or auto-action MUST call `notify()` with its own results
- **AND** a job that produced nothing actionable MAY exit without notifying

#### Scenario: Headless output reaches the owner only via notify
- **WHEN** a job produces proposals or auto-applied changes
- **THEN** the summary MUST be delivered through `notify()` (not session text, which is discarded in a headless run)

#### Scenario: Episodic fact reclassification proposal is replayable
- **WHEN** episodic-predicate cleanup proposes changing a fact to volatile permanence
- **THEN** it parks the exact registered Relationship `memory_reclassify` command
- **AND** owner approval executes that command through the standard approvals executor
- **AND** an inactive or missing fact produces a truthful execution failure rather than changing another fact or inventing a replacement command

#### Scenario: Direct memory reclassification remains approval-gated
- **WHEN** a normal Relationship caller invokes `memory_reclassify` for an active fact
- **THEN** the native approval gate parks the exact command without changing the fact
- **AND** owner approval replays the captured original handler exactly once
