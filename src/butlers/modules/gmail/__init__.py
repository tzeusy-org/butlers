"""Gmail module: the owner's own mailbox, searched, read, archived and labelled.

Owner-mailbox tools register only after the RFC 0038 switch (``enforcement_enabled()``):

- ``gmail_list_mailboxes``: the connected Google accounts and each one's usable labels
- ``gmail_search``: Gmail query syntax over one account, at most 50 threads per call
- ``gmail_get_thread``: one thread's messages, decoded plain text, attachment metadata only
- ``gmail_modify_threads``: archive, mark read, star, mark important, user labels

The same switch also admits draft list/get/save and approval-gated plaintext in-thread replies.
It never trashes mail, writes ``ingest.v1`` envelopes or admits a goal.
Tokens come from the shared Google credential path and never appear
in a result. Every subject, snippet and body is third-party text: it is bearer-scrubbed, and a
scrubber failure withholds the content rather than returning it raw. See docs/modules/gmail.md.

Configured via ``[modules.gmail]`` in butler.toml.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from butlers.core.goal_lineage import current_lineage, enforcement_enabled
from butlers.egress.http_transport import external_async_client
from butlers.gmail_payload import (
    attachment_metadata,
    extract_body,
    header_name_is,
    header_str_value,
)
from butlers.google_account_registry import GoogleAccount, list_google_accounts
from butlers.google_credentials import (
    GoogleTokenCache,
    MissingGoogleCredentialsError,
    _mark_account_revoked,
    google_oauth_error_code,
    resolve_google_credentials,
)
from butlers.ingestion_bearer_scrub import scrub_text
from butlers.modules.base import Module, ToolMeta
from butlers.modules.gmail.actions import GmailActions
from butlers.modules.gmail.mime import GmailError as _GmailError
from butlers.modules.gmail.mime import valid_id as _valid_id

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
RECONSENT_ROUTE = "/api/oauth/google/start"

DEFAULT_SEARCH_RESULTS = 20
MAX_SEARCH_RESULTS = 50
MAX_THREADS_PER_MODIFY = 50
#: Per-message decoded-body cap; the message carries ``truncated`` when it applies.
BODY_CHAR_LIMIT = 20_000
#: Reads retry once on 429 and 5xx after this many seconds.
RETRY_BACKOFF_S = 1.0

#: RFC 0038 D5 origins; a lineage naming anything else is refused.
_KNOWN_ORIGINS = frozenset({"owner", "obligation", "third_party", "staffer"})
_MODIFY_TOOL = "gmail_modify_threads"
#: Removals that park under the Gmail exposure rule (the broker's ``GMAIL_PARKED_REMOVALS``).
_PARKED_REMOVALS = frozenset({"INBOX", "UNREAD"})
_SCRUB_DOMAIN = "gmail"
_ALLOWED_SYSTEM_LABELS = ("INBOX", "UNREAD", "STARRED", "IMPORTANT")
_REFUSED_SYSTEM_LABELS = frozenset({"TRASH", "SPAM", "SENT", "DRAFT", "CHAT"})


class GmailConfig(BaseModel):
    """``[modules.gmail]``: no settings today; the account is a per-call argument."""

    model_config = ConfigDict(extra="forbid")


class _TokenState:
    """Plain holder the shared token cache reads and writes."""

    _access_token: str | None = None
    _token_expires_at: Any = None


def _error(code: str, message: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error": code, "message": message or code, **extra}


def _status_code(status: int) -> str:
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    return "provider_error"


class GmailModule(GmailActions, Module):
    """Owner-mailbox tools over Gmail REST; see the module docstring."""

    name = "gmail"  # pyright: ignore[reportAssignmentType]

    def __init__(self) -> None:
        self._config = GmailConfig()
        self._db: Any = None
        self._credential_store: Any = None
        self._http_client: httpx.AsyncClient | None = None
        self._tokens: dict[str, tuple[_TokenState, GoogleTokenCache]] = {}
        self._tools_registered = False

    @property
    def config_schema(self) -> type[BaseModel]:
        return GmailConfig

    def migration_revisions(self) -> str | None:
        return None

    def tool_metadata(self) -> dict[str, ToolMeta]:
        if not self._tools_registered:
            return {}
        return {
            "gmail_list_mailboxes": ToolMeta(),
            "gmail_search": ToolMeta(arg_sensitivities={"account": True, "query": True}),
            "gmail_get_thread": ToolMeta(arg_sensitivities={"account": True, "thread_id": True}),
            "gmail_modify_threads": ToolMeta(
                arg_sensitivities={
                    "account": True,
                    "thread_ids": True,
                    "add_labels": True,
                    "remove_labels": True,
                }
            ),
            "gmail_list_drafts": ToolMeta(arg_sensitivities={"account": True}),
            "gmail_get_draft": ToolMeta(arg_sensitivities={"account": True, "draft_id": True}),
            "gmail_save_draft": ToolMeta(
                arg_sensitivities={
                    name: True
                    for name in (
                        "account",
                        "draft_id",
                        "thread_id",
                        "in_reply_to_message_id",
                        "to",
                        "cc",
                        "subject",
                        "body",
                    )
                }
            ),
            "gmail_send_reply": ToolMeta(
                arg_sensitivities={
                    name: True
                    for name in (
                        "account",
                        "thread_id",
                        "in_reply_to_message_id",
                        "in_reply_to_sender",
                        "in_reply_to_date",
                        "to",
                        "cc",
                        "subject",
                        "body",
                        "replaces_draft_id",
                    )
                }
            ),
        }

    # -- lifecycle -------------------------------------------------------------------

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        self._config = config if isinstance(config, GmailConfig) else GmailConfig(**(config or {}))
        self._db = db
        self._credential_store = credential_store
        if self._http_client is not None:
            await self._http_client.aclose()
        self._http_client = external_async_client(timeout=30.0)

    async def on_shutdown(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # -- accounts and tokens ---------------------------------------------------------

    def _pool(self) -> Any:
        return getattr(self._db, "pool", None) if self._db is not None else None

    async def _find_account(self, selector: str) -> GoogleAccount | None:
        wanted = (selector or "").strip().lower()
        if not wanted:
            return None
        for account in await list_google_accounts(self._pool()):
            if account.status != "active":
                continue
            if wanted in {(account.email or "").lower(), str(account.id).lower()}:
                return account
        return None

    @staticmethod
    def _has_scope(account: GoogleAccount) -> bool:
        return MODIFY_SCOPE in (account.granted_scopes or [])

    @staticmethod
    def _label(account: GoogleAccount) -> str:
        return account.email or str(account.id)

    async def _token(self, account: GoogleAccount) -> str:
        """A fresh access token for ``account``; raises ``_GmailError`` with a typed code."""
        client = self._http_client
        if client is None:
            raise _GmailError("provider_error")
        try:
            creds = await resolve_google_credentials(
                self._credential_store, pool=self._pool(), caller="gmail", account=account.id
            )
        except MissingGoogleCredentialsError as exc:
            raise _GmailError("credentials_unavailable") from exc
        key = str(account.id)
        if key not in self._tokens:
            state = _TokenState()
            self._tokens[key] = (state, GoogleTokenCache(state, account=key))
        _, cache = self._tokens[key]
        try:
            return await cache.get_token(
                client,
                client_id=creds.client_id,
                client_secret=creds.client_secret,
                refresh_token=creds.refresh_token,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPStatusError as exc:
            if google_oauth_error_code(exc.response) == "invalid_grant":
                try:
                    await _mark_account_revoked(
                        self._pool(),
                        account_id=account.id,
                        expected_refresh_token=creds.refresh_token,
                    )
                except Exception:  # noqa: BLE001 - the typed error still reaches the caller
                    logger.warning("gmail: could not persist revoked state", exc_info=True)
                raise _GmailError("account_revoked") from exc
            raise _GmailError("provider_error") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise _GmailError("provider_error") from exc

    async def _authorize(self, selector: str) -> tuple[GoogleAccount, str]:
        """Resolve the account, check scope, mint a token. Raises ``_GmailError``."""
        account = await self._find_account(selector)
        if account is None:
            raise _GmailError("unknown_account")
        if not self._has_scope(account):
            raise _GmailError("scope_missing")
        return account, await self._token(account)

    @staticmethod
    def _typed(exc: _GmailError) -> dict[str, Any]:
        if exc.code == "scope_missing":
            return _error(
                "scope_missing",
                "This account has not granted gmail.modify; re-consent through "
                f"{RECONSENT_ROUTE}?force_consent=true",
            )
        if exc.code == "unknown_account":
            return _error("unknown_account", "No active connected Google account matches")
        return _error(exc.code)

    # -- Gmail REST ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        retry: bool,
    ) -> Any:
        client = self._http_client
        if client is None:
            raise _GmailError("provider_error")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        for attempt in range(2 if retry else 1):
            try:
                response = await client.request(
                    method, f"{GMAIL_API_BASE}/{path}", params=params, json=json, headers=headers
                )
            except httpx.HTTPError as exc:
                raise _GmailError("provider_error") from exc
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise _GmailError("provider_error") from exc
            if response.status_code == 204 and method == "DELETE":
                return {}
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and retry and attempt == 0:
                await asyncio.sleep(RETRY_BACKOFF_S)
                continue
            raise _GmailError(_status_code(response.status_code), response.status_code)
        raise _GmailError("provider_error")  # pragma: no cover

    # -- scrubbing -------------------------------------------------------------------

    @staticmethod
    def _scrub(text: str) -> str:
        return scrub_text(text, provider_domain=_SCRUB_DOMAIN)[0]

    # -- tools -----------------------------------------------------------------------

    async def _list_mailboxes(self) -> dict[str, Any]:
        mailboxes: list[dict[str, Any]] = []
        for account in await list_google_accounts(self._pool()):
            if account.status != "active":
                continue
            entry: dict[str, Any] = {
                "account": self._label(account),
                "is_primary": account.is_primary,
                "modify_granted": self._has_scope(account),
                "labels": [],
            }
            if entry["modify_granted"]:
                try:
                    token = await self._token(account)
                    data = await self._request("GET", "labels", token, retry=True)
                    entry["labels"] = _allowed_labels(data.get("labels"))
                except _GmailError as exc:
                    entry["error"] = exc.code
            mailboxes.append(entry)
        return {"status": "ok", "mailboxes": mailboxes}

    async def _search(
        self, account: str, query: str, max_results: int, page_token: str | None
    ) -> dict[str, Any]:
        if (
            not isinstance(max_results, int)
            or isinstance(max_results, bool)
            or not 1 <= max_results <= MAX_SEARCH_RESULTS
        ):
            return _error(
                "invalid_argument", f"max_results must be between 1 and {MAX_SEARCH_RESULTS}"
            )
        try:
            acct, token = await self._authorize(account)
            params: dict[str, Any] = {"q": query, "maxResults": max_results}
            if page_token:
                params["pageToken"] = page_token
            data = await self._request("GET", "threads", token, params=params, retry=True)
        except _GmailError as exc:
            return self._typed(exc)
        threads = []
        for item in data.get("threads") or []:
            summary: dict[str, Any] = {"thread_id": item.get("id")}
            try:
                summary["snippet"] = self._scrub(item.get("snippet") or "")
            except Exception:  # noqa: BLE001 - fail closed: metadata only
                logger.warning("gmail: scrubber failed on a snippet; withholding it")
                summary["content_withheld"] = True
            threads.append(summary)
        return {
            "status": "ok",
            "account": self._label(acct),
            "threads": threads,
            "next_page_token": data.get("nextPageToken"),
        }

    def _render_message(self, message: dict[str, Any]) -> dict[str, Any]:
        raw_payload = message.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        raw_headers = payload.get("headers")
        headers: list[Any] = raw_headers if isinstance(raw_headers, list) else []

        def header(name: str) -> str | None:
            for entry in headers:
                if header_name_is(entry, name.lower()):
                    return header_str_value(entry)
            return None

        rendered: dict[str, Any] = {
            "id": message.get("id"),
            "label_ids": list(message.get("labelIds") or []),
            "from": header("From"),
            "to": header("To"),
            "cc": header("Cc"),
            "date": header("Date"),
            "message_id": header("Message-ID"),
            "attachments": attachment_metadata(payload),
        }
        try:
            body = self._scrub(extract_body(payload))
            rendered["subject"] = self._scrub(header("Subject") or "")
            rendered["snippet"] = self._scrub(message.get("snippet") or "")
        except Exception:  # noqa: BLE001 - fail closed: metadata only, no body or snippet
            logger.warning("gmail: content scrub failed; withholding message content")
            rendered.pop("subject", None)
            rendered.pop("snippet", None)
            rendered["content_withheld"] = True
            return rendered
        rendered["truncated"] = len(body) > BODY_CHAR_LIMIT
        rendered["body"] = body[:BODY_CHAR_LIMIT]
        return rendered

    async def _get_thread(self, account: str, thread_id: str) -> dict[str, Any]:
        if not _valid_id(thread_id):
            return _error("invalid_argument", "thread_id is not a valid Gmail id")
        try:
            acct, token = await self._authorize(account)
            data = await self._request(
                "GET", f"threads/{thread_id}", token, params={"format": "full"}, retry=True
            )
        except _GmailError as exc:
            return self._typed(exc)
        messages = [m for m in data.get("messages") or [] if isinstance(m, dict)]
        order = sorted(range(len(messages)), key=lambda i: (_internal_date(messages[i]), i))
        return {
            "status": "ok",
            "account": self._label(acct),
            "thread_id": data.get("id", thread_id),
            "messages": [self._render_message(messages[i]) for i in order],
        }

    @staticmethod
    def _modify_refusal(remove: list[str]) -> str | None:
        """Defense in depth for the Gmail exposure rule; the broker is its enforcement point.

        The broker binds an exposed or third-party goal to its admitting thread, parks any
        other target or an INBOX/UNREAD removal for per-event approval, and caps the goal at
        five calls (RFC 0038 D13). The domain cannot see the goal's thread or count, so it
        re-checks what the call alone decides: missing lineage or an unknown origin is refused,
        and an exposed or third-party call that removes INBOX or UNREAD runs only as the
        owner-approved call itself. Exposure decides, not origin: an unexposed owner,
        obligation or staffer goal keeps the ordinary record behavior.
        """
        lineage = current_lineage()
        if lineage is None or lineage.origin not in _KNOWN_ORIGINS:
            return "Mailbox changes need a verified goal lineage"
        if not (lineage.exposed or lineage.origin == "third_party"):
            return None
        approved = lineage.capability == _MODIFY_TOOL and lineage.approval_ref is not None
        if not approved and set(remove) & _PARKED_REMOVALS:
            return (
                "Archiving or marking read under an exposed or third-party goal needs the "
                "owner's approval of this exact change"
            )
        return None

    @staticmethod
    def _static_label_problem(add: list[str], remove: list[str]) -> str | None:
        if not add and not remove:
            return "an empty change"
        for label in (*add, *remove):
            if not isinstance(label, str) or not label.strip():
                return "a blank label"
            if label in _REFUSED_SYSTEM_LABELS or label.startswith("CATEGORY_"):
                return f"the label {label} cannot be changed here"
        if set(add) & set(remove):
            return "a label cannot be both added and removed"
        return None

    @staticmethod
    def _resolve_labels(names: list[str], labels: list[dict[str, Any]]) -> list[str] | str:
        """Label ids for ``names``, or the first unusable name."""
        resolved: list[str] = []
        for wanted in names:
            if wanted in _ALLOWED_SYSTEM_LABELS:
                resolved.append(wanted)
                continue
            match = next(
                (
                    entry
                    for entry in labels
                    if entry.get("type") == "user"
                    and wanted in (entry.get("id"), entry.get("name"))
                ),
                None,
            )
            if match is None:
                return wanted
            resolved.append(str(match["id"]))
        return resolved

    async def _modify_threads(
        self,
        account: str,
        thread_ids: list[str],
        add_labels: list[str] | None,
        remove_labels: list[str] | None,
    ) -> dict[str, Any]:
        add, remove = list(add_labels or []), list(remove_labels or [])
        ids = list(dict.fromkeys(thread_ids or []))
        if not ids:
            return _error("invalid_argument", "thread_ids is empty")
        if len(ids) > MAX_THREADS_PER_MODIFY:
            return _error(
                "invalid_argument", f"at most {MAX_THREADS_PER_MODIFY} thread ids per call"
            )
        if not all(_valid_id(thread_id) for thread_id in ids):
            return _error("invalid_argument", "a thread id is not a valid Gmail id")
        problem = self._static_label_problem(add, remove)
        if problem:
            return _error("invalid_argument", problem)
        refusal = self._modify_refusal(remove)
        if refusal is not None:
            return _error("exposed_modify_refused", refusal)
        try:
            acct, token = await self._authorize(account)
            if all(label in _ALLOWED_SYSTEM_LABELS for label in (*add, *remove)):
                add_ids, remove_ids = add, remove
            else:
                data = await self._request("GET", "labels", token, retry=True)
                labels = [e for e in data.get("labels") or [] if isinstance(e, dict)]
                add_resolved = self._resolve_labels(add, labels)
                remove_resolved = self._resolve_labels(remove, labels)
                for resolved in (add_resolved, remove_resolved):
                    if isinstance(resolved, str):
                        return _error("invalid_argument", f"unknown label {resolved!r}")
                assert not isinstance(add_resolved, str) and not isinstance(remove_resolved, str)
                if set(add_resolved) & set(remove_resolved):
                    return _error("invalid_argument", "a label cannot be both added and removed")
                add_ids, remove_ids = add_resolved, remove_resolved
        except _GmailError as exc:
            return self._typed(exc)
        body: dict[str, Any] = {}
        if add_ids:
            body["addLabelIds"] = add_ids
        if remove_ids:
            body["removeLabelIds"] = remove_ids
        outcomes: list[dict[str, Any]] = []
        for thread_id in ids:
            try:
                await self._request(
                    "POST", f"threads/{thread_id}/modify", token, json=body, retry=False
                )
                outcomes.append({"thread_id": thread_id, "status": "ok"})
            except _GmailError as exc:
                outcomes.append({"thread_id": thread_id, "status": "error", "error": exc.code})
        failed = [o["thread_id"] for o in outcomes if o["status"] != "ok"]
        if not failed:
            status = "ok"
        elif len(failed) == len(outcomes):
            status = "error"
        else:
            status = "partial"
        result: dict[str, Any] = {
            "status": status,
            "account": self._label(acct),
            "partial": bool(failed),
            "failed_thread_ids": failed,
            "outcomes": outcomes,
        }
        if status == "error":
            result["error"] = "modify_failed"
        return result

    # -- registration ----------------------------------------------------------------

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        if isinstance(config, dict):
            config = GmailConfig(**config)
        if isinstance(config, GmailConfig):
            self._config = config
        self._db = db
        if not enforcement_enabled():
            # Dark until the RFC 0038 switch: today's registry holds no gmail tool.
            self._tools_registered = False
            return
        self._tools_registered = True
        module = self

        @mcp.tool(name="gmail_list_mailboxes")
        async def gmail_list_mailboxes() -> dict[str, Any]:
            """List the connected Google accounts the assistant may act in, with their labels.

            Each mailbox carries ``account`` (use it as the ``account`` argument elsewhere),
            ``is_primary``, ``modify_granted`` and the labels that can be changed here (INBOX,
            UNREAD, STARRED, IMPORTANT and the owner's own labels).
            """
            return await module._list_mailboxes()

        @mcp.tool(name="gmail_search")
        async def gmail_search(
            account: str,
            query: str,
            max_results: int = DEFAULT_SEARCH_RESULTS,
            page_token: str | None = None,
        ) -> dict[str, Any]:
            """Search one account with Gmail query syntax; thread summaries, newest first.

            Snippets are third-party text, untrusted, and bearer-scrubbed. Reading exposes the
            invocation. At most 50 threads per call; pass ``next_page_token`` as ``page_token``
            for the next page.

            Args:
                account: A ``gmail_list_mailboxes`` account (email).
                query: Gmail search query, for example ``from:ann newer_than:7d``.
                max_results: 1 to 50 (default 20).
                page_token: The previous result's ``next_page_token``.
            """
            return await module._search(account, query, max_results, page_token)

        @mcp.tool(name="gmail_get_thread")
        async def gmail_get_thread(account: str, thread_id: str) -> dict[str, Any]:
            """Read one thread: headers, decoded plain-text bodies, attachment metadata only.

            Messages come oldest first. Bodies are third-party text, untrusted and
            bearer-scrubbed, truncated per message (``truncated``). Attachment content is
            never returned.
            """
            return await module._get_thread(account, thread_id)

        @mcp.tool(name="gmail_modify_threads")
        async def gmail_modify_threads(
            account: str,
            thread_ids: list[str],
            add_labels: list[str] | None = None,
            remove_labels: list[str] | None = None,
        ) -> dict[str, Any]:
            """Archive, mark read, star or label threads in the owner's own mailbox.

            Removing INBOX archives (adding it unarchives); removing UNREAD marks read.
            Allowed labels: INBOX, UNREAD, STARRED, IMPORTANT and existing user labels by id
            or exact name. Never trashes, deletes, sends or drafts. At most 50 threads per
            call; a partial failure returns per-thread outcomes and ``failed_thread_ids``.
            After reading mail or under a third-party goal, only the thread that started the
            goal changes directly; another thread, or archiving or marking read, waits for
            the owner's approval, and at most five such calls run per goal.
            """
            return await module._modify_threads(account, thread_ids, add_labels, remove_labels)

        @mcp.tool(name="gmail_list_drafts")
        async def gmail_list_drafts(
            account: str, max_results: int = 20, page_token: str | None = None
        ) -> dict[str, Any]:
            """List up to 50 Gmail draft references; use next_page_token to continue.

            Drafts are private, untrusted mailbox content. Unknown/inactive accounts and
            missing gmail.modify refuse; no message is delivered.
            """
            return await module._list_drafts(account, max_results, page_token)

        @mcp.tool(name="gmail_get_draft")
        async def gmail_get_draft(account: str, draft_id: str) -> dict[str, Any]:
            """Read a draft's scrubbed recipients, subject and plaintext body without sending.

            Content is untrusted and withheld if scrubbing fails; a missing draft returns
            not_found. A read exposes the invocation like every Gmail content read.
            """
            return await module._get_draft(account, draft_id)

        @mcp.tool(name="gmail_save_draft")
        async def gmail_save_draft(
            account: str,
            to: list[str],
            subject: str,
            body: str,
            cc: list[str] | None = None,
            draft_id: str | None = None,
            thread_id: str | None = None,
            in_reply_to_message_id: str | None = None,
        ) -> dict[str, Any]:
            """Create a new Gmail draft or replace draft_id; this delivers nothing.

            Optional thread_id and in_reply_to_message_id must be paired and name the
            message being replied to. Use 1-20 explicit bare To/Cc addresses, a matching
            single-line subject and at most 50,000 plaintext body characters. No HTML,
            Bcc or attachments. Exposed and third-party goals park for exact owner approval.
            Draft writes never retry automatically; inspect drafts after an uncertain result.
            """
            return await module._save_draft(
                {
                    "account": account,
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "cc": cc,
                    "draft_id": draft_id,
                    "thread_id": thread_id,
                    "in_reply_to_message_id": in_reply_to_message_id,
                }
            )

        @mcp.tool(name="gmail_send_reply")
        async def gmail_send_reply(
            account: str,
            thread_id: str,
            in_reply_to_message_id: str,
            in_reply_to_sender: str,
            in_reply_to_date: str,
            to: list[str],
            subject: str,
            body: str,
            cc: list[str] | None = None,
            replaces_draft_id: str | None = None,
        ) -> dict[str, Any]:
            """Send one exact plaintext reply in an existing Gmail thread, approval-gated.

            Copy in_reply_to_sender/date from gmail_get_thread so the owner sees the pinned
            target context; dispatch verifies both against that message. Explicit To/Cc,
            subject and body are the entire approved content (1-20 recipients, <=50,000 body
            characters). No new-message send, Bcc, HTML or attachments. The verified effect
            ID is the durable send identity; sent/failed/unknown receipts never send again.
            Unknown outcomes require exact SENT Message-ID evidence or owner reconciliation.
            A replacement draft is deleted only when its current content still matches.
            """
            return await module._send_reply(
                {
                    "account": account,
                    "thread_id": thread_id,
                    "in_reply_to_message_id": in_reply_to_message_id,
                    "in_reply_to_sender": in_reply_to_sender,
                    "in_reply_to_date": in_reply_to_date,
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "cc": cc,
                    "replaces_draft_id": replaces_draft_id,
                }
            )


def _internal_date(message: dict[str, Any]) -> int:
    try:
        return int(message.get("internalDate") or 0)
    except (TypeError, ValueError):
        return 0


def _allowed_labels(raw: Any) -> list[dict[str, str]]:
    """Labels a mailbox listing exposes: the four system labels and user labels."""
    out: list[dict[str, str]] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        label_id, name, kind = entry.get("id"), entry.get("name"), entry.get("type")
        if not isinstance(label_id, str) or not isinstance(name, str):
            continue
        if (kind == "system" and label_id in _ALLOWED_SYSTEM_LABELS) or kind == "user":
            out.append({"id": label_id, "name": name, "type": str(kind)})
    return out


__all__ = [
    "BODY_CHAR_LIMIT",
    "DEFAULT_SEARCH_RESULTS",
    "MAX_SEARCH_RESULTS",
    "MAX_THREADS_PER_MODIFY",
    "GmailConfig",
    "GmailModule",
]
