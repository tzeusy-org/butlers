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

import gzip
import logging
import logging.handlers
import os
import re
import shutil
import sys
import warnings
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

import structlog
from opentelemetry import trace

logger = logging.getLogger(__name__)

# Suppress websockets deprecation warnings emitted by uvicorn's legacy WS handler.
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"websockets\.legacy")
warnings.filterwarnings("ignore", message=r"websockets\.server\.WebSocketServerProtocol")

# ---------------------------------------------------------------------------
# Butler context (asyncio-safe via ContextVar)
# ---------------------------------------------------------------------------

_butler_context: ContextVar[str | None] = ContextVar("butler_name", default=None)


def set_butler_context(name: str) -> Token[str | None]:
    """Set the butler name for the current async context.

    Returns the token a scoped caller passes to :func:`reset_butler_context`.
    """
    return _butler_context.set(name)


def reset_butler_context(token: Token[str | None]) -> None:
    """Restore the butler name that was current before ``set_butler_context``."""
    _butler_context.reset(token)


def get_butler_context() -> str | None:
    """Get the butler name for the current async context."""
    return _butler_context.get()


def resolve_log_root(configured_log_root: str | None) -> Path | None:
    """Resolve file-log root from config + environment overrides.

    Precedence:
    1. ``BUTLERS_LOG_ROOT`` (path override, or disable sentinel)
    2. ``BUTLERS_DISABLE_FILE_LOGGING`` (truthy disables file logs)
    3. ``configured_log_root`` from config
    4. Default ``logs/`` fallback
    """
    explicit_log_root = os.environ.get("BUTLERS_LOG_ROOT")
    if explicit_log_root is not None:
        raw = explicit_log_root.strip()
        if raw == "" or raw.lower() in {"none", "null", "stdout", "stderr", "disable", "-"}:
            return None
        return Path(raw)

    disable_file_logging = os.environ.get("BUTLERS_DISABLE_FILE_LOGGING", "").strip().lower()
    if disable_file_logging in {"1", "true", "yes", "on"}:
        return None

    if configured_log_root:
        return Path(configured_log_root)
    return Path("logs")


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
# Credential redaction (defense-in-depth)
# ---------------------------------------------------------------------------

# Pattern matching Telegram bot tokens embedded in URL paths:
#   /bot<digits>:<base64url-chars>/
_RE_TELEGRAM_BOT_TOKEN = re.compile(r"/bot\d+:[A-Za-z0-9_-]+/")

# Pattern matching HTTP Authorization / Bearer header values in log text.
# Matches "Bearer <token>" where the token is a non-whitespace run of 16+ chars, so
# prose such as "bearer scrub failed" survives now that the filter runs on every
# handler (the record is shared, so caplog and the DB sink see the redaction too).
_RE_BEARER_TOKEN = re.compile(r"Bearer\s+\S{16,}", re.IGNORECASE)

# Pattern matching credential-bearing URL query parameters, e.g. the Steam Web
# API ``?key=<api key>``.  Anchored on ``?``/``&`` so pagination cursors such as
# ``pageToken=`` or ``syncToken=`` are left intact.
_RE_URL_CREDENTIAL_PARAM = re.compile(
    r"([?&](?:key|api_key|apikey|access_token|token)=)[^&#\s\"']+", re.IGNORECASE
)

# Additional known credential shapes can be appended here as the project grows.
_REDACTION_RULES: list[tuple[re.Pattern[str], str]] = [
    (_RE_TELEGRAM_BOT_TOKEN, "/bot[REDACTED]/"),
    (_RE_BEARER_TOKEN, "Bearer [REDACTED]"),
    (_RE_URL_CREDENTIAL_PARAM, r"\1[REDACTED]"),
]


class CredentialRedactionFilter(logging.Filter):
    """Defense-in-depth log filter that scrubs known credential patterns.

    ``configure_logging()`` attaches it to every handler it installs, so it
    fires regardless of which logger emits the record — including third-party
    libraries like httpx that log full request URLs.  It must sit on handlers,
    not on the root logger: a logger's own filters only see records logged
    directly to it, so a root-logger filter never sees records propagated from
    ``httpx`` or ``butlers.*`` loggers.

    Currently redacts:
    - Telegram bot tokens in URL paths (``/bot<id>:<token>/``)
    - HTTP Bearer tokens (``Authorization: Bearer <token>``)
    - Credential query parameters (``?key=``, ``&access_token=``, ...)
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        """Scrub credential patterns from the log record message in-place.

        Always returns ``True`` so the record is never dropped, and never
        raises: a filter exception would drop the record on that handler.
        """
        try:
            if isinstance(record.msg, dict):
                # structlog's ``wrap_for_formatter`` hands handlers the event
                # dict itself; ``ProcessorFormatter`` later calls ``.copy()`` on
                # it, so the message must stay a dict.
                record.msg = _redact_value(record.msg)
                return True

            msg = record.getMessage()
            redacted = _redact_text(msg)
            if redacted != msg:
                # Overwrite the message and clear args so getMessage() won't
                # re-interpolate the original format string with unredacted args.
                record.msg = redacted
                record.args = ()
        except Exception:  # noqa: BLE001
            pass
        return True


def _redact_text(text: str) -> str:
    for pattern, replacement in _REDACTION_RULES:
        text = pattern.sub(replacement, text)
    return text


def _redact_value(value: Any) -> Any:
    """Redact string leaves of a structlog event value, preserving container types."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return type(value)(_redact_value(item) for item in value)
    return value


CREDENTIAL_REDACTION = CredentialRedactionFilter()


def redact_exception(
    logger: logging.Logger,
    method_name: str,
    event_dict: dict,
) -> dict:
    """Scrub credential patterns from a record's traceback before rendering.

    ``CredentialRedactionFilter`` only sees the message; the traceback is
    rendered later from ``exc_info``, and exception text such as
    ``httpx.HTTPStatusError`` carries the full request URL. When redaction
    changes the rendered traceback, the redacted text replaces ``exc_info`` so
    no renderer can re-format the original; otherwise ``exc_info`` is left for
    the renderer (rich console tracebacks stay intact).
    """
    exc_info = event_dict.get("exc_info")
    if exc_info:
        rendered = structlog.processors.format_exc_info(
            logger, method_name, {"exc_info": exc_info}
        ).get("exception")
        if isinstance(rendered, str):
            redacted = _redact_text(rendered)
            if redacted != rendered:
                del event_dict["exc_info"]
                event_dict["exception"] = redacted
    for key in ("exception", "stack_info"):
        if isinstance(event_dict.get(key), str):
            event_dict[key] = _redact_text(event_dict[key])
    return event_dict


class ButlerContextFileFilter(logging.Filter):
    """Route shared-process file logs to the active butler's file only."""

    def __init__(self, butler_name: str) -> None:
        super().__init__()
        self._butler_name = butler_name

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003, ARG002
        return get_butler_context() == self._butler_name


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


# Weekly rotation on Monday 00:00 local time, keeping 4 weeks of history.
# Override via BUTLERS_LOG_BACKUP_COUNT.
_LOG_ROTATE_WHEN = "W0"
_LOG_ROTATE_BACKUP_COUNT_DEFAULT = 4


def _resolve_backup_count() -> int:
    raw = os.environ.get("BUTLERS_LOG_BACKUP_COUNT", "").strip()
    if not raw:
        return _LOG_ROTATE_BACKUP_COUNT_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return _LOG_ROTATE_BACKUP_COUNT_DEFAULT
    return max(0, value)


def _gzip_rotator(source: str, dest: str) -> None:
    """Compress *source* to *dest* with gzip, then delete the source."""
    try:
        with open(source, "rb") as sf, gzip.open(dest, "wb") as df:
            shutil.copyfileobj(sf, df)
        os.remove(source)
    except OSError as exc:
        # Don't crash the logging pipeline if compression fails — keep the
        # uncompressed rollover file so data isn't lost.
        logger.warning("Log rotation gzip failed for %s -> %s: %s", source, dest, exc)


def _gzip_namer(name: str) -> str:
    return name + ".gz"


def _make_file_handler(path: Path, processors: list) -> logging.handlers.TimedRotatingFileHandler:
    """Create a weekly-rotating JSON file handler at *path*.

    Rotates on Monday 00:00 local time; rotated files are suffixed with the
    rollover date and gzip-compressed (e.g. ``switchboard.log.2026-05-04.gz``).
    """
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            redact_exception,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=processors,
    )
    handler = logging.handlers.TimedRotatingFileHandler(
        path,
        when=_LOG_ROTATE_WHEN,
        backupCount=_resolve_backup_count(),
        encoding="utf-8",
        utc=False,
    )
    handler.rotator = _gzip_rotator
    handler.namer = _gzip_namer
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)
    handler.addFilter(CREDENTIAL_REDACTION)
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
        renderers: list[structlog.types.Processor] = [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Console: compact HH:MM:SS, no microseconds
        console_processors = _build_processors(time_fmt="%H:%M:%S")
        # Never render traceback locals: frames such as Database.connect hold
        # connection kwargs, including the login password.
        renderers = [
            structlog.dev.ConsoleRenderer(
                exception_formatter=structlog.dev.RichTracebackFormatter(show_locals=False)
            )
        ]

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            redact_exception,
            *renderers,
        ],
        foreign_pre_chain=console_processors,
    )

    # -- Console handler (stderr) --
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(CREDENTIAL_REDACTION)

    root = logging.getLogger()

    # Replace console handlers and filters without disturbing file handlers
    # from other butlers.  In multi-butler processes (``butlers up``), each
    # butler calls configure_logging() during startup; the old code closed
    # *every* handler, destroying file handlers created by earlier butlers.
    _existing_file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    for _handler in list(root.handlers):
        if not isinstance(_handler, logging.FileHandler):
            _handler.close()
    root.handlers = _existing_file_handlers
    root.filters.clear()
    root.addHandler(console_handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Credential redaction lives on each handler (see CredentialRedactionFilter).
    # Keeping httpx/httpcore at WARNING is the first line of defence: their INFO
    # request lines carry full URLs, which embed credentials for some APIs.
    # Suppress noisy third-party loggers on console
    for name in _NOISE_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # -- File handlers (structured directory layout) --
    if log_root is not None:
        log_root = Path(log_root)
        file_processors = _build_processors(time_fmt="iso")
        log_name = butler_name or "butlers"
        try:
            # Create all subdirectories upfront
            for subdir in (_DIR_BUTLERS, _DIR_UVICORN, _DIR_CONNECTORS):
                (log_root / subdir).mkdir(parents=True, exist_ok=True)

            target_butler_path = str(log_root / _DIR_BUTLERS / f"{log_name}.log")
            target_uvicorn_path = str(log_root / _DIR_UVICORN / f"{log_name}.log")

            # Close and remove any existing file handler for *this* butler only
            # (handles reconfiguration without clobbering other butlers' handlers).
            for h in list(root.handlers):
                if isinstance(h, logging.FileHandler) and h.baseFilename == target_butler_path:
                    h.close()
                    root.removeHandler(h)

            # Butler application logs → logs/butlers/{name}.log
            butler_handler = _make_file_handler(
                log_root / _DIR_BUTLERS / f"{log_name}.log",
                file_processors,
            )
            if butler_name:
                butler_handler.addFilter(ButlerContextFileFilter(butler_name))
            root.addHandler(butler_handler)

            # Noise/transport logs → logs/uvicorn/{name}.log
            # Same per-butler dedup for noise loggers.
            for noise_name in _NOISE_LOGGERS:
                noise_logger = logging.getLogger(noise_name)
                for h in list(noise_logger.handlers):
                    if isinstance(h, logging.FileHandler) and h.baseFilename == target_uvicorn_path:
                        h.close()
                        noise_logger.removeHandler(h)

            uvicorn_handler = _make_file_handler(
                log_root / _DIR_UVICORN / f"{log_name}.log",
                file_processors,
            )
            if butler_name:
                uvicorn_handler.addFilter(ButlerContextFileFilter(butler_name))
            for name in _NOISE_LOGGERS:
                logging.getLogger(name).addHandler(uvicorn_handler)
        except OSError as exc:
            logger.warning(
                "Failed to initialize file logging under %s; continuing with console-only logs: %s",
                log_root,
                exc,
            )

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
