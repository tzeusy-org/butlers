"""Shared helpers for switchboard routing benchmarks.

Uses the **real production code** end-to-end:

- ``OpenCodeAdapter`` from ``src/butlers/core/runtimes/opencode.py`` to spawn
  the LLM via ``opencode run`` (for opencode-compatible models)
- Direct Ollama HTTP API calls for ``ollama/`` models (OpenCode does not
  register Ollama models in its provider registry)
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
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from butlers.core.runtimes.opencode import OpenCodeAdapter
from butlers.modules.pipeline import _build_routing_prompt, _extract_routed_butlers

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

VALID_BUTLERS = frozenset(
    {
        "general",
        "relationship",
        "health",
        "finance",
        "travel",
        "education",
        "home",
    }
)

SWITCHBOARD_CONFIG_DIR = Path(__file__).resolve().parents[3] / "roster" / "switchboard"

# ---------------------------------------------------------------------------
# Butler registry snapshot — matches switchboard.butler_registry in prod.
# ---------------------------------------------------------------------------

BUTLER_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "education",
        "description": (
            "Personalized tutor with spaced repetition, mind maps, and adaptive learning"
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
            "calendar",
            "contacts",
            "health",
            "home_assistant",
            "memory",
        ],
    },
    {
        "name": "home",
        "description": (
            "Smart home automation orchestrator for comfort, energy, and device management"
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
# OpenAI-compatible tool definitions for direct Ollama calls
# ---------------------------------------------------------------------------

_ROUTE_TOOL = {
    "type": "function",
    "function": {
        "name": "route_to_butler",
        "description": "Route a message to a specialist butler.",
        "parameters": {
            "type": "object",
            "properties": {
                "butler": {"type": "string", "description": "Target butler name"},
                "prompt": {"type": "string", "description": "The message to route"},
                "context": {
                    "type": "string",
                    "description": "Optional context",
                    "default": None,
                },
                "complexity": {
                    "type": "string",
                    "description": "Complexity tier: trivial, medium, high, extra_high",
                    "default": "medium",
                },
            },
            "required": ["butler", "prompt"],
        },
    },
}

_NOTIFY_TOOL = {
    "type": "function",
    "function": {
        "name": "notify",
        "description": "Send an outbound notification.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Notification channel"},
                "message": {"type": "string", "description": "Message body"},
                "recipient": {"type": "string", "description": "Recipient"},
                "subject": {"type": "string", "description": "Subject line"},
                "intent": {"type": "string", "description": "Intent tag"},
            },
            "required": ["channel", "message"],
        },
    },
}


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
# LLM call — OpenCodeAdapter or direct Ollama HTTP
# ---------------------------------------------------------------------------


def _build_env() -> dict[str, str]:
    """Build environment for the opencode subprocess."""
    return dict(os.environ)


def _load_system_prompt() -> str:
    """Load the switchboard system prompt from its config directory."""
    agents_md = SWITCHBOARD_CONFIG_DIR / "AGENTS.md"
    if agents_md.exists():
        content = agents_md.read_text().strip()
        if len(content) >= 50:
            return content
    claude_md = SWITCHBOARD_CONFIG_DIR / "CLAUDE.md"
    if claude_md.exists():
        return claude_md.read_text().strip()
    return ""


def _call_routing_ollama(
    prompt: str,
    *,
    ollama_url: str,
    model: str,
    timeout: float,
) -> dict:
    """Call Ollama directly via its OpenAI-compatible /v1/chat/completions endpoint.

    Sends the routing prompt with tool definitions for route_to_butler and notify.
    Parses tool calls from the response using the same production extractor.
    """
    base_url = ollama_url.rstrip("/").removesuffix("/v1")
    url = f"{base_url}/v1/chat/completions"
    system_prompt = _load_system_prompt()

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "tools": [_ROUTE_TOOL, _NOTIFY_TOOL],
        "temperature": 0.0,
    }

    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
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
    data = resp.json()

    # Parse tool calls from OpenAI-format response
    tool_calls: list[dict[str, Any]] = []
    result_text = ""
    usage_data = data.get("usage", {})

    for choice in data.get("choices", []):
        msg = choice.get("message", {})
        content = msg.get("content")
        if isinstance(content, str) and content:
            result_text = _THINK_RE.sub("", content).strip()
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            tool_calls.append({
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "input": args if isinstance(args, dict) else {},
            })

    routed, _acked, _failed = _extract_routed_butlers(tool_calls)
    routed_to = routed[0] if routed else None
    if not routed_to and result_text:
        routed_to = parse_butler(result_text)

    return {
        "routed_to": routed_to,
        "routed_targets": routed,
        "raw": result_text,
        "tool_calls": tool_calls,
        "latency_ms": elapsed,
        "prompt_tokens": usage_data.get("prompt_tokens", 0),
        "completion_tokens": usage_data.get("completion_tokens", 0),
        "error": None,
    }


def _call_routing_opencode(
    prompt: str,
    *,
    mock_mcp_url: str,
    model: str,
    timeout: float,
) -> dict:
    """Route via OpenCodeAdapter subprocess (original path)."""
    adapter = OpenCodeAdapter()
    system_prompt = adapter.parse_system_prompt_file(SWITCHBOARD_CONFIG_DIR)
    if len(system_prompt) < 50:
        claude_md = SWITCHBOARD_CONFIG_DIR / "CLAUDE.md"
        if claude_md.exists():
            system_prompt = claude_md.read_text().strip()

    mcp_servers = {"switchboard": {"url": mock_mcp_url}}

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
    routed, _acked, _failed = _extract_routed_butlers(tool_calls)
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


def call_routing(
    prompt: str,
    *,
    mock_mcp_url: str,
    model: str,
    ollama_url: str | None = None,
    timeout: float = 120.0,
) -> dict:
    """Route a message and capture the routing decision.

    For ``ollama/*`` models, calls the Ollama HTTP API directly with tool
    definitions (OpenCode does not register Ollama models). For all other
    models, spawns ``opencode run`` via OpenCodeAdapter with a mock MCP server.

    Returns:
        dict with keys: routed_to, routed_targets, raw, tool_calls,
        latency_ms, prompt_tokens, completion_tokens, error
    """
    if model.startswith("ollama/") and ollama_url:
        ollama_model = model.removeprefix("ollama/")
        return _call_routing_ollama(
            prompt,
            ollama_url=ollama_url,
            model=ollama_model,
            timeout=timeout,
        )

    return _call_routing_opencode(
        prompt,
        mock_mcp_url=mock_mcp_url,
        model=model,
        timeout=timeout,
    )
