"""The generic provider callback records what its profile fetch actually did.

``oauth_provider_callback``'s non-Google path resolves an account identity with

    profile_data = profile_resp.json()
    account_email = profile_data.get("email") or profile_data.get("id")

inside a bare ``except Exception:  # noqa: BLE001`` whose only handler is a
``logger.debug``.  Two things fall through that:

* a body that is not a JSON object makes ``.get`` raise ``AttributeError``.
  The bare ``except`` catches it, so the callback completes and the audit row
  it writes says ``"OAuth dance complete"`` -- the same words it writes when
  there was no profile endpoint to call.  A contract violation and a
  never-attempted fetch become indistinguishable after the fact.
* a dict body whose ``email`` or ``id`` is present but not a string passes the
  ``or`` chain because it is truthy, and lands in the ``connected`` audit note
  as ``account=<not-an-address>``.

The blast radius is small and these tests do not pretend otherwise: the value
reaches a ``logger.info`` and the audit note and stops there.  It is not
persisted and it does not reach account creation, unlike the Google path.  What
is wrong is the *telemetry*: an audit note asserts an account identity the code
never established, and a failed profile resolution leaves no trace at all.

So every assertion here is about the recorded outcome -- the audit note and the
log line -- plus the two things that must not change: the profile fetch stays
non-fatal, and the credential writes it precedes still happen.

``oauth.httpx`` is swapped module-locally so the callback runs its own request,
its own status check, and its own parse over a canned body, rather than having
the resolution patched out.  All material here is synthetic and built in this
file; the redaction assertions check for the *absence* of a marker value and
never reproduce one.
"""

from __future__ import annotations

import json
import logging
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers import oauth as oauth_module
from butlers.api.routers.oauth import _clear_state_store, _generate_state, _store_state

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_OAUTH_LOGGER = "butlers.api.routers.oauth"

# --- Synthetic material.  None of this is, or resembles, a real credential. ---
_ACCESS_TOKEN = "synthetic-generic-access-token"
_REFRESH_TOKEN = "synthetic-generic-refresh-token"
_EMAIL = "generic-profile@example.invalid"
_ACCOUNT_ID = "synthetic-provider-account-0001"

#: Embedded in every profile value that can carry a string, so the redaction
#: assertions have something specific to look for.  A body that is refused must
#: not put this in the audit note or in a log line.
_LEAK_MARKER = "profile-leak-marker-7c31"

_TOKEN_PAYLOAD: dict[str, Any] = {
    "access_token": _ACCESS_TOKEN,
    "refresh_token": _REFRESH_TOKEN,
    "scope": "user-read-email user-read-private",
    "token_type": "Bearer",
    "expires_in": 3600,
}

#: Note text for a resolution that produced an identity.  Unchanged behaviour,
#: pinned so the fix cannot quietly drop the identity it does establish.
_RESOLVED_NOTE = "OAuth dance complete (account={account})"

#: Note text for a resolution that was attempted and produced nothing.  The
#: reason is one of a small closed set of locally-authored tokens -- never a
#: provider-supplied value, never an HTTP body, never a status line.
_UNRESOLVED_NOTE = "OAuth dance complete (account unresolved: {reason})"

#: Note text when no profile resolution was attempted at all.  This is the
#: string the malformed cases wrongly share today, and the whole point of the
#: change is that they stop sharing it.
_NOT_ATTEMPTED_NOTE = "OAuth dance complete"

#: 200 bodies that cannot yield an account identity.  ``top_level_*`` is the
#: ``AttributeError`` half the bare ``except`` swallowed; the field cases are
#: the half that reached the audit note as a truthy non-string.
_MALFORMED_BODIES: dict[str, Any] = {
    "top_level_array": [{"email": f"{_LEAK_MARKER}@example.invalid"}],
    "top_level_empty_array": [],
    "top_level_int": 42,
    "top_level_string": _LEAK_MARKER,
    "top_level_null": None,
    "top_level_bool": True,
    "int_email": {"email": 12345},
    "list_email": {"email": [f"{_LEAK_MARKER}@example.invalid"]},
    "dict_email": {"email": {"address": f"{_LEAK_MARKER}@example.invalid"}},
    "bool_email": {"email": True},
    "int_id": {"id": 987654},
    "list_id": {"id": [_LEAK_MARKER]},
    "dict_id": {"id": {"value": _LEAK_MARKER}},
    "bool_id": {"id": False, "display_name": "unused"},
    "null_email_int_id": {"email": None, "id": 4321},
}

#: Well-formed 200 bodies that simply carry no identity.  The provider answered
#: correctly; it just did not grant an address or an id.  These must record a
#: distinct reason from the malformed ones -- collapsing them would swap one
#: kind of dishonest telemetry for another.
_IDENTITY_ABSENT_BODIES: dict[str, Any] = {
    "empty_object": {},
    "unrelated_fields_only": {"display_name": "Someone", "country": "SG"},
    "email_null": {"email": None},
    "email_blank": {"email": ""},
    "both_null": {"email": None, "id": None},
    "both_blank": {"email": "", "id": ""},
    "email_and_id_whitespace": {"email": "   ", "id": "  "},
}

#: Bodies that must still resolve, mapped to the identity they must resolve to.
#: These keep the rejection assertions from being satisfiable by a fix that
#: rejects everything.
_RESOLVED_BODIES: dict[str, tuple[dict[str, Any], str]] = {
    "email_only": ({"email": _EMAIL}, _EMAIL),
    "email_preferred_over_id": ({"email": _EMAIL, "id": _ACCOUNT_ID}, _EMAIL),
    "id_when_email_absent": ({"id": _ACCOUNT_ID}, _ACCOUNT_ID),
    "id_when_email_null": ({"email": None, "id": _ACCOUNT_ID}, _ACCOUNT_ID),
    "id_when_email_blank": ({"email": "", "id": _ACCOUNT_ID}, _ACCOUNT_ID),
    "id_when_email_whitespace": ({"email": "   ", "id": _ACCOUNT_ID}, _ACCOUNT_ID),
    "padded_email": ({"email": f"  {_EMAIL}  "}, _EMAIL),
    "padded_id": ({"id": f"\t{_ACCOUNT_ID}\n"}, _ACCOUNT_ID),
}

_RESOLVE_PROVIDER_CREDS = "butlers.api.routers.oauth._resolve_provider_credentials"
_EXCHANGE = "butlers.api.routers.oauth._exchange_code_for_tokens"
_CRED_STORE = "butlers.api.routers.oauth._make_credential_store"
_SHARED_POOL = "butlers.api.routers.oauth._get_shared_pool"
_AUDIT = "butlers.api.routers.oauth._emit_oauth_audit"


@pytest.fixture(autouse=True)
def clear_states():
    _clear_state_store()
    yield
    _clear_state_store()


@pytest.fixture(autouse=True)
def stub_off(monkeypatch):
    """The OAuth stub short-circuits the profile fetch before it parses anything."""
    monkeypatch.delenv("TEST_MODE_OAUTH_STUB", raising=False)
    monkeypatch.delenv("ENV", raising=False)


# ---------------------------------------------------------------------------
# A canned profile response, delivered through the real callback
# ---------------------------------------------------------------------------


class _FakeResponse:
    """A profile response with a settable status and a controllable parse."""

    def __init__(self, body: Any, *, status_code: int = 200, parse_error: bool = False) -> None:
        self._body = body
        self.status_code = status_code
        self._parse_error = parse_error

    def json(self) -> Any:
        if self._parse_error:
            raise json.JSONDecodeError("Expecting value", _LEAK_MARKER, 0)
        return self._body


class _FakeAsyncClient:
    def __init__(self, responder) -> None:
        self._responder = responder

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        return self._responder()


class _HttpxShim:
    """``oauth.httpx`` with only ``AsyncClient`` swapped for a canned responder.

    Patching ``httpx.AsyncClient`` itself would mutate the shared module object
    and break the ASGI client driving the callback in the same test.  Binding
    the swap to the attribute the oauth module looks the name up on keeps it
    scoped, and ``__getattr__`` leaves the real exception classes -- which the
    callback's ``except`` clauses need to be the genuine article -- untouched.
    """

    def __init__(self, responder) -> None:
        self._responder = responder

    def __getattr__(self, name: str) -> Any:
        return getattr(httpx, name)

    def AsyncClient(self, *args: Any, **kwargs: Any) -> _FakeAsyncClient:  # noqa: N802
        return _FakeAsyncClient(self._responder)


def _ok(body: Any):
    return lambda: _FakeResponse(body)


def _status(code: int):
    return lambda: _FakeResponse({"email": f"{_LEAK_MARKER}@example.invalid"}, status_code=code)


def _unparseable():
    return lambda: _FakeResponse(None, parse_error=True)


def _transport_error():
    def _raise() -> _FakeResponse:
        raise httpx.ConnectError(f"connection refused for {_LEAK_MARKER}")

    return _raise


def _unexpected_error():
    """Something outside the httpx / JSON vocabulary the callback can name.

    ``httpx.InvalidURL`` is a real member of this class: it derives from
    ``Exception``, not ``httpx.HTTPError``.  A stand-in is used here because the
    point is the shape of the failure, not its identity.
    """

    def _raise() -> _FakeResponse:
        raise RuntimeError(f"unexpected failure involving {_LEAK_MARKER}")

    return _raise


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _Harness:
    """Every collaborator the generic callback touches except the profile fetch.

    The profile fetch is deliberately left unpatched -- it is the code under
    test -- and reaches ``_HttpxShim`` instead.
    """

    def __init__(self, responder) -> None:
        self.responder = responder
        self.pool = MagicMock()
        self.cred_store = AsyncMock()
        self.cred_store.store = AsyncMock()
        self.audit = AsyncMock()

    def patches(self):
        return (
            patch.object(oauth_module, "httpx", _HttpxShim(self.responder)),
            patch(_RESOLVE_PROVIDER_CREDS, AsyncMock(return_value=("client-id", "client-secret"))),
            patch(_EXCHANGE, AsyncMock(return_value=dict(_TOKEN_PAYLOAD))),
            patch(_CRED_STORE, return_value=self.cred_store),
            patch(_SHARED_POOL, return_value=self.pool),
            patch(_AUDIT, self.audit),
        )

    def connected_note(self) -> str | None:
        """The note on the single ``connected`` audit row, or fail loudly."""
        connected = [
            call for call in self.audit.await_args_list if call.kwargs.get("action") == "connected"
        ]
        assert len(connected) == 1, (
            f"expected exactly one 'connected' audit row, got {len(connected)}"
        )
        return connected[0].kwargs.get("note")

    def stored_keys(self) -> set[str]:
        return {call.args[0] for call in self.cred_store.store.await_args_list}


async def _drive(harness: _Harness) -> httpx.Response:
    """Drive ``oauth_provider_callback`` over HTTP for a non-Google provider."""
    app = create_app()
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: MagicMock()
    state = _generate_state()
    _store_state(state, provider="spotify")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        with ExitStack() as stack:
            for patcher in harness.patches():
                stack.enter_context(patcher)
            return await client.get(
                "/api/oauth/spotify/callback",
                params={"code": "synthetic-auth-code", "state": state},
            )


def _log_text(caplog) -> str:
    """Everything the log emitted, formatted -- ``exc_info`` tracebacks included.

    ``record.getMessage()`` alone would miss a value carried in an attached
    exception, which is exactly where an unclassified failure would put one, so
    the leak assertions would be checking something they cannot see.
    """
    return caplog.text


# ---------------------------------------------------------------------------
# The malformed half: a body the ``.get`` chain cannot honestly read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(_MALFORMED_BODIES), ids=list(_MALFORMED_BODIES))
async def test_malformed_profile_body_is_recorded_in_the_audit_note(case: str) -> None:
    """AC: a non-dict profile body produces a recorded outcome rather than silence.

    ``"OAuth dance complete"`` is what this callback writes when there was no
    profile endpoint to call.  A profile fetch that returned a body the code
    could not read must not be filed under the same words.
    """
    harness = _Harness(_ok(_MALFORMED_BODIES[case]))

    await _drive(harness)

    note = harness.connected_note()
    assert note == _UNRESOLVED_NOTE.format(reason="profile_malformed"), note


@pytest.mark.parametrize("case", list(_MALFORMED_BODIES), ids=list(_MALFORMED_BODIES))
async def test_malformed_profile_body_never_supplies_an_account_identity(case: str) -> None:
    """AC: a non-string email/id never reaches the audit note as an account identity.

    The truthiness of ``12345`` or ``["a@b"]`` is the whole bug: it satisfies the
    ``or`` chain and the ``if account_email`` guard, so the note claims an
    account the code never established.
    """
    harness = _Harness(_ok(_MALFORMED_BODIES[case]))

    await _drive(harness)

    note = harness.connected_note()
    assert note is not None
    assert "account=" not in note, (
        "The audit note asserted an account identity for a profile body that "
        f"could not supply one: {note!r}"
    )


@pytest.mark.parametrize("case", list(_MALFORMED_BODIES), ids=list(_MALFORMED_BODIES))
async def test_malformed_profile_body_leaks_nothing_into_the_record(case: str, caplog) -> None:
    """The recorded outcome carries fixed local wording and a type name only."""
    harness = _Harness(_ok(_MALFORMED_BODIES[case]))

    with caplog.at_level(logging.DEBUG, logger=_OAUTH_LOGGER):
        await _drive(harness)

    note = harness.connected_note()
    assert note is not None
    assert _LEAK_MARKER not in note, f"a provider-supplied value reached the audit note: {note!r}"
    assert _LEAK_MARKER not in _log_text(caplog), "a provider-supplied value reached a log line"


@pytest.mark.parametrize("case", list(_MALFORMED_BODIES), ids=list(_MALFORMED_BODIES))
async def test_malformed_profile_body_is_logged_above_debug(case: str, caplog) -> None:
    """Silence is the defect.  A swallowed contract violation gets a WARNING.

    ``logger.debug`` is off in every deployment this runs in, so the pre-change
    handler recorded nothing anywhere a reader would look.
    """
    harness = _Harness(_ok(_MALFORMED_BODIES[case]))

    with caplog.at_level(logging.DEBUG, logger=_OAUTH_LOGGER):
        await _drive(harness)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a profile body the callback could not read was never logged above DEBUG"


@pytest.mark.parametrize("case", list(_MALFORMED_BODIES), ids=list(_MALFORMED_BODIES))
async def test_malformed_profile_body_stays_non_fatal(case: str) -> None:
    """Profile resolution is best-effort and must remain so.

    The credential the user just authorized is the point of the flow; an
    unreadable profile body must not cost them it.  Pinned here so the fix
    cannot buy honesty by turning a swallowed error into a 502.
    """
    harness = _Harness(_ok(_MALFORMED_BODIES[case]))

    response = await _drive(harness)

    assert response.status_code in (302, 307), response.text
    assert "SPOTIFY_ACCESS_TOKEN" in harness.stored_keys()
    assert "SPOTIFY_REFRESH_TOKEN" in harness.stored_keys()


# ---------------------------------------------------------------------------
# The transport / status / parse half the same ``except`` also swallowed
# ---------------------------------------------------------------------------


async def test_unreachable_profile_endpoint_is_recorded() -> None:
    """A transport failure is a distinct outcome from a body we could not read."""
    harness = _Harness(_transport_error())

    await _drive(harness)

    note = harness.connected_note()
    assert note == _UNRESOLVED_NOTE.format(reason="profile_unreachable"), note


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500, 503])
async def test_non_200_profile_response_is_recorded(code: int) -> None:
    """The pre-change code checked ``== 200`` and then did nothing at all else.

    A 401 from the profile endpoint means the token we just persisted may not
    carry the scope we think it does.  That is worth a row, not a shrug.
    """
    harness = _Harness(_status(code))

    await _drive(harness)

    note = harness.connected_note()
    assert note == _UNRESOLVED_NOTE.format(reason="profile_http_error"), note


async def test_unparseable_profile_body_is_recorded() -> None:
    """A 200 that is not JSON at all is its own outcome, not a malformed object."""
    harness = _Harness(_unparseable())

    await _drive(harness)

    note = harness.connected_note()
    assert note == _UNRESOLVED_NOTE.format(reason="profile_unparseable"), note


async def test_unexpected_profile_failure_is_recorded_not_swallowed() -> None:
    """A failure the callback cannot name is the case the bare ``except`` was for.

    Keeping a catch-all here is deliberate -- see the resolver's docstring; the
    authorization code is already spent by this point, so letting an optional
    identity lookup escape would cost the user the credential they just granted
    and force a full re-consent.  What is not acceptable is that the catch-all
    leaves no trace, so it gets its own reason rather than the bare note.
    """
    harness = _Harness(_unexpected_error())

    await _drive(harness)

    note = harness.connected_note()
    assert note == _UNRESOLVED_NOTE.format(reason="profile_unexpected_error"), note


@pytest.mark.parametrize(
    "responder_factory",
    [_transport_error, _unparseable, _unexpected_error],
    ids=["transport_error", "unparseable", "unexpected_error"],
)
async def test_transport_and_parse_failures_leak_nothing(responder_factory, caplog) -> None:
    """Exception text from the transport layer is not fit for a record either."""
    harness = _Harness(responder_factory())

    with caplog.at_level(logging.DEBUG, logger=_OAUTH_LOGGER):
        await _drive(harness)

    note = harness.connected_note()
    assert note is not None
    assert _LEAK_MARKER not in note, f"a raw exception value reached the audit note: {note!r}"
    assert _LEAK_MARKER not in _log_text(caplog), "a raw exception value reached a log line"


@pytest.mark.parametrize(
    "responder_factory",
    [_transport_error, _unparseable, _unexpected_error],
    ids=["transport_error", "unparseable", "unexpected_error"],
)
async def test_transport_and_parse_failures_are_logged_above_debug(
    responder_factory, caplog
) -> None:
    """Same silence complaint as the malformed-body case, same remedy."""
    harness = _Harness(responder_factory())

    with caplog.at_level(logging.DEBUG, logger=_OAUTH_LOGGER):
        await _drive(harness)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a failed profile fetch was never logged above DEBUG"


@pytest.mark.parametrize(
    "responder_factory",
    [_transport_error, _unparseable, _unexpected_error],
    ids=["transport_error", "unparseable", "unexpected_error"],
)
async def test_transport_and_parse_failures_stay_non_fatal(responder_factory) -> None:
    """Same non-fatality guarantee as the malformed-body case."""
    harness = _Harness(responder_factory())

    response = await _drive(harness)

    assert response.status_code in (302, 307), response.text
    assert "SPOTIFY_ACCESS_TOKEN" in harness.stored_keys()


# ---------------------------------------------------------------------------
# Well-formed but identity-free: correct provider behaviour, distinct reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(_IDENTITY_ABSENT_BODIES), ids=list(_IDENTITY_ABSENT_BODIES))
async def test_identity_absent_profile_is_recorded_distinctly(case: str) -> None:
    """A provider that grants no address is not a provider that answered badly.

    Both end with no identity, but only one of them says something is wrong.
    Recording them under one reason would leave the audit trail unable to
    distinguish a scope the user declined from a contract the provider broke.
    """
    harness = _Harness(_ok(_IDENTITY_ABSENT_BODIES[case]))

    await _drive(harness)

    note = harness.connected_note()
    assert note == _UNRESOLVED_NOTE.format(reason="profile_has_no_identity"), note


@pytest.mark.parametrize("case", list(_IDENTITY_ABSENT_BODIES), ids=list(_IDENTITY_ABSENT_BODIES))
async def test_identity_absent_profile_stays_non_fatal(case: str) -> None:
    """The flow completes and the credential is stored, exactly as it does today."""
    harness = _Harness(_ok(_IDENTITY_ABSENT_BODIES[case]))

    response = await _drive(harness)

    assert response.status_code in (302, 307), response.text
    assert "SPOTIFY_ACCESS_TOKEN" in harness.stored_keys()


# ---------------------------------------------------------------------------
# The sibling that keeps every rejection above from being vacuous
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(_RESOLVED_BODIES), ids=list(_RESOLVED_BODIES))
async def test_a_usable_profile_still_reaches_the_audit_note(case: str) -> None:
    """A string identity is the case the note was always right about.

    Including ``email`` losing to a blank string and falling through to ``id``:
    that is the behaviour of the ``or`` chain and it must survive.
    """
    body, expected = _RESOLVED_BODIES[case]
    harness = _Harness(_ok(body))

    response = await _drive(harness)

    assert harness.connected_note() == _RESOLVED_NOTE.format(account=expected)
    assert response.status_code in (302, 307), response.text


async def test_no_profile_endpoint_keeps_the_bare_note() -> None:
    """AC boundary: never-attempted is a third outcome and keeps its own words.

    Google is the only registered provider with no ``profile_url``, and it takes
    a different code path, so this drives the generic path with the attribute
    cleared instead of inventing a provider.
    """
    harness = _Harness(_ok({"email": _EMAIL}))
    cfg = oauth_module._PROVIDER_REGISTRY["spotify"]

    with patch.object(cfg, "profile_url", None):
        response = await _drive(harness)

    assert harness.connected_note() == _NOT_ATTEMPTED_NOTE
    assert response.status_code in (302, 307), response.text
