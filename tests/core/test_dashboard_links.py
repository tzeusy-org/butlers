"""Owner-facing dashboard links: one helper, no localhost fallback, registered routes only."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest

from butlers.core import dashboard_links

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "frontend" / "tests" / "e2e" / "telegram-minted-routes.json"


@pytest.fixture(autouse=True)
def _reset_warning(monkeypatch):
    monkeypatch.setattr(dashboard_links, "_warned_unset", False)


def test_link_is_absolute_when_dashboard_url_is_set(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example/butlers/")
    assert (
        dashboard_links.dashboard_link("/secrets?focus=u%3Agoogle")
        == "https://dash.example/butlers/secrets?focus=u%3Agoogle"
    )


def test_no_link_and_one_warning_when_dashboard_url_is_unset(monkeypatch, caplog):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    monkeypatch.setenv("DASHBOARD_PORT", "41200")
    with caplog.at_level(logging.WARNING, logger=dashboard_links.logger.name):
        assert dashboard_links.dashboard_link("/system") is None
        assert dashboard_links.dashboard_base_url() is None
    assert [r for r in caplog.records if "DASHBOARD_URL" in r.getMessage()].__len__() == 1


def _registered_patterns() -> list[re.Pattern[str]]:
    entries = json.loads(REGISTRY.read_text())["routes"]
    return [re.compile(e["pattern"]) for e in entries]


def assert_registered(path: str) -> None:
    assert any(p.fullmatch(path) for p in _registered_patterns()), (
        f"{path} is not in telegram-minted-routes.json; add an entry and phone-spec fixtures"
    )


def test_registry_entries_name_a_payload_selector():
    for entry in json.loads(REGISTRY.read_text())["routes"]:
        assert entry["pattern"] and entry["example"] and entry["payload_selector"]
        assert re.fullmatch(entry["pattern"], entry["example"]), entry


def test_secrets_lifecycle_links_are_registered():
    from butlers.jobs.secrets_lifecycle import CredentialSnapshot, _focus_fragment

    snap = CredentialSnapshot(key="u:google", family="user", label="Google", state="expired")
    assert_registered(_focus_fragment(snap.key))


_DOOR = re.compile(r"'door',\s*'([^']+)'(\s*\|\|\s*\w+::text)?")


def test_runtime_attention_doors_are_registered():
    """Every door the seeded outbox writers can mint is a registered route."""
    sql = (REPO / "scripts" / "init-db.sql").read_text()
    doors = {
        m.group(1) + ("11111111-1111-4111-8111-111111111111" if m.group(2) else "")
        for m in _DOOR.finditer(sql)
    }
    assert {"/system", "/spend?openDrawer=fleet-halt", "/spend?outcome=quota_skip"} <= doors
    for door in sorted(doors):
        assert_registered(door)


def test_approval_self_routes_are_registered():
    from butlers.tools.switchboard.notification.self_links import SELF_ROUTES

    for template in SELF_ROUTES.values():
        assert_registered(template.format(action_id="11111111-1111-4111-8111-111111111111"))


_ALLOWED_READERS = {
    "src/butlers/core/dashboard_links.py",
    # Not Telegram links: OAuth/Spotify redirects, startup guard, QA PR-body links.
    "src/butlers/api/routers/oauth.py",
    "src/butlers/api/routers/spotify.py",
    "src/butlers/startup_guard.py",
    "src/butlers/core/qa/dispatch.py",
    "src/butlers/core/qa/prompts.py",
    "src/butlers/modules/qa/__init__.py",
    # Self-link resolver: stricter (https-only) and independent by design.
    "roster/switchboard/tools/notification/self_links.py",
}


def test_only_the_helper_reads_dashboard_url():
    offenders = []
    for root in ("src", "roster"):
        for path in (REPO / root).rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel in _ALLOWED_READERS:
                continue
            text = path.read_text()
            if re.search(r"environ(\.get\(|\[)\s*[\"']DASHBOARD_URL[\"']", text):
                offenders.append(rel)
    assert offenders == [], f"read DASHBOARD_URL outside butlers.core.dashboard_links: {offenders}"
