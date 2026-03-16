"""Shared helpers for switchboard routing benchmarks.

Uses the **real production code** for prompt construction:
- ``_build_routing_prompt`` from ``src/butlers/modules/pipeline.py``
- ``_format_capabilities`` from ``roster/switchboard/tools/routing/classify.py``
- Classification rules from the production ``message-triage`` SKILL.md

The only adaptation for local Ollama models: the SKILL.md content is injected
as a system prompt (since local models don't have Claude Code's skill-loading
system), and the output format asks for a single butler name instead of MCP
tool calls.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Import the REAL production prompt builder
# ---------------------------------------------------------------------------
from butlers.modules.pipeline import _build_routing_prompt

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

VALID_BUTLERS = frozenset({
    "general", "relationship", "health", "finance", "travel", "education", "home",
})

# ---------------------------------------------------------------------------
# Butler registry snapshot — matches production switchboard.butler_registry.
# Used instead of a live DB query to avoid a runtime dependency.
# Update if butlers are added/removed/renamed.
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
# System prompt — production SKILL.md with benchmark output format
# ---------------------------------------------------------------------------

_SKILL_PATH = (
    Path(__file__).resolve().parents[3]  # tests/benchmarks/switchboard -> repo root
    / "roster" / "switchboard" / ".agents" / "skills"
    / "message-triage" / "SKILL.md"
)

_BENCH_PREAMBLE = """\
You are a message router for a personal assistant system. Your job is to \
classify each incoming message and decide which specialist butler should \
handle it.

Reply with EXACTLY one butler name. No explanation, no punctuation, \
no other text — just the butler name.

Valid butler names: general, relationship, health, finance, travel, education, home

"""

_BENCH_SUFFIX = """

REMINDER: Reply with EXACTLY one butler name and nothing else."""


def _load_skill_text() -> str:
    """Load and clean the production SKILL.md."""
    if not _SKILL_PATH.exists():
        return ""
    skill_text = _SKILL_PATH.read_text()
    # Strip YAML frontmatter
    if skill_text.startswith("---"):
        end = skill_text.find("---", 3)
        if end != -1:
            skill_text = skill_text[end + 3:].strip()
    # Strip sections that reference MCP tool calls (not available in benchmark)
    for section in [
        "## Execution Contract",
        "## Routing via `route_to_butler` Tool",
        "## Outbound Delivery via `notify` Tool",
        "## Implementation Notes",
    ]:
        idx = skill_text.find(section)
        if idx == -1:
            continue
        next_heading = skill_text.find("\n## ", idx + len(section))
        if next_heading != -1:
            skill_text = skill_text[:idx] + skill_text[next_heading:]
        else:
            skill_text = skill_text[:idx]
    return skill_text.strip()


ROUTING_SYSTEM_PROMPT = _BENCH_PREAMBLE + _load_skill_text() + _BENCH_SUFFIX


# ---------------------------------------------------------------------------
# Prompt construction — uses real production _build_routing_prompt
# ---------------------------------------------------------------------------


def build_routing_prompt(entry: dict) -> str:
    """Build a routing prompt using the production prompt builder.

    Calls ``_build_routing_prompt`` from ``src/butlers/modules/pipeline.py``
    with the real butler registry, ensuring the benchmark prompt matches
    production exactly (butler descriptions, capability formatting, JSON
    message encoding).
    """
    return _build_routing_prompt(
        message=entry["text"],
        butlers=BUTLER_REGISTRY,
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_butler(raw: str) -> str | None:
    """Extract a valid butler name from model output."""
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
# LLM call
# ---------------------------------------------------------------------------


def call_routing(
    prompt: str,
    *,
    ollama_url: str,
    model: str,
    timeout: float = 10.0,
) -> dict:
    """Call the routing LLM and return structured result.

    Uses the Ollama native ``/api/chat`` endpoint with ``think: false``.

    Returns:
        dict with keys: routed_to, raw, latency_ms, prompt_tokens,
        completion_tokens, error
    """
    base_url = ollama_url.rstrip("/").removesuffix("/v1")
    url = f"{base_url}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ROUTING_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "think": False,
        "stream": False,
        "options": {
            "num_predict": 32,
            "temperature": 0.0,
        },
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
            "raw": "",
            "latency_ms": elapsed,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "error": str(exc),
        }

    elapsed = (time.perf_counter() - t0) * 1000
    data = resp.json()
    raw = data["message"]["content"]
    routed_to = parse_butler(raw)

    return {
        "routed_to": routed_to,
        "raw": raw,
        "latency_ms": elapsed,
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
        "error": None,
    }
