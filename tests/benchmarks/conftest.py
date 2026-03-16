"""Shared configuration for all Ollama model benchmark suites.

NOT run in CI/CD. Run manually with --override-ini="addopts=" to bypass
the default marker/ignore exclusions:

    uv run pytest tests/benchmarks/ -v --override-ini="addopts=" --model gemma3:4b
    uv run pytest tests/benchmarks/discretion_layer/ -v --override-ini="addopts=" --model gemma3:4b
    uv run pytest tests/benchmarks/switchboard/ -v --override-ini="addopts=" --model gemma3:4b

JUnit XML output:

    uv run pytest tests/benchmarks/ -v --override-ini="addopts=" \\
        --model gemma3:4b --junit-xml=bench-results.xml
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--ollama-url",
        default="https://ollama.parrot-hen.ts.net",
        help="Ollama base URL",
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
