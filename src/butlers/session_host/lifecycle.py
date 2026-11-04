"""Finite invocation ownership, independent of the eventual kernel backend.

A Domain is host-owned trusted code, never selected from a control frame. Its
settle method must observe termination of the whole kernel process domain,
including descendants; leader exit is insufficient. This module supplies no
backend or adapter eligibility proof. Missing backend means no launch.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from .contracts import LaunchRequest, Refused
from .material import InvocationMaterial, StagingCleanupFailed


class Domain(Protocol):
    async def execute(self, material: InvocationMaterial) -> bytes:
        """Run the server-selected profile, with bounded output in the backend."""
        ...

    async def settle(self) -> bool:
        """Terminate and observe ALL owned descendants; exact True only if terminal."""
        ...


async def _execute(domain: Domain, material: InvocationMaterial) -> bytes:
    # Invoke through a coroutine boundary so even a malformed backend's
    # synchronous exception remains inside the owner's cleanup path.
    return await domain.execute(material)


async def _settle(domain: Domain) -> bool:
    return await domain.settle()


class InvocationOwner:
    """Never publish output or erase material before positive whole-domain cleanup.

    A failed observer quarantines its material/tasks and poisons this owner for
    subsequent launches. Operators must resolve that containment failure; it is
    never turned into a successful result or an automatic replay.
    """

    def __init__(self, stage_root: Path, *, cleanup_timeout_s: float = 30) -> None:
        if (
            type(cleanup_timeout_s) not in (int, float)
            or not math.isfinite(cleanup_timeout_s)
            or not 0 < cleanup_timeout_s <= 30
        ):
            raise ValueError("invalid cleanup bound")
        self._root = stage_root
        self._cleanup_timeout = cleanup_timeout_s
        self._seen: set[UUID] = set()
        # Restart never interprets old material as safe merely because it is
        # old or its leader PID disappeared. Leave it untouched and refuse
        # new work until an operator supplies independent containment proof.
        # Separate hosts must use separate operator-owned staging roots.
        try:
            self._healthy = not any(stage_root.iterdir())
        except OSError:
            self._healthy = False
        self._quarantined: list[InvocationMaterial] = []
        self._pending: set[asyncio.Task] = set()

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def pending_count(self) -> int:
        return sum(not task.done() for task in self._pending)

    def _quarantine(self, material: InvocationMaterial) -> None:
        self._healthy = False
        if not any(item is material for item in self._quarantined):
            self._quarantined.append(material)

    def _discard(self, material: InvocationMaterial) -> None:
        try:
            material.discard()
        except BaseException:
            self._quarantine(material)
            raise Refused("stage_cleanup_failed") from None

    def _start(
        self, coroutine: Coroutine[Any, Any, Any], material: InvocationMaterial
    ) -> asyncio.Task:
        try:
            task = asyncio.create_task(coroutine)
        except BaseException:
            # No trustworthy task handle means no evidence about process
            # execution/settlement. Do not erase its material or admit work.
            coroutine.close()
            self._quarantine(material)
            raise Refused("domain_task_unavailable") from None
        self._track(task)
        return task

    def _track(self, task: asyncio.Task) -> None:
        self._pending.add(task)

        def finished(completed: asyncio.Task) -> None:
            self._pending.discard(completed)
            # Consume late exceptions without logging content. Cancellation is
            # a terminal task state, not evidence the kernel domain is gone.
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(finished)

    async def run(self, request: LaunchRequest, factory: Callable[[], Domain]) -> bytes:
        """Run a host-selected inert factory, never a request-selected plugin.

        Construction must not launch processes. Only execute may start the
        kernel domain, and settle must remain safe after partial startup.
        """
        if type(request) is not LaunchRequest or not self._healthy:
            raise Refused("host_unavailable")
        if request.invocation_id in self._seen:
            raise Refused("invocation_already_started")
        # No await before ownership: concurrent reuse cannot launch twice.
        self._seen.add(request.invocation_id)
        try:
            material = InvocationMaterial.stage(self._root, request)
        except StagingCleanupFailed as error:
            self._quarantine(error.material)
            raise
        except BaseException:
            # Fail closed even if allocation failed before a material handle
            # could be returned. Never guess that a partial directory is gone.
            self._healthy = False
            raise Refused("staging_failed") from None
        try:
            domain = factory()
        except BaseException:
            self._discard(material)
            raise Refused("domain_unavailable") from None
        work = self._start(_execute(domain, material), material)
        cancelled = False
        output: bytes | None = None
        failed = False
        try:
            done, _ = await asyncio.wait({work}, timeout=request.timeout_s)
            if not done:
                failed = True
            elif work.cancelled():
                failed = True
            else:
                try:
                    output = work.result()
                    if type(output) is not bytes or len(output) > 1024 * 1024:
                        failed = True
                except BaseException:
                    failed = True
        except asyncio.CancelledError:
            cancelled = True
        finally:
            if not work.done():
                work.cancel()
            # A separate task owns cleanup. Repeated cancellation of the caller
            # cannot reset its absolute deadline or interrupt containment.
            cleanup = self._start(_settle(domain), material)
            deadline = asyncio.get_running_loop().time() + self._cleanup_timeout
            while not (cleanup.done() and work.done()):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                unfinished = {t for t in (cleanup, work) if not t.done()}
                try:
                    await asyncio.wait(unfinished, timeout=remaining)
                except asyncio.CancelledError:
                    cancelled = True
            settled = False
            if cleanup.done() and not cleanup.cancelled() and work.done():
                try:
                    settled = cleanup.result() is True
                except BaseException:
                    pass
            if not settled:
                self._quarantine(material)
                if not cleanup.done():
                    cleanup.cancel()
            else:
                self._discard(material)
        if not settled:
            raise Refused("domain_cleanup_unproven")
        if cancelled:
            raise asyncio.CancelledError
        if failed or output is None:
            raise Refused("runtime_failed")
        return output
