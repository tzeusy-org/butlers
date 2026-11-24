## Why

A tripped QA circuit breaker cost one claim, one delete and one dispatch event per finding on every
patrol (about 17 each), and the concurrency-cap path told the patrol it had queued a retry that it
never wrote for the first capped finding. An open breaker is a property of the patrol, not of a
finding, so it should be decided once, and every finding it holds should survive for a later
patrol (bu-ejow5p.3, slice S1 of Breaker v2).

## What changes

- `dispatch_novel_findings` reads the breaker before each launch. When it is open, every remaining
  finding of the patrol is stamped `circuit_breaker`, queued in one write, and one dispatch event is
  recorded. No attempt is claimed or deleted. A breaker that trips mid-patrol holds the findings
  after the trip the same way.
- The per-finding breaker gate inside `dispatch_qa_investigation` is removed: the batch dispatcher
  is its only caller and owns the decision.
- The concurrency cap queues every leftover finding in one write, including the first one over the
  cap.
- A failed hold is counted as the `hold_failed` dispatch decision and is not reported as held.

## Deferred

- The `halted` patrol status and its migration (S2), cause fields and the half-open probe (S3).
- Hoisting the no-model gate to batch level.
