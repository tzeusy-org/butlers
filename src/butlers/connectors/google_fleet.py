"""Source adapters share one account reconciliation and owned-lifecycle supervisor.

The adapters retain their discovery, scopes, credentials, health payloads and
account-loop implementations. This module never reads or writes credentials.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from butlers.connectors.metrics import get_error_type
from butlers.google_credentials import GOOGLE_TOKEN_URL, google_oauth_requires_reauthorization

logger = logging.getLogger(__name__)


class AccountHealthStatus(BaseModel):
    """Per-account health payload shared by the Google source adapters."""

    email: str | None
    endpoint_identity: str
    status: Literal["healthy", "degraded", "error"]
    last_checkpoint_save_at: str | None
    last_ingest_submit_at: str | None
    source_api_connectivity: Literal["connected", "disconnected", "unknown"]
    error: str | None = None


class MultiAccountHealthStatus(BaseModel):
    """Aggregate payload; each adapter retains its own aggregation policy."""

    status: Literal["healthy", "degraded", "error"]
    uptime_seconds: float
    active_accounts: int
    account_health: list[AccountHealthStatus]
    timestamp: str


def _redact_email(email: str | None) -> str | None:
    """Expose only the first two local-part characters in health responses."""
    if email is None:
        return None
    at_pos = email.find("@")
    if at_pos <= 0:
        return "***"
    local = email[:at_pos]
    domain = email[at_pos:]
    visible = local[:2]
    return f"{visible}***{domain}"


def _format_google_error(
    response: httpx.Response | None, *, gmail_detail: bool = False
) -> str | None:
    """Preserve each source's existing diagnostic format, without request data."""
    if response is None:
        return None
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            parts: list[str] = []
            code = error.get("code")
            if code is not None:
                parts.append(f"code={code}")
            status = error.get("status")
            if isinstance(status, str) and status:
                parts.append(f"status={status}")
            if gmail_detail:
                reason = None
                nested_errors = error.get("errors")
                if isinstance(nested_errors, list):
                    for item in nested_errors:
                        if isinstance(item, dict) and item.get("reason"):
                            reason = item["reason"]
                            break
                if isinstance(reason, str) and reason:
                    parts.append(f"reason={reason}")
            message = error.get("message")
            if isinstance(message, str) and message:
                parts.append(f"message={message}")
            if parts or gmail_detail:
                return ", ".join(parts) if parts else None
        if isinstance(error, str) and error:
            description = payload.get("error_description")
            if isinstance(description, str) and description:
                return f"error={error}, description={description}"
            return f"error={error}"
    if gmail_detail:
        return None
    body = response.text.strip()
    if body:
        return f"HTTP {response.status_code}: {body[:200]}"
    return f"HTTP {response.status_code}"


def _classify_source_api_error(exc: Exception, *, gmail_detail: bool = False) -> tuple[bool, str]:
    """Only an actual token POST response can prove OAuth revocation."""
    response = getattr(exc, "response", None)
    detail = (
        _format_google_error(response, gmail_detail=gmail_detail)
        if response is not None and (gmail_detail or isinstance(response, httpx.Response))
        else None
    )
    is_auth_revocation = False
    if isinstance(response, httpx.Response):
        try:
            request = response.request
        except RuntimeError:
            request = None
        if (
            request is not None
            and request.method == "POST"
            and str(request.url) == GOOGLE_TOKEN_URL
        ):
            is_auth_revocation = google_oauth_requires_reauthorization(response)
    error_type = get_error_type(exc) if gmail_detail else type(exc).__name__
    return is_auth_revocation, detail or f"{error_type}: {exc}"


class AccountLoop(Protocol):
    def start(self) -> None: ...
    async def stop(self) -> None: ...
    @property
    def is_running(self) -> bool: ...


class GoogleAccountFleet[AccountLoopT: AccountLoop]:
    """One supervisor per existing connector process; no deployment unification."""

    restart_dead_accounts = False

    def __init__(self) -> None:
        self._loops: dict[str, AccountLoopT] = {}
        self._start_time = time.time()
        self._running = False
        self._reload_event = asyncio.Event()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._health_server: Any = None
        self._health_thread: Any = None
        self._sync_lock = asyncio.Lock()
        self._signal_registered = False
        self._stopping = False

    @property
    def rescan_interval(self) -> float:
        raise NotImplementedError

    async def _discover_accounts(self) -> dict[str, Any]:
        raise NotImplementedError

    async def _make_account_loop(self, email: str, metadata: Any) -> AccountLoopT | None:
        raise NotImplementedError

    def _start_health_server(self) -> None:
        raise NotImplementedError

    async def _prepare_source(self) -> None:
        pass

    async def _fleet_ready(self) -> None:
        pass

    async def _close_source(self) -> None:
        pass

    async def _sync_accounts(self) -> tuple[list[str], list[str], list[str]]:
        # Timer, SIGHUP and explicit reloads cannot race an account's ownership.
        async with self._sync_lock:
            if self._stopping:
                return [], [], []
            desired = await self._discover_accounts()
            current = set(self._loops)
            if self.restart_dead_accounts:
                for email in current & desired.keys():
                    if not self._loops[email].is_running:
                        await self._loops[email].stop()
                        del self._loops[email]
                current = set(self._loops)
            removed = current - desired.keys()
            for email in sorted(removed):
                await self._loops[email].stop()
                del self._loops[email]
            added = []
            for email in sorted(desired.keys() - current):
                loop = await self._make_account_loop(email, desired[email])
                if loop is None:
                    continue
                self._loops[email] = loop
                loop.start()
                added.append(email)
            return added, sorted(removed), sorted(current & desired.keys())

    async def sync_accounts(self) -> tuple[list[str], list[str], list[str]]:
        """Explicit Drive reload entrypoint, using the same reconciliation lock."""
        return await self._sync_accounts()

    async def reload_accounts(self) -> dict[str, Any]:
        added, removed, unchanged = await self._sync_accounts()
        return {"added": added, "removed": removed, "unchanged": unchanged}

    def _setup_sighup(self) -> None:
        try:
            asyncio.get_running_loop().add_signal_handler(signal.SIGHUP, self._reload_event.set)
            self._signal_registered = True
        except (OSError, NotImplementedError):
            logger.debug("SIGHUP is unavailable on this platform")

    async def _run_rescan_loop(self) -> None:
        while self._running:
            try:
                await asyncio.wait_for(self._reload_event.wait(), timeout=self.rescan_interval)
            except TimeoutError:
                pass
            self._reload_event.clear()
            if self._running:
                await self._sync_accounts()

    async def start(self) -> None:
        self._running = True
        self._stopping = False
        self._main_loop = asyncio.get_running_loop()
        try:
            self._start_health_server()
            self._setup_sighup()
            await self._prepare_source()
            await self._sync_accounts()
            await self._fleet_ready()
            await self._run_rescan_loop()
        except BaseException:
            # A cleanup failure must not replace the original error/cancellation.
            try:
                await self.stop()
            except BaseException as cleanup:
                logger.warning("Google fleet cleanup failed (%s)", type(cleanup).__name__)
            raise
        else:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        self._stopping = True
        self._reload_event.set()
        failures: list[BaseException] = []
        async with self._sync_lock:
            for email, loop in list(self._loops.items()):
                try:
                    await loop.stop()
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    del self._loops[email]
            try:
                await self._close_source()
            except BaseException as exc:
                failures.append(exc)
            if self._health_server is not None:
                self._health_server.should_exit = True
            if self._health_thread is not None:
                try:
                    await asyncio.to_thread(self._health_thread.join, 5)
                    if self._health_thread.is_alive():
                        failures.append(RuntimeError("Google fleet health thread did not stop"))
                except Exception as exc:
                    failures.append(exc)
            if self._signal_registered and self._main_loop is not None:
                self._main_loop.remove_signal_handler(signal.SIGHUP)
                self._signal_registered = False
        if failures:
            raise failures[0]
