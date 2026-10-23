"""Private same-owning Git admission, separate from legacy dispatch and D7.

Only lifecycle supplies the actual captured source runtime and final registry.
No request/MCP/transport accepts this factory or its objects. An occurrence DTO
is a handoff hint: the admission transaction rechecks all owning material.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace

from butlers.core.catalog_reader import PublishedCatalogProvider
from butlers.core.goal_admission import Evidence, EvidenceRefused, GoalAdmission
from butlers.core.obligation_admission import DarkSubmission
from butlers.core.obligation_git_sources import GitScheduleRuntime, LoadedGitSources
from butlers.core.obligation_sources import SourceConflict
from butlers.core.tool_catalog import ToolCatalog

logger = logging.getLogger(__name__)
SUBMISSION_SECONDS = 5.0


class GitOccurrenceEvidenceProvider:
    """Recheck source fences AFTER admission's attempt/source-ledger locks."""

    def __init__(self, runtime: GitScheduleRuntime, submitted: DarkSubmission):
        self.runtime, self.submitted = runtime, submitted

    async def load(self, connection, source):
        try:
            current = await self.runtime.read_submission_in_transaction(connection, self.submitted)
        except SourceConflict as exc:
            raise EvidenceRefused("source_evidence_unavailable") from exc
        definition = current.definition
        if (
            self.runtime.owner_domain != "switchboard"
            or definition.owner_domain != "switchboard"
            or definition.origin != "git"
            or definition.creator is not None
            or definition.family not in {"schedule", "deadline", "model_job"}
            or source != current.source
        ):
            raise EvidenceRefused("source_evidence_unavailable")
        # A held runtime-policy digest cannot certify seasonal/context inputs as
        # unexposed. No runtime creator/cause/recipient authority is manufactured.
        return Evidence(
            "occurrence",
            f"switchboard:{definition.family}:{definition.source_id}",
            obligation=current.obligation,
            outside_content=True,
        )


class RegisteredGitCatalog(PublishedCatalogProvider):
    """Validate held registration and project notify/resources from ONE read."""

    def __init__(self, registry: ToolCatalog):
        self.registry = registry

    async def load(self, connection):
        snapshot = await self.snapshot(connection)
        registered = snapshot.registration("switchboard").catalog
        if registered != self.registry:
            raise EvidenceRefused("catalog_unavailable")
        return self.project(snapshot)


class PrivateGitAdmission:
    """One bounded owned admission; an unsettled operation quarantines this consumer.

    Cancellation/commit uncertainty never becomes a successful decision. A
    replay uses the same occurrence UUID, so a committed lost reply is resolved
    through GoalAdmission's actual immutable attempt binding, never a fresh ID.
    """

    def __init__(self, pool, runtime: GitScheduleRuntime, registry: ToolCatalog):
        self.pool, self.runtime, self.registry = pool, runtime, registry
        self._pending: dict[asyncio.Task, float] = {}
        self._closed = False

    def _reap(self):
        for task in tuple(self._pending):
            if task.done():
                self._pending.pop(task, None)
                if not task.cancelled():
                    task.exception()

    async def _settle(self, task, deadline):
        interrupted = False
        while not task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait({task}, timeout=remaining)
            except asyncio.CancelledError:
                interrupted = True
                task.cancel()
        return interrupted

    async def submit(self, submitted: DarkSubmission):
        self._reap()
        if self._closed or self._pending or type(submitted) is not DarkSubmission:
            logger.warning("Private Git admission unavailable")
            return None
        service = GoalAdmission(
            self.pool,
            GitOccurrenceEvidenceProvider(self.runtime, submitted),
            RegisteredGitCatalog(self.registry),
        )
        task = asyncio.create_task(
            service.admit_submission(submitted.source, submitted.occurrence_id),
            name="private-git-admission",
        )
        deadline = asyncio.get_running_loop().time() + SUBMISSION_SECONDS
        self._pending[task] = deadline

        def finished(done):
            self._pending.pop(done, None)
            # Observe exceptions even after an explicitly incomplete close.
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        cancelled = False
        try:
            await asyncio.wait({task}, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        except asyncio.CancelledError:
            cancelled = True
        if cancelled or not task.done():
            task.cancel()
            cancelled |= await self._settle(task, deadline)
            if not task.done():
                logger.error("Private Git admission cleanup incomplete")
            if cancelled:
                raise asyncio.CancelledError
            logger.warning("Private Git admission outcome unknown")
            return None
        try:
            return task.result()
        except Exception:
            # SQL/commit loss and refusal-to-read stay unavailable, with no
            # dynamic exception text or retry and no change to legacy dispatch.
            logger.warning("Private Git admission outcome unknown")
            return None

    async def aclose(self):
        self._closed = True
        interrupted = False
        for task, deadline in tuple(self._pending.items()):
            task.cancel()
            interrupted |= await self._settle(task, deadline)
        self._reap()
        if self._pending:
            raise RuntimeError("private_git_admission_cleanup_incomplete")
        if interrupted:
            raise asyncio.CancelledError


def bind_private_git_admission(pool, runtime, loaded, registry, *, mode="off"):
    """Lifecycle-only selection; OFF has zero added IO and preserves staging."""
    if mode == "off":
        return runtime
    if (
        mode != "dark"
        or type(runtime) is not GitScheduleRuntime
        or type(loaded) is not LoadedGitSources
        or type(registry) is not ToolCatalog
        or loaded.owner_domain != "switchboard"
        or runtime.owner_domain != "switchboard"
        or not registry.classification_complete
        or any(item.owner_domain != "switchboard" for item in registry.descriptors)
        or any(material not in loaded.definitions for _, material in runtime.bindings)
        or runtime.admission is not None
        or json.loads(loaded.context_json).get("registry_generation") != registry.generation_digest
    ):
        raise ValueError("private_git_admission_binding_unavailable")
    return replace(runtime, admission=PrivateGitAdmission(pool, runtime, registry))
