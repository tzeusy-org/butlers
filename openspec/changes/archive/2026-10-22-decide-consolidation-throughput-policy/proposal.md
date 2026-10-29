# Decide consolidation throughput policy

## Why

bu-de1hrl requires four explicit policy dispositions backed by current dev backlog and per-session token evidence. Existing batching is already shipped, and scheduled consolidation must preserve catalog-backed Spawner authority.

## What Changes

- Record deliberate present declines for additional batching, a new consolidation-specific per-run cap, backlog deadman automation and a forced cheap-tier transport.
- Add the four dispositions and their reconsideration boundary to REQ-module-memory-005 while preserving its complete existing requirement and scenarios.
- Publish sanitized aggregate evidence and its exact limitations in docs/decisions/consolidation-throughput-2026-10-15.md.
- Propose no runtime, scheduler, batching, model/catalog, quota, cleanup, migration or deployment change.

## Impact

Affected spec: module-memory, REQ-module-memory-005. Runtime code: none. No controls are adopted, so no implementation issues are required because all four declines were accepted for publication. Original issue completion still requires actual strict validation and related-parent note publication.
