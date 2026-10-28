# QA dispatch persistence

The infrastructure-condition gate in `butlers.core.qa.dispatch` preserves the adopted
[infrastructure reliability](../../openspec/specs/infrastructure-reliability/spec.md) and
[QA dispatch](../../openspec/specs/qa-investigation-dispatch/spec.md) decision-only contracts.
This note describes persistence; it does not change admission order or authorize investigation.

## Implementation Notes

An ordinary `infra_condition_open` result requires both the finding-reason write and the
condition-linked decision-event write to return successfully. Neither write is best effort.
If either raises, the existing dispatcher boundary reports `internal_error`, with no attempt
claim, model invocation, runtime session or worktree. It does not invent a retry queue.

The writes commit independently. A failed call can therefore leave partial evidence, and a lost
response can leave a committed event even though dispatch reported `internal_error`. Retrying the
same persisted finding uses a deterministic decision UUID derived from its finding ID. The event
writer reuses the original row and reason, checks its fingerprint/butler/decision/attempt binding,
and never changes it into an execution record. A separate finding still has a separate decision
identity. This preserves at most one decision per suppressed finding across retries/concurrency.

Existing callers that omit an event ID retain append-only event creation. The suppression metric
is emitted only after both persistence calls succeed; the error result does not advertise durable
suppression. PostgreSQL durability and concurrent retry are covered by the named roundtrip in
`tests/integration/test_infra_condition_suppression_persistence_roundtrip.py`; a collection result
or mocked writer is not proof that those database bodies executed.
