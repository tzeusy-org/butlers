"""Static RFC 0010 reuse contract for the RFC 0024 planning packet.

RFC 0024 is planning-only, so this test deliberately checks the binding packet
rather than a future migration, scheduler, or provider path.  It prevents the
Relationship aggregate exception from drifting into a general cross-butler
reader before implementation is authorized.
"""

from __future__ import annotations

import re
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


def test_private_email_rfc_uses_a_unique_indexed_number_and_coherent_packet_references() -> None:
    """The planning packet must not reuse another canonical RFC's number."""
    rfc_dir = _REPO_ROOT / "about/legends-and-lore/rfcs"
    numbered_rfcs = sorted(rfc_dir.glob("[0-9][0-9][0-9][0-9]-*.md"))
    numbers = [path.name[:4] for path in numbered_rfcs]
    assert len(numbers) == len(set(numbers)), f"duplicate RFC numbers: {numbers}"

    rfc_path = rfc_dir / "0024-messenger-private-email-correspondence-ledger.md"
    assert rfc_path.is_file(), "private-email RFC must use the next unallocated number"
    assert rfc_path.read_text(encoding="utf-8").startswith(
        "# RFC 0024: Messenger-Private Email Correspondence Ledger"
    )

    index = _packet_text("about/legends-and-lore/README.md")
    index_entries = re.findall(
        r"^\| \[(\d{4})\]\(rfcs/(\d{4})-[^)]+\) \|",
        index,
        flags=re.MULTILINE,
    )
    assert [index_number for index_number, _ in index_entries] == numbers
    assert all(index_number == path_number for index_number, path_number in index_entries)
    assert (
        "[0024](rfcs/0024-messenger-private-email-correspondence-ledger.md)"
        " | Messenger-Private Email Correspondence Ledger |" in index
    )

    for relative_path in (
        "openspec/changes/true-bidirectional-email-correspondence/proposal.md",
        "openspec/changes/true-bidirectional-email-correspondence/tasks.md",
    ):
        text = _packet_text(relative_path)
        assert "RFC 0024" in text, f"{relative_path} must name the private-email RFC"
        assert "RFC 0023" not in text, f"{relative_path} must not name the approval-delivery RFC"


def test_rfc_0024_binds_all_rfc_0010_scheduled_reader_guardrails() -> None:
    """The RFC must constrain the exception, not just call the consumer deterministic."""
    _assert_contains(
        "about/legends-and-lore/rfcs/0024-messenger-private-email-correspondence-ledger.md",
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


def test_trimmed_packet_keeps_the_aggregate_read_only_and_non_negative() -> None:
    """The trimmed change keeps one read-only view, a deterministic job, and no `false`."""
    change = "openspec/changes/true-bidirectional-email-correspondence"
    _assert_contains(
        f"{change}/specs/database-security/spec.md",
        "RFC 0010",
        "`SELECT` on `messenger.v_confirmed_email_outbound`",
    )
    _assert_contains(
        f"{change}/specs/butler-relationship/spec.md",
        "email_correspondence_enrichment",
        '`dispatch_mode="job"`',
        "zero-LLM",
        "at most 100",
        "never `false`",
    )
    _assert_contains(
        f"{change}/design.md",
        "## Deferred",
        "Scheduler protected-job registry",
    )


def test_rfc_0024_requires_scheduler_admission_for_the_protected_job() -> None:
    """The fixed job cannot rely on absence of a bespoke aggregate tool alone."""
    _assert_contains(
        "about/legends-and-lore/rfcs/0024-messenger-private-email-correspondence-ledger.md",
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
