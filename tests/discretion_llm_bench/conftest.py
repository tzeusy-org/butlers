"""Pytest configuration for discretion LLM benchmarks.

These tests hit a live Ollama endpoint and are NOT run in CI/CD.
Run manually with:

    uv run pytest tests/discretion-llm-bench/ -v --ollama-url https://ollama.parrot-hen.ts.net

To compare models:

    uv run pytest tests/discretion-llm-bench/ -v --model gemma3:4b
    uv run pytest tests/discretion-llm-bench/ -v --model qwen3:4b
    uv run pytest tests/discretion-llm-bench/ -v --model gemma3:1b
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROMPTS_FILE = Path(__file__).parent / "prompts.jsonl"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--ollama-url",
        default="https://ollama.parrot-hen.ts.net",
        help="Ollama base URL (without /v1 suffix)",
    )
    parser.addoption(
        "--model",
        default="gemma3:4b",
        help="Model name to benchmark (e.g. gemma3:4b, qwen3:4b)",
    )
    parser.addoption(
        "--bench-timeout",
        default=10.0,
        type=float,
        help="Per-request timeout in seconds (default: 10)",
    )


@pytest.fixture(scope="session")
def ollama_url(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--ollama-url").rstrip("/")


@pytest.fixture(scope="session")
def model_name(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--model")


@pytest.fixture(scope="session")
def bench_timeout(request: pytest.FixtureRequest) -> float:
    return request.config.getoption("--bench-timeout")


@pytest.fixture(scope="session")
def prompts() -> list[dict]:
    """Load all test prompts from prompts.jsonl."""
    entries = []
    with PROMPTS_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


@pytest.fixture(scope="session")
def forward_prompts(prompts: list[dict]) -> list[dict]:
    return [p for p in prompts if p["expected"] == "FORWARD"]


@pytest.fixture(scope="session")
def ignore_prompts(prompts: list[dict]) -> list[dict]:
    return [p for p in prompts if p["expected"] == "IGNORE"]
