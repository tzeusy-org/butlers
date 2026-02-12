"""Message classification — classify and decompose messages across butlers."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from butlers.tools.switchboard.registry import discover_butlers, list_butlers

logger = logging.getLogger(__name__)
_DEFAULT_ROSTER_DIR = Path(__file__).resolve().parents[3]


async def _load_available_butlers(pool: Any) -> list[dict[str, Any]]:
    """Load butlers from registry; auto-discover from roster when empty."""
    butlers = await list_butlers(pool)
    if butlers:
        return butlers

    try:
        await discover_butlers(pool, _DEFAULT_ROSTER_DIR)
        butlers = await list_butlers(pool)
    except Exception:
        logger.exception(
            "classify_message: failed to auto-discover butlers from %s",
            _DEFAULT_ROSTER_DIR,
        )

    return butlers


async def classify_message(
    pool: Any,
    message: str,
    dispatch_fn: Any,
) -> list[dict[str, str]]:
    """Use CC spawner to classify and decompose a message across butlers.

    Spawns a CC instance that sees the butler registry and determines
    which butler(s) should handle the message.  If the message spans
    multiple domains the CC instance decomposes it into distinct
    sub-messages, each tagged with the target butler.

    Returns a list of dicts with keys ``'butler'`` and ``'prompt'``.
    For single-domain messages the list contains exactly one entry.
    Falls back to ``[{'butler': 'general', 'prompt': message}]`` when
    classification fails.
    """
    fallback = [{"butler": "general", "prompt": message}]

    butlers = await _load_available_butlers(pool)
    butler_list = "\n".join(
        f"- {b['name']}: {b.get('description') or 'No description'}" for b in butlers
    )

    prompt = (
        "Analyze the following message and determine which butler(s) should handle it.\n"
        "If the message spans multiple domains, decompose it into distinct sub-messages,\n"
        "each tagged with the appropriate butler.\n\n"
        f"Available butlers:\n{butler_list}\n\n"
        f"Message: {message}\n\n"
        'Respond with ONLY a JSON array. Each element must have keys "butler" and "prompt".\n'
        "Example for a single-domain message:\n"
        '[{"butler": "health", "prompt": "Log weight at 75kg"}]\n'
        "Example for a multi-domain message:\n"
        '[{"butler": "health", "prompt": "Log weight at 75kg"}, '
        '{"butler": "relationship", "prompt": "Remind me to call Mom on Tuesday"}]\n'
        "Respond with ONLY the JSON array, no other text."
    )

    try:
        result = await dispatch_fn(prompt=prompt, trigger_source="tick")
        if result and hasattr(result, "result") and result.result:
            return _parse_classification(result.result, butlers, message)
    except Exception:
        logger.exception("Classification failed")

    return fallback


async def classify_message_multi(
    pool: Any,
    message: str,
    dispatch_fn: Any,
) -> list[str]:
    """Back-compat helper returning only target butler names.

    Older callers/tests expect a list like ``["health", "email"]`` rather than
    structured decomposition entries. This wrapper preserves that interface while
    delegating classification to :func:`classify_message`.
    """
    butlers = await _load_available_butlers(pool)
    known = {b["name"] for b in butlers}

    def _extract_targets(entries: list[dict[str, str]]) -> list[str]:
        targets: list[str] = []
        for entry in entries:
            butler_name = entry.get("butler", "").strip().lower()
            if butler_name and butler_name not in targets:
                targets.append(butler_name)
        return targets

    try:
        result = await dispatch_fn(prompt=message, trigger_source="tick")
        if result and hasattr(result, "result") and result.result:
            raw = str(result.result).strip()

            # Newer format: JSON decomposition payload.
            parsed_entries = _parse_classification(raw, butlers, message)
            parsed_targets = _extract_targets(parsed_entries)
            if parsed_targets != ["general"]:
                return parsed_targets

            # Legacy format: comma/newline separated names (e.g. "health, email").
            candidates = [c.strip().lower() for c in re.split(r"[,\n]", raw) if c.strip()]
            legacy_targets: list[str] = []
            for candidate in candidates:
                if candidate in known and candidate not in legacy_targets:
                    legacy_targets.append(candidate)
            if legacy_targets:
                return legacy_targets
    except Exception:
        logger.exception("Legacy multi-classification failed")

    entries = await classify_message(pool, message, dispatch_fn)
    targets = _extract_targets(entries)
    return targets or ["general"]


def _parse_classification(
    raw: str,
    butlers: list[dict[str, Any]],
    original_message: str,
) -> list[dict[str, str]]:
    """Parse the JSON classification response from CC.

    Validates that each entry references a known butler and has the
    required keys.  Returns the fallback on any parse or validation
    error.
    """
    fallback = [{"butler": "general", "prompt": original_message}]
    known = {b["name"] for b in butlers}

    try:
        parsed = json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        logger.warning("classify_message: failed to parse JSON: %s", raw)
        return fallback

    if not isinstance(parsed, list) or len(parsed) == 0:
        return fallback

    entries: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            return fallback
        butler_name = item.get("butler", "").strip().lower()
        sub_prompt = item.get("prompt", "").strip()
        if not butler_name or not sub_prompt:
            return fallback
        if butler_name not in known:
            return fallback
        entries.append({"butler": butler_name, "prompt": sub_prompt})

    return entries if entries else fallback
