"""Structured tool-use classification fast lane (bu-qvnce.12 slice 3).

Deferred from PR #2936. Migration ``core_157`` flipped switchboard
classification's ``cheap`` catalog tier onto ``runtime_type='api'``
(``butlers.core.runtimes.api.ApiAdapter``) so classification would skip the
cold CLI-subprocess + MCP-handshake latency every other spawn pays. But
``ApiAdapter.invoke()`` refuses non-empty ``mcp_servers`` (see its module
docstring), and the classification session needs ``route_to_butler`` /
``file_bug_report`` — real tool calls dispatched through the switchboard's own
MCP server. Since ``core.spawner.Spawner._run()`` always wires ``mcp_servers``
for ``trigger_source not in ("healing", "qa")``, every classification call was
silently attempting (and immediately failing) the api-adapter candidate
before same-tier failover fell back to the previous CLI candidate — the tier
flip was a no-op with extra failure-metric noise.

This module closes that gap. Instead of driving a full CLI subprocess with a
live MCP handshake, it makes ONE direct Anthropic Messages call with a forced
tool-use schema mirroring ``route_to_butler`` / ``file_bug_report`` exactly
(``ApiAdapter.invoke_structured()``), then executes the decision *in-process*
by calling the SAME registered FastMCP tool functions directly — no
subprocess, no HTTP round trip — reusing every side effect those tools
already implement (permission-matrix checks, dashboard lane exclusivity,
envelope construction, ``_switchboard_route`` dispatch).

The classification SCHEMA and downstream consumers
(``_extract_routed_butlers`` / ``_extract_bug_report_calls`` in
``butlers.modules.pipeline``) are unchanged: this module only produces a
spawn-result-shaped object (``output``/``tool_calls``/``model``/
``input_tokens``/``output_tokens``) whose ``tool_calls`` use the exact same
``name``/``input``/``result`` shape those functions already parse.

Same-tier failover composes with the shared primitives
(``classify_failover_eligibility`` / ``next_same_tier_candidate``, bu-8fves)
rather than forking a new implementation — mirroring
``DiscretionDispatcher.call()``'s loop shape. Failover only continues while a
candidate resolves to ``runtime_type == "api"``; landing on a non-api
candidate (or exhausting attempts, or quota) returns ``None`` so the caller
falls back to the existing CLI/free-text classification path unchanged.

A schema-invalid (but successfully-received) response is retried once against
the same candidate before falling back — never silently misclassify by
executing an unvalidated decision.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from butlers.core.failover_classifier import FailoverContext, classify_failover_eligibility
from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import (
    Complexity,
    check_token_quota,
    next_same_tier_candidate,
    record_token_usage,
    resolve_model_with_effective_tier,
)
from butlers.core.runtimes.base import create_adapter

logger = logging.getLogger(__name__)

# Defensive backstop against a pathological catalog (many same-tier "api"
# entries all failing), mirroring DiscretionDispatcher's cap.
_MAX_FAILOVER_ATTEMPTS = 5

_SCHEMA_RETRY_REMINDER = (
    "\n\n--- IMPORTANT ---\nYour previous response did not include a valid "
    "tool call. You MUST call one of the provided tools with all required "
    "arguments filled in."
)

#: Anthropic Messages tool schema mirroring
#: ``core_tools._switchboard.register_switchboard_tools.route_to_butler``'s
#: signature exactly. Keep the two in sync if that tool's signature changes.
ROUTE_TO_BUTLER_TOOL: dict[str, Any] = {
    "name": "route_to_butler",
    "description": (
        "ROUTING TOOL — call this to send a message to a specialist butler. "
        "Call it once per target butler. Dashboard chat-widget bug/system "
        "reports must NOT be routed here — use file_bug_report instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "butler": {
                "type": "string",
                "description": (
                    "Target butler name — one of: finance, health, "
                    "relationship, travel, education, lifestyle, general."
                ),
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Self-contained prompt for the target butler. Must be "
                    "independently understandable without conversation history."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "Optional — key details and context the target butler "
                    "needs to act on this request."
                ),
            },
            "complexity": {
                "type": "string",
                "enum": ["reasoning", "workhorse", "cheap", "specialty", "local", "legacy"],
                "description": "Task complexity tier. Defaults to workhorse when omitted.",
            },
        },
        "required": ["butler", "prompt"],
    },
}

#: Mirrors ``core_tools._switchboard.register_switchboard_tools.file_bug_report``.
FILE_BUG_REPORT_TOOL: dict[str, Any] = {
    "name": "file_bug_report",
    "description": (
        "File a dashboard bug/system report with QA. Use only for chat-widget "
        "messages describing something broken in the dashboard itself — never "
        "for domain requests, which belong to route_to_butler."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Concise description of the problem.",
            },
            "severity": {
                "type": "integer",
                "minimum": 0,
                "maximum": 4,
                "description": "0=critical .. 4=info. Defaults to 2 (medium).",
            },
        },
        "required": ["summary"],
    },
}

_KNOWN_TOOL_NAMES = frozenset({"route_to_butler", "file_bug_report"})
_VALID_COMPLEXITY = frozenset({"reasoning", "workhorse", "cheap", "specialty", "local", "legacy"})


@dataclass
class StructuredClassificationResult:
    """Spawn-result-shaped output.

    Matches the subset of ``SpawnerResult`` that
    ``butlers.modules.pipeline.MessagePipeline.process()`` already reads off
    ``self._dispatch_fn``'s return value (``.output``, ``.tool_calls``,
    ``.model``, ``.input_tokens``, ``.output_tokens``), so the fast lane's
    result can be dropped in as a direct substitute with zero changes to
    downstream extraction/telemetry/lifecycle code.
    """

    output: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def _validate_tool_call(call: dict[str, Any]) -> bool:
    """Validate one forced tool_use block against the classification schema.

    Never accepts a call whose required arguments are missing or
    implausibly typed — a schema-invalid decision must trigger a retry (or
    CLI fallback), not silent misclassification.
    """
    name = str(call.get("name") or "")
    if name not in _KNOWN_TOOL_NAMES:
        return False
    raw_input = call.get("input")
    if not isinstance(raw_input, dict):
        return False

    if name == "route_to_butler":
        butler = raw_input.get("butler")
        prompt = raw_input.get("prompt")
        if not isinstance(butler, str) or not butler.strip():
            return False
        if not isinstance(prompt, str) or not prompt.strip():
            return False
        complexity = raw_input.get("complexity")
        if complexity is not None and (
            not isinstance(complexity, str) or complexity not in _VALID_COMPLEXITY
        ):
            return False
        context = raw_input.get("context")
        if context is not None and not isinstance(context, str):
            return False
        return True

    # file_bug_report
    summary = raw_input.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return False
    severity = raw_input.get("severity")
    if severity is not None and (
        isinstance(severity, bool) or not isinstance(severity, int) or not (0 <= severity <= 4)
    ):
        return False
    return True


def _validate_tool_calls(calls: list[dict[str, Any]]) -> bool:
    """A response is usable only when it has >=1 call and every call validates."""
    if not calls:
        return False
    return all(_validate_tool_call(call) for call in calls)


async def _execute_tool_call(mcp_server: Any, call: dict[str, Any]) -> dict[str, Any]:
    """Execute one validated tool_use block in-process against the local FastMCP
    server — no subprocess, no HTTP round trip. Reuses the SAME registered tool
    function (and therefore the SAME side effects: permission checks, dashboard
    lane exclusivity, envelope construction) a full CLI session would have
    triggered via the MCP protocol.
    """
    name = str(call.get("name") or "")
    kwargs = dict(call.get("input") or {})

    get_tool = getattr(mcp_server, "get_tool", None)
    if not callable(get_tool):
        logger.error(
            "structured_classify: FastMCP instance has no get_tool(name); "
            "cannot execute %s locally",
            name,
        )
        return {**call, "result": {"status": "error", "error": "no local tool resolver"}}

    tool_obj: Any = None
    try:
        tool_obj = get_tool(name)
        if hasattr(tool_obj, "__await__"):
            tool_obj = await tool_obj
    except KeyError:
        tool_obj = None
    except Exception:
        logger.exception("structured_classify: failed to resolve local tool %s", name)
        tool_obj = None

    if tool_obj is None:
        return {**call, "result": {"status": "error", "error": f"tool not registered: {name}"}}

    fn = getattr(tool_obj, "fn", None)
    if not callable(fn):
        return {
            **call,
            "result": {"status": "error", "error": f"tool has no callable fn: {name}"},
        }

    try:
        # SAFETY BOUNDARY: Direct ``.fn()`` execution skips the FastMCP HTTP/ASGI
        # request pipeline. This server currently wraps that pipeline only for
        # runtime-session capture and SSE disconnect handling, not auth or rate
        # limiting. If future server middleware controls tool calls, enforce an
        # explicit equivalent check here before this invocation.
        if inspect.iscoroutinefunction(fn):
            result = await fn(**kwargs)
        else:
            result = fn(**kwargs)
            if inspect.isawaitable(result):
                result = await result
    except Exception as exc:
        logger.warning("structured_classify: local execution of %s failed: %s", name, exc)
        result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    return {**call, "result": result}


async def try_structured_classification(
    pool: asyncpg.Pool,
    *,
    mcp_server: Any,
    prompt: str,
    system_prompt: str = "",
    include_bug_report: bool,
    butler_name: str = "switchboard",
    credential_store: Any | None = None,
) -> StructuredClassificationResult | None:
    """Attempt the api-adapter structured tool-use classification fast lane.

    Returns ``None`` to signal the caller must fall back to the existing
    CLI/free-text ``dispatch_fn`` path unchanged — because the resolved
    runtime for this tier is not ``"api"``, the resolved entry is over
    quota, every same-tier "api" candidate failed, or the model never
    produced a schema-valid tool call after one retry. Returns a populated
    :class:`StructuredClassificationResult` (with already-executed
    ``tool_calls``) on success.

    Parameters
    ----------
    pool:
        asyncpg pool for the switchboard database (model catalog / ledger).
    mcp_server:
        The switchboard's live FastMCP server instance (``ButlerDaemon.mcp``).
        ``None`` short-circuits to ``None`` immediately — the fast lane is
        opt-in and requires a local tool resolver to execute decisions.
    prompt:
        The full routing prompt (already built by
        ``_build_routing_prompt``/``_build_dashboard_lane_prompt``) — sent as
        the user message, unchanged from the CLI classification path.
    include_bug_report:
        Whether ``file_bug_report`` should be offered alongside
        ``route_to_butler`` (dashboard two-lane classification).
    """
    if mcp_server is None:
        return None

    tools = [ROUTE_TO_BUTLER_TOOL] + ([FILE_BUG_REPORT_TOOL] if include_bug_report else [])

    catalog_result = await resolve_model_with_effective_tier(pool, butler_name, Complexity.CHEAP)
    if catalog_result is None:
        return None

    (
        runtime_type,
        model_id,
        _extra_args,
        catalog_entry_id,
        session_timeout_s,
        effective_tier,
    ) = catalog_result

    metrics = ButlerMetrics(butler_name=butler_name)
    attempted_ids: list[uuid.UUID] = []
    attempt_count = 0
    adapter_cache: dict[str, Any] = {}

    while True:
        attempt_count += 1

        if runtime_type != "api":
            # Same-tier failover started on (or landed on) a CLI-only
            # runtime; the fast lane cannot drive it without a live MCP
            # session — the caller's existing dispatch_fn path handles this
            # unchanged.
            return None

        quota = await check_token_quota(pool, catalog_entry_id)
        if not quota.allowed:
            # A hard per-entry block, not failover-eligible (mirrors
            # DiscretionDispatcher) — fall back to the CLI path rather than
            # hunting for another same-tier "api" candidate.
            return None

        adapter = adapter_cache.get(runtime_type)
        if adapter is None:
            adapter = create_adapter(
                "api", credential_store=credential_store, butler_name=butler_name
            )
            adapter_cache[runtime_type] = adapter

        effective_prompt = prompt
        result: StructuredClassificationResult | None = None
        attempt_exc: Exception | None = None

        for schema_attempt in range(2):  # one retry on schema-invalid output only
            try:
                tool_calls, text, usage = await adapter.invoke_structured(
                    prompt=effective_prompt,
                    system_prompt=system_prompt,
                    tools=tools,
                    env={},
                    model=model_id,
                    timeout=session_timeout_s,
                )
            except Exception as exc:  # classified below, after the retry loop
                attempt_exc = exc
                break

            if usage:
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                if input_tokens is not None:
                    await record_token_usage(
                        pool,
                        catalog_entry_id=catalog_entry_id,
                        butler_name=butler_name,
                        session_id=None,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens or 0,
                        cached_input_tokens=usage.get("cache_read_input_tokens", 0) or 0,
                        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
                        purpose="classification",
                    )
                    metrics.record_token_usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens or 0,
                        model=model_id,
                        butler=butler_name,
                    )

            if _validate_tool_calls(tool_calls):
                result = StructuredClassificationResult(
                    output=text,
                    tool_calls=tool_calls,
                    model=model_id,
                    input_tokens=usage.get("input_tokens") if usage else None,
                    output_tokens=usage.get("output_tokens") if usage else None,
                )
                break

            logger.warning(
                "structured_classify: schema-invalid tool_calls from model=%s (attempt %d/2); %s",
                model_id,
                schema_attempt + 1,
                "retrying once" if schema_attempt == 0 else "giving up; falling back to CLI",
            )
            effective_prompt = f"{prompt}{_SCHEMA_RETRY_REMINDER}"

        if attempt_exc is None and result is not None:
            # Execute the validated decision in-process — no MCP round trip.
            executed = [await _execute_tool_call(mcp_server, call) for call in result.tool_calls]
            result.tool_calls = executed
            return result

        if attempt_exc is None:
            # Schema-invalid after the retry: not a call failure, just an
            # unusable decision — fall back to the CLI path rather than
            # burning a same-tier failover attempt on a "successful" call.
            return None

        # The structured call itself raised — classify for same-tier
        # failover eligibility exactly like DiscretionDispatcher.call() does
        # (composing with the shared classifier rather than forking it). No
        # tool_calls were captured yet (nothing was executed), so Gate 1
        # never fires here.
        decision = classify_failover_eligibility(
            FailoverContext(exception=attempt_exc, process_info=adapter.last_process_info)
        )
        if not decision.eligible:
            logger.debug(
                "structured_classify: failover suppressed for model=%s: %s",
                model_id,
                decision.reason,
            )
            metrics.record_failover_suppressed(reason=decision.reason)
            return None

        attempted_ids.append(catalog_entry_id)
        if attempt_count >= _MAX_FAILOVER_ATTEMPTS:
            logger.warning(
                "structured_classify: same-tier failover safety cap "
                "(_MAX_FAILOVER_ATTEMPTS=%d) reached for tier=%s",
                _MAX_FAILOVER_ATTEMPTS,
                effective_tier,
            )
            metrics.record_failover_exhausted(tier=effective_tier)
            return None

        next_candidate = await next_same_tier_candidate(
            pool, butler_name, effective_tier, attempted_ids
        )
        if next_candidate is None:
            metrics.record_failover_exhausted(tier=effective_tier)
            return None

        previous_model_id = model_id
        (
            runtime_type,
            model_id,
            _extra_args,
            catalog_entry_id,
            session_timeout_s,
        ) = next_candidate
        metrics.record_failover_attempt(
            from_model=previous_model_id,
            to_model=model_id,
            reason=decision.reason.split(":")[0],
        )
