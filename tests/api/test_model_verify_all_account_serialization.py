"""verify-all never probes two rows of one runtime and allowance account at once (bu-vothyr)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.api.routers import model_settings
from butlers.core.runtime_probe_control.coordinator import ProbeResult, ProbeStatus

pytestmark = pytest.mark.unit


class _GatedClient:
    """Records in-flight probes per group; each probe yields until the loop has run others."""

    def __init__(self, group_of: dict[uuid.UUID, Any]) -> None:
        self._group_of = group_of
        self.in_flight: dict[Any, int] = {}
        self.peak_per_group: dict[Any, int] = {}
        self.total_in_flight = 0
        self.peak_total = 0
        self.asked: list[uuid.UUID] = []

    async def probe(self, entry_id: uuid.UUID) -> ProbeResult:
        group = self._group_of[entry_id]
        self.asked.append(entry_id)
        self.in_flight[group] = self.in_flight.get(group, 0) + 1
        self.total_in_flight += 1
        self.peak_per_group[group] = max(self.peak_per_group.get(group, 0), self.in_flight[group])
        self.peak_total = max(self.peak_total, self.total_in_flight)
        for _ in range(5):
            await asyncio.sleep(0)
        self.in_flight[group] -= 1
        self.total_in_flight -= 1
        return ProbeResult(ProbeStatus.COMPLETED, ok=True, latency_ms=1)


def _rows(*specs: tuple[str | None, str | None]):
    rows, group_of = [], {}
    for runtime_type, account in specs:
        entry_id = uuid.uuid4()
        rows.append({"id": entry_id, "runtime_type": runtime_type, "allowance_account": account})
        group_of[entry_id] = (runtime_type, account)
    return rows, group_of


async def _run(monkeypatch, specs):
    rows, group_of = _rows(*specs)
    client = _GatedClient(group_of)
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    monkeypatch.setattr(model_settings, "_probe_client", lambda caller: client)
    monkeypatch.setattr(model_settings.audit, "append", AsyncMock(return_value=1))
    result = await model_settings.run_verify_all_models(pool)
    return result, client


async def test_same_account_rows_are_never_in_flight_together(monkeypatch) -> None:
    result, client = await _run(monkeypatch, [("codex", "acct-a")] * 4)

    assert result.total == 4 and result.ok == 4
    assert client.peak_per_group[("codex", "acct-a")] == 1


async def test_different_accounts_still_run_concurrently(monkeypatch) -> None:
    _result, client = await _run(
        monkeypatch,
        [("codex", "acct-a"), ("codex", "acct-a"), ("codex", "acct-b"), ("claude", "acct-a")],
    )

    assert max(client.peak_per_group.values()) == 1
    assert client.peak_total == 3


async def test_rows_without_an_allowance_account_serialize_per_runtime(monkeypatch) -> None:
    _result, client = await _run(
        monkeypatch, [("codex", None)] * 3 + [("claude", None)] * 2 + [("codex", "acct-a")]
    )

    assert client.peak_per_group[("codex", None)] == 1
    assert client.peak_per_group[("claude", None)] == 1
    assert client.peak_total == 3


async def test_every_enabled_row_is_still_probed_once(monkeypatch) -> None:
    result, client = await _run(monkeypatch, [("codex", "a")] * 3 + [("codex", "b")] * 2)

    assert result.total == 5
    assert len(client.asked) == len(set(client.asked)) == 5
