"""Owner-private native Google Docs and Sheets tools for the Drive module.

Mixed into ``GoogleDriveModule``. Every write first reads its target and proceeds only when
the target is owner-private (see ``native.evaluate_reach``); that guard is why these tools are
effect class ``record`` rather than ``communicate``. A later change to the refusal must
reclassify them (docs/modules/google-drive.md).
"""

from __future__ import annotations

import json as _json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from butlers.core.goal_lineage import current_lineage
from butlers.google_credentials import _pool_acquire
from butlers.modules.google_drive.native import (
    CONTENT_FORMATS,
    CREATED_BY_PROPERTY,
    MAX_CELLS,
    SHEETS_API_BASE,
    TARGET_FIELDS,
    a1_path,
    doc_too_large,
    evaluate_reach,
    prepare_doc_content,
    prepare_rows,
    sheet_title_range,
)

logger = logging.getLogger(__name__)

_FOLDER_MIME = "application/vnd.google-apps.folder"
_DOC_MIME = "application/vnd.google-apps.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_RESULT_FIELDS = "id,name,webViewLink,mimeType,version"

NATIVE_TOOL_NAMES = (
    "drive_create_doc",
    "drive_update_doc",
    "drive_create_sheet",
    "drive_read_sheet",
    "drive_append_sheet_rows",
    "drive_update_sheet_range",
)

PostWriteHook = Callable[[str, str, dict[str, Any]], Awaitable[None]]


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error": message, **extra}


def _sheets_disabled(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    text = response.text
    return "accessNotConfigured" in text or "SERVICE_DISABLED" in text


class NativeDocsMixin:
    """The six native Docs and Sheets tools and their shared guard."""

    # Provided by GoogleDriveModule.
    _client: Any
    _http_client: Any
    _db: Any
    _butler_name: str

    _owner_email_cache: str | None = None
    _import_formats: dict[str, list[str]] | None = None
    # The single post-write seam. A no-op until a hook is registered: the sheet-watch sibling
    # records a self-write snapshot here so its own writes do not wake it.
    _post_write_hook: PostWriteHook | None = None

    def set_post_write_hook(self, hook: PostWriteHook | None) -> None:
        self._post_write_hook = hook

    async def _after_write(self, tool: str, file_id: str, detail: dict[str, Any]) -> None:
        hook = self._post_write_hook
        if hook is None:
            return
        try:
            await hook(tool, file_id, detail)
        except Exception:
            logger.warning("google_drive: post-write hook failed for %s", tool, exc_info=True)

    # -- shared plumbing -------------------------------------------------------------

    def _sheets_client(self) -> Any:
        from butlers.modules.google_drive import _DriveHTTPClient

        client = self._client
        return _DriveHTTPClient(
            client._http_client,
            client._token_cache,
            client._credentials,
            api_base=SHEETS_API_BASE,
        )

    @staticmethod
    def _exposed() -> bool:
        """Missing lineage counts as exposed (RFC 0038 D6)."""
        lineage = current_lineage()
        return lineage is None or lineage.exposed

    @staticmethod
    def _effect_id() -> uuid.UUID | None:
        lineage = current_lineage()
        return lineage.effect_id if lineage is not None else None

    async def _owner_email(self) -> str | None:
        if self._owner_email_cache is None:
            response = await self._client.get("about", params={"fields": "user(emailAddress)"})
            if response.status_code != 200:
                return None
            email = (response.json().get("user") or {}).get("emailAddress")
            if isinstance(email, str) and email:
                self._owner_email_cache = email
        return self._owner_email_cache

    async def _private_target(
        self, target_id: str, *, mime: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Read ``target_id`` and return ``(metadata, refusal)``; exactly one is set."""
        response = await self._client.get(
            f"files/{target_id}",
            params={"fields": TARGET_FIELDS, "supportsAllDrives": "true"},
        )
        if response.status_code == 404:
            return None, {"status": "not_found", "file_id": target_id}
        if response.status_code != 200:
            return None, _error(f"Drive API error reading the target (HTTP {response.status_code})")
        meta = response.json()
        if meta.get("trashed"):
            return None, {"status": "not_found", "file_id": target_id}
        owner = await self._owner_email()
        if owner is None:
            return None, _error("Could not verify the connected Google account")
        private, reach = evaluate_reach(meta, owner)
        if not private:
            return None, {"status": "target_shared", "reach": reach}
        if meta.get("mimeType") != mime:
            return None, {"status": "wrong_type", "mime_type": meta.get("mimeType")}
        return meta, None

    async def _resolve_folder(self, folder_id: str | None) -> tuple[str | None, dict | None]:
        if folder_id is not None:
            return folder_id, None

        async def guard(parent: str) -> dict[str, Any] | None:
            # Nothing is created under a parent the owner has shared.
            return (await self._private_target(parent, mime=_FOLDER_MIME))[1]

        resolved = await self._ensure_butler_folder(  # type: ignore[attr-defined]
            self._butler_name, parent_guard=guard
        )
        if isinstance(resolved, dict):
            return None, resolved
        return resolved, None

    async def _doc_import_mime(self, content_format: str) -> tuple[str, str | None]:
        """The upload media type for ``content_format`` and a degradation note, if any."""
        if content_format == "text":
            return "text/plain", None
        formats = self._import_formats
        if formats is None:
            response = await self._client.get("about", params={"fields": "importFormats"})
            if response.status_code != 200:
                raise RuntimeError(f"Drive about.get failed (HTTP {response.status_code})")
            formats = self._import_formats = response.json().get("importFormats") or {}
        if _DOC_MIME in (formats.get("text/markdown") or []):
            return "text/markdown", None
        return "text/plain", "markdown_imported_as_text"

    # -- receipts (RFC 0038 D12) -----------------------------------------------------

    def _receipt_pool(self) -> Any:
        return getattr(self._db, "pool", None) if self._db is not None else None

    async def _receipt_lookup(self, effect_id: uuid.UUID) -> tuple[dict | None, bool]:
        """``(receipt, available)``; ``available`` is False when the store cannot answer."""
        pool = self._receipt_pool()
        if pool is None:
            return None, False
        try:
            async with _pool_acquire(pool) as conn:
                row = await conn.fetchrow(
                    "SELECT receipt FROM google_drive_effect_receipts WHERE effect_id = $1",
                    effect_id,
                )
        except Exception:
            logger.warning("google_drive: receipt lookup failed", exc_info=True)
            return None, False
        if row is None:
            return None, True
        receipt = row["receipt"]
        return (_json.loads(receipt) if isinstance(receipt, str) else dict(receipt)), True

    async def _receipt_store(
        self, effect_id: uuid.UUID, tool: str, file_id: str | None, receipt: dict[str, Any]
    ) -> None:
        pool = self._receipt_pool()
        if pool is None:
            return
        try:
            async with _pool_acquire(pool) as conn:
                await conn.execute(
                    "INSERT INTO google_drive_effect_receipts (effect_id, tool, file_id, receipt) "
                    "VALUES ($1, $2, $3, $4::jsonb) ON CONFLICT (effect_id) DO NOTHING",
                    effect_id,
                    tool,
                    file_id,
                    _json.dumps(receipt),
                )
        except Exception:
            logger.warning("google_drive: receipt store failed", exc_info=True)

    async def _replayed(self, tool: str) -> dict[str, Any] | None:
        """The stored receipt for this call's effect id, or a refusal when it cannot be read."""
        effect_id = self._effect_id()
        if effect_id is None:
            return None
        receipt, available = await self._receipt_lookup(effect_id)
        if not available:
            return _error("Effect receipts are unavailable; nothing was written")
        if receipt is None:
            return None
        return {**receipt, "replayed": True}

    async def _finish(
        self, tool: str, file_id: str, result: dict[str, Any], detail: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        effect_id = self._effect_id()
        if effect_id is not None:
            await self._receipt_store(effect_id, tool, file_id, result)
        await self._after_write(tool, file_id, detail or {})
        return result

    # -- Docs ------------------------------------------------------------------------

    async def _upload(
        self,
        method: str,
        metadata: dict[str, Any] | None,
        body: str,
        media_type: str,
        *,
        file_id: str | None = None,
    ) -> httpx.Response:
        params = {"fields": _RESULT_FIELDS, "supportsAllDrives": "true"}
        if file_id is not None:
            # In-place content replacement: the file id, link and revision history are kept.
            return await self._client.patch(
                f"{_UPLOAD_URL}/{file_id}",
                params={**params, "uploadType": "media"},
                content=body.encode("utf-8"),
                headers={"Content-Type": media_type},
            )
        boundary = f"butlers_{uuid.uuid4().hex}"
        payload = (
            (
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{_json.dumps(metadata)}\r\n--{boundary}\r\nContent-Type: {media_type}\r\n\r\n"
            ).encode()
            + body.encode("utf-8")
            + f"\r\n--{boundary}--".encode()
        )
        return await self._client.post(
            _UPLOAD_URL,
            content=payload,
            params={**params, "uploadType": "multipart"},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        )

    async def _drive_create_doc(
        self,
        title: str,
        content: str,
        content_format: str = "markdown",
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        if self._client is None:
            return self._not_configured_error()  # type: ignore[attr-defined]
        if content_format not in CONTENT_FORMATS or not title.strip():
            return {"status": "invalid", "error": "title and a known content_format are required"}
        if doc_too_large(content):
            return {"status": "too_large"}
        try:
            if (replay := await self._replayed("drive_create_doc")) is not None:
                return replay
            parent, refusal = await self._resolve_folder(folder_id)
            if refusal is not None:
                return refusal
            assert parent is not None
            meta, refusal = await self._private_target(parent, mime=_FOLDER_MIME)
            if refusal is not None:
                return refusal
            assert meta is not None
            body = prepare_doc_content(content, content_format, inert=self._exposed())
            if doc_too_large(body):
                return {"status": "too_large"}
            media_type, degraded = await self._doc_import_mime(content_format)
            response = await self._upload(
                "POST",
                {
                    "name": title,
                    "mimeType": _DOC_MIME,
                    "parents": [meta["id"]],
                    "appProperties": {CREATED_BY_PROPERTY: self._butler_name},
                },
                body,
                media_type,
            )
            if response.status_code not in (200, 201):
                return _error(f"Upload failed (HTTP {response.status_code})")
            data = response.json()
            result: dict[str, Any] = {
                "status": "ok",
                "file_id": data["id"],
                "name": data.get("name", title),
                "web_view_link": data.get("webViewLink"),
                "mime_type": _DOC_MIME,
                "folder": meta["id"],
            }
            if degraded:
                result["format_degraded"] = degraded
            return await self._finish("drive_create_doc", data["id"], result)
        except Exception as exc:
            logger.error("drive_create_doc failed: %s", exc, exc_info=True)
            return _error(str(exc))

    async def _drive_update_doc(
        self, file_id: str, content: str, content_format: str = "markdown"
    ) -> dict[str, Any]:
        if self._client is None:
            return self._not_configured_error()  # type: ignore[attr-defined]
        if content_format not in CONTENT_FORMATS:
            return {"status": "invalid", "error": "unknown content_format"}
        if doc_too_large(content):
            return {"status": "too_large"}
        try:
            meta, refusal = await self._private_target(file_id, mime=_DOC_MIME)
            if refusal is not None:
                return refusal
            assert meta is not None
            marks = meta.get("appProperties") or {}
            if marks.get(CREATED_BY_PROPERTY) != self._butler_name:
                return {"status": "not_created_by_butlers"}
            body = prepare_doc_content(content, content_format, inert=self._exposed())
            if doc_too_large(body):
                return {"status": "too_large"}
            media_type, degraded = await self._doc_import_mime(content_format)
            response = await self._upload("PATCH", None, body, media_type, file_id=file_id)
            if response.status_code != 200:
                return _error(f"Update failed (HTTP {response.status_code})")
            data = response.json()
            result: dict[str, Any] = {
                "status": "ok",
                "file_id": data.get("id", file_id),
                "web_view_link": data.get("webViewLink"),
                "mime_type": _DOC_MIME,
                "pre_write_version": meta.get("version"),
            }
            if degraded:
                result["format_degraded"] = degraded
            return await self._finish("drive_update_doc", file_id, result)
        except Exception as exc:
            logger.error("drive_update_doc failed: %s", exc, exc_info=True)
            return _error(str(exc))

    # -- Sheets ----------------------------------------------------------------------

    async def _sheets_call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self._sheets_client()
        call = client.get if method == "GET" else client.post
        if method == "PUT":
            token = await client._token()
            from butlers.modules.google_drive import _drive_request

            return await _drive_request(
                client._http_client, "PUT", client._url(path), token=token, **kwargs
            )
        return await call(path, **kwargs)

    @staticmethod
    def _sheets_error(response: httpx.Response) -> dict[str, Any]:
        if _sheets_disabled(response):
            return _error(
                "The Google Sheets API is not enabled on the OAuth client's Cloud project; "
                "enable it and retry."
            )
        return _error(f"Sheets API error (HTTP {response.status_code})")

    async def _first_tab(self, file_id: str) -> str | None:
        response = await self._sheets_call(
            "GET", f"spreadsheets/{file_id}", params={"fields": "sheets.properties.title"}
        )
        if response.status_code != 200:
            return None
        sheets = response.json().get("sheets") or []
        return sheets[0]["properties"]["title"] if sheets else None

    async def _trash(self, file_id: str) -> bool:
        try:
            response = await self._client.patch(
                f"files/{file_id}", json={"trashed": True}, params={"supportsAllDrives": "true"}
            )
            return response.status_code == 200
        except Exception:
            return False

    async def _drive_create_sheet(
        self, title: str, rows: Any, folder_id: str | None = None
    ) -> dict[str, Any]:
        if self._client is None:
            return self._not_configured_error()  # type: ignore[attr-defined]
        if not title.strip():
            return {"status": "invalid", "error": "title is required"}
        prepared = prepare_rows(rows, inert=self._exposed())
        if isinstance(prepared, str):
            return (
                {"status": "too_large", "max_cells": MAX_CELLS}
                if prepared == "too_large"
                else {"status": "invalid", "error": prepared}
            )
        try:
            if (replay := await self._replayed("drive_create_sheet")) is not None:
                return replay
            parent, refusal = await self._resolve_folder(folder_id)
            if refusal is not None:
                return refusal
            assert parent is not None
            meta, refusal = await self._private_target(parent, mime=_FOLDER_MIME)
            if refusal is not None:
                return refusal
            assert meta is not None
            created = await self._client.post(
                "files",
                json={
                    "name": title,
                    "mimeType": _SHEET_MIME,
                    "parents": [meta["id"]],
                    "appProperties": {CREATED_BY_PROPERTY: self._butler_name},
                },
                params={"fields": _RESULT_FIELDS, "supportsAllDrives": "true"},
            )
            if created.status_code not in (200, 201):
                return _error(f"Create failed (HTTP {created.status_code})")
            data = created.json()
            file_id = data["id"]
            written = await self._sheets_call(
                "POST",
                f"spreadsheets/{file_id}/values/A1:append",
                params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                json={"majorDimension": "ROWS", "values": prepared},
            )
            if written.status_code != 200:
                failure = self._sheets_error(written)
                failure["trashed"] = await self._trash(file_id)
                return failure
            result = {
                "status": "ok",
                "file_id": file_id,
                "name": data.get("name", title),
                "web_view_link": data.get("webViewLink"),
                "mime_type": _SHEET_MIME,
                "folder": meta["id"],
            }
            return await self._finish("drive_create_sheet", file_id, result, {"rows": prepared})
        except Exception as exc:
            logger.error("drive_create_sheet failed: %s", exc, exc_info=True)
            return _error(str(exc))

    async def _drive_read_sheet(self, file_id: str, a1_range: str | None = None) -> dict[str, Any]:
        if self._client is None:
            return self._not_configured_error()  # type: ignore[attr-defined]
        try:
            head = await self._client.get(
                f"files/{file_id}",
                params={"fields": "id,mimeType,trashed", "supportsAllDrives": "true"},
            )
            if head.status_code == 404 or (head.status_code == 200 and head.json().get("trashed")):
                return {"status": "not_found", "file_id": file_id}
            if head.status_code != 200:
                return _error(f"Drive API error (HTTP {head.status_code})")
            if head.json().get("mimeType") != _SHEET_MIME:
                return {"status": "wrong_type", "mime_type": head.json().get("mimeType")}
            if a1_range is not None:
                ranges = [a1_range]
            else:
                tabs = await self._sheets_call(
                    "GET", f"spreadsheets/{file_id}", params={"fields": "sheets.properties.title"}
                )
                if tabs.status_code != 200:
                    return self._sheets_error(tabs)
                ranges = [
                    sheet_title_range(s["properties"]["title"])
                    for s in tabs.json().get("sheets") or []
                ]
            values = await self._sheets_call(
                "GET",
                f"spreadsheets/{file_id}/values:batchGet",
                params={"ranges": ranges, "valueRenderOption": "FORMATTED_VALUE"},
            )
            if values.status_code != 200:
                return self._sheets_error(values)
            out, cells, truncated = [], 0, False
            for block in values.json().get("valueRanges") or []:
                rows = block.get("values") or []
                kept = []
                for row in rows:
                    if cells + len(row) > MAX_CELLS:
                        truncated = True
                        break
                    cells += len(row)
                    kept.append(row)
                out.append({"range": block.get("range"), "values": kept})
            return {"status": "ok", "file_id": file_id, "tabs": out, "truncated": truncated}
        except Exception as exc:
            logger.error("drive_read_sheet failed: %s", exc, exc_info=True)
            return _error(str(exc))

    async def _sheet_write(
        self, tool: str, file_id: str, a1_range: str | None, rows: Any, *, append: bool
    ) -> dict[str, Any]:
        if self._client is None:
            return self._not_configured_error()  # type: ignore[attr-defined]
        prepared = prepare_rows(rows, inert=self._exposed())
        if isinstance(prepared, str):
            return (
                {"status": "too_large", "max_cells": MAX_CELLS}
                if prepared == "too_large"
                else {"status": "invalid", "error": prepared}
            )
        if not append and not a1_range:
            return {"status": "invalid", "error": "a1_range is required"}
        try:
            if append and (replay := await self._replayed(tool)) is not None:
                return replay
            meta, refusal = await self._private_target(file_id, mime=_SHEET_MIME)
            if refusal is not None:
                return refusal
            assert meta is not None
            if append:
                target = a1_range or await self._first_tab(file_id)
                if target is None:
                    return _error("Could not read the spreadsheet's tabs")
                if a1_range is None:
                    target = sheet_title_range(target)
                response = await self._sheets_call(
                    "POST",
                    f"spreadsheets/{file_id}/values/{a1_path(target)}:append",
                    params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                    json={"majorDimension": "ROWS", "values": prepared},
                )
            else:
                assert a1_range is not None
                response = await self._sheets_call(
                    "PUT",
                    f"spreadsheets/{file_id}/values/{a1_path(a1_range)}",
                    params={"valueInputOption": "RAW"},
                    json={"majorDimension": "ROWS", "values": prepared},
                )
            if response.status_code != 200:
                return self._sheets_error(response)
            body = response.json()
            updates = body.get("updates") or body
            result = {
                "status": "ok",
                "file_id": file_id,
                "updated_range": updates.get("updatedRange"),
                "updated_rows": updates.get("updatedRows"),
                "pre_write_version": meta.get("version"),
            }
            if append:
                return await self._finish(tool, file_id, result, {"rows": prepared})
            await self._after_write(tool, file_id, {"rows": prepared})
            return result
        except Exception as exc:
            logger.error("%s failed: %s", tool, exc, exc_info=True)
            return _error(str(exc))

    async def _drive_append_sheet_rows(
        self, file_id: str, rows: Any, a1_range: str | None = None
    ) -> dict[str, Any]:
        return await self._sheet_write(
            "drive_append_sheet_rows", file_id, a1_range, rows, append=True
        )

    async def _drive_update_sheet_range(
        self, file_id: str, a1_range: str, rows: Any
    ) -> dict[str, Any]:
        return await self._sheet_write(
            "drive_update_sheet_range", file_id, a1_range, rows, append=False
        )
