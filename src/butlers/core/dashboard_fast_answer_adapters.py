"""Owning provider and registered-MCP adapters for the fast-answer core."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastmcp import Client

from butlers.core.dashboard_fast_answer_policy import (
    FAST_ANSWER_CONCIERGE_TOOLS_V1,
    FastAnswerAdmission,
)
from butlers.core.dashboard_fast_answer_runtime import FenceLost, InvalidAdmission
from butlers.core.dashboard_fast_answer_store import FastAnswerFence
from butlers.core.dispatch_intent import Consequence, DispatchIntent
from butlers.core.model_capabilities import ModelFeature
from butlers.core.model_routing import (
    check_token_quota,
    record_token_usage,
    resolve_model_with_effective_tier,
)
from butlers.core.runtimes.base import create_adapter


def mcp_data(result: Any) -> Any:
    """Decode a completed registered call; error and malformed data stay errors."""
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        raise RuntimeError("registered tool refused fast answer")
    for name in ("data", "structured_content", "structuredContent"):
        value = getattr(result, name, None)
        if value is not None:
            return value
    for block in getattr(result, "content", ()):
        if getattr(block, "type", None) == "text":
            return json.loads(block.text)
    raise RuntimeError("registered tool result unavailable")


@dataclass(frozen=True)
class _Model:
    model: str
    entry_id: UUID
    timeout: int
    purpose: str
    fence: FastAnswerFence


@dataclass
class ConciergeTarget:
    pool: Any
    endpoint: str
    client: Any
    tools: dict[str, Any]
    enabled: bool

    async def read(self, name: str, arguments: dict) -> Any:
        """Use normal registry routing and target validation, with exactly one send."""
        from butlers.tools.switchboard.routing.route import route

        if name not in FAST_ANSWER_CONCIERGE_TOOLS_V1 or name not in self.tools:
            raise RuntimeError("fast read authority unavailable")
        states = mcp_data(await self.client.call_tool("module.states", {}, raise_on_error=False))
        if not _dashboard_read_enabled(states):
            raise RuntimeError("fast read module disabled")

        async def once(endpoint, tool_name, args):
            if endpoint != self.endpoint:
                raise RuntimeError("fast read target changed")
            return mcp_data(await self.client.call_tool(tool_name, args, raise_on_error=False))

        routed = await route(
            self.pool, "concierge", name, arguments, source_butler="switchboard", call_fn=once
        )
        if "result" not in routed:
            raise RuntimeError("registered fast read unavailable")
        return routed["result"]


def _dashboard_read_enabled(states: Any) -> bool:
    state = states.get("dashboard_read") if isinstance(states, dict) else None
    return (
        isinstance(state, dict) and state.get("health") == "active" and state.get("enabled") is True
    )


class RegisteredFastAnswerDependencies:
    """Daemon-bound adapters; no target handler, direct SQL read or CLI fallback."""

    def __init__(self, daemon: Any, store: Any):
        self.daemon = daemon
        self.store = store
        self.pool = store.pool
        self._adapter = None

    async def models(self, fence: FastAnswerFence) -> tuple[_Model, _Model] | None:
        values = []
        for purpose, features in (
            ("dashboard_fast_answer_classification", frozenset({ModelFeature.STRUCTURED_OUTPUT})),
            ("dashboard_fast_answer_phrasing", frozenset()),
        ):
            intent = DispatchIntent(
                trigger_class=purpose,
                complexity_tier="cheap",
                consequence=Consequence.OBSERVE,
                required_features=features,
            )
            selected = await resolve_model_with_effective_tier(
                self.pool,
                "switchboard",
                "cheap",
                allow_tier_fallthrough=False,
                quota_aware=True,
                intent=intent,
            )
            if selected is None:
                return None
            runtime, model, _args, entry, timeout, tier = selected
            if (
                runtime != "api"
                or entry is None
                or str(tier) != "cheap"
                or type(timeout) is not int
                or timeout <= 0
            ):
                return None
            if not (await check_token_quota(self.pool, entry)).allowed:
                return None
            values.append(_Model(model, entry, timeout, purpose, fence))
        return tuple(values)

    async def target(self) -> ConciergeTarget | None:
        from butlers.core.mcp_urls import canonical_runtime_mcp_url, resolve_cross_container_mcp_url
        from butlers.tools.switchboard.routing.route import (
            _get_cached_router_client,
            _resolve_receiver_route_target,
            receiver_route_cutover_enabled,
            resolve_routing_target,
        )

        if receiver_route_cutover_enabled():
            target, _reason, _retryable = await _resolve_receiver_route_target(
                self.pool, "concierge", required_capability=None, route_contract_version=1
            )
            endpoint = target.endpoint_url if target is not None else None
        else:
            target, _reason = await resolve_routing_target(
                self.pool, "concierge", required_capability=None, route_contract_version=1
            )
            endpoint = target["endpoint_url"] if target is not None else None
        if endpoint is None:
            return None
        endpoint = resolve_cross_container_mcp_url(canonical_runtime_mcp_url(endpoint))
        client = await _get_cached_router_client(endpoint)
        states = mcp_data(await client.call_tool("module.states", {}, raise_on_error=False))
        if not _dashboard_read_enabled(states):
            return None
        tools = {tool.name: tool for tool in await client.list_tools()}
        return ConciergeTarget(self.pool, endpoint, client, tools, True)

    def _api(self):
        if self._adapter is None:
            self._adapter = create_adapter(
                "api", butler_name="switchboard", credential_store=self.daemon._credential_store
            )
        return self._adapter

    def _attempt_data(self, model: _Model, outcome: str, duration_ms: int) -> dict:
        return {
            "purpose": model.purpose,
            "requested_tier": "cheap",
            "effective_tier": "cheap",
            "runtime_type": "api",
            "resolution_source": "catalog",
            "model": model.model,
            "catalog_entry_id": str(model.entry_id),
            "execution_timeout": model.timeout,
            "outcome": outcome,
            "duration_ms": duration_ms,
        }

    async def _record_attempt(self, model: _Model, outcome: str, duration_ms: int = 0) -> dict:
        result = await self.store.control(
            model.fence, "attribution", self._attempt_data(model, outcome, duration_ms)
        )
        if result["outcome"] != "accepted":
            raise RuntimeError("fast provider attribution fence lost")
        return result

    async def _attribution(self, model: _Model, outcome: str, duration_ms: int = 0) -> None:
        await self._record_attempt(model, outcome, duration_ms)

    async def _cancelled_attempt(self, model: _Model, started_record: dict, started: float) -> None:
        # Only the database-issued start position can settle this exact attempt.
        # A failed metadata write leaves settlement unknown, never confirms Stop.
        attempt_index = started_record.get("attempt_index")
        if type(attempt_index) is not int or attempt_index < 0:
            raise FenceLost("provider cancellation attribution unavailable")
        try:
            result = await self.store.control(
                model.fence,
                "attempt_cancelled",
                {
                    **self._attempt_data(
                        model, "cancelled", int((time.monotonic() - started) * 1000)
                    ),
                    "attempt_index": attempt_index,
                },
            )
            if result["outcome"] != "accepted":
                raise FenceLost("provider cancellation attribution unavailable")
        except (Exception, asyncio.CancelledError):
            raise FenceLost("provider cancellation attribution unavailable") from None

    async def _usage(self, model: _Model, usage: dict | None) -> None:
        if usage and usage.get("input_tokens") is not None:
            await record_token_usage(
                self.pool,
                catalog_entry_id=model.entry_id,
                butler_name="switchboard",
                session_id=model.fence.session_id,
                input_tokens=usage["input_tokens"],
                output_tokens=usage.get("output_tokens", 0) or 0,
                cached_input_tokens=usage.get("cache_read_input_tokens", 0) or 0,
                cache_creation_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
                purpose=model.purpose,
            )

    async def classify(self, models, prompt, catalog, target) -> Any:
        model = models[0]
        tools = [
            {
                "name": "dashboard_fast_admission",
                "description": "Classify only; no execution.",
                "input_schema": FastAnswerAdmission.model_json_schema(),
            }
        ]
        metadata = {
            "catalog_state": catalog.state,
            "selected_owner": catalog.selected_owner,
            "candidates": [candidate.provenance() for candidate in catalog.candidates],
            "registered_reads": [
                {"name": name, "input_schema": tool.inputSchema}
                for name, tool in target.tools.items()
                if name in FAST_ANSWER_CONCIERGE_TOOLS_V1
            ],
        }
        started_record = await self._record_attempt(model, "started")
        started = time.monotonic()
        try:
            calls, _text, usage = await self._api().invoke_structured(
                prompt=prompt,
                system_prompt=(
                    "Classify the dashboard turn without effects: A statement, B action, "
                    "C bug, D question. System means fleet/runtime/spend telemetry only. Finances, "
                    "health, relationships or calendar are domain. Select Concierge only when "
                    "the provided catalog selects it. Plan at most three listed reads. "
                    "Return only dashboard_fast_admission. Evidence: " + json.dumps(metadata)
                ),
                tools=tools,
                env={},
                model=model.model,
                timeout=model.timeout,
            )
        except asyncio.CancelledError:
            await self._cancelled_attempt(model, started_record, started)
            raise
        except Exception:
            await self._attribution(model, "failed", int((time.monotonic() - started) * 1000))
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        await self._attribution(model, "received", duration_ms)
        await self._usage(model, usage)
        if (
            not isinstance(calls, list)
            or len(calls) != 1
            or calls[0].get("name") != "dashboard_fast_admission"
        ):
            raise InvalidAdmission("invalid admission envelope")
        return calls[0].get("input")

    async def phrase(self, models, question, results) -> str:
        model = models[1]
        started_record = await self._record_attempt(model, "started")
        started = time.monotonic()
        try:
            text, calls, usage = await self._api().invoke(
                prompt=json.dumps({"question": question, "registered_results": results}),
                system_prompt=(
                    "Answer briefly using only these system-plane results. "
                    "No tools or invented facts."
                ),
                mcp_servers={},
                env={},
                model=model.model,
                timeout=model.timeout,
            )
        except asyncio.CancelledError:
            await self._cancelled_attempt(model, started_record, started)
            raise
        except Exception:
            await self._attribution(model, "failed", int((time.monotonic() - started) * 1000))
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        await self._attribution(model, "received", duration_ms)
        await self._usage(model, usage)
        if calls:
            raise RuntimeError("phrasing attempted unsupported tool")
        return text

    async def reply(self, fence: FastAnswerFence, payload: dict) -> dict:
        async with Client(self.daemon.mcp) as client:
            return mcp_data(
                await client.call_tool(
                    "conversation_reply",
                    {
                        "conversation_id": payload["conversation_id"],
                        "message": payload["message"],
                        "sources": payload["source_labels"],
                        "fast_answer": {
                            "message_id": str(fence.message_id),
                            "session_id": str(fence.session_id),
                            "owner_instance_id": str(fence.owner_instance_id),
                            "lease_generation": fence.lease_generation,
                        },
                    },
                    raise_on_error=False,
                )
            )

    async def decline(self, question: str) -> Any:
        async with Client(self.daemon.mcp) as client:
            return mcp_data(
                await client.call_tool(
                    "cannot_answer",
                    {
                        "question_summary": question,
                        "scope_checked": ["system"],
                        "reason": (
                            "The held-authority catalog found no owning system-plane evidence."
                        ),
                    },
                    raise_on_error=False,
                )
            )
