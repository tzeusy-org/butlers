"""Local compatibility patches for third-party MCP transports.

Downgrade standalone-SSE-writer client-disconnect errors in
``mcp.server.streamable_http`` from ERROR to DEBUG without re-vendoring the
upstream method body. The upstream handler logs every failure of its
``standalone_sse_writer`` task via ``logger.exception(...)`` with message
``"Error in standalone SSE writer"``. When the cause is an expected
``anyio.ClosedResourceError`` / ``BrokenResourceError`` (client closed the
SSE stream), the stack trace is noise that pollutes QA error dashboards.

This module installs a single ``logging.Filter`` on the
``mcp.server.streamable_http`` logger. The filter inspects ``record.exc_info``
and, if it matches the disconnect exception types AND the record targets the
known writer log message, rewrites the record to DEBUG level with an
unambiguous message and no traceback. All other records pass through
untouched, so we do not hide unrelated errors.

Scope: tracks MCP 1.26.0. If MCP upstream changes the log message or adds new
disconnect paths, this filter becomes a no-op for those paths — it is failsafe
by construction (nothing gets silenced that does not match BOTH the exception
type and the exact message).
"""

from __future__ import annotations

import logging

import anyio

_WRITER_ERROR_MESSAGE = "Error in standalone SSE writer"
_WRITER_DISCONNECT_DEBUG_MESSAGE = "Standalone SSE stream closed during client disconnect"
_DISCONNECT_EXC_TYPES: tuple[type[BaseException], ...] = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
)


class _StandaloneSseDisconnectFilter(logging.Filter):
    """Downgrade expected client-disconnect tracebacks from the MCP SSE writer.

    Matches log records emitted by ``mcp.server.streamable_http`` where:

    * ``record.msg`` equals the upstream "Error in standalone SSE writer" text.
    * ``record.exc_info`` is set and the raised exception is an
      ``anyio.ClosedResourceError`` or ``anyio.BrokenResourceError``.

    Matching records are rewritten in-place: level set to ``DEBUG``, message
    replaced with a terse client-disconnect note, and ``exc_info`` cleared so
    the traceback does not surface. All other records pass through unchanged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg != _WRITER_ERROR_MESSAGE:
            return True
        exc_info = record.exc_info
        if not exc_info:
            return True
        exc = exc_info[1]
        if not isinstance(exc, _DISCONNECT_EXC_TYPES):
            return True

        record.levelno = logging.DEBUG
        record.levelname = "DEBUG"
        record.msg = _WRITER_DISCONNECT_DEBUG_MESSAGE
        record.args = None
        record.exc_info = None
        record.exc_text = None
        return True


def apply_streamable_http_disconnect_patch() -> None:
    """Install the disconnect log filter on the MCP streamable-http logger.

    Idempotent: repeated calls leave exactly one filter attached.
    """

    import mcp.server.streamable_http as streamable_http

    logger = streamable_http.logger
    for existing in logger.filters:
        if isinstance(existing, _StandaloneSseDisconnectFilter):
            return
    logger.addFilter(_StandaloneSseDisconnectFilter())
