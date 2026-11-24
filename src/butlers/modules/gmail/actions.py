"""Drafts and at-most-once plaintext replies in the owner's Gmail account."""

from __future__ import annotations

import base64
import itertools
import logging
from abc import ABC, abstractmethod
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Any

from butlers.core.goal_lineage import VerifiedLineage, approving_lineage, current_lineage
from butlers.google_account_registry import GoogleAccount
from butlers.modules.gmail.mime import (
    GmailError,
    address,
    build_message,
    content_digest,
    headers,
    reply_headers,
    valid_id,
    validate_content,
)
from butlers.modules.gmail.receipts import SendReceipts, receipt

logger = logging.getLogger(__name__)


def _error(code: str, message: str | None = None) -> dict[str, Any]:
    return {"status": "error", "error": code, "message": message or code}


def _verified(tool: str, args: dict[str, Any]) -> tuple[VerifiedLineage | None, bool]:
    """Recheck the handler arguments, allowing only omitted optional None defaults.

    FastMCP binds omitted defaults before calling the handler, whereas D7 signs the raw
    wire call. An explicit null and an omitted optional parameter have the same effect.
    Every supplied non-null value must still match the signed call exactly.
    """
    lineage = current_lineage()
    if lineage is None or lineage.capability != tool:
        return None, False
    nulls = [key for key, value in args.items() if value is None]
    present = {key: value for key, value in args.items() if value is not None}
    for include in itertools.product((False, True), repeat=len(nulls)):
        wire = {**present, **{key: None for key, keep in zip(nulls, include, strict=True) if keep}}
        if lineage.covers(tool, wire):
            return lineage, approving_lineage(tool, wire) is not None
    return None, False


def _raw_mime(draft: dict[str, Any]) -> EmailMessage:
    message = draft["message"]
    raw = message["raw"]
    return BytesParser(_class=EmailMessage, policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw)
    )


class GmailActions(ABC):
    """Mixin using GmailModule's account, transport and scrubber boundaries."""

    @abstractmethod
    async def _authorize(self, selector: str) -> tuple[GoogleAccount, str]: ...

    @abstractmethod
    async def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        retry: bool,
    ) -> Any: ...

    @abstractmethod
    def _pool(self) -> Any: ...

    @staticmethod
    @abstractmethod
    def _label(account: GoogleAccount) -> str: ...

    @staticmethod
    @abstractmethod
    def _typed(exc: GmailError) -> dict[str, Any]: ...

    @staticmethod
    @abstractmethod
    def _scrub(text: str) -> str: ...

    async def _list_drafts(
        self, account: str, max_results: int, page_token: str | None
    ) -> dict[str, Any]:
        if type(max_results) is not int or not 1 <= max_results <= 50:
            return _error("invalid_argument", "max_results must be between 1 and 50")
        try:
            acct, token = await self._authorize(account)
            params: dict[str, Any] = {"maxResults": max_results}
            if page_token:
                params["pageToken"] = page_token
            data = await self._request("GET", "drafts", token, params=params, retry=True)
            return {
                "status": "ok",
                "account": self._label(acct),
                "drafts": data.get("drafts") or [],
                "next_page_token": data.get("nextPageToken"),
            }
        except GmailError as exc:
            return self._typed(exc)

    async def _get_draft(self, account: str, draft_id: str) -> dict[str, Any]:
        if not valid_id(draft_id):
            return _error("invalid_argument")
        try:
            acct, token = await self._authorize(account)
            data = await self._request(
                "GET", f"drafts/{draft_id}", token, params={"format": "raw"}, retry=True
            )
            mime = _raw_mime(data)
            body = mime.get_body(preferencelist=("plain",)) if mime.is_multipart() else mime
            message: dict[str, Any] = {
                "thread_id": data["message"].get("threadId"),
                "attachments": [],
            }
            try:
                message.update(
                    {key: self._scrub(str(mime[key] or "")) for key in ("to", "cc", "subject")}
                )
                text = self._scrub(body.get_content() if body else "")
                message.update(body=text[:50_000], truncated=len(text) > 50_000)
            except Exception:  # noqa: BLE001 - withheld rather than unsanitized draft text
                message = {"content_withheld": True}
            return {
                "status": "ok",
                "account": self._label(acct),
                "draft_id": draft_id,
                "message": message,
            }
        except GmailError as exc:
            return self._typed(exc)
        except (KeyError, ValueError, TypeError):
            return _error("provider_error")

    async def _prepare_message(
        self, args: dict[str, Any]
    ) -> tuple[GoogleAccount, str, dict[str, str]]:
        acct, token = await self._authorize(args["account"])
        threading = {}
        if args.get("thread_id"):
            target = await self._request(
                "GET",
                f"messages/{args['in_reply_to_message_id']}",
                token,
                params={"format": "full"},
                retry=True,
            )
            threading = reply_headers(target, args)
        return acct, token, threading

    async def _save_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_content(args)
            lineage, approved = _verified("gmail_save_draft", args)
            if lineage is None or lineage.origin not in {
                "owner",
                "obligation",
                "staffer",
                "third_party",
            }:
                return _error("unverified_call")
            if (lineage.exposed or lineage.origin == "third_party") and not approved:
                return _error("approval_required")
            acct, token, threading = await self._prepare_message(args)
            # Validate the account From address and every MIME header before any write.
            message = build_message(address(acct.email), args, threading)
            draft_id = args.get("draft_id")
            data = await self._request(
                "PUT" if draft_id else "POST",
                f"drafts/{draft_id}" if draft_id else "drafts",
                token,
                json={"message": message},
                retry=False,
            )
            return {"status": "ok", "account": self._label(acct), "draft_id": data["id"]}
        except GmailError as exc:
            return self._typed(exc)
        except KeyError:
            return _error("provider_error")
        except (ValueError, TypeError) as exc:
            return _error("invalid_argument", str(exc))

    async def _send_reply(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_content(args)
            if not args.get("thread_id"):
                raise ValueError("a send must reply in an existing thread")
            lineage, _ = _verified("gmail_send_reply", args)
            if lineage is None or lineage.effect_id is None:
                return _error("unverified_call")
            store = SendReceipts(self._pool())
            effect_id, call_digest = lineage.effect_id, lineage.args_digest
            existing = await store.get(effect_id)
            if existing:
                if existing["call_digest"] != call_digest:
                    return _error("effect_conflict")
                if existing["state"] in {"dispatching", "unknown"}:
                    return await self._reconcile_send(store, existing, args)
                return receipt(existing)
            acct, token, threading = await self._prepare_message(args)
            sender = address(acct.email)
            message_id = f"<butlers.{effect_id.hex}@{sender.split('@', 1)[1]}>"
            message = build_message(sender, args, threading, message_id)
            row = await store.reserve(
                effect_id, self._label(acct), args["thread_id"], message_id, call_digest
            )
            if row is None:
                # The unique key has committed in another dispatcher; never send again.
                existing = await store.get(effect_id)
                return (
                    receipt(existing)
                    if existing["call_digest"] == call_digest
                    else _error("effect_conflict")
                )
        except GmailError as exc:
            return self._typed(exc)
        except (ValueError, TypeError) as exc:
            return _error("invalid_argument", str(exc))
        except Exception:  # noqa: BLE001 - no durable fence, no send
            logger.warning("gmail: send receipt reservation unavailable")
            return _error("receipt_unavailable")

        state, gmail_id = "unknown", None
        try:
            data = await self._request("POST", "messages/send", token, json=message, retry=False)
            if (
                isinstance(data, dict)
                and valid_id(data.get("id"))
                and data.get("threadId") == args["thread_id"]
            ):
                state, gmail_id = "sent", data["id"]
        except GmailError as exc:
            if exc.status is not None and 400 <= exc.status < 500:
                state = "failed"
        # Cancellation or process death leaves dispatching: it is uncertainty, never retry.
        try:
            row = await store.settle(effect_id, state, gmail_id)
        except Exception:  # noqa: BLE001 - provider may have accepted; durable reservation remains
            logger.warning("gmail: send receipt completion unavailable")
            return {**receipt(row), "status": "unknown"}
        if row["state"] == "sent" and args.get("replaces_draft_id"):
            outcome = await self._cleanup_draft(token, args)
            try:
                row = await store.cleanup(effect_id, outcome)
            except Exception:  # noqa: BLE001 - a cleanup audit failure cannot undo a confirmed send
                logger.warning("gmail: draft cleanup receipt unavailable; send remains confirmed")
        return receipt(row)

    async def _reconcile_send(
        self, store: SendReceipts, row: Any, args: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            _, token = await self._authorize(args["account"])
            thread = await self._request(
                "GET", f"threads/{row['thread_id']}", token, params={"format": "full"}, retry=True
            )
            matches = [
                m
                for m in thread.get("messages", [])
                if thread.get("id") == row["thread_id"]
                and m.get("threadId") == row["thread_id"]
                and "SENT" in (m.get("labelIds") or [])
                and headers(m).get("message-id") == row["message_id"]
                and valid_id(m.get("id"))
            ]
            if len(matches) == 1:
                row = await store.settle(row["effect_id"], "sent", matches[0]["id"])
        except Exception:  # noqa: BLE001 - missing or malformed evidence preserves uncertainty
            logger.info("gmail: send remains unconfirmed")
        return receipt(row)

    async def _cleanup_draft(self, token: str, args: dict[str, Any]) -> str:
        try:
            draft = await self._request(
                "GET",
                f"drafts/{args['replaces_draft_id']}",
                token,
                params={"format": "raw"},
                retry=True,
            )
            mime = _raw_mime(draft)
            if (
                draft["message"].get("threadId") != args["thread_id"]
                or mime.is_multipart()
                or mime.get_content_type() != "text/plain"
                or mime.defects
            ):
                return "draft_kept_changed"
            to = [a.addr_spec for h in mime.get_all("To", []) for a in h.addresses]
            cc = [a.addr_spec for h in mime.get_all("Cc", []) for a in h.addresses]
            actual = content_digest(to, cc, str(mime["Subject"] or ""), mime.get_content())
            expected = content_digest(
                args["to"], args.get("cc") or [], args["subject"], args["body"]
            )
            if actual != expected or mime.get_all("Bcc"):
                return "draft_kept_changed"
            await self._request("DELETE", f"drafts/{args['replaces_draft_id']}", token, retry=False)
            return "draft_removed"
        except Exception:  # noqa: BLE001 - post-send cleanup cannot change delivery truth
            return "draft_kept_unavailable"
