"""Inventory of today's generic ``route`` callers and what each becomes (RFC 0038 D7).

RFC 0038 D7 allows no generic route proxy to survive the switch. Every production
call site that reaches another butler through ``route`` is listed here with
exactly one disposition:

``named_relay``
    The call moves to a named service-plane relay on the control plane's service
    listener: fixed schema, one target, a per-endpoint caller allowlist.
``messenger_delivery``
    Switchboard ``deliver()`` sending to Messenger. Messenger's delivery endpoint
    authenticates Switchboard as the caller.
``dashboard_call``
    An owner action from the dashboard. It goes through the control plane's
    service listener, authenticated by the signed dashboard control client.
``control_plane_internal``
    Switchboard's own deterministic code calling one fixed domain tool in
    process, under that call's own authentication. No daemon-to-daemon hop
    exists to authenticate; it must not become a generic proxy.
``removed``
    The call dies at the switch, with the module or lane that owns it.

``tests/contracts/test_service_plane_inventory.py`` scans production code and
fails when a route call site is missing from this table or the table names a
site that no longer exists. The scan resolves import aliases, assignments,
``partial`` and ``call_fn=route``. Files the deletion inventory removes whole
(section 1.1, for example ``modules/pipeline.py`` and ``correct_route.py``) are
excluded: their sites die with them. A new caller must be classified here first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Disposition = Literal[
    "named_relay", "messenger_delivery", "dashboard_call", "control_plane_internal", "removed"
]


@dataclass(frozen=True, slots=True)
class RouteCaller:
    """One production call site that reaches another butler through ``route``."""

    path: str
    function: str
    disposition: Disposition
    becomes: str


ROUTE_CALLERS: tuple[RouteCaller, ...] = (
    RouteCaller(
        "src/butlers/daemon.py",
        "ButlerDaemon._start_mcp_server",
        "control_plane_internal",
        "Hands Switchboard's route() to FinanceWarningControlOwner: one fixed Finance read "
        "under its own held-call bearer (finance_warning_control.py).",
    ),
    RouteCaller(
        "src/butlers/core_tools/_switchboard.py",
        "register_switchboard_tools.answer_question",
        "removed",
        "Passes route() to read_system_answer; dies with the dashboard answer lane.",
    ),
    RouteCaller(
        "src/butlers/core/service_plane/wiring.py",
        "build_switchboard_relay_route.dispatch_for.dispatch",
        "named_relay",
        "The relays' own dispatch: Switchboard's in-process route(), after the relay's checks.",
    ),
    RouteCaller(
        "roster/switchboard/modules/tools.py",
        "register_tools.route",
        "removed",
        "The generic route MCP tool. Its daemon callers move to the named relays below.",
    ),
    RouteCaller(
        "roster/switchboard/tools/extraction/audit_log.py",
        "extraction_log_undo",
        "removed",
        "Undo of a signal-extraction write; dies with classify-and-route extraction.",
    ),
    RouteCaller(
        "roster/switchboard/tools/notification/deliver.py",
        "_deliver_via_notify_request",
        "messenger_delivery",
        "notify.v1 delivery to Messenger route.execute.",
    ),
    RouteCaller(
        "roster/switchboard/tools/notification/deliver.py",
        "_deliver_recovery_via_notify_request",
        "messenger_delivery",
        "Approval-recovery notify.v1 delivery to Messenger route.execute.",
    ),
    RouteCaller(
        "roster/switchboard/tools/notification/deliver.py",
        "deliver",
        "messenger_delivery",
        "Channel delivery through the Messenger candidate.",
    ),
    RouteCaller(
        "roster/switchboard/tools/notification/deliver.py",
        "amend_delivery",
        "messenger_delivery",
        "Edit of a delivered message through Messenger.",
    ),
    RouteCaller(
        "roster/travel/tools/health.py",
        "request_health_medication_snapshot",
        "named_relay",
        "medication.travel_snapshot relay (served): Travel reads Health's service-only twin "
        "medication_travel_snapshot_service; the route call is the switch-off path.",
    ),
    RouteCaller(
        "src/butlers/core/qa/relay.py",
        "QaRelay._try_qa_relay",
        "named_relay",
        "qa.report_finding: the QA error relay.",
    ),
    RouteCaller(
        "src/butlers/core_tools/_dashboard_system_answer.py",
        "read_system_answer",
        "removed",
        "Dashboard answer lane; dies with route_to_butler and the dashboard lane.",
    ),
    RouteCaller(
        "src/butlers/core_tools/_domain_events.py",
        "_dispatch_receive_via_switchboard",
        "named_relay",
        "domain_event.receive: domain-event fan-out to one subscriber.",
    ),
    RouteCaller(
        "src/butlers/core_tools/_domain_events.py",
        "_SwitchboardInProcessRouteClient.call_tool",
        "control_plane_internal",
        "Switchboard's reconciliation sweep redelivering receive_domain_event to a subscriber "
        "the fan-out ledger already resolved.",
    ),
    RouteCaller(
        "src/butlers/core_tools/_fleet_cases.py",
        "_dispatch_fleet_case_write",
        "named_relay",
        "fleet_case.open/close/propose_posture/record_link relays (served): each reaches "
        "Switchboard's service-only twin (open_case_service, ...); the route call is the "
        "switch-off path.",
    ),
    RouteCaller(
        "src/butlers/core_tools/_memory_catalog.py",
        "register_memory_catalog_tools.memory_catalog_fetch",
        "named_relay",
        "memory.catalog_fetch relay (served): only the catalog pointer travels; Switchboard "
        "reads the owner from public.memory_catalog and reaches its service-only "
        "memory_get_service twin. The route call is the switch-off and Switchboard path.",
    ),
    RouteCaller(
        "src/butlers/core_tools/_switchboard.py",
        "register_switchboard_tools._dispatch_dashboard_target",
        "removed",
        "Dies with route_to_butler and the dashboard lane.",
    ),
    RouteCaller(
        "src/butlers/core_tools/_switchboard.py",
        "register_switchboard_tools.file_bug_report",
        "dashboard_call",
        "Owner bug report: the dashboard calls qa.report_finding through the service listener.",
    ),
    RouteCaller(
        "src/butlers/modules/calendar_response_fence.py",
        "CalendarResponseFence.check_peers",
        "removed",
        "Deleted before the switch: bu-qozovd.1 slice 3 (owner decision 2026-10-24) makes "
        "Relationship the sole invite-response owner and deletes the fence, its tools and this "
        "call. No relay or twin; the switch depends on that slice.",
    ),
    RouteCaller(
        "src/butlers/modules/contacts/owner.py",
        "ContactsOwner._call",
        "named_relay",
        "contacts.resolve_channel and contacts.report_channel relays (served): a Contacts "
        "daemon reaches Relationship's service-only contacts_ingress tools; the route call is "
        "the switch-off path.",
    ),
)
