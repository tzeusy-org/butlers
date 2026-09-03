"""Executable planning contract for Relationship stale-contact source authority.

Spec: REQ-butler-relationship-001 and REQ-dashboard-domain-pages-049.

This prerequisite consumes the merged RFC 0029 contract without implementing Relationship
adoption. The tests execute the design's machine-readable producer and endpoint-liveness matrix,
and bind its passive channel inventory to the current Relationship writer. Continued
``bu-8cdl1.3`` adoption must replace this planning harness with migrated-PostgreSQL integration
coverage in a new PR.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DESIGN = (
    _REPO_ROOT
    / "openspec/changes/archive/2026-09-03-relationship-stale-contact-producer-mapping/design.md"
)
_START = "<!-- relationship-stale-contact-producer-map:start -->"
_END = "<!-- relationship-stale-contact-producer-map:end -->"


def _producer_rows() -> list[dict[str, Any]]:
    text = _DESIGN.read_text(encoding="utf-8")
    block = text.split(_START, 1)[1].split(_END, 1)[0]
    match = re.search(r"```json\s*(\[.*\])\s*```", block, flags=re.DOTALL)
    assert match is not None, "design must carry the executable producer-map JSON block"
    rows = json.loads(match.group(1))
    assert isinstance(rows, list)
    return rows


@dataclass(frozen=True)
class _Decision:
    state: str
    candidate_allowed: bool
    priority: int | None = None


def _evaluate_contract(
    rows: list[dict[str, Any]],
    *,
    evidence_state: str,
    days_since: float,
    cadence_days: int,
) -> _Decision:
    """Execute the target one-producer gate without implementing downstream adoption."""
    producers = {row["producer"] for row in rows if row.get("producer")}
    if any(row["mapping"] != "mapped" for row in rows) or len(producers) != 1:
        return _Decision("unmeasurable", False)
    if evidence_state != "healthy_current":
        return _Decision("unmeasurable", False)
    if days_since <= cadence_days:
        return _Decision("present", False)
    priority = 45 if days_since > cadence_days * 2 else 35
    return _Decision("absent", True, priority)


def test_current_passive_writer_channels_have_exactly_one_mapped_producer() -> None:
    """Current automatic message writers cannot drift outside the reviewed map."""
    from butlers.jobs._roster.relationship_jobs import _INTERACTION_SYNC_CHANNEL_MAP

    mapped = {
        row["input_channel"]: row["producer"]
        for row in _producer_rows()
        if row["mapping"] == "mapped" and row["writer"] == "interaction_sync"
    }

    assert mapped == {
        "email": "connector:gmail",
        "telegram_user_client": "connector:telegram_user_client",
        "whatsapp_user_client": "connector:whatsapp_user_client",
    }
    assert set(_INTERACTION_SYNC_CHANNEL_MAP) == set(mapped)

    connector_rows = [
        row
        for row in _producer_rows()
        if row["mapping"] == "mapped" and row["kill_mode"] == "heartbeat"
    ]
    assert connector_rows
    assert all(
        row["endpoint_identity"] == "server-derived source_endpoint_identity"
        for row in connector_rows
    )
    owner_row = next(row for row in _producer_rows() if row["producer"] == "owner")
    assert owner_row["endpoint_identity"] is None


@pytest.mark.parametrize(
    "evidence_state",
    ["stale", "dead_or_offline", "unhealthy", "missing", "unreadable"],
)
def test_killing_each_mapped_connector_after_cadence_is_unmeasurable_without_candidate(
    evidence_state: str,
) -> None:
    """Every mapped connector fails closed; no healthy sibling can substitute."""
    connector_rows = [
        row
        for row in _producer_rows()
        if row["mapping"] == "mapped" and row["kill_mode"] == "heartbeat"
    ]
    assert connector_rows

    for row in connector_rows:
        decision = _evaluate_contract(
            [row],
            evidence_state=evidence_state,
            days_since=60,
            cadence_days=14,
        )
        assert decision == _Decision("unmeasurable", False), row["source_id"]


def _endpoint_is_measurable(
    *,
    producer: str,
    endpoint_identity: str,
    liveness_rows: list[dict[str, str]],
) -> bool:
    connector_type = producer.removeprefix("connector:")
    return any(
        row["connector_type"] == connector_type
        and row["endpoint_identity"] == endpoint_identity
        and row["state"] == "healthy_current"
        for row in liveness_rows
    )


def test_healthy_sibling_endpoint_cannot_substitute_for_dead_attested_endpoint() -> None:
    """Liveness authority is the exact connector-type/endpoint pair in either row order."""
    connector_rows = [
        row
        for row in _producer_rows()
        if row["mapping"] == "mapped" and row["kill_mode"] == "heartbeat"
    ]

    for row in connector_rows:
        connector_type = row["producer"].removeprefix("connector:")
        attested_endpoint = f"{connector_type}:account-a"
        liveness_rows = [
            {
                "connector_type": connector_type,
                "endpoint_identity": attested_endpoint,
                "state": "dead_or_offline",
            },
            {
                "connector_type": connector_type,
                "endpoint_identity": f"{connector_type}:account-b",
                "state": "healthy_current",
            },
        ]
        for ordered_rows in (liveness_rows, list(reversed(liveness_rows))):
            exact_endpoint_healthy = _endpoint_is_measurable(
                producer=row["producer"],
                endpoint_identity=attested_endpoint,
                liveness_rows=ordered_rows,
            )
            assert not exact_endpoint_healthy, row["source_id"]
            decision = _evaluate_contract(
                [row],
                evidence_state=("healthy_current" if exact_endpoint_healthy else "dead_or_offline"),
                days_since=60,
                cadence_days=14,
            )
            assert decision == _Decision("unmeasurable", False), row["source_id"]


def test_removing_owner_attestation_after_cadence_is_unmeasurable_without_nudge() -> None:
    """The owner source is measurable only because the server attested the principal."""
    row = next(row for row in _producer_rows() if row["source_id"] == "manual_owner_attested")

    decision = _evaluate_contract(
        [row],
        evidence_state="attestation_missing",
        days_since=60,
        cadence_days=14,
    )

    assert decision == _Decision("unmeasurable", False)


def test_each_unmapped_writer_is_unmeasurable_without_candidate() -> None:
    """Telegram bot, Discord, calendar, current manual, and legacy sources cannot imply absence."""
    unmapped_rows = [row for row in _producer_rows() if row["mapping"] == "unmeasurable"]
    assert {row["source_id"] for row in unmapped_rows} == {
        "manual_unattested",
        "telegram_bot",
        "discord",
        "calendar_event",
        "legacy_or_unknown",
    }

    for row in unmapped_rows:
        decision = _evaluate_contract(
            [row],
            evidence_state="healthy_current",
            days_since=60,
            cadence_days=14,
        )
        assert decision == _Decision("unmeasurable", False), row["source_id"]


def test_mixed_or_unprovable_ownership_fails_closed() -> None:
    """Two live mapped sources still cannot become a guessed aggregate producer."""
    rows = _producer_rows()
    email = next(row for row in rows if row["source_id"] == "gmail_email")
    telegram = next(row for row in rows if row["source_id"] == "telegram_user_client")

    mixed = _evaluate_contract(
        [email, telegram],
        evidence_state="healthy_current",
        days_since=60,
        cadence_days=14,
    )

    assert mixed == _Decision("unmeasurable", False)


@pytest.mark.parametrize(
    "source_id",
    ["gmail_email", "telegram_user_client", "whatsapp_user_client", "manual_owner_attested"],
)
def test_healthy_elapsed_mapped_source_preserves_existing_priority_policy(source_id: str) -> None:
    """A provable source changes authority, not Relationship outreach policy."""
    row = next(row for row in _producer_rows() if row["source_id"] == source_id)

    moderate = _evaluate_contract(
        [row],
        evidence_state="healthy_current",
        days_since=15,
        cadence_days=14,
    )
    severe = _evaluate_contract(
        [row],
        evidence_state="healthy_current",
        days_since=29,
        cadence_days=14,
    )
    not_elapsed = _evaluate_contract(
        [row],
        evidence_state="healthy_current",
        days_since=14,
        cadence_days=14,
    )

    assert moderate == _Decision("absent", True, 35)
    assert severe == _Decision("absent", True, 45)
    assert not_elapsed == _Decision("present", False)


def test_planning_packet_names_every_stale_contact_consumer_and_downstream_handoff() -> None:
    """The adoption task cannot protect one nudge path while leaving another elapsed-only."""
    design = _DESIGN.read_text(encoding="utf-8")
    required = {
        "run_insight_scan()",
        "contacts_overdue()",
        "relationship-maintenance",
        "reconnect-planner",
        "Relationship Contacts tab",
        'Plex "Worth attention" rail',
        "bu-8cdl1.3",
        "new implementation PR",
        "producer_endpoint_identity",
        "(connector_type, endpoint_identity)",
        "request_context->>'source_endpoint_identity'",
        "thread/channel-only",
        "RFC 0029",
    }

    assert not {phrase for phrase in required if phrase not in design}
    assert "After this prerequisite merges, PR #3965 SHALL rebase" not in design
