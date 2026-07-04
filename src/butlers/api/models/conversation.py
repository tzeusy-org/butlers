"""Pydantic response/request models for dashboard conversation endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PageContext(BaseModel):
    """Dashboard route/query/entity context captured at message send time.

    Attached to the ingest envelope so a routed butler session receives
    grounded context for the owner's statement (e.g. which entity page they
    were viewing when they stated a correction).
    """

    route: str = Field(..., min_length=1, description="Dashboard route path")
    query_params: dict[str, str] = Field(
        default_factory=dict, description="Route query string parameters"
    )
    entity_ref: str | None = Field(
        None, description="Optional entity/subject reference the page exposes"
    )


class ConversationCreateRequest(BaseModel):
    """Request body for creating a new conversation."""

    message: str = Field(..., min_length=1, description="First user message to send")
    page_context: PageContext | None = Field(
        None, description="Dashboard page context captured at send time"
    )


class MessageCreateRequest(BaseModel):
    """Request body for sending a follow-up message."""

    message: str = Field(..., min_length=1, description="User message to send")
    page_context: PageContext | None = Field(
        None, description="Dashboard page context captured at send time"
    )


class ConversationUpdateRequest(BaseModel):
    """Request body for updating a conversation (title or status)."""

    title: str | None = Field(
        None, min_length=1, max_length=500, description="New conversation title"
    )
    status: Literal["active", "archived"] | None = Field(
        None, description="New conversation status"
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ConversationSummary(BaseModel):
    """Lightweight conversation representation for list views."""

    id: UUID
    butler_name: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_duration_ms: int
    routed_butler: str | None = None


class ConversationMessage(BaseModel):
    """Full message representation including attribution."""

    id: UUID
    conversation_id: UUID
    role: str
    content: str
    created_at: datetime
    session_id: UUID | None = None
    model_name: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None
    tool_calls: list[dict[str, Any]] | None = None
    error: str | None = None
    request_id: UUID | None = None


class ConversationSearchResult(BaseModel):
    """Conversation search result with matching message snippet."""

    id: UUID
    butler_name: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_duration_ms: int
    routed_butler: str | None = None
    snippet: str


class ConversationStats(BaseModel):
    """Aggregate conversation statistics for a butler."""

    total_conversations: int
    active_conversations: int
    total_messages: int
    total_input_tokens: int
    total_output_tokens: int
    total_duration_ms: int
