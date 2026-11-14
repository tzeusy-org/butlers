"""The legacy parity runner against the real context bus and perception store (PR7).

Real migrated core under the repository's privileged test bootstrap, in one
transaction that is rolled back. The legacy side is the real
``public.user_context`` read; the perception side runs under the real
``perception_owner_reader`` role switched with ``SET LOCAL ROLE`` by the runner
itself (``situation_read`` refuses any other role). No perception profile is
configured, so every kind reads ``unconfigured``: the receipts must say the
legacy bus asserts what perception cannot yet confirm, and carry no room name.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.perception import flags
from butlers.perception.legacy_parity import LegacyParity, log_line
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.perception.factories import OWNER

pytestmark = [pytest.mark.integration, pytest.mark.db]

ROOM = "Master Bedroom"


def _bootstrap_url(container):
    name = migration_db_name()
    # The same bootstrap the owner.mode projector's DB test uses.
    create_migrated_test_db(
        container,
        name,
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
    )
    return migration_bootstrap_db_url(container, name)


class _Reader:
    """The test's own connection as the runner's reader; closing is left to the test."""

    def __init__(self, connection):
        self.connection = connection

    def transaction(self):
        return self.connection.transaction()

    async def execute(self, sql):
        return await self.connection.execute(sql)

    async def fetchval(self, sql, *args):
        return await self.connection.fetchval(sql, *args)

    async def close(self):
        return None


async def _exercise(url):
    conn = await asyncpg.connect("postgresql://" + url.split("://", 1)[1])
    await register_jsonb_codec(conn)
    tx = conn.transaction()
    await tx.start()
    try:
        now = await conn.fetchval("SELECT now()")
        await conn.execute(
            "INSERT INTO public.entities(id, canonical_name, entity_type, roles) "
            "VALUES ($1, 'opaque test owner', 'person', ARRAY['owner'])",
            OWNER,
        )
        for signal, value, butler in (("meeting", None, "general"), ("in_space", ROOM, "home")):
            await conn.execute(
                "INSERT INTO public.user_context(signal_type, value, set_by_butler, expires_at) "
                "VALUES ($1, $2, $3, $4)",
                signal,
                value,
                butler,
                now + timedelta(hours=1),
            )

        async def reader():
            return _Reader(conn)

        receipts = await LegacyParity(conn, subject=OWNER, open_reader=reader).run(as_of=now)
        by_kind = {r.kind: r for r in receipts}
        assert by_kind["owner.activity"].legacy == ("meeting",)
        assert by_kind["owner.place"].legacy == ("inside",)
        assert by_kind["owner.suppression"].legacy == ("off",)
        for receipt in receipts:
            assert receipt.perception_state == "unconfigured"
            assert (receipt.agree, receipt.reason) == (False, "legacy_only")
        assert ROOM not in repr(receipts) and ROOM not in log_line(receipts)
        # The runner, not the test, switched to the owner reader role.
        assert await conn.fetchval("SELECT current_user") == "perception_owner_reader"
    finally:
        await tx.rollback()
        await conn.close()


def test_the_runner_compares_the_real_bus_with_the_real_owner_reads(
    postgres_container, monkeypatch
):
    monkeypatch.setattr(flags, "LEGACY_PARITY_LIVE", True)
    asyncio.run(_exercise(_bootstrap_url(postgres_container)))
