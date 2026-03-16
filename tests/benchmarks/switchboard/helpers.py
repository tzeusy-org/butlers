"""Shared helpers for switchboard routing benchmarks.

Uses the **real production code** end-to-end:

- ``OpenCodeAdapter`` from ``src/butlers/core/runtimes/opencode.py`` to spawn
  the LLM via ``opencode run``
- ``_build_routing_prompt`` from ``src/butlers/modules/pipeline.py`` for prompt
  construction (butler list, capabilities, JSON message encoding)
- ``_extract_routed_butlers`` from ``src/butlers/modules/pipeline.py`` to parse
  tool calls from the spawn result
- The real ``message-triage`` SKILL.md is loaded by OpenCode's skill system

The only mock is the MCP server: ``route_to_butler`` captures the routing
decision and returns ``{"status": "ok"}`` without actually dispatching.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

from butlers.core.runtimes.opencode import OpenCodeAdapter
from butlers.modules.pipeline import _build_routing_prompt, _extract_routed_butlers

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

VALID_BUTLERS = frozenset({
    "general", "relationship", "health", "finance", "travel", "education", "home",
})

SWITCHBOARD_CONFIG_DIR = (
    Path(__file__).resolve().parents[3] / "roster" / "switchboard"
)

# ---------------------------------------------------------------------------
# Butler registry snapshot — matches switchboard.butler_registry in prod.
# ---------------------------------------------------------------------------

BUTLER_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "education",
        "description": (
            "Personalized tutor with spaced repetition, mind maps,"
            " and adaptive learning"
        ),
        "modules": ["education", "memory", "contacts"],
    },
    {
        "name": "finance",
        "description": (
            "Personal finance specialist for receipts, bills,"
            " subscriptions, and transaction alerts."
        ),
        "modules": ["email", "calendar", "memory", "finance"],
    },
    {
        "name": "general",
        "description": "Flexible catch-all assistant for freeform data",
        "modules": ["calendar", "contacts", "general", "memory"],
    },
    {
        "name": "health",
        "description": (
            "Health tracking assistant for measurements, medications,"
            " diet, food preferences, nutrition, meals, and symptoms"
        ),
        "modules": [
            "calendar", "contacts", "health", "home_assistant", "memory",
        ],
    },
    {
        "name": "home",
        "description": (
            "Smart home automation orchestrator for comfort, energy,"
            " and device management"
        ),
        "modules": ["home_assistant", "memory", "contacts", "approvals"],
    },
    {
        "name": "relationship",
        "description": (
            "Personal CRM. Manages contacts, relationships, important"
            " dates, interactions, gifts, and reminders."
        ),
        "modules": ["calendar", "contacts", "memory", "relationship"],
    },
    {
        "name": "travel",
        "description": (
            "Travel logistics and itinerary intelligence specialist"
            " for flights, hotels, car rentals, and trip planning."
        ),
        "modules": ["email", "calendar", "memory"],
    },
]


# ---------------------------------------------------------------------------
# Prompt construction — uses real production _build_routing_prompt
# ---------------------------------------------------------------------------


def build_routing_prompt(entry: dict) -> str:
    """Build a routing prompt using the production prompt builder."""
    return _build_routing_prompt(
        message=entry["text"],
        butlers=BUTLER_REGISTRY,
    )


# ---------------------------------------------------------------------------
# Fallback response parsing (when model outputs text instead of tool calls)
# ---------------------------------------------------------------------------


def parse_butler(raw: str) -> str | None:
    """Extract a valid butler name from model text output."""
    cleaned = _THINK_RE.sub("", raw).strip().lower()
    if cleaned in VALID_BUTLERS:
        return cleaned
    first_word = cleaned.split()[0].strip(".:,\"'") if cleaned else ""
    if first_word in VALID_BUTLERS:
        return first_word
    for butler in VALID_BUTLERS:
        if re.search(rf"\b{butler}\b", cleaned):
            return butler
    return None


# ---------------------------------------------------------------------------
# LLM call — real OpenCodeAdapter
# ---------------------------------------------------------------------------


def _build_env() -> dict[str, str]:
    """Build environment for the opencode subprocess.

    Passes PATH plus any provider auth env vars so the spawned process
    can authenticate with model backends.
    """
    env: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
    for key, val in os.environ.items():
        if key.startswith((
            "OPENCODE_", "OPENAI_", "ANTHROPIC_", "OPENROUTER_",
            "GOOGLE_", "GEMINI_",
        )):
            env[key] = val
    return env


def call_routing(
    prompt: str,
    *,
    mock_mcp_url: str,
    model: str,
    timeout: float = 120.0,
) -> dict:
    """Route a message using the real OpenCodeAdapter + mock MCP server.

    Spawns ``opencode run --model <model>`` with the switchboard's system
    prompt and skill files, pointed at a mock MCP server. Parses tool calls
    via the production ``_extract_routed_butlers`` function.

    Returns:
        dict with keys: routed_to, routed_targets, raw, tool_calls,
        latency_ms, prompt_tokens, completion_tokens, error
    """
    adapter = OpenCodeAdapter()
    system_prompt = adapter.parse_system_prompt_file(SWITCHBOARD_CONFIG_DIR)

    mcp_servers = {
        "switchboard": {"url": mock_mcp_url},
    }

    t0 = time.perf_counter()
    try:
        result_text, tool_calls, usage = asyncio.run(
            adapter.invoke(
                prompt=prompt,
                system_prompt=system_prompt,
                mcp_servers=mcp_servers,
                env=_build_env(),
                model=model,
                timeout=int(timeout),
                cwd=SWITCHBOARD_CONFIG_DIR,
            )
        )
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "routed_to": None,
            "routed_targets": [],
            "raw": "",
            "tool_calls": [],
            "latency_ms": elapsed,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "error": str(exc),
        }

    elapsed = (time.perf_counter() - t0) * 1000

    # Use real production extractor to parse routing decisions from tool calls
    routed, acked, failed = _extract_routed_butlers(tool_calls)

    # Primary: use tool-call-based routing. Fallback: parse text output.
    routed_to = routed[0] if routed else None
    if not routed_to and result_text:
        routed_to = parse_butler(result_text)

    return {
        "routed_to": routed_to,
        "routed_targets": routed,
        "raw": result_text or "",
        "tool_calls": tool_calls,
        "latency_ms": elapsed,
        "prompt_tokens": (usage or {}).get("input_tokens", 0),
        "completion_tokens": (usage or {}).get("output_tokens", 0),
        "error": None,
    }
