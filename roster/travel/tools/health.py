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
        return _switchboard_unavailable()

    from butlers.core.service_plane.messenger_delivery import service_plane_auth_enabled

    if service_plane_auth_enabled():
        return await _request_via_relay(switchboard_client, timeout_s=timeout_s)

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

    return _settle(getattr(result, "data", result))


def _switchboard_unavailable() -> dict[str, Any]:
    return _failure(
        code="switchboard_unavailable",
        message="Switchboard is unavailable for the Health medication request.",
        retryable=True,
    )


async def _request_via_relay(switchboard_client: Any, *, timeout_s: float) -> dict[str, Any]:
    """The ``medication.travel_snapshot`` named relay (RFC 0038 D7, bu-2c1g7v).

    Switchboard authenticates this workload, admits only Travel, and reads
    Health's service-only twin. A 403 refusal means Travel is not allowed, so it
    is permanent; anything else, including a relay not served (404), is
    retryable unavailability. The reply is Switchboard's
    route envelope, settled exactly as the legacy route reply.
    """
    from butlers.core.service_plane import client as relay_client
    from butlers.core.service_plane.registry import MEDICATION_TRAVEL_SNAPSHOT
    from butlers.core.service_plane.relays import RelayRefused

    switchboard_url = relay_client.configured_switchboard_url(switchboard_client)
    if switchboard_url is None:
        return _switchboard_unavailable()
    try:
        data = await relay_client.call_relay(
            MEDICATION_TRAVEL_SNAPSHOT,
            {},
            daemon="travel",
            switchboard_url=switchboard_url,
            request_timeout_s=timeout_s,
        )
    except RelayRefused as refused:
        logger.warning("Switchboard refused the medication relay: %s", refused.code)
        if not relay_client.refusal_is_permanent(refused):
            # Not served (404): Switchboard's switch is off or it predates the relay.
            return _failure(
                code="health_unavailable",
                message="Health medication data is temporarily unavailable.",
                retryable=True,
            )
        return _failure(
            code="permission_denied",
            message="Travel is not permitted to request Health medication data.",
            retryable=False,
        )
    except Exception:
        logger.warning("Travel could not reach the medication relay", exc_info=True)
        return _failure(
            code="health_unavailable",
            message="Health medication data is temporarily unavailable.",
            retryable=True,
        )
    return _settle(data)


def _settle(data: Any) -> dict[str, Any]:
    """Validate Switchboard's route envelope around Health's snapshot."""
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
