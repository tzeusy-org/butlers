"""Pydantic response/request models for dashboard conversation endpoints."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

#: Closed vocabulary for ``VisibleResource.kind``, mirrored by the frontend
#: registry (``frontend/src/lib/page-context-registry.ts``) and the stateful
#: pages that call ``usePageSubject()``. An unrecognized kind is rejected
#: rather than forwarded into a routed session's prompt untyped.
VISIBLE_RESOURCE_KINDS = frozenset(
    {
        "entity",
        "session",
        "spend_window",
        "concentration",
        "memory",
        "qa_overview",
        "connector",
    }
)

#: Query-param key substrings (case-insensitive) that must never reach a
#: routed session even if a client forgot to strip them client-side. Defense
#: in depth: the dashboard's ContextChip/registry are the primary control
#: (``about/heart-and-soul/security.md`` — page context is untrusted display
#: data crossing into a prompt), this is the server-side backstop.
_SECRET_QUERY_KEY_MARKERS: tuple[str, ...] = (
    "token",
    "key",
    "secret",
    "password",
    "authorization",
)

#: Serialized-size budget (characters) for one PageContext payload. Exceeding
#: it truncates the largest optional contributors (filters, then
#: query_params, then visible_summary) and sets ``truncated=True`` rather
#: than silently dropping the whole snapshot or rejecting the request.
_PAGE_CONTEXT_BUDGET_CHARS = 2000


class VisibleResource(BaseModel):
    """Typed pointer to the specific resource a stateful page is showing.

    Set by a page's ``usePageSubject()`` call (e.g. the session id on
    ``SessionDetailPage``, the active predicate filters on
    ``ConcentrationPage``) on top of the auto-captured ``route``/
    ``query_params``.
    """

    kind: str = Field(..., min_length=1, description="Resource kind; see VISIBLE_RESOURCE_KINDS")
    id: str | None = Field(None, description="Resource identifier, when applicable")
    filters: dict[str, str] | None = Field(None, description="Active filter predicates")
    window: str | None = Field(None, description="Active time window, when applicable")

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        if value not in VISIBLE_RESOURCE_KINDS:
            raise ValueError(
                f"visible_resource.kind must be one of {sorted(VISIBLE_RESOURCE_KINDS)}, "
                f"got {value!r}"
            )
        return value


class PageContext(BaseModel):
    """Dashboard route/query/entity context captured at message send time.

    Attached to the ingest envelope so a routed butler session receives
    grounded context for the owner's statement (e.g. which entity page they
    were viewing when they stated a correction). Untrusted display data
    crossing into a prompt (``about/heart-and-soul/security.md``): the
    validators below are a redaction/budget backstop, not the primary
    control -- the frontend's per-route ``contextPolicy`` decides whether a
    page attaches this at all.
    """

    route: str = Field(..., min_length=1, description="Dashboard route path")
    query_params: dict[str, str] = Field(
        default_factory=dict, description="Route query string parameters"
    )
    entity_ref: str | None = Field(
        None, description="Optional entity/subject reference the page exposes"
    )
    visible_resource: VisibleResource | None = Field(
        None, description="Typed pointer to the specific resource the page is showing"
    )
    visible_summary: str | None = Field(
        None,
        max_length=200,
        description="Human-readable label for what visible_resource points at",
    )
    truncated: bool = Field(
        False,
        description=(
            "Set by the server when the payload exceeded the size budget and "
            "one or more fields were dropped to fit it."
        ),
    )

    @field_validator("query_params")
    @classmethod
    def _redact_secret_query_params(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            key: val
            for key, val in value.items()
            if not any(marker in key.lower() for marker in _SECRET_QUERY_KEY_MARKERS)
        }

    @model_validator(mode="after")
    def _enforce_size_budget(self) -> PageContext:
        def _size() -> int:
            return len(
                json.dumps(
                    {
                        "route": self.route,
                        "query_params": self.query_params,
                        "entity_ref": self.entity_ref,
                        "visible_resource": (
                            self.visible_resource.model_dump() if self.visible_resource else None
                        ),
                        "visible_summary": self.visible_summary,
                    },
                    ensure_ascii=False,
                )
            )

        if _size() <= _PAGE_CONTEXT_BUDGET_CHARS:
            return self

        truncated = False
        if self.visible_resource is not None and self.visible_resource.filters:
            self.visible_resource.filters = None
            truncated = True
        if _size() > _PAGE_CONTEXT_BUDGET_CHARS and self.query_params:
            self.query_params = {}
            truncated = True
        if _size() > _PAGE_CONTEXT_BUDGET_CHARS and self.visible_summary:
            self.visible_summary = self.visible_summary[:100]
            truncated = True
        if truncated:
            self.truncated = True
        return self


class ConversationCreateRequest(BaseModel):
    """Request body for creating a new conversation."""

    message: str = Field(..., min_length=1, description="First user message to send")
    message_id: UUID | None = Field(
        None,
        description=(
            "Client-generated UUID for this user message. Dashboard UI clients must "
            "supply and reuse it for retries and pre-SSE Stop; omission is only "
            "legacy API compatibility."
        ),
    )
    page_context: PageContext | None = Field(
        None, description="Dashboard page context captured at send time"
    )


class MessageCreateRequest(BaseModel):
    """Request body for sending a follow-up message."""

    message: str = Field(..., min_length=1, description="User message to send")
    message_id: UUID | None = Field(
        None,
        description=(
            "Client-generated UUID for this user message. Dashboard UI clients must "
            "supply and reuse it for retries and pre-SSE Stop; omission is only "
            "legacy API compatibility."
        ),
    )
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
    routed_butler: str | None = None
    latest_assistant_reply_at: datetime | None = Field(
        None,
        description=(
            "Timestamp of the most recent assistant-role message in this "
            "conversation, or null if none has arrived yet. This is the "
            "unread-badge watermark signal because it moves when a "
            "confirm-loop reply is persisted."
        ),
    )


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
    page_context: dict[str, Any] | None = Field(
        None,
        description=(
            "Compact page-context snapshot captured with this user message, "
            "or null for assistant rows and user rows sent without one."
        ),
    )
    captured_at: datetime | None = Field(
        None, description="When page_context was captured; null when page_context is null"
    )


class ConversationSearchResult(ConversationSummary):
    """Conversation search result with matching message snippet."""

    snippet: str


class ConversationStats(BaseModel):
    """Aggregate conversation statistics for a butler."""

    total_conversations: int
    active_conversations: int
    total_messages: int


class ConversationCancelResponse(BaseModel):
    """Raw response for the canonical message-scoped dashboard Stop endpoint.

    ``POST .../conversation-turns/{message_id}/cancel`` is canonical; the
    conversation-scoped cancel route is compatibility-only.

    Always HTTP 200 -- the three outcomes below are all legitimate results,
    not error conditions, and the frontend renders each honestly rather than
    treating a non-cancellation as calm success:

    - ``cancelled=True``: Stop is durably authoritative for this message:
      either its in-flight runtime accepted cancellation, or no runtime had
      reached the invocation boundary and none can start.
    - ``cancelled=False, already_finished=True``: nothing was in flight for
      this message turn (the turn already completed, or Stop was clicked
      after the reply landed) -- a benign no-op, never rendered as a failure.
    - ``cancelled=False, already_finished=False``: Stop could not be
      confirmed for work already invoking, or an irreversible side effect was
      already committed. ``message`` explains why; the frontend must surface
      this as a real failure, never as "stopped".
    """

    cancelled: bool
    already_finished: bool
    conversation_id: UUID | None = None
    session_id: UUID | None = None
    message: str | None = None
