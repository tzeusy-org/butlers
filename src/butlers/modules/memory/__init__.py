"""Memory module — wires memory domain tools into the butler's MCP server.

Registers 12 MCP tools that delegate to the existing implementations in
``butlers.tools.memory``. The tool closures strip ``pool`` and
``embedding_engine`` from the MCP-visible signature and inject them from
module state at call time.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class MemoryModuleConfig(BaseModel):
    """Configuration for the Memory module."""

    class RetrievalConfig(BaseModel):
        """Config for memory retrieval and context assembly defaults."""

        context_token_budget: int = 3000
        default_limit: int = 20
        default_mode: str = "hybrid"
        score_weights: dict[str, float] = Field(
            default_factory=lambda: {
                "relevance": 0.4,
                "importance": 0.3,
                "recency": 0.2,
                "confidence": 0.1,
            }
        )

    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)


class MemoryModule(Module):
    """Memory module providing 12 MCP tools for memory CRUD and retrieval."""

    def __init__(self) -> None:
        self._db: Any = None
        self._embedding_engine: Any = None
        self._config: MemoryModuleConfig = MemoryModuleConfig()

    @property
    def name(self) -> str:
        return "memory"

    @property
    def config_schema(self) -> type[BaseModel]:
        return MemoryModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return "memory"

    async def on_startup(self, config: Any, db: Any) -> None:
        """Store the Database reference for later pool access."""
        self._db = db
        if isinstance(config, MemoryModuleConfig):
            self._config = config

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None
        self._embedding_engine = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("MemoryModule not initialised — no DB available")
        return self._db.pool

    def _get_embedding_engine(self):
        """Lazy-load and return the shared embedding engine singleton."""
        if self._embedding_engine is None:
            from butlers.tools.memory import get_embedding_engine

            self._embedding_engine = get_embedding_engine()
        return self._embedding_engine

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all 12 memory MCP tools."""
        self._db = db
        if isinstance(config, MemoryModuleConfig):
            self._config = config
        module = self  # capture for closures
        default_context_budget = self._config.retrieval.context_token_budget

        # Import sub-modules (not individual functions) to avoid name collisions
        # between the MCP closure names and the imported symbols.
        # Deferred to avoid import-time side effects (sentence_transformers).
        from butlers.tools.memory import context as _context
        from butlers.tools.memory import feedback as _feedback
        from butlers.tools.memory import management as _management
        from butlers.tools.memory import reading as _reading
        from butlers.tools.memory import writing as _writing

        # --- Writing tools ---

        @mcp.tool()
        async def memory_store_episode(
            content: str,
            butler: str,
            session_id: str | None = None,
            importance: float = 5.0,
        ) -> dict[str, Any]:
            """Store a raw episode from a CC session."""
            return await _writing.memory_store_episode(
                module._get_pool(),
                content,
                butler,
                session_id=session_id,
                importance=importance,
            )

        @mcp.tool()
        async def memory_store_fact(
            subject: str,
            predicate: str,
            content: str,
            importance: float = 5.0,
            permanence: str = "standard",
            scope: str = "global",
            tags: list[str] | None = None,
        ) -> dict[str, Any]:
            """Store a fact, automatically superseding any existing match."""
            return await _writing.memory_store_fact(
                module._get_pool(),
                module._get_embedding_engine(),
                subject,
                predicate,
                content,
                importance=importance,
                permanence=permanence,
                scope=scope,
                tags=tags,
            )

        @mcp.tool()
        async def memory_store_rule(
            content: str,
            scope: str = "global",
            tags: list[str] | None = None,
        ) -> dict[str, Any]:
            """Store a new behavioral rule as a candidate."""
            return await _writing.memory_store_rule(
                module._get_pool(),
                module._get_embedding_engine(),
                content,
                scope=scope,
                tags=tags,
            )

        # --- Reading tools ---

        @mcp.tool()
        async def memory_search(
            query: str,
            types: list[str] | None = None,
            scope: str | None = None,
            mode: str = "hybrid",
            limit: int = 10,
            min_confidence: float = 0.2,
        ) -> list[dict[str, Any]]:
            """Search across memory types using hybrid, semantic, or keyword mode."""
            return await _reading.memory_search(
                module._get_pool(),
                module._get_embedding_engine(),
                query,
                types=types,
                scope=scope,
                mode=mode,
                limit=limit,
                min_confidence=min_confidence,
            )

        @mcp.tool()
        async def memory_recall(
            topic: str,
            scope: str | None = None,
            limit: int = 10,
        ) -> list[dict[str, Any]]:
            """High-level composite-scored retrieval of relevant facts and rules."""
            return await _reading.memory_recall(
                module._get_pool(),
                module._get_embedding_engine(),
                topic,
                scope=scope,
                limit=limit,
            )

        @mcp.tool()
        async def memory_get(
            memory_type: str,
            memory_id: str,
        ) -> dict[str, Any] | None:
            """Retrieve a specific memory by type and ID."""
            return await _reading.memory_get(
                module._get_pool(),
                memory_type,
                memory_id,
            )

        # --- Feedback tools ---

        @mcp.tool()
        async def memory_confirm(
            memory_type: str,
            memory_id: str,
        ) -> dict[str, Any]:
            """Confirm a fact or rule is still accurate, resetting confidence decay."""
            return await _feedback.memory_confirm(
                module._get_pool(),
                memory_type,
                memory_id,
            )

        @mcp.tool()
        async def memory_mark_helpful(
            rule_id: str,
        ) -> dict[str, Any]:
            """Report a rule was applied successfully."""
            return await _feedback.memory_mark_helpful(
                module._get_pool(),
                rule_id,
            )

        @mcp.tool()
        async def memory_mark_harmful(
            rule_id: str,
            reason: str | None = None,
        ) -> dict[str, Any]:
            """Report a rule caused problems."""
            return await _feedback.memory_mark_harmful(
                module._get_pool(),
                rule_id,
                reason=reason,
            )

        # --- Management tools ---

        @mcp.tool()
        async def memory_forget(
            memory_type: str,
            memory_id: str,
        ) -> dict[str, Any]:
            """Soft-delete a memory by type and ID."""
            return await _management.memory_forget(
                module._get_pool(),
                memory_type,
                memory_id,
            )

        @mcp.tool()
        async def memory_stats(
            scope: str | None = None,
        ) -> dict[str, Any]:
            """System health indicators across all memory types."""
            return await _management.memory_stats(
                module._get_pool(),
                scope=scope,
            )

        # --- Context tool ---

        @mcp.tool()
        async def memory_context(
            trigger_prompt: str,
            butler: str,
            token_budget: int = default_context_budget,
        ) -> str:
            """Build a memory context block for injection into a CC system prompt."""
            return await _context.memory_context(
                module._get_pool(),
                module._get_embedding_engine(),
                trigger_prompt,
                butler,
                token_budget=token_budget,
            )
