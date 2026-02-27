"""Education module — wires education domain tools into the butler's MCP server.

Registers 33 MCP tools that delegate to the existing implementations in
``butlers.tools.education``. The tool closures strip ``pool`` and scheduler
callbacks from the MCP-visible signature and inject them from module state
at call time.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class EducationModuleConfig(BaseModel):
    """Configuration for the Education module (empty — no settings needed yet)."""


class EducationModule(Module):
    """Education module providing 33 MCP tools for mind maps, teaching flows,
    mastery tracking, spaced repetition, diagnostics, curriculum, and analytics.
    """

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "education"

    @property
    def config_schema(self) -> type[BaseModel]:
        return EducationModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # education tables already exist via separate migrations

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store the Database reference for later pool access."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("EducationModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:  # noqa: C901
        """Register all education MCP tools."""
        self._db = db
        module = self  # capture for closures

        # Import sub-modules (deferred to avoid import-time side effects)
        from butlers.core.scheduler import schedule_create as _sched_create
        from butlers.core.scheduler import schedule_delete as _sched_delete
        from butlers.tools.education import analytics as _analytics
        from butlers.tools.education import curriculum as _curriculum
        from butlers.tools.education import diagnostic as _diagnostic
        from butlers.tools.education import mastery as _mastery
        from butlers.tools.education import mind_map_edges as _edges
        from butlers.tools.education import mind_map_nodes as _nodes
        from butlers.tools.education import mind_map_queries as _queries
        from butlers.tools.education import mind_maps as _maps
        from butlers.tools.education import spaced_repetition as _sr
        from butlers.tools.education import teaching_flows as _flows

        # --- Scheduler callback factories ---
        # Education tools use callback injection for scheduler integration.
        # The callbacks adapt the core scheduler API (pool + positional args)
        # into the keyword-only interface expected by education tool functions.

        def _make_schedule_create():
            async def _bound(**kwargs: Any) -> str:
                pool = module._get_pool()
                name = kwargs.pop("name")
                cron = kwargs.pop("cron")
                prompt = kwargs.pop("prompt", None)
                task_id = await _sched_create(pool, name, cron, prompt, **kwargs)
                return str(task_id)

            return _bound

        def _make_schedule_delete():
            async def _bound(name: str) -> None:
                pool = module._get_pool()
                row = await pool.fetchrow("SELECT id FROM scheduled_tasks WHERE name = $1", name)
                if row is not None:
                    await _sched_delete(pool, row["id"])

            return _bound

        bound_schedule_create = _make_schedule_create()
        bound_schedule_delete = _make_schedule_delete()

        # =================================================================
        # Mind Map tools
        # =================================================================

        @mcp.tool()
        async def mind_map_create(title: str) -> str:
            """Create a new mind map with status='active'."""
            return await _maps.mind_map_create(module._get_pool(), title)

        @mcp.tool()
        async def mind_map_get(mind_map_id: str) -> dict[str, Any] | None:
            """Retrieve a mind map by ID, including its nodes and edges."""
            return await _maps.mind_map_get(module._get_pool(), mind_map_id)

        @mcp.tool()
        async def mind_map_list(status: str | None = None) -> list[dict[str, Any]]:
            """List mind maps, optionally filtered by status."""
            return await _maps.mind_map_list(module._get_pool(), status=status)

        @mcp.tool()
        async def mind_map_update_status(mind_map_id: str, status: str) -> None:
            """Update the status of a mind map."""
            await _maps.mind_map_update_status(module._get_pool(), mind_map_id, status)

        # =================================================================
        # Mind Map Node tools
        # =================================================================

        @mcp.tool()
        async def mind_map_node_create(
            mind_map_id: str,
            label: str,
            description: str | None = None,
            depth: int | None = None,
            effort_minutes: int | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> str:
            """Create a new node in a mind map."""
            return await _nodes.mind_map_node_create(
                module._get_pool(),
                mind_map_id,
                label,
                description=description,
                depth=depth,
                effort_minutes=effort_minutes,
                metadata=metadata,
            )

        @mcp.tool()
        async def mind_map_node_get(node_id: str) -> dict[str, Any] | None:
            """Retrieve a single node by ID."""
            return await _nodes.mind_map_node_get(module._get_pool(), node_id)

        @mcp.tool()
        async def mind_map_node_list(
            mind_map_id: str,
            mastery_status: str | None = None,
        ) -> list[dict[str, Any]]:
            """List nodes in a mind map, optionally filtered by mastery_status."""
            return await _nodes.mind_map_node_list(
                module._get_pool(), mind_map_id, mastery_status=mastery_status
            )

        @mcp.tool()
        async def mind_map_node_update(
            node_id: str,
            mastery_score: float | None = None,
            mastery_status: str | None = None,
            ease_factor: float | None = None,
            repetitions: int | None = None,
            next_review_at: str | None = None,
            last_reviewed_at: str | None = None,
            effort_minutes: int | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            """Update writable fields on a node."""
            fields = {
                k: v
                for k, v in {
                    "mastery_score": mastery_score,
                    "mastery_status": mastery_status,
                    "ease_factor": ease_factor,
                    "repetitions": repetitions,
                    "next_review_at": next_review_at,
                    "last_reviewed_at": last_reviewed_at,
                    "effort_minutes": effort_minutes,
                    "metadata": metadata,
                }.items()
                if v is not None
            }
            await _nodes.mind_map_node_update(module._get_pool(), node_id, **fields)

        # =================================================================
        # Mind Map Edge tools
        # =================================================================

        @mcp.tool()
        async def mind_map_edge_create(
            parent_node_id: str,
            child_node_id: str,
            edge_type: str = "prerequisite",
        ) -> None:
            """Create an edge from parent to child in the mind map DAG."""
            await _edges.mind_map_edge_create(
                module._get_pool(), parent_node_id, child_node_id, edge_type=edge_type
            )

        @mcp.tool()
        async def mind_map_edge_delete(parent_node_id: str, child_node_id: str) -> None:
            """Delete an edge between two nodes (idempotent)."""
            await _edges.mind_map_edge_delete(module._get_pool(), parent_node_id, child_node_id)

        # =================================================================
        # Mind Map Query tools
        # =================================================================

        @mcp.tool()
        async def mind_map_frontier(mind_map_id: str) -> list[dict[str, Any]]:
            """Return frontier nodes (prerequisites mastered, node not yet mastered)."""
            return await _queries.mind_map_frontier(module._get_pool(), mind_map_id)

        @mcp.tool()
        async def mind_map_subtree(node_id: str) -> list[dict[str, Any]]:
            """Return all descendants of a node (recursive CTE)."""
            return await _queries.mind_map_subtree(module._get_pool(), node_id)

        # =================================================================
        # Teaching Flow tools
        # =================================================================

        @mcp.tool()
        async def teaching_flow_start(
            topic: str,
            goal: str | None = None,
        ) -> dict[str, Any]:
            """Start a new teaching flow for a topic."""
            return await _flows.teaching_flow_start(module._get_pool(), topic, goal=goal)

        @mcp.tool()
        async def teaching_flow_get(mind_map_id: str) -> dict[str, Any] | None:
            """Read current flow state from the KV store."""
            return await _flows.teaching_flow_get(module._get_pool(), mind_map_id)

        @mcp.tool()
        async def teaching_flow_advance(mind_map_id: str) -> dict[str, Any]:
            """Advance the teaching flow state machine to the next state."""
            return await _flows.teaching_flow_advance(module._get_pool(), mind_map_id)

        @mcp.tool()
        async def teaching_flow_abandon(mind_map_id: str) -> None:
            """Abandon a teaching flow and clean up pending review schedules."""
            await _flows.teaching_flow_abandon(
                module._get_pool(),
                mind_map_id,
                schedule_delete=bound_schedule_delete,
            )

        @mcp.tool()
        async def teaching_flow_list(
            status: str | None = None,
        ) -> list[dict[str, Any]]:
            """List teaching flows with optional status filter."""
            return await _flows.teaching_flow_list(module._get_pool(), status=status)

        # =================================================================
        # Mastery tools
        # =================================================================

        @mcp.tool()
        async def mastery_record_response(
            node_id: str,
            mind_map_id: str,
            question_text: str,
            user_answer: str | None,
            quality: int,
            response_type: str = "review",
            session_id: str | None = None,
        ) -> str:
            """Record a quiz response and update node mastery score and status."""
            return await _mastery.mastery_record_response(
                module._get_pool(),
                node_id,
                mind_map_id,
                question_text,
                user_answer,
                quality,
                response_type=response_type,
                session_id=session_id,
            )

        @mcp.tool()
        async def mastery_get_node_history(
            node_id: str,
            limit: int | None = None,
        ) -> list[dict[str, Any]]:
            """Return quiz response history for a node, most recent first."""
            return await _mastery.mastery_get_node_history(module._get_pool(), node_id, limit=limit)

        @mcp.tool()
        async def mastery_get_map_summary(mind_map_id: str) -> dict[str, Any]:
            """Return aggregate mastery statistics for all nodes in a mind map."""
            return await _mastery.mastery_get_map_summary(module._get_pool(), mind_map_id)

        @mcp.tool()
        async def mastery_detect_struggles(mind_map_id: str) -> list[dict[str, Any]]:
            """Identify nodes with declining or consistently low mastery."""
            return await _mastery.mastery_detect_struggles(module._get_pool(), mind_map_id)

        # =================================================================
        # Spaced Repetition tools
        # =================================================================

        @mcp.tool()
        async def spaced_repetition_record_response(
            node_id: str,
            mind_map_id: str,
            quality: int,
        ) -> dict[str, Any]:
            """Record a spaced-repetition review response and schedule the next review."""
            return await _sr.spaced_repetition_record_response(
                module._get_pool(),
                node_id,
                mind_map_id,
                quality,
                schedule_create=bound_schedule_create,
                schedule_delete=bound_schedule_delete,
            )

        @mcp.tool()
        async def spaced_repetition_pending_reviews(
            mind_map_id: str,
        ) -> list[dict[str, Any]]:
            """Return nodes due for spaced-repetition review (next_review_at <= now)."""
            return await _sr.spaced_repetition_pending_reviews(module._get_pool(), mind_map_id)

        @mcp.tool()
        async def spaced_repetition_schedule_cleanup(mind_map_id: str) -> int:
            """Remove all pending review schedules for a terminal mind map."""
            return await _sr.spaced_repetition_schedule_cleanup(
                module._get_pool(),
                mind_map_id,
                schedule_delete=bound_schedule_delete,
            )

        # =================================================================
        # Diagnostic tools
        # =================================================================

        @mcp.tool()
        async def diagnostic_start(mind_map_id: str) -> list[dict[str, Any]]:
            """Initialise a diagnostic assessment for a mind map."""
            return await _diagnostic.diagnostic_start(module._get_pool(), mind_map_id)

        @mcp.tool()
        async def diagnostic_record_probe(
            mind_map_id: str,
            node_id: str,
            quality: int,
            inferred_mastery: float,
        ) -> dict[str, Any]:
            """Record a diagnostic probe result and seed node mastery."""
            return await _diagnostic.diagnostic_record_probe(
                module._get_pool(), mind_map_id, node_id, quality, inferred_mastery
            )

        @mcp.tool()
        async def diagnostic_complete(mind_map_id: str) -> dict[str, Any]:
            """Finalise the diagnostic and transition flow state to PLANNING."""
            return await _diagnostic.diagnostic_complete(module._get_pool(), mind_map_id)

        # =================================================================
        # Curriculum tools
        # =================================================================

        @mcp.tool()
        async def curriculum_generate(
            mind_map_id: str,
            goal: str | None = None,
            diagnostic_results: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Validate a concept graph, run topological sort, assign learning sequence."""
            return await _curriculum.curriculum_generate(
                module._get_pool(),
                mind_map_id,
                goal=goal,
                diagnostic_results=diagnostic_results,
            )

        @mcp.tool()
        async def curriculum_replan(
            mind_map_id: str,
            reason: str | None = None,
        ) -> dict[str, Any]:
            """Re-compute learning sequence based on current mastery state."""
            return await _curriculum.curriculum_replan(
                module._get_pool(), mind_map_id, reason=reason
            )

        @mcp.tool()
        async def curriculum_next_node(mind_map_id: str) -> dict[str, Any] | None:
            """Return the frontier node with the lowest sequence number."""
            return await _curriculum.curriculum_next_node(module._get_pool(), mind_map_id)

        # =================================================================
        # Analytics tools
        # =================================================================

        @mcp.tool()
        async def analytics_get_snapshot(
            mind_map_id: str,
            snapshot_date: str | None = None,
        ) -> dict[str, Any] | None:
            """Return the latest (or specific-date) analytics snapshot for a mind map."""
            parsed_date: date | None = None
            if snapshot_date is not None:
                parsed_date = date.fromisoformat(snapshot_date)
            return await _analytics.analytics_get_snapshot(
                module._get_pool(), mind_map_id, date=parsed_date
            )

        @mcp.tool()
        async def analytics_get_trend(
            mind_map_id: str,
            days: int = 30,
        ) -> list[dict[str, Any]]:
            """Return a time-series of snapshots within the last N days."""
            return await _analytics.analytics_get_trend(module._get_pool(), mind_map_id, days=days)

        @mcp.tool()
        async def analytics_get_cross_topic() -> dict[str, Any]:
            """Return comparative analytics across all active mind maps."""
            return await _analytics.analytics_get_cross_topic(module._get_pool())
