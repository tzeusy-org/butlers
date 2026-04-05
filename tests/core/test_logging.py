"""Tests for structured logging module."""

from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_logging():
    """Reset logging and butler context between tests."""
    token = _butler_context.set(None)
    yield
    _butler_context.reset(token)
    # Restore root logger — close file handlers before clearing to avoid ResourceWarning
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
    root.handlers.clear()
    root.filters.clear()
    root.setLevel(logging.WARNING)
    # Clear file handlers leaked onto noise loggers
    for name in _NOISE_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            handler.close()
        logger.handlers.clear()


# ---------------------------------------------------------------------------
# ContextVar accessors
# ---------------------------------------------------------------------------


class TestButlerContext:
    def test_set_get_and_inject(self):
        """set/get ContextVar; add_butler_context injects name or None."""
        assert get_butler_context() is None

        set_butler_context("health")
        assert get_butler_context() == "health"

        set_butler_context("switchboard")
        result = add_butler_context(None, "info", {"event": "test"})
        assert result["butler"] == "switchboard"

        # Unset context — butler=None, no crash
        from butlers.core.logging import _butler_context

        tok = _butler_context.set(None)
        try:
            result2 = add_butler_context(None, "info", {"event": "test"})
            assert result2["butler"] is None
        finally:
            _butler_context.reset(tok)


# ---------------------------------------------------------------------------
# add_otel_context processor
# ---------------------------------------------------------------------------


class TestAddOtelContext:
    def test_otel_context_injection(self):
        """No active span injects zeroed IDs; active span injects real hex IDs."""
        result_no_span = add_otel_context(None, "info", {"event": "test"})
        assert result_no_span["trace_id"] == "0" * 32
        assert result_no_span["span_id"] == "0" * 16

        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            result_span = add_otel_context(None, "info", {"event": "test"})
            assert result_span["trace_id"] != "0" * 32
            assert result_span["span_id"] != "0" * 16
            assert len(result_span["trace_id"]) == 32
            assert len(result_span["span_id"]) == 16
        provider.shutdown()


# ---------------------------------------------------------------------------
# configure_logging()
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_configure_logging_behavior_and_formats(self):
        """Text renderer, level, butler context, noise suppression; json format installs JSONRenderer."""
        configure_logging(fmt="text", butler_name="health", level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert get_butler_context() == "health"
        assert len(root.handlers) >= 1
        formatter = root.handlers[0].formatter
        assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
        assert isinstance(formatter.processors[-1], structlog.dev.ConsoleRenderer)
        assert logging.getLogger("httpx").level >= logging.WARNING
        assert logging.getLogger("uvicorn.access").level >= logging.WARNING

        # Reset before testing JSON format
        for handler in list(root.handlers):
            handler.close()
        root.handlers.clear()
        root.filters.clear()
        root.setLevel(logging.WARNING)

        configure_logging(fmt="json")
        root2 = logging.getLogger()
        assert len(root2.handlers) >= 1
        formatter2 = root2.handlers[0].formatter
        assert isinstance(formatter2, structlog.stdlib.ProcessorFormatter)
        assert isinstance(formatter2.processors[-1], structlog.processors.JSONRenderer)


# ---------------------------------------------------------------------------
# resolve_log_root()
# ---------------------------------------------------------------------------


class TestResolveLogRoot:
    def test_log_root_resolution(self, monkeypatch: pytest.MonkeyPatch):
        """Default=logs; DISABLE_FILE_LOGGING=1 disables; BUTLERS_LOG_ROOT=none disables."""
        monkeypatch.delenv("BUTLERS_LOG_ROOT", raising=False)
        monkeypatch.delenv("BUTLERS_DISABLE_FILE_LOGGING", raising=False)
        assert resolve_log_root(None) == Path("logs")

        monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "1")
        assert resolve_log_root("/tmp/ignored") is None

        monkeypatch.setenv("BUTLERS_LOG_ROOT", "none")
        monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "0")
        assert resolve_log_root("/tmp/ignored") is None


# ---------------------------------------------------------------------------
# Log directory structure
# ---------------------------------------------------------------------------


class TestLogDirectoryStructure:
    def test_directory_structure_and_files(self, tmp_path: Path):
        """Creates subdirs; butler/uvicorn logs land in correct dirs; file is always JSON."""
        configure_logging(fmt="text", log_root=tmp_path, butler_name="health")
        assert (tmp_path / "butlers").is_dir()
        assert (tmp_path / "uvicorn").is_dir()
        assert (tmp_path / "connectors").is_dir()

        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        assert str(file_handlers[0].baseFilename).endswith("butlers/health.log")
        # File handler always JSON regardless of console format
        formatter = file_handlers[0].formatter
        assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
        assert isinstance(formatter.processors[-1], structlog.processors.JSONRenderer)

        uvicorn_logger = logging.getLogger("uvicorn.access")
        uv_handlers = [h for h in uvicorn_logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(uv_handlers) >= 1
        assert str(uv_handlers[0].baseFilename).endswith("uvicorn/health.log")

    def test_nested_log_root_and_json_output(self, tmp_path: Path):
        """Nested path auto-created; log file writes parseable JSON."""
        log_dir = tmp_path / "deep" / "nested"
        configure_logging(fmt="json", log_root=log_dir, butler_name="jsontest")
        assert (log_dir / "butlers").is_dir()

        test_logger = logging.getLogger("butlers.test")
        test_logger.info("hello structured world")

        log_file = log_dir / "butlers" / "jsontest.log"
        assert log_file.exists()
        content = log_file.read_text().strip()
        if content:
            data = json.loads(content)
            assert data["event"] == "hello structured world"
            assert "butler" in data


# ---------------------------------------------------------------------------
# CredentialRedactionFilter
# ---------------------------------------------------------------------------


class TestCredentialRedactionFilter:
    """Unit tests for the CredentialRedactionFilter logging.Filter."""

    def _make_record(self, msg: str, *args) -> logging.LogRecord:
        """Build a minimal LogRecord with the given message and args."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=msg,
            args=args,
            exc_info=None,
        )
        return record

    def test_credential_redaction(self):
        """Telegram token, Bearer token redacted; clean messages pass through."""
        f = CredentialRedactionFilter()

        # Telegram token
        r1 = self._make_record(
            "HTTP Request: GET https://api.telegram.org/bot8448271413:AAHelloWorldToken_xyz/getUpdates"
        )
        f.filter(r1)
        assert "[REDACTED]" in r1.msg
        assert "8448271413" not in r1.msg

        # Bearer token
        r2 = self._make_record("Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.abc")
        f.filter(r2)
        assert "[REDACTED]" in r2.msg
        assert "eyJhbGciOiJSUzI1NiJ9" not in r2.msg

        # Both patterns + args cleared on redaction
        r3 = self._make_record(
            "GET https://api.telegram.org/bot999:SecretTok_en/getMe Authorization: Bearer abc123"
        )
        f.filter(r3)
        assert "bot999:SecretTok_en" not in r3.msg
        assert "abc123" not in r3.msg

        # Args cleared on redaction; preserved when no redaction
        r4 = self._make_record(
            "URL: https://api.telegram.org/bot123:TOKEN_abc/sendMessage extra=%s", "value"
        )
        f.filter(r4)
        assert r4.args == ()

        r5 = self._make_record("Status: %s", "ok")
        f.filter(r5)
        assert r5.args == ("ok",)

        # Clean message unchanged; filter always returns True
        original = "Normal log message without credentials"
        r6 = self._make_record(original)
        assert f.filter(r6) is True
        assert r6.msg == original

    def test_configure_logging_attaches_and_deduplicates_filter(self):
        """configure_logging() attaches CredentialRedactionFilter; calling twice yields only one; httpx suppressed."""
        configure_logging()
        root = logging.getLogger()
        redaction_filters = [f for f in root.filters if isinstance(f, CredentialRedactionFilter)]
        assert len(redaction_filters) == 1

        # Calling again must not duplicate the filter
        configure_logging()
        root2 = logging.getLogger()
        redaction_filters2 = [f for f in root2.filters if isinstance(f, CredentialRedactionFilter)]
        assert len(redaction_filters2) == 1

        # httpx suppressed and redaction filter provides secondary defense
        assert logging.getLogger("httpx").level >= logging.WARNING
        f = CredentialRedactionFilter()
        record = self._make_record(
            "HTTP Request: GET https://api.telegram.org/bot8448271413:ATokenHere123/getUpdates"
        )
        f.filter(record)
        assert "ATokenHere123" not in record.msg
