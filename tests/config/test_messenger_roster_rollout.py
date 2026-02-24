"""Regression tests for messenger roster scaffolding and runbook coverage."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
MESSENGER_DIR = REPO_ROOT / "roster" / "messenger"


def test_messenger_roster_identity_files_exist() -> None:
    """Messenger roster should include required identity files."""
    assert (MESSENGER_DIR / "butler.toml").is_file()
    assert (MESSENGER_DIR / "CLAUDE.md").is_file()
    assert (MESSENGER_DIR / "MANIFESTO.md").is_file()


def test_messenger_butler_toml_has_delivery_module_wiring() -> None:
    """Messenger config should wire telegram/email modules."""
    with (MESSENGER_DIR / "butler.toml").open("rb") as fh:
        data = tomllib.load(fh)

    butler = data.get("butler", {})
    assert butler.get("name") == "messenger"
    assert butler.get("port") == 40104

    db = butler.get("db", {})
    assert db.get("name") == "butlers"
    assert db.get("schema") == "messenger"

    modules = data.get("modules", {})

    telegram = modules.get("telegram")
    assert isinstance(telegram, dict)

    email = modules.get("email")
    assert isinstance(email, dict)


def test_messenger_claude_guidance_mentions_notify_route_contract() -> None:
    """Messenger CLAUDE guidance should describe notify/route execution boundaries."""
    guidance = (MESSENGER_DIR / "CLAUDE.md").read_text().lower()
    required_fragments = (
        "route.execute",
        "notify.v1",
        "must not recursively call `notify`",
        "telegram_send_message",
        "email_send_message",
    )
    for fragment in required_fragments:
        assert fragment in guidance


def test_readme_documents_running_messenger_with_switchboard() -> None:
    """README should describe local/dev messenger startup and service port."""
    readme = (REPO_ROOT / "README.md").read_text().lower()

    assert "butlers up --only switchboard --only messenger" in readme
    assert "| messenger" in readme
    assert "| 40104" in readme
    assert "butler_telegram_token" in readme
    assert "butler_email_address" in readme
    assert "butler_email_password" in readme
