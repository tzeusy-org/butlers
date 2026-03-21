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

import sys
import time

import httpx
import pytest

_WARMUP_TIMEOUT = 120  # seconds — large models need time to load into VRAM
_PULL_TIMEOUT = 600  # seconds — pulling a multi-GB model over the network


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


def _pull_model(base: str, model: str) -> bool:
    """Pull a model from the Ollama registry. Returns True on success."""
    sys.stderr.write(f"\n  pull: downloading {model} (this may take a few minutes) ... ")
    sys.stderr.flush()

    t0 = time.monotonic()
    try:
        # Ollama /api/pull streams JSON status lines
        with httpx.stream(
            "POST",
            f"{base}/api/pull",
            json={"model": model},
            timeout=httpx.Timeout(connect=30, read=_PULL_TIMEOUT, write=30, pool=30),
        ) as resp:
            resp.raise_for_status()
            last_status = ""
            for line in resp.iter_lines():
                if not line:
                    continue
                import json

                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                status = msg.get("status", "")
                if status != last_status:
                    sys.stderr.write(f"\n    {status}")
                    sys.stderr.flush()
                    last_status = status
        elapsed = time.monotonic() - t0
        sys.stderr.write(f"\n  pull: complete ({elapsed:.0f}s)\n")
        return True
    except Exception as exc:
        elapsed = time.monotonic() - t0
        sys.stderr.write(f"\n  pull: failed after {elapsed:.0f}s: {exc}\n")
        return False


@pytest.fixture(scope="session", autouse=True)
def _warmup_ollama_model(ollama_url: str, model_name: str) -> None:
    """Pre-load the model into Ollama VRAM before any benchmark runs.

    If the model isn't available (404), pulls it first. Without the warmup,
    rapid sequential requests each trigger a fresh model load that cancels
    the previous one, creating a thrashing loop.
    """
    # Strip ollama/ prefix if present (OpenCode convention)
    model = model_name.removeprefix("ollama/")
    base = ollama_url.rstrip("/").removesuffix("/v1")

    sys.stderr.write(f"\n  warmup: loading {model} on {base} ... ")
    sys.stderr.flush()

    t0 = time.monotonic()
    try:
        resp = httpx.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            },
            timeout=_WARMUP_TIMEOUT,
        )
        resp.raise_for_status()
        elapsed = time.monotonic() - t0
        sys.stderr.write(f"ready ({elapsed:.1f}s)\n")
    except httpx.HTTPStatusError as exc:
        elapsed = time.monotonic() - t0
        if exc.response.status_code == 404:
            sys.stderr.write(f"model not found ({elapsed:.1f}s)\n")
            if not _pull_model(base, model):
                pytest.skip(f"Failed to pull {model} from Ollama registry")
            # Retry warmup after pull
            sys.stderr.write(f"  warmup: loading {model} after pull ... ")
            sys.stderr.flush()
            t0 = time.monotonic()
            try:
                resp = httpx.post(
                    f"{base}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                    },
                    timeout=_WARMUP_TIMEOUT,
                )
                resp.raise_for_status()
                elapsed = time.monotonic() - t0
                sys.stderr.write(f"ready ({elapsed:.1f}s)\n")
            except Exception as exc2:
                elapsed = time.monotonic() - t0
                sys.stderr.write(f"failed after {elapsed:.1f}s: {exc2}\n")
                pytest.skip(f"Model {model} pulled but warmup failed: {exc2}")
        else:
            sys.stderr.write(f"HTTP {exc.response.status_code} after {elapsed:.1f}s — continuing\n")
    except Exception as exc:
        elapsed = time.monotonic() - t0
        sys.stderr.write(f"failed after {elapsed:.1f}s: {exc} — continuing\n")
