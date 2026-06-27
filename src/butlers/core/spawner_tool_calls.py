"""Spawner — tool-call processing seam.

Responsible for:
 - normalising tool names across runtime-specific prefixes
 - merging parser and capture-side tool-call records
 - deduplicating lifecycle events for the same tool call

Extracted from butlers.core.spawner as part of bu-dl98i.7.1 (structural
decomposition into internal seams).  The Spawner continues to use these via
re-exports so existing import paths and test patches remain valid.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from butlers.core.tool_call_capture import fingerprint_tool_call_payload


def _dedup_tool_calls_by_id(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse records sharing the same non-empty id, keeping the last occurrence.

    This handles lifecycle duplicates (e.g. in_progress + completed events for
    the same tool call) — the last occurrence carries the most complete data.
    Records with no id are always kept.
    """
    seen_ids: dict[str, int] = {}
    for i, call in enumerate(calls):
        call_id = call.get("id")
        if isinstance(call_id, str) and call_id:
            seen_ids[call_id] = i  # last occurrence wins
    if not seen_ids:
        return calls
    keep_indexes = set(seen_ids.values())
    return [
        call
        for i, call in enumerate(calls)
        if i in keep_indexes or not (isinstance(call.get("id"), str) and call.get("id"))
    ]


def _looks_like_mcp_endpoint_alias(alias: str) -> bool:
    """Return whether an MCP server alias is an endpoint-derived identifier.

    Codex/OpenCode should report configured server names such as ``lifestyle``,
    but some runtime builds can echo the remote endpoint identity in the
    ``mcp__<server>__<tool>`` prefix.  Those aliases are environment-specific
    and must still collapse to the same bare tool name as server-side capture.
    """
    if not alias:
        return False
    if "://" in alias:
        try:
            parsed = urlsplit(alias)
        except ValueError:
            return False
        return bool(parsed.scheme and parsed.netloc)
    if "/" in alias or "?" in alias:
        return True
    if alias in {"localhost", "127.0.0.1", "::1"}:
        return True
    return ":" in alias or "." in alias


def _normalize_tool_name(name: str, butler_name: str | None) -> str:
    """Strip runtime-specific prefixes so the same underlying MCP tool matches
    regardless of which runtime emitted the record.

    Runtimes emit the same tool under three forms:

    - bare ``fn.__name__`` (server-side capture in ``tool_call_capture``)
    - ``mcp__{butler_name}__{fn}`` (claude_code / codex parsers)
    - ``{butler_name}_{fn}`` (opencode parser)

    Without normalization the merge-by-(name, payload) signature never matches
    across those forms, causing duplicate rows in ``sessions.tool_calls``.

    Safety rule: we unconditionally strip a leading ``{butler_name}_`` when
    present. Underscores are ambiguous in theory — a butler named ``memory``
    plus a real tool ``entity_resolve`` would collide with a tool named
    ``memory_entity_resolve`` — but in practice this collision is already
    impossible: opencode would emit ``memory_memory_entity_resolve`` vs
    ``memory_entity_resolve`` for the two cases, and the bare
    ``memory_entity_resolve`` form is what the capture side records either
    way, so stripping is safe. No registered tool can start with its own
    butler's name as a prefix without already colliding with opencode's
    prefixed form.
    """
    if not name:
        return name
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3 and _looks_like_mcp_endpoint_alias(parts[1]):
            return parts[2]
    if butler_name:
        mcp_prefix = f"mcp__{butler_name}__"
        if name.startswith(mcp_prefix):
            return name[len(mcp_prefix) :]
        opencode_prefix = f"{butler_name}_"
        if name.startswith(opencode_prefix):
            return name[len(opencode_prefix) :]
    return name


def _merge_tool_call_records(
    parsed_calls: list[dict[str, Any]],
    executed_calls: list[dict[str, Any]],
    *,
    butler_name: str,
) -> list[dict[str, Any]]:
    """Merge parser + executed call records while preserving retry attempts.

    ``butler_name`` drives normalization of runtime-specific tool-name prefixes
    so parser-side records (e.g. ``mcp__lifestyle__memory_entity_resolve`` or
    ``lifestyle_memory_entity_resolve``) collapse against capture-side records
    that use the bare ``fn.__name__`` form. It is required: every production
    caller passes ``self._config.name``, which the config layer guarantees is a
    non-empty butler name.
    """

    def _normalize_name_in_place(call: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(call)
        raw_name = str(normalized.get("name", "") or "")
        normalized["name"] = _normalize_tool_name(raw_name, butler_name)
        return normalized

    if not executed_calls:
        return _dedup_tool_calls_by_id([_normalize_name_in_place(c) for c in parsed_calls])
    if not parsed_calls:
        return _dedup_tool_calls_by_id([_normalize_name_in_place(c) for c in executed_calls])

    def _payload_for_signature(call: dict[str, Any]) -> Any:
        payload = call.get("input")
        if payload is None:
            payload = call.get("args")
        if payload is None:
            payload = call.get("arguments")
        if payload is None:
            payload = call.get("parameters")
        if isinstance(payload, str):
            stripped = payload.strip()
            if stripped:
                try:
                    return json.loads(stripped)
                except Exception:
                    return payload
        return payload

    def _signature(call: dict[str, Any]) -> str:
        raw_name = str(call.get("name", "") or "")
        name = _normalize_tool_name(raw_name, butler_name)
        fingerprint = call.get("input_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            fingerprint = fingerprint_tool_call_payload(_payload_for_signature(call))
        return f"{name}|{fingerprint}"

    merged: list[dict[str, Any]] = []
    matched_parsed_indexes: set[int] = set()

    for executed_call in executed_calls:
        executed_signature = _signature(executed_call)
        parsed_index = next(
            (
                idx
                for idx, parsed_call in enumerate(parsed_calls)
                if (
                    idx not in matched_parsed_indexes
                    and _signature(parsed_call) == executed_signature
                )
            ),
            None,
        )
        if parsed_index is None:
            # No parser counterpart; capture name is already bare. Keep as-is.
            merged.append(executed_call)
            continue
        matched_parsed_indexes.add(parsed_index)
        merged_record = dict(parsed_calls[parsed_index])
        merged_record.update(executed_call)
        # Canonical stored name is the bare fn.__name__ form. The capture-side
        # record carries that form already, but we normalize defensively so a
        # single invocation always persists under one stable tool name.
        raw_name = str(merged_record.get("name", "") or "")
        merged_record["name"] = _normalize_tool_name(raw_name, butler_name)
        merged.append(merged_record)

    for idx, parsed_call in enumerate(parsed_calls):
        if idx in matched_parsed_indexes:
            continue
        # Unmatched parser record: normalize the stored name so downstream
        # consumers (dashboard, tool-call-scorecard) see one canonical form
        # per tool even when no capture counterpart was recorded.
        merged.append(_normalize_name_in_place(parsed_call))

    return _dedup_tool_calls_by_id(merged)


def _has_non_command_tool_calls(tool_calls: list[dict[str, Any]]) -> bool:
    """Return whether the record set includes any non-bash tool call."""
    return any(str(call.get("name", "") or "") != "command_execution" for call in tool_calls)
