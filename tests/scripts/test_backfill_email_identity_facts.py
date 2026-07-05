"""Tests for scripts/backfill_email_identity_facts.py (bu-qeaou).

Covers:
1. Senders already having an active has-email fact are skipped.
2. Ambiguous/unmatched senders (0 or >1 candidate entity) are skipped.
3. An unambiguous match writes the fact via relationship_assert_fact (real
   central-writer path) — never a hand INSERT.
4. Dry run performs no writes but reports what would happen.
5. A pending_approval outcome (owner carve-out) is counted separately from
   a successful link, not as a failure.
6. main() validates required env/args.

The DB layer is mocked (asyncpg pool); relationship_assert_fact is patched at
its home module (the established anchor per
tests/api/test_relationship_entities_update_contact.py) since the script
imports it lazily inside the function body.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "backfill_email_identity_facts.py"
)
_MODULE_NAME = "backfill_email_identity_facts"


def _load_script():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_script()

_NOW = datetime(2026, 7, 1, tzinfo=UTC)
_ASSERT_FACT_PATCH = "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"


def _event_row(address: str) -> dict:
    return {
        "source_sender_identity": address,
        "source_thread_identity": "t1",
        "received_at": _NOW,
    }


class _FakePool:
    def __init__(
        self,
        *,
        sender_rows: list[dict],
        has_email_addresses: set[str] | None = None,
        name_match_ids: dict[str, uuid.UUID] | None = None,
    ) -> None:
        self._sender_rows = sender_rows
        self._has_email_addresses = has_email_addresses or set()
        self._name_match_ids = name_match_ids or {}

    async def fetch(self, query: str, *args):
        if "FROM public.ingestion_events" in query:
            return self._sender_rows
        if "FROM relationship.entity_facts" in query and "object = ANY" in query:
            wanted = set(args[0])
            return [{"object": a} for a in wanted if a in self._has_email_addresses]
        if "FROM public.entities" in query and "ILIKE" in query:
            eid = self._name_match_ids.get(args[0])
            return [{"id": eid}] if eid is not None else []
        raise AssertionError(f"unexpected fetch: {query}")


@pytest.mark.asyncio
async def test_already_linked_address_is_skipped() -> None:
    pool = _FakePool(
        sender_rows=[_event_row("john.doe@example.com")],
        has_email_addresses={"john.doe@example.com"},
    )

    summary = await _mod.backfill_email_identity_facts(
        pool, lookback_days=180, row_limit=20_000, dry_run=False
    )

    assert summary["already_linked"] == 1
    assert summary["linked"] == 0


@pytest.mark.asyncio
async def test_no_match_is_skipped_as_ambiguous_or_unmatched() -> None:
    pool = _FakePool(sender_rows=[_event_row("john.doe@example.com")])

    summary = await _mod.backfill_email_identity_facts(
        pool, lookback_days=180, row_limit=20_000, dry_run=False
    )

    assert summary["ambiguous_or_unmatched"] == 1
    assert summary["linked"] == 0


@pytest.mark.asyncio
async def test_unambiguous_match_writes_via_central_writer() -> None:
    from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult

    entity_id = uuid.uuid4()
    pool = _FakePool(
        sender_rows=[_event_row("john.doe@example.com")],
        name_match_ids={"John Doe": entity_id},
    )

    with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock) as mock_assert:
        mock_assert.return_value = AssertResult(
            outcome=AssertOutcome.inserted, fact_id=uuid.uuid4()
        )
        summary = await _mod.backfill_email_identity_facts(
            pool, lookback_days=180, row_limit=20_000, dry_run=False
        )

    assert summary["linked"] == 1
    assert summary["pending_approval"] == 0
    mock_assert.assert_awaited_once()
    call_args = mock_assert.await_args
    assert call_args.args[1] == entity_id
    assert call_args.args[2] == "has-email"
    assert call_args.args[3] == "john.doe@example.com"
    assert call_args.kwargs["src"] == "migration"


@pytest.mark.asyncio
async def test_dry_run_writes_nothing() -> None:
    entity_id = uuid.uuid4()
    pool = _FakePool(
        sender_rows=[_event_row("john.doe@example.com")],
        name_match_ids={"John Doe": entity_id},
    )

    with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock) as mock_assert:
        summary = await _mod.backfill_email_identity_facts(
            pool, lookback_days=180, row_limit=20_000, dry_run=True
        )

    assert summary["linked"] == 1  # reported as "would link"
    mock_assert.assert_not_called()


@pytest.mark.asyncio
async def test_owner_carveout_pending_approval_counted_separately() -> None:
    from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult

    entity_id = uuid.uuid4()
    pool = _FakePool(
        sender_rows=[_event_row("john.doe@example.com")],
        name_match_ids={"John Doe": entity_id},
    )

    with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock) as mock_assert:
        mock_assert.return_value = AssertResult(
            outcome=AssertOutcome.pending_approval, fact_id=None, action_id=uuid.uuid4()
        )
        summary = await _mod.backfill_email_identity_facts(
            pool, lookback_days=180, row_limit=20_000, dry_run=False
        )

    assert summary["pending_approval"] == 1
    assert summary["linked"] == 0


@pytest.mark.asyncio
async def test_assert_fact_exception_is_counted_as_error_not_raised() -> None:
    entity_id = uuid.uuid4()
    pool = _FakePool(
        sender_rows=[_event_row("john.doe@example.com")],
        name_match_ids={"John Doe": entity_id},
    )

    with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock) as mock_assert:
        mock_assert.side_effect = RuntimeError("boom")
        summary = await _mod.backfill_email_identity_facts(
            pool, lookback_days=180, row_limit=20_000, dry_run=False
        )

    assert summary["errors"] == 1
    assert summary["linked"] == 0


@pytest.mark.asyncio
async def test_no_senders_is_a_noop() -> None:
    pool = _FakePool(sender_rows=[])

    summary = await _mod.backfill_email_identity_facts(
        pool, lookback_days=180, row_limit=20_000, dry_run=False
    )

    assert summary["senders_scanned"] == 0
    assert summary["linked"] == 0


@pytest.mark.asyncio
async def test_main_rejects_nonpositive_lookback_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUTLERS_DATABASE_URL", "postgresql://x/y")
    rc = await _mod.main(["--lookback-days", "0"])
    assert rc == 1


@pytest.mark.asyncio
async def test_main_rejects_nonpositive_row_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUTLERS_DATABASE_URL", "postgresql://x/y")
    rc = await _mod.main(["--row-limit", "0"])
    assert rc == 1


@pytest.mark.asyncio
async def test_main_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUTLERS_DATABASE_URL", raising=False)
    rc = await _mod.main([])
    assert rc == 1
