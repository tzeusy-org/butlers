#!/usr/bin/env python3
"""Finite deployment startup gate for an expected daemon's route acceptance.

This read-only observation does not guarantee later delivery or recover events.
The Helm caller supplies the fixed internal Health endpoint, without credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys

import httpx

_MAX_RESPONSE_BYTES = 2048
_MAX_REQUEST_SECONDS = 5.0


async def _accepts_routes(client: httpx.AsyncClient, url: str, butler: str) -> bool:
    async with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
        if response.status_code != 200:
            return False
        body = bytearray()
        # Raw streaming bounds bytes retained without decompressing an unsolicited
        # compressed payload. Compression is unnecessary for this tiny identity.
        async for chunk in response.aiter_raw(chunk_size=_MAX_RESPONSE_BYTES + 1):
            if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                return False
            body.extend(chunk)
    try:
        identity = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return False
    return (
        isinstance(identity, dict)
        and identity.get("schema_version") == "butler.control.v1"
        and identity.get("butler_name") == butler
        and type(identity.get("boot_epoch")) is int
        and identity["boot_epoch"] > 0
        and identity.get("accepting_routes") is True
    )


async def wait_for_routes(
    url: str,
    butler: str,
    *,
    timeout_seconds: float = 300,
    interval_seconds: float = 5,
    request_timeout_seconds: float = 5,
) -> bool:
    """Observe readiness within finite whole-request and overall deadlines."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    try:
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    request_budget = min(_MAX_REQUEST_SECONDS, request_timeout_seconds, remaining)
                    try:
                        # httpx's inactivity timers alone cannot bound a slow
                        # trickling body. Include headers and the entire body.
                        async with asyncio.timeout(request_budget):
                            if await _accepts_routes(client, url, butler):
                                print("route-gate: route acceptance confirmed", flush=True)
                                return True
                    except (TimeoutError, httpx.HTTPError, httpx.InvalidURL):
                        pass
                    print("route-gate: waiting for route acceptance", flush=True)
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(interval_seconds, remaining))
    except TimeoutError:
        pass
    print("route-gate: startup deadline exceeded", file=sys.stderr, flush=True)
    return False


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("expected finite positive seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--butler", required=True)
    parser.add_argument("--timeout-seconds", type=_positive_seconds, default=300)
    parser.add_argument("--interval-seconds", type=_positive_seconds, default=5)
    parser.add_argument("--request-timeout-seconds", type=_positive_seconds, default=5)
    args = parser.parse_args()
    ready = asyncio.run(
        wait_for_routes(
            args.url,
            args.butler,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
            request_timeout_seconds=args.request_timeout_seconds,
        )
    )
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
