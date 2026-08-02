"""Native Switchboard handlers for approved connector lifecycle commands."""

from __future__ import annotations

from typing import Any


async def connector_disconnect(
    pool: Any,
    connector_type: str,
    endpoint_identity: str,
) -> dict[str, Any]:
    """Soft-delete one connector registry row after an approval has resolved.

    The dashboard submission endpoint only creates the pending action.  This
    handler is intentionally separate so an approved action invokes the real
    schema owner through the daemon executor instead of re-entering the
    submission/gate path.
    """
    updated = await pool.fetchrow(
        """
        UPDATE connector_registry
        SET deleted_at = now()
        WHERE connector_type = $1
          AND endpoint_identity = $2
          AND deleted_at IS NULL
        RETURNING connector_type, endpoint_identity, deleted_at
        """,
        connector_type,
        endpoint_identity,
    )
    if updated is not None:
        return {
            "success": True,
            "connector_type": str(updated["connector_type"]),
            "endpoint_identity": str(updated["endpoint_identity"]),
            "status": "disconnected",
        }

    # A concurrent/dispatched equivalent action may already have achieved the
    # requested state.  It is a truthful idempotent success, not a second
    # mutation. A genuinely absent identity remains an execution failure.
    existing = await pool.fetchrow(
        """
        SELECT connector_type, endpoint_identity, deleted_at
        FROM connector_registry
        WHERE connector_type = $1 AND endpoint_identity = $2
        """,
        connector_type,
        endpoint_identity,
    )
    if existing is None:
        raise ValueError(
            f"Connector '{connector_type}/{endpoint_identity}' no longer exists; "
            "it was not disconnected"
        )

    if existing["deleted_at"] is not None:
        return {
            "success": True,
            "connector_type": str(existing["connector_type"]),
            "endpoint_identity": str(existing["endpoint_identity"]),
            "status": "already_disconnected",
        }

    # The UPDATE saw no row although the select found a live row. Treat this
    # unusual race/DB result as a failure rather than claiming completion.
    raise RuntimeError(
        f"Connector '{connector_type}/{endpoint_identity}' could not be disconnected"
    )
