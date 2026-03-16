"""Shared helpers for switchboard routing benchmarks."""

from __future__ import annotations

import re
import time

import httpx

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

VALID_BUTLERS = frozenset({
    "general", "relationship", "health", "finance", "travel", "education", "home",
})

ROUTING_SYSTEM_PROMPT = """\
You are a message router for a personal assistant system. Route each incoming \
message to the single most appropriate specialist butler.

Available butlers and their domains:
- general: Freeform questions, notes, reminders, calendar events, trivia, general knowledge
- relationship: Personal contacts, birthdays, anniversaries, interactions, gifts, social events
- health: Health measurements, medications, diet, nutrition, meals, symptoms, conditions, exercise
- finance: Receipts, bills, subscriptions, transactions, spending, budgets, bank alerts
- travel: Flights, hotels, car rentals, itineraries, bookings, travel documents, trips
- education: Learning, teaching, study sessions, mind maps, quizzes, courses, tutoring
- home: Smart home devices, automations, scenes, energy management, comfort settings

Rules:
- All food, meal, and nutrition mentions route to health (not general)
- Route to the most specific butler; use general only when no specialist fits
- Reply with EXACTLY one butler name from the list above, nothing else"""


def build_routing_prompt(entry: dict) -> str:
    """Build a routing prompt from a scenario entry."""
    return f'Route this message: "{entry["text"]}"'


def parse_butler(raw: str) -> str | None:
    """Extract a valid butler name from model output.

    Returns the butler name if found, or None if unparseable.
    """
    cleaned = _THINK_RE.sub("", raw).strip().lower()
    # Exact match
    if cleaned in VALID_BUTLERS:
        return cleaned
    # First word match (model might add explanation)
    first_word = cleaned.split()[0].strip(".:,") if cleaned else ""
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
