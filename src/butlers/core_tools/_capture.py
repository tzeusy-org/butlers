"""capture() -- the second brain's intake verb (bu-2jtfw.9).

A fleet-wide core tool: every butler can call ``capture()`` to durably
record a thought before deciding what to do with it. The write to
``public.captures`` as a ``held`` row is synchronous and always succeeds (or
raises loudly) before any routing is attempted, so a capture_id is always
handed back even if the routing step that follows never completes -- a dead
session, a crash mid-classification -- leaving the row discoverable via
``GET /api/captures?state=held`` rather than lost.

Routing (writing the actual target row and promoting the receipt to
``routed``) is performed inline, synchronously, when ``capture()`` runs on
the General butler itself -- the common case, since General is "the default
home and router of last resort" (MANIFESTO amendment) and owns the only
target table this slice routes into (``collection_items``). When
``capture()`` runs on another butler's daemon, the ownership-refusal check
still runs (it is pure and universal), but the write-through to General's
target table is not attempted from a foreign schema-scoped pool -- the row
is left ``held`` by design, not as a failure. Wiring a cross-butler dispatch
for that path (mirroring ``dispatch_via_switchboard_route``) is real
follow-up work, not a stub pretending to work; see the PR description.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from butlers.core.ownership_refusal import check_ownership_refusal
from butlers.core_tools._base import ToolContext

_CAPTURE_CATALOG_SUMMARY_MAX_LEN = 500


async def _admit_capture_to_catalog(
    pool: Any, *, source_id: uuid.UUID, source_butler: str, summary: str
) -> None:
    """Upsert a routed capture into public.memory_catalog for fleet visibility.

    Best-effort: catalog admission failing must never fail the capture
    itself (the capture row and its target row are already durably written).
    """
    truncated = summary[:_CAPTURE_CATALOG_SUMMARY_MAX_LEN]
    try:
        await pool.execute(
            """
            INSERT INTO public.memory_catalog
                (source_schema, source_table, source_id, source_butler,
                 tenant_id, summary, search_vector, memory_type)
            VALUES ('general', 'collection_items', $1, $2,
                    'shared', $3, to_tsvector('english', $3), 'capture')
            ON CONFLICT (source_schema, source_table, source_id) DO UPDATE
            SET summary = EXCLUDED.summary,
                search_vector = EXCLUDED.search_vector,
                updated_at = now()
            """,
            source_id,
            source_butler,
            truncated,
        )
    except Exception:  # noqa: BLE001 - best-effort, mirrors memory catalog write-behind
        import logging

        logging.getLogger(__name__).warning(
            "capture(): memory_catalog admission failed for capture target %s",
            source_id,
            exc_info=True,
        )


async def _route_into_general(pool: Any, *, channel: str, content: str) -> dict[str, Any]:
    """Write the capture's content into General's default 'notes' collection.

    Returns the routed target pointer. Raises on failure -- the caller is
    responsible for catching this and leaving the capture 'held'.
    """
    from butlers.tools.general.items import item_create

    item_id = await item_create(
        pool,
        "notes",
        {"content": content, "channel": channel},
    )
    return {
        "routed_kind": "note",
        "target_schema": "general",
        "target_table": "collection_items",
        "target_row_id": item_id,
    }


def register_capture_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register the ``capture`` core tool."""
    pool = ctx.pool
    butler_name = ctx.butler_name

    @_core_tool("infra")
    async def capture(
        channel: str,
        content: str,
        mutation_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Durably record a thought before deciding what to do with it.

        Always writes a ``held`` receipt row synchronously and returns
        ``capture_id`` before any routing is attempted -- a dead session
        after this point leaves the row ``held``, discoverable via
        ``GET /api/captures?state=held``, never lost. Content that plainly
        belongs to another butler's owned domain (e.g. a bank transaction)
        is refused, naming that butler and the tool to use, instead of being
        absorbed into General. Otherwise the capture is routed into
        General's default collection and the receipt cites the written row.

        ``mutation_id`` makes a transport retry idempotent: the same
        ``mutation_id`` always returns the same ``capture_id`` and does not
        re-run routing.
        """
        if pool is None:
            return {"status": "unavailable", "reason": "database_unavailable"}

        inserted = await pool.fetchrow(
            """
            INSERT INTO public.captures (channel, content, mutation_id, source_butler)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (mutation_id) DO NOTHING
            RETURNING capture_id, receipt_state
            """,
            channel,
            content,
            mutation_id,
            butler_name,
        )
        if inserted is None:
            # mutation_id replay: return the existing row's current state
            # unchanged rather than re-running routing.
            existing = await pool.fetchrow(
                "SELECT capture_id, receipt_state, refusal_reason, target_row_id"
                " FROM public.captures WHERE mutation_id = $1",
                mutation_id,
            )
            return {
                "capture_id": str(existing["capture_id"]),
                "receipt_state": existing["receipt_state"],
                "refusal_reason": existing["refusal_reason"],
                "target_row_id": (
                    str(existing["target_row_id"]) if existing["target_row_id"] else None
                ),
            }

        capture_id = inserted["capture_id"]

        refusal = check_ownership_refusal(content)
        if refusal is not None:
            await pool.execute(
                "UPDATE public.captures SET receipt_state = 'refused', refusal_reason = $2,"
                " updated_at = now() WHERE capture_id = $1",
                capture_id,
                refusal.reason,
            )
            return {
                "capture_id": str(capture_id),
                "receipt_state": "refused",
                "refusal_reason": refusal.reason,
            }

        if butler_name != "general":
            # Routing is only wired for the General butler's own schema-
            # scoped pool in this slice (see module docstring). The capture
            # stays legitimately 'held' -- not a failure, just not yet routed.
            return {
                "capture_id": str(capture_id),
                "receipt_state": "held",
                "refusal_reason": None,
            }

        try:
            routed = await _route_into_general(pool, channel=channel, content=content)
        except Exception as exc:  # noqa: BLE001 - convert any routing failure to a held receipt
            await pool.execute(
                "UPDATE public.captures SET refusal_reason = $2, updated_at = now()"
                " WHERE capture_id = $1 AND receipt_state = 'held'",
                capture_id,
                f"target_write_failed:{type(exc).__name__}: {exc}",
            )
            return {
                "capture_id": str(capture_id),
                "receipt_state": "held",
                "refusal_reason": f"target_write_failed:{type(exc).__name__}: {exc}",
            }

        await pool.execute(
            """
            UPDATE public.captures
            SET receipt_state = 'routed', routed_kind = $2, target_schema = $3,
                target_table = $4, target_row_id = $5, updated_at = now()
            WHERE capture_id = $1
            """,
            capture_id,
            routed["routed_kind"],
            routed["target_schema"],
            routed["target_table"],
            routed["target_row_id"],
        )
        await _admit_capture_to_catalog(
            pool,
            source_id=routed["target_row_id"],
            source_butler="general",
            summary=content,
        )

        return {
            "capture_id": str(capture_id),
            "receipt_state": "routed",
            "routed_kind": routed["routed_kind"],
            "target_schema": routed["target_schema"],
            "target_table": routed["target_table"],
            "target_row_id": str(routed["target_row_id"]),
        }
