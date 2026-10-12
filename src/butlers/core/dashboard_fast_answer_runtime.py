"""One cancellable, fenced lifetime for dashboard system-plane fast answers.

Reasoning and registered reads are supplied by owning adapters. This core
orchestrator never imports a target handler or supplies another schema's pool.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError

from butlers.core.dashboard_fast_answer_catalog import resolve_fast_answer_catalog
from butlers.core.dashboard_fast_answer_policy import (
    FAST_ANSWER_CONCIERGE_TOOLS_V1,
    FastAnswerAdmission,
    admit_fast_answer,
    valid_grants,
)
from butlers.core.dashboard_fast_answer_store import FastAnswerFence, reply_payload

logger = logging.getLogger(__name__)


class FenceLost(RuntimeError):
    """No later work may cross a lost lease or Stop fence."""


class InvalidAdmission(RuntimeError):
    """Only this schema failure permits one same-candidate classification retry."""


@dataclass(frozen=True)
class FastAnswerResult:
    handled: bool
    outcome: str
    session_id: UUID | None = None


class FastAnswerDependencies(Protocol):
    async def models(self, fence: FastAnswerFence) -> Any: ...
    async def target(self) -> Any: ...
    async def classify(self, models: Any, prompt: str, catalog: Any, target: Any) -> Any: ...
    async def phrase(self, models: Any, question: str, results: list[Any]) -> str: ...
    async def reply(self, fence: FastAnswerFence, payload: dict) -> dict: ...
    async def decline(self, question: str) -> Any: ...


@dataclass
class _LiveRuntime:
    task: asyncio.Task | None = None
    fence: FastAnswerFence | None = None
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False
    reply_payload: dict | None = None
    effects_started: bool = False
    uncertain: bool = False


def source_envelope(result: Any) -> dict[str, str]:
    """Validate target-produced provenance; a model cannot supply citations."""
    if not isinstance(result, dict) or not isinstance(result.get("source"), dict):
        raise ValueError("read_source_unavailable")
    source = result["source"]
    if any(
        not isinstance(source.get(key), str) or not source[key].strip()
        for key in ("kind", "ref", "as_of")
    ):
        raise ValueError("read_source_unavailable")
    try:
        stamp = datetime.fromisoformat(source["as_of"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("read_source_unavailable") from exc
    if stamp.tzinfo is None:
        raise ValueError("read_source_unavailable")
    return {key: source[key] for key in ("kind", "ref", "as_of")}


class DashboardFastAnswerRuntime:
    """Switchboard process owner, live cancellation registry and supervised sweeper."""

    def __init__(
        self,
        store: Any,
        dependencies: FastAnswerDependencies,
        *,
        owner_instance_id: UUID | None = None,
        heartbeat_seconds: float = 20,
        sweep_seconds: float = 60,
    ):
        if not 0 < heartbeat_seconds <= 20 or not 0 < sweep_seconds <= 60:
            raise ValueError("fast runtime supervision exceeds its adopted bound")
        self.store = store
        self.dependencies = dependencies
        self.owner_instance_id = owner_instance_id or uuid4()
        self.heartbeat_seconds = heartbeat_seconds
        self.sweep_seconds = sweep_seconds
        self._live: dict[UUID, _LiveRuntime] = {}
        self._supervisor: asyncio.Task | None = None

    async def start(self) -> None:
        """Drive the same operational sweep at startup and at most every minute."""
        if self._supervisor is not None:
            return
        try:
            await self.sweep()
        except Exception:
            # Availability can suspend a scan; it cannot remove its supervisor.
            logger.warning("dashboard fast startup reconciliation unavailable")
        self._supervisor = asyncio.create_task(self._supervise(), name="dashboard-fast-reconciler")

    async def sweep(self) -> list[dict]:
        return await self.store.reconcile(self.owner_instance_id)

    async def _supervise(self) -> None:
        while True:
            await asyncio.sleep(self.sweep_seconds)
            try:
                await self.sweep()
            except Exception:
                # Store outages retain durable pending state. Never log operands.
                logger.warning("dashboard fast reconciliation unavailable")

    async def close(self) -> None:
        if self._supervisor is not None:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
            self._supervisor = None
        tasks = [entry.task for entry in self._live.values() if entry.task is not None]
        for entry in self._live.values():
            entry.uncertain = True  # Shutdown is not an owner Stop acknowledgement.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def owns(self, session_id: UUID) -> bool:
        return session_id in self._live

    async def cancel(self, session_id: UUID) -> bool:
        entry = self._live.get(session_id)
        if entry is None or entry.task is None:
            return False
        entry.task.cancel()
        await entry.settled.wait()
        return entry.cancelled

    def expected_reply(self, fence: FastAnswerFence, payload: dict) -> bool:
        entry = self._live.get(fence.session_id)
        return entry is not None and entry.fence == fence and entry.reply_payload == payload

    async def registered_reply(
        self, binding: dict, conversation_id: UUID, message: str, citations: list
    ) -> dict:
        """Only a locally prepared same-runtime reply may use its durable claim."""
        if set(binding) != {"message_id", "session_id", "owner_instance_id", "lease_generation"}:
            raise ValueError("fast reply identity unavailable")
        if type(binding["lease_generation"]) is not int:
            raise ValueError("fast reply generation unavailable")
        fence = FastAnswerFence(
            UUID(binding["message_id"]),
            UUID(binding["session_id"]),
            UUID(binding["owner_instance_id"]),
            binding["lease_generation"],
        )
        entry = self._live.get(fence.session_id)
        payload = entry.reply_payload if entry is not None else None
        if (
            entry is None
            or entry.fence != fence
            or payload is None
            or (
                payload["conversation_id"] != str(conversation_id)
                or payload["message"] != message
                or payload["citations"] != citations
            )
        ):
            raise ValueError("fast reply prepared payload mismatch")
        result = await self.store.control(fence, "reply", payload)
        return result

    async def run(
        self,
        *,
        message_id: UUID,
        request_id: UUID,
        conversation_id: UUID,
        prompt: str,
        catalog_query: str | None = None,
    ) -> FastAnswerResult:
        if not valid_grants(FAST_ANSWER_CONCIERGE_TOOLS_V1):
            return FastAnswerResult(False, "invalid_grants")
        session_id = uuid4()
        entry = _LiveRuntime()
        self._live[session_id] = entry
        entry.task = asyncio.create_task(
            self._owned(
                entry,
                message_id,
                request_id,
                conversation_id,
                session_id,
                prompt,
                prompt if catalog_query is None else catalog_query,
            ),
            name=f"dashboard-fast-{session_id}",
        )
        try:
            return await entry.task
        finally:
            self._live.pop(session_id, None)

    async def _write(self, entry: _LiveRuntime, action: str, data: dict | None = None) -> dict:
        if entry.fence is None:
            raise FenceLost("fast runtime registration unavailable")
        result = await self.store.control(entry.fence, action, data)
        if result["outcome"] in {"fenced", "stopped", "finished", "conflict"}:
            raise FenceLost(result["outcome"])
        return result

    async def _heartbeat(self, entry: _LiveRuntime) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                await self._write(entry, "heartbeat")
            except FenceLost as exc:
                entry.uncertain = str(exc) != "stopped"
                if entry.task is not None:
                    entry.task.cancel()
                return
            except Exception:
                entry.uncertain = True
                if entry.task is not None:
                    entry.task.cancel()
                return

    async def _owned(
        self,
        entry: _LiveRuntime,
        message_id: UUID,
        request_id: UUID,
        conversation_id: UUID,
        session_id: UUID,
        prompt: str,
        catalog_query: str,
    ) -> FastAnswerResult:
        heartbeat = None
        try:
            registered = await self.store.register(
                message_id, request_id, session_id, self.owner_instance_id
            )
            if registered["outcome"] != "registered":
                return FastAnswerResult(True, registered["outcome"], session_id)
            entry.fence = FastAnswerFence(
                message_id, session_id, self.owner_instance_id, registered["lease_generation"]
            )
            heartbeat = asyncio.create_task(self._heartbeat(entry))
            # Both exact-cheap candidates are resolved before catalog/provider effects.
            models = await self.dependencies.models(entry.fence)
            if models is None:
                await self._write(entry, "fallback")
                return FastAnswerResult(False, "models_unavailable", session_id)
            await self._write(entry, "advance", {"phase": "catalog"})
            catalog = await resolve_fast_answer_catalog(
                catalog_query, eligible_owners=frozenset({"concierge"})
            )
            if catalog.state == "unavailable":
                await self._write(entry, "fallback")
                return FastAnswerResult(False, "catalog_unavailable", session_id)
            target = await self.dependencies.target()
            if target is None:
                await self._write(entry, "fallback")
                return FastAnswerResult(False, "target_unavailable", session_id)
            admission = None
            for attempt in range(2):
                await self._write(entry, "advance", {"phase": "classification"})
                try:
                    raw = await self.dependencies.classify(models, prompt, catalog, target)
                    admission = FastAnswerAdmission.model_validate_json(
                        json.dumps(raw, allow_nan=False)
                    )
                    break
                except (ValidationError, InvalidAdmission):
                    if attempt == 1:
                        await self._write(entry, "fallback")
                        return FastAnswerResult(False, "invalid_admission", session_id)
            decision = admit_fast_answer(
                admission,
                catalog,
                registered_tools=frozenset(target.tools),
                module_enabled=target.enabled,
            )
            if admission.lane in {"A", "B", "C", "D"}:
                await self.store.intent(
                    message_id, "answer" if admission.lane == "D" else "non_answer"
                )
            if decision.state == "fallback":
                await self._write(entry, "fallback")
                return FastAnswerResult(False, decision.reason, session_id)
            if decision.state == "no_match":
                await self._write(entry, "advance", {"phase": "reply"})
                await self._write(entry, "fallback")
                entry.effects_started = True
                declined = await self.dependencies.decline(admission.question)
                return FastAnswerResult(
                    True,
                    "no_match"
                    if isinstance(declined, dict) and declined.get("status") == "ok"
                    else "decline_unavailable",
                    session_id,
                )
            if not admission.reads:
                # Catalog pointers are provenance, not canonical answer content.
                # With no target result there is no grounded fast reply to make.
                await self._write(entry, "fallback")
                return FastAnswerResult(False, "grounding_unavailable", session_id)
            results, sources = [], []
            for read in admission.reads:
                await self._write(entry, "advance", {"phase": "reads"})
                entry.effects_started = True
                try:
                    result = await target.read(read.name, read.arguments)
                except BaseException:
                    entry.uncertain = (
                        True  # A cancelled transport may already have reached the peer.
                    )
                    raise
                sources.append(source_envelope(result))
                results.append(result)
            await self._write(entry, "advance", {"phase": "phrasing"})
            entry.effects_started = True
            text = await self.dependencies.phrase(models, admission.question, results)
            if not isinstance(text, str) or not text.strip() or not sources:
                raise ValueError("grounded fast reply unavailable")
            payload = reply_payload(message_id, conversation_id, text, sources)
            await self._write(entry, "reply_claim", {"digest": payload["digest"]})
            entry.reply_payload = payload
            try:
                result = await self.dependencies.reply(entry.fence, payload)
            except BaseException:
                entry.uncertain = True
                raise
            if result.get("outcome") != "completed":
                if result.get("outcome") == "stopped":
                    raise FenceLost("stopped")
                entry.uncertain = True
                raise FenceLost("reply settlement unavailable")
            return FastAnswerResult(True, "completed", session_id)
        except asyncio.CancelledError:
            if entry.fence is not None:
                try:
                    outcome = await self._write(
                        entry, "uncertain" if entry.uncertain else "cancelled"
                    )
                    entry.cancelled = outcome["outcome"] == "cancelled"
                except Exception:
                    pass
            return FastAnswerResult(
                True, "cancelled" if entry.cancelled else "pending_reconciliation", session_id
            )
        except FenceLost as exc:
            if str(exc) == "stopped" and not entry.uncertain:
                try:
                    result = await self._write(entry, "cancelled")
                    entry.cancelled = result["outcome"] == "cancelled"
                except Exception:
                    pass
            return FastAnswerResult(
                True, "cancelled" if entry.cancelled else "pending_reconciliation", session_id
            )
        except Exception:
            if entry.fence is None:
                # No catalog/provider/read was reached. A registration error
                # cannot invent a durable pending runtime or suppress the
                # existing continuation; its own claim still fails closed.
                return FastAnswerResult(False, "registration_unavailable")
            action = (
                "uncertain"
                if entry.uncertain
                else "failed"
                if entry.effects_started
                else "fallback"
            )
            try:
                await self._write(entry, action)
            except Exception:
                return FastAnswerResult(True, "pending_reconciliation", session_id)
            return FastAnswerResult(action != "fallback", action, session_id)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            entry.settled.set()
