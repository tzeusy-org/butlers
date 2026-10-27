"""Media core tools: get_attachment, attachment_view."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from butlers.core.telemetry import tool_span
from butlers.core_tools._base import ToolContext
from butlers.storage import BlobNotFoundError
from butlers.tools.attachments import attachment_view as _attachment_view
from butlers.tools.attachments import get_attachment as _get_attachment

logger = logging.getLogger(__name__)


def register_media_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register media group tools: get_attachment, attachment_view."""
    daemon = ctx.daemon
    butler_name = ctx.butler_name

    @_core_tool("media")
    @tool_span("get_attachment", butler_name=butler_name)
    async def get_attachment(storage_ref: str) -> dict:
        """Retrieve a small non-image attachment (PDF, text, ...) as base64.

        For an image, call ``attachment_view`` instead — it returns a real
        image content block with no inline-size cap. This tool refuses (a
        typed ``status: "refused"`` result, not a base64 dump) any blob whose
        base64 encoding would exceed the inline cap.

        Parameters
        ----------
        storage_ref:
            Storage reference string (e.g., 's3://bucket/general/2026/02/16/abc123.jpg')

        Returns
        -------
        dict
            - storage_ref: The storage reference
            - media_type: Inferred MIME type
            - data_base64: Base64-encoded blob data
            - size_bytes: Size of the blob in bytes

            Or, when refused: ``{"status": "refused", "reason": ..., ...}``.
        """
        try:
            return await _get_attachment(daemon.blob_store, storage_ref)
        except BlobNotFoundError:
            # Return structured error instead of raising
            return {
                "error": f"Attachment not found: {storage_ref}",
                "status": "not_found",
            }
        except ValueError as exc:
            # Invalid storage_ref or size limit exceeded — give the LLM
            # actionable guidance so it retries with the correct value.
            hint = ""
            if "://" not in storage_ref:
                hint = (
                    " The storage_ref must be a full reference like "
                    "'s3://bucket/path/file.ext'. Check the ATTACHMENTS "
                    "section in your system prompt for the correct "
                    "storage_ref value — do not use the filename."
                )
            return {
                "error": f"{exc}{hint}",
                "status": "invalid",
            }
        except Exception as exc:
            logger.exception("get_attachment failed for %s", storage_ref)
            return {
                "error": f"Failed to retrieve attachment: {exc}",
                "status": "error",
            }

    @_core_tool("media")
    @tool_span("attachment_view", butler_name=butler_name)
    async def attachment_view(storage_ref: str):
        """View an image attachment as a real image content block.

        Returns the image itself (an MCP image content block), the correct
        way to hand a model vision input — never a JSON blob with a base64
        field. A blob over the vision size cap, a non-image media type, or a
        blob that cannot be found returns a typed refusal
        (``{"status": "refused", "reason": ...}``) instead.

        Parameters
        ----------
        storage_ref:
            Storage reference string (e.g., 's3://bucket/general/2026/02/16/abc123.jpg')
        """
        try:
            return await _attachment_view(daemon.blob_store, storage_ref)
        except Exception as exc:
            logger.exception("attachment_view failed for %s", storage_ref)
            return {
                "status": "refused",
                "reason": "internal_error",
                "storage_ref": storage_ref,
                "hint": str(exc),
            }
