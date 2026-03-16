"""Shared helpers for switchboard routing benchmarks.

The system prompt is derived from the production switchboard's message-triage
skill (roster/switchboard/.agents/skills/message-triage/SKILL.md) and routing
prompt builder (src/butlers/modules/pipeline.py::_build_routing_prompt).

Adapted for local Ollama models: instead of MCP tool calls, the model responds
with a single butler name.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

VALID_BUTLERS = frozenset({
    "general", "relationship", "health", "finance", "travel", "education", "home",
})

# ---------------------------------------------------------------------------
# System prompt — derived from production message-triage SKILL.md
# ---------------------------------------------------------------------------

# Read the real SKILL.md at import time so the benchmark always tracks
# the production classification rules.
_SKILL_PATH = (
    Path(__file__).resolve().parents[2]  # tests/benchmarks/switchboard -> repo root/tests
    / ".."  # -> repo root
    / "roster" / "switchboard" / ".agents" / "skills" / "message-triage" / "SKILL.md"
).resolve()

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


def _load_system_prompt() -> str:
    """Build the benchmark system prompt from the production SKILL.md."""
    if _SKILL_PATH.exists():
        skill_text = _SKILL_PATH.read_text()
        # Strip YAML frontmatter
        if skill_text.startswith("---"):
            end = skill_text.find("---", 3)
            if end != -1:
                skill_text = skill_text[end + 3:].strip()
        # Strip sections that reference MCP tool calls (not available in benchmark)
        # Keep classification rules, safety rules, confidence scoring, decision matrix
        sections_to_strip = [
            "## Execution Contract",
            "## Routing via `route_to_butler` Tool",
            "## Outbound Delivery via `notify` Tool",
            "## Implementation Notes",
        ]
        for section in sections_to_strip:
            idx = skill_text.find(section)
            if idx == -1:
                continue
            # Find next ## heading or end of text
            next_heading = skill_text.find("\n## ", idx + len(section))
            if next_heading != -1:
                skill_text = skill_text[:idx] + skill_text[next_heading:]
            else:
                skill_text = skill_text[:idx]
        return _BENCH_PREAMBLE + skill_text.strip() + _BENCH_SUFFIX
    # Fallback if SKILL.md not found (shouldn't happen in repo)
    return _BENCH_PREAMBLE + _FALLBACK_RULES + _BENCH_SUFFIX


_FALLBACK_RULES = """\
## Available Butlers

- finance: Receipts, invoices, bills, subscriptions, transaction alerts, spending queries
- relationship: Contacts, interactions, reminders, gifts, social events
- health: Medications, measurements, conditions, symptoms, exercise, diet, nutrition
- travel: Flight bookings, hotel reservations, car rentals, trip itineraries, travel documents
- education: Personalized tutoring, quizzes, spaced repetition, learning progress
- home: Smart home devices, automations, scenes, energy management, comfort settings
- general: Last-resort fallback — only when no specialist butler matches

## Routing Safety Rules

- All food, meal, and nutrition mentions route to health (not general)
- Finance wins on explicit payment/billing/subscription semantics
- Travel wins on explicit booking/itinerary/flight semantics
- Education wins on explicit learning/teaching/quiz intent or technical questions
- Education does NOT capture health questions without tutoring intent
- General is last-resort only — never route to general if a specialist matches
"""

ROUTING_SYSTEM_PROMPT = _load_system_prompt()


def build_routing_prompt(entry: dict) -> str:
    """Build a routing prompt from a scenario entry.

    Mirrors production's JSON-encoded message format from _build_routing_prompt.
    """
    import json
    encoded = json.dumps({"message": entry["text"]}, ensure_ascii=False)
    return f"Route this message to the appropriate butler.\n\nUser input JSON:\n{encoded}"


def parse_butler(raw: str) -> str | None:
    """Extract a valid butler name from model output.

    Returns the butler name if found, or None if unparseable.
    """
    cleaned = _THINK_RE.sub("", raw).strip().lower()
    # Exact match
    if cleaned in VALID_BUTLERS:
        return cleaned
    # First word match (model might add explanation)
    first_word = cleaned.split()[0].strip(".:,\"'") if cleaned else ""
    if first_word in VALID_BUTLERS:
        return first_word
    # Search for any butler name in the response
    for butler in VALID_BUTLERS:
        if re.search(rf"\b{butler}\b", cleaned):
            return butler
    return None


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
