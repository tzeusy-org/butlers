"""Bound speculative Timeline reads before they reach shared database pools."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic

import anyio
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from opentelemetry import metrics

_CONCURRENT_READS = 2
_ADMISSION_TIMEOUT_S = 0.25
_READ_TIMEOUT_S = 10.0

_METER = metrics.get_meter("butlers")
_WAIT_SECONDS = _METER.create_histogram(
    "butlers.ingestion.read.admission",
    description="Time waiting for Timeline read admission, before database acquisition.",
    unit="s",
)
_READ_SECONDS = _METER.create_histogram(
    "butlers.ingestion.read.duration", description="Admitted Timeline read duration.", unit="s"
)
_OUTCOMES = _METER.create_counter(
    "butlers.ingestion.read.outcomes", description="Timeline read outcomes."
)


async def _wait_for_disconnect(request: Request, scope: anyio.CancelScope) -> None:
    # These routes apply this budget only to GETs; no request body is consumed
    # by their handlers. Reading receive here lets abandoned queries cancel
    # promptly instead of holding a connection until the full read deadline.
    # BaseHTTPMiddleware wraps receive in AnyIO task groups. A one-shot
    # asyncio.Task.cancel can leave their cleanup waiting for response_sent,
    # deadlocking a route that waits for this watcher before sending. Level
    # cancellation unwinds the receive stack before the response is returned.
    with scope:
        while (await request.receive())["type"] != "http.disconnect":
            pass


def _unavailable() -> Response:
    return JSONResponse(
        status_code=503,
        content={"detail": "Ingestion read temporarily unavailable"},
        headers={"Retry-After": "1"},
    )


class IngestionReadBudgetRoute(APIRoute):
    """Share one admission limit across ingestion GET routes in each API app.

    Mutation routes retain their existing transaction and audit lifecycle.
    Metrics use registered handler names, never request IDs or filter values.
    This limits Timeline demand; it does not reserve PostgreSQL connections
    against unrelated workloads or other API processes.
    """

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()
        operation = self.name

        async def bounded(request: Request) -> Response:
            if request.method != "GET":
                return await handler(request)
            limiter = getattr(request.app.state, "ingestion_read_limiter", None)
            if limiter is None:
                limiter = asyncio.Semaphore(_CONCURRENT_READS)
                request.app.state.ingestion_read_limiter = limiter

            queued_at = monotonic()
            try:
                await asyncio.wait_for(limiter.acquire(), _ADMISSION_TIMEOUT_S)
            except TimeoutError:
                _OUTCOMES.add(1, {"operation": operation, "outcome": "busy"})
                return _unavailable()
            finally:
                _WAIT_SECONDS.record(monotonic() - queued_at, {"operation": operation})

            started_at = monotonic()
            outcome = "error"
            read = asyncio.create_task(handler(request))
            disconnect_scope = anyio.CancelScope()
            disconnected = asyncio.create_task(_wait_for_disconnect(request, disconnect_scope))
            try:
                done, _ = await asyncio.wait(
                    (read, disconnected),
                    timeout=_READ_TIMEOUT_S,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if read in done:
                    response = read.result()
                    outcome = "ok" if response.status_code < 400 else "error"
                    return response
                if disconnected in done:
                    # Propagate receive failures instead of classifying them
                    # as a successful disconnect.
                    disconnected.result()
                    outcome = "disconnected"
                    return Response(status_code=499)
                outcome = "timeout"
                return _unavailable()
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            finally:
                read.cancel()
                disconnect_scope.cancel()
                try:
                    await asyncio.gather(read, disconnected, return_exceptions=True)
                finally:
                    limiter.release()
                attributes = {"operation": operation, "outcome": outcome}
                _READ_SECONDS.record(monotonic() - started_at, attributes)
                _OUTCOMES.add(1, attributes)

        return bounded
