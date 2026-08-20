## Why

The owner condition ledger (`generalize-owner-condition-ledger`) provides
durable level-triggered lifecycle for standing concerns a deterministic
producer can re-observe. Its single reconciliation path —
`reconcile_snapshot()` — assumes a producer that can authoritatively survey
the world and resolve conditions by omission.

Owner-declared commitments ("I told Sam I'd send that book") have no
deterministic producer that can verify fulfillment. Resolution comes from
the owner confirming, evidence from another system (e.g. email to Sam
detected), deadline expiry, or explicit cancellation. The condition ledger's
lifecycle and escalation remain correct; what's missing is a resolution path
that doesn't require a producer snapshot.

Today, unfinished owner business lives as unstructured memory facts
(relationship predicates like `reminder` or `contact_task`) with no lifecycle,
escalation, surfacing, or closure receipt. The owner carries the mental load
of tracking whether they followed through.

## What Changes

### New Capabilities

- `commitment-lifecycle`: the evidence-backed lifecycle for owner-declared
  commitments, implemented as a commitment-class convention on
  `owner_conditions` rows with an explicit resolution path on the condition
  ledger engine.

### Modified Capabilities

- `owner-condition-ledger`: adds `resolve_condition()` for explicit resolution
  without snapshot reconciliation, and a `resolve_owner_condition` MCP tool on
  Switchboard. Additive; no change to existing `reconcile_snapshot` behavior.

## In Scope

- `resolve_condition()` on condition_ledger (explicit resolution path)
- `resolve_owner_condition` MCP tool on Switchboard
- Commitment metadata convention (`metadata->>'class' = 'commitment'`)
- `butlers.core.commitments` helper module (create, resolve, list, query)
- Relationship Butler commitment extraction (explicit first-person statements
  with confidence >= 0.8)
- Commitment escalation job composing with insight engine
- Dashboard commitment-class filter on conditions panel

## Out of Scope

- Automatic commitment inference from arbitrary conversation patterns (start
  with explicit owner statements)
- Automatic closure from cross-domain evidence (start with owner-confirmed
  resolution)
- Multi-domain commitment extraction beyond Relationship (prove the pattern
  in one domain first: Health follow-ups, Finance obligations follow later)
- Commitment analytics and follow-through metrics
- Domain-event-triggered resolution
- Moment Prep integration (separate change — consumes commitment query surface)

## Impact

- `src/butlers/core/condition_ledger.py` (extended: `resolve_condition()`)
- `src/butlers/core/owner_conditions.py` (re-export)
- `src/butlers/core/commitments.py` (new: helper module)
- `roster/switchboard/modules/owner_conditions_broker.py` (extended:
  `resolve_owner_condition` tool)
- `roster/switchboard/` signal extraction skills (extended: commitment pattern)
- `roster/relationship/.claude/skills/` (extended: commitment detection,
  resolution)
- `src/butlers/jobs/commitment_escalation.py` (new)
- `frontend/src/components/system/StandingConditionsTile.tsx` (extended:
  commitment-class rendering)
- Tests: condition_ledger resolution tests, commitment helper tests,
  MCP tool tests, escalation job tests, signal extraction tests

## Design

See RFC 0026 (`about/legends-and-lore/rfcs/0026-commitment-lifecycle.md`).
