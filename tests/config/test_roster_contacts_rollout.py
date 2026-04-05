"""Regression tests for roster contacts module rollout configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from butlers.modules.contacts import ContactsConfig

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTACTS_ENABLED_BUTLERS = ("general", "health", "relationship")
CONTACTS_EXCLUDED_BUTLERS = ("switchboard", "messenger")


def _load_butler_toml(butler_name: str) -> dict:
    path = REPO_ROOT / "roster" / butler_name / "butler.toml"
    with path.open("rb") as fh:
        return tomllib.load(fh)


def test_contacts_rollout_config_and_docs() -> None:
    """Enabled butlers have valid contacts config; excluded butlers omit it."""
    for butler_name in CONTACTS_ENABLED_BUTLERS:
        modules = _load_butler_toml(butler_name).get("modules", {})
        contacts = modules.get("contacts")
        assert isinstance(contacts, dict), f"{butler_name} is missing [modules.contacts]"
        validated = ContactsConfig.model_validate(contacts)
        assert "google" in validated.provider_types
        assert validated.sync.enabled is True and validated.sync.interval_minutes == 15

    for butler_name in CONTACTS_EXCLUDED_BUTLERS:
        assert "contacts" not in _load_butler_toml(butler_name).get("modules", {})

    guidance = (REPO_ROOT / "docs/modules/contacts.md").read_text().lower()
    for frag in ("google_oauth_client_id", "google_oauth_client_secret", "google_oauth_refresh"):
        assert frag in guidance
