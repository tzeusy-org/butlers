"""Unit tests for the credential-key normalisation utility.

Covers:
- normalize_credential_key: long-scope → canonical prefix form
- normalize_credential_key: short-alias passthrough
- normalize_credential_key: unknown scope raises ValueError
- normalize_key_param: canonical short-prefix form passthrough
- normalize_key_param: long-scope form normalised to canonical
- normalize_key_param: malformed key (no colon) raises ValueError
- normalize_key_param: unknown scope in raw key raises ValueError
"""

from __future__ import annotations

import pytest

from butlers.core.credential_keys import normalize_credential_key, normalize_key_param

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# normalize_credential_key — long-scope forms (spec §Normalisation roundtrip)
# ---------------------------------------------------------------------------


def test_user_scope_produces_u_prefix():
    assert normalize_credential_key("user", "google") == "u:google"


def test_system_scope_produces_s_prefix():
    assert normalize_credential_key("system", "BUTLER_TELEGRAM_TOKEN") == "s:BUTLER_TELEGRAM_TOKEN"


def test_cli_scope_produces_c_prefix():
    assert normalize_credential_key("cli", "claude") == "c:claude"


# ---------------------------------------------------------------------------
# normalize_credential_key — short-alias forms (convenience round-trips)
# ---------------------------------------------------------------------------


def test_short_u_alias_passthrough():
    assert normalize_credential_key("u", "google") == "u:google"


def test_short_s_alias_passthrough():
    assert normalize_credential_key("s", "BUTLER_TELEGRAM_TOKEN") == "s:BUTLER_TELEGRAM_TOKEN"


def test_short_c_alias_passthrough():
    assert normalize_credential_key("c", "claude") == "c:claude"


# ---------------------------------------------------------------------------
# normalize_credential_key — key name is passed verbatim (no case transform)
# ---------------------------------------------------------------------------


def test_key_name_case_preserved():
    """System secret names are uppercase; we must not lowercase them."""
    result = normalize_credential_key("system", "MY_API_KEY")
    assert result == "s:MY_API_KEY"


def test_user_key_name_case_preserved():
    result = normalize_credential_key("user", "Google")
    assert result == "u:Google"


# ---------------------------------------------------------------------------
# normalize_credential_key — unknown scope raises ValueError
# ---------------------------------------------------------------------------


def test_unknown_scope_raises_value_error():
    with pytest.raises(ValueError, match="Unknown credential scope"):
        normalize_credential_key("admin", "foo")


def test_empty_scope_raises_value_error():
    with pytest.raises(ValueError, match="Unknown credential scope"):
        normalize_credential_key("", "foo")


# ---------------------------------------------------------------------------
# normalize_key_param — canonical short-prefix form passthrough
# ---------------------------------------------------------------------------


def test_normalize_key_param_canonical_u():
    assert normalize_key_param("u:google") == "u:google"


def test_normalize_key_param_canonical_s():
    assert normalize_key_param("s:BUTLER_TELEGRAM_TOKEN") == "s:BUTLER_TELEGRAM_TOKEN"


def test_normalize_key_param_canonical_c():
    assert normalize_key_param("c:claude") == "c:claude"


# ---------------------------------------------------------------------------
# normalize_key_param — long-scope form is normalised to canonical
# ---------------------------------------------------------------------------


def test_normalize_key_param_long_user_scope():
    assert normalize_key_param("user:google") == "u:google"


def test_normalize_key_param_long_system_scope():
    assert normalize_key_param("system:BUTLER_TELEGRAM_TOKEN") == "s:BUTLER_TELEGRAM_TOKEN"


def test_normalize_key_param_long_cli_scope():
    assert normalize_key_param("cli:claude") == "c:claude"


# ---------------------------------------------------------------------------
# normalize_key_param — malformed input raises ValueError
# ---------------------------------------------------------------------------


def test_normalize_key_param_no_colon_raises():
    with pytest.raises(ValueError, match="Invalid credential-key format"):
        normalize_key_param("ugoogle")


def test_normalize_key_param_empty_raises():
    with pytest.raises(ValueError, match="Invalid credential-key format"):
        normalize_key_param("")


def test_normalize_key_param_unknown_scope_raises():
    with pytest.raises(ValueError, match="Unknown credential scope"):
        normalize_key_param("admin:foo")
