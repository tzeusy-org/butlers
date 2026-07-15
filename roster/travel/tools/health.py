"""Privacy-safe Health medication access routed through the Switchboard."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg
from pydantic import ValidationError

from butlers.core.permissions import CROSS_BUTLER_PERMISSION, check_permission
from butlers.health_medication_contract import (
    MedicationTravelErrorCode,
    MedicationTravelSnapshot,
)

logger = logging.getLogger(__name__)

_ROUTE_TIMEOUT_S = 10.0


def _failure(*, code: MedicationTravelErrorCode, message: str, retryable: bool) -> dict[str, Any]:
    return MedicationTravelSnapshot.failure(
        code=code,
        message=message,
        retryable=retryable,
    ).model_dump(mode="json")


async def request_health_medication_snapshot(
    pool: asyncpg.Pool,
    switchboard_client: Any | None,
    *,
    timeout_s: float = _ROUTE_TIMEOUT_S,
) -> dict[str, Any]:
    """Request Health's active medication snapshot through Switchboard MCP."""
    permission = await check_permission(pool, "travel", CROSS_BUTLER_PERMISSION)
    if not permission.allowed:
        return _failure(
            code="permission_denied",
            message="Travel is not permitted to request Health medication data.",
            retryable=False,
        )

    if switchboard_client is None:
        return _failure(
            code="switchboard_unavailable",
            message="Switchboard is unavailable for the Health medication request.",
            retryable=True,
        )

    route_args = {
        "target_butler": "health",
        "tool_name": "medication_travel_snapshot",
        "args": {},
        "source_butler": "travel",
    }
    try:
        result = await asyncio.wait_for(
            switchboard_client.call_tool("route", route_args),
            timeout=timeout_s,
        )
    except Exception:
        logger.warning(
            "Travel could not route the medication snapshot request to Health",
            exc_info=True,
        )
        return _failure(
            code="health_unavailable",
            message="Health medication data is temporarily unavailable.",
            retryable=True,
        )

    if bool(getattr(result, "is_error", False)):
        logger.warning("Switchboard returned an MCP error for the Health medication request")
        return _failure(
            code="health_unavailable",
            message="Health medication data is temporarily unavailable.",
            retryable=True,
        )

    data = getattr(result, "data", result)
    if isinstance(data, dict) and data.get("error"):
        logger.warning("Switchboard could not route the Health medication request")
        return _failure(
            code="health_unavailable",
            message="Health medication data is temporarily unavailable.",
            retryable=True,
        )

    routed_result = data.get("result") if isinstance(data, dict) else None
    if isinstance(routed_result, dict) and routed_result.get("is_error") is True:
        logger.warning("Health returned an MCP error for the medication request")
        return _failure(
            code="health_unavailable",
            message="Health medication data is temporarily unavailable.",
            retryable=True,
        )

    payload = (
        routed_result.get("data")
        if isinstance(routed_result, dict) and routed_result.get("is_error") is False
        else None
    )
    try:
        snapshot = MedicationTravelSnapshot.model_validate(payload)
    except ValidationError:
        logger.warning("Health returned an invalid medication travel snapshot")
        return _failure(
            code="invalid_health_response",
            message="Health returned an invalid medication response.",
            retryable=False,
        )

    if snapshot.status != "ok":
        logger.warning("Health returned an invalid medication travel snapshot")
        return _failure(
            code="invalid_health_response",
            message="Health returned an invalid medication response.",
            retryable=False,
        )

    return snapshot.model_dump(mode="json")
