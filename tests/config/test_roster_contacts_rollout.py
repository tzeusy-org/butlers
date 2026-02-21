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


def test_contacts_enabled_butlers_have_valid_contacts_config() -> None:
    """Enabled butlers should provide a valid ContactsConfig contract."""
    for butler_name in CONTACTS_ENABLED_BUTLERS:
        modules = _load_butler_toml(butler_name).get("modules", {})
        contacts = modules.get("contacts")

        assert isinstance(contacts, dict), f"{butler_name} is missing [modules.contacts]"

        validated = ContactsConfig.model_validate(contacts)
        assert validated.provider == "google"
        assert validated.include_other_contacts is False
        assert validated.sync.enabled is True
        assert validated.sync.run_on_startup is True
        assert validated.sync.interval_minutes == 15
        assert validated.sync.full_sync_interval_days == 6


def test_contacts_excluded_butlers_documented_as_not_enabled() -> None:
    """Routing/delivery plane butlers should not enable contacts sync."""
    for butler_name in CONTACTS_EXCLUDED_BUTLERS:
        modules = _load_butler_toml(butler_name).get("modules", {})
        assert "contacts" not in modules, (
            f"{butler_name} should not enable [modules.contacts] (intentional exclusion)"
        )


def test_contacts_rollout_docs_state_provider_and_required_secrets() -> None:
    """Docs should capture rollout assumptions and secret requirements."""
    guidance = (REPO_ROOT / "docs/modules/contacts_draft.md").read_text().lower()
    required_fragments = (
        'provider = "google"',
        "roster/general/butler.toml",
        "roster/health/butler.toml",
        "roster/relationship/butler.toml",
        "roster/switchboard/butler.toml",
        "roster/messenger/butler.toml",
        "google_oauth_client_id",
        "google_oauth_client_secret",
        "google_refresh_token",
    )
    for fragment in required_fragments:
        assert fragment in guidance, f"contacts rollout docs missing fragment: {fragment}"
