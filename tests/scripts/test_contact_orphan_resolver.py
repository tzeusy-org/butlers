"""Unit tests for src/butlers/scripts/contact_orphan_resolver.py.

Covers the three spec scenarios from
openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/relationship-facts/spec.md
§ "Requirement: Orphan contact handling":

1. Dry-run guard — invocation without --apply MUST NOT write to public.entities,
   public.contacts, or relationship.entity_facts.  Only the plan is reported.

2. Entity-mint path — when an orphan has a usable canonical-name signal and --apply
   is passed, the resolver mints a new row in public.entities and backfills
   public.contacts.entity_id.

3. Escalation path — when an orphan has no canonical-name signal, the resolver
   defers the row (notifies the owner) and MUST NOT mint an entity.

Issue: bu-zuh7k
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import butlers.scripts.contact_orphan_resolver as resolver
from butlers.scripts.contact_orphan_resolver import (
    OrphanRow,
    ResolutionOutcome,
    ResolverStats,
    _build_notify_message,
    _mint_entity_and_backfill,
    _render_report,
    _resolve_orphan,
    _run_resolver_with_pool,
    _validate_date_label,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)


def _orphan(
    *,
    name: str = "Alice Smith",
    first_name: str | None = "Alice",
    last_name: str | None = "Smith",
    nickname: str | None = None,
    company: str | None = None,
    roles: list[str] | None = None,
    oid: uuid.UUID | None = None,
) -> OrphanRow:
    """Build an OrphanRow with sensible defaults."""
    return OrphanRow(
        id=oid or uuid.uuid4(),
        name=name,
        first_name=first_name,
        last_name=last_name,
        nickname=nickname,
        company=company,
        roles=roles or [],
        created_at=_NOW,
    )


def _nameless_orphan(*, oid: uuid.UUID | None = None) -> OrphanRow:
    """Build an OrphanRow with no usable canonical-name signal."""
    return OrphanRow(
        id=oid or uuid.uuid4(),
        name="",
        first_name=None,
        last_name=None,
        nickname=None,
        company=None,
        roles=[],
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Minimal asyncpg pool/connection fakes
# ---------------------------------------------------------------------------


class _FakeTransaction:
    """Async context manager that acts as a no-op DB transaction."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeConn:
    """Minimal asyncpg connection mock that records executed SQL."""

    def __init__(self, *, fetchval_side_effects: list | None = None):
        self._fetchval_queue: list = list(fetchval_side_effects or [])
        self.executed_sql: list[str] = []
        self.execute_args: list[tuple] = []

    async def fetchval(self, sql: str, *args):
        if self._fetchval_queue:
            val = self._fetchval_queue.pop(0)
            if isinstance(val, Exception):
                raise val
            return val
        return None

    async def execute(self, sql: str, *args):
        self.executed_sql.append(sql)
        self.execute_args.append(args)
        return "OK"

    def transaction(self):
        return _FakeTransaction()


class _FakePool:
    """Minimal asyncpg pool that yields a single _FakeConn from acquire()."""

    def __init__(
        self,
        conn: _FakeConn,
        *,
        fetchval_return=None,
        fetch_return=None,
    ):
        self._conn = conn
        # Pool-level methods (used by _snapshot_exists and _fetch_orphans)
        self.fetchval = AsyncMock(return_value=fetchval_return)
        self.fetch = AsyncMock(return_value=fetch_return or [])

    def acquire(self):
        conn = self._conn

        class _CM:
            async def __aenter__(self_cm):
                return conn

            async def __aexit__(self_cm, *args):
                return False

        return _CM()


# ---------------------------------------------------------------------------
# OrphanRow.canonical_name_signal — unit tests
# ---------------------------------------------------------------------------


class TestCanonicalNameSignal:
    def test_first_and_last_name(self):
        o = _orphan(first_name="Alice", last_name="Smith")
        assert o.canonical_name_signal() == "Alice Smith"

    def test_first_name_only(self):
        o = _orphan(first_name="Bob", last_name=None)
        assert o.canonical_name_signal() == "Bob"

    def test_last_name_only(self):
        o = _orphan(first_name=None, last_name="Jones")
        assert o.canonical_name_signal() == "Jones"

    def test_nickname_fallback(self):
        o = _orphan(first_name=None, last_name=None, nickname="Sparky")
        assert o.canonical_name_signal() == "Sparky"

    def test_name_field_fallback(self):
        o = _orphan(name="Carol Danvers", first_name=None, last_name=None)
        assert o.canonical_name_signal() == "Carol Danvers"

    def test_generic_names_return_none(self):
        for generic in ("unknown", "unnamed", "contact", "new contact", ""):
            o = _orphan(name=generic, first_name=None, last_name=None)
            assert o.canonical_name_signal() is None, f"Expected None for {generic!r}"

    def test_no_signal_at_all(self):
        assert _nameless_orphan().canonical_name_signal() is None


# ---------------------------------------------------------------------------
# Scenario 1: Dry-run guard
# ---------------------------------------------------------------------------


class TestDryRunGuard:
    """Spec scenario: Dry-run is the default.

    WHEN contact_orphan_resolver.py is invoked without --apply
    THEN the script MUST NOT write to public.entities, public.contacts, or
         relationship.entity_facts.
    AND the script MUST emit the proposed resolution plan to the report file.
    """

    async def test_dry_run_no_writes_for_named_orphan(self):
        """Named orphan in dry-run: status is dry-run-would-mint; no DB writes."""
        orphan = _orphan(first_name="Dry", last_name="Run")
        conn = _FakeConn()
        pool = _FakePool(conn)

        outcome = await _resolve_orphan(pool, orphan, apply=False)

        assert outcome.status == "dry-run-would-mint"
        # No SQL executed on the connection at all
        assert conn.executed_sql == []

    async def test_dry_run_no_writes_for_nameless_orphan(self):
        """Nameless orphan in dry-run: status is dry-run-would-defer; no DB writes."""
        orphan = _nameless_orphan()
        conn = _FakeConn()
        pool = _FakePool(conn)

        outcome = await _resolve_orphan(pool, orphan, apply=False)

        assert outcome.status == "dry-run-would-defer"
        assert conn.executed_sql == []

    async def test_dry_run_report_contains_plan(self, tmp_path):
        """run_resolver_with_pool in dry-run produces a report with DRY-RUN mode."""
        date_label = "20260601"
        snapshot_table = f"contacts_pre_migration_{date_label}"
        orphan = _orphan(first_name="Plan", last_name="Preview")

        # Build a fake record for _fetch_orphans
        fake_record = {
            "id": orphan.id,
            "name": orphan.name,
            "first_name": orphan.first_name,
            "last_name": orphan.last_name,
            "nickname": None,
            "company": None,
            "roles": [],
            "created_at": orphan.created_at,
        }

        conn = _FakeConn()
        pool = _FakePool(
            conn,
            fetchval_return=snapshot_table,  # table exists
            fetch_return=[fake_record],
        )

        report_path = tmp_path / "orphan-report.md"
        rc = await _run_resolver_with_pool(
            pool,
            date_label=date_label,
            report_path=report_path,
            apply=False,
        )

        assert rc == 0
        report_text = report_path.read_text()
        assert "DRY-RUN" in report_text
        assert "dry-run-would-mint" in report_text
        # No actual entity minting; connection has no executed SQL
        assert conn.executed_sql == []

    async def test_run_resolver_returns_error_when_snapshot_missing(self, tmp_path):
        """Returns exit code 1 when snapshot table does not exist."""
        conn = _FakeConn()
        pool = _FakePool(conn, fetchval_return=None)  # to_regclass returns NULL

        report_path = tmp_path / "missing.md"
        rc = await _run_resolver_with_pool(
            pool,
            date_label="20260601",
            report_path=report_path,
            apply=False,
        )

        assert rc == 1
        assert conn.executed_sql == []


# ---------------------------------------------------------------------------
# Scenario 2: Entity-mint path
# ---------------------------------------------------------------------------


class TestEntityMintPath:
    """Spec scenario: Orphan with canonical-name signal mints an entity.

    WHEN the resolver finds an orphan row with a non-empty canonical-name signal
    AND the operator passes --apply
    THEN the script MUST mint a new row in public.entities via direct SQL
    AND the script MUST backfill public.contacts.entity_id for the orphan.
    """

    async def test_mint_entity_and_backfill(self):
        """apply=True on a named orphan inserts into entities and updates contacts."""
        orphan = _orphan(first_name="Alice", last_name="Smith")
        new_entity_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

        conn = _FakeConn(
            fetchval_side_effects=[
                None,  # existing entity_id check → not already backfilled
                new_entity_id,  # INSERT INTO entities RETURNING id
            ]
        )
        pool = _FakePool(conn)

        returned_id = await _mint_entity_and_backfill(pool, orphan, "Alice Smith")

        assert returned_id == new_entity_id
        # Two SQL statements: INSERT INTO entities, UPDATE contacts
        assert len(conn.executed_sql) == 1  # execute() called once for UPDATE
        update_sql = conn.executed_sql[0]
        assert "UPDATE public.contacts" in update_sql
        assert "entity_id" in update_sql

    async def test_mint_idempotent_when_already_backfilled(self):
        """Re-run: if entity_id is already set on contacts, return existing id, no re-mint."""
        orphan = _orphan(first_name="Bob", last_name="Already")
        existing_entity_id = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")

        conn = _FakeConn(
            fetchval_side_effects=[existing_entity_id]  # already backfilled
        )
        pool = _FakePool(conn)

        returned_id = await _mint_entity_and_backfill(pool, orphan, "Bob Already")

        assert returned_id == existing_entity_id
        # No UPDATE should be issued (no execute calls)
        assert conn.executed_sql == []

    async def test_resolve_orphan_apply_mints(self):
        """_resolve_orphan with apply=True returns status='minted' and entity_id set."""
        orphan = _orphan(first_name="Carol", last_name="Mint")
        new_entity_id = uuid.UUID("cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa")

        conn = _FakeConn(
            fetchval_side_effects=[
                None,  # existing_entity_id check
                new_entity_id,  # INSERT RETURNING id
            ]
        )
        pool = _FakePool(conn)

        outcome = await _resolve_orphan(pool, orphan, apply=True)

        assert outcome.status == "minted"
        assert outcome.entity_id == new_entity_id
        assert "Carol Mint" in outcome.note

    async def test_mint_path_in_full_run(self, tmp_path):
        """Full run with apply=True on a named orphan: report records minted status."""
        date_label = "20260601"
        snapshot_table = f"contacts_pre_migration_{date_label}"
        orphan = _orphan(first_name="Full", last_name="Run")
        new_entity_id = uuid.UUID("dddddddd-eeee-ffff-aaaa-bbbbbbbbbbbb")

        fake_record = {
            "id": orphan.id,
            "name": orphan.name,
            "first_name": orphan.first_name,
            "last_name": orphan.last_name,
            "nickname": None,
            "company": None,
            "roles": [],
            "created_at": orphan.created_at,
        }

        conn = _FakeConn(
            fetchval_side_effects=[
                None,  # existing entity_id check
                new_entity_id,  # INSERT RETURNING id
            ]
        )
        pool = _FakePool(
            conn,
            fetchval_return=snapshot_table,  # snapshot exists
            fetch_return=[fake_record],
        )

        report_path = tmp_path / "apply-report.md"
        rc = await _run_resolver_with_pool(
            pool,
            date_label=date_label,
            report_path=report_path,
            apply=True,
        )

        assert rc == 0
        report_text = report_path.read_text()
        assert "APPLY" in report_text
        assert "minted" in report_text
        assert str(new_entity_id) in report_text

        # Exactly one UPDATE was issued (backfill)
        update_calls = [sql for sql in conn.executed_sql if "UPDATE public.contacts" in sql]
        assert len(update_calls) == 1


# ---------------------------------------------------------------------------
# Scenario 3: Escalation path (nameless orphan → owner notification)
# ---------------------------------------------------------------------------


class TestEscalationPath:
    """Spec scenario: Orphan without signal escalates to owner.

    WHEN the resolver finds an orphan row with no canonical-name signal
    THEN the script MUST emit a notify() to the owner describing the row
    AND the script MUST NOT mint an entity for that row.
    """

    async def test_nameless_orphan_deferred_no_entity_minted(self):
        """apply=True on nameless orphan: status='deferred', no INSERT into entities."""
        orphan = _nameless_orphan()
        conn = _FakeConn()
        pool = _FakePool(conn)

        # Suppress the Telegram side-effect by patching _send_telegram_notification
        with patch.object(
            resolver,
            "_send_telegram_notification",
            new=AsyncMock(return_value=False),
        ):
            outcome = await _resolve_orphan(pool, orphan, apply=True)

        assert outcome.status == "deferred"
        assert outcome.entity_id is None
        # No DB writes on the connection
        assert conn.executed_sql == []

    async def test_nameless_orphan_telegram_notification_attempted(self):
        """When apply=True for a nameless orphan, Telegram notification is attempted."""
        orphan = _nameless_orphan()
        conn = _FakeConn()
        pool = _FakePool(conn)

        with patch.object(
            resolver,
            "_send_telegram_notification",
            new=AsyncMock(return_value=True),
        ) as mock_send:
            outcome = await _resolve_orphan(pool, orphan, apply=True)

        assert outcome.status == "deferred"
        assert outcome.notify_sent is True
        mock_send.assert_awaited_once()
        # Message arg should mention the orphan's ID
        sent_message = mock_send.call_args[0][1]
        assert str(orphan.id) in sent_message

    async def test_nameless_orphan_report_shows_deferred(self, tmp_path):
        """Full apply run with nameless orphan: report records deferred status."""
        date_label = "20260601"
        snapshot_table = f"contacts_pre_migration_{date_label}"
        orphan = _nameless_orphan()

        fake_record = {
            "id": orphan.id,
            "name": "",
            "first_name": None,
            "last_name": None,
            "nickname": None,
            "company": None,
            "roles": [],
            "created_at": orphan.created_at,
        }

        conn = _FakeConn()
        pool = _FakePool(
            conn,
            fetchval_return=snapshot_table,
            fetch_return=[fake_record],
        )

        report_path = tmp_path / "nameless-report.md"
        with patch.object(
            resolver,
            "_send_telegram_notification",
            new=AsyncMock(return_value=False),
        ):
            rc = await _run_resolver_with_pool(
                pool,
                date_label=date_label,
                report_path=report_path,
                apply=True,
            )

        assert rc == 0
        report_text = report_path.read_text()
        assert "deferred" in report_text
        # No entity minting happened
        assert conn.executed_sql == []

    async def test_escalation_not_sent_when_credentials_missing(self):
        """When Telegram credentials are absent, notify_sent=False but status is still deferred."""
        orphan = _nameless_orphan()
        conn = _FakeConn()
        pool = _FakePool(conn)

        with patch.object(
            resolver,
            "_resolve_telegram_credentials",
            new=AsyncMock(return_value=(None, None)),
        ):
            outcome = await _resolve_orphan(pool, orphan, apply=True)

        assert outcome.status == "deferred"
        assert outcome.notify_sent is False
        assert "credentials missing" in outcome.note


# ---------------------------------------------------------------------------
# Validate date label
# ---------------------------------------------------------------------------


def test_validate_date_label_valid():
    _validate_date_label("20260601")  # should not raise


def test_validate_date_label_invalid():
    with pytest.raises(ValueError, match="YYYYMMDD"):
        _validate_date_label("2026-06-01")

    with pytest.raises(ValueError, match="YYYYMMDD"):
        _validate_date_label("2026060")  # 7 digits


# ---------------------------------------------------------------------------
# _build_notify_message
# ---------------------------------------------------------------------------


def test_build_notify_message_includes_id_and_created():
    orphan = _nameless_orphan()
    msg = _build_notify_message(orphan)
    assert str(orphan.id) in msg
    assert "2026-05-30" in msg  # from _NOW
    assert "Orphan contact needs review" in msg


def test_build_notify_message_includes_optional_fields():
    orphan = _orphan(
        first_name="Eve",
        last_name="Tester",
        nickname="ET",
        company="Acme",
        roles=["owner"],
    )
    msg = _build_notify_message(orphan)
    assert "First name: Eve" in msg
    assert "Last name: Tester" in msg
    assert "Nickname: ET" in msg
    assert "Company: Acme" in msg
    assert "Roles: owner" in msg


# ---------------------------------------------------------------------------
# _render_report smoke tests
# ---------------------------------------------------------------------------


def test_render_report_dry_run():
    stats = ResolverStats(total_orphans=2)
    report = _render_report(date_label="20260601", stats=stats, apply=False)
    assert "DRY-RUN" in report
    assert "--apply" in report


def test_render_report_apply():
    new_eid = uuid.uuid4()
    orphan = _orphan()
    stats = ResolverStats(
        total_orphans=1,
        minted=1,
        outcomes=[
            ResolutionOutcome(
                orphan=orphan,
                status="minted",
                entity_id=new_eid,
                note="canonical_name='Alice Smith'",
            )
        ],
    )
    report = _render_report(date_label="20260601", stats=stats, apply=True)
    assert "APPLY" in report
    assert str(new_eid) in report
    assert "minted" in report
