"""``_fetch_google_userinfo`` types its own return before a caller reads it.

The helper is annotated ``-> dict[str, Any]`` and returned ``response.json()``
unchecked, so the annotation was a claim nothing verified.  Both callers then
read it with ``userinfo.get("email")`` inside a ``try`` that catches only
``_UserinfoError``, which leaves two holes:

* a JSON body that is not an object makes ``.get`` raise ``AttributeError``.
  That is not ``_UserinfoError``, so it walks past the handler whose whole job
  is to turn a bad userinfo response into a structured 502, and the OAuth
  callback answers 500 instead.
* an ``email`` that is present but not a string passes straight through into
  ``create_google_account(email=...)`` -- a wrong type crossing into account
  creation, at the one step that decides which account a credential belongs to.

So these tests run the *real* helper.  Every other oauth test module patches
``_fetch_google_userinfo`` out, which replaces the code under test with a mock
that returns a dict by construction -- the malformed bodies below could not
occur.  Here ``oauth.httpx`` is swapped module-locally instead, so the helper
does its own parse and its own validation over a canned body, and the callback
tests exercise the same path Google reaches.

The distinction the fix has to hold is between *malformed* and *merely absent*.
An absent, ``null``, or blank ``email`` is what Google sends when the ``email``
scope was not granted; the callbacks already answer that by skipping account
resolution and completing the flow, so it must keep that verdict.  A ``list``
or an ``int`` is not that case, and is rejected.

All material here is synthetic and built in this file.  The redaction
assertions check that a rejected value is absent from the message and the
response body; they never reproduce one.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import ExitStack
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
    _UserinfoError,
)
from butlers.google_account_registry import GoogleAccountNotFoundError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# --- Synthetic material.  None of this is, or resembles, a real credential. ---
_ACCESS_TOKEN = "synthetic-access-token-not-real"
_REFRESH_TOKEN = "synthetic-refresh-from-this-exchange"
_EMAIL = "userinfo-payload@example.invalid"
_DISPLAY_NAME = "Userinfo Payload Tester"

#: Embedded in every malformed value that can carry a string, so the redaction
#: assertions have something specific to look for.  A rejected body must not
#: put this in an error message, a log line, or a response body.
_LEAK_MARKER = "userinfo-leak-marker-2f9a"

_VALID_TOKEN_PAYLOAD: dict[str, Any] = {
    "access_token": _ACCESS_TOKEN,
    "refresh_token": _REFRESH_TOKEN,
    "scope": "scope-a scope-b",
    "token_type": "Bearer",
    "expires_in": 3600,
}

_VALID_USERINFO: dict[str, Any] = {
    "email": _EMAIL,
    "name": _DISPLAY_NAME,
    "id": "synthetic-google-subject-0001",
}

#: Userinfo bodies a 200 can carry that the helper must refuse to hand back.
#: The ``top_level_*`` cases are the ``AttributeError`` half; the ``*_email``
#: cases are the half that reached ``create_google_account``.
_MALFORMED: dict[str, Any] = {
    "top_level_array": [{"email": f"{_LEAK_MARKER}@example.invalid"}],
    "top_level_empty_array": [],
    "top_level_int": 42,
    "top_level_string": _LEAK_MARKER,
    "top_level_null": None,
    "int_email": {"email": 12345, "name": _DISPLAY_NAME},
    "list_email": {"email": [f"{_LEAK_MARKER}@example.invalid"], "name": _DISPLAY_NAME},
    "dict_email": {"email": {"address": f"{_LEAK_MARKER}@example.invalid"}},
    "bool_email": {"email": True, "name": _DISPLAY_NAME},
    "int_name": {"email": _EMAIL, "name": 999},
    "list_name": {"email": _EMAIL, "name": [_LEAK_MARKER]},
    "bool_name": {"email": _EMAIL, "name": False},
}

#: Bodies that are *not* malformed: each says "Google did not give me this
#: field", which the callbacks already handle.  Value is the body plus the
#: ``(email, name)`` the helper must report for it.
_TOLERATED: dict[str, tuple[dict[str, Any], tuple[str | None, str | None]]] = {
    "email_absent": ({"name": _DISPLAY_NAME}, (None, _DISPLAY_NAME)),
    "email_null": ({"email": None, "name": _DISPLAY_NAME}, (None, _DISPLAY_NAME)),
    "email_blank": ({"email": "", "name": _DISPLAY_NAME}, (None, _DISPLAY_NAME)),
    "email_whitespace": ({"email": "   ", "name": _DISPLAY_NAME}, (None, _DISPLAY_NAME)),
    "name_absent": ({"email": _EMAIL}, (_EMAIL, None)),
    "name_null": ({"email": _EMAIL, "name": None}, (_EMAIL, None)),
    "name_blank": ({"email": _EMAIL, "name": "  "}, (_EMAIL, None)),
    "both_absent": ({"id": "synthetic-google-subject-0002"}, (None, None)),
    "padded_email": ({"email": f"  {_EMAIL}  "}, (_EMAIL, None)),
}

_EXCHANGE = "butlers.api.routers.oauth._exchange_code_for_tokens"
_GET_ACCOUNT = "butlers.api.routers.oauth.get_google_account"
_CREATE_ACCOUNT = "butlers.api.routers.oauth.create_google_account"
_STORE_APP_CREDS = "butlers.api.routers.oauth.store_app_credentials"
_STORE_GOOGLE_CREDS = "butlers.api.routers.oauth.store_google_credentials"
_RESOLVE_CREDS = "butlers.api.routers.oauth._resolve_app_credentials"
_CRED_STORE = "butlers.api.routers.oauth._make_credential_store"
_SHARED_POOL = "butlers.api.routers.oauth._get_shared_pool"
_AUDIT = "butlers.api.routers.oauth._emit_oauth_audit"
_UPDATE_REFRESH = "butlers.api.routers.oauth._update_account_refresh_token"


@pytest.fixture(autouse=True)
def clear_states():
    _clear_state_store()
    yield
    _clear_state_store()


@pytest.fixture(autouse=True)
def stub_off(monkeypatch):
    """The OAuth stub short-circuits the helper before it parses anything."""
    monkeypatch.delenv("TEST_MODE_OAUTH_STUB", raising=False)
    monkeypatch.delenv("ENV", raising=False)


# ---------------------------------------------------------------------------
# A canned userinfo response, delivered through the real helper
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: Any) -> None:
        self._body = body
        self.status_code = 200

    def json(self) -> Any:
        return self._body


class _FakeAsyncClient:
    def __init__(self, body: Any) -> None:
        self._body = body

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse(self._body)

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        # The callback pings the Gmail connector to reload; it is best-effort.
        return _FakeResponse({})


class _HttpxShim:
    """``oauth.httpx`` with only ``AsyncClient`` swapped for a canned body.

    Patching ``httpx.AsyncClient`` itself would mutate the shared module object
    and break the ASGI client driving the callback in the same test.  Binding
    the swap to the attribute the oauth module looks the name up on keeps it
    scoped, and ``__getattr__`` leaves ``httpx.TransportError`` -- which the
    helper's ``except`` clause needs to be the real class -- untouched.
    """

    def __init__(self, body: Any) -> None:
        self._body = body

    def __getattr__(self, name: str) -> Any:
        return getattr(httpx, name)

    def AsyncClient(self, *args: Any, **kwargs: Any) -> _FakeAsyncClient:  # noqa: N802
        return _FakeAsyncClient(self._body)


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(_MALFORMED), ids=list(_MALFORMED))
async def test_fetch_userinfo_rejects_malformed_body(case: str, monkeypatch) -> None:
    """A body the annotation does not describe raises the error callers catch.

    ``_UserinfoError`` specifically: the callers' ``except`` clause names only
    that, so any other exception type is one that escapes them.
    """
    monkeypatch.setattr(oauth_module, "httpx", _HttpxShim(_MALFORMED[case]))

    with pytest.raises(_UserinfoError) as excinfo:
        await oauth_module._fetch_google_userinfo(_ACCESS_TOKEN)

    assert _LEAK_MARKER not in str(excinfo.value), (
        "The rejection message reproduced a provider-supplied value. Only a "
        "type name may be interpolated into it."
    )


@pytest.mark.parametrize("case", list(_TOLERATED), ids=list(_TOLERATED))
async def test_fetch_userinfo_keeps_absent_fields_absent(case: str, monkeypatch) -> None:
    """An unset optional field is not a malformed body and must not be rejected.

    Google omits ``email`` when the scope was not granted, and the callbacks
    answer that by skipping account resolution and completing the flow. Turning
    it into a rejection would replace a working outcome with a 502.
    """
    body, (expected_email, expected_name) = _TOLERATED[case]
    monkeypatch.setattr(oauth_module, "httpx", _HttpxShim(body))

    result = await oauth_module._fetch_google_userinfo(_ACCESS_TOKEN)

    assert result.get("email") == expected_email
    assert result.get("name") == expected_name


async def test_fetch_userinfo_preserves_unvalidated_keys(monkeypatch) -> None:
    """Validation narrows the fields it knows about; it does not strip the body."""
    monkeypatch.setattr(oauth_module, "httpx", _HttpxShim(dict(_VALID_USERINFO)))

    result = await oauth_module._fetch_google_userinfo(_ACCESS_TOKEN)

    assert result == _VALID_USERINFO


async def test_fetch_userinfo_still_reports_a_non_200(monkeypatch) -> None:
    """The pre-existing HTTP-status rejection is unchanged by the type check."""

    class _NotOkClient(_FakeAsyncClient):
        async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
            response = _FakeResponse(None)
            response.status_code = 403
            return response

    shim = _HttpxShim(None)
    shim.AsyncClient = lambda *a, **k: _NotOkClient(None)  # type: ignore[method-assign]
    monkeypatch.setattr(oauth_module, "httpx", shim)

    with pytest.raises(_UserinfoError, match="403"):
        await oauth_module._fetch_google_userinfo(_ACCESS_TOKEN)


# ---------------------------------------------------------------------------
# Both callbacks, driven the way Google reaches them
# ---------------------------------------------------------------------------


class _Harness:
    """Every collaborator a Google callback touches except userinfo itself.

    ``_fetch_google_userinfo`` is deliberately left unpatched -- it is the code
    under test -- and reaches ``_HttpxShim`` instead.
    """

    def __init__(self, body: Any) -> None:
        self.body = body
        self.pool = MagicMock()
        self.create_account = AsyncMock(return_value=MagicMock(entity_id=uuid.uuid4()))
        self.get_account = AsyncMock(side_effect=GoogleAccountNotFoundError("absent"))
        self.update_refresh = AsyncMock()
        self.store_app_credentials = AsyncMock()
        self.store_google_credentials = AsyncMock()
        self.cred_store = AsyncMock()
        self.cred_store.store = AsyncMock()
        self.audit = AsyncMock()

    def patches(self):
        return (
            patch.object(oauth_module, "httpx", _HttpxShim(self.body)),
            patch(_RESOLVE_CREDS, AsyncMock(return_value=("client-id", "client-secret"))),
            patch(_EXCHANGE, AsyncMock(return_value=dict(_VALID_TOKEN_PAYLOAD))),
            patch(_GET_ACCOUNT, self.get_account),
            patch(_CREATE_ACCOUNT, self.create_account),
            patch(_UPDATE_REFRESH, self.update_refresh),
            patch(_STORE_APP_CREDS, self.store_app_credentials),
            patch(_STORE_GOOGLE_CREDS, self.store_google_credentials),
            patch(_CRED_STORE, return_value=self.cred_store),
            patch(_SHARED_POOL, return_value=self.pool),
            patch(_AUDIT, self.audit),
        )

    def assert_no_account_touched(self) -> None:
        self.create_account.assert_not_awaited()
        self.update_refresh.assert_not_awaited()

    def assert_nothing_persisted(self) -> None:
        self.assert_no_account_touched()
        self.store_app_credentials.assert_not_awaited()
        self.store_google_credentials.assert_not_awaited()
        self.cred_store.store.assert_not_awaited()


async def _call_site_one(harness: _Harness) -> httpx.Response:
    """Drive ``oauth_google_callback`` over HTTP, the way Google reaches it."""
    app = create_app()
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: MagicMock()
    state = _generate_state()
    _store_state(state)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        with ExitStack() as stack:
            for patcher in harness.patches():
                stack.enter_context(patcher)
            return await client.get(
                "/api/oauth/google/callback", params={"code": "4/synthetic", "state": state}
            )


async def _call_site_two(harness: _Harness) -> Any:
    """Drive ``_google_callback_from_state`` directly.

    The literal ``/google/callback`` route is registered before
    ``/{provider}/callback``, so this function is not reachable by URL for
    ``provider=google``; ``oauth_provider_callback`` delegates to it in process.
    """
    state_entry = _StateEntry(expiry=time.monotonic() + 300, provider="google")

    with ExitStack() as stack:
        for patcher in harness.patches():
            stack.enter_context(patcher)
        return await _google_callback_from_state(
            code="4/synthetic",
            state_entry=state_entry,
            db_manager=MagicMock(),
            page_of_origin=None,
        )


def _drive(site: str):
    return _call_site_one if site == "oauth_google_callback" else _call_site_two


def _body_of(response: Any) -> tuple[int, str]:
    """(status, rendered body) for either an httpx response or a raw Response."""
    if isinstance(response, httpx.Response):
        return response.status_code, response.text
    return response.status_code, response.body.decode("utf-8")


def _error_code(rendered: str) -> str:
    payload = json.loads(rendered)
    return payload.get("error_code") or payload["data"]["error_code"]


@pytest.mark.parametrize("case", list(_MALFORMED), ids=list(_MALFORMED))
@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_malformed_userinfo_is_a_structured_error_not_a_crash(case: str, site: str) -> None:
    """The callback answers 502 ``userinfo_failed`` instead of raising.

    Reaching this assertion at all is half the point: before the fix the
    ``top_level_*`` cases raised ``AttributeError`` out of the handler, so the
    call did not return a response to inspect.
    """
    harness = _Harness(_MALFORMED[case])

    response = await _drive(site)(harness)

    # What must not have happened leads, so a failure names the write rather
    # than the status code.
    harness.assert_nothing_persisted()

    status, rendered = _body_of(response)
    assert status == 502, rendered
    assert _error_code(rendered) == "userinfo_failed"
    assert _LEAK_MARKER not in rendered, (
        "A rejected userinfo value reached the response body. The error surface "
        "carries fixed local text only."
    )


@pytest.mark.parametrize("case", ["int_email", "list_email", "dict_email", "bool_email"])
@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_non_string_email_never_reaches_account_creation(case: str, site: str) -> None:
    """The serious half: a wrong-typed identity must not create an account.

    ``create_google_account(email=...)`` decides which account a credential
    belongs to. An ``int`` or a ``list`` arriving there is not a bad row, it is
    an account keyed on something that is not an address.
    """
    harness = _Harness(_MALFORMED[case])

    await _drive(site)(harness)

    harness.assert_no_account_touched()


@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_a_valid_userinfo_body_does_create_the_account(site: str) -> None:
    """The sibling that keeps ``assert_not_awaited`` above from being vacuous."""
    harness = _Harness(dict(_VALID_USERINFO))

    response = await _drive(site)(harness)

    harness.create_account.assert_awaited_once()
    assert harness.create_account.await_args.kwargs["email"] == _EMAIL
    assert harness.create_account.await_args.kwargs["display_name"] == _DISPLAY_NAME
    status, _ = _body_of(response)
    assert status in (302, 307), status


@pytest.mark.parametrize("case", ["email_absent", "email_null", "email_blank"])
@pytest.mark.parametrize("site", ["oauth_google_callback", "_google_callback_from_state"])
async def test_absent_email_still_completes_the_flow(case: str, site: str) -> None:
    """AC: a body missing ``email`` keeps the verdict it has today.

    No account can be resolved without an address, so the callback falls back to
    owner-entity credential storage and redirects. Rejecting it instead would
    break the not-granted-``email``-scope flow.
    """
    body, _ = _TOLERATED[case]
    harness = _Harness(body)

    response = await _drive(site)(harness)

    harness.assert_no_account_touched()
    harness.store_google_credentials.assert_awaited_once()
    status, _ = _body_of(response)
    assert status in (302, 307), status
