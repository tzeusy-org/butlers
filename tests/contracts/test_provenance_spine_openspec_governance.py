"""Static governance regressions for the landed provenance-spine slices.

Spec: REQ-module-memory-005
Spec: REQ-module-memory-006
Spec: REQ-module-memory-012
Spec: REQ-dashboard-api-053
Spec: REQ-dashboard-domain-pages-048
Spec: REQ-memory-graph-health-001
Spec: REQ-memory-graph-health-002
Spec: REQ-memory-retention-policy-009
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_ROOT = Path(__file__).resolve().parents[2]
_CHANGES = _ROOT / "openspec" / "changes"
_ARCHIVE = _CHANGES / "archive"
_SPECS = _ROOT / "openspec" / "specs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_completed_provenance_changes_are_archived_with_canonical_test_mappings() -> None:
    """Completed #3669/#3734 deltas retain a verifiable canonical handoff."""
    expected_archives = {
        "2026-08-14-consolidation-exact-artifact-evidence": {
            "LLM-driven memory consolidation pipeline": _SPECS / "module-memory" / "spec.md",
            "Consolidation executor with per-action error isolation": _SPECS
            / "module-memory"
            / "spec.md",
            "test_invalid_artifact_evidence_stops_before_any_write": _ROOT
            / "tests/modules/memory/test_consolidation_executor.py",
            "test_failed_evidence_link_rolls_back_its_artifact": _ROOT
            / "tests/modules/memory/test_consolidation_executor.py",
        },
        "2026-08-14-memory-graph-health-read-api": {
            "Memory stats expose typed graph-health coverage": _SPECS / "dashboard-api" / "spec.md",
            "Memory Overture renders graph-health coverage honestly": _SPECS
            / "dashboard-domain-pages"
            / "spec.md",
            "Read-only memory-pool graph-health coverage": _SPECS
            / "memory-graph-health"
            / "spec.md",
            "Graph-health cleanup-lag population is exact": _SPECS
            / "memory-graph-health"
            / "spec.md",
            "Graph-health coverage reuses the consolidation-aware cleanup population": _SPECS
            / "memory-retention-policy"
            / "spec.md",
            "test_stats_graph_health_is_unknown_without_memory_pool_evidence": _ROOT
            / "tests/api/test_memory.py",
            "renders unknown graph-health coverage when no memory pool returns evidence": _ROOT
            / "frontend/src/components/memory/MemoryOverture.test.tsx",
        },
    }

    for archive_name, evidence in expected_archives.items():
        assert not (_CHANGES / archive_name.removeprefix("2026-08-14-")).exists()
        mapping = _ARCHIVE / archive_name / "CANONICALIZATION.md"
        mapping_text = _read(mapping)
        for text, destination in evidence.items():
            assert text in mapping_text
            assert text in _read(destination)


def test_req_module_memory_006_preserves_pr_3669_action_isolation_contract() -> None:
    """Canonical REQ-006 keeps valid work eligible after one action fails."""
    module_memory = _read(_SPECS / "module-memory" / "spec.md")
    scenario = re.search(
        r"#### Scenario: Individual action failures do not block others\n(?P<body>.*?)(?=\n####|\Z)",
        module_memory,
        flags=re.DOTALL,
    )

    assert scenario is not None
    action_isolation = scenario.group("body")
    assert "- **WHEN** storing one valid new fact fails with an exception" in action_isolation
    assert "- **THEN** the error MUST be logged and added to the `errors` list" in action_isolation
    assert "- **AND** subsequent valid actions MUST still be attempted" in action_isolation


def test_b5_b6_have_narrow_observed_authority_and_carrier_is_archived() -> None:
    """#3728 is canonicalized alone; the carrier archived once Tracks A-E were verified."""
    module_memory = _read(_SPECS / "module-memory" / "spec.md")
    normalized_module_memory = " ".join(module_memory.split())
    assert not (_CHANGES / "relational-edges-single-home").exists()
    (carrier,) = _ARCHIVE.glob("*-relational-edges-single-home")
    transfer = _read(carrier / "landed-b5-b6-transfer.md")
    carrier_tasks = _read(carrier / "tasks.md")
    carrier_delta = _read(carrier / "specs" / "module-memory" / "spec.md")

    assert (
        "### Requirement: Consolidation narrative edges use an exact local allowlist"
        in module_memory
    )
    expected_allowlist = (
        "planned_dinner_with",
        "wake_coordination",
        "social_exchange_with",
    )
    allowlist_match = re.search(
        r"versioned local v1 allowlist:\s*`([^`]+)`,\s*`([^`]+)`,\s*and\s*`([^`]+)`",
        module_memory,
    )
    assert allowlist_match is not None
    assert allowlist_match.groups() == expected_allowlist
    for boundary in (
        "storage boundary",
        "unavailable or missing classification",
        "relationship.entity_predicate_registry",
        "relationship.entity_facts",
        "generic `memory_store_fact()` admission behavior",
        "rather than silently downgrading it to a property fact",
    ):
        assert boundary in normalized_module_memory

    assert "PR #3728" in transfer
    assert "Consolidation narrative edges use an exact local allowlist" not in carrier_delta
    assert re.search(r"^- \[ \]", carrier_tasks, flags=re.MULTILINE) is None
