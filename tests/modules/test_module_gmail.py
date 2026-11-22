"""Gmail module: owner-mailbox search, read, archive and label (bu-kqme7f.5).

Every test runs against a fake Gmail transport behind ``httpx.MockTransport``; no test
reaches a real Google account. Account listing, credential resolution and the revoked-grant
write are the three seams patched on the module.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
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

    async def test_switch_on_registers_the_four_tools(self, monkeypatch) -> None:
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
