"""The dashboard's one reader of butler MCP tool results (RFC 0038 D7, bu-2c1g7v).

Dashboard routers that call a butler tool read the result here instead of
each keeping its own parser. The result is the text the tool returned: the
text blocks of a ``CallToolResult`` (or of a legacy bare content list), joined,
then parsed as JSON when it is JSON. ``structured_content`` and the SDK object
itself are never stringified into a payload, and the error flag comes only
from the SDK result. Each router keeps its own mapping from payload to HTTP
status.

This is also the seam the dashboard control-plane client takes over when the
dashboard's tool calls move to the control plane's service listener.
"""

from __future__ import annotations

import json
from typing import Any


def tool_result_is_error(result: Any) -> bool:
    """Whether the SDK marked the call an error (a payload's own status is not)."""
    return getattr(result, "is_error", False) is True


def tool_result_text(result: Any) -> str | None:
    """The tool's non-empty text blocks joined by newlines, or ``None``."""
    content = getattr(result, "content", None)
    blocks = content if isinstance(content, list) else result if isinstance(result, list) else None
    if blocks is None:
        return None
    parts = [
        text for block in blocks if isinstance(text := getattr(block, "text", None), str) and text
    ]
    return "\n".join(parts) if parts else None


def parse_tool_text(text: str | None) -> Any:
    """JSON when the text is JSON, else the text itself; ``None`` stays ``None``."""
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def tool_result_payload(result: Any) -> Any:
    """The parsed payload of a tool result: JSON, plain text, or ``None``."""
    return parse_tool_text(tool_result_text(result))
