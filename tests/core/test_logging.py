"""Tests for structured logging module."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import structlog

from butlers.core.logging import (
    _NOISE_LOGGERS,
    _butler_context,
    add_butler_context,
    add_otel_context,
    configure_logging,
    get_butler_context,
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
