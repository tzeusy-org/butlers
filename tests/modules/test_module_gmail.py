"""Gmail module: owner-mailbox search, read, archive and label (bu-kqme7f.5).

Every test runs against a fake Gmail transport behind ``httpx.MockTransport``; no test
reaches a real Google account. Account listing, credential resolution and the revoked-grant
write are the three seams patched on the module.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import FastMCP

from butlers.core.goal_lineage import VerifiedLineage, lineage_scope
from butlers.google_account_registry import GoogleAccount
from butlers.google_credentials import GoogleCredentials
from butlers.modules.base import Module
from butlers.modules.gmail import (
    BODY_CHAR_LIMIT,
    MAX_SEARCH_RESULTS,
    MAX_THREADS_PER_MODIFY,
    GmailConfig,
    GmailModule,
)
from butlers.testing.mcp import registered_tools

pytestmark = pytest.mark.unit

GMAIL_TOOLS = {
    "gmail_list_mailboxes",
    "gmail_search",
    "gmail_get_thread",
    "gmail_modify_threads",
    "gmail_list_drafts",
    "gmail_get_draft",
    "gmail_save_draft",
    "gmail_send_reply",
}
MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
OWNER = "owner@example.test"
OTHER = "second@example.test"


def _b64(text: str, charset: str = "utf-8") -> str:
    return base64.urlsafe_b64encode(text.encode(charset)).decode("ascii")


def _account(email: str, *, scopes: list[str] | None = None, status: str = "active") -> Any:
    return GoogleAccount(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, email),
        entity_id=uuid.uuid4(),
        email=email,
        display_name=None,
        is_primary=email == OWNER,
        granted_scopes=[MODIFY_SCOPE] if scopes is None else scopes,
        status=status,
        connected_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_token_refresh_at=None,
    )


class FakeGmail:
    """Recording Gmail REST + token endpoint fake."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "UNREAD", "name": "UNREAD", "type": "system"},
            {"id": "STARRED", "name": "STARRED", "type": "system"},
            {"id": "IMPORTANT", "name": "IMPORTANT", "type": "system"},
            {"id": "TRASH", "name": "TRASH", "type": "system"},
            {"id": "SPAM", "name": "SPAM", "type": "system"},
            {"id": "Label_7", "name": "Receipts", "type": "user"},
            {"id": "Label_9", "name": "Travel", "type": "user"},
        ]
        self.threads: dict[str, dict[str, Any]] = {}
        self.search_pages: list[dict[str, Any]] = []
        self.search_status = 200
        self.token_status = 200
        self.token_body: dict[str, Any] = {"error": "invalid_grant"}
        self.modify_failures: dict[str, int] = {}
        self.minted = 0
        self.drafts: dict[str, dict[str, Any]] = {}
        self.sent: list[dict[str, Any]] = []
        self.send_status = 200
        self.send_timeout = False
        self.messages = {"m1": {**_message("m1"), "threadId": "t1"}}

    @property
    def gmail_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "gmail.googleapis.com"]

    @property
    def modifies(self) -> list[httpx.Request]:
        return [r for r in self.gmail_calls if r.url.path.endswith("/modify")]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "oauth2.googleapis.com":
            if self.token_status != 200:
                return httpx.Response(self.token_status, json=self.token_body)
            self.minted += 1
            return httpx.Response(
                200, json={"access_token": f"tok-{self.minted}", "expires_in": 3600}
            )
        assert request.headers["authorization"].startswith("Bearer tok-")
        prefix = "/gmail/v1/users/me/"
        if path == prefix + "messages/send":
            self.sent.append(json.loads(request.read()))
            if self.send_timeout:
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            return httpx.Response(self.send_status, json={"id": "sent1", "threadId": "t1"})
        if path.startswith(prefix + "messages/"):
            message = self.messages.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=message) if message else httpx.Response(404)
        if path == prefix + "drafts" and request.method == "GET":
            return httpx.Response(200, json={"drafts": [{"id": k} for k in self.drafts]})
        if path.startswith(prefix + "drafts"):
            draft_id = path.rsplit("/", 1)[-1] if path != prefix + "drafts" else "d1"
            if request.method == "DELETE":
                self.drafts.pop(draft_id, None)
                return httpx.Response(204)
            if request.method in {"POST", "PUT"}:
                self.drafts[draft_id] = {"id": draft_id, **json.loads(request.read())}
            draft = self.drafts.get(draft_id)
            return httpx.Response(200, json=draft) if draft else httpx.Response(404)
        if path == "/gmail/v1/users/me/labels":
            return httpx.Response(200, json={"labels": self.labels})
        if path == "/gmail/v1/users/me/threads":
            if self.search_status != 200:
                return httpx.Response(self.search_status, json={"error": {"message": "x"}})
            token = request.url.params.get("pageToken")
            index = int(token) if token else 0
            return httpx.Response(200, json=self.search_pages[index])
        if path.endswith("/modify"):
            thread_id = path.split("/")[-2]
            status = self.modify_failures.get(thread_id)
            if status:
                return httpx.Response(status, json={"error": {"message": "nope"}})
            return httpx.Response(200, json={"id": thread_id})
        if path.startswith("/gmail/v1/users/me/threads/"):
            thread = self.threads.get(path.rsplit("/", 1)[-1])
            if thread is None:
                return httpx.Response(404, json={"error": {"message": "missing"}})
            return httpx.Response(200, json=thread)
        return httpx.Response(500)


def _message(
    message_id: str,
    *,
    subject: str = "Hello",
    body: str = "plain body",
    mime: str = "text/plain",
    labels: list[str] | None = None,
    extra_parts: list[dict[str, Any]] | None = None,
    charset: str = "utf-8",
) -> dict[str, Any]:
    headers = [
        {"name": "From", "value": "Ann <ann@example.test>"},
        {"name": "To", "value": OWNER},
        {"name": "Cc", "value": "cc@example.test"},
        {"name": "Date", "value": "Mon, 2 Nov 2026 09:00:00 +0000"},
        {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": f"<{message_id}@mail.example.test>"},
    ]
    text_part = {
        "mimeType": mime,
        "headers": [{"name": "Content-Type", "value": f"{mime}; charset={charset}"}],
        "body": {"data": _b64(body, charset)},
    }
    return {
        "id": message_id,
        "labelIds": labels if labels is not None else ["INBOX", "UNREAD"],
        "snippet": "snippet text",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": headers,
            "parts": [text_part, *(extra_parts or [])],
        },
    }


@pytest.fixture
def fake() -> FakeGmail:
    return FakeGmail()


@pytest.fixture
def accounts(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    rows = [_account(OWNER), _account(OTHER, scopes=["https://www.googleapis.com/auth/drive"])]
    monkeypatch.setattr("butlers.modules.gmail.list_google_accounts", AsyncMock(return_value=rows))
    monkeypatch.setattr(
        "butlers.modules.gmail.resolve_google_credentials",
        AsyncMock(
            return_value=GoogleCredentials(
                client_id="cid",
                client_secret="synthetic-secret",
                refresh_token="rtoken",
                scope=MODIFY_SCOPE,
            )
        ),
    )
    return rows


@pytest.fixture
def revoked(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mark = AsyncMock()
    monkeypatch.setattr("butlers.modules.gmail._mark_account_revoked", mark)
    return mark


@pytest.fixture
async def tools(
    monkeypatch: pytest.MonkeyPatch, fake: FakeGmail, accounts: list[Any], revoked: AsyncMock
) -> AsyncIterator[dict[str, Any]]:
    monkeypatch.setattr("butlers.core.goal_lineage._enforced", True)
    module = GmailModule()
    mcp = FastMCP("gmail-tools")
    db = MagicMock()
    db.pool = MagicMock()
    await module.register_tools(mcp=mcp, config={}, db=db, butler_name="messenger")
    module._db = db
    module._credential_store = MagicMock()
    module._http_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    registered: dict[str, Any] = dict(await registered_tools(mcp))
    registered["_module"] = module
    yield registered
    await module._http_client.aclose()


def _lineage(*, exposed: bool = False, origin: str = "owner") -> VerifiedLineage:
    return VerifiedLineage(
        goal_id=uuid.uuid4(),
        invocation_id=uuid.uuid4(),
        depth=1,
        origin=origin,
        exposed=exposed,
        capability="gmail_modify_threads",
        args_digest="d",
        call_id=uuid.uuid4(),
    )


# ---------------------------------------------------------------------------
# Module contract and registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_module_contract(self) -> None:
        from butlers.modules.registry import default_registry

        module = GmailModule()
        assert issubclass(GmailModule, Module)
        assert module.name == "gmail"
        assert module.config_schema is GmailConfig
        assert module.dependencies == []
        assert "gmail" in default_registry().available_modules

    async def test_switch_on_registers_the_owner_mailbox_tools(self, monkeypatch) -> None:
        monkeypatch.setattr("butlers.core.goal_lineage._enforced", True)
        module = GmailModule()
        mcp = FastMCP("gmail-tools")
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="messenger")
        assert set(await registered_tools(mcp)) == GMAIL_TOOLS
        assert set(module.tool_metadata()) == GMAIL_TOOLS

    async def test_switch_off_registers_nothing(self) -> None:
        module = GmailModule()
        mcp = FastMCP("gmail-tools")
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="messenger")
        assert set(await registered_tools(mcp)) == set()
        assert module.tool_metadata() == {}


# ---------------------------------------------------------------------------
# gmail_list_mailboxes
# ---------------------------------------------------------------------------


class TestListMailboxes:
    async def test_lists_actionable_accounts_with_labels(self, tools, fake) -> None:
        result = await tools["gmail_list_mailboxes"]()
        assert result["status"] == "ok"
        by_email = {m["account"]: m for m in result["mailboxes"]}
        assert by_email[OWNER]["is_primary"] is True
        assert by_email[OWNER]["modify_granted"] is True
        names = {label["name"] for label in by_email[OWNER]["labels"]}
        assert {"INBOX", "UNREAD", "STARRED", "IMPORTANT", "Receipts", "Travel"} <= names
        assert "TRASH" not in names and "SPAM" not in names

    async def test_account_without_scope_is_listed_without_labels(self, tools, fake) -> None:
        result = await tools["gmail_list_mailboxes"]()
        other = next(m for m in result["mailboxes"] if m["account"] == OTHER)
        assert other["modify_granted"] is False
        assert other["labels"] == []
        # Only the owner account was queried for labels.
        assert len([r for r in fake.gmail_calls if r.url.path.endswith("/labels")]) == 1


# ---------------------------------------------------------------------------
# gmail_search
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_returns_scrubbed_summaries_and_next_page_token(self, tools, fake) -> None:
        fake.search_pages = [
            {
                "threads": [{"id": "t1", "snippet": "Your code is 482913, do not share it"}],
                "nextPageToken": "1",
            },
            {"threads": [{"id": "t2", "snippet": "later"}]},
        ]
        first = await tools["gmail_search"](account=OWNER, query="from:ann")
        assert first["status"] == "ok"
        assert [t["thread_id"] for t in first["threads"]] == ["t1"]
        assert "482913" not in first["threads"][0]["snippet"]
        assert first["next_page_token"] == "1"
        second = await tools["gmail_search"](
            account=OWNER, query="from:ann", page_token=first["next_page_token"]
        )
        assert [t["thread_id"] for t in second["threads"]] == ["t2"]
        assert second["next_page_token"] is None

    async def test_default_and_maximum_result_count(self, tools, fake) -> None:
        fake.search_pages = [{"threads": []}]
        await tools["gmail_search"](account=OWNER, query="x")
        assert fake.gmail_calls[-1].url.params["maxResults"] == "20"
        await tools["gmail_search"](account=OWNER, query="x", max_results=MAX_SEARCH_RESULTS)
        assert fake.gmail_calls[-1].url.params["maxResults"] == "50"

    async def test_refuses_more_than_fifty_before_any_call(self, tools, fake) -> None:
        result = await tools["gmail_search"](account=OWNER, query="x", max_results=51)
        assert result["status"] == "error" and result["error"] == "invalid_argument"
        assert fake.requests == []

    async def test_raising_scrubber_returns_metadata_only(self, tools, fake, monkeypatch) -> None:
        fake.search_pages = [{"threads": [{"id": "t1", "snippet": "secret words"}]}]

        def boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("detector down")

        monkeypatch.setattr("butlers.modules.gmail.scrub_text", boom)
        result = await tools["gmail_search"](account=OWNER, query="x")
        thread = result["threads"][0]
        assert thread["thread_id"] == "t1"
        assert "snippet" not in thread
        assert thread["content_withheld"] is True

    async def test_rate_limit_is_typed(self, tools, fake, monkeypatch) -> None:
        monkeypatch.setattr("butlers.modules.gmail.RETRY_BACKOFF_S", 0.0)
        fake.search_status = 429
        result = await tools["gmail_search"](account=OWNER, query="x")
        assert result["error"] == "rate_limited"
        # Reads retry once with backoff.
        assert len([r for r in fake.gmail_calls if r.url.path.endswith("/threads")]) == 2

    async def test_provider_error_is_typed(self, tools, fake, monkeypatch) -> None:
        monkeypatch.setattr("butlers.modules.gmail.RETRY_BACKOFF_S", 0.0)
        fake.search_status = 503
        result = await tools["gmail_search"](account=OWNER, query="x")
        assert result["error"] == "provider_error"


# ---------------------------------------------------------------------------
# gmail_get_thread
# ---------------------------------------------------------------------------


class TestGetThread:
    async def test_headers_body_labels_and_attachment_metadata(self, tools, fake) -> None:
        attachment = {
            "mimeType": "application/pdf",
            "filename": "bill.pdf",
            "body": {"attachmentId": "ATT-SECRET-ID", "size": 4096},
        }
        fake.threads["t1"] = {
            "id": "t1",
            "messages": [
                _message("m1", subject="Invoice", body="Pay by Friday", extra_parts=[attachment])
            ],
        }
        result = await tools["gmail_get_thread"](account=OWNER, thread_id="t1")
        assert result["status"] == "ok"
        message = result["messages"][0]
        assert message["from"] == "Ann <ann@example.test>"
        assert message["to"] == OWNER and message["cc"] == "cc@example.test"
        assert message["subject"] == "Invoice"
        assert message["message_id"] == "<m1@mail.example.test>"
        assert message["label_ids"] == ["INBOX", "UNREAD"]
        assert message["body"] == "Pay by Friday" and message["truncated"] is False
        assert message["attachments"] == [
            {"filename": "bill.pdf", "mime_type": "application/pdf", "size_bytes": 4096}
        ]
        assert "ATT-SECRET-ID" not in repr(result)

    async def test_html_only_and_charset_go_through_the_shared_helper(self, tools, fake) -> None:
        fake.threads["t1"] = {
            "id": "t1",
            "messages": [
                _message(
                    "m1", body="<p>caf\xe9 <b>open</b></p>", mime="text/html", charset="latin-1"
                )
            ],
        }
        result = await tools["gmail_get_thread"](account=OWNER, thread_id="t1")
        assert result["messages"][0]["body"] == "caf\xe9 open"

    async def test_messages_oldest_first(self, tools, fake) -> None:
        newer, older = _message("m2"), _message("m1")
        newer["internalDate"], older["internalDate"] = "2000", "1000"
        fake.threads["t1"] = {"id": "t1", "messages": [newer, older]}
        result = await tools["gmail_get_thread"](account=OWNER, thread_id="t1")
        assert [m["id"] for m in result["messages"]] == ["m1", "m2"]

    async def test_long_body_is_truncated_and_flagged(self, tools, fake) -> None:
        fake.threads["t1"] = {
            "id": "t1",
            "messages": [_message("m1", body="a" * (BODY_CHAR_LIMIT + 50))],
        }
        result = await tools["gmail_get_thread"](account=OWNER, thread_id="t1")
        message = result["messages"][0]
        assert len(message["body"]) == BODY_CHAR_LIMIT and message["truncated"] is True

    async def test_one_time_code_is_scrubbed_everywhere(self, tools, fake) -> None:
        fake.threads["t1"] = {
            "id": "t1",
            "messages": [
                _message(
                    "m1",
                    subject="Your verification code is 482913",
                    body="Your verification code is 482913. It expires in 10 minutes.",
                )
            ],
        }
        result = await tools["gmail_get_thread"](account=OWNER, thread_id="t1")
        assert "482913" not in repr(result)

    async def test_raising_scrubber_returns_metadata_without_body_or_snippet(
        self, tools, fake, monkeypatch
    ) -> None:
        fake.threads["t1"] = {"id": "t1", "messages": [_message("m1", body="private words")]}

        def boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("detector down")

        monkeypatch.setattr("butlers.modules.gmail.scrub_text", boom)
        result = await tools["gmail_get_thread"](account=OWNER, thread_id="t1")
        message = result["messages"][0]
        assert message["id"] == "m1" and message["label_ids"] == ["INBOX", "UNREAD"]
        for withheld in ("body", "snippet", "subject"):
            assert withheld not in message
        assert message["content_withheld"] is True
        assert "private words" not in repr(result)

    async def test_missing_thread_is_not_found(self, tools, fake) -> None:
        result = await tools["gmail_get_thread"](account=OWNER, thread_id="nope")
        assert result["error"] == "not_found"


# ---------------------------------------------------------------------------
# gmail_modify_threads
# ---------------------------------------------------------------------------


class TestModifyThreads:
    async def test_archive_and_mark_read_apply_label_deltas(self, tools, fake) -> None:
        with lineage_scope(_lineage()):
            result = await tools["gmail_modify_threads"](
                account=OWNER, thread_ids=["t1", "t2"], remove_labels=["INBOX", "UNREAD"]
            )
        assert result["status"] == "ok" and result["partial"] is False
        assert [o["status"] for o in result["outcomes"]] == ["ok", "ok"]
        bodies = [r.read() for r in fake.modifies]
        assert len(bodies) == 2
        assert b'"removeLabelIds"' in bodies[0] and b"INBOX" in bodies[0]

    async def test_user_label_resolves_by_exact_name_to_id(self, tools, fake) -> None:
        with lineage_scope(_lineage()):
            await tools["gmail_modify_threads"](
                account=OWNER, thread_ids=["t1"], add_labels=["Receipts", "STARRED"]
            )
        body = fake.modifies[0].read()
        assert b"Label_7" in body and b"STARRED" in body

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"add_labels": ["TRASH"]},
            {"remove_labels": ["SPAM"]},
            {"add_labels": ["SENT"]},
            {"add_labels": ["DRAFT"]},
            {"add_labels": ["CHAT"]},
            {"add_labels": ["CATEGORY_PROMOTIONS"]},
            {},
            {"add_labels": ["STARRED"], "remove_labels": ["STARRED"]},
        ],
    )
    async def test_refused_before_any_call(self, tools, fake, kwargs) -> None:
        with lineage_scope(_lineage()):
            result = await tools["gmail_modify_threads"](account=OWNER, thread_ids=["t1"], **kwargs)
        assert result["status"] == "error" and result["error"] == "invalid_argument"
        assert fake.requests == []

    async def test_unknown_label_refused_before_any_modify(self, tools, fake) -> None:
        with lineage_scope(_lineage()):
            result = await tools["gmail_modify_threads"](
                account=OWNER, thread_ids=["t1"], add_labels=["NoSuchLabel"]
            )
        assert result["error"] == "invalid_argument"
        assert fake.modifies == []

    async def test_more_than_fifty_threads_refused(self, tools, fake) -> None:
        with lineage_scope(_lineage()):
            result = await tools["gmail_modify_threads"](
                account=OWNER,
                thread_ids=[f"t{i}" for i in range(MAX_THREADS_PER_MODIFY + 1)],
                add_labels=["STARRED"],
            )
        assert result["error"] == "invalid_argument"
        assert fake.requests == []

    async def test_partial_failure_names_failed_threads(self, tools, fake) -> None:
        fake.modify_failures["t2"] = 404
        with lineage_scope(_lineage()):
            result = await tools["gmail_modify_threads"](
                account=OWNER, thread_ids=["t1", "t2", "t3"], add_labels=["STARRED"]
            )
        assert result["status"] == "partial" and result["partial"] is True
        assert result["failed_thread_ids"] == ["t2"]
        by_id = {o["thread_id"]: o for o in result["outcomes"]}
        assert by_id["t1"]["status"] == "ok" and by_id["t3"]["status"] == "ok"
        assert by_id["t2"] == {"thread_id": "t2", "status": "error", "error": "not_found"}

    # Gmail exposure rule, domain half (bu-kqme7f.10). The broker binds the thread, parks and
    # caps; the domain re-checks only what the call alone decides.
    @pytest.mark.parametrize("lineage", [_lineage(origin="some_new_origin"), None])
    async def test_missing_lineage_or_unknown_origin_is_refused(self, tools, fake, lineage) -> None:
        with lineage_scope(lineage):
            result = await tools["gmail_modify_threads"](
                account=OWNER, thread_ids=["t1"], add_labels=["STARRED"]
            )
        assert result["status"] == "error"
        assert result["error"] == "exposed_modify_refused"
        assert fake.modifies == []

    @pytest.mark.parametrize(
        "lineage",
        [
            _lineage(exposed=True),
            _lineage(origin="third_party"),
            _lineage(exposed=True, origin="obligation"),
        ],
    )
    @pytest.mark.parametrize("removal", ["INBOX", "UNREAD"])
    async def test_exposed_or_third_party_archive_or_mark_read_needs_approval(
        self, tools, fake, lineage, removal
    ) -> None:
        with lineage_scope(lineage):
            result = await tools["gmail_modify_threads"](
                account=OWNER, thread_ids=["t1"], remove_labels=[removal]
            )
        assert result["error"] == "exposed_modify_refused"
        assert fake.requests == []

    @pytest.mark.parametrize(
        "lineage", [_lineage(exposed=True), _lineage(exposed=True, origin="third_party")]
    )
    async def test_exposed_label_change_runs_as_the_broker_dispatched_it(
        self, tools, fake, lineage
    ) -> None:
        with lineage_scope(lineage):
            result = await tools["gmail_modify_threads"](
                account=OWNER, thread_ids=["t1"], add_labels=["Receipts"], remove_labels=["STARRED"]
            )
        assert result["status"] == "ok"
        assert len(fake.modifies) == 1

    async def test_the_owner_approved_archive_runs_under_exposure(self, tools, fake) -> None:
        approved = replace(_lineage(exposed=True, origin="third_party"), approval_ref="a1")
        with lineage_scope(approved):
            result = await tools["gmail_modify_threads"](
                account=OWNER, thread_ids=["t9"], remove_labels=["INBOX", "UNREAD"]
            )
        assert result["status"] == "ok" and len(fake.modifies) == 1

    @pytest.mark.parametrize("origin", ["owner", "obligation", "staffer"])
    async def test_an_unexposed_goal_keeps_the_record_behavior(self, tools, fake, origin) -> None:
        # Exposure decides, not origin: an unexposed obligation is owner lineage here.
        with lineage_scope(_lineage(origin=origin)):
            result = await tools["gmail_modify_threads"](
                account=OWNER, thread_ids=["t1"], remove_labels=["INBOX"]
            )
        assert result["status"] == "ok" and len(fake.modifies) == 1


BAD_IDS = ["../messages/X/trash?", "%2e%2e/x", "a?b", "a#b", "a/b", "", "a b", "x" * 65]


class TestIdValidation:
    @pytest.mark.parametrize("bad", BAD_IDS)
    async def test_get_thread_refuses_path_injection_before_any_call(
        self, tools, fake, bad
    ) -> None:
        result = await tools["gmail_get_thread"](account=OWNER, thread_id=bad)
        assert result["status"] == "error" and result["error"] == "invalid_argument"
        assert fake.requests == []

    @pytest.mark.parametrize("bad", BAD_IDS)
    async def test_modify_refuses_path_injection_before_any_call(self, tools, fake, bad) -> None:
        with lineage_scope(_lineage()):
            result = await tools["gmail_modify_threads"](
                account=OWNER, thread_ids=["t1", bad], remove_labels=["INBOX"]
            )
        assert result["status"] == "error" and result["error"] == "invalid_argument"
        assert fake.requests == []


# ---------------------------------------------------------------------------
# Accounts, scope, revocation
# ---------------------------------------------------------------------------


class TestAccountErrors:
    async def test_unknown_account_makes_no_gmail_call(self, tools, fake) -> None:
        result = await tools["gmail_search"](account="nobody@example.test", query="x")
        assert result["error"] == "unknown_account"
        assert fake.requests == []

    async def test_inactive_account_is_unknown(self, tools, fake, accounts, monkeypatch) -> None:
        monkeypatch.setattr(
            "butlers.modules.gmail.list_google_accounts",
            AsyncMock(return_value=[_account(OWNER, status="revoked")]),
        )
        result = await tools["gmail_search"](account=OWNER, query="x")
        assert result["error"] == "unknown_account"
        assert fake.requests == []

    async def test_missing_modify_scope_names_the_reconsent_route(self, tools, fake) -> None:
        result = await tools["gmail_get_thread"](account=OTHER, thread_id="t1")
        assert result["error"] == "scope_missing"
        assert "/api/oauth/google/start" in result["message"]
        assert fake.requests == []

    async def test_modify_without_scope_makes_no_call(self, tools, fake) -> None:
        with lineage_scope(_lineage()):
            result = await tools["gmail_modify_threads"](
                account=OTHER, thread_ids=["t1"], add_labels=["STARRED"]
            )
        assert result["error"] == "scope_missing"
        assert fake.requests == []

    async def test_revoked_grant_marks_the_account_and_returns_account_revoked(
        self, tools, fake, revoked, accounts
    ) -> None:
        fake.token_status = 400
        result = await tools["gmail_search"](account=OWNER, query="x")
        assert result["error"] == "account_revoked"
        revoked.assert_awaited_once()
        assert revoked.await_args.kwargs["account_id"] == accounts[0].id
        assert revoked.await_args.kwargs["expected_refresh_token"] == "rtoken"
        assert fake.gmail_calls == []

    async def test_other_token_failure_does_not_mark_revoked(self, tools, fake, revoked) -> None:
        fake.token_status = 500
        fake.token_body = {"error": "backend_error"}
        result = await tools["gmail_search"](account=OWNER, query="x")
        assert result["error"] == "provider_error"
        revoked.assert_not_awaited()

    async def test_no_secret_material_in_any_result(self, tools, fake) -> None:
        fake.search_pages = [{"threads": []}]
        result = await tools["gmail_search"](account=OWNER, query="x")
        text = repr(result)
        for secret in ("synthetic-secret", "rtoken", "tok-1"):
            assert secret not in text


# ---------------------------------------------------------------------------
# Ingest isolation
# ---------------------------------------------------------------------------


class TestIngestIsolation:
    async def test_reads_never_submit_ingest_or_touch_the_database(
        self, tools, fake, monkeypatch
    ) -> None:
        from butlers.connectors.mcp_client import CachedMCPClient

        spy = AsyncMock()
        monkeypatch.setattr(CachedMCPClient, "call_tool", spy)
        fake.search_pages = [{"threads": [{"id": "t1", "snippet": "hi"}]}]
        fake.threads["t1"] = {"id": "t1", "messages": [_message("m1")]}
        await tools["gmail_list_mailboxes"]()
        await tools["gmail_search"](account=OWNER, query="x")
        await tools["gmail_get_thread"](account=OWNER, thread_id="t1")
        spy.assert_not_called()
        pool = tools["_module"]._db.pool
        assert pool.method_calls == []
        # The only hosts touched are Google's.
        assert {r.url.host for r in fake.requests} <= {
            "gmail.googleapis.com",
            "oauth2.googleapis.com",
        }


def reply_args(**changes):
    return {
        "account": OWNER,
        "thread_id": "t1",
        "in_reply_to_message_id": "m1",
        "in_reply_to_sender": "Ann <ann@example.test>",
        "in_reply_to_date": "Mon, 2 Nov 2026 09:00:00 +0000",
        "to": ["ann@example.test"],
        "cc": ["cc@example.test"],
        "subject": "Re: Hello",
        "body": "Exact body without an added newline: café",
        **changes,
    }


def call_lineage(tool, args, *, approved=False, exposed=False, origin="owner", effect_id=None):
    from butlers.core.capability_json import arguments_digest

    return replace(
        _lineage(exposed=exposed, origin=origin),
        capability=tool,
        args_digest=arguments_digest(args),
        approval_ref="approval-1" if approved else None,
        effect_id=effect_id or uuid.uuid4(),
    )


def decode_mime(message):
    return BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(message["raw"]))


class TestDrafts:
    async def test_read_scrubs_content_and_withholds_on_scrubber_failure(
        self, tools, fake, monkeypatch
    ):
        from butlers.modules.gmail.mime import build_message

        fake.drafts["d1"] = {
            "id": "d1",
            "message": build_message(
                OWNER,
                {
                    "to": [OWNER],
                    "subject": "Verification code",
                    "body": "Your verification code is 482913",
                },
                {},
            ),
        }
        got = await tools["gmail_get_draft"](account=OWNER, draft_id="d1")
        assert got["status"] == "ok" and "482913" not in repr(got)
        assert got["message"]["subject"] == "Verification code"

        def refuse(*args, **kwargs):
            raise RuntimeError("synthetic scrub failure")

        monkeypatch.setattr("butlers.modules.gmail.scrub_text", refuse)
        withheld = await tools["gmail_get_draft"](account=OWNER, draft_id="d1")
        assert withheld["message"] == {"content_withheld": True}
        assert "482913" not in repr(withheld)

    async def test_save_list_get_and_update_reply_draft(self, tools, fake):
        args = reply_args()
        args.pop("in_reply_to_sender")
        args.pop("in_reply_to_date")
        with lineage_scope(call_lineage("gmail_save_draft", args)):
            saved = await tools["gmail_save_draft"](**args)
        assert saved["status"] == "ok"
        raw = fake.drafts[saved["draft_id"]]["message"]
        mime = decode_mime(raw)
        assert raw["threadId"] == "t1"
        assert str(mime["From"]) == OWNER
        assert str(mime["To"]) == "ann@example.test"
        assert str(mime["Cc"]) == "cc@example.test"
        assert mime["In-Reply-To"] == "<m1@mail.example.test>"
        assert mime["References"] == "<m1@mail.example.test>"
        assert mime.get_content() == args["body"]
        assert mime["Message-ID"] is None
        listed = await tools["gmail_list_drafts"](account=OWNER)
        assert listed["drafts"] == [{"id": saved["draft_id"]}]
        got = await tools["gmail_get_draft"](account=OWNER, draft_id=saved["draft_id"])
        assert got["message"]["body"] == args["body"]
        args.update(draft_id=saved["draft_id"], body="Replacement")
        with lineage_scope(call_lineage("gmail_save_draft", args)):
            assert (await tools["gmail_save_draft"](**args))["status"] == "ok"
        assert decode_mime(fake.drafts[saved["draft_id"]]["message"]).get_content() == "Replacement"
        assert fake.sent == []

    @pytest.mark.parametrize("origin,exposed", [("owner", True), ("third_party", False)])
    async def test_exposed_draft_requires_exact_approval(self, tools, fake, origin, exposed):
        args = {"account": OWNER, "to": [OWNER], "subject": "Private", "body": "draft"}
        with lineage_scope(call_lineage("gmail_save_draft", args, origin=origin, exposed=exposed)):
            result = await tools["gmail_save_draft"](**args)
        assert result["error"] == "approval_required"
        assert fake.requests == []
        with lineage_scope(
            call_lineage("gmail_save_draft", args, approved=True, origin=origin, exposed=exposed)
        ):
            assert (await tools["gmail_save_draft"](**args))["status"] == "ok"
        assert len(fake.drafts) == 1 and fake.sent == []


class FakeSendReceipts:
    """Committed receipt seam; SQL and transaction behavior run on real PostgreSQL below."""

    def __init__(self):
        self.rows = {}

    async def get(self, effect):
        return self.rows.get(effect)

    async def reserve(self, effect, account, thread, message_id, digest):
        if effect in self.rows:
            return None
        row = dict(
            effect_id=effect,
            account=account,
            thread_id=thread,
            message_id=message_id,
            call_digest=digest,
            state="dispatching",
            gmail_message_id=None,
            draft_cleanup=None,
        )
        self.rows[effect] = row
        return row

    async def settle(self, effect, state, gmail_message_id=None):
        self.rows[effect].update(state=state, gmail_message_id=gmail_message_id)
        return self.rows[effect]

    async def cleanup(self, effect, outcome):
        self.rows[effect]["draft_cleanup"] = outcome
        return self.rows[effect]


@pytest.fixture
def send_store(monkeypatch):
    store = FakeSendReceipts()
    monkeypatch.setattr("butlers.modules.gmail.actions.SendReceipts", lambda pool: store)
    return store


class TestReplies:
    async def test_send_pins_every_header_and_returns_original_receipt(
        self, tools, fake, send_store
    ):
        args = reply_args()
        lineage = call_lineage("gmail_send_reply", args, approved=True, exposed=True)
        with lineage_scope(lineage):
            sent = await tools["gmail_send_reply"](**args)
            repeated = await tools["gmail_send_reply"](**args)
        assert sent == repeated and sent["status"] == "sent"
        assert len(fake.sent) == 1
        assert fake.sent[0]["threadId"] == "t1"
        mime = decode_mime(fake.sent[0])
        assert str(mime["From"]) == OWNER
        assert str(mime["To"]) == args["to"][0] and str(mime["Cc"]) == args["cc"][0]
        assert str(mime["Subject"]) == args["subject"]
        assert mime["In-Reply-To"] == "<m1@mail.example.test>"
        assert mime["References"] == "<m1@mail.example.test>"
        assert lineage.effect_id.hex in mime["Message-ID"] and mime["Date"]
        assert mime.get_content_type() == "text/plain" and mime.get_content() == args["body"]

    @pytest.mark.parametrize(
        "change",
        [
            {"thread_id": "t2"},
            {"subject": "Different"},
            {"to": []},
            {"cc": ["a@example.test"] * 20},
            {"to": ["bad"]},
            {"to": ["a@example.test\r\nBcc: leak@example.test"]},
            {"subject": "Re: Hello\nInjected: header"},
            {"body": "x" * 50_001},
            {"account": OTHER},
            {"in_reply_to_sender": "forged@example.test"},
            {"in_reply_to_date": "a different date"},
            {"replaces_draft_id": "../else"},
        ],
    )
    async def test_validation_refuses_before_any_write(self, tools, fake, send_store, change):
        args = reply_args(**change)
        with lineage_scope(call_lineage("gmail_send_reply", args, approved=True)):
            result = await tools["gmail_send_reply"](**args)
        assert result["status"] == "error"
        assert fake.sent == [] and send_store.rows == {}
        assert all(
            r.method == "GET" or r.url.host == "oauth2.googleapis.com" for r in fake.requests
        )

    @pytest.mark.parametrize(
        "status,times_out,expected",
        [
            (400, False, "failed"),
            (403, False, "failed"),
            (429, False, "failed"),
            (503, False, "unknown"),
            (200, True, "unknown"),
        ],
    )
    async def test_definite_and_ambiguous_outcomes_never_resend(
        self, tools, fake, send_store, status, times_out, expected
    ):
        fake.send_status, fake.send_timeout = status, times_out
        args = reply_args()
        with lineage_scope(call_lineage("gmail_send_reply", args, approved=True)):
            first = await tools["gmail_send_reply"](**args)
            repeated = await tools["gmail_send_reply"](**args)
        assert first["status"] == expected and repeated["status"] == expected
        assert len(fake.sent) == 1

    async def test_dispatching_after_crash_never_resends_and_needs_exact_sent_evidence(
        self, tools, fake, send_store
    ):
        args = reply_args()
        lineage = call_lineage("gmail_send_reply", args, approved=True)
        expected_id = f"<butlers.{lineage.effect_id.hex}@example.test>"
        await send_store.reserve(lineage.effect_id, OWNER, "t1", expected_id, lineage.args_digest)
        message = {**_message("accepted", labels=["SENT"]), "threadId": "t1"}
        fake.threads["t1"] = {"id": "t1", "messages": [message]}
        with lineage_scope(lineage):
            unknown = await tools["gmail_send_reply"](**args)
            assert unknown["status"] == "unknown"  # SENT alone is insufficient.
            for header in message["payload"]["headers"]:
                if header["name"] == "Message-ID":
                    header["value"] = expected_id
            message["labelIds"] = ["INBOX"]
            assert (await tools["gmail_send_reply"](**args))["status"] == "unknown"
            message["labelIds"] = ["SENT"]
            assert (await tools["gmail_send_reply"](**args))["status"] == "sent"
        assert fake.sent == []

    @pytest.mark.parametrize("changed", [False, True])
    async def test_cleanup_preserves_changed_draft(self, tools, fake, send_store, changed):
        from butlers.modules.gmail.mime import build_message

        args = reply_args(replaces_draft_id="d1")
        fake.drafts["d1"] = {
            "id": "d1",
            "message": build_message(
                OWNER, {**args, "body": "Owner edit"} if changed else args, {}
            ),
        }
        with lineage_scope(call_lineage("gmail_send_reply", args, approved=True)):
            result = await tools["gmail_send_reply"](**args)
        assert result["status"] == "sent"
        assert result["draft_cleanup"] == ("draft_kept_changed" if changed else "draft_removed")
        assert ("d1" in fake.drafts) is changed
        assert len(fake.sent) == 1

    async def test_receipt_commit_failure_sends_nothing(self, tools, fake, send_store):
        send_store.reserve = AsyncMock(side_effect=RuntimeError("synthetic commit failure"))
        args = reply_args()
        with lineage_scope(call_lineage("gmail_send_reply", args, approved=True)):
            result = await tools["gmail_send_reply"](**args)
        assert result["error"] == "receipt_unavailable" and fake.sent == []

    async def test_cleanup_recording_failure_preserves_confirmed_send(
        self, tools, fake, send_store
    ):
        send_store.cleanup = AsyncMock(side_effect=RuntimeError("synthetic cleanup commit failure"))
        args = reply_args(replaces_draft_id="d1")
        with lineage_scope(call_lineage("gmail_send_reply", args, approved=True)):
            result = await tools["gmail_send_reply"](**args)
        assert result["status"] == "sent" and len(fake.sent) == 1
        assert next(iter(send_store.rows.values()))["state"] == "sent"

    async def test_thread_headers_are_preserved_and_injection_refused(
        self, tools, fake, send_store
    ):
        args = reply_args()
        target_headers = fake.messages["m1"]["payload"]["headers"]
        target_headers.append({"name": "References", "value": "<prior@example.test>"})
        with lineage_scope(call_lineage("gmail_send_reply", args, approved=True)):
            assert (await tools["gmail_send_reply"](**args))["status"] == "sent"
        assert (
            decode_mime(fake.sent[0])["References"] == "<prior@example.test> <m1@mail.example.test>"
        )
        target_headers[-1]["value"] = "<prior@example.test>\r\nBcc: leak@example.test"
        with lineage_scope(call_lineage("gmail_send_reply", args, approved=True)):
            assert (await tools["gmail_send_reply"](**args))["error"] == "invalid_argument"
        assert len(fake.sent) == 1
