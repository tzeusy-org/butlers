"""Shared Home Assistant recorder statistics WebSocket client."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

import aiohttp

StatisticsErrorScope = Literal["connection", "command"]

VALID_STATISTICS_PERIODS = frozenset({"5minute", "hour", "day", "week", "month"})
VALID_STATISTICS_TYPES = frozenset({"change", "max", "mean", "min", "state", "sum"})

_SAFE_PROVIDER_ERROR_CODES = frozenset(
    {
        "home_assistant_error",
        "invalid_end_time",
        "invalid_format",
        "invalid_start_time",
        "not_allowed",
        "not_found",
        "not_supported",
        "service_validation_error",
        "timeout",
        "unauthorized",
        "unknown_command",
        "unknown_error",
    }
)


class HAStatisticsError(RuntimeError):
    """A bounded statistics failure safe to expose to callers and logs."""

    def __init__(self, code: str, *, scope: StatisticsErrorScope) -> None:
        self.code = code
        self.scope = scope
        super().__init__(code)


def _provider_error_code(error: object) -> str:
    if not isinstance(error, dict):
        return "provider_error"
    code = error.get("code")
    if isinstance(code, str) and code in _SAFE_PROVIDER_ERROR_CODES:
        return code
    return "provider_error"


def parse_statistics_change_series(series: object) -> list[float] | None:
    """Return finite per-period changes, or ``None`` for an unsupported series.

    A cumulative-energy series is usable only when it is non-empty and every
    bucket explicitly contains a finite JSON number. In particular, a missing
    value is not zero, while an explicit numeric zero remains valid.
    """
    if not isinstance(series, list) or not series:
        return None

    changes: list[float] = []
    for bucket in series:
        if not isinstance(bucket, dict) or "change" not in bucket:
            return None
        value = bucket["change"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        change = float(value)
        if not math.isfinite(change):
            return None
        changes.append(change)
    return changes


class HAStatisticsClient:
    """Fetch recorder statistics through HA's current WebSocket command.

    The client can open a short-lived authenticated WebSocket from ``ha_url``
    and ``ha_token`` or reuse an already-connected caller through
    ``command_sender``.
    """

    def __init__(
        self,
        *,
        ha_url: str | None = None,
        ha_token: str | None = None,
        verify_ssl: bool = True,
        command_sender: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        if command_sender is None and (not ha_url or not ha_token):
            raise ValueError("HA URL and token are required without a command sender")
        if command_sender is not None and (ha_url is not None or ha_token is not None):
            raise ValueError("Provide credentials or a command sender, not both")
        self._ha_url = ha_url
        self._ha_token = ha_token
        self._verify_ssl = verify_ssl
        self._command_sender = command_sender

    async def get_statistics(
        self,
        *,
        statistic_ids: Sequence[str],
        start: str,
        end: str,
        period: str = "hour",
        types: Sequence[str] = ("change",),
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Return statistics keyed by statistic ID."""
        if period not in VALID_STATISTICS_PERIODS:
            raise ValueError(
                f"Invalid period {period!r}. "
                f"Must be one of: {', '.join(sorted(VALID_STATISTICS_PERIODS))}."
            )
        invalid_types = set(types) - VALID_STATISTICS_TYPES
        if not types or invalid_types:
            raise ValueError(
                "Statistics types must be a non-empty subset of: "
                f"{', '.join(sorted(VALID_STATISTICS_TYPES))}."
            )

        command = {
            "type": "recorder/statistics_during_period",
            "statistic_ids": list(statistic_ids),
            "start_time": start,
            "end_time": end,
            "period": period,
            "types": list(types),
        }
        if self._command_sender is not None:
            try:
                result = await self._command_sender(command, timeout=timeout)
            except TimeoutError:
                raise HAStatisticsError("timeout", scope="command") from None
            except HAStatisticsError:
                raise
            except Exception:
                raise HAStatisticsError("provider_error", scope="command") from None
        else:
            result = await self._send_one_shot(command, timeout=timeout)

        if not isinstance(result, dict):
            raise HAStatisticsError("protocol_error", scope="command")
        return result

    async def _send_one_shot(
        self,
        command: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        assert self._ha_url is not None
        assert self._ha_token is not None
        base_url = self._ha_url.rstrip("/")
        if base_url.startswith("https://"):
            websocket_url = base_url.replace("https://", "wss://", 1)
        elif base_url.startswith("http://"):
            websocket_url = base_url.replace("http://", "ws://", 1)
        else:
            raise HAStatisticsError("invalid_url", scope="connection")
        websocket_url = f"{websocket_url}/api/websocket"

        client_timeout = aiohttp.ClientTimeout(total=timeout + 45.0, connect=10.0)
        command_sent = False
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.ws_connect(
                    websocket_url,
                    heartbeat=None,
                    ssl=self._verify_ssl,
                ) as websocket:
                    auth_required = await websocket.receive_json(timeout=10.0)
                    if (
                        not isinstance(auth_required, dict)
                        or auth_required.get("type") != "auth_required"
                    ):
                        raise HAStatisticsError("protocol_error", scope="connection")

                    await websocket.send_json({"type": "auth", "access_token": self._ha_token})
                    auth_result = await websocket.receive_json(timeout=10.0)
                    if not isinstance(auth_result, dict) or auth_result.get("type") != "auth_ok":
                        raise HAStatisticsError("unauthorized", scope="connection")

                    payload = dict(command)
                    payload["id"] = 1
                    await websocket.send_json(payload)
                    command_sent = True
                    response = await websocket.receive_json(timeout=timeout)
        except HAStatisticsError:
            raise
        except (aiohttp.ClientError, TimeoutError):
            raise HAStatisticsError(
                "transport_error",
                scope="command" if command_sent else "connection",
            ) from None
        except (TypeError, ValueError, RuntimeError):
            raise HAStatisticsError(
                "protocol_error",
                scope="command" if command_sent else "connection",
            ) from None

        if (
            not isinstance(response, dict)
            or response.get("id") != 1
            or response.get("type") != "result"
        ):
            raise HAStatisticsError("protocol_error", scope="command")
        if response.get("success") is not True:
            raise HAStatisticsError(
                _provider_error_code(response.get("error")),
                scope="command",
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise HAStatisticsError("protocol_error", scope="command")
        return result
