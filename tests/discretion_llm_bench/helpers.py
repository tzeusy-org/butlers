"""Shared helpers for discretion LLM benchmarks."""

from __future__ import annotations

import time

import httpx

# Re-use the actual discretion layer's system prompt and template.
from butlers.connectors.discretion import (
    _DEFAULT_SYSTEM_PROMPT as _SYSTEM_PROMPT,
)
from butlers.connectors.discretion import (
    _USER_PROMPT_TEMPLATE,
    _parse_verdict,
)


def build_prompt(entry: dict) -> str:
    """Build a discretion prompt from a test fixture entry."""
    context = entry.get("context", [])
    if context:
        context_lines = "\n".join(f"[{i + 1}] (webcam) {line}" for i, line in enumerate(context))
    else:
        context_lines = "(none)"

    return _USER_PROMPT_TEMPLATE.format(
        n=len(context),
        context=context_lines,
        source="webcam",
        text=entry["text"],
    )


def call_discretion(
    prompt: str,
    *,
    ollama_url: str,
    model: str,
    timeout: float = 10.0,
) -> dict:
    """Call the discretion LLM and return structured result.

    Returns:
        dict with keys: verdict, reason, raw, latency_ms, prompt_tokens,
        completion_tokens, error
    """
    url = f"{ollama_url}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 64,
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
            "verdict": None,
            "reason": "",
            "raw": "",
            "latency_ms": elapsed,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "error": str(exc),
        }

    elapsed = (time.perf_counter() - t0) * 1000
    data = resp.json()
    raw = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})

    try:
        verdict, reason = _parse_verdict(raw)
    except ValueError:
        verdict, reason = None, ""

    return {
        "verdict": verdict,
        "reason": reason,
        "raw": raw,
        "latency_ms": elapsed,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "error": None,
    }
