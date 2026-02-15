"""Structured logging for Butlers — context-aware, configurable per-butler.

Uses structlog's ProcessorFormatter to transparently upgrade all existing
``logging.getLogger(__name__)`` call sites. Zero changes needed at call sites.

Two output formats:
- ``text``: Colored, human-readable console output (dev default)
- ``json``: Machine-parseable JSON lines (production / log aggregation)

Butler identity and OTel trace context are injected automatically via
processors that read from a ContextVar and the current OTel span.

Log directory layout (when ``log_root`` is set)::

    logs/
      butlers/          # Per-butler application logs (JSON)
        switchboard.log
        health.log
      uvicorn/          # Per-butler HTTP server & MCP transport logs (JSON)
        switchboard.log
        health.log
      connectors/       # Standalone connector logs (JSON)
        gmail.log
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from pathlib import Path

import structlog
from opentelemetry import trace

# ---------------------------------------------------------------------------
# Butler context (asyncio-safe via ContextVar)
# ---------------------------------------------------------------------------

_butler_context: ContextVar[str | None] = ContextVar("butler_name", default=None)


def set_butler_context(name: str) -> None:
    """Set the butler name for the current async context."""
    _butler_context.set(name)


def get_butler_context() -> str | None:
    """Get the butler name for the current async context."""
    return _butler_context.get()


# ---------------------------------------------------------------------------
# Structlog processors
# ---------------------------------------------------------------------------


def add_butler_context(
    logger: logging.Logger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: dict,
) -> dict:
    """Inject ``butler`` key from the ContextVar into the event dict."""
    event_dict["butler"] = _butler_context.get()
    return event_dict


def add_otel_context(
    logger: logging.Logger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: dict,
) -> dict:
    """Inject ``trace_id`` and ``span_id`` from the current OTel span."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.trace_id:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    else:
        event_dict["trace_id"] = "0" * 32
        event_dict["span_id"] = "0" * 16
    return event_dict


# ---------------------------------------------------------------------------
# Noise suppression
# ---------------------------------------------------------------------------

_NOISE_LOGGERS = (
    "uvicorn.access",
    "uvicorn.error",
    "mcp.server.lowlevel.server",
    "httpx",
    "httpcore",
)

# Subdirectory names under log_root
_DIR_BUTLERS = "butlers"
_DIR_UVICORN = "uvicorn"
_DIR_CONNECTORS = "connectors"


def _build_processors(
    time_fmt: str,
) -> list[structlog.types.Processor]:
    """Build the pre-chain processor list with the given timestamp format."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt=time_fmt),
        add_butler_context,
        add_otel_context,
        structlog.stdlib.ExtraAdder(),
    ]


def _make_file_handler(path: Path, processors: list) -> logging.FileHandler:
    """Create a JSON file handler at *path*."""
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=processors,
    )
    handler = logging.FileHandler(path)
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)
    return handler


# ---------------------------------------------------------------------------
# configure_logging()
# ---------------------------------------------------------------------------


def configure_logging(
    level: str = "INFO",
    fmt: str = "text",
    log_root: Path | None = None,
    butler_name: str | None = None,
) -> None:
    """Configure structured logging for the process.

    Parameters
    ----------
    level:
        Root log level (e.g. "DEBUG", "INFO", "WARNING").
    fmt:
        Output format — ``"text"`` for colored console, ``"json"`` for JSON lines.
    log_root:
        Root directory for structured log files.  When set, creates::

            {log_root}/butlers/{butler_name}.log   — application logs
            {log_root}/uvicorn/{butler_name}.log   — HTTP/MCP transport logs
            {log_root}/connectors/                 — (directory created for connectors)

    butler_name:
        Butler identity. Set in the ContextVar and used for file naming.
    """
    if butler_name:
        set_butler_context(butler_name)

    if fmt == "json":
        console_processors = _build_processors(time_fmt="iso")
        renderer = structlog.processors.JSONRenderer()
    else:
        # Console: compact HH:MM:SS, no microseconds
        console_processors = _build_processors(time_fmt="%H:%M:%S")
        renderer = structlog.dev.ConsoleRenderer()

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=console_processors,
    )

    # -- Console handler (stderr) --
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    # Remove existing handlers to avoid duplicate output on reconfiguration
    root.handlers.clear()
    root.addHandler(console_handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Suppress noisy third-party loggers on console
    for name in _NOISE_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # -- File handlers (structured directory layout) --
    if log_root is not None:
        log_root = Path(log_root)
        file_processors = _build_processors(time_fmt="iso")
        log_name = butler_name or "butlers"

        # Create all subdirectories upfront
        for subdir in (_DIR_BUTLERS, _DIR_UVICORN, _DIR_CONNECTORS):
            (log_root / subdir).mkdir(parents=True, exist_ok=True)

        # Butler application logs → logs/butlers/{name}.log
        butler_handler = _make_file_handler(
            log_root / _DIR_BUTLERS / f"{log_name}.log",
            file_processors,
        )
        root.addHandler(butler_handler)

        # Noise/transport logs → logs/uvicorn/{name}.log
        uvicorn_handler = _make_file_handler(
            log_root / _DIR_UVICORN / f"{log_name}.log",
            file_processors,
        )
        for name in _NOISE_LOGGERS:
            logging.getLogger(name).addHandler(uvicorn_handler)

    # Configure structlog itself (for direct structlog.get_logger() usage)
    structlog.configure(
        processors=[
            *console_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
