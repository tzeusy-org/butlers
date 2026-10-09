"""Bounded, content-blind runtime observations, distinct from requested routing.

Only adapter-owned top-level metadata enters this module. CLI usage-model
labels are reports, not proof of the provider deployment. Missing observations
remain unknown; neither a configured model nor a previous call fills them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any

MAX_BYTES = 16 * 1024
MAX_EXECUTIONS = 8
MAX_MODELS = 16
_MAX_INT = 2**63 - 1
_MODEL = re.compile(
    r"(?:[a-zA-Z0-9_.-]+/)?(?:claude|gpt|o[1-9]|gemini|codex|qwen|llama|"
    r"deepseek|mistral|ministral|nemotron|glm|kimi|minimax|groq|command|"
    r"phi|gemma|sonnet|opus|haiku)[a-zA-Z0-9_.:+-]*(?:\[1m\])?\Z"
)
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
_PACKAGES = {"@anthropic-ai/claude-code", "@openai/codex", "@google/gemini-cli", "opencode-ai"}
_RUNTIME_PACKAGE = {
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
    "gemini": "@google/gemini-cli",
    "opencode": "opencode-ai",
}
_BUCKETS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
_SUBTYPES = {
    "error_max_turns": "max_turns",
    "error_max_budget_usd": "max_budget",
    "error_during_execution": "execution_error",
    "error_max_structured_output_retries": "structured_output_retries",
    "success": None,
}
# Pinned Gemini CLI 0.46.0 emits these names through getErrorType() in a
# terminal result. Unrecognized types retain the generic category, never text.
_GEMINI_ERROR_TYPES = {
    "FatalTurnLimitedError": "max_turns",
    "FatalAuthenticationError": "provider_auth",
}
_STATES = {"observed", "partial", "unknown", "malformed", "truncated", "not_invoked"}
_AUTHORITIES = {"provider_response", "cli_usage_breakdown", "configured_only", "unavailable"}
_PROVENANCE = {"provider_response", "provider_or_request_fallback", "cli_reported_unproven"}
_FINDINGS = {
    "metadata_truncated",
    "conflicting_finals",
    "cli_version_drift",
    "multiple_models",
    "requested_not_served",
    "malformed_identity",
}
_ERRORS = {
    "max_turns",
    "max_budget",
    "execution_error",
    "structured_output_retries",
    "provider_auth",
    "rate_limit",
    "provider_unavailable",
    "permission_denied",
    "unknown_error",
    "timeout",
    "owner_cancelled",
}
_REASONS = {"not_emitted", "not_applicable", "malformed", "scope_unproved", "not_invoked"}


def token(value: object) -> int | None:
    """Accept an actual nonnegative JSON integer, never bool/coerced text."""
    return value if type(value) is int and 0 <= value <= _MAX_INT else None


def model_id(value: object) -> str | None:
    # The allowlist is ASCII, so its character and UTF-8 byte limits coincide.
    # Check without encoding untrusted JSON strings (which may contain a lone surrogate).
    if isinstance(value, str) and len(value) <= 128 and _MODEL.fullmatch(value):
        return value
    return None


def version(value: object) -> str | None:
    if isinstance(value, str) and len(value) <= 64 and _VERSION.fullmatch(value):
        return value
    return None


def cost(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
        if result.is_finite() and 0 <= result <= Decimal("1000000000"):
            return format(result.quantize(Decimal("0.000000001")), "f")
    except (InvalidOperation, ValueError):
        pass
    return None


def buckets(raw: object, *, camel: bool = False) -> dict[str, int | None]:
    source = raw if isinstance(raw, dict) else {}
    keys = (
        ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
        if camel
        else _BUCKETS
    )
    return {name: token(source.get(key)) for name, key in zip(_BUCKETS, keys, strict=True)}


def expected_toolchain() -> dict[str, Any]:
    """Read only the fixed image manifest; never execute/probe a provider CLI."""
    try:
        fd = os.open("/opt/cli-versions.txt", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return {"state": "unknown", "reason": "manifest_unavailable"}
            data = os.read(fd, 65537)
        finally:
            os.close(fd)
        if len(data) > 65536:
            raise ValueError
        found = {}
        for line in data.decode("utf-8").splitlines():
            # npm's installed package manifest is emitted as package@version.
            package, separator, reported = (
                line.strip().split()[-1].rpartition("@") if line.strip() else ("", "", "")
            )
            if package not in _PACKAGES:
                continue
            if not separator or package in found or version(reported) is None:
                raise ValueError
            found[package] = reported
        if not found:
            raise ValueError
        return {"state": "observed", "versions": found, "sha256": hashlib.sha256(data).hexdigest()}
    except (OSError, ValueError, UnicodeError):
        return {"state": "unknown", "reason": "manifest_unavailable"}


def unknown(runtime: str, *, state: str = "unknown") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "runtime_type": runtime,
        "observation_state": state,
        "executions": [],
        "finding_codes": [],
        "expected_toolchain": expected_toolchain(),
    }


def _execution(configured: object, completion: str) -> dict[str, Any]:
    return {
        "execution_index": 0,
        "completion_state": completion,
        "configured_model_id": model_id(configured),
        "reported_models": [],
        "served_models": [],
        "identity_authority": "unavailable",
        "cli_version_reported": None,
        "reported_cost": {
            "value": None,
            "unit": "USD",
            "source": "cli_estimate",
            "scope": "unknown",
        },
        "error": {"is_error": None, "code": None, "subtype": None, "permission_denial_count": None},
        "aggregate_usage": buckets(None),
        "model_usage": [],
    }


def _gemini_buckets(raw: object) -> dict[str, int | None]:
    source = raw if isinstance(raw, dict) else {}
    prompt, cached, input_count = (
        token(source.get(k)) for k in ("input_tokens", "cached", "input")
    )
    # Pinned Gemini has both prompt (inclusive) and input (uncached).
    if prompt is not None and cached is not None:
        derived = prompt - cached if cached <= prompt else None
        if input_count is None:
            input_count = derived
        elif derived != input_count:
            input_count = None
    return {
        "input_tokens": input_count,
        "output_tokens": token(source.get("output_tokens")),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": None,
    }


def stream_evidence(
    runtime: str, stdout: str, *, configured: object = None, completion: str = "success"
) -> dict[str, Any]:
    """Project only owned final/init metadata, with bounded scanning and no content."""
    record = unknown(runtime)
    execution = _execution(configured, completion)
    record["executions"] = [execution]
    finals = []
    scanned = 0
    # Bound the extra parser allocation as well as the byte scan. The existing
    # subprocess capture is outside this module; never split an unbounded
    # captured transcript merely to discard its tail afterward.
    scan_window = stdout[: 8 * 1024 * 1024 + 65537]
    if len(scan_window) < len(stdout):
        record["observation_state"] = "truncated"
        record["finding_codes"].append("metadata_truncated")
    for line in scan_window.splitlines():
        size = len(line.encode("utf-8"))
        scanned += size
        if size > 65536 or scanned > 8 * 1024 * 1024:
            record["observation_state"] = "truncated"
            record["finding_codes"].append("metadata_truncated")
            if scanned > 8 * 1024 * 1024:
                break
            continue
        try:
            obj = json.loads(line, parse_float=Decimal)
        except (ValueError, InvalidOperation):
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("type")
        if runtime == "claude" and kind == "system" and obj.get("subtype") == "init":
            execution["configured_model_id"] = model_id(obj.get("model"))
            execution["cli_version_reported"] = version(obj.get("claude_code_version"))
        if runtime == "gemini" and kind == "init":
            execution["configured_model_id"] = model_id(obj.get("model"))
        final = _execution(execution["configured_model_id"], completion)
        final["cli_version_reported"] = execution["cli_version_reported"]
        if runtime == "claude" and kind == "result":
            final["aggregate_usage"] = buckets(obj.get("usage"))
            final["reported_cost"]["value"] = cost(obj.get("total_cost_usd"))
            error = obj.get("is_error")
            subtype = obj.get("subtype")
            subtype = subtype if isinstance(subtype, str) else None
            final["error"].update(
                {
                    "is_error": error if type(error) is bool else None,
                    "subtype": subtype if subtype in _SUBTYPES else None,
                    "code": _SUBTYPES.get(subtype, "unknown_error") if error is True else None,
                }
            )
            denials = obj.get("permission_denials")
            if isinstance(denials, list):
                final["error"]["permission_denial_count"] = min(len(denials), 65535)
                final["error"]["denials_truncated"] = len(denials) > 65535
            if error is True:
                final["completion_state"] = "error"
            raw_models = obj.get("modelUsage")
            if isinstance(raw_models, dict):
                _add_models(final, raw_models, runtime)
        elif runtime == "codex" and kind == "turn.completed":
            raw = obj.get("usage")
            raw = raw if isinstance(raw, dict) else {}
            inclusive, cached = (
                token(raw.get("input_tokens")),
                token(raw.get("cached_input_tokens")),
            )
            final["aggregate_usage"] = {
                "input_tokens": inclusive - cached
                if inclusive is not None and cached is not None and cached <= inclusive
                else None,
                "output_tokens": token(raw.get("output_tokens")),
                "cache_read_input_tokens": cached,
                "cache_creation_input_tokens": token(raw.get("cache_write_input_tokens")),
            }
            if token(raw.get("cache_write_input_tokens", 0)) != 0:
                final["aggregate_usage"]["input_tokens"] = None
                final["cache_overlap_state"] = "unknown"

        elif runtime == "codex" and kind in {"turn.failed", "error"}:
            final["completion_state"] = "error"
            final["error"].update({"is_error": True, "code": "execution_error"})
        elif runtime == "gemini" and kind == "result":
            stats = obj.get("stats")
            stats = stats if isinstance(stats, dict) else {}
            raw_models = stats.get("models")
            if isinstance(raw_models, dict):
                _add_models(final, raw_models, runtime)
            # Stream output may expose the same token aggregate directly.
            final["aggregate_usage"] = _gemini_buckets(stats)
            if obj.get("status") == "error":
                final["completion_state"] = "error"
                raw_error = obj.get("error")
                raw_type = raw_error.get("type") if isinstance(raw_error, dict) else None
                code = (
                    _GEMINI_ERROR_TYPES.get(raw_type, "execution_error")
                    if isinstance(raw_type, str)
                    else "execution_error"
                )
                final["error"].update({"is_error": True, "code": code})
        else:
            continue
        finals.append(final)
    if finals:
        # Compare normalized evidence only, not content/result/session identifiers.
        selected = finals[-1]
        fatal = [item for item in finals if item["completion_state"] == "error"]
        if fatal:
            selected = fatal[-1]
        conflict = any(item != selected for item in finals)
        record["executions"] = [selected]
        if conflict:
            record["observation_state"] = "partial"
            record["finding_codes"].append("conflicting_finals")
        elif record["observation_state"] != "truncated":
            record["observation_state"] = "observed"
    else:
        record["observation_state"] = "partial" if stdout else "unknown"
    return bounded(record)


def _add_models(execution: dict, raw_models: dict, runtime: str) -> None:
    valid = sorted(key for key in raw_models if model_id(key) is not None)
    if len(valid) != len(raw_models):
        execution["malformed_identity"] = True
    execution["model_count"] = len(raw_models)
    execution["models_truncated"] = len(valid) > MAX_MODELS
    for name in valid[:MAX_MODELS]:
        raw = raw_models[name]
        raw = raw if isinstance(raw, dict) else {}
        provenance = (
            "cli_reported_unproven" if runtime == "claude" else "provider_or_request_fallback"
        )
        execution["reported_models"].append(
            {
                "model_id": name,
                "source": "result.modelUsage" if runtime == "claude" else "result.stats.models",
                "provenance": provenance,
            }
        )
        usage = buckets(raw, camel=True) if runtime == "claude" else _gemini_buckets(raw)
        execution["model_usage"].append(
            {
                "model_id": name,
                **usage,
                "identity_authority": "cli_usage_breakdown",
                "provenance": provenance,
                "reported_cost_usd": cost(raw.get("costUSD")),
                "cost_scope": "unknown",
                "evidence_state": "cumulative_unknown" if runtime == "claude" else "partial",
            }
        )
    if execution["reported_models"]:
        execution["identity_authority"] = "cli_usage_breakdown"


def bounded(record: dict) -> dict:
    """Detach an allowlisted projection before persistence/worker handoff.

    Apply the same projection on reads: neither an accidentally attached raw
    event nor mutable stored JSON can add content fields to this surface.
    """
    runtime = record.get("runtime_type")
    runtime = runtime if runtime in {"claude", "codex", "gemini", "opencode", "api"} else "unknown"
    result = {
        "schema_version": 1,
        "runtime_type": runtime,
        "observation_state": record.get("observation_state")
        if record.get("observation_state") in _STATES
        else "malformed",
        "executions": [],
        "finding_codes": sorted(set(record.get("finding_codes", [])) & _FINDINGS),
        "expected_toolchain": {"state": "unknown", "reason": "manifest_unavailable"},
    }
    toolchain = record.get("expected_toolchain")
    if isinstance(toolchain, dict) and toolchain.get("state") == "observed":
        versions = toolchain.get("versions", {})
        digest = toolchain.get("sha256")
        if (
            isinstance(versions, dict)
            and isinstance(digest, str)
            and re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            result["expected_toolchain"] = {
                "state": "observed",
                "sha256": digest,
                "versions": {k: v for k, v in versions.items() if k in _PACKAGES and version(v)},
            }
    raw_executions = record.get("executions", [])
    raw_executions = raw_executions if isinstance(raw_executions, list) else []
    for raw in raw_executions[:MAX_EXECUTIONS]:
        if not isinstance(raw, dict):
            result["observation_state"] = "malformed"
            continue
        item = _execution(raw.get("configured_model_id"), "unknown")
        item["execution_index"] = len(result["executions"])
        if raw.get("completion_state") in {"success", "error", "timeout", "cancelled", "unknown"}:
            item["completion_state"] = raw["completion_state"]
        item["cli_version_reported"] = version(raw.get("cli_version_reported"))
        if raw.get("identity_authority") in _AUTHORITIES:
            item["identity_authority"] = raw["identity_authority"]
        item["served_models"] = sorted({v for v in raw.get("served_models", []) if model_id(v)})[
            :MAX_MODELS
        ]
        for model in raw.get("reported_models", [])[:MAX_MODELS]:
            if not isinstance(model, dict) or not model_id(model.get("model_id")):
                continue
            if model.get("source") not in {
                "result.modelUsage",
                "result.stats.models",
                "api.response.model",
            }:
                continue
            if model.get("provenance") not in _PROVENANCE:
                continue
            item["reported_models"].append(
                {k: model[k] for k in ("model_id", "source", "provenance")}
            )
        item["aggregate_usage"] = buckets(raw.get("aggregate_usage"))
        for model in raw.get("model_usage", [])[:MAX_MODELS]:
            if not isinstance(model, dict) or not model_id(model.get("model_id")):
                continue
            if (
                model.get("identity_authority") not in _AUTHORITIES
                or model.get("provenance") not in _PROVENANCE
            ):
                continue
            item["model_usage"].append(
                {
                    "model_id": model["model_id"],
                    **buckets(model),
                    "identity_authority": model["identity_authority"],
                    "provenance": model["provenance"],
                    "reported_cost_usd": _project_cost(model.get("reported_cost_usd")),
                    "cost_scope": model.get("cost_scope")
                    if model.get("cost_scope")
                    in {"invocation", "conversation_cumulative", "unknown"}
                    else "unknown",
                    "evidence_state": model.get("evidence_state")
                    if model.get("evidence_state") in _STATES | {"cumulative_unknown"}
                    else "unknown",
                }
            )
        reported_cost = raw.get("reported_cost", {})
        if isinstance(reported_cost, dict):
            item["reported_cost"]["value"] = _project_cost(reported_cost.get("value"))
            if reported_cost.get("scope") in {"invocation", "conversation_cumulative", "unknown"}:
                item["reported_cost"]["scope"] = reported_cost["scope"]
        error = raw.get("error", {})
        if isinstance(error, dict):
            item["error"].update(
                {
                    "is_error": error.get("is_error")
                    if type(error.get("is_error")) is bool
                    else None,
                    "code": error.get("code")
                    if isinstance(error.get("code"), str) and error["code"] in _ERRORS
                    else None,
                    "subtype": error.get("subtype")
                    if isinstance(error.get("subtype"), str) and error["subtype"] in _SUBTYPES
                    else None,
                    "permission_denial_count": token(error.get("permission_denial_count"))
                    if token(error.get("permission_denial_count")) is not None
                    and error["permission_denial_count"] <= 65535
                    else None,
                }
            )
            if type(error.get("denials_truncated")) is bool:
                item["error"]["denials_truncated"] = error["denials_truncated"]
        for key in ("models_truncated", "malformed_identity"):
            if type(raw.get(key)) is bool:
                item[key] = raw[key]
        if token(raw.get("model_count")) is not None:
            item["model_count"] = raw["model_count"]
        if raw.get("cache_overlap_state") == "unknown":
            item["cache_overlap_state"] = "unknown"
        item["unknown_reasons"] = {
            key: "not_applicable"
            if key == "cli_version_reported" and runtime == "api"
            else "scope_unproved"
            if key == "served_models"
            else "not_emitted"
            for key in ("configured_model_id", "cli_version_reported", "served_models")
            if not item[key]
        }
        item["unknown_reasons"].update(
            {k: "not_emitted" for k, v in item["aggregate_usage"].items() if v is None}
        )
        prior_reasons = raw.get("unknown_reasons", {})
        if isinstance(prior_reasons, dict):
            item["unknown_reasons"].update(
                {
                    key: value
                    for key, value in prior_reasons.items()
                    if key in item["unknown_reasons"]
                    and isinstance(value, str)
                    and value in _REASONS
                }
            )
        if item["reported_cost"]["value"] is None:
            item["unknown_reasons"]["reported_cost"] = "not_emitted"
        elif item["reported_cost"]["scope"] == "unknown":
            item["unknown_reasons"]["reported_cost_scope"] = "scope_unproved"
        result["executions"].append(item)
    count = max(len(raw_executions), token(record.get("execution_count")) or 0)
    if count > len(result["executions"]):
        result["execution_count"] = count
        result["observation_state"] = "truncated"
    # PostgreSQL JSONB text includes separators with whitespace, so bound that
    # representation as well as the compact wire form.
    while len(json.dumps(result).encode()) > MAX_BYTES and result["executions"]:
        result["executions"].pop()
        result["observation_state"] = "truncated"
        result["execution_count"] = count
    return result


def _project_cost(value: object) -> str | None:
    # Canonical receipts already contain checked decimal strings; raw source
    # strings are still rejected by cost() at the extraction boundary.
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,9})?", value):
        return cost(Decimal(value))
    return cost(value)


def snapshot(adapter: Any) -> dict:
    info = adapter.last_process_info
    raw = info.get("served") if isinstance(info, dict) else None
    runtime = info.get("runtime_type", "unknown") if isinstance(info, dict) else "unknown"
    return bounded(raw) if isinstance(raw, dict) else unknown(runtime)


def observe_invocation(runtime: str):
    """Finalize every exit on an exclusively owned worker before caller awaits."""

    def decorate(invoke):
        @wraps(invoke)
        async def observed(self, *args, **kwargs):
            self._last_process_info = None
            self._served_executions = []
            completion = "success"
            try:
                return await invoke(self, *args, **kwargs)
            except BaseException as exc:
                import asyncio

                completion = (
                    "cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "timeout"
                    if isinstance(exc, TimeoutError)
                    else "error"
                )
                raise
            finally:
                record = unknown(runtime)
                records = self._served_executions
                record["executions"] = []
                for observed_record in records:
                    for execution in observed_record["executions"]:
                        item = deepcopy(execution)
                        item["execution_index"] = len(record["executions"])
                        record["executions"].append(item)
                    record["finding_codes"].extend(observed_record["finding_codes"])
                if records:
                    states = {r["observation_state"] for r in records}
                    record["observation_state"] = (
                        "truncated"
                        if "truncated" in states
                        else ("observed" if states == {"observed"} else "partial")
                    )
                if completion != "success":
                    record["observation_state"] = "partial" if records else "unknown"
                    if not record["executions"]:
                        record["executions"] = [_execution(kwargs.get("model"), completion)]
                    record["executions"][-1]["completion_state"] = completion
                info = self._last_process_info
                if not isinstance(info, dict):
                    info = {"runtime_type": runtime}
                    self._last_process_info = info
                info["served"] = bounded(record)

        return observed

    return decorate


def append_stream(
    adapter: Any, runtime: str, stdout: str, *, configured: object, completion: str
) -> dict:
    record = stream_evidence(runtime, stdout, configured=configured, completion=completion)
    # Internal subprocess tests can enter below invoke's finalizer. Each real
    # invocation resets this collector before any await; no previous metadata
    # is used to fill an observation.
    if not hasattr(adapter, "_served_executions"):
        adapter._served_executions = []
    adapter._served_executions.append(record)
    return record


def api_evidence(response: Any, usage: dict | None, *, configured: object) -> dict:
    record = unknown("api")
    execution = _execution(configured, "success")
    execution["aggregate_usage"] = buckets(usage)
    served = model_id(getattr(response, "model", None))
    if served:
        execution["served_models"] = [served]
        execution["identity_authority"] = "provider_response"
        execution["model_usage"] = [
            {
                "model_id": served,
                **execution["aggregate_usage"],
                "identity_authority": "provider_response",
                "provenance": "provider_response",
                "reported_cost_usd": None,
                "cost_scope": "invocation",
                "evidence_state": "observed",
            }
        ]
    record["executions"] = [execution]
    record["observation_state"] = "observed"
    return bounded(record)


class TerminalResultError(RuntimeError):
    """Exit zero with a failed terminal result; no raw provider error text."""

    def __init__(self, served: dict, usage: dict | None, tool_calls: list[dict]):
        super().__init__("Runtime reported a failed terminal result")
        self.served = bounded(served)
        self.usage = deepcopy(usage)
        self.tool_calls = deepcopy(tool_calls)


def compare(record: dict, requested: object) -> dict:
    """Qualify comparisons; CLI labels/partial evidence never imply a mismatch."""
    result = bounded(record)
    findings = set(result["finding_codes"])
    expected = result["expected_toolchain"].get("versions", {})
    package = _RUNTIME_PACKAGE.get(result["runtime_type"])
    for execution in result["executions"]:
        observed = execution.get("cli_version_reported")
        if observed and expected.get(package) and observed != expected[package]:
            findings.add("cli_version_drift")
        actual = execution["served_models"]
        if len(actual) > 1:
            findings.add("multiple_models")
        if (
            result["observation_state"] == "observed"
            and model_id(requested)
            and execution["identity_authority"] == "provider_response"
            and actual
            and requested not in actual
        ):
            findings.add("requested_not_served")
    result["finding_codes"] = sorted(findings)
    return bounded(result)
