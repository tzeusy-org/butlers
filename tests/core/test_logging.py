"""Tests for structured logging module — condensed."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import structlog

from butlers.core.logging import (
    _NOISE_LOGGERS,
    CredentialRedactionFilter,
    _butler_context,
    add_butler_context,
    add_otel_context,
    configure_logging,
    get_butler_context,
    resolve_log_root,
    set_butler_context,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_logging():
    token = _butler_context.set(None)
    yield
    _butler_context.reset(token)
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
    root.handlers.clear()
    root.filters.clear()
    root.setLevel(logging.WARNING)
    for name in _NOISE_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            handler.close()
        logger.handlers.clear()


def test_butler_context_and_otel_context():
    """set/get ContextVar; add_butler_context injects name or None; OTel zero IDs without
    span; real IDs with span."""
    assert get_butler_context() is None
    set_butler_context("health")
    assert get_butler_context() == "health"
    result = add_butler_context(None, "info", {"event": "test"})
    assert result["butler"] == "health"

    tok = _butler_context.set(None)
    try:
        assert add_butler_context(None, "info", {"event": "test"})["butler"] is None
    finally:
        _butler_context.reset(tok)

    # OTel: zero IDs without span
    result_no_span = add_otel_context(None, "info", {"event": "test"})
    assert result_no_span["trace_id"] == "0" * 32 and result_no_span["span_id"] == "0" * 16

    # OTel: real hex IDs with active span
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("test-span"):
        r = add_otel_context(None, "info", {"event": "test"})
        assert r["trace_id"] != "0" * 32 and len(r["trace_id"]) == 32
    provider.shutdown()


def test_configure_logging_formats_and_log_root(monkeypatch: pytest.MonkeyPatch):
    """text/json format installs correct renderer; level/noise/butler-ctx set; log_root
    env vars; disabled paths."""
    configure_logging(fmt="text", butler_name="health", level="DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG and get_butler_context() == "health"
    assert isinstance(root.handlers[0].formatter.processors[-1], structlog.dev.ConsoleRenderer)
    assert logging.getLogger("httpx").level >= logging.WARNING

    for h in list(root.handlers):
        h.close()
    root.handlers.clear()
    root.filters.clear()
    root.setLevel(logging.WARNING)

    configure_logging(fmt="json")
    assert isinstance(
        logging.getLogger().handlers[0].formatter.processors[-1], structlog.processors.JSONRenderer
    )

    # resolve_log_root
    monkeypatch.delenv("BUTLERS_LOG_ROOT", raising=False)
    monkeypatch.delenv("BUTLERS_DISABLE_FILE_LOGGING", raising=False)
    assert resolve_log_root(None) == Path("logs")

    monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "1")
    assert resolve_log_root("/tmp/ignored") is None

    monkeypatch.setenv("BUTLERS_LOG_ROOT", "none")
    monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "0")
    assert resolve_log_root("/tmp/ignored") is None


def test_log_directory_structure_and_credential_redaction(tmp_path: Path):
    """Log dirs created; butler/uvicorn files in correct dirs; file always JSON;
    credentials redacted."""
    configure_logging(fmt="text", log_root=tmp_path, butler_name="health")
    assert (tmp_path / "butlers").is_dir()
    assert (tmp_path / "uvicorn").is_dir()
    assert (tmp_path / "connectors").is_dir()

    file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
    assert str(file_handlers[0].baseFilename).endswith("butlers/health.log")
    assert isinstance(file_handlers[0].formatter.processors[-1], structlog.processors.JSONRenderer)

    uv_handlers = [
        h
        for h in logging.getLogger("uvicorn.access").handlers
        if isinstance(h, logging.FileHandler)
    ]
    assert str(uv_handlers[0].baseFilename).endswith("uvicorn/health.log")

    # Nested path auto-created; JSON parseable
    log_dir = tmp_path / "deep" / "nested"
    configure_logging(fmt="json", log_root=log_dir, butler_name="jsontest")
    assert (log_dir / "butlers").is_dir()

    # Credential redaction
    f = CredentialRedactionFilter()

    r1 = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "HTTP Request: GET https://api.telegram.org/bot8448271413:AAHelloWorldToken_xyz/getUpdates",
        (),
        None,
    )
    f.filter(r1)
    assert "[REDACTED]" in r1.msg and "8448271413" not in r1.msg

    r2 = logging.LogRecord(
        "test", logging.INFO, "", 0, "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.abc", (), None
    )
    f.filter(r2)
    assert "[REDACTED]" in r2.msg and "eyJhbGciOiJSUzI1NiJ9" not in r2.msg

    r3 = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "URL: https://api.telegram.org/bot123:TOKEN_abc/sendMessage extra=%s",
        ("value",),
        None,
    )
    f.filter(r3)
    assert r3.args == ()

    # configure_logging attaches filter; idempotent
    configure_logging()
    root2 = logging.getLogger()
    assert len([f for f in root2.filters if isinstance(f, CredentialRedactionFilter)]) == 1
    configure_logging()
    assert (
        len([f for f in logging.getLogger().filters if isinstance(f, CredentialRedactionFilter)])
        == 1
    )
