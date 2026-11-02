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

from butlers.core.tool_call_capture import (
    fingerprint_tool_call_payload,
    plain_tool_call_fingerprint,
)
from butlers.mcp_wrappers import (
    _VISIBLE_CAPTURE_INPUT_FIELDS,
    _VISIBLE_CAPTURE_INPUT_FIELDS_BY_TOOL,
)

# Runtime-reported records of these tools, and Claude built-ins (tagged ``builtin``), keep their
# raw input: they are work the runtime ran itself, not butler MCP tools with a capture allowlist.
_RAW_INPUT_TOOL_NAMES = frozenset({"command_execution"})
_RAW_INPUT_KEYS = ("input", "args", "arguments", "parameters")


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


def _capture_view(tool_name: str, payload: Any) -> dict[str, Any]:
    """The part of a tool input that may be persisted: the capture allowlist.

    This is the same allowlist the daemon-side wrapper applies
    (``mcp_wrappers._visible_capture_input``), minus signature defaults, which a runtime
    parser cannot know. Anything else a tool received, including parameters it deliberately
    never stores, is dropped.
    """
    if not isinstance(payload, dict):
        return {}
    fields = set(_VISIBLE_CAPTURE_INPUT_FIELDS)
    fields |= _VISIBLE_CAPTURE_INPUT_FIELDS_BY_TOOL.get(tool_name, frozenset())
    return {key: payload[key] for key in fields if key in payload}


def _json_normalized(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _strip_parsed_input(call: dict[str, Any], butler_name: str) -> dict[str, Any]:
    """Normalize a parser record's name and reduce its raw input to the capture allowlist.

    The record keeps a keyed fingerprint of the full raw input, so pairing with the daemon
    record and the degenerate-loop check still tell different inputs apart.
    """
    normalized = dict(call)
    raw_name = str(normalized.get("name", "") or "")
    # The stored name stays as reported: the merge normalizes it exactly once.
    name = _normalize_tool_name(raw_name, butler_name)
    if (
        normalized.get("builtin") is True
        or name in _RAW_INPUT_TOOL_NAMES
        or raw_name in _RAW_INPUT_TOOL_NAMES
    ):
        return normalized
    payload = _payload_for_signature(normalized)
    has_raw_input = any(key in normalized for key in _RAW_INPUT_KEYS)
    if not has_raw_input:
        return normalized
    visible = _capture_view(name, payload)
    if payload is not None and visible != payload and not normalized.get("input_fingerprint"):
        # The allowlist view drops information, so keep a keyed fingerprint of the full
        # raw input to tell this call from another with the same visible view.
        normalized["input_fingerprint"] = fingerprint_tool_call_payload(payload)
    reported_input = "input" in normalized
    for key in _RAW_INPUT_KEYS:
        normalized.pop(key, None)
    if visible or reported_input:
        # A parser that reported an (even empty) input keeps the key, so record shapes
        # are stable; only what the allowlist admits is left inside it.
        normalized["input"] = visible
    return normalized


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

    Parser records report the raw arguments the model sent, including parameters a tool
    deliberately never stores. Every persisted record therefore carries only the capture
    allowlist view of its input, whether or not it pairs with a daemon record.

    Pairing is by name and keyed input fingerprint first. A parser record cannot know the
    defaults the daemon binds, so a call that omits an optional parameter does not match by
    fingerprint; the remaining records of one tool then pair in call order.
    """
    parsed_calls = [_strip_parsed_input(call, butler_name) for call in parsed_calls]

    def _normalize_name_in_place(call: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(call)
        raw_name = str(normalized.get("name", "") or "")
        normalized["name"] = _normalize_tool_name(raw_name, butler_name)
        return normalized

    if not executed_calls:
        return _dedup_tool_calls_by_id([_normalize_name_in_place(c) for c in parsed_calls])
    if not parsed_calls:
        return _dedup_tool_calls_by_id([_normalize_name_in_place(c) for c in executed_calls])

    def _canonical_name(call: dict[str, Any]) -> str:
        """Fixed point of name normalization, so every runtime form of one tool compares equal.

        A parser reports ``mcp__chronicler__chronicler_day_close_bundle`` where the daemon
        records ``chronicler_day_close_bundle``; one normalization pass leaves them different
        (the second still carries the butler prefix), and the second pass makes them equal.
        """
        name = str(call.get("name", "") or "")
        while True:
            stripped = _normalize_tool_name(name, butler_name)
            if stripped == name:
                return name
            name = stripped

    def _signature(call: dict[str, Any]) -> str:
        fingerprint = call.get("input_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            # A record without a fingerprint persists its whole input, so the comparable
            # form is the plain one the daemon records for a lossless capture view.
            fingerprint = plain_tool_call_fingerprint(_payload_for_signature(call))
        return f"{_canonical_name(call)}|{fingerprint}"

    def _view_agrees(parsed_call: dict[str, Any], executed_call: dict[str, Any]) -> bool:
        """Every allowlisted value the parser reported equals the executed capture's value.

        A call the server rejected before execution (then retried with other values) must
        not take its retry's executed record.
        """
        parsed_view = parsed_call.get("input")
        if not isinstance(parsed_view, dict) or not parsed_view:
            return True
        executed_view = executed_call.get("input")
        if not isinstance(executed_view, dict):
            return False
        return all(
            key in executed_view and _json_normalized(executed_view[key]) == _json_normalized(value)
            for key, value in parsed_view.items()
        )

    # Pair each executed (capture-side) record with an unclaimed parser record:
    # first by name and keyed input fingerprint, then, for what is left, by
    # name in call order (a parser cannot know the defaults the daemon binds),
    # but only with a parser record whose allowlisted view agrees with the execution.
    executed_for_parsed: dict[int, int] = {}
    paired_executed: set[int] = set()

    def _pair(key: Any, *, check_view: bool = False) -> None:
        for executed_index, executed_call in enumerate(executed_calls):
            if executed_index in paired_executed:
                continue
            executed_key = key(executed_call)
            parsed_index = next(
                (
                    idx
                    for idx, parsed_call in enumerate(parsed_calls)
                    if idx not in executed_for_parsed
                    and key(parsed_call) == executed_key
                    and (not check_view or _view_agrees(parsed_call, executed_call))
                ),
                None,
            )
            if parsed_index is not None:
                executed_for_parsed[parsed_index] = executed_index
                paired_executed.add(executed_index)

    _pair(_signature)
    _pair(_canonical_name, check_view=True)

    # Emit in the parser's stream order, which interleaves MCP calls with
    # runtime built-ins (Bash, Read, ...) the capture side never sees; the
    # per-thread loop guard reads adjacency from this order. A capture record
    # with no parser counterpart goes just before the next paired record that
    # executed after it, or at the end.
    merged: list[dict[str, Any]] = []
    next_executed = 0

    def _emit_executed_before(stop: int) -> None:
        nonlocal next_executed
        while next_executed < stop:
            if next_executed not in paired_executed:
                # No parser counterpart; capture name is already bare. Keep as-is.
                merged.append(executed_calls[next_executed])
            next_executed += 1

    for idx, parsed_call in enumerate(parsed_calls):
        executed_index = executed_for_parsed.get(idx)
        if executed_index is None:
            # Unmatched parser record: normalize the stored name so downstream
            # consumers (dashboard, tool-call-scorecard) see one canonical form
            # per tool even when no capture counterpart was recorded.
            merged.append(_normalize_name_in_place(parsed_call))
            continue
        _emit_executed_before(executed_index)
        executed_call = executed_calls[executed_index]
        merged_record = dict(parsed_call)
        merged_record.update(executed_call)
        # Canonical stored name is the bare fn.__name__ form, which the capture-side
        # record carries already. Normalizing it again would strip a tool's own butler
        # prefix (``chronicler_day_close_bundle`` is a bare name), so only a prefixed
        # runtime form is normalized, defensively, so a single invocation always
        # persists under one stable tool name.
        raw_name = str(executed_call.get("name", "") or "")
        if not raw_name or raw_name.startswith("mcp__"):
            raw_name = str(merged_record.get("name", "") or "")
            raw_name = _normalize_tool_name(raw_name, butler_name)
        merged_record["name"] = raw_name
        merged.append(merged_record)
    _emit_executed_before(len(executed_calls))

    return _dedup_tool_calls_by_id(merged)


def _is_mcp_tool_call(call: dict[str, Any]) -> bool:
    """Return whether a record is MCP work rather than a runtime-native tool.

    Runtime-native work is Codex/OpenCode shell (``command_execution``) or a
    Claude CLI built-in, which the Claude adapter marks ``builtin``.
    """
    if call.get("builtin") is True:
        return False
    return str(call.get("name", "") or "") != "command_execution"


def _has_mcp_tool_calls(tool_calls: list[dict[str, Any]]) -> bool:
    """Return whether the record set confirms any MCP tool call."""
    return any(_is_mcp_tool_call(call) for call in tool_calls)
