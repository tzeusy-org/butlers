## ADDED Requirements

### Requirement: [TARGET-STATE] Relationship-only graph reconciliation scope
The graph SHALL have one reconciler that compares `relationship.entity_facts` with its `public.entity_graph_edges` projection and repairs only rows whose natural key is `source_schema='relationship'`, `source_table='entity_facts'`. The comparison set on the source side SHALL be entity-to-entity assertions (`object_kind='entity'`) whose lifecycle the Relationship contract still treats as active; literal and unary facts SHALL NOT acquire an edge. The reconciler SHALL add a missing edge, correct a stale `subject_entity_id`, `predicate` or `object_entity_id`, and remove an edge whose Relationship source row is absent or no longer an active entity-to-entity assertion. It SHALL NOT read, write or remove a Memory, commitment or any other source's projection row, SHALL NOT infer a fact, SHALL NOT read another schema's private tables, and SHALL NOT bypass Relationship's approval and central-writer boundary. Rows written by this reconciler SHALL be live edges with `sensitivity='normal'` and NULL `withheld_reason`, matching the Relationship live writer.

ID: REQ-entity-graph-013
Source: RFC0031 Amendment 1; bu-h40h2b.7 acceptance item 1(1); REQ-entity-graph-002, REQ-entity-graph-004
Scope: v1-mandatory

#### Scenario: Reconciler repairs a Relationship-only difference
- **WHEN** an active Relationship entity-to-entity fact has no projection, a projection with a stale payload, or a projection whose source row is retracted or absent
- **THEN** the reconciler SHALL respectively add, correct or remove that one `relationship`/`entity_facts` edge
- **AND** a second run over unchanged data SHALL report zero added, updated and removed rows

#### Scenario: Other sources are never touched
- **WHEN** `public.entity_graph_edges` holds Memory or commitment rows, including rows with no matching live source
- **THEN** the reconciler SHALL leave them unchanged and SHALL NOT count them in any receipt figure
- **AND** a Relationship literal contact-channel fact SHALL NOT acquire an edge

### Requirement: [TARGET-STATE] Identical reassert repairs a missing projection
When Relationship's central writer receives an assertion that matches an existing active fact (the `unchanged` outcome), it SHALL still ensure that the fact's source-keyed projection exists and matches, inserting or correcting it in the same outer transaction as the evidence write. A projection failure on this path SHALL roll back that transaction like any other eligible writer path. This requirement SHALL NOT change the assertion outcome returned to the caller and SHALL NOT make a reassert of an unchanged, already-projected fact write a second edge.

ID: REQ-entity-graph-014
Source: RFC0031 Amendment 1; bu-h40h2b.7 design (current-state correction); REQ-entity-graph-002
Scope: v1-mandatory

#### Scenario: Identical reassert repairs a deleted projection
- **WHEN** an identical entity-to-entity fact is reasserted and its projection row is missing
- **THEN** the projection SHALL exist after the transaction commits
- **AND** the caller SHALL still receive the `unchanged` outcome

#### Scenario: Identical reassert of a projected fact is a no-op
- **WHEN** an identical fact is reasserted and its projection already matches
- **THEN** no second edge SHALL be created
- **AND** natural-key convergence SHALL hold

### Requirement: [TARGET-STATE] Content-blind reconciliation receipts
Each reconciler attempt, including a skipped one, SHALL write exactly one receipt. A receipt SHALL carry only: a receipt identifier, the schedule occurrence identity, `started_at`, `finished_at`, `duration_ms`, `status`, the integers `added`, `updated`, `removed` and `remaining`, and the configured batch and batch-count limits. `status` SHALL be exactly one of `success`, `partial`, `failed` or `skipped_lock`. `success` SHALL mean the run reached a fixed point with `remaining=0`; `partial` SHALL mean a limit stopped the run with `remaining>0`; `failed` SHALL mean the run stopped on an error; `skipped_lock` SHALL mean another run held the lock. A receipt SHALL NOT contain any predicate, entity identifier, fact text, source identifier, error message text or other source content; a failure SHALL be recorded by status and a bounded code from a fixed vocabulary. A failed projection or receipt write SHALL roll back its own atomic unit; a `failed` receipt SHALL be written only in an independently safe transaction, and if that write also fails no receipt SHALL be fabricated. Receipts SHALL be retained for 90 days, and the most recent receipt of each status SHALL be kept beyond that age; pruning SHALL run inside the same job and SHALL be bounded by the same batch limit.

ID: REQ-entity-graph-015
Source: RFC0031 Amendment 1; bu-h40h2b.7 acceptance item 1(2) and behavior matrix
Scope: v1-mandatory

#### Scenario: Partial run reports what remains
- **WHEN** the batch-count limit is reached with differences still outstanding
- **THEN** the receipt status SHALL be `partial` with `remaining` set to the outstanding count
- **AND** the next scheduled run SHALL continue from the same comparison without a manual step

#### Scenario: Receipt contains no source content
- **WHEN** any receipt is stored for any status
- **THEN** it SHALL contain none of the withheld field classes listed in this requirement
- **AND** an error SHALL be recorded as a status and a bounded code only

#### Scenario: Failed run without a safe receipt transaction
- **WHEN** the run fails and the receipt transaction cannot be committed safely
- **THEN** no `success` or fresh-timestamp receipt SHALL be fabricated
- **AND** the absence of a newer receipt SHALL be observable as stale

### Requirement: [TARGET-STATE] Deterministic bounded reconciler schedule and lock
The reconciler SHALL run only as a native deterministic job: a Git-defined `[[butler.schedule]]` entry in `roster/relationship/butler.toml` with `dispatch_mode = "job"` and `job_name = "relationship_graph_reconcile"` on a daily cron. It SHALL NOT be a prompt-mode schedule, SHALL NOT start a reasoning session, and SHALL NOT depend on prompt-mode scheduling, goal admission or capability grants. A run SHALL take a non-blocking advisory lock (`pg_try_advisory_lock`) on a fixed key derived from the job name before any work; when the lock is not obtained it SHALL write one `skipped_lock` receipt and return, leaving retry to the next due run. The lock SHALL be held on a dedicated connection and SHALL NOT be held by a long-running transaction or snapshot. A run SHALL process at most 500 source rows per batch and at most 20 batches (10000 rows), committing each batch in its own short transaction, and SHALL stop with `partial` at the limit. Disabling the schedule entry SHALL stop all reconciliation and SHALL leave source facts and the last projection untouched. Deploying the job SHALL NOT run a live backfill.

ID: REQ-entity-graph-016
Source: RFC0031 Amendment 1; bu-h40h2b.7 acceptance item 1(2); RFC0038 D2 (deterministic scheduled jobs are not frozen); core-scheduler job dispatch mode
Scope: v1-mandatory

#### Scenario: Concurrent run is skipped
- **WHEN** a run starts while another holds the reconciler lock
- **THEN** it SHALL write exactly one `skipped_lock` receipt and perform no repair
- **AND** the lock-holding run's batches SHALL be unaffected

#### Scenario: Schedule is job-mode only
- **WHEN** the schedule entry is synchronized from Git
- **THEN** it SHALL have `dispatch_mode='job'` and a non-empty `job_name` and no `prompt`
- **AND** a prompt-mode entry for the same work SHALL be rejected by review

#### Scenario: Rollback
- **WHEN** the schedule entry is removed or disabled
- **THEN** no further reconciliation SHALL occur
- **AND** existing facts, projections and receipts SHALL remain intact

### Requirement: [TARGET-STATE] Receipt presentation never claims completeness
Any read-only presentation of reconciliation state SHALL expose the latest receipt's `status`, `finished_at` and counts additively, and SHALL distinguish `never run` (no receipt), `failed`, `partial`, `skipped_lock` and `success`. It SHALL report the graph as in sync only for a `success` receipt with `remaining=0`; a zero-count run with any other status SHALL NOT be presented as complete. Absence of a receipt or an unreadable receipt SHALL render as unknown, never as a fresh timestamp. Receipt and API fields SHALL be additive so that older clients see no fabricated health. This presentation SHALL be a later slice that lands only after the receipt contract is implemented and stable.

ID: REQ-entity-graph-017
Source: RFC0031 Amendment 1; bu-h40h2b.7 design step 3 and behavior matrix
Scope: v1-mandatory

#### Scenario: Never-run and zero-count success differ
- **WHEN** no receipt exists, and separately, a `success` receipt with all counts zero exists
- **THEN** the first SHALL present as never run and the second as in sync as of its `finished_at`
- **AND** a `partial` receipt with zero added SHALL NOT present as in sync

### Requirement: [TARGET-STATE] Named fleet evidence belongs to the situations detail
Rendering fleet-case evidence references as canonical names and links SHALL NOT be implemented in the Relationship entity-graph surfaces, in `EntityDetailPage`, or in the reconciler. That behavior is owned by bu-h40h2b.8 and its `/situations` detail, which SHALL type each evidence reference before resolving it, SHALL use existing canonical entity-name data, SHALL link only a validated entity UUID reference to `/entities/:id`, and SHALL leave an unknown or non-entity reference visibly non-clickable. No string-to-entity guessing and no content-bearing cross-schema shortcut SHALL be used. The reconciler and its receipts SHALL carry no fleet-case content.

ID: REQ-entity-graph-018
Source: RFC0031 Amendment 1; bu-h40h2b.7 design step 5 and acceptance item 1(4); bu-h40h2b.8
Scope: v1-mandatory

#### Scenario: Evidence reference handling is out of this surface
- **WHEN** a fleet-case evidence reference needs a name or link
- **THEN** resolution SHALL happen in the situations detail under bu-h40h2b.8
- **AND** the Relationship graph surfaces SHALL NOT resolve or render it
