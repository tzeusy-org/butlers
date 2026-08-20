# RFC 0026: Evidence-Backed Commitment Lifecycle

**Status:** Draft
**Author:** tze
**Date:** 2026-08-20

## Summary

Extends the owner condition ledger (RFC: `generalize-owner-condition-ledger`)
with explicit resolution semantics and a commitment metadata convention, enabling
the system to track unfinished owner obligations, promises, and expectations
with provenance, temporal awareness, and closure receipts.

## Motivation

The owner condition ledger provides durable, level-triggered lifecycle for
standing concerns that a deterministic producer can re-observe (an overdue bill,
a spending anomaly). Its single reconciliation path — `reconcile_snapshot()` —
assumes a producer that can authoritatively survey the world and resolve
conditions by omission.

Owner-declared commitments ("I told Sam I'd send him that book") have no
deterministic producer. No scheduled job can observe whether the owner followed
through. Resolution comes from:

- The owner confirming ("I sent it")
- Evidence from another system (email to Sam detected)
- Deadline expiry
- Explicit cancellation or supersession

The condition ledger's lifecycle (open → aging → resolved) and escalation
(L0–L3) remain correct for commitments. What's missing is a resolution path
that doesn't require a producer snapshot.

Links to doctrine:

- `about/heart-and-soul/vision.md:120–137`: "The measure is not feature
  count — it is the amount of mental labor the system reliably absorbs."
  Commitments are a category of mental labor the owner currently carries.
- JARVIS Run 08 headline: "Accepted is not completed." The system records
  facts but cannot prove outcomes closed.

## Design

### 1. Explicit Resolution Path

Add `resolve_condition()` to `butlers.core.condition_ledger`:

```python
async def resolve_condition(
    pool: asyncpg.Pool,
    *,
    table: str,
    source: str,
    fingerprint: str,
    resolution_metadata: dict[str, Any] | None = None,
) -> ConditionTransition | None:
```

Semantics:

- Transitions the active (open/aging) episode for `(source, fingerprint)` to
  `resolved`, recording `resolved_at`, `recovered_after_s`, and merging
  `resolution_metadata` into the row's existing `metadata` JSONB.
- Returns a `ConditionTransition` with `kind="resolved"`, or `None` if no
  active episode exists.
- Holds the same transaction-scoped advisory lock as `reconcile_snapshot()` —
  keyed by `hashtext(table || ':' || source)` — so concurrent
  `reconcile_snapshot` and `resolve_condition` calls for the same source are
  serialized.
- Refuses to resolve a condition that is already `resolved` (returns `None`).
- `resolution_metadata` is merged into the row's `metadata` column, not
  replaced, preserving the creation-time metadata.

This is deliberately minimal: one new function with the same concurrency
contract as the existing reconciler. The `owner_conditions.py` facade
re-exports it bound to `table="public.owner_conditions"`.

### 2. MCP Tool: `resolve_owner_condition`

New tool on `roster/switchboard/modules/owner_conditions_broker.py`:

```
resolve_owner_condition(
    source: str,
    fingerprint: str,
    resolution_reason: str,   # satisfied | cancelled | superseded | expired
    detail: str | None = None
) -> {"status": "resolved", "episode": int, ...}
     | {"status": "not_found"}
     | {"status": "error", "reason": "..."}
```

Constructs `resolution_metadata` from the arguments and delegates to
`owner_conditions.resolve_condition()`. Validates `resolution_reason` against
the enum before touching the pool.

### 3. Commitment Metadata Convention

Commitments are `owner_conditions` rows where `metadata->>'class' = 'commitment'`.
No schema change to the `owner_conditions` table — the `metadata` JSONB column
carries commitment-specific semantics:

```json
{
  "class": "commitment",
  "kind": "promise | waiting_for | follow_up | obligation | decision",
  "direction": "owner_to_other | other_to_owner | self",
  "counterparty_entity_id": "uuid | null",
  "confidence": 0.85,
  "deadline": "2026-08-25T00:00:00Z",
  "next_action_window": "2026-08-25T09:00:00+08:00",
  "evidence_opened": {
    "source": "conversation | email | calendar | explicit",
    "session_id": "uuid",
    "excerpt": "I'll send Sam that book tomorrow",
    "detected_at": "2026-08-20T14:30:00Z"
  },
  "evidence_closed": {
    "source": "owner_confirmed | evidence_observed | expired | cancelled | superseded",
    "session_id": "uuid | null",
    "detail": "Owner said: I sent it"
  },
  "resolution_reason": "satisfied | cancelled | superseded | expired"
}
```

A thin helper module `butlers.core.commitments` provides convenience functions
with validation:

- `create_commitment()` — validates metadata, computes fingerprint from stable
  identity facts (counterparty + action hash), delegates to
  `reconcile_snapshot(snapshot_complete=False)`.
- `resolve_commitment()` — validates resolution_reason, delegates to
  `resolve_condition()` with structured resolution_metadata.
- `list_active_commitments()` — queries `owner_conditions` filtering by
  `metadata->>'class' = 'commitment'` and `state IN ('open', 'aging')`.
- `list_entity_commitments(entity_id)` — queries by
  `metadata->>'counterparty_entity_id'`.

### 4. Fingerprint Identity

Commitment fingerprint is computed from stable identity facts that define
"same commitment" across re-observations:

```python
compute_fingerprint(
    source="relationship:commitment",
    version=1,
    identity_facts={
        "counterparty_entity_id": "sam-uuid",
        "action_hash": sha256("send-book-recommendation"),
    },
)
```

The `action_hash` is a normalized hash of the commitment's action description,
preventing duplicate commitments for the same action to the same person. The
`version` field supports future identity schema changes.

Mutable fields (deadline, confidence, summary text) are NOT part of the
fingerprint — they may change during the episode without creating a new
commitment.

### 5. Domain Ownership via Source Convention

Each butler owns its commitment semantics through the existing `source` naming
convention:

| Source | Butler | Example |
|---|---|---|
| `relationship:commitment` | Relationship | "Send Sam the book" |
| `health:follow-up` | Health | "Repeat blood test in 6 months" |
| `finance:obligation` | Finance | "Cancel subscription before renewal" |
| `general:commitment` | General | Catch-all for unroutable commitments |

A source's advisory lock scope prevents cross-category interference. A
`snapshot_complete=True` call for `"relationship:commitment"` only ever resolves
relationship commitment episodes, never a health follow-up.

### 6. Escalation Integration

Commitments use the condition ledger's existing escalation schedule:

- **L0** (grace period): commitment exists, no surfacing yet. Grace period
  defaults to 24h or until `next_action_window`, whichever is sooner.
- **L1** (1 day after L0): first surfacing via insight engine. Normal priority.
- **L2** (3 days after L1): elevated priority insight.
- **L3** (7 days after L2, repeats weekly): persistent, declining priority.

Deadline-bearing commitments adjust the schedule: if the deadline is within
the L0 grace period, L0 is shortened to surface before the deadline.

### 7. Insight Engine Integration

Commitment surfacing composes with the existing proactive insight engine
(RFC 0011). A new commitment escalation job:

1. Queries `owner_conditions` for commitment-class rows at L1+ escalation.
2. For each, proposes an insight candidate via `propose_insight_candidate()`.
3. The insight broker handles deduplication, budget, quiet hours, and delivery.

No changes to the insight engine itself. Commitments compete for the same
daily delivery budget as other insights.

### 8. Confidence and Creation Thresholds

- `confidence >= 0.8`: auto-created by domain butler session. Surfaced
  proactively at L1+.
- `0.6 <= confidence < 0.8`: created but never surfaced proactively. Available
  for prep card queries and dashboard.
- `confidence < 0.6`: not created. Too uncertain to warrant a durable record.

Confidence is an LLM judgment, set at creation time, immutable after creation.
The system does not attempt to re-assess confidence — if the commitment was
wrong, the owner cancels it.

### 9. Garbage Collection

Commitments at L3 for 90+ days without any re-confirmation propose an archival
insight: "This commitment has been open for 90 days with no activity. Cancel
or keep?" If cancelled, resolves with `resolution_reason: "cancelled"`. If
kept, resets escalation to L1 for another cycle.

## Integration

- **Condition ledger** (existing): lifecycle engine, concurrency, query surface.
- **Insight engine** (RFC 0011): delivery mechanism for surfacing.
- **Attention ledger** (existing): records all delivery attempts.
- **Entity system** (`public.entities`): counterparty anchoring.
- **Switchboard MCP** (existing): session-accessible tool surface.
- **Calendar module**: deadline alignment, event-triggered prep queries.
- **Domain-event bus** (RFC 0022): potential evidence triggers from cross-domain
  events (future scope).

## Alternatives Considered

### A. New `open_loops` table

Rejected. Creates a parallel lifecycle system alongside `owner_conditions` and
`infra_conditions`. The condition ledger already provides the right lifecycle.
Adding a third table with the same lifecycle but different field names is
architectural waste.

### B. Extend memory facts with lifecycle semantics

Rejected. Memory facts (`memory_store_fact`) are designed for narrative
knowledge (SPO triples with decay). Lifecycle management (escalation,
resolution, provenance receipts) is not their job. Forcing lifecycle into
memory creates a confusing dual-purpose system.

### C. Build commitments as a new module

Rejected. Modules only add tools to one butler. Commitments need cross-butler
coordination (any butler can create, Switchboard provides the MCP doorway,
insight engine surfaces). This is core infrastructure.

### D. Use the deadline system directly

Partial fit. Deadlines handle "something due by date X" well, but commitments
without deadlines ("send Sam that book whenever") have no deterministic due
date. The condition ledger's escalation provides time-based nudging without
requiring a deadline.

## V1 Scope

**In scope:**
- `resolve_condition()` in condition_ledger
- `resolve_owner_condition` MCP tool
- Commitment metadata convention and helper module
- Relationship Butler commitment extraction (explicit statements only)
- Commitment escalation job
- Dashboard commitment-class filter on conditions panel

**Out of scope (future):**
- Automatic inference from arbitrary conversation patterns
- Automatic closure from cross-domain evidence
- Multi-domain commitment extraction (Health, Finance) — pattern proven in
  Relationship first
- Commitment analytics and follow-through metrics
- Domain-event-triggered resolution
