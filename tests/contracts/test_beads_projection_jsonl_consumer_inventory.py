"""Static contracts for the planning-only Beads projection retirement boundary.

RFC 0007 Amendment 2 ships an allowlisted JSONL-backed Bead detail route.
The Decisions-only projection packet must not silently make that distinct
consumer eligible for JSONL retirement.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from butlers.migrations import get_chain_head

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _requirement_preamble(text: str, title: str) -> str:
    """Return one requirement's prose through its first scenario heading."""
    match = re.search(rf"^### Requirement: {re.escape(title)}$", text, re.MULTILINE)
    assert match is not None, f"missing requirement: {title}"

    next_requirement = text.find("\n### Requirement:", match.end())
    requirement = text[match.end() : next_requirement if next_requirement != -1 else None]
    first_scenario = requirement.find("\n#### Scenario:")
    assert first_scenario != -1, f"{title}: no scenario"
    return requirement[:first_scenario].rstrip()


def _assert_contiguous_traceability(
    text: str,
    *,
    title: str,
    requirement_id: str,
    source: str,
) -> None:
    """Require ID/Source/Scope directly between normative prose and scenarios."""
    expected = f"ID: {requirement_id}\nSource: {source}\nScope: v1-mandatory"
    assert _requirement_preamble(text, title).endswith(expected), (
        f"{title}: require contiguous ID, Source, Scope metadata before scenarios"
    )


def test_projection_plan_allocates_its_core_revision_from_the_exact_rebased_head() -> None:
    """The planning packet cannot reserve a migration revision before implementation."""
    implementation_plan = _read("docs/superpowers/plans/2026-08-13-beads-projection-exporter.md")
    tasks = _read("openspec/changes/beads-projection-exporter/tasks.md")
    core_head = get_chain_head("core")
    match = re.fullmatch(r"core_(\d+)", core_head)
    assert match is not None, f"unexpected core-chain head: {core_head}"
    next_revision = f"core_{int(match.group(1)) + 1}"

    for text in map(_normalise, (implementation_plan, tasks)):
        assert "exact rebased core-chain head at implementation time" in text
        assert "allocate the next core Alembic revision" in text

    assert not re.search(r"core_\d+_beads_projection\.py", implementation_plan)
    assert f"{core_head}_beads_projection.py" not in implementation_plan
    assert f"{next_revision}_beads_projection.py" not in implementation_plan
    assert f"after `{core_head}`" not in tasks


def test_tracker_projection_rfc_identity_is_unique_and_indexed() -> None:
    """The tracker-exporter packet owns one unique, correctly indexed RFC."""
    rfc_number = "0025"
    rfc_filename = "0025-tracker-host-beads-projection-exporter.md"
    rfc_title = "Tracker-Host Beads Projection Exporter"
    rfc_directory = _REPO_ROOT / "about/legends-and-lore/rfcs"

    numbered_rfcs = sorted(rfc_directory.glob("[0-9][0-9][0-9][0-9]-*.md"))
    rfc_numbers = [path.name[:4] for path in numbered_rfcs]
    assert len(rfc_numbers) == len(set(rfc_numbers))
    assert (rfc_directory / "0023-durable-approval-delivery-intent-recovery.md").is_file()
    assert (rfc_directory / "0024-messenger-private-email-correspondence-ledger.md").is_file()

    tracker_exporter_rfcs = sorted(
        rfc_directory.glob("*-tracker-host-beads-projection-exporter.md")
    )
    expected_rfc = rfc_directory / rfc_filename
    assert tracker_exporter_rfcs == [expected_rfc]
    assert sorted(rfc_directory.glob(f"{rfc_number}-*.md")) == [expected_rfc]

    rfc = expected_rfc.read_text(encoding="utf-8")
    assert re.search(rf"^# RFC {rfc_number}: {re.escape(rfc_title)}$", rfc, re.MULTILINE)
    assert "RFC 0024" not in rfc

    index_rows = re.findall(
        r"^\| \[(\d{4})\]\(rfcs/([^)]*)\) \| ([^|]+) \|",
        _read("about/legends-and-lore/README.md"),
        re.MULTILINE,
    )
    assert [row for row in index_rows if row[0] == rfc_number] == [
        (rfc_number, rfc_filename, rfc_title)
    ]


def test_current_bead_detail_is_a_distinct_jsonl_consumer() -> None:
    """REQ-beads-projection-005: retain detail until it has its own safe migration."""
    route = _read("src/butlers/api/routers/beads.py")
    reader = _read("src/butlers/beads_snapshot.py")
    detail_contract = _read("docs/frontend/backend-api-contract.md")
    projection_spec = _read(
        "openspec/changes/beads-projection-exporter/specs/beads-projection/spec.md"
    )

    assert "BeadSnapshotReader" in route
    assert "return BeadSnapshotReader()" in route
    assert "design=record.design" in reader
    assert "acceptance_criteria=record.acceptance_criteria" in reader
    assert "`GET /api/beads/{id}`" in detail_contract
    assert "`design`" in detail_contract
    assert "`acceptance_criteria`" in detail_contract
    assert "source description only for an eligible non-epic" in projection_spec


def test_retirement_packet_requires_a_complete_jsonl_consumer_inventory() -> None:
    """REQ-beads-projection-005: every JSONL consumer has a proven disposition."""
    required_contract = (
        "complete JSONL consumer inventory",
        "migrated with contract and regression proof",
        "explicitly retained",
        "separately scoped security review",
    )
    planning_artifacts = (
        "about/legends-and-lore/rfcs/0025-tracker-host-beads-projection-exporter.md",
        "docs/architecture/beads-runtime-data-bridge.md",
        "docs/superpowers/plans/2026-08-13-beads-projection-exporter.md",
        "openspec/changes/beads-projection-exporter/design.md",
        "openspec/changes/beads-projection-exporter/specs/beads-projection/spec.md",
        "openspec/changes/beads-projection-exporter/tasks.md",
    )

    for relative_path in planning_artifacts:
        text = " ".join(_read(relative_path).split())
        assert "`GET /api/beads/{id}`" in text, relative_path
        assert "BeadSnapshotReader" in text, relative_path
        for required_text in required_contract:
            assert required_text in text, f"{relative_path}: {required_text}"


def test_projection_requirement_traceability_is_contiguous() -> None:
    """REQ-beads-projection-001 through -006 remain mechanically traceable."""
    projection_spec = _read(
        "openspec/changes/beads-projection-exporter/specs/beads-projection/spec.md"
    )
    requirements = (
        (
            "Tracker-Host Export Boundary and Minimal Active Projection",
            "REQ-beads-projection-001",
            "RFC 0025 §§1-3, 10",
        ),
        (
            "Atomic Complete Snapshot Publication and Retention",
            "REQ-beads-projection-002",
            "RFC 0025 §§3-4",
        ),
        (
            "Bounded Atomic BeadReadProvider and Freshness Classification",
            "REQ-beads-projection-003",
            "RFC 0025 §§3, 5-6",
        ),
        (
            "Preserved Decision Lint and Dependency Semantics",
            "REQ-beads-projection-004",
            "RFC 0025 §7",
        ),
        (
            "Shadow Parity, Explicit Cutover, and Explicit JSONL Rollback",
            "REQ-beads-projection-006",
            "RFC 0025 §8",
        ),
        (
            "JSONL Consumer Inventory Gates Retirement",
            "REQ-beads-projection-005",
            "RFC 0025 §9; RFC 0007 Amendment 2",
        ),
    )

    for title, requirement_id, source in requirements:
        _assert_contiguous_traceability(
            projection_spec,
            title=title,
            requirement_id=requirement_id,
            source=source,
        )


def test_modified_dashboard_requirements_have_contiguous_traceability() -> None:
    """REQ-dashboard-api-001 and REQ-dashboard-decisions-001 remain traceable."""
    _assert_contiguous_traceability(
        _read("openspec/changes/beads-projection-exporter/specs/dashboard-api/spec.md"),
        title="Decisions Digest Endpoint",
        requirement_id="REQ-dashboard-api-001",
        source="RFC 0025 §§3, 5-8; RFC 0007",
    )
    _assert_contiguous_traceability(
        _read("openspec/changes/beads-projection-exporter/specs/dashboard-decisions/spec.md"),
        title="Export As-Of Plaque",
        requirement_id="REQ-dashboard-decisions-001",
        source="RFC 0025 §§3, 5-8; RFC 0007",
    )


def test_projection_contract_keeps_tracker_authority_and_derived_jsonl_role() -> None:
    """REQ-beads-projection-001: only Beads/Dolt is tracker authority."""
    rfc = _read("about/legends-and-lore/rfcs/0025-tracker-host-beads-projection-exporter.md")
    projection_spec = _read(
        "openspec/changes/beads-projection-exporter/specs/beads-projection/spec.md"
    )
    proposal = _read("openspec/changes/beads-projection-exporter/proposal.md")

    required = (
        "sole authoritative tracker",
        "derived compatibility and rollback path",
    )
    for text in map(_normalise, (rfc, projection_spec, proposal)):
        for phrase in required:
            assert phrase in text


def test_projection_tls_policy_and_bounds_fail_closed_in_the_plan() -> None:
    """REQ-beads-projection-001: verified transport and bounded candidates are planned."""
    rfc = _read("about/legends-and-lore/rfcs/0025-tracker-host-beads-projection-exporter.md")
    projection_spec = _read(
        "openspec/changes/beads-projection-exporter/specs/beads-projection/spec.md"
    )
    tasks = _read("openspec/changes/beads-projection-exporter/tasks.md")

    bounds = (
        "MAX_BEAD_ID_CHARS = 128",
        "MAX_ISSUE_TITLE_CHARS = 512",
        "MAX_STATUS_CHARS = 16",
        "MAX_ISSUE_TYPE_CHARS = 64",
        "MAX_TIMESTAMP_CHARS = 64",
        "MAX_LABELS_PER_ISSUE = 32",
        "MAX_LABEL_CHARS = 128",
        "MAX_DECISION_DESCRIPTION_CHARS = 16_384",
        "MAX_OPTIONS_PER_DECISION = 16",
        "MAX_DECISION_OPTION_CHARS = 512",
        "MAX_DEPENDENCY_TYPE_CHARS = 64",
        "MAX_LINT_CATEGORY_CODE_CHARS = 64",
        "MAX_CATEGORICAL_REASON_CHARS = 128",
        "MAX_PRODUCER_VERSION_CHARS = 64",
        "MAX_SNAPSHOT_ISSUES = 10_000",
        "MAX_SNAPSHOT_DEPENDENCY_EDGES = 25_000",
        "MAX_SNAPSHOT_LINT_VIOLATIONS = 1_000",
    )
    for text in map(_normalise, (rfc, projection_spec)):
        for bound in bounds:
            assert bound in text
        assert "reject the entire candidate snapshot" in text
        assert "field_bound_exceeded" in text
        assert "active pointer unchanged" in text

    tls_requirements = (
        "sslmode=verify-full",
        "trusted CA bundle",
        "hostname verification",
        "TLS 1.2 or newer",
        "fails closed",
    )
    for text in map(_normalise, (rfc, projection_spec, tasks)):
        for requirement in tls_requirements:
            assert requirement in text

    assert "at-limit acceptance and bound-plus-one rejection" in tasks
    assert "migrated-PostgreSQL integration tests" in tasks
    assert "planning regression tests for each rejection" in tasks
    assert "unverified TLS mode" in tasks


def test_suspicious_empty_or_regressed_candidate_requires_source_completeness_evidence() -> None:
    """REQ-beads-projection-001: an unproven smaller source cannot create an all-clear."""
    rfc = _read("about/legends-and-lore/rfcs/0025-tracker-host-beads-projection-exporter.md")
    projection_spec = _read(
        "openspec/changes/beads-projection-exporter/specs/beads-projection/spec.md"
    )
    architecture = _read("docs/architecture/beads-runtime-data-bridge.md")
    design = _read("openspec/changes/beads-projection-exporter/design.md")
    tasks = _read("openspec/changes/beads-projection-exporter/tasks.md")
    implementation_plan = _read("docs/superpowers/plans/2026-08-13-beads-projection-exporter.md")
    dashboard_api_spec = _read(
        "openspec/changes/beads-projection-exporter/specs/dashboard-api/spec.md"
    )
    dashboard_decisions_spec = _read(
        "openspec/changes/beads-projection-exporter/specs/dashboard-decisions/spec.md"
    )

    for text in map(_normalise, (rfc, projection_spec)):
        for requirement in (
            "same source watermark",
            "authoritative active count",
            "candidate active count",
            "source_completeness_unverified",
            "active pointer unchanged",
            "staged candidate itself",
            "availability override",
            "only a later source-complete publication clears it",
        ):
            assert requirement in text

    for text in map(_normalise, (architecture, design, tasks, implementation_plan)):
        assert "source_completeness_unverified" in text
        assert "availability override" in text
        assert "regression" in text

    dashboard_contract = _normalise(dashboard_api_spec)
    assert "source_completeness_unverified" in dashboard_contract
    assert "decisions_available: false" in dashboard_contract
    assert "source_completeness_unverified" in _normalise(dashboard_decisions_spec)
