"""Prometheus HTTP API query helpers.

Provides async functions for PromQL instant and range queries via the
Prometheus HTTP API (``/api/v1/query`` and ``/api/v1/query_range``).

No auth headers are required — the Prometheus endpoint is Tailscale-gated;
plain HTTP internally.

All functions return results on success and surface errors as dicts rather
than raising, so callers (MCP tools) can relay descriptive messages to the
LLM without unhandled exceptions crossing the MCP boundary.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


async def async_query(
    url: str,
    query: str,
    time: str | None = None,
) -> list[dict[str, Any]]:
    """Execute an instant PromQL query via GET /api/v1/query.

    Parameters
    ----------
    url:
        Base URL of the Prometheus HTTP API (e.g. ``"http://lgtm:9090"``).
        The path ``/api/v1/query`` is appended automatically.
    query:
        PromQL expression string.
    time:
        Optional evaluation timestamp (RFC 3339 or Unix timestamp).
        When omitted, Prometheus uses the current server time.

    Returns
    -------
    list[dict]
        On success — the ``data.result`` list from the Prometheus response
        (a vector; each element has ``metric`` and ``value`` keys).
        On any error — a single-element list ``[{"error": "<message>"}]``.
    """
    params: dict[str, str] = {"query": query}
    if time is not None:
        params["time"] = time

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(f"{url}/api/v1/query", params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        # Prometheus returns 400 for bad PromQL; surface the body as error text.
        try:
            body = exc.response.json()
            error_msg = body.get("error") or body.get("errorType") or exc.response.text
        except Exception:
            error_msg = exc.response.text or str(exc)
        logger.warning("Prometheus query HTTP error: %s", error_msg)
        return [{"error": error_msg}]
    except httpx.RequestError as exc:
        error_msg = f"Network error contacting Prometheus: {exc}"
        logger.warning(error_msg)
        return [{"error": error_msg}]
    except Exception as exc:  # noqa: BLE001
        error_msg = f"Unexpected error during Prometheus query: {exc}"
        logger.exception(error_msg)
        return [{"error": error_msg}]

    if payload.get("status") != "success":
        error_msg = payload.get("error") or f"Prometheus returned status: {payload.get('status')}"
        logger.warning("Prometheus query non-success: %s", error_msg)
        return [{"error": error_msg}]

    return payload["data"]["result"]


async def async_query_range(
    url: str,
    query: str,
    start: str,
    end: str,
    step: str,
) -> list[dict[str, Any]]:
    """Execute a range PromQL query via GET /api/v1/query_range.

    Parameters
    ----------
    url:
        Base URL of the Prometheus HTTP API (e.g. ``"http://lgtm:9090"``).
        The path ``/api/v1/query_range`` is appended automatically.
    query:
        PromQL expression string.
    start:
        Range start time (RFC 3339 or Unix timestamp string).
    end:
        Range end time (RFC 3339 or Unix timestamp string).
    step:
        Resolution step, e.g. ``"15s"``, ``"1m"``, ``"300"`` (seconds).

    Returns
    -------
    list[dict]
        On success — the ``data.result`` list from the Prometheus response
        (a matrix; each element has ``metric`` and ``values`` keys).
        On any error — a single-element list ``[{"error": "<message>"}]``.
    """
    params: dict[str, str] = {
        "query": query,
        "start": start,
        "end": end,
        "step": step,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(f"{url}/api/v1/query_range", params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        try:
            body = exc.response.json()
            error_msg = body.get("error") or body.get("errorType") or exc.response.text
        except Exception:
            error_msg = exc.response.text or str(exc)
        logger.warning("Prometheus query_range HTTP error: %s", error_msg)
        return [{"error": error_msg}]
    except httpx.RequestError as exc:
        error_msg = f"Network error contacting Prometheus: {exc}"
        logger.warning(error_msg)
        return [{"error": error_msg}]
    except Exception as exc:  # noqa: BLE001
        error_msg = f"Unexpected error during Prometheus range query: {exc}"
        logger.exception(error_msg)
        return [{"error": error_msg}]

    if payload.get("status") != "success":
        error_msg = payload.get("error") or f"Prometheus returned status: {payload.get('status')}"
        logger.warning("Prometheus query_range non-success: %s", error_msg)
        return [{"error": error_msg}]

    return payload["data"]["result"]


__all__ = [
    "async_query",
    "async_query_range",
]
