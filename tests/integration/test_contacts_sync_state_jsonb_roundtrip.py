"""Real-Postgres regression: contacts sync's ``ContactsSyncStateStore`` must not
double-encode ``contacts_sync_state.contact_versions`` (bu-dycxq — sibling
sweep to bu-cymc4/bu-x92jw/bu-bstqu/bu-c8b8e/bu-xfcpf).

``ContactsSyncStateStore.save`` used to ``json.dumps()`` the ``contact_versions``
dict before binding it (no explicit ``::jsonb`` cast, but the column itself is
JSONB so asyncpg still routes the parameter through the registered JSONB codec,
``register_jsonb_codec``, ``src/butlers/db.py`` — whose encoder calls
``json.dumps()`` on the bound Python object again). This double-encoded
``contact_versions`` into a jsonb-typed STRING instead of an OBJECT.
``_normalize_contact_versions`` carried an ``isinstance(value, str)`` /
``json.loads`` workaround on read to tolerate the corrupted shape.

The lack of any prior unit test for ``ContactsSyncStateStore`` means mocked-pool
tests could not have caught this bug either way — these tests exercise the real
``save``/``load`` methods against a real, migrated (``contacts`` chain) Postgres
instance (testcontainers).

Live-data audit (read-only, butlers-dev, 2026-07-05): ALL 7 pre-existing
``contacts_sync_state`` rows across every butler schema with rows (education,
general, health, home, lifestyle: 1 each; relationship: 2) have
``jsonb_typeof(contact_versions) = 'string'`` — every existing row is corrupted.
The ``_normalize_contact_versions`` read-side workaround is therefore KEPT, and
``ContactsSyncStateStore.load`` now additionally self-heals a corrupted row
back to a proper jsonb object via a guarded UPDATE (``jsonb_typeof(...) =
'string'``), matching the precedent set by PR #2925 (bu-x92jw) for the same bug
class.
"""

from __future__ import annotations

import json
import shutil

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.contacts.sync import ContactsSyncState, ContactsSyncStateStore
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the contacts chain — contacts_sync_state (public schema)."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["contacts"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE contacts_sync_state")
    yield p
    await p.close()


async def test_save_then_load_round_trips_contact_versions_as_object(
    pool: asyncpg.Pool,
) -> None:
    """save() + load() round-trip contact_versions as a jsonb OBJECT, not a
    jsonb-typed string."""
    store = ContactsSyncStateStore(pool)
    state = ContactsSyncState(
        sync_cursor="cursor-1",
        contact_versions={"contact-a": "etag:abc", "contact-b": "hash:def"},
    )

    await store.save(provider="google", account_id="alice@example.com", state=state)

    row = await pool.fetchrow(
        "SELECT contact_versions FROM contacts_sync_state WHERE provider = $1 AND account_id = $2",
        "google",
        "alice@example.com",
    )
    stored = row["contact_versions"]
    assert isinstance(stored, dict), (
        f"Expected contact_versions to be stored as a jsonb OBJECT but got "
        f"{type(stored).__name__!r}: {stored!r}"
    )
    assert stored == {"contact-a": "etag:abc", "contact-b": "hash:def"}

    loaded = await store.load(provider="google", account_id="alice@example.com")
    assert loaded.contact_versions == {"contact-a": "etag:abc", "contact-b": "hash:def"}
    assert loaded.sync_cursor == "cursor-1"


async def test_load_reconstructs_and_heals_legacy_corrupted_row(pool: asyncpg.Pool) -> None:
    """A pre-existing corrupted row (contact_versions stored as a jsonb-typed
    STRING, matching every row found in the live-data audit) is transparently
    reconstructed into a dict on read, and the guarded repair UPDATE heals it
    back into a proper jsonb object, idempotently."""
    corrupted_json_string = json.dumps({"contact-a": "etag:abc"})
    await pool.execute(
        """
        INSERT INTO contacts_sync_state (provider, account_id, contact_versions)
        VALUES ($1, $2, $3::jsonb)
        """,
        "google",
        "legacy@example.com",
        corrupted_json_string,
    )

    row = await pool.fetchrow(
        "SELECT contact_versions FROM contacts_sync_state WHERE provider = $1 AND account_id = $2",
        "google",
        "legacy@example.com",
    )
    assert isinstance(row["contact_versions"], str), (
        "Test setup sanity check: expected the hand-inserted row to reproduce "
        "the corrupted (string-typed) shape found in the live-data audit."
    )

    store = ContactsSyncStateStore(pool)
    state = await store.load(provider="google", account_id="legacy@example.com")
    assert state.contact_versions == {"contact-a": "etag:abc"}

    healed_row = await pool.fetchrow(
        "SELECT contact_versions FROM contacts_sync_state WHERE provider = $1 AND account_id = $2",
        "google",
        "legacy@example.com",
    )
    healed = healed_row["contact_versions"]
    assert isinstance(healed, dict), (
        f"Expected the guarded repair UPDATE to heal contact_versions back "
        f"into a jsonb OBJECT but got {type(healed).__name__!r}: {healed!r}"
    )
    assert healed == {"contact-a": "etag:abc"}

    # Idempotent: loading an already-healed (object-shaped) row is a no-op —
    # the jsonb_typeof guard matches zero rows, so a second load must not
    # clobber the healed value.
    state_again = await store.load(provider="google", account_id="legacy@example.com")
    assert state_again.contact_versions == {"contact-a": "etag:abc"}
    unchanged_row = await pool.fetchrow(
        "SELECT contact_versions FROM contacts_sync_state WHERE provider = $1 AND account_id = $2",
        "google",
        "legacy@example.com",
    )
    assert unchanged_row["contact_versions"] == {"contact-a": "etag:abc"}
