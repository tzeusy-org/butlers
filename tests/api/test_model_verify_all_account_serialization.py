"""verify-all never probes two rows of one allowance account at once (bu-vothyr).

The account is the repo identity ``COALESCE(allowance_account, runtime_type)``
(``ALLOWANCE_ACCOUNT_KEY_SQL``): a label reused across runtimes shares one allowance.
"""

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

    def __init__(
        self, group_of: dict[uuid.UUID, Any], failing: set[uuid.UUID] | None = None
    ) -> None:
        self._group_of = group_of
        self._failing = failing or set()
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
        return ProbeResult(ProbeStatus.COMPLETED, ok=entry_id not in self._failing, latency_ms=1)


def _rows(*specs: tuple[str | None, str | None]):
    rows, group_of = [], {}
    for runtime_type, account in specs:
        entry_id = uuid.uuid4()
        rows.append({"id": entry_id, "runtime_type": runtime_type, "allowance_account": account})
        group_of[entry_id] = account if account is not None else runtime_type
    return rows, group_of


async def _run(monkeypatch, specs, *, fail_first: bool = False):
    rows, group_of = _rows(*specs)
    client = _GatedClient(group_of, {rows[0]["id"]} if fail_first else None)
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    monkeypatch.setattr(model_settings, "_probe_client", lambda caller: client)
    monkeypatch.setattr(model_settings.audit, "append", AsyncMock(return_value=1))
    result = await model_settings.run_verify_all_models(pool)
    return result, client


async def test_same_account_rows_are_never_in_flight_together(monkeypatch) -> None:
    result, client = await _run(monkeypatch, [("codex", "acct-a")] * 4)

    assert result.total == 4 and result.ok == 4
    assert client.peak_per_group["acct-a"] == 1


async def test_different_accounts_still_run_concurrently(monkeypatch) -> None:
    _result, client = await _run(
        monkeypatch,
        [("codex", "acct-a"), ("codex", "acct-a"), ("codex", "acct-b"), ("claude", "acct-c")],
    )

    assert max(client.peak_per_group.values()) == 1
    assert client.peak_total == 3


async def test_a_label_shared_across_runtimes_is_one_account(monkeypatch) -> None:
    result, client = await _run(
        monkeypatch, [("codex", "acct-a"), ("opencode", "acct-a"), ("claude", "acct-a")]
    )

    assert result.total == 3
    assert client.peak_total == 1


async def test_unlabelled_rows_share_the_account_named_like_their_runtime(monkeypatch) -> None:
    _result, client = await _run(
        monkeypatch, [("codex", None), ("codex", None), ("claude", "codex")]
    )

    assert client.peak_total == 1


async def test_rows_without_an_allowance_account_serialize_per_runtime(monkeypatch) -> None:
    _result, client = await _run(
        monkeypatch, [("codex", None)] * 3 + [("claude", None)] * 2 + [("codex", "acct-a")]
    )

    assert client.peak_per_group["codex"] == 1
    assert client.peak_per_group["claude"] == 1
    assert client.peak_total == 3


async def test_overall_concurrency_is_capped_across_many_accounts(monkeypatch) -> None:
    cap = model_settings._VERIFY_ALL_CONCURRENCY
    result, client = await _run(monkeypatch, [("codex", f"acct-{i}") for i in range(cap + 4)])

    assert result.total == cap + 4
    assert client.peak_total == cap


async def test_a_failed_probe_does_not_stop_the_rest_of_its_account(monkeypatch) -> None:
    result, client = await _run(monkeypatch, [("codex", "acct-a")] * 4, fail_first=True)

    assert (result.total, result.ok, result.failed) == (4, 3, 1)
    assert len(client.asked) == 4


def test_python_grouping_mirrors_the_canonical_account_key() -> None:
    from butlers.core.model_routing import ALLOWANCE_ACCOUNT_KEY_SQL

    assert ALLOWANCE_ACCOUNT_KEY_SQL == "COALESCE(mc.allowance_account, mc.runtime_type)"


async def test_every_enabled_row_is_still_probed_once(monkeypatch) -> None:
    result, client = await _run(monkeypatch, [("codex", "a")] * 3 + [("codex", "b")] * 2)

    assert result.total == 5
    assert len(client.asked) == len(set(client.asked)) == 5
