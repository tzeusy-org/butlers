"""The QA write token is never resolved into the daemon (bu-ejow5p.1).

Investigators run as root in the same pod as the daemon, so any credential the daemon holds is
reachable by construction.  The daemon therefore resolves only a read-only polling token
(``BUTLERS_QA_GH_READ_TOKEN``); a write-scoped key found in the credential store is refused,
reported content-blind and never loaded.  All token values here are synthetic markers.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.dispatch import (
    QA_GH_READ_TOKEN_KEY,
    QA_GH_TOKEN_KEY,
    build_agent_env,
)
from butlers.modules.qa import QaModule

pytestmark = pytest.mark.unit

_READ_MARKER = "synthetic-read-marker-0001"
_WRITE_MARKER = "synthetic-write-marker-0002"


class _RecordingStore:
    """Fake CredentialStore that records every resolve/has call."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values
        self.resolved: list[str] = []
        self.checked: list[str] = []

    async def resolve(self, key: str, **_: object) -> str | None:
        self.resolved.append(key)
        return self._values.get(key)

    async def has(self, key: str) -> bool:
        self.checked.append(key)
        return key in self._values


def _module(store: _RecordingStore) -> QaModule:
    mod = QaModule()
    mod._credential_store = store
    return mod


def test_keys_are_distinct():
    assert QA_GH_READ_TOKEN_KEY == "BUTLERS_QA_GH_READ_TOKEN"
    assert QA_GH_READ_TOKEN_KEY != QA_GH_TOKEN_KEY


async def test_module_resolves_only_the_read_key():
    store = _RecordingStore({QA_GH_READ_TOKEN_KEY: _READ_MARKER, QA_GH_TOKEN_KEY: _WRITE_MARKER})
    token = await _module(store)._resolve_gh_token()
    assert token == _READ_MARKER
    assert QA_GH_TOKEN_KEY not in store.resolved


async def test_write_key_alone_yields_no_token_and_is_never_resolved():
    store = _RecordingStore({QA_GH_TOKEN_KEY: _WRITE_MARKER})
    assert await _module(store)._resolve_gh_token() is None
    assert QA_GH_TOKEN_KEY not in store.resolved


async def test_write_key_present_raises_one_content_blind_condition_per_patrol(caplog):
    store = _RecordingStore({QA_GH_TOKEN_KEY: _WRITE_MARKER})
    mod = _module(store)
    sent: list[dict] = []

    async def _notify(**kwargs):
        sent.append(kwargs)
        return {}

    mod._notify_fn = _notify
    patrol = uuid.uuid4()
    with caplog.at_level("WARNING"):
        assert await mod._refuse_write_token(patrol) is True
        assert await mod._refuse_write_token(patrol) is True  # same patrol: deduplicated
    assert len(sent) == 1
    assert QA_GH_TOKEN_KEY in sent[0]["message"]
    assert _WRITE_MARKER not in sent[0]["message"] + caplog.text
    assert QA_GH_TOKEN_KEY not in store.resolved  # presence check only


async def test_no_write_key_means_no_condition():
    store = _RecordingStore({QA_GH_READ_TOKEN_KEY: _READ_MARKER})
    mod = _module(store)
    mod._notify_fn = AsyncMock()
    assert await mod._refuse_write_token(uuid.uuid4()) is False
    mod._notify_fn.assert_not_awaited()


async def test_patrol_never_hands_the_token_to_investigation_dispatch():
    """The read token polls PR status only; investigation dispatch gets no token."""
    store = _RecordingStore({QA_GH_READ_TOKEN_KEY: _READ_MARKER, QA_GH_TOKEN_KEY: _WRITE_MARKER})
    mod = _module(store)
    mod._notify_fn = AsyncMock(return_value={})
    from butlers.modules.qa import QaConfig

    mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
    from butlers.testing.fake_pool import fake_pool

    mod._pool = fake_pool(fetchval=uuid.uuid4(), fetch=[], execute=None, fetchrow=None)
    mod._sources = []
    with (
        patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as triage,
        patch("butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock) as dispatch,
        patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock) as poll,
    ):
        triage.return_value = MagicMock(all_findings=[], novel_findings=[], dedup_counts={})
        dispatch.return_value = []
        await mod._run_patrol_cycle()
    assert dispatch.await_args.kwargs["gh_token"] is None
    assert poll.await_args.args[2] == _READ_MARKER
    assert QA_GH_TOKEN_KEY not in store.resolved


def test_read_token_never_enters_the_process_environment_or_agent_env():
    env_before = dict(os.environ)
    with patch.dict(
        os.environ,
        {
            "BUTLERS_QA_GH_TOKEN": _WRITE_MARKER,
            "BUTLERS_QA_GH_READ_TOKEN": _READ_MARKER,
            "GH_TOKEN": _WRITE_MARKER,
            "GITHUB_TOKEN": _READ_MARKER,
        },
    ):
        agent_env = build_agent_env()
        assert _READ_MARKER not in agent_env.values()
        assert _WRITE_MARKER not in agent_env.values()
        assert not any("TOKEN" in key for key in agent_env)
    assert dict(os.environ) == env_before


async def test_router_pr_panel_resolves_only_the_read_key(monkeypatch):
    from butlers.api.routers import qa as router

    store = _RecordingStore({QA_GH_READ_TOKEN_KEY: _READ_MARKER, QA_GH_TOKEN_KEY: _WRITE_MARKER})
    monkeypatch.setattr("butlers.credential_store.CredentialStore", lambda pool: store)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    db = MagicMock()
    assert await router._resolve_github_token(db) == _READ_MARKER
    assert store.resolved == [QA_GH_READ_TOKEN_KEY]


async def test_router_missing_read_key_degrades_and_never_falls_back_to_write(monkeypatch):
    from butlers.api.routers import qa as router

    store = _RecordingStore({QA_GH_TOKEN_KEY: _WRITE_MARKER})
    monkeypatch.setattr("butlers.credential_store.CredentialStore", lambda pool: store)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert await router._resolve_github_token(MagicMock()) is None
    assert QA_GH_TOKEN_KEY not in store.resolved


async def test_credentials_status_reports_read_key_and_flags_a_present_write_key(monkeypatch):
    from butlers.api.routers import qa as router

    store = _RecordingStore({QA_GH_READ_TOKEN_KEY: _READ_MARKER, QA_GH_TOKEN_KEY: _WRITE_MARKER})
    monkeypatch.setattr("butlers.credential_store.CredentialStore", lambda pool: store)
    status = await router._resolve_credentials_status(MagicMock())
    assert status["gh_token_present"] is True  # the read key
    assert status["gh_write_token_present"] is True  # action needed: remove it
    assert QA_GH_TOKEN_KEY not in store.resolved
