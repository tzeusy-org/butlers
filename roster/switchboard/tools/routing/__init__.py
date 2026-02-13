"""Routing tools — message classification, routing, dispatch, and aggregation."""

from butlers.tools.switchboard.routing.classify import (
    classify_message,
    classify_message_multi,
)
from butlers.tools.switchboard.routing.contracts import (
    IngestEnvelopeV1,
    RouteEnvelopeV1,
    RouteRequestContextV1,
    parse_ingest_envelope,
    parse_route_envelope,
)
from butlers.tools.switchboard.routing.dispatch import (
    ButlerResult,
    aggregate_responses,
    dispatch_decomposed,
    dispatch_to_targets,
)
from butlers.tools.switchboard.routing.route import (
    post_mail,
    route,
)

__all__ = [
    "ButlerResult",
    "IngestEnvelopeV1",
    "RouteEnvelopeV1",
    "RouteRequestContextV1",
    "aggregate_responses",
    "classify_message",
    "classify_message_multi",
    "dispatch_to_targets",
    "dispatch_decomposed",
    "parse_ingest_envelope",
    "parse_route_envelope",
    "post_mail",
    "route",
]
