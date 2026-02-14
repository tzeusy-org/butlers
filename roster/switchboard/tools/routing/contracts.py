"""Versioned contract models for Switchboard ingest/route envelopes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

SourceChannel = Literal["telegram", "slack", "email", "api", "mcp"]
SourceProvider = Literal["telegram", "slack", "gmail", "imap", "internal"]
NotifyChannel = Literal["telegram", "email", "sms", "chat"]
NotifyIntent = Literal["send", "reply"]
PolicyTier = Literal["default", "interactive", "high_priority"]
FanoutMode = Literal["parallel", "ordered", "conditional"]
_ALLOWED_PROVIDERS_BY_CHANNEL: dict[SourceChannel, frozenset[SourceProvider]] = {
    "telegram": frozenset({"telegram"}),
    "slack": frozenset({"slack"}),
    "email": frozenset({"gmail", "imap"}),
    "api": frozenset({"internal"}),
    "mcp": frozenset({"internal"}),
}
_RFC3339_WITH_TZ_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_IMMUTABLE_REQUEST_CONTEXT_FIELDS = (
    "request_id",
    "received_at",
    "source_channel",
    "source_endpoint_identity",
    "source_sender_identity",
)


def _validate_schema_version(value: str, *, expected: str) -> str:
    normalized = value.strip()
    if normalized != expected:
        raise PydanticCustomError(
            "unsupported_schema_version",
            "Unsupported schema version '{received}'; expected '{expected}'.",
            {"received": normalized, "expected": expected},
        )
    return normalized


def _validate_tz_aware(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PydanticCustomError(
            "timezone_required",
            "{field_name} must be RFC3339 with timezone offset.",
            {"field_name": field_name},
        )
    return value


def _validate_rfc3339_timestamp_input(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise PydanticCustomError(
            "rfc3339_string_required",
            "{field_name} must be an RFC3339 timestamp string with timezone offset.",
            {"field_name": field_name},
        )

    normalized = value.strip()
    if not _RFC3339_WITH_TZ_RE.fullmatch(normalized):
        raise PydanticCustomError(
            "rfc3339_string_required",
            "{field_name} must be an RFC3339 timestamp string with timezone offset.",
            {"field_name": field_name},
        )
    return normalized


def _normalize_request_context_payload(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return payload

    normalized = dict(payload)
    received_at = normalized.get("received_at")
    if isinstance(received_at, datetime):
        normalized["received_at"] = received_at.isoformat()
    return normalized


class IngestSourceV1(BaseModel):
    """Source identity for canonical ingest payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: SourceChannel
    provider: SourceProvider
    endpoint_identity: NonEmptyStr

    @model_validator(mode="after")
    def _validate_channel_provider_pair(self) -> IngestSourceV1:
        allowed_providers = _ALLOWED_PROVIDERS_BY_CHANNEL[self.channel]
        if self.provider not in allowed_providers:
            raise PydanticCustomError(
                "invalid_source_provider",
                "source.provider '{provider}' is not valid for source.channel '{channel}'.",
                {"provider": self.provider, "channel": self.channel},
            )
        return self


class IngestEventV1(BaseModel):
    """Provider event metadata for canonical ingest payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    external_event_id: NonEmptyStr
    external_thread_id: NonEmptyStr | None = None
    observed_at: datetime

    @field_validator("observed_at", mode="before")
    @classmethod
    def _observed_at_must_be_rfc3339_string(cls, value: Any) -> str:
        return _validate_rfc3339_timestamp_input(value, field_name="event.observed_at")

    @field_validator("observed_at")
    @classmethod
    def _observed_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        return _validate_tz_aware(value, field_name="event.observed_at")


class IngestSenderV1(BaseModel):
    """Sender identity for canonical ingest payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: NonEmptyStr


class IngestPayloadV1(BaseModel):
    """Source payload for canonical ingest payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw: dict[str, Any]
    normalized_text: NonEmptyStr


class IngestControlV1(BaseModel):
    """Optional ingest control metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: NonEmptyStr | None = None
    trace_context: dict[str, Any] = Field(default_factory=dict)
    policy_tier: PolicyTier = "default"


class IngestEnvelopeV1(BaseModel):
    """Canonical versioned ingest envelope (`ingest.v1`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    source: IngestSourceV1
    event: IngestEventV1
    sender: IngestSenderV1
    payload: IngestPayloadV1
    control: IngestControlV1 = Field(default_factory=IngestControlV1)

    @field_validator("schema_version")
    @classmethod
    def _validate_ingest_schema_version(cls, value: str) -> str:
        return _validate_schema_version(value, expected="ingest.v1")


class RouteRequestContextV1(BaseModel):
    """Immutable routed request lineage context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    received_at: datetime
    source_channel: SourceChannel
    source_endpoint_identity: NonEmptyStr
    source_sender_identity: NonEmptyStr
    source_thread_identity: NonEmptyStr | None = None
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


class RouteInputV1(BaseModel):
    """Route input payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: NonEmptyStr
    context: NonEmptyStr | dict[str, Any] | None = None


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
    """Optional source metadata propagated during dispatch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: SourceChannel
    identity: NonEmptyStr
    tool_name: NonEmptyStr
    source_id: NonEmptyStr | None = None


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


class NotifyRequestContextV1(BaseModel):
    """Optional request-context lineage for `notify.v1` requests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    source_channel: SourceChannel
    source_endpoint_identity: NonEmptyStr
    source_sender_identity: NonEmptyStr
    source_thread_identity: NonEmptyStr | None = None
    received_at: datetime | None = None

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
    def _received_at_must_be_tz_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _validate_tz_aware(value, field_name="request_context.received_at")

    @field_validator("received_at", mode="before")
    @classmethod
    def _received_at_must_be_rfc3339_string(cls, value: Any) -> Any:
        if value is None:
            return None
        return _validate_rfc3339_timestamp_input(value, field_name="request_context.received_at")


class NotifyDeliveryV1(BaseModel):
    """Delivery payload for `notify.v1` requests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: NotifyIntent
    channel: NotifyChannel
    message: NonEmptyStr
    recipient: NonEmptyStr | None = None
    subject: NonEmptyStr | None = None


class NotifyRequestV1(BaseModel):
    """Canonical versioned notify envelope (`notify.v1`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    origin_butler: NonEmptyStr
    delivery: NotifyDeliveryV1
    request_context: NotifyRequestContextV1 | None = None

    @field_validator("schema_version")
    @classmethod
    def _validate_notify_schema_version(cls, value: str) -> str:
        return _validate_schema_version(value, expected="notify.v1")

    @model_validator(mode="after")
    def _validate_reply_context(self) -> NotifyRequestV1:
        if self.delivery.intent != "reply":
            return self

        if self.request_context is None:
            raise PydanticCustomError(
                "reply_context_required",
                "notify.v1 reply intent requires request_context.",
                {},
            )

        if self.delivery.channel == "telegram" and not self.request_context.source_thread_identity:
            raise PydanticCustomError(
                "reply_thread_required",
                "notify.v1 telegram reply intent requires request_context.source_thread_identity.",
                {},
            )

        return self


def parse_ingest_envelope(payload: Mapping[str, Any]) -> IngestEnvelopeV1:
    """Parse and validate an `ingest.v1` envelope."""

    return IngestEnvelopeV1.model_validate(payload)


def parse_route_envelope(payload: Mapping[str, Any]) -> RouteEnvelopeV1:
    """Parse and validate a `route.v1` envelope."""

    return RouteEnvelopeV1.model_validate(payload)


def parse_notify_request(payload: Mapping[str, Any]) -> NotifyRequestV1:
    """Parse and validate a `notify.v1` request envelope."""

    return NotifyRequestV1.model_validate(payload)


__all__ = [
    "IngestControlV1",
    "IngestEnvelopeV1",
    "IngestEventV1",
    "IngestPayloadV1",
    "IngestSenderV1",
    "IngestSourceV1",
    "NotifyDeliveryV1",
    "NotifyRequestContextV1",
    "NotifyRequestV1",
    "RouteEnvelopeV1",
    "RouteInputV1",
    "RouteRequestContextV1",
    "RouteSourceMetadataV1",
    "RouteSubrequestV1",
    "RouteTargetV1",
    "parse_ingest_envelope",
    "parse_notify_request",
    "parse_route_envelope",
]
