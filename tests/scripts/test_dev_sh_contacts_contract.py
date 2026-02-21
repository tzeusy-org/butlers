"""Contracts for contacts sync runtime behavior in local dev bootstrap."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _dev_sh_text() -> str:
    return Path("scripts/dev.sh").read_text(encoding="utf-8")


def test_contacts_sync_is_documented_as_module_internal_runtime() -> None:
    text = _dev_sh_text()
    assert "Contacts sync runs as a module-internal poller in butlers up" in text


def test_dev_sh_does_not_launch_standalone_contacts_connector() -> None:
    text = _dev_sh_text()
    assert "butlers.connectors.contacts" not in text
    assert "CONNECTOR_PROVIDER=contacts" not in text
