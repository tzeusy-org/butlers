"""Unit tests for ContactBackfillWriter child-table upserts (bu-j9kwc).

Covers upsert_addresses, upsert_important_dates, and upsert_labels —
which were no-ops until contacts_004 added the local_entity_id anchor
columns to addresses, important_dates, and contact_labels.

These tests use fake pools that record SQL calls so they run without a
database container and remain fast in the unit suite.

Key invariants checked
- When the table does not exist (_has_table is False), the method
  returns without calling pool.execute.
- When the table exists but the entity-anchor column is absent
  (_has_entity_anchor is False), the method returns without calling
  pool.execute.
- When both checks pass, the method issues the expected INSERT SQL
  containing 'local_entity_id'.
- When contact data has no writable rows (empty lists, missing required
  fields), execute is not called.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from butlers.modules.contacts.backfill import ContactBackfillWriter
from butlers.modules.contacts.sync import CanonicalContact, ContactAddress, ContactDate

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake pool helpers
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal fake asyncpg pool that stubs the probe and write calls."""

    def __init__(
        self,
        *,
        tables_exist: bool = True,
        entity_columns_exist: bool = True,
        label_id: uuid.UUID | None = None,
    ) -> None:
        self._tables_exist = tables_exist
        self._entity_columns_exist = entity_columns_exist
        self._label_id = label_id or uuid.uuid4()
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrows_called: list[str] = []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrows_called.append(query)
        # Check pg_attribute before to_regclass: the entity-anchor probe
        # uses to_regclass inside its pg_attribute query, so order matters.
        if "pg_attribute" in query:
            # Entity-anchor column probe
            return {"col": 1} if self._entity_columns_exist else None
        if "to_regclass" in query:
            return {"exists": self._tables_exist}
        # Duplicate-check queries return None (no existing row).
        return None

    async def fetchval(self, query: str, *args: Any) -> Any | None:
        if "SELECT id FROM labels" in query:
            return self._label_id
        return None

    async def execute(self, query: str, *args: Any) -> None:
        self.executes.append((query, args))


def _writer(pool: _FakePool) -> ContactBackfillWriter:
    return ContactBackfillWriter(pool, provider="google", account_id="test")


def _entity_id() -> uuid.UUID:
    return uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


# Per-method (upsert callable, populated-contact) pairs used by the shared
# graceful-degradation parametrization below.
def _addresses_contact() -> CanonicalContact:
    return CanonicalContact(
        external_id="people/1",
        addresses=[ContactAddress(street="1 Main St")],
    )


def _dates_contact() -> CanonicalContact:
    return CanonicalContact(
        external_id="people/1",
        birthdays=[ContactDate(month=1, day=1)],
    )


def _labels_contact() -> CanonicalContact:
    return CanonicalContact(
        external_id="people/1",
        group_memberships=["contactGroups/myContacts"],
    )


_UPSERT_METHODS = [
    ("upsert_addresses", _addresses_contact),
    ("upsert_important_dates", _dates_contact),
    ("upsert_labels", _labels_contact),
]


@pytest.mark.parametrize(("method_name", "contact_factory"), _UPSERT_METHODS)
async def test_upsert_skips_when_table_absent(method_name: str, contact_factory) -> None:
    """No writes when the child table does not exist (cross-chain migration hazard)."""
    pool = _FakePool(tables_exist=False, entity_columns_exist=False)
    writer = _writer(pool)
    await getattr(writer, method_name)(_entity_id(), contact_factory())
    assert pool.executes == []


@pytest.mark.parametrize(("method_name", "contact_factory"), _UPSERT_METHODS)
async def test_upsert_skips_when_anchor_column_absent(method_name: str, contact_factory) -> None:
    """No writes when contacts_004 local_entity_id anchor column is absent."""
    pool = _FakePool(tables_exist=True, entity_columns_exist=False)
    writer = _writer(pool)
    await getattr(writer, method_name)(_entity_id(), contact_factory())
    assert pool.executes == []


@pytest.mark.parametrize("method_name", [m for m, _ in _UPSERT_METHODS])
async def test_upsert_no_op_for_empty_contact(method_name: str) -> None:
    """No writes when the contact carries no rows for this child table."""
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)
    await getattr(writer, method_name)(_entity_id(), CanonicalContact(external_id="people/1"))
    assert pool.executes == []


# ---------------------------------------------------------------------------
# upsert_addresses
# ---------------------------------------------------------------------------


async def test_upsert_addresses_writes_when_table_and_column_exist() -> None:
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)
    eid = _entity_id()

    contact = CanonicalContact(
        external_id="people/1",
        addresses=[
            ContactAddress(street="1 Main St", city="Springfield", label="Home"),
        ],
    )
    await writer.upsert_addresses(eid, contact)

    assert pool.executes, "expected at least one execute call"
    sql, _ = pool.executes[0]
    assert "INSERT INTO addresses" in sql
    assert "local_entity_id" in sql


async def test_upsert_addresses_skips_entry_with_no_street() -> None:
    """Addresses without a street value must be skipped (line_1 is NOT NULL)."""
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)

    contact = CanonicalContact(
        external_id="people/1",
        addresses=[
            ContactAddress(city="Nowhere"),  # no street
        ],
    )
    await writer.upsert_addresses(_entity_id(), contact)

    assert pool.executes == [], "address with no street should be skipped"


async def test_upsert_addresses_clears_invalid_country_code() -> None:
    """Country strings that are not exactly 2 chars must be dropped (schema is VARCHAR(2))."""
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)

    contact = CanonicalContact(
        external_id="people/1",
        addresses=[
            ContactAddress(street="1 Main St", country="USA"),  # 3 chars → invalid
        ],
    )
    await writer.upsert_addresses(_entity_id(), contact)

    assert pool.executes, "address should still be inserted"
    _sql, args = pool.executes[0]
    # country arg is at position 8 (0-indexed: local_entity_id, label, street, line2,
    # city, province, postal_code, country → index 7)
    assert args[7] is None, "invalid country code must be stored as NULL"


# ---------------------------------------------------------------------------
# upsert_important_dates
# ---------------------------------------------------------------------------


async def test_upsert_important_dates_writes_birthday() -> None:
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)
    eid = _entity_id()

    contact = CanonicalContact(
        external_id="people/1",
        birthdays=[ContactDate(month=3, day=15, year=1990)],
    )
    await writer.upsert_important_dates(eid, contact)

    assert pool.executes, "expected at least one execute call"
    sql, args = pool.executes[0]
    assert "INSERT INTO important_dates" in sql
    assert "local_entity_id" in sql
    assert args[0] == eid  # local_entity_id
    assert args[1] == "birthday"
    assert args[2] == 3  # month
    assert args[3] == 15  # day
    assert args[4] == 1990  # year


async def test_upsert_important_dates_writes_anniversary() -> None:
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)

    contact = CanonicalContact(
        external_id="people/1",
        anniversaries=[ContactDate(month=6, day=1)],
    )
    await writer.upsert_important_dates(_entity_id(), contact)

    assert pool.executes
    _sql, args = pool.executes[0]
    assert args[1] == "anniversary"


async def test_upsert_important_dates_uses_custom_label() -> None:
    """ContactDate.label overrides the default 'birthday'/'anniversary' label."""
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)

    contact = CanonicalContact(
        external_id="people/1",
        birthdays=[ContactDate(month=1, day=1, label="nameday")],
    )
    await writer.upsert_important_dates(_entity_id(), contact)

    assert pool.executes
    _sql, args = pool.executes[0]
    assert args[1] == "nameday"


async def test_upsert_important_dates_skips_entry_with_null_month_or_day() -> None:
    """Dates with NULL month or day must be skipped (schema is NOT NULL)."""
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)

    contact = CanonicalContact(
        external_id="people/1",
        birthdays=[
            ContactDate(month=None, day=15),  # month missing
            ContactDate(month=3, day=None),  # day missing
        ],
    )
    await writer.upsert_important_dates(_entity_id(), contact)
    assert pool.executes == []


# ---------------------------------------------------------------------------
# upsert_labels
# ---------------------------------------------------------------------------


async def test_upsert_labels_inserts_label_and_contact_labels() -> None:
    label_id = uuid.uuid4()
    pool = _FakePool(tables_exist=True, entity_columns_exist=True, label_id=label_id)
    writer = _writer(pool)
    eid = _entity_id()

    contact = CanonicalContact(
        external_id="people/1",
        group_memberships=["contactGroups/myContacts"],
    )
    await writer.upsert_labels(eid, contact)

    # Expect: INSERT INTO labels ... and INSERT INTO contact_labels ...
    assert len(pool.executes) == 2  # noqa: PLR2004
    labels_sql, _labels_args = pool.executes[0]
    cl_sql, cl_args = pool.executes[1]

    assert "INSERT INTO labels" in labels_sql
    assert "INSERT INTO contact_labels" in cl_sql
    assert "local_entity_id" in cl_sql
    assert cl_args[0] == label_id
    assert cl_args[1] == eid


async def test_upsert_labels_skips_empty_label_name() -> None:
    """group_memberships entries that normalise to empty strings are skipped."""
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)

    # _normalize_group_label("") returns ""
    contact = CanonicalContact(
        external_id="people/1",
        group_memberships=[""],
    )
    await writer.upsert_labels(_entity_id(), contact)
    assert pool.executes == []


async def test_upsert_labels_multiple_groups_each_get_own_rows() -> None:
    """Each group membership produces a labels insert + contact_labels insert."""
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)

    contact = CanonicalContact(
        external_id="people/1",
        group_memberships=[
            "contactGroups/myContacts",
            "contactGroups/starred",
        ],
    )
    await writer.upsert_labels(_entity_id(), contact)

    # 2 groups × 2 queries (INSERT labels + INSERT contact_labels) = 4 executes
    assert len(pool.executes) == 4  # noqa: PLR2004
    insert_labels = [q for q, _ in pool.executes if "INSERT INTO labels" in q]
    insert_cl = [q for q, _ in pool.executes if "INSERT INTO contact_labels" in q]
    assert len(insert_labels) == 2  # noqa: PLR2004
    assert len(insert_cl) == 2  # noqa: PLR2004


# ---------------------------------------------------------------------------
# Flags isolation: second call to _ensure_table_flags is a no-op
# ---------------------------------------------------------------------------


async def test_table_flags_cached_after_first_call() -> None:
    """_ensure_table_flags must only query the DB once (cached by _table_flags)."""
    pool = _FakePool(tables_exist=True, entity_columns_exist=True)
    writer = _writer(pool)
    eid = _entity_id()

    contact = CanonicalContact(external_id="people/1")
    # Call two upsert methods — both trigger _ensure_table_flags but probes run once.
    await writer.upsert_addresses(eid, contact)
    probe_count_after_first = len(pool.fetchrows_called)

    await writer.upsert_important_dates(eid, contact)
    probe_count_after_second = len(pool.fetchrows_called)

    assert probe_count_after_first == probe_count_after_second, (
        "_ensure_table_flags should not re-probe on second call"
    )
