"""The named relays the control plane serves (RFC 0038 D7, bu-2c1g7v).

Each relay names one target tool, the butlers that tool may live on, its
argument schema and its ``(workload, daemon)`` caller allowlist. A request
for a name not in this registry is refused before anything else happens.

Attribution a target needs (``source_butler``) comes from the authenticated
caller's presented daemon, never from the request body: every schema here
forbids extra fields, so a body cannot carry its own ``source_butler``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from butlers.core.domain_event_contracts import DomainEventContractRegistry
from butlers.core.service_plane.relays import RelayEndpoint, RelayRefused, ServiceCaller

#: The workload that hosts every domain and staffer daemon except Switchboard.
DOMAIN_WORKLOAD = "butlers-up"
QA_REPORT_FINDING = "qa.report_finding"
DOMAIN_EVENT_RECEIVE = "domain_event.receive"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QaFindingArgs(_Strict):
    fingerprint: str = Field(min_length=1, max_length=256)
    exception_type: str = Field(min_length=1, max_length=256)
    call_site: str = Field(min_length=1, max_length=512)
    severity: int
    event_summary: str = Field(max_length=200)
    context: str | None = None


class DomainEventArgs(_Strict):
    subscriber: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    payload: dict[str, Any]


def roster_daemons(roster_dir: Path | None = None) -> frozenset[str]:
    """Every roster butler, by directory with a ``butler.toml``."""
    root = roster_dir or Path(__file__).resolve().parents[4] / "roster"
    return frozenset(entry.name for entry in root.iterdir() if (entry / "butler.toml").is_file())


def _domain_callers(daemons: Iterable[str]) -> frozenset[ServiceCaller]:
    return frozenset(
        ServiceCaller(DOMAIN_WORKLOAD, daemon) for daemon in daemons if daemon != "switchboard"
    )


def _qa_prepare(caller: ServiceCaller, validated: BaseModel) -> tuple[str, dict[str, Any]]:
    args = validated.model_dump(mode="json", exclude_none=True)
    return "qa", {**args, "source_butler": caller.daemon}


def _domain_event_prepare(contracts: DomainEventContractRegistry):
    def prepare(caller: ServiceCaller, validated: BaseModel) -> tuple[str, dict[str, Any]]:
        assert isinstance(validated, DomainEventArgs)
        contract = contracts.get(validated.event_type)
        if contract is None or contract.publisher != caller.daemon:
            raise RelayRefused("caller_not_publisher")
        if validated.subscriber not in contract.permitted_subscribers:
            raise RelayRefused("target_not_allowed")
        return validated.subscriber, {
            "event_id": validated.event_id,
            "event_type": validated.event_type,
            "source_butler": caller.daemon,
            "payload": validated.payload,
        }

    return prepare


def build_relays(
    daemons: Iterable[str], contracts: DomainEventContractRegistry
) -> Mapping[str, RelayEndpoint]:
    """The relay registry for a roster and its domain-event contracts."""
    callers = _domain_callers(daemons)
    publishers = {contract.publisher for contract in contracts.contracts()}
    subscribers = frozenset(
        subscriber
        for contract in contracts.contracts()
        for subscriber in contract.permitted_subscribers
    )
    return {
        QA_REPORT_FINDING: RelayEndpoint(
            name=QA_REPORT_FINDING,
            target_butlers=frozenset({"qa"}),
            target_tool="report_finding",
            args_model=QaFindingArgs,
            allowed_callers=callers,
            prepare=_qa_prepare,
            allow_stale=True,
        ),
        DOMAIN_EVENT_RECEIVE: RelayEndpoint(
            name=DOMAIN_EVENT_RECEIVE,
            target_butlers=subscribers,
            target_tool="receive_domain_event",
            args_model=DomainEventArgs,
            allowed_callers=frozenset(c for c in callers if c.daemon in publishers),
            prepare=_domain_event_prepare(contracts),
        ),
    }
