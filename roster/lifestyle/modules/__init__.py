"""Lifestyle module — MCP tools for the taste ledger (bu-2jtfw.10).

Registers the five taste-ledger tools (backfill, list, get, summary, and
owner-verdict) that read/write ``works``/``taste_signals``/``verdicts`` in the
lifestyle schema. The resolver/projector logic itself lives in
``butlers.tools.lifestyle.taste_ledger`` (loaded dynamically from
``roster/lifestyle/tools/`` per ``butlers.tools._loader``); this module only
wires it to MCP.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class LifestyleModuleConfig(BaseModel):
    """Configuration for the Lifestyle module (no settings yet)."""


class LifestyleModule(Module):
    """Taste-ledger MCP tools for the Lifestyle butler."""

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "lifestyle"

    @property
    def config_schema(self) -> type[BaseModel]:
        return LifestyleModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # taste-ledger tables live in roster/lifestyle/migrations

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        self._db = db

    async def on_shutdown(self) -> None:
        self._db = None

    def _get_pool(self) -> Any:
        if self._db is None:
            raise RuntimeError("LifestyleModule not initialised -- no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        self._db = db
        from butlers.tools.lifestyle import taste_ledger as ledger

        @mcp.tool()
        async def taste_backfill_ledger() -> dict[str, Any]:
            """Project connector evidence (Spotify sessions + track plays) into
            the taste ledger. Deterministic and safe to call repeatedly — a
            second call over the same evidence creates nothing new.
            """
            sessions_result = await ledger.backfill_from_listening_sessions(self._get_pool())
            plays_result = await ledger.backfill_from_track_plays(self._get_pool())
            return {
                "sessions": {
                    "works_created": sessions_result.works_created,
                    "signals_created": sessions_result.signals_created,
                },
                "track_plays": {
                    "works_created": plays_result.works_created,
                    "signals_created": plays_result.signals_created,
                },
            }

        @mcp.tool()
        async def taste_list_works(kind: str | None = None, limit: int = 20) -> dict[str, Any]:
            """List works in the taste ledger, optionally filtered by kind
            (e.g. 'track'). Ordered newest-first.
            """
            pool = self._get_pool()
            limit = max(1, min(limit, 200))
            if kind is not None:
                rows = await pool.fetch(
                    "SELECT id, kind, title, external_ids, created_at FROM works "
                    "WHERE kind = $1 ORDER BY created_at DESC LIMIT $2",
                    kind,
                    limit,
                )
            else:
                rows = await pool.fetch(
                    "SELECT id, kind, title, external_ids, created_at FROM works "
                    "ORDER BY created_at DESC LIMIT $1",
                    limit,
                )
            return {
                "works": [
                    {
                        "id": str(row["id"]),
                        "kind": row["kind"],
                        "title": row["title"],
                        "external_ids": row["external_ids"],
                        "created_at": row["created_at"].isoformat(),
                    }
                    for row in rows
                ]
            }

        @mcp.tool()
        async def taste_get_work(work_id: str) -> dict[str, Any]:
            """Return one work with its recent taste_signals and verdicts."""
            pool = self._get_pool()
            work = await pool.fetchrow(
                "SELECT id, kind, title, external_ids, metadata, created_at"
                " FROM works WHERE id = $1",
                work_id,
            )
            if work is None:
                return {"error": f"No work found with id {work_id}"}

            signals = await pool.fetch(
                "SELECT signal_kind, source_table, occurred_at, strength, metadata "
                "FROM taste_signals WHERE work_id = $1 ORDER BY occurred_at DESC LIMIT 50",
                work_id,
            )
            verdicts = await pool.fetch(
                "SELECT predicate, verdict_text, source, created_at "
                "FROM verdicts WHERE work_id = $1 ORDER BY created_at DESC",
                work_id,
            )
            return {
                "work": {
                    "id": str(work["id"]),
                    "kind": work["kind"],
                    "title": work["title"],
                    "external_ids": work["external_ids"],
                    "metadata": work["metadata"],
                    "created_at": work["created_at"].isoformat(),
                },
                "signals": [
                    {
                        "signal_kind": s["signal_kind"],
                        "source_table": s["source_table"],
                        "occurred_at": s["occurred_at"].isoformat(),
                        "strength": s["strength"],
                        "metadata": s["metadata"],
                    }
                    for s in signals
                ],
                "verdicts": [
                    {
                        "predicate": v["predicate"],
                        "verdict_text": v["verdict_text"],
                        "source": v["source"],
                        "created_at": v["created_at"].isoformat(),
                    }
                    for v in verdicts
                ],
            }

        @mcp.tool()
        async def taste_get_summary() -> dict[str, Any]:
            """Return ledger-wide counts: works by kind, and signal totals by kind."""
            pool = self._get_pool()
            works_by_kind = await pool.fetch(
                "SELECT kind, count(*) AS n FROM works GROUP BY kind ORDER BY n DESC"
            )
            signals_by_kind = await pool.fetch(
                "SELECT signal_kind, count(*) AS n FROM taste_signals"
                " GROUP BY signal_kind ORDER BY n DESC"
            )
            total_works = await pool.fetchval("SELECT count(*) FROM works")
            total_signals = await pool.fetchval("SELECT count(*) FROM taste_signals")
            total_verdicts = await pool.fetchval("SELECT count(*) FROM verdicts")
            return {
                "total_works": total_works,
                "total_signals": total_signals,
                "total_verdicts": total_verdicts,
                "works_by_kind": {row["kind"]: row["n"] for row in works_by_kind},
                "signals_by_kind": {row["signal_kind"]: row["n"] for row in signals_by_kind},
            }

        @mcp.tool()
        async def taste_add_verdict(
            verdict_text: str,
            predicate: str = "opinion",
            work_id: str | None = None,
        ) -> dict[str, Any]:
            """Record an owner-asserted taste opinion (a verdict), optionally
            tied to a specific work. Never asserted by an LLM on the owner's
            behalf -- this tool is for recording what the owner actually said.
            """
            pool = self._get_pool()
            row = await pool.fetchrow(
                """
                INSERT INTO verdicts (work_id, predicate, verdict_text, source)
                VALUES ($1, $2, $3, 'owner_assertion')
                RETURNING id, created_at
                """,
                work_id,
                predicate,
                verdict_text,
            )
            return {"id": str(row["id"]), "created_at": row["created_at"].isoformat()}
