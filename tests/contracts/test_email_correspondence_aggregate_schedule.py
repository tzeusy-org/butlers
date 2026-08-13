"""Static RFC 0010 reuse contract for the RFC 0023 planning packet.

RFC 0023 is planning-only, so this test deliberately checks the binding packet
rather than a future migration, scheduler, or provider path.  It prevents the
Relationship aggregate exception from drifting into a general cross-butler
reader before implementation is authorized.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _packet_text(relative_path: str) -> str:
    path = _REPO_ROOT / relative_path
    assert path.is_file(), f"Planning packet artifact not found: {relative_path}"
    return path.read_text(encoding="utf-8")


def _assert_contains(relative_path: str, *required: str) -> None:
    text = " ".join(_packet_text(relative_path).casefold().split())
    missing = [phrase for phrase in required if " ".join(phrase.casefold().split()) not in text]
    assert not missing, f"{relative_path} is missing RFC 0010 guardrail(s): {missing}"


def test_rfc_0023_binds_all_rfc_0010_scheduled_reader_guardrails() -> None:
    """The RFC must constrain the exception, not just call the consumer deterministic."""
    _assert_contains(
        "about/legends-and-lore/rfcs/0023-messenger-private-email-correspondence-ledger.md",
        "RFC 0010 scheduled-reader guardrails",
        "database-enforced narrow reader",
        "email_correspondence_enrichment",
        "`35 6 * * *`",
        '`dispatch_mode="job"`',
        "`src/butlers/scheduled_jobs.py`",
        "`roster/relationship/butler.toml`",
        "zero-LLM",
        "MCP/API/on-demand/interactive",
        "up to 101 LLM sessions per daily batch",
        "migration-managed",
        "migrated PostgreSQL",
    )


def test_active_deltas_keep_the_aggregate_database_bounded_and_noninteractive() -> None:
    """The relevant OpenSpec deltas must preserve the RFC's implementation boundary."""
    _assert_contains(
        "openspec/changes/true-bidirectional-email-correspondence/specs/butler-relationship/spec.md",
        "RFC 0010",
        "email_correspondence_enrichment",
        '`dispatch_mode="job"`',
        "zero-LLM",
        "no MCP/API/on-demand/interactive consumer",
        "maximum 100",
        "up to 101 LLM sessions per daily batch",
    )
    _assert_contains(
        "openspec/changes/true-bidirectional-email-correspondence/specs/database-security/spec.md",
        "database-enforced narrow reader",
        "migration-managed",
        "no MCP/API/on-demand/interactive aggregate path",
    )
    _assert_contains(
        "openspec/changes/true-bidirectional-email-correspondence/specs/butler-messenger/spec.md",
        "no MCP/API/on-demand/interactive aggregate path",
    )


def test_tasks_and_implementation_plan_name_the_scheduler_wiring_and_regressions() -> None:
    """Future implementation must have an exact job surface and regression plan."""
    for relative_path in (
        "openspec/changes/true-bidirectional-email-correspondence/tasks.md",
        "openspec/changes/true-bidirectional-email-correspondence/implementation-plan.md",
    ):
        _assert_contains(
            relative_path,
            "email_correspondence_enrichment",
            "`src/butlers/scheduled_jobs.py`",
            "`roster/relationship/butler.toml`",
            "`35 6 * * *`",
            '`dispatch_mode="job"`',
            "no MCP/API/on-demand/interactive",
            "static packet contract",
            "migrated PostgreSQL",
        )


def test_proposal_and_design_keep_the_cost_case_bounded_and_planning_only() -> None:
    """The change records both the cost rationale and its non-execution boundary."""
    _assert_contains(
        "openspec/changes/true-bidirectional-email-correspondence/proposal.md",
        "RFC 0010",
        "email_correspondence_enrichment",
        "planning only",
    )
    _assert_contains(
        "openspec/changes/true-bidirectional-email-correspondence/design.md",
        "maximum 100",
        "up to 101 LLM sessions per daily batch",
        "zero-LLM",
        "no MCP/API/on-demand/interactive",
    )


def test_rfc_0023_requires_scheduler_admission_for_the_protected_job() -> None:
    """The fixed job cannot rely on absence of a bespoke aggregate tool alone."""
    _assert_contains(
        "about/legends-and-lore/rfcs/0023-messenger-private-email-correspondence-ledger.md",
        "scheduler-level protected-job registry",
        "email_correspondence_enrichment",
        "`schedule_trigger`",
        "`schedule_create`",
        "`schedule_update`",
        "before dispatch",
        "before persistence",
        "`source='toml'`",
        "auditable rejection",
        "metric",
    )


def test_active_packet_binds_protected_job_admission_at_the_scheduler_seam() -> None:
    """Future work must protect trigger, create, and update without blocking the TOML job."""
    _assert_contains(
        "openspec/changes/true-bidirectional-email-correspondence/specs/core-scheduler/spec.md",
        "protected-job registry",
        "email_correspondence_enrichment",
        "`schedule_trigger`",
        "`schedule_create`",
        "`schedule_update`",
        "before dispatch",
        "before persistence",
        "`source='toml'`",
        "fixed TOML schedule",
        "auditable rejection",
        "metric",
    )
    _assert_contains(
        "openspec/changes/true-bidirectional-email-correspondence/specs/butler-relationship/spec.md",
        "protected-job registry",
        "no generic interactive trigger/create/update path",
        "fixed TOML schedule",
    )
    _assert_contains(
        "openspec/changes/true-bidirectional-email-correspondence/specs/database-security/spec.md",
        "scheduler-level protected-job enforcement",
        "not application convention",
    )
    for relative_path in (
        "openspec/changes/true-bidirectional-email-correspondence/proposal.md",
        "openspec/changes/true-bidirectional-email-correspondence/design.md",
        "openspec/changes/true-bidirectional-email-correspondence/tasks.md",
        "openspec/changes/true-bidirectional-email-correspondence/implementation-plan.md",
    ):
        _assert_contains(
            relative_path,
            "protected-job registry",
            "email_correspondence_enrichment",
            "schedule_trigger",
            "schedule_create",
            "schedule_update",
        )
