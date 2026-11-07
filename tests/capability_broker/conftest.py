"""The migrated broker database shared by the ledger and typed-boundary suites."""

from __future__ import annotations

import asyncio

import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.capability_broker.helpers import BROKER_DOMAIN


@pytest.fixture(scope="module")
def broker_db_url(postgres_container):
    """sw_047+ ledger plus the owning domain's core and approvals chains."""
    from butlers.migrations import run_migrations

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard", "relationship"],
        schemas={
            "core": "switchboard",
            "switchboard": "switchboard",
            "relationship": "relationship",
        },
    )
    for chain in ("core", "approvals"):
        asyncio.run(run_migrations(url, chain=chain, schema=BROKER_DOMAIN))
    return url
