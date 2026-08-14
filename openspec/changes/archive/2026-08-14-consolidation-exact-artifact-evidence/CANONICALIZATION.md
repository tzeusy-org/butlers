# Canonicalization Record — Exact Consolidation Artifact Evidence

## Status

This completed change was merged as PR #3669 (`b4cc8d848a8a65ae21fafc19906eab171955f031`)
and archived on 2026-08-14 after its completed delta was selectively synced.
The canonical authority is the current `module-memory` main spec; this archive
preserves the historical proposal, design, delta, and task evidence only.

## Requirement-to-evidence mapping

| Landed delta requirement | Canonical destination | Behavior-executing evidence |
|---|---|---|
| `LLM-driven memory consolidation pipeline` | `openspec/specs/module-memory/spec.md` — `REQ-module-memory-005` | `tests/modules/memory/test_consolidation.py::test_consolidation_prompt_exposes_episode_ids_and_requires_artifact_evidence`; `tests/modules/memory/test_consolidation.py::test_parser_retains_per_artifact_episode_evidence`; `tests/modules/memory/test_consolidation_executor.py::test_invalid_artifact_evidence_stops_before_any_write` |
| `Consolidation executor with per-action error isolation` | `openspec/specs/module-memory/spec.md` — `REQ-module-memory-006` | `tests/modules/memory/test_consolidation_executor.py::test_each_artifact_links_only_its_validated_episode_evidence`; `tests/modules/memory/test_consolidation_executor.py::test_updated_fact_links_only_its_validated_episode_evidence`; `tests/modules/memory/test_consolidation_executor.py::test_failed_evidence_link_rolls_back_its_artifact`; `tests/modules/memory/test_memory_migration_integration.py::test_consolidation_rolls_back_artifact_when_evidence_link_fails` |

The synced requirements retain the landed bounded behavior: exact per-artifact
episode evidence is preflight-validated before a non-empty group persists, and
each artifact plus only its validated `derived_from` links commits atomically.
They do not authorize retention, backfill, migration, relationship writes, or
any new provider/runtime action.
