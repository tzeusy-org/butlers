"""Server-authorized, Switchboard-brokered memory catalog dereference."""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field

from butlers.core_tools._base import ToolContext
from butlers.core_tools._switchboard_route_dispatch import dispatch_via_switchboard_route


def _classify_fetch_route(raw: Any) -> tuple[dict[str, Any] | None, str | None, bool]:
    if not isinstance(raw, dict):
        return None, "Switchboard returned an invalid catalog-fetch envelope.", False
    if raw.get("error"):
        return None, str(raw["error"]), bool(raw.get("retryable"))
    result = raw.get("result")
    if result is None:
        return None, None, False
    if not isinstance(result, dict):
        return None, "Owning butler returned an invalid memory payload.", False
    return result, None, False


async def _catalog_pointer(
    pool: Any,
    *,
    source_schema: str,
    source_table: str,
    source_id: uuid.UUID,
) -> Any:
    return await pool.fetchrow(
        """
        SELECT source_butler, memory_type, sensitivity
        FROM public.memory_catalog
        WHERE tenant_id = 'shared'
          AND source_schema = $1
          AND source_table = $2
          AND source_id = $3
          AND invalid_at IS NULL
        """,
        source_schema,
        source_table,
        source_id,
    )


def register_memory_catalog_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register the read-only catalog provenance fetch tool."""
    pool = ctx.pool
    daemon = ctx.daemon

    @_core_tool("infra")
    async def memory_catalog_fetch(
        source_schema: Annotated[str, Field(description="Catalog provenance source schema.")],
        source_table: Annotated[
            str, Field(description="Catalog provenance source table: 'facts' or 'rules'.")
        ],
        source_id: Annotated[str, Field(description="Catalog provenance source UUID.")],
    ) -> dict[str, Any]:
        """Fetch one canonical catalog item under this butler's held authority.

        The server loads ``catalog_read_sensitivity`` from this butler's
        ``runtime_config`` row. Above-ceiling pointers return a fixed withheld
        marker. Authorized cross-butler reads are routed through Switchboard to
        the owning butler's existing ``memory_get`` tool.
        """
        if pool is None:
            return {"status": "unavailable", "reason": "database_unavailable"}
        if source_table not in {"facts", "rules"}:
            raise ValueError("source_table must be 'facts' or 'rules'")
        parsed_source_id = uuid.UUID(source_id)

        memory_search = importlib.import_module("butlers.modules.memory.search")
        read_policy = await memory_search.load_catalog_read_policy(pool)
        pointer = await _catalog_pointer(
            pool,
            source_schema=source_schema,
            source_table=source_table,
            source_id=parsed_source_id,
        )
        if pointer is None:
            return {"status": "not_found"}

        sensitivity = pointer["sensitivity"] or memory_search.DEFAULT_CATALOG_SENSITIVITY
        if sensitivity not in read_policy.allowed_sensitivities:
            return {"status": "withheld", "reason": "sensitivity"}

        source_butler = pointer["source_butler"]
        if not isinstance(source_butler, str) or not source_butler.strip():
            return {"status": "not_found"}

        result, error, retryable = await dispatch_via_switchboard_route(
            getattr(daemon, "switchboard_client", None),
            pool,
            ctx.butler_name,
            target_butler=source_butler,
            tool_name="memory_get",
            args={"memory_type": pointer["memory_type"], "memory_id": str(parsed_source_id)},
            classify=_classify_fetch_route,
            route_purpose="memory catalog fetch",
        )
        if error is not None:
            response: dict[str, Any] = {
                "status": "unavailable",
                "reason": "source_unavailable",
            }
            if retryable:
                response["retryable"] = True
            return response
        if result is None:
            return {"status": "not_found"}
        return {
            "status": "ok",
            "source_schema": source_schema,
            "source_table": source_table,
            "source_id": str(parsed_source_id),
            "memory": result,
        }
