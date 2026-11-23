"""Home Assistant transports; callers retain their sessions, retries and authority."""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import httpx


class HAAuthInvalidError(RuntimeError):
    """Authentication rejected; no provider message or credential is retained."""


class HAHandshakeProtocolError(RuntimeError):
    """An unexpected closed handshake state."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__("Unexpected HA handshake " + stage)


def ws_url(base_url: str) -> str:
    """Map supported HTTP/WS schemes to the HA WebSocket endpoint."""
    parsed = urlsplit(base_url.rstrip("/"))
    schemes = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}
    if parsed.scheme not in schemes or not parsed.netloc:
        raise ValueError("Unsupported HA URL")
    return urlunsplit(
        (
            schemes[parsed.scheme],
            parsed.netloc,
            parsed.path + "/api/websocket",
            parsed.query,
            parsed.fragment,
        )
    )


async def authenticate(websocket: Any, token: str) -> dict[str, Any]:
    """Authenticate once. The caller owns connection closure and feature negotiation."""
    greeting = await websocket.receive_json(timeout=10.0)
    if not isinstance(greeting, dict) or greeting.get("type") != "auth_required":
        raise HAHandshakeProtocolError("greeting")
    await websocket.send_json({"type": "auth", "access_token": token})
    result = await websocket.receive_json(timeout=10.0)
    if isinstance(result, dict) and result.get("type") == "auth_invalid":
        raise HAAuthInvalidError("HA authentication rejected")
    if not isinstance(result, dict) or result.get("type") != "auth_ok":
        raise HAHandshakeProtocolError("response")
    return result


def decode_ws_text(data: str | bytes) -> list[dict[str, Any]]:
    """Decode one JSON dictionary or coalesced dictionaries, withholding raw text."""
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid HA WebSocket JSON") from None
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def rest_client(
    base_url: str | None,
    token: str | None,
    *,
    verify_ssl: bool = True,
    timeout: httpx.Timeout | float | None = 5.0,
    trust_env: bool = True,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    """Create a caller-owned client, without implicit retries or credential fallback.

    The per-phase HTTPX timeout is separate from a caller's total request budget.
    Existing HTTPX owners retain environment policy; migrated aiohttp owners pass
    trust_env=False and follow_redirects=True to preserve their prior transport defaults.
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(
        base_url=base_url or "",
        headers=headers,
        verify=verify_ssl,
        timeout=timeout,
        trust_env=trust_env,
        follow_redirects=follow_redirects,
    )


async def get_json(
    client: httpx.AsyncClient, url: str, *, params: dict[str, str] | None = None
) -> Any:
    """GET, retain HTTPX status/transport exceptions, then decode the response."""
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


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
        timeout: float = 30.0,  # noqa: ASYNC109 - command_sender keyword; rename deferred (#4816)
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
        timeout: float,  # noqa: ASYNC109 - command_sender keyword; rename deferred (#4816)
    ) -> dict[str, Any]:
        assert self._ha_url is not None
        assert self._ha_token is not None
        try:
            websocket_url = ws_url(self._ha_url)
        except ValueError:
            raise HAStatisticsError("invalid_url", scope="connection") from None

        client_timeout = aiohttp.ClientTimeout(total=timeout + 45.0, connect=10.0)
        command_sent = False
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.ws_connect(
                    websocket_url,
                    heartbeat=None,
                    ssl=self._verify_ssl,
                ) as websocket:
                    try:
                        await authenticate(websocket, self._ha_token)
                    except HAAuthInvalidError:
                        raise HAStatisticsError("unauthorized", scope="connection") from None
                    except HAHandshakeProtocolError as error:
                        code = "protocol_error" if error.stage == "greeting" else "unauthorized"
                        raise HAStatisticsError(code, scope="connection") from None

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
