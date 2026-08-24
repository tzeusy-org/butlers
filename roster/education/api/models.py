"""Pydantic models for the education butler API.

Provides models for mind maps, nodes, quiz responses, analytics snapshots,
teaching flows, and mastery summaries used by the education butler's
dashboard endpoints.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class MindMapEdgeResponse(BaseModel):
    """A directed edge in the mind map DAG."""

    parent_node_id: str
    child_node_id: str
    edge_type: str


class MindMapNodeResponse(BaseModel):
    """A concept node in a mind map."""

    id: str
    mind_map_id: str
    label: str
    description: str | None = None
    depth: int = 0
    mastery_score: float = 0.0
    mastery_status: str = "unseen"
    ease_factor: float = 2.5
    repetitions: int = 0
    next_review_at: str | None = None
    last_reviewed_at: str | None = None
    effort_minutes: int | None = None
    metadata: dict = {}
    created_at: str
    updated_at: str


class MindMapResponse(BaseModel):
    """A mind map with optional nested nodes and edges."""

    id: str
    title: str
    root_node_id: str | None = None
    status: str
    created_at: str
    updated_at: str
    nodes: list[MindMapNodeResponse] = []
    edges: list[MindMapEdgeResponse] = []


class QuizResponseModel(BaseModel):
    """A recorded quiz response for a concept node."""

    id: str
    node_id: str
    mind_map_id: str
    question_text: str
    user_answer: str | None = None
    quality: int
    response_type: str
    session_id: str | None = None
    responded_at: str
    evaluator_notes: str | None = None
    node_label: str | None = None


class AnalyticsSnapshotResponse(BaseModel):
    """An analytics snapshot for a mind map, with optional trend data."""

    id: str | None = None
    mind_map_id: str
    snapshot_date: str
    metrics: dict[str, Any] = {}
    created_at: str | None = None
    trend: list[dict[str, Any]] = []


class TeachingFlowResponse(BaseModel):
    """A teaching flow entry with mastery summary."""

    mind_map_id: str
    title: str
    status: str
    session_count: int = 0
    started_at: str | None = None
    last_session_at: str | None = None
    mastery_pct: float = 0.0


class MasterySummaryResponse(BaseModel):
    """Aggregate mastery statistics for a mind map."""

    mind_map_id: str
    total_nodes: int
    mastered_count: int
    learning_count: int
    reviewing_count: int
    unseen_count: int
    diagnosed_count: int
    avg_mastery_score: float
    struggling_node_ids: list[str] = []


class CrossTopicTopicEntry(BaseModel):
    """Per-topic entry in cross-topic analytics."""

    mind_map_id: str
    title: str
    mastery_pct: float
    retention_rate_7d: float | None = None
    velocity: float


class CrossTopicAnalyticsResponse(BaseModel):
    """Cross-topic comparative analytics across all active mind maps."""

    topics: list[CrossTopicTopicEntry] = []
    strongest_topic: str | None = None
    weakest_topic: str | None = None
    portfolio_mastery: float = 0.0


class PendingReviewNodeResponse(BaseModel):
    """A node due for spaced-repetition review."""

    node_id: str
    label: str
    ease_factor: float
    repetitions: int
    next_review_at: str
    mastery_status: str
    # Real mastery score for this node (education.mind_map_nodes.mastery_score).
    # Optional/nullable because older callers of spaced_repetition_pending_reviews
    # (e.g. mocked tests) may not include it — never fabricate a value when absent.
    mastery_score: float | None = None


class StatusUpdateRequest(BaseModel):
    """Request body for updating a mind map's status."""

    status: str


class CurriculumRequestBody(BaseModel):
    """Request body for submitting a new curriculum request."""

    topic: str
    goal: str | None = None


class CurriculumRequestResponse(BaseModel):
    """202 acknowledgement for a submitted curriculum request.

    ``status`` is always ``"accepted"`` — the request has been durably recorded
    and the curriculum session has been handed off, nothing more. ``request_id``
    is the receipt to follow for the actual outcome.
    """

    status: str
    topic: str
    request_id: str


class CurriculumRequestReceipt(BaseModel):
    """Durable accepted-to-outcome receipt for one curriculum request.

    Mirrors a row of ``education.curriculum_requests``. Evidence fields stay
    ``None`` until the detached curriculum work settles them; a terminal
    ``status`` (``completed``/``failed``) always carries ``settled_at``, and
    ``failed`` always carries ``failure_reason``.
    """

    request_id: str
    topic: str
    goal: str | None = None
    status: str
    session_id: str | None = None
    mind_map_id: str | None = None
    calibration_ready_at: str | None = None
    # What the notification path attests about the calibration notice, and when
    # a delivery channel accepted it. ``calibration_notice_outcome`` carries the
    # attention ledger's own word ("delivered", "failed", "deferred",
    # "suppressed", "coalesced") or one of two sentinels for the state of our
    # evidence rather than the dispatch: "no_record" (ledger read, nothing
    # there) and "unproven" (ledger not readable, or no session to read for).
    # ``calibration_notice_accepted_at`` is set only for "delivered", and means
    # a channel accepted the message, not that the owner read it.
    calibration_notice_outcome: str | None = None
    calibration_notice_accepted_at: str | None = None
    failure_reason: str | None = None
    requested_at: str
    triggered_at: str | None = None
    settled_at: str | None = None
    updated_at: str


class CurriculumRequestStatusResponse(BaseModel):
    """Read-only status envelope for curriculum request receipts.

    ``receipts_available`` is ``False`` when the receipt store cannot be read
    (e.g. the education chain has not been migrated yet). Callers must render
    that as "status unavailable" rather than as "no request in flight".
    """

    receipts_available: bool = True
    receipt: CurriculumRequestReceipt | None = None


class AnalyticsTrendEntry(BaseModel):
    """One snapshot entry in an analytics trend time-series."""

    id: str | None = None
    mind_map_id: str
    snapshot_date: str
    metrics: dict[str, Any] = {}
    created_at: str | None = None


class AnalyticsTrendResponse(BaseModel):
    """Analytics trend time-series for a mind map."""

    mind_map_id: str
    days: int
    trend: list[AnalyticsTrendEntry] = []


class StrugglingNodeEntry(BaseModel):
    """A concept node identified as struggling."""

    node_id: str
    node_label: str
    mastery_score: float
    mastery_status: str
    reason: str


class StrugglingNodesResponse(BaseModel):
    """List of struggling nodes for a mind map."""

    mind_map_id: str
    nodes: list[StrugglingNodeEntry] = []
