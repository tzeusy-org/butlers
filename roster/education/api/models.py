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


class StatusUpdateRequest(BaseModel):
    """Request body for updating a mind map's status."""

    status: str


class CurriculumRequestBody(BaseModel):
    """Request body for submitting a new curriculum request."""

    topic: str
    goal: str | None = None


class CurriculumRequestResponse(BaseModel):
    """Response body for a submitted curriculum request."""

    status: str
    topic: str
