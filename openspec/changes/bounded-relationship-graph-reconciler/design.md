## Context

- `public.entity_graph_edges` is a derived projection with natural key `(source_schema, source_table, source_id)`. RFC 0031 requires the owning writer to maintain it in the same transaction.
- `relationship_assert_fact._upsert_fact()` returns `AssertOutcome.unchanged` after persisting evidence without re-projecting, so a deleted projection is never repaired by an identical reassert.
- `backfill_relationship_entity_facts_edges()` in `src/butlers/core/entity_graph_edges.py` inserts missing relationship edges (500 per call) and has only test callers. It cannot correct a payload, remove an orphan or leave a receipt.
- `roster/relationship/butler.toml` already runs several `dispatch_mode = "job"` schedules. Job mode is a native deterministic function (core-scheduler), and RFC 0038's change keeps it that way.
- Rule scoring is `success_count / applied_count if applied_count > 0 else 0.0`; the Rule detail page prints the stored score unconditionally.

## Decisions

- [decision] Relationship-only reconciler (the bead's option A). A wider all-source sweeper would need cross-schema reads or a security-definer exception that RFC 0031 deliberately avoids. Reversible: yes, by removing the schedule entry.
- [decision] Add, correct and remove, all keyed to `relationship`/`entity_facts`. Removal is limited to edges whose Relationship source is absent or no longer an active entity-to-entity assertion, so historical rows of other sources are never retracted.
- [decision] Receipts are counts and a status only. A fixed status vocabulary and a bounded failure code avoid persisting source content or free-text errors.
- [decision] Retention of 90 days plus the latest receipt of each status keeps "last known failure" visible without unbounded growth. Figures are owner question Q1.
- [decision] Daily job-mode cadence with 500 rows by 20 batches, one short transaction per batch, and a non-blocking advisory lock on a dedicated connection. A skipped run retries on the next due run. Figures are owner question Q2.
- [decision] The ratio threshold is exactly `applied_count = 0`, with copy `No applications yet`. Any positive denominator keeps today's value. Owner question Q3.
- [decision] The ratio rule is a MODIFIED "Rule detail page" block copied whole from the baseline, changing only the Outcome-record bullet and the numeral scenario's precondition (`applied_count >= 1`), because the baseline requires a numeral and scoring returns 0.0 at zero applications; an ADDED refinement would contradict it after archive. All three baseline scenario names are kept and two scenarios are added. `make check-spec-overwrites` reports no loss, so no baseline is frozen.
- [decision] Receipts live in Relationship's own schema (`relationship`), are written only by the job and need no grant elsewhere; this keeps the change inside the no-cross-schema-access non-goal and lets the later read-only presentation go through Relationship's own API router. This is decided, not an owner question.
- [decision] Named fleet-case evidence is out of the Relationship surfaces and handed to bu-h40h2b.8 (REQ-entity-graph-018).

## Risks and trade-offs

- The reconciler is a second writer to the projection. Natural-key convergence and the Relationship-only filter keep it from creating a competing authority; the same-transaction rule in REQ-entity-graph-002 remains the primary mechanism and the reconciler only repairs drift.
- Batching bounds each transaction but means a very large drift needs several days to converge; `partial` receipts with `remaining` make that visible rather than hiding it.
- A zero-count `success` is the only state presented as in sync, so a quiet failure cannot look healthy.

## Rollback

Remove the schedule entry. Source facts, the last projection and existing receipts stay intact. Deployment performs no live backfill.
