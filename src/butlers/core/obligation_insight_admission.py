"""Lifecycle-held Insight selected-source admission, never runner/delivery authority.

Only the server supplies this context. Source preparation releases its transaction
before admission acquires attempt keys; the private reader then holds all owning
source fences on admission's connection through the durable decision commit.
"""

from __future__ import annotations

import asyncio
import json
import logging

from butlers.core.goal_admission import GoalAdmission
from butlers.core.obligation_git_admission import (
    GitOccurrenceEvidenceProvider,
    PrivateGitAdmission,
    RegisteredGitCatalog,
)
from butlers.core.obligation_git_sources import LoadedGitSources
from butlers.core.obligation_insight_sources import (
    JOBS,
    InsightPreparation,
    InsightSelectedRuntime,
)
from butlers.core.tool_catalog import ToolCatalog

logger = logging.getLogger(__name__)
SUBMISSION_SECONDS = 5.0


class PrivateInsightAdmission(PrivateGitAdmission):
    """Reuse retained-task close/replay; one five-second owner covers prepare+admit."""

    def __init__(self, pool, loaded, registry):
        super().__init__(pool, InsightSelectedRuntime(loaded), registry)
        self.loaded = loaded

    def preparer(self, job_name):
        materials = [m for m in self.loaded.definitions if m.schedule["job_name"] == job_name]
        if job_name not in JOBS or len(materials) != 1:
            raise ValueError("private_insight_binding_unavailable")
        material = materials[0]
        preparation = InsightPreparation(
            self.pool,
            job_name,
            material.declaration,
            self.loaded.source_sha256,
            self.loaded.config_digest,
        )

        async def prepare(raw, selected, context):
            async def operation():
                submitted = await preparation.prepare_committed(raw, selected, context)
                # Source transaction/pool is now released: no source->admission
                # lock inversion or second pool acquisition inside admission.
                service = GoalAdmission(
                    self.pool,
                    GitOccurrenceEvidenceProvider(self.runtime, submitted),
                    RegisteredGitCatalog(self.registry),
                )
                return await service.admit_submission(submitted.source, submitted.occurrence_id)

            result = await self._prepare_owned(operation)
            # Existing collector ABI carries neither an admission receipt nor
            # permission to execute. Even an admitted goal would need a runner.
            return "staged_unverified" if result is not None else "unavailable"

        return prepare

    async def _prepare_owned(self, operation):
        self._reap()
        if self._closed or self._pending:
            logger.warning("Private Insight admission unavailable")
            return None
        task = asyncio.create_task(operation(), name="private-insight-admission")
        deadline = asyncio.get_running_loop().time() + SUBMISSION_SECONDS
        self._pending[task] = deadline

        def completed(done):
            self._pending.pop(done, None)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(completed)
        cancelled = False
        try:
            await asyncio.wait({task}, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        except asyncio.CancelledError:
            cancelled = True
        if cancelled or not task.done():
            task.cancel()
            cancelled |= await self._settle(task, deadline)
            if not task.done():
                logger.error("Private Insight admission cleanup incomplete")
            if cancelled:
                raise asyncio.CancelledError
            logger.warning("Private Insight admission outcome unknown")
            return None
        try:
            return task.result()
        except Exception:
            logger.warning("Private Insight admission outcome unknown")
            return None


def start_insight_admission(pool, loaded, registry, *, source_mode="off", admission_mode="off"):
    """OFF/preparation-only has no added source/catalog IO or behavior change."""
    if source_mode == "off" or admission_mode == "off":
        return None
    if (
        source_mode != "dark"
        or admission_mode != "dark"
        or type(loaded) is not LoadedGitSources
        or type(registry) is not ToolCatalog
        or loaded.owner_domain != "switchboard"
        or not registry.classification_complete
        or any(d.owner_domain != "switchboard" for d in registry.descriptors)
        or json.loads(loaded.context_json).get("registry_generation") != registry.generation_digest
    ):
        raise ValueError("private_insight_binding_unavailable")
    return PrivateInsightAdmission(pool, loaded, registry)
