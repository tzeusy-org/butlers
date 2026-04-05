"""Tests for shared Google credential storage (butlers.google_credentials).

Covers:
- GoogleCredentials model validation
- Security: no secret material in repr/logs
"""

from __future__ import annotations

import pytest

from butlers.google_credentials import (
    GoogleCredentials,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_creds() -> GoogleCredentials:
    return GoogleCredentials(
        client_id="client-id-123.apps.googleusercontent.com",
        client_secret="super-secret-xyz",
        refresh_token="1//refresh-token-abc",
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )


# ---------------------------------------------------------------------------
# GoogleCredentials model
# ---------------------------------------------------------------------------


class TestGoogleCredentialsModel:
    def test_valid_credentials(self, fake_creds: GoogleCredentials) -> None:
        assert fake_creds.client_id == "client-id-123.apps.googleusercontent.com"
        assert fake_creds.client_secret == "super-secret-xyz"
        assert fake_creds.refresh_token == "1//refresh-token-abc"
        assert fake_creds.scope == "https://www.googleapis.com/auth/gmail.readonly"

    def test_scope_is_optional(self) -> None:
        creds = GoogleCredentials(client_id="id", client_secret="secret", refresh_token="token")
        assert creds.scope is None

    def test_strips_whitespace_from_required_fields(self) -> None:
        creds = GoogleCredentials(
            client_id="  id  ", client_secret="  secret  ", refresh_token="  token  "
        )
        assert creds.client_id == "id"
        assert creds.client_secret == "secret"
        assert creds.refresh_token == "token"

    def test_empty_client_id_raises(self) -> None:
        with pytest.raises(Exception):
            GoogleCredentials(client_id="", client_secret="s", refresh_token="r")

    def test_whitespace_only_client_id_raises(self) -> None:
        with pytest.raises(Exception):
            GoogleCredentials(client_id="   ", client_secret="s", refresh_token="r")

    def test_empty_client_secret_raises(self) -> None:
        with pytest.raises(Exception):
            GoogleCredentials(client_id="id", client_secret="", refresh_token="r")

    def test_empty_refresh_token_raises(self) -> None:
        with pytest.raises(Exception):
            GoogleCredentials(client_id="id", client_secret="s", refresh_token="")

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(Exception):
            GoogleCredentials(client_id="id", client_secret="s", refresh_token="r", unknown="x")

    def test_repr_does_not_leak_secret(self, fake_creds: GoogleCredentials) -> None:
        """client_secret and refresh_token must never appear in repr()."""
        r = repr(fake_creds)
        assert "super-secret-xyz" not in r
        assert "1//refresh-token-abc" not in r
        assert "REDACTED" in r
