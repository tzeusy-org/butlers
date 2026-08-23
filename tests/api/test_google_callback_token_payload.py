"""The two Google OAuth callbacks validate the token payload before persisting it.

bu-n8gvq routed six extraction sites through ``validate_oauth_token_payload``
and left these two out for their bespoke error flows.  They are the two actually
reached for ``provider=google``, and both still read the payload with a bare
``.get`` behind, at most, a truthiness guard.

Truthiness is not a type check.  A ``list``, a ``dict``, or an ``int`` is truthy,
so it passed the guard and was **written** -- through ``_update_account_refresh_token``
onto the existing account's credential row, or through ``create_google_account``
into a new one.  The access token was formatted into an ``Authorization: Bearer``
header before anything confirmed it was a string.

So the assertions here are mostly about what does *not* happen, and an absence
assertion that cannot fail is worse than none at all.  Two things keep these
honest:

* the account row is not a mock.  ``_update_account_refresh_token`` is left
  unpatched and runs its real SQL against a fake connection that applies the
  writes to :class:`_AccountRow`, so "unchanged" is measured on the same object
  the happy path visibly changes -- and
  :func:`test_the_unchanged_row_assertion_can_fail` drives that happy path to
  prove the comparison has something to catch.
* :func:`test_userinfo_is_not_called_before_validation` would pass trivially if
  the callback never called userinfo at all, so its sibling asserts the valid
  payload does reach it.

All token material is synthetic and generated in this file.  The redaction tests
assert absence and never reproduce a rejected value into a message.

[bu-z6udd]
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import ExitStack, asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers import oauth as oauth_module
from butlers.api.routers.oauth import (
    _clear_state_store,
    _generate_state,
    _google_callback_from_state,
    _StateEntry,
    _store_state,
)
from butlers.google_account_registry import GoogleAccountNotFoundError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# --- Synthetic material.  None of this is, or resembles, a real credential. ---
_EXISTING_REFRESH_TOKEN = "synthetic-refresh-already-on-the-account"
_FRESH_REFRESH_TOKEN = "synthetic-refresh-from-this-exchange"
_ACCESS_TOKEN = "synthetic-access-token-not-real"
_EXISTING_SCOPES = ["scope-existing"]
_EMAIL = "callback-payload@example.invalid"

_VALID_PAYLOAD: dict[str, Any] = {
    "access_token": _ACCESS_TOKEN,
    "refresh_token": _FRESH_REFRESH_TOKEN,
    "scope": "scope-a scope-b",
    "token_type": "Bearer",
    "expires_in": 3600,
}

_USERINFO = {"email": _EMAIL, "name": "Payload Tester"}

#: Payloads a token endpoint can return with a 200 that must not be persisted.
#: The ``*_refresh_token`` cases are the bead's bug: every one is truthy, so the
#: old ``if refresh_token:`` guard passed it straight through to the store.
_MALFORMED: dict[str, dict[str, Any]] = {
    "missing_access_token": {"refresh_token": _FRESH_REFRESH_TOKEN, "expires_in": 3600},
    "non_string_access_token": {"access_token": 12345, "refresh_token": _FRESH_REFRESH_TOKEN},
    "blank_access_token": {"access_token": "   ", "refresh_token": _FRESH_REFRESH_TOKEN},
    "int_refresh_token": {"access_token": _ACCESS_TOKEN, "refresh_token": 987654321},
    "list_refresh_token": {"access_token": _ACCESS_TOKEN, "refresh_token": ["a", "b"]},
    "dict_refresh_token": {"access_token": _ACCESS_TOKEN, "refresh_token": {"t": "v"}},
    "bool_refresh_token": {"access_token": _ACCESS_TOKEN, "refresh_token": True},
    "non_string_scope": {
        "access_token": _ACCESS_TOKEN,
        "refresh_token": _FRESH_REFRESH_TOKEN,
        "scope": ["scope-a"],
    },
    "string_expires_in": {"access_token": _ACCESS_TOKEN, "expires_in": "3600"},
    "negative_expires_in": {"access_token": _ACCESS_TOKEN, "expires_in": -1},
    "absurd_expires_in": {"access_token": _ACCESS_TOKEN, "expires_in": 10**12},
    "not_an_object": ["access_token", _ACCESS_TOKEN],
}

#: Shapes that mean "Google did not issue a refresh token this time".  Each one
#: is what today's truthiness guard already treats as absent, so each must keep
#: reaching the callbacks' own ``no_refresh_token`` answer rather than the
#: generic invalid-payload rejection (AC2).
_NO_REFRESH: dict[str, dict[str, Any]] = {
    "key_absent": {"access_token": _ACCESS_TOKEN, "scope": "scope-a", "expires_in": 3600},
    "explicit_null": {"access_token": _ACCESS_TOKEN, "refresh_token": None, "scope": "scope-a"},
    "empty_string": {"access_token": _ACCESS_TOKEN, "refresh_token": "", "scope": "scope-a"},
    "whitespace_only": {"access_token": _ACCESS_TOKEN, "refresh_token": "   ", "scope": "scope-a"},
}

_EXCHANGE = "butlers.api.routers.oauth._exchange_code_for_tokens"
_USERINFO_FN = "butlers.api.routers.oauth._fetch_google_userinfo"
_GET_ACCOUNT = "butlers.api.routers.oauth.get_google_account"
_CREATE_ACCOUNT = "butlers.api.routers.oauth.create_google_account"
_STORE_APP_CREDS = "butlers.api.routers.oauth.store_app_credentials"
_STORE_GOOGLE_CREDS = "butlers.api.routers.oauth.store_google_credentials"
_RESOLVE_CREDS = "butlers.api.routers.oauth._resolve_app_credentials"
_CRED_STORE = "butlers.api.routers.oauth._make_credential_store"
_SHARED_POOL = "butlers.api.routers.oauth._get_shared_pool"
_AUDIT = "butlers.api.routers.oauth._emit_oauth_audit"


@pytest.fixture(autouse=True)
def clear_states():
    _clear_state_store()
    yield
    _clear_state_store()


# ---------------------------------------------------------------------------
# A durable account row the real persistence code writes into
# ---------------------------------------------------------------------------


class _AccountRow:
    """The pre-existing credential row a rejected callback must leave alone.

    ``_update_account_refresh_token`` is deliberately *not* mocked in this
    module: it runs its real SQL against :class:`_FakeConnection`, which applies
    the writes here.  Asserting this object is unchanged is therefore an
    assertion about the code that actually persists, not about a mock that was
    never wired to anything.
    """

    def __init__(self) -> None:
        self.entity_id = uuid.uuid4()
        self.refresh_token = _EXISTING_REFRESH_TOKEN
        self.scopes = list(_EXISTING_SCOPES)

    def snapshot(self) -> tuple[Any, ...]:
        return (self.refresh_token, tuple(self.scopes))


class _FakeConnection:
    def __init__(self, row: _AccountRow) -> None:
        self._row = row

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, query: str, *args: Any) -> str:
        if "entity_info" in query and "google_oauth_refresh" in query:
            self._row.refresh_token = args[1]
        elif "google_accounts" in query and "granted_scopes" in query:
            self._row.scopes = args[0]
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> None:
        return None


def _fake_pool(row: _AccountRow) -> MagicMock:
    conn = _FakeConnection(row)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    return pool


class _Harness:
    """Every collaborator a Google callback touches, in one place."""

    def __init__(self, row: _AccountRow, *, account_exists: bool) -> None:
        self.row = row
        self.pool = _fake_pool(row)
        self.userinfo = AsyncMock(return_value=dict(_USERINFO))
        self.create_account = AsyncMock(
            return_value=MagicMock(entity_id=uuid.uuid4()),
        )
        self.store_app_credentials = AsyncMock()
        self.store_google_credentials = AsyncMock()
        self.cred_store = AsyncMock()
        self.cred_store.store = AsyncMock()
        self.audit = AsyncMock()
        if account_exists:
            self.get_account = AsyncMock(return_value=MagicMock(entity_id=row.entity_id))
        else:
            self.get_account = AsyncMock(side_effect=GoogleAccountNotFoundError("absent"))

    def patches(self, payload: Any):
        return (
            patch(_RESOLVE_CREDS, AsyncMock(return_value=("client-id", "client-secret"))),
            patch(_EXCHANGE, AsyncMock(return_value=payload)),
            patch(_USERINFO_FN, self.userinfo),
            patch(_GET_ACCOUNT, self.get_account),
            patch(_CREATE_ACCOUNT, self.create_account),
            patch(_STORE_APP_CREDS, self.store_app_credentials),
            patch(_STORE_GOOGLE_CREDS, self.store_google_credentials),
            patch(_CRED_STORE, return_value=self.cred_store),
            patch(_SHARED_POOL, return_value=self.pool),
            patch(_AUDIT, self.audit),
        )

    def assert_nothing_persisted(self) -> None:
        """AC4: no write of any kind reached any store."""
        self.create_account.assert_not_awaited()
        self.store_app_credentials.assert_not_awaited()
        self.store_google_credentials.assert_not_awaited()
        self.cred_store.store.assert_not_awaited()


async def _call_site_one(payload: Any, harness: _Harness) -> httpx.Response:
    """Drive ``oauth_google_callback`` over HTTP, the way Google reaches it."""
    app = create_app()
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: MagicMock()
    state = _generate_state()
    _store_state(state)

    with ExitStack() as stack:
        for patcher in harness.patches(payload):
            stack.enter_context(patcher)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            return await client.get(
                "/api/oauth/google/callback", params={"code": "4/synthetic", "state": state}
            )


async def _call_site_two(payload: Any, harness: _Harness) -> Any:
    """Drive ``_google_callback_from_state`` directly.

    The literal ``/google/callback`` route is registered before
    ``/{provider}/callback``, so this function is not reachable by URL for
    ``provider=google``; ``oauth_provider_callback`` delegates to it in process.
    Calling it directly is the only way to exercise it, and is what the
    delegation does.
    """
    state_entry = _StateEntry(expiry=time.monotonic() + 300, provider="google")

    with ExitStack() as stack:
        for patcher in harness.patches(payload):
            stack.enter_context(patcher)
        return await _google_callback_from_state(
            code="4/synthetic",
            state_entry=state_entry,
            db_manager=MagicMock(),
            page_of_origin=None,
        )


def _body_of(response: Any) -> tuple[int, str]:
    """(status, rendered body) for either an httpx response or a raw Response."""
    if isinstance(response, httpx.Response):
        return response.status_code, response.text
    return response.status_code, response.body.decode("utf-8")


def _error_code(rendered: str) -> str:
    payload = json.loads(rendered)
    return payload.get("error_code") or payload["data"]["error_code"]


# ---------------------------------------------------------------------------
# AC1 + AC4: a malformed payload is rejected and nothing is persisted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(_MALFORMED), ids=list(_MALFORMED))
@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_malformed_payload_is_rejected_and_persists_nothing(case: str, site: str) -> None:
    payload = _MALFORMED[case]
    row = _AccountRow()
    before = row.snapshot()
    harness = _Harness(row, account_exists=True)

    driver = _call_site_one if site == "oauth_google_callback" else _call_site_two
    response = await driver(payload, harness)

    # The security property leads: a failure here is the bug itself, and reading
    # it first means the diagnostic names what was written rather than what
    # status code came back.
    harness.assert_nothing_persisted()
    assert row.snapshot() == before, (
        "A rejected token payload changed the pre-existing account row. The "
        "refresh token on that row is a live credential; a malformed 200 must "
        f"not be able to replace it. Row now holds: {row.snapshot()!r}"
    )

    status, rendered = _body_of(response)
    assert status == 502, rendered
    assert _error_code(rendered) == "invalid_token_payload"


@pytest.mark.parametrize("case", list(_MALFORMED), ids=list(_MALFORMED))
@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_userinfo_is_not_called_before_validation(case: str, site: str) -> None:
    """The access token never reaches a Bearer header unvalidated (AC1).

    This assertion could pass for the wrong reason -- a callback that never
    calls userinfo at all would satisfy it -- so
    :func:`test_a_valid_payload_does_reach_userinfo` pins the other side.
    """
    row = _AccountRow()
    harness = _Harness(row, account_exists=True)
    driver = _call_site_one if site == "oauth_google_callback" else _call_site_two

    await driver(_MALFORMED[case], harness)

    harness.userinfo.assert_not_awaited()


@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_a_valid_payload_does_reach_userinfo(site: str) -> None:
    """The sibling that keeps ``assert_not_awaited`` above from being vacuous."""
    row = _AccountRow()
    harness = _Harness(row, account_exists=True)
    driver = _call_site_one if site == "oauth_google_callback" else _call_site_two

    await driver(dict(_VALID_PAYLOAD), harness)

    harness.userinfo.assert_awaited_once()
    assert harness.userinfo.await_args.args[0] == _ACCESS_TOKEN


# ---------------------------------------------------------------------------
# The absence assertions above, proved capable of failing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_the_unchanged_row_assertion_can_fail(site: str) -> None:
    """Drive the write the rejection tests assert never happens.

    ``assert row.snapshot() == before`` is only evidence if this same row, this
    same fake connection, and the same unmocked ``_update_account_refresh_token``
    can in fact move it.  A valid payload for an existing account does exactly
    that, so the comparison is measuring a reachable state, not an inert object.
    """
    row = _AccountRow()
    before = row.snapshot()
    harness = _Harness(row, account_exists=True)
    driver = _call_site_one if site == "oauth_google_callback" else _call_site_two

    await driver(dict(_VALID_PAYLOAD), harness)

    assert row.snapshot() != before
    assert row.refresh_token == _FRESH_REFRESH_TOKEN
    assert row.scopes == ["scope-a", "scope-b"]


@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_the_nothing_persisted_assertion_can_fail(site: str) -> None:
    """The other half: a valid payload for a *new* account does reach the store."""
    row = _AccountRow()
    harness = _Harness(row, account_exists=False)
    driver = _call_site_one if site == "oauth_google_callback" else _call_site_two

    await driver(dict(_VALID_PAYLOAD), harness)

    harness.create_account.assert_awaited_once()
    assert harness.create_account.await_args.kwargs["refresh_token"] == _FRESH_REFRESH_TOKEN
    with pytest.raises(AssertionError):
        harness.assert_nothing_persisted()


# ---------------------------------------------------------------------------
# AC2: no_refresh_token semantics survive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(_NO_REFRESH), ids=list(_NO_REFRESH))
@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_new_account_without_a_refresh_token_keeps_its_own_answer(
    case: str, site: str
) -> None:
    """A refresh token Google did not issue is not a malformed payload.

    All four shapes are what the previous truthiness guard already treated as
    absent.  Each must keep the distinct, actionable ``no_refresh_token``
    response that tells the user to re-authorize with ``force_consent=true``,
    rather than collapsing into ``invalid_token_payload``.
    """
    row = _AccountRow()
    harness = _Harness(row, account_exists=False)
    driver = _call_site_one if site == "oauth_google_callback" else _call_site_two

    response = await driver(_NO_REFRESH[case], harness)

    status, rendered = _body_of(response)
    assert status == 400, rendered
    assert _error_code(rendered) == "no_refresh_token"
    harness.create_account.assert_not_awaited()


@pytest.mark.parametrize("case", list(_NO_REFRESH), ids=list(_NO_REFRESH))
@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_existing_account_without_a_refresh_token_keeps_the_stored_one(
    case: str, site: str
) -> None:
    """The re-authorization path: no new token, so the old one stays put.

    This is the common case -- Google omits ``refresh_token`` on every
    authorization after the first -- and it must still succeed.  Rejecting it
    would break re-auth for every already-connected account.
    """
    row = _AccountRow()
    before = row.snapshot()
    harness = _Harness(row, account_exists=True)
    driver = _call_site_one if site == "oauth_google_callback" else _call_site_two

    response = await driver(_NO_REFRESH[case], harness)

    status, _rendered = _body_of(response)
    assert status in (302, 307)
    assert row.snapshot() == before, (
        "A callback carrying no refresh token overwrote the stored one. "
        "Preserving it is the whole point of this branch."
    )


# ---------------------------------------------------------------------------
# AC3: nothing the provider sent reaches an error surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(_MALFORMED), ids=list(_MALFORMED))
@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_no_provider_value_reaches_the_response_body(case: str, site: str) -> None:
    payload = _MALFORMED[case]
    row = _AccountRow()
    harness = _Harness(row, account_exists=True)
    driver = _call_site_one if site == "oauth_google_callback" else _call_site_two

    response = await driver(payload, harness)
    _status, rendered = _body_of(response)

    values = payload.values() if isinstance(payload, dict) else payload
    for value in values:
        if isinstance(value, str) and not value.strip():
            continue
        assert str(value) not in rendered, (
            f"a provider-supplied value from the {case!r} payload was echoed back"
        )


@pytest.mark.parametrize("case", list(_MALFORMED), ids=list(_MALFORMED))
async def test_no_provider_value_reaches_the_audit_note(case: str) -> None:
    """Only ``_google_callback_from_state`` audits; the older callback does not."""
    payload = _MALFORMED[case]
    row = _AccountRow()
    harness = _Harness(row, account_exists=True)

    await _call_site_two(payload, harness)

    assert [c.kwargs.get("action") for c in harness.audit.call_args_list] == ["failed"]
    assert [c.kwargs.get("note") for c in harness.audit.call_args_list] == ["Invalid token payload"]
    assert [c.kwargs.get("failure_category") for c in harness.audit.call_args_list] == ["malformed"]


@pytest.mark.parametrize("case", list(_MALFORMED), ids=list(_MALFORMED))
@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_no_provider_value_reaches_a_log_line(
    case: str, site: str, caplog: pytest.LogCaptureFixture
) -> None:
    payload = _MALFORMED[case]
    row = _AccountRow()
    harness = _Harness(row, account_exists=True)
    driver = _call_site_one if site == "oauth_google_callback" else _call_site_two

    with caplog.at_level("DEBUG", logger="butlers.api.routers.oauth"):
        await driver(payload, harness)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    values = payload.values() if isinstance(payload, dict) else payload
    for value in values:
        if isinstance(value, str) and not value.strip():
            continue
        assert str(value) not in logged, (
            f"a provider-supplied value from the {case!r} payload reached a log line"
        )
