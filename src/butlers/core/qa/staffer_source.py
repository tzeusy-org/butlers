"""Dark QA source adapter for the existing admission journal.

There is intentionally no production DashboardFindingReader implementation.
The QA-authenticated service/owner evidence boundary has not been released;
direct Switchboard SQL into QA's store would not supply it. Closed tests may
inject the owner reader to prove admission semantics, never authentication.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from butlers.core.goal_admission import (
    CatalogProvider,
    Evidence,
    EvidenceRefused,
    SourceRef,
    StafferContract,
    digest,
)
from butlers.core.qa.dashboard_inbox import DashboardIdentity

_DEFINITION = Path(__file__).resolve().parents[4] / "roster/qa/qa_triage_v1.toml"
_EXPECTED = {
    "work_kind": "qa_triage_v1",
    "version": 1,
    "staffer": "qa",
    "owner_domain": "qa",
    "capabilities": [],
    "resources": {},
}


def triage_source(finding_id: UUID) -> SourceRef:
    if not isinstance(finding_id, UUID):
        raise EvidenceRefused("source_evidence_unavailable")
    return SourceRef("staffer", f"qa:triage:{finding_id}")


@dataclass(frozen=True)
class DashboardFinding:
    """Owner-supplied acknowledged snapshot; never accepted as a tool argument.

    The reader owns verifying accepted relay provenance, enabled source,
    immutable identity/hash/linkage and current winning generation. A receipt
    or caller-supplied instance alone is not that trusted verification.
    """

    identity: DashboardIdentity
    payload_hash: str
    generation: int
    finding_id: UUID
    finding: dict


class DashboardFindingReader(Protocol):
    async def load(self, finding_id: UUID) -> DashboardFinding:
        """Read via released authenticated QA-owner evidence, or refuse/unavailable.

        Never deserialize caller-selected provenance, contract, enabled state,
        generation or source identity. No direct foreign-schema SQL fallback.
        """
        ...


@dataclass(frozen=True)
class QaTriageInput(Evidence):
    # Entire persisted anonymized finding, exposed data only. No tool reader,
    # effect grant, executor request or trusted instruction is inferred.
    finding: dict | None = None


class QaTriageEvidence:
    def __init__(
        self,
        reader: DashboardFindingReader | None = None,
        catalog: CatalogProvider | None = None,
    ):
        if catalog is None:
            from butlers.core.catalog_reader import PublishedCatalogProvider

            catalog = PublishedCatalogProvider()
        self.reader, self.catalog = reader, catalog

    async def load(self, connection, source: SourceRef) -> QaTriageInput:
        if self.reader is None or source.kind != "staffer":
            raise EvidenceRefused("source_evidence_unavailable")
        try:
            finding_id = UUID(source.id.removeprefix("qa:triage:"))
        except ValueError:
            raise EvidenceRefused("source_evidence_unavailable") from None
        if source != triage_source(finding_id):
            raise EvidenceRefused("source_evidence_unavailable")
        row = await self.reader.load(finding_id)
        if (
            not isinstance(row.identity, DashboardIdentity)
            or row.finding_id != finding_id
            or row.finding.get("id") != finding_id
            or row.finding.get("source_type") != "butler_reports"
            or row.finding.get("source_butler") != "switchboard"
            or type(row.generation) is not int
            or row.generation < 1
            or len(row.payload_hash) != 64
            or any(c not in "0123456789abcdef" for c in row.payload_hash)
        ):
            raise EvidenceRefused("source_evidence_unavailable")
        try:
            definition = tomllib.loads(_DEFINITION.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise EvidenceRefused("staffer_contract_unavailable") from None
        if definition != _EXPECTED or type(definition.get("version")) is not int:
            raise EvidenceRefused("staffer_contract_unavailable")
        catalog = await self.catalog.load(connection)
        contract = StafferContract(
            staffer="qa",
            owner_domain="qa",
            version=1,
            capabilities=(),
            catalog_digest=catalog.digest,
            digest=digest({"definition": definition, "catalog_digest": catalog.digest}),
        )
        return QaTriageInput(
            kind="staffer",
            source_partition="qa:triage",
            outside_content=True,
            staffer=contract,
            finding=dict(row.finding),
        )
