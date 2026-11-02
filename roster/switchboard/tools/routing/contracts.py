"""Versioned contract models for the Switchboard `route.v1` envelope.

The shared ingest/notify wire types and validators live in
``butlers.tools.switchboard.contracts``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from butlers.tools.switchboard.contracts import (
    NonEmptyStr,
    SourceChannel,
    _validate_rfc3339_timestamp_input,
    _validate_schema_version,
    _validate_tz_aware,
)

FanoutMode = Literal["parallel", "ordered", "conditional"]
_IMMUTABLE_REQUEST_CONTEXT_FIELDS = (
    "request_id",
    "received_at",
    "source_channel",
    "source_endpoint_identity",
    "source_sender_identity",
)


def _normalize_request_context_payload(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return payload

    normalized = dict(payload)
    received_at = normalized.get("received_at")
    if isinstance(received_at, datetime):
        normalized["received_at"] = received_at.isoformat()
    return normalized


class RouteRequestContextV1(BaseModel):
    """Immutable routed request lineage context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    received_at: datetime
    source_channel: SourceChannel
    source_endpoint_identity: NonEmptyStr
    source_sender_identity: NonEmptyStr
    source_sender_contact_id: NonEmptyStr | None = None
    source_sender_entity_id: NonEmptyStr | None = None
    source_thread_identity: NonEmptyStr | None = None
    external_conversation_id: NonEmptyStr | None = None
    reply_target_ref: NonEmptyStr | None = None
    addressed: bool = False
    subrequest_id: NonEmptyStr | None = None
    segment_id: NonEmptyStr | None = None
    trace_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("request_id")
    @classmethod
    def _request_id_must_be_uuid7(cls, value: UUID) -> UUID:
        if value.version != 7:
            raise PydanticCustomError(
                "uuid7_required",
                "request_context.request_id must be a valid UUID7.",
                {},
            )
        return value

    @field_validator("received_at")
    @classmethod
    def _received_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        return _validate_tz_aware(value, field_name="request_context.received_at")

    @field_validator("received_at", mode="before")
    @classmethod
    def _received_at_must_be_rfc3339_string(cls, value: Any) -> str:
        return _validate_rfc3339_timestamp_input(value, field_name="request_context.received_at")

    @model_validator(mode="after")
    def _validate_lineage_immutability(self, info: ValidationInfo) -> RouteRequestContextV1:
        context = info.context if isinstance(info.context, dict) else {}
        lineage = context.get("lineage")
        if lineage is None:
            return self

        if isinstance(lineage, dict):
            lineage = type(self).model_validate(lineage)

        if not isinstance(lineage, type(self)):
            raise PydanticCustomError(
                "invalid_lineage_context",
                "lineage context must be a RouteRequestContextV1 object or mapping.",
                {},
            )

        for field_name in _IMMUTABLE_REQUEST_CONTEXT_FIELDS:
            if getattr(self, field_name) != getattr(lineage, field_name):
                raise PydanticCustomError(
                    "immutable_request_context",
                    "request_context.{field_name} is immutable for routed lineage.",
                    {"field_name": field_name},
                )
        return self

    @classmethod
    def model_validate_with_lineage(
        cls,
        obj: Any,
        *,
        lineage: RouteRequestContextV1 | dict[str, Any],
    ) -> RouteRequestContextV1:
        """Validate request context while enforcing immutable lineage fields."""
        normalized_obj = _normalize_request_context_payload(obj)
        normalized_lineage = _normalize_request_context_payload(lineage)
        return cls.model_validate(
            normalized_obj,
            context={"lineage": normalized_lineage},
        )


_ALLOWED_COMPLEXITY_VALUES: frozenset[str] = frozenset(
    {"reasoning", "workhorse", "cheap", "specialty", "local", "legacy"}
)


class RouteInputV1(BaseModel):
    """Route input payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: NonEmptyStr
    context: NonEmptyStr | dict[str, Any] | None = None
    conversation_history: str | None = None
    complexity: str = "workhorse"
    attachments: list[dict[str, Any]] | None = None

    @field_validator("complexity")
    @classmethod
    def _validate_complexity(cls, value: str) -> str:
        normalized = value.strip().lower() if isinstance(value, str) else value
        if normalized not in _ALLOWED_COMPLEXITY_VALUES:
            raise PydanticCustomError(
                "invalid_complexity",
                "input.complexity '{value}' is not valid; expected one of {allowed}.",
                {"value": value, "allowed": sorted(_ALLOWED_COMPLEXITY_VALUES)},
            )
        return normalized


class RouteSubrequestV1(BaseModel):
    """Subrequest metadata for fanout routing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subrequest_id: NonEmptyStr
    segment_id: NonEmptyStr
    fanout_mode: FanoutMode


class RouteTargetV1(BaseModel):
    """Target metadata for downstream dispatch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    butler: NonEmptyStr
    tool: Literal["route.execute"] = "route.execute"


class RouteSourceMetadataV1(BaseModel):
    """Optional source metadata propagated during dispatch.

    ``dashboard_message_id`` is the immutable user-message identity for the
    dashboard Stop control protocol.  It is deliberately distinct from
    ``source_id``, which remains sender/transport identity for dedupe and
    relationship resolution.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: SourceChannel
    identity: NonEmptyStr
    tool_name: NonEmptyStr
    source_id: NonEmptyStr | None = None
    dashboard_message_id: UUID | None = None


class RouteEnvelopeV1(BaseModel):
    """Canonical versioned route envelope (`route.v1`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    request_context: RouteRequestContextV1
    input: RouteInputV1
    subrequest: RouteSubrequestV1 | None = None
    target: RouteTargetV1 | None = None
    source_metadata: RouteSourceMetadataV1 | None = None
    trace_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _validate_route_schema_version(cls, value: str) -> str:
        return _validate_schema_version(value, expected="route.v1")

    @model_validator(mode="after")
    def _validate_lineage_consistency(self) -> RouteEnvelopeV1:
        if self.subrequest is None:
            return self

        context = self.request_context
        if context.subrequest_id and context.subrequest_id != self.subrequest.subrequest_id:
            raise PydanticCustomError(
                "lineage_mismatch",
                "request_context.subrequest_id must match subrequest.subrequest_id.",
                {},
            )
        if context.segment_id and context.segment_id != self.subrequest.segment_id:
            raise PydanticCustomError(
                "lineage_mismatch",
                "request_context.segment_id must match subrequest.segment_id.",
                {},
            )
        return self


def parse_route_envelope(payload: Mapping[str, Any]) -> RouteEnvelopeV1:
    """Parse and validate a `route.v1` envelope."""

    return RouteEnvelopeV1.model_validate(payload)


__all__ = [
    "RouteEnvelopeV1",
    "RouteInputV1",
    "RouteRequestContextV1",
    "RouteSourceMetadataV1",
    "RouteSubrequestV1",
    "RouteTargetV1",
    "parse_route_envelope",
]
