## Why

Chronicles can presently collapse a real owned PostgreSQL read failure into an
empty source result or an anonymous non-content state. That lets a retrospective
briefing, archive boundary, cached prose, or source-badge strip look healthier
than the completed read supports.

This focused availability contract makes the owner-visible distinction without
turning expected cold-boot relation absence into an operational incident.

## What Changes

- Add a typed per-subquery availability ledger to the successful Chronicles
  briefing response. It names only the stable briefing concern that was read,
  never a raw database error or relation name.
- Classify a deliberately not-requested read and expected optional/cold-boot
  relation absence separately from a genuine owned query failure.
- Require a genuine owned read failure to produce a named unavailable entry,
  high source-error attention, a degraded or unavailable briefing state before
  cache selection, and deterministic rather than cached prose.
- Make the archive boundary explicitly unavailable when its coverage read
  cannot establish a trustworthy floor.
- Make the source-state badge strip expose its own failed request, preserve
  retained badges only as stale data, and provide an explicit retry control.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-chronicles`: define typed briefing subquery availability, truthful
  failure precedence, archive-boundary behavior, and accessible source-state
  retry presentation.

## Impact

- `src/butlers/chronicler/editorial.py`
- `roster/chronicler/api/models.py` and `roster/chronicler/api/router.py`
- Chronicles client types and hooks
- `SourceStateBadgeStrip` and `ChroniclesPage`
- Focused backend/API and frontend behavior tests

No migration, topology, authorization, privacy, cross-schema, or LLM behavior
changes are introduced.
