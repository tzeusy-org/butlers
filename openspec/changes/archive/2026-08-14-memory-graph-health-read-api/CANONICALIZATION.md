# Canonicalization Record — Memory Graph-Health Read API

## Status

This completed change was merged as PR #3734 (`560e3492d0ceaf6ee162a386ef1d1c1edaa17e30`)
and archived on 2026-08-14 after its completed deltas were selectively synced.
The canonical authority is the current main-spec set below; this archive retains
the historical proposal, design, delta, and task evidence only.

## Requirement-to-evidence mapping

| Landed delta requirement | Canonical destination | Behavior-executing evidence |
|---|---|---|
| `Memory stats expose typed graph-health coverage` | `openspec/specs/dashboard-api/spec.md` — `REQ-dashboard-api-053` | `tests/api/test_memory.py::test_stats_graph_health_marks_partial_memory_schema_as_unknown`; `tests/api/test_memory.py::test_stats_graph_health_is_unknown_without_memory_pool_evidence`; `tests/contracts/test_degraded_envelope.py` |
| `Memory Overture renders graph-health coverage honestly` | `openspec/specs/dashboard-domain-pages/spec.md` — `REQ-dashboard-domain-pages-048` | `frontend/src/components/memory/MemoryOverture.test.tsx` — `renders complete graph-health coverage without calling the graph healthy`; `renders unknown graph-health coverage when no memory pool returns evidence` |
| `Read-only memory-pool graph-health coverage` and `Graph-health cleanup-lag population is exact` | `openspec/specs/memory-graph-health/spec.md` — `REQ-memory-graph-health-001` and `REQ-memory-graph-health-002` | `tests/api/test_memory.py::test_stats_graph_health_is_unknown_when_only_partial_memory_schema_fails`; `tests/api/test_memory.py::test_stats_graph_health_reuses_the_consolidation_aware_retention_population` |
| `Graph-health coverage reuses the consolidation-aware cleanup population` | `openspec/specs/memory-retention-policy/spec.md` — `REQ-memory-retention-policy-009` | `tests/api/test_memory.py::test_stats_graph_health_reuses_the_consolidation_aware_retention_population` |

The synced behavior is additive and observational: it preserves established
retention fields, classifies unavailable or empty evidence fail-closed, and
adds neither a write, job trigger, repair control, retention action, migration,
nor relationship entity-fact operation.
