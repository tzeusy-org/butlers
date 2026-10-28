"""Source adapters share one account reconciliation and owned-lifecycle supervisor.

The adapters retain their discovery, scopes, credentials, health payloads and
account-loop implementations. This module never reads or writes credentials.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class AccountLoop(Protocol):
    def start(self) -> None: ...
    async def stop(self) -> None: ...
    @property
    def is_running(self) -> bool: ...


class GoogleAccountFleet:
    """One supervisor per existing connector process; no deployment unification."""

    restart_dead_accounts = False

    def __init__(self) -> None:
        self._loops: dict[str, AccountLoop] = {}
        self._start_time = time.time()
        self._running = False
        self._reload_event = asyncio.Event()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._health_server: Any = None
        self._health_thread: Any = None
        self._sync_lock = asyncio.Lock()
        self._signal_registered = False

    @property
    def rescan_interval(self) -> float:
        raise NotImplementedError

    async def _discover_accounts(self) -> dict[str, Any]:
        raise NotImplementedError

    async def _make_account_loop(self, email: str, metadata: Any) -> AccountLoop | None:
        raise NotImplementedError

    def _start_health_server(self) -> None:
        raise NotImplementedError

    async def _fleet_ready(self) -> None:
        pass

    async def _close_source(self) -> None:
        pass

    async def _sync_accounts(self) -> tuple[list[str], list[str], list[str]]:
        # Timer, SIGHUP and explicit reloads cannot race an account's ownership.
        async with self._sync_lock:
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
        self._main_loop = asyncio.get_running_loop()
        try:
            self._start_health_server()
            self._setup_sighup()
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
                await asyncio.to_thread(self._health_thread.join, 5)
            if self._signal_registered and self._main_loop is not None:
                self._main_loop.remove_signal_handler(signal.SIGHUP)
                self._signal_registered = False
        if failures:
            raise failures[0]
