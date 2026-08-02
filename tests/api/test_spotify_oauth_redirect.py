"""Post-callback redirect target for the Spotify connector OAuth flow.

Spotify authorizes through the connector PKCE dance
(``/api/connectors/spotify/oauth/*`` — client_id only, no client secret), which
is the flow the registered Spotify app's redirect URIs point at and the one the
passport's Spotify drawer drives. Its callback used to land on the dashboard
*root* tagged ``?spotify_connected=1``, a param no frontend code ever read — so
authorizing from the Spotify credential card returned the user somewhere else
with no confirmation.

It now returns to that card using the same params the generalized OAuth callback
emits (``?focus=u:spotify`` plus ``toast=connected`` / ``oauth_error=<code>``),
which SecretsPage already surfaces as a toast and strips from the URL.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from butlers.api.routers.spotify import _secrets_redirect

pytestmark = pytest.mark.unit


def _parts(url: str) -> tuple[str, str, dict[str, list[str]]]:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}", parsed.path, parse_qs(parsed.query)


def test_success_redirect_targets_the_spotify_credential_card():
    url = _secrets_redirect("https://host.example/butlers-dev", toast="connected")

    origin, path, query = _parts(url)
    assert origin == "https://host.example"
    assert path == "/butlers-dev/secrets"
    assert query["focus"] == ["u:spotify"]
    assert query["toast"] == ["connected"]


def test_error_redirect_uses_the_shared_oauth_error_param():
    # SecretsPage keys its amber toast off `oauth_error`; the old
    # `spotify_error` param was read by nothing.
    url = _secrets_redirect("https://host.example/butlers-dev", oauth_error="access_denied")

    _, path, query = _parts(url)
    assert path == "/butlers-dev/secrets"
    assert query["oauth_error"] == ["access_denied"]
    assert "spotify_error" not in query
    assert "toast" not in query


def test_redirect_works_on_a_root_mounted_dashboard():
    url = _secrets_redirect("http://localhost:41200", toast="connected")

    origin, path, query = _parts(url)
    assert origin == "http://localhost:41200"
    assert path == "/secrets"
    assert query["focus"] == ["u:spotify"]


def test_existing_base_url_query_is_merged_not_concatenated():
    url = _secrets_redirect("https://host.example/butlers-dev?theme=dark", toast="connected")

    _, path, query = _parts(url)
    assert path == "/butlers-dev/secrets"
    assert query["theme"] == ["dark"]
    assert query["toast"] == ["connected"]


def test_error_code_is_url_encoded():
    # The provider controls this value; it must not be able to inject params.
    url = _secrets_redirect("https://host.example", oauth_error="bad&toast=connected")

    _, _, query = _parts(url)
    assert query["oauth_error"] == ["bad&toast=connected"]
    assert "toast" not in query
