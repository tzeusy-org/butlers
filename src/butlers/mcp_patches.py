"""Local compatibility patches for third-party MCP transports.

Downgrade expected disconnect noise in the upstream streamable-HTTP client and
server implementations without re-vendoring their method bodies.

Two upstream log sites currently misclassify normal teardown as errors:

* ``mcp.server.streamable_http`` logs ``"Error in standalone SSE writer"``
  when a client disconnects during the background SSE writer task.
* ``mcp.client.streamable_http`` logs ``"Error parsing SSE message"`` when a
  stale client connection is being torn down and the in-memory stream has
  already been closed.

In both cases the raised exception is an expected
``anyio.ClosedResourceError`` / ``BrokenResourceError`` rather than a genuine
protocol or parsing bug. These tracebacks pollute QA findings and mask the real
operator signal, so we rewrite only those narrow cases to DEBUG with a clear
message and no traceback.

Scope: tracks MCP 1.26.0+. If MCP upstream changes the log messages or adds
new disconnect paths, these filters become no-ops for those paths — they are
failsafe by construction because nothing is silenced unless BOTH the exception
type and exact message match.
"""

from __future__ import annotations

import logging

import anyio

_WRITER_ERROR_MESSAGE = "Error in standalone SSE writer"
_WRITER_DISCONNECT_DEBUG_MESSAGE = "Standalone SSE stream closed during client disconnect"
_CLIENT_PARSE_ERROR_MESSAGE = "Error parsing SSE message"
_CLIENT_PARSE_DISCONNECT_DEBUG_MESSAGE = "Streamable HTTP SSE reader closed during client disconnect"
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


class _ClientSseParseDisconnectFilter(logging.Filter):
    """Downgrade expected client-side SSE teardown tracebacks.

    Matches log records emitted by ``mcp.client.streamable_http`` where:

    * ``record.msg`` equals the upstream ``"Error parsing SSE message"`` text.
    * ``record.exc_info`` is set and the raised exception is an
      ``anyio.ClosedResourceError`` or ``anyio.BrokenResourceError``.

    This specifically covers stale session teardown where the upstream client
    tries to forward a final SSE event into an already-closed in-memory stream.
    The log text is misleading in that case because there is no malformed SSE
    payload.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg != _CLIENT_PARSE_ERROR_MESSAGE:
            return True
        exc_info = record.exc_info
        if not exc_info:
            return True
        exc = exc_info[1]
        if not isinstance(exc, _DISCONNECT_EXC_TYPES):
            return True

        record.levelno = logging.DEBUG
        record.levelname = "DEBUG"
        record.msg = _CLIENT_PARSE_DISCONNECT_DEBUG_MESSAGE
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


def apply_streamable_http_client_disconnect_patch() -> None:
    """Install the client-side disconnect log filter on the MCP HTTP logger.

    Idempotent: repeated calls leave exactly one filter attached.
    """

    import mcp.client.streamable_http as streamable_http

    logger = streamable_http.logger
    for existing in logger.filters:
        if isinstance(existing, _ClientSseParseDisconnectFilter):
            return
    logger.addFilter(_ClientSseParseDisconnectFilter())
