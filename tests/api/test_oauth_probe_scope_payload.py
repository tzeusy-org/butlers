"""The OAuth status probe answers a malformed ``scope`` with a degraded status.

``_probe_google_token`` reads ``token_data.get("scope")`` behind ``if
granted_scope_str is None:`` and then calls ``.split()`` on it.  An ``is None``
comparison is not a type check: a provider-supplied ``list``, ``int``, ``dict``,
or ``bool`` is not ``None``, so it passed the guard and raised ``AttributeError``
inside a read-only status endpoint -- a 500 where the caller asked "is this
credential healthy?" and deserved a structured answer.

Scope is deliberately narrow.  This site persists nothing, mints no credential,
and formats no ``Authorization`` header; the defect is robustness and
observability, not credential integrity.  So the tests below pin *both* edges:
the malformed shapes must degrade, and the shapes that already had a correct
verdict -- absent scope, ``null`` scope, blank scope, a real scope string --
must keep exactly the verdict they had.

All scope strings here are synthetic; the required-scope URLs are the public
Google API scope identifiers the module already hardcodes, not credentials.

[bu-22yl5]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from butlers.api.models.oauth import OAuthCredentialState, OAuthCredentialStatus
from butlers.api.routers import oauth as oauth_module
from butlers.api.routers.oauth import _REQUIRED_SCOPES, _probe_google_token

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# --- Synthetic material.  None of this is, or resembles, a real credential. ---
_CLIENT_ID = "synthetic-client-id.apps.example.invalid"
_CLIENT_SECRET = "synthetic-client-secret-not-real"
_REFRESH_TOKEN = "synthetic-refresh-token-not-real"
_ACCESS_TOKEN = "synthetic-access-token-not-real"

_ALL_REQUIRED = " ".join(sorted(_REQUIRED_SCOPES))

#: A marker embedded in every malformed scope so a redaction assertion has
#: something concrete to look for in the response the probe hands back.
_MARKER = "synthetic-scope-marker-do-not-echo"

#: Non-string ``scope`` values a token endpoint can return with a 200.  Every
#: one of them is ``is not None``, so each sailed past the old guard and hit
#: ``.split()``.
_NON_STRING_SCOPES: dict[str, Any] = {
    "list": [_MARKER, "scope-b"],
    "int": 12345,
    "dict": {"granted": _MARKER},
    "bool": True,
    "float": 1.5,
    "nested_list": [[_MARKER]],
}


class _FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeAsyncClient:
    """Stands in for ``httpx.AsyncClient`` so the probe never leaves the process."""

    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self._payload = payload
        self._status_code = status_code

    def __call__(self, *args: Any, **kwargs: Any) -> _FakeAsyncClient:
        return self

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def post(self, *args: Any, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._payload, status_code=self._status_code)


async def _probe(payload: Any) -> OAuthCredentialStatus:
    """Run the probe against a token endpoint that answers 200 with ``payload``."""
    client = _FakeAsyncClient(payload)
    with patch.object(oauth_module.httpx, "AsyncClient", client):
        return await _probe_google_token(
            client_id=_CLIENT_ID,
            client_secret=_CLIENT_SECRET,
            refresh_token=_REFRESH_TOKEN,
        )


# ---------------------------------------------------------------------------
# The bug: a non-string scope must degrade, not raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", sorted(_NON_STRING_SCOPES))
async def test_non_string_scope_yields_degraded_status(shape: str) -> None:
    status = await _probe({"access_token": _ACCESS_TOKEN, "scope": _NON_STRING_SCOPES[shape]})

    assert isinstance(status, OAuthCredentialStatus)
    assert status.state is OAuthCredentialState.unknown_error
    assert status.connected is False
    assert status.remediation
    assert status.scopes_granted is None


@pytest.mark.parametrize("shape", sorted(_NON_STRING_SCOPES))
async def test_non_string_scope_does_not_echo_the_payload(shape: str) -> None:
    """The degraded answer names the shape, never the provider-supplied value."""
    status = await _probe({"access_token": _ACCESS_TOKEN, "scope": _NON_STRING_SCOPES[shape]})

    rendered = f"{status.remediation} {status.detail}"
    assert _MARKER not in rendered


async def test_non_dict_token_payload_yields_degraded_status() -> None:
    """A 200 whose body is not a JSON object cannot even be ``.get``-ed."""
    status = await _probe([{"scope": _ALL_REQUIRED}])

    assert status.state is OAuthCredentialState.unknown_error
    assert status.connected is False
    assert status.scopes_granted is None


# ---------------------------------------------------------------------------
# The branches that were already right, pinned so the fix cannot swallow them
# ---------------------------------------------------------------------------


async def test_absent_scope_is_still_treated_as_connected() -> None:
    """Google omits ``scope`` when scopes are unchanged; that is not a failure."""
    status = await _probe({"access_token": _ACCESS_TOKEN, "expires_in": 3600})

    assert status.state is OAuthCredentialState.connected
    assert status.connected is True
    assert status.scopes_granted is None
    assert status.remediation is None
    assert status.detail is None


async def test_null_scope_is_still_treated_as_connected() -> None:
    """An explicit JSON ``null`` reached the absent branch before, and still must."""
    status = await _probe({"access_token": _ACCESS_TOKEN, "scope": None})

    assert status.state is OAuthCredentialState.connected
    assert status.scopes_granted is None
    assert status.remediation is None
    assert status.detail is None


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
async def test_blank_scope_still_reports_missing_scope(blank: str) -> None:
    """A blank string is a real answer -- no scopes granted -- not a malformed one."""
    status = await _probe({"access_token": _ACCESS_TOKEN, "scope": blank})

    assert status.state is OAuthCredentialState.missing_scope
    assert status.scopes_granted == []
    assert status.remediation


async def test_full_scope_string_reports_connected() -> None:
    status = await _probe({"access_token": _ACCESS_TOKEN, "scope": _ALL_REQUIRED})

    assert status.state is OAuthCredentialState.connected
    assert set(status.scopes_granted or []) == set(_REQUIRED_SCOPES)


async def test_partial_scope_string_reports_missing_scope() -> None:
    one_required = sorted(_REQUIRED_SCOPES)[0]
    status = await _probe({"access_token": _ACCESS_TOKEN, "scope": one_required})

    assert status.state is OAuthCredentialState.missing_scope
    assert status.scopes_granted == [one_required]
    assert status.detail and sorted(_REQUIRED_SCOPES)[1] in status.detail
