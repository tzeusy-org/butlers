"""Switchboard tools — inter-butler routing and registry.

Re-exports all public symbols so that ``from butlers.tools.switchboard import X``
continues to work as before.
"""

from butlers.tools.switchboard.extraction.audit_log import (
    extraction_log_list,
    extraction_log_undo,
    log_extraction,
)
from butlers.tools.switchboard.ingestion.ingest import (
    IngestAcceptedResponse,
    ingest_v1,
)
from butlers.tools.switchboard.notification.deliver import (
    SUPPORTED_CHANNELS,
    _build_channel_args,
    _write_outbound_message_inbox,
    deliver,
)
from butlers.tools.switchboard.notification.log import (
    log_notification,
)
from butlers.tools.switchboard.registry.registry import (
    DEFAULT_ROUTE_CONTRACT_VERSION,
    ELIGIBILITY_ACTIVE,
    ELIGIBILITY_QUARANTINED,
    ELIGIBILITY_STALE,
    discover_butlers,
    list_butlers,
    register_butler,
    resolve_routing_target,
    validate_route_target,
)
from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep
from butlers.tools.switchboard.routing.contracts import (
    IngestEnvelopeV1,
    NotifyRequestV1,
    RouteEnvelopeV1,
    RouteRequestContextV1,
    parse_ingest_envelope,
    parse_notify_request,
    parse_route_envelope,
)
from butlers.tools.switchboard.routing.route import (
    _call_butler_tool,
    _log_routing,
    post_mail,
    route,
)
from butlers.tools.switchboard.routing.telemetry import (
    get_switchboard_telemetry,
    reset_switchboard_telemetry_for_tests,
)

__all__ = [
    "DEFAULT_ROUTE_CONTRACT_VERSION",
    "ELIGIBILITY_ACTIVE",
    "ELIGIBILITY_QUARANTINED",
    "ELIGIBILITY_STALE",
    "IngestAcceptedResponse",
    "IngestEnvelopeV1",
    "NotifyRequestV1",
    "RouteEnvelopeV1",
    "RouteRequestContextV1",
    "SUPPORTED_CHANNELS",
    "_build_channel_args",
    "_write_outbound_message_inbox",
    "_call_butler_tool",
    "_log_routing",
    "deliver",
    "discover_butlers",
    "extraction_log_list",
    "extraction_log_undo",
    "get_switchboard_telemetry",
    "ingest_v1",
    "list_butlers",
    "log_extraction",
    "log_notification",
    "parse_ingest_envelope",
    "parse_notify_request",
    "parse_route_envelope",
    "post_mail",
    "register_butler",
    "reset_switchboard_telemetry_for_tests",
    "resolve_routing_target",
    "route",
    "run_eligibility_sweep",
    "validate_route_target",
]
