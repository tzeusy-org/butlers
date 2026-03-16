"""Shared helpers for discretion LLM benchmarks."""

from __future__ import annotations

import re
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

# Strip <think>...</think> blocks that reasoning models (qwen3, deepseek-r1, etc.)
# may emit before the actual verdict in some API configurations.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


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

    Calls Ollama's native ``/api/chat`` endpoint directly for raw
    latency measurement, with ``think: false`` so reasoning models
    produce a direct answer.

    Returns:
        dict with keys: verdict, reason, raw, latency_ms, prompt_tokens,
        completion_tokens, error
    """
    base_url = ollama_url.rstrip("/").removesuffix("/v1")
    url = f"{base_url}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "think": False,
        "stream": False,
        "options": {
            "num_predict": 64,
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
    raw = data["message"]["content"]

    # Strip any <think> blocks that may have leaked through.
    cleaned = _THINK_RE.sub("", raw).strip()

    try:
        verdict, reason = _parse_verdict(cleaned)
    except ValueError:
        verdict, reason = None, ""

    return {
        "verdict": verdict,
        "reason": reason,
        "raw": raw,
        "latency_ms": elapsed,
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
        "error": None,
    }
