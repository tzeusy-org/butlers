"""Doc-code drift checks for key doctrine claims (bu-dl98i.4.6).

These tests guard a small set of high-value doc/code alignment claims so that
doctrine documents and the code they describe cannot silently diverge.

Scope and non-overlap
---------------------
- These tests do NOT duplicate ``test_credential_tier_resolution.py``, which
  validates CredentialStore API surface and callable availability.
- These tests do NOT duplicate ``test_no_reveal_route_contract.py`` (bu-dl98i.1.2),
  which validates that no legacy raw-secret reveal route is mounted.

This file checks three orthogonal invariants:

1. ``pyproject.toml`` description does not contain the word "framework".
   Doctrine: ``about/heart-and-soul/vision.md`` — "Not a framework for building
   other products. Butlers is the product."

2. The Tier 1 table name used in ``credential_store.py`` matches the name
   documented in ``about/heart-and-soul/security.md``.
   Doctrine: "Tier 1: System (butler_secrets)" — the table is named
   ``butler_secrets`` in the code.

3. ``CredentialStore.resolve()`` has ``env_fallback`` disabled by default.
   Doctrine: security.md — "only reads os.environ when env_fallback=True is
   explicitly passed (disabled by default)."
   Note: ``test_credential_tier_resolution.py`` checks that the parameter
   *exists*; this test checks that its *default value is False*.
"""

from __future__ import annotations

import inspect
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

# ---------------------------------------------------------------------------
# Locate the repository root from this test file's position.
# tests/contracts/test_doctrine_drift.py -> tests/ -> repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Invariant 1: pyproject.toml description must not claim Butlers is a "framework"
# ---------------------------------------------------------------------------


def test_pyproject_description_not_framework():
    """pyproject.toml [project].description must not contain the word 'framework'.

    Doctrine: about/heart-and-soul/vision.md — "Not a framework for building
    other products. Butlers is the product. It is not a library, not a toolkit,
    not a platform for third-party developers."

    This check fails if the description is reverted to wording like
    "AI agent framework" after the correction in PR #2446.
    """
    pyproject_path = _REPO_ROOT / "pyproject.toml"
    assert pyproject_path.exists(), f"pyproject.toml not found at {pyproject_path}"

    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)

    description: str = data["project"]["description"]
    assert "framework" not in description.lower(), (
        f"pyproject.toml [project].description must not use the word 'framework' "
        f"(doctrine: vision.md 'Not a framework for building other products'). "
        f"Got: {description!r}"
    )


# ---------------------------------------------------------------------------
# Invariant 2: Tier 1 table name in credential_store.py matches security.md
# ---------------------------------------------------------------------------


def test_tier1_credential_table_name_matches_doc():
    """The Tier 1 storage identifier in credential_store.py must be 'butler_secrets'.

    Doctrine: about/heart-and-soul/security.md — "Tier 1: System (butler_secrets)".
    The module-level ``_TABLE`` constant is the single source of truth for the
    physical table name.  If it drifts from the documented name, every connector
    and operator guide that says 'butler_secrets' becomes misleading.
    """
    from butlers import credential_store

    assert hasattr(credential_store, "_TABLE"), (
        "credential_store._TABLE not found; the module-level table name constant "
        "has been renamed or removed (security.md claims Tier 1 table is 'butler_secrets')"
    )
    assert credential_store._TABLE == "butler_secrets", (
        f"security.md documents Tier 1 table as 'butler_secrets', "
        f"but credential_store._TABLE is {credential_store._TABLE!r}. "
        f"Update the doc or the constant so they agree."
    )


# ---------------------------------------------------------------------------
# Invariant 3: env_fallback defaults to False in CredentialStore.resolve()
# ---------------------------------------------------------------------------


def test_tier1_env_fallback_off_by_default():
    """CredentialStore.resolve() env_fallback must default to False.

    Doctrine: about/heart-and-soul/security.md — "only reads os.environ when
    env_fallback=True is explicitly passed (disabled by default)."

    The sister test in test_credential_tier_resolution.py confirms the parameter
    *exists*.  This test confirms the *default value is False*, ensuring that a
    future refactor cannot accidentally flip the safe default to True without
    this guard catching it.
    """
    from butlers.credential_store import CredentialStore

    sig = inspect.signature(CredentialStore.resolve)
    param = sig.parameters.get("env_fallback")

    assert param is not None, (
        "CredentialStore.resolve() must have an 'env_fallback' parameter "
        "(security.md: 'only reads os.environ when env_fallback=True is explicitly passed')"
    )
    assert param.default is False, (
        f"security.md says env_fallback is 'disabled by default', but "
        f"CredentialStore.resolve(env_fallback=...) has default={param.default!r}. "
        f"The safe default must be False."
    )
