"""Tests for structured logging module — condensed."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import httpx
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
        logger.setLevel(logging.NOTSET)
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


def test_console_traceback_omits_frame_locals():
    """A logged exception must not render frame locals such as connection kwargs."""
    configure_logging(fmt="text")
    console = next(
        h
        for h in logging.getLogger().handlers
        if isinstance(h.formatter, structlog.stdlib.ProcessorFormatter)
    )

    def connect():
        pool_kwargs = {"user": "butlers", "password": "frame-local-sentinel"}
        raise RuntimeError(f"cannot connect as {pool_kwargs['user']}")

    try:
        connect()
    except RuntimeError:
        record = logging.LogRecord(
            "test", logging.WARNING, "", 0, "connect failed", (), sys.exc_info()
        )

    rendered = console.formatter.format(record)
    assert "cannot connect as butlers" in rendered
    assert "frame-local-sentinel" not in rendered


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

    r4 = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "GET https://x.test/v1?appid=1&key=SECRETKEY&pageToken=c",
        (),
        None,
    )
    f.filter(r4)
    assert "SECRETKEY" not in r4.msg and "appid=1" in r4.msg and "pageToken=c" in r4.msg


_SYNTHETIC_BOT_TOKEN = "123456789:AAsyntheticBotTokenForLogTest_xyz"


def _request(url: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    with httpx.Client(transport=transport) as client:
        client.get(url)


def test_httpx_request_lines_never_leak_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """httpx request logs never carry a credential to the console or log files.

    The INFO request line is suppressed by default; and if a caller turns httpx
    back up to INFO, the propagated record is still redacted by every handler.
    """
    configure_logging(fmt="json", log_root=tmp_path, butler_name="telegram-bot")
    _request(f"https://api.telegram.org/bot{_SYNTHETIC_BOT_TOKEN}/getUpdates")
    assert "HTTP Request" not in capsys.readouterr().err

    logging.getLogger("httpx").setLevel(logging.INFO)
    _request(f"https://api.telegram.org/file/bot{_SYNTHETIC_BOT_TOKEN}/voice/file_1.oga")
    _request("https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?key=STEAMKEY1&x=1")
    # structlog hands handlers its event dict as record.msg; redaction must keep
    # it a dict or ProcessorFormatter raises and the record is dropped.
    structlog.get_logger("butlers.x").info("structlog line", url="https://x.test/v1?key=SKEY2")
    for handler in logging.getLogger().handlers + logging.getLogger("httpx").handlers:
        handler.flush()

    sinks = {
        "console": capsys.readouterr().err,
        "butler file": (tmp_path / "butlers" / "telegram-bot.log").read_text(),
        "transport file": (tmp_path / "uvicorn" / "telegram-bot.log").read_text(),
    }
    assert "HTTP Request" in sinks["console"] and "HTTP Request" in sinks["transport file"]
    assert "structlog line" in sinks["console"] and "structlog line" in sinks["butler file"]
    for name, text in sinks.items():
        assert _SYNTHETIC_BOT_TOKEN.split(":")[1] not in text, name
        assert "STEAMKEY1" not in text, name
        assert "SKEY2" not in text, name


def test_per_butler_file_handlers_filter_by_context(tmp_path: Path):
    """Multi-butler processes must not fan one record out to every butler log."""
    configure_logging(fmt="json", log_root=tmp_path, butler_name="switchboard")
    configure_logging(fmt="json", log_root=tmp_path, butler_name="lifestyle")

    log = logging.getLogger("butlers.test")
    set_butler_context("switchboard")
    log.error("switchboard-only event")

    set_butler_context("lifestyle")
    log.error("lifestyle-only event")

    for handler in logging.getLogger().handlers:
        handler.flush()

    switchboard_log = (tmp_path / "butlers" / "switchboard.log").read_text()
    lifestyle_log = (tmp_path / "butlers" / "lifestyle.log").read_text()

    assert "switchboard-only event" in switchboard_log
    assert "switchboard-only event" not in lifestyle_log
    assert "lifestyle-only event" in lifestyle_log
    assert "lifestyle-only event" not in switchboard_log
