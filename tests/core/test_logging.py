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
    # Restore root logger
    root = logging.getLogger()
    root.handlers.clear()
    root.filters.clear()
    root.setLevel(logging.WARNING)
    # Clear file handlers leaked onto noise loggers
    for name in _NOISE_LOGGERS:
        logging.getLogger(name).handlers.clear()


# ---------------------------------------------------------------------------
# ContextVar accessors
# ---------------------------------------------------------------------------


class TestButlerContext:
    def test_set_and_get(self):
        set_butler_context("health")
        assert get_butler_context() == "health"

    def test_default_is_none(self):
        assert get_butler_context() is None


# ---------------------------------------------------------------------------
# add_butler_context processor
# ---------------------------------------------------------------------------


class TestAddButlerContext:
    def test_injects_butler_name(self):
        set_butler_context("switchboard")
        event_dict = {"event": "test"}
        result = add_butler_context(None, "info", event_dict)
        assert result["butler"] == "switchboard"

    def test_handles_unset_context(self):
        """ContextVar not set — butler=None, no crash."""
        event_dict = {"event": "test"}
        result = add_butler_context(None, "info", event_dict)
        assert result["butler"] is None


# ---------------------------------------------------------------------------
# add_otel_context processor
# ---------------------------------------------------------------------------


class TestAddOtelContext:
    def test_zeroed_ids_when_no_span(self):
        """No active OTel span — injects zeroed trace_id and span_id."""
        event_dict = {"event": "test"}
        result = add_otel_context(None, "info", event_dict)
        assert result["trace_id"] == "0" * 32
        assert result["span_id"] == "0" * 16

    def test_real_ids_when_span_active(self):
        """Active OTel span — injects real hex trace_id and span_id."""
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            event_dict = {"event": "test"}
            result = add_otel_context(None, "info", event_dict)
            assert result["trace_id"] != "0" * 32
            assert result["span_id"] != "0" * 16
            assert len(result["trace_id"]) == 32
            assert len(result["span_id"]) == 16
        provider.shutdown()


# ---------------------------------------------------------------------------
# configure_logging()
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_text_format_installs_console_renderer(self):
        configure_logging(fmt="text")
        root = logging.getLogger()
        assert len(root.handlers) >= 1
        handler = root.handlers[0]
        formatter = handler.formatter
        assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
        # The last processor should be ConsoleRenderer
        last_proc = formatter.processors[-1]
        assert isinstance(last_proc, structlog.dev.ConsoleRenderer)

    def test_json_format_installs_json_renderer(self):
        configure_logging(fmt="json")
        root = logging.getLogger()
        assert len(root.handlers) >= 1
        handler = root.handlers[0]
        formatter = handler.formatter
        assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
        last_proc = formatter.processors[-1]
        assert isinstance(last_proc, structlog.processors.JSONRenderer)

    def test_sets_butler_context(self):
        configure_logging(butler_name="health")
        assert get_butler_context() == "health"

    def test_noise_loggers_suppressed(self):
        configure_logging()
        assert logging.getLogger("httpx").level >= logging.WARNING
        assert logging.getLogger("httpcore").level >= logging.WARNING
        assert logging.getLogger("uvicorn.access").level >= logging.WARNING

    def test_log_level_applied(self):
        configure_logging(level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG


# ---------------------------------------------------------------------------
# resolve_log_root()
# ---------------------------------------------------------------------------


class TestResolveLogRoot:
    def test_defaults_to_logs_when_unset(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("BUTLERS_LOG_ROOT", raising=False)
        monkeypatch.delenv("BUTLERS_DISABLE_FILE_LOGGING", raising=False)
        assert resolve_log_root(None) == Path("logs")

    def test_disable_file_logging_env_wins(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("BUTLERS_LOG_ROOT", raising=False)
        monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "1")
        assert resolve_log_root("/tmp/ignored") is None

    def test_log_root_env_can_disable_with_sentinel(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BUTLERS_LOG_ROOT", "none")
        monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "0")
        assert resolve_log_root("/tmp/ignored") is None


# ---------------------------------------------------------------------------
# Log directory structure
# ---------------------------------------------------------------------------


class TestLogDirectoryStructure:
    def test_creates_subdirectories(self, tmp_path: Path):
        """log_root creates butlers/, uvicorn/, connectors/ subdirs."""
        configure_logging(log_root=tmp_path, butler_name="testbot")
        assert (tmp_path / "butlers").is_dir()
        assert (tmp_path / "uvicorn").is_dir()
        assert (tmp_path / "connectors").is_dir()

    def test_butler_log_file_created(self, tmp_path: Path):
        """Butler log lands in butlers/ subdir."""
        configure_logging(log_root=tmp_path, butler_name="health")
        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        assert str(file_handlers[0].baseFilename).endswith("butlers/health.log")

    def test_uvicorn_log_file_created(self, tmp_path: Path):
        """Noise loggers write to uvicorn/ subdir."""
        configure_logging(log_root=tmp_path, butler_name="health")
        uvicorn_logger = logging.getLogger("uvicorn.access")
        file_handlers = [h for h in uvicorn_logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) >= 1
        assert str(file_handlers[0].baseFilename).endswith("uvicorn/health.log")

    def test_file_handler_always_json(self, tmp_path: Path):
        """File handler uses JSON renderer regardless of console format."""
        configure_logging(fmt="text", log_root=tmp_path, butler_name="testbot")
        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        formatter = file_handlers[0].formatter
        assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
        last_proc = formatter.processors[-1]
        assert isinstance(last_proc, structlog.processors.JSONRenderer)

    def test_nested_log_root_created(self, tmp_path: Path):
        """log_root with nested path is created if missing."""
        log_dir = tmp_path / "deep" / "nested"
        configure_logging(log_root=log_dir, butler_name="testbot")
        assert (log_dir / "butlers").is_dir()

    def test_json_output_is_valid_json(self, tmp_path: Path):
        """Butler log file writes parseable JSON."""
        configure_logging(fmt="json", log_root=tmp_path, butler_name="jsontest")
        test_logger = logging.getLogger("butlers.test")
        test_logger.info("hello structured world")

        log_file = tmp_path / "butlers" / "jsontest.log"
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

    def test_telegram_token_in_url_is_redacted(self):
        """Telegram bot token in URL path is replaced with [REDACTED]."""
        f = CredentialRedactionFilter()
        record = self._make_record(
            "HTTP Request: GET https://api.telegram.org/bot8448271413:AAHelloWorldToken_xyz/getUpdates"
        )
        f.filter(record)
        assert "[REDACTED]" in record.msg
        assert "8448271413" not in record.msg
        assert "AAHelloWorldToken_xyz" not in record.msg

    def test_bearer_token_is_redacted(self):
        """Bearer token in Authorization header is scrubbed."""
        f = CredentialRedactionFilter()
        record = self._make_record(
            "Sending request with Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.abc"
        )
        f.filter(record)
        assert "[REDACTED]" in record.msg
        assert "eyJhbGciOiJSUzI1NiJ9" not in record.msg

    def test_clean_message_unchanged(self):
        """Messages with no credential patterns pass through unmodified."""
        f = CredentialRedactionFilter()
        original = "Normal log message without credentials"
        record = self._make_record(original)
        f.filter(record)
        assert record.msg == original

    def test_filter_always_returns_true(self):
        """Filter never drops records — always returns True."""
        f = CredentialRedactionFilter()
        record = self._make_record("anything")
        assert f.filter(record) is True

    def test_args_cleared_on_redaction(self):
        """When redaction occurs, args are cleared to prevent re-interpolation."""
        f = CredentialRedactionFilter()
        record = self._make_record(
            "URL: https://api.telegram.org/bot123:TOKEN_abc/sendMessage extra=%s",
            "value",
        )
        f.filter(record)
        assert record.args == ()
        assert "[REDACTED]" in record.msg

    def test_args_preserved_when_no_redaction(self):
        """When no redaction occurs, args are left intact."""
        f = CredentialRedactionFilter()
        record = self._make_record("Status: %s", "ok")
        f.filter(record)
        assert record.args == ("ok",)

    def test_multiple_patterns_in_single_message(self):
        """Both Telegram token and Bearer token in same message are both redacted."""
        f = CredentialRedactionFilter()
        record = self._make_record(
            "GET https://api.telegram.org/bot999:SecretTok_en/getMe Authorization: Bearer abc123"
        )
        f.filter(record)
        # Token path redacted
        assert "bot999:SecretTok_en" not in record.msg
        # Bearer redacted
        assert "abc123" not in record.msg

    def test_configure_logging_attaches_redaction_filter(self):
        """configure_logging() attaches CredentialRedactionFilter to root logger."""
        configure_logging()
        root = logging.getLogger()
        redaction_filters = [f for f in root.filters if isinstance(f, CredentialRedactionFilter)]
        assert len(redaction_filters) == 1

    def test_configure_logging_called_twice_has_single_filter(self):
        """Calling configure_logging() twice does not accumulate duplicate filters."""
        configure_logging()
        configure_logging()
        root = logging.getLogger()
        redaction_filters = [f for f in root.filters if isinstance(f, CredentialRedactionFilter)]
        assert len(redaction_filters) == 1

    def test_httpx_url_with_token_is_suppressed_or_redacted(self):
        """httpx logger set to WARNING means INFO token URLs never reach handlers.

        This test verifies that after configure_logging(), a simulated httpx
        INFO record with a bot token in the URL would be redacted if it were
        to pass the level gate (defense-in-depth).
        """
        configure_logging()
        # Verify httpx is suppressed to WARNING (primary defense)
        assert logging.getLogger("httpx").level >= logging.WARNING

        # Verify the redaction filter would also scrub the token (secondary defense)
        f = CredentialRedactionFilter()
        record = self._make_record(
            "HTTP Request: GET https://api.telegram.org/bot8448271413:ATokenHere123/getUpdates"
        )
        f.filter(record)
        assert "ATokenHere123" not in record.msg
