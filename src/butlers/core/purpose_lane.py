"""Content-blind purpose classification for model dispatches."""

from __future__ import annotations

from typing import Any, Literal

PurposeLane = Literal["standard", "private_content"]

PURPOSE_LANE_STANDARD: PurposeLane = "standard"
PURPOSE_LANE_PRIVATE_CONTENT: PurposeLane = "private_content"

_PRIVATE_SOURCE_CHANNELS = frozenset(
    {
        "telegram",
        "telegram_bot",
        "telegram_user_client",
        "whatsapp",
        "whatsapp_user_client",
    }
)


def purpose_lane_for_source_channel(source_channel: str | None) -> PurposeLane:
    """Classify a trusted channel token without inspecting message content."""
    if isinstance(source_channel, str) and source_channel.casefold() in _PRIVATE_SOURCE_CHANNELS:
        return PURPOSE_LANE_PRIVATE_CONTENT
    return PURPOSE_LANE_STANDARD


def source_channel_from_routing_context(context: dict[str, Any] | None) -> str | None:
    """Read the trusted source channel from the established routing envelope."""
    if not isinstance(context, dict):
        return None
    request_context = context.get("request_context")
    if isinstance(request_context, dict):
        channel = request_context.get("source_channel")
        if isinstance(channel, str) and channel:
            return channel
    source_metadata = context.get("source_metadata")
    if isinstance(source_metadata, dict):
        channel = source_metadata.get("channel")
        if isinstance(channel, str) and channel:
            return channel
    return None


def purpose_lane_from_routing_context(context: dict[str, Any] | None) -> PurposeLane:
    """Derive a lane solely from trusted routing metadata."""
    return purpose_lane_for_source_channel(source_channel_from_routing_context(context))
