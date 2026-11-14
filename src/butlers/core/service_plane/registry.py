"""The named relays the control plane serves (RFC 0038 D7, bu-2c1g7v).

Each relay names one target tool, the butlers that tool may live on, its
argument schema and its ``(workload, daemon)`` caller allowlist. A request
for a name not in this registry is refused before anything else happens.

Attribution a target needs (``source_butler``) comes from the authenticated
caller's presented daemon, never from the request body: every schema here
forbids extra fields, so a body cannot carry its own ``source_butler``.
"""

from __future__ import annotations

import tomllib
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from butlers.core.domain_event_contracts import DomainEventContractRegistry
from butlers.core.service_plane.relays import RelayEndpoint, RelayRefused, ServiceCaller

#: The workload that hosts every domain and staffer daemon except Switchboard.
DOMAIN_WORKLOAD = "butlers-up"
QA_REPORT_FINDING = "qa.report_finding"
DOMAIN_EVENT_RECEIVE = "domain_event.receive"
MEDICATION_TRAVEL_SNAPSHOT = "medication.travel_snapshot"
CONTACTS_RESOLVE_CHANNEL = "contacts.resolve_channel"
CONTACTS_REPORT_CHANNEL = "contacts.report_channel"
FLEET_CASE_OPEN = "fleet_case.open"
FLEET_CASE_CLOSE = "fleet_case.close"
FLEET_CASE_PROPOSE_POSTURE = "fleet_case.propose_posture"
FLEET_CASE_RECORD_LINK = "fleet_case.record_link"
MEMORY_CATALOG_FETCH = "memory.catalog_fetch"
OWNER_CHAT_SPANS = "owner_chat.spans"

#: Reads one live, visible ``public.memory_catalog`` row by its source pointer:
#: ``(source_schema, source_table, source_id)`` to a row with ``source_butler``
#: and ``memory_type``, or ``None``.
CatalogPointer = Callable[[str, str, UUID], Awaitable[Mapping[str, Any] | None]]
#: One page of owner-chat spans after an optional ``(received_at, inbox id)``
#: cursor, read by Switchboard from its own inbox (REQ-butler-switchboard-011).
OwnerChatSpans = Callable[[datetime | None, UUID | None], Awaitable[Mapping[str, Any]]]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Strict):
    """A fixed-purpose read: the caller chooses nothing."""


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


class ContactsResolveArgs(_Strict):
    channel_type: str
    channel_value: str
    request_id: UUID


class ContactsReportArgs(_Strict):
    subject: UUID
    channel_type: str
    channel_value: str
    primary: bool = False


class FleetCaseOpenArgs(_Strict):
    correlation_key: str


class FleetCaseCloseArgs(_Strict):
    case_id: str
    outcome: str


class FleetCaseProposePostureArgs(_Strict):
    case_id: str
    posture: str


class FleetCaseRecordLinkArgs(_Strict):
    case_id: str
    link_kind: str
    ref: str


class MemoryCatalogFetchArgs(_Strict):
    """Only the catalog pointer: the owner and memory type are Switchboard's to read."""

    source_schema: str = Field(min_length=1)
    source_table: Literal["facts", "rules"]
    source_id: UUID


class OwnerChatSpansArgs(_Strict):
    """Only the cursor: both values or neither. The caller chooses nothing else."""

    after_received_at: AwareDatetime | None = None
    after_inbox_id: UUID | None = None

    @model_validator(mode="after")
    def _whole_cursor(self) -> OwnerChatSpansArgs:
        if (self.after_received_at is None) != (self.after_inbox_id is None):
            raise ValueError("cursor_incomplete")
        return self


def _owner_chat_local(read: OwnerChatSpans):
    async def local(validated: BaseModel) -> Mapping[str, Any]:
        cursor = OwnerChatSpansArgs.model_validate(validated.model_dump())
        return await read(cursor.after_received_at, cursor.after_inbox_id)

    return local


#: Each Switchboard fleet-case write: relay name, its schema, the service twin.
FLEET_CASE_WRITES: tuple[tuple[str, type[BaseModel], str], ...] = (
    (FLEET_CASE_OPEN, FleetCaseOpenArgs, "open_case_service"),
    (FLEET_CASE_CLOSE, FleetCaseCloseArgs, "close_case_service"),
    (FLEET_CASE_PROPOSE_POSTURE, FleetCaseProposePostureArgs, "propose_case_posture_service"),
    (FLEET_CASE_RECORD_LINK, FleetCaseRecordLinkArgs, "record_case_link_service"),
)


def _roster_root(roster_dir: Path | None) -> Path:
    return roster_dir or Path(__file__).resolve().parents[4] / "roster"


def roster_daemons(roster_dir: Path | None = None) -> frozenset[str]:
    """Every roster butler, by directory with a ``butler.toml``."""
    root = _roster_root(roster_dir)
    return frozenset(entry.name for entry in root.iterdir() if (entry / "butler.toml").is_file())


def roster_module_daemons(module: str, roster_dir: Path | None = None) -> frozenset[str]:
    """Every roster butler whose ``butler.toml`` declares ``[modules.<module>]``."""
    root = _roster_root(roster_dir)
    return frozenset(
        daemon
        for daemon in roster_daemons(root)
        if module
        in tomllib.loads((root / daemon / "butler.toml").read_text(encoding="utf-8")).get(
            "modules", {}
        )
    )


def _domain_callers(daemons: Iterable[str]) -> frozenset[ServiceCaller]:
    return frozenset(
        ServiceCaller(DOMAIN_WORKLOAD, daemon) for daemon in daemons if daemon != "switchboard"
    )


async def _qa_prepare(caller: ServiceCaller, validated: BaseModel) -> tuple[str, dict[str, Any]]:
    args = validated.model_dump(mode="json", exclude_none=True)
    return "qa", {**args, "source_butler": caller.daemon}


def _domain_event_prepare(contracts: DomainEventContractRegistry):
    async def prepare(caller: ServiceCaller, validated: BaseModel) -> tuple[str, dict[str, Any]]:
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


def _memory_catalog_prepare(catalog_pointer: CatalogPointer):
    async def prepare(caller: ServiceCaller, validated: BaseModel) -> tuple[str, dict[str, Any]]:
        assert isinstance(validated, MemoryCatalogFetchArgs)
        row = await catalog_pointer(
            validated.source_schema, validated.source_table, validated.source_id
        )
        if row is None:
            raise RelayRefused("catalog_pointer_not_found")
        # The owner and the memory type come from the catalog, never the caller.
        return str(row["source_butler"]), {
            "memory_type": row["memory_type"],
            "memory_id": str(validated.source_id),
        }

    return prepare


def build_relays(
    daemons: Iterable[str],
    contracts: DomainEventContractRegistry,
    *,
    contacts_daemons: Iterable[str] = (),
    memory_daemons: Iterable[str] = (),
    catalog_pointer: CatalogPointer | None = None,
    owner_chat_spans: OwnerChatSpans | None = None,
) -> Mapping[str, RelayEndpoint]:
    """The relay registry for a roster and its domain-event contracts.

    ``contacts_daemons`` are the butlers hosting the Contacts module; only they
    may reach Relationship's Contacts ingress. ``memory_daemons`` host the
    Memory module; ``catalog_pointer`` reads ``public.memory_catalog`` for the
    memory catalog relay, which is served only when it is given.
    ``owner_chat_spans`` is Switchboard's own owner-chat read; the
    ``owner_chat.spans`` relay is served only when it is given, and only the
    Relationship daemon may call it.
    """
    callers = _domain_callers(daemons)
    owner_chat_relays = (
        {
            # REQ-butler-switchboard-011: answered by Switchboard itself, with no
            # MCP tool behind it, so no model session or direct MCP caller can
            # reach the owner's message text.
            OWNER_CHAT_SPANS: RelayEndpoint(
                name=OWNER_CHAT_SPANS,
                target_butlers=frozenset({"switchboard"}),
                target_tool="owner_chat_spans",
                args_model=OwnerChatSpansArgs,
                allowed_callers=frozenset(c for c in callers if c.daemon == "relationship"),
                local=_owner_chat_local(owner_chat_spans),
            )
        }
        if owner_chat_spans is not None
        else {}
    )
    memory_relays = (
        {
            # A cross-butler catalog dereference. The caller has already applied
            # its own held sensitivity ceiling; the owner applies its own again.
            MEMORY_CATALOG_FETCH: RelayEndpoint(
                name=MEMORY_CATALOG_FETCH,
                target_butlers=frozenset(memory_daemons),
                target_tool="memory_get_service",
                args_model=MemoryCatalogFetchArgs,
                allowed_callers=callers,
                prepare=_memory_catalog_prepare(catalog_pointer),
            )
        }
        if catalog_pointer is not None
        else {}
    )
    contacts = frozenset(contacts_daemons)
    contacts_callers = frozenset(c for c in callers if c.daemon in contacts)
    publishers = {contract.publisher for contract in contracts.contracts()}
    subscribers = frozenset(
        subscriber
        for contract in contracts.contracts()
        for subscriber in contract.permitted_subscribers
    )
    # Fleet-case writes (RLS: only Switchboard's role writes fleet_cases and
    # fleet_case_links). Every domain daemon may write, as today; each relay
    # reaches Switchboard's own service-only twin of the tool.
    fleet_case_relays = {
        name: RelayEndpoint(
            name=name,
            target_butlers=frozenset({"switchboard"}),
            target_tool=twin,
            args_model=args_model,
            allowed_callers=callers,
        )
        for name, args_model, twin in FLEET_CASE_WRITES
    }
    return {
        **fleet_case_relays,
        **memory_relays,
        **owner_chat_relays,
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
        # Travel's pre-trip read of Health's privacy-minimized medication fields
        # (REQ-butler-switchboard-008's worked example). The target is Health's
        # service-only twin; the LLM-facing tool is never relay-reachable.
        MEDICATION_TRAVEL_SNAPSHOT: RelayEndpoint(
            name=MEDICATION_TRAVEL_SNAPSHOT,
            target_butlers=frozenset({"health"}),
            target_tool="medication_travel_snapshot_service",
            args_model=NoArgs,
            allowed_callers=frozenset(c for c in callers if c.daemon == "travel"),
        ),
        # The Contacts module's owner boundary: lookup and third-party report of
        # one channel. Both targets are already service-only, so no twin; neither
        # takes a caller identity, and the schemas forbid any authority field.
        CONTACTS_RESOLVE_CHANNEL: RelayEndpoint(
            name=CONTACTS_RESOLVE_CHANNEL,
            target_butlers=frozenset({"relationship"}),
            target_tool="contacts_resolve_channel",
            args_model=ContactsResolveArgs,
            allowed_callers=contacts_callers,
        ),
        CONTACTS_REPORT_CHANNEL: RelayEndpoint(
            name=CONTACTS_REPORT_CHANNEL,
            target_butlers=frozenset({"relationship"}),
            target_tool="contacts_report_channel",
            args_model=ContactsReportArgs,
            allowed_callers=contacts_callers,
        ),
    }
