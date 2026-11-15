"""The public MCP proposal importer over the real two-database topology.

REQ-public-mcp-proposals-002 (conflict ordering), -004 (copy-then-delete, validation order),
-005 (one parked action per proposal), -006 (reconcile, retention) and -007 (kill switch on
the primary). The inbox is the real ``butlers_public.mcp_inbox.proposals`` read and drained as
``mcp_proposals_reader`` over TCP with a file-mounted password; the primary is ``butlers`` with
the Relationship and approvals chains, used through ``butler_relationship_rw`` as the daemon
uses it. Inbox rows are written through the projection's owner connection so each test pins
its own ``received_at``. Every instant handed to the importer is fixed.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import make_url, text

from butlers.db import register_jsonb_codec
from butlers.knowledge_read.manifest import load_manifest
from butlers.migrations import run_migrations
from butlers.migrations.public_mcp import reconcile_connection
from butlers.migrations.public_mcp_owner import render_owner_program
from butlers.modules.approvals import operations
from butlers.roster_imports import roster_module
from butlers.testing.public_mcp import public_mcp_topology

importer = roster_module("relationship", "tools/public_mcp_proposal_import")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
RECEIVED = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
SUB = "7"
# Reused idempotency keys (conflict cases); built so they read as data, not credentials.
REUSED = "-".join(("reused", "1"))
REUSED_AFTER = "-".join(("reused", "2"))
_TABLE = "relationship.external_fact_proposals"


def _wait(predicate, *, seconds=40):
    deadline = time.monotonic() + seconds
    while not (value := predicate()):
        assert time.monotonic() < deadline, "the subscriber did not reach the observation"
        time.sleep(0.05)
    return value


def _scalar(engine, sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar_one()


@pytest.fixture(scope="module")
def projected():
    with public_mcp_topology() as topology:
        projection = topology.projection_engine()
        manifest = load_manifest()
        try:
            url = topology.engine.url.render_as_string(hide_password=False)
            asyncio.run(run_migrations(url, chain="approvals", schema="relationship"))
            with topology.engine.begin() as conn:
                reconcile_connection(conn, manifest)
            result = topology.execute(render_owner_program(manifest))
            assert result.exit_code == 0, repr(result.output)[-4000:]
            _wait(
                lambda: _scalar(
                    projection,
                    "SELECT COALESCE(bool_and(copy_complete AND digest=:d), false) "
                    "FROM mcp_read.manifest_applied",
                    {"d": manifest.digest},
                )
            )
            yield topology, projection
        finally:
            projection.dispose()


@pytest.fixture
async def primary(projected):
    """The Relationship daemon's view: schema search path and its runtime role."""
    topology, _ = projected
    url = topology.engine.url

    async def as_relationship(conn):
        await conn.execute("SET ROLE butler_relationship_rw")

    pool = await asyncpg.create_pool(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database=url.database,
        min_size=1,
        max_size=4,
        server_settings={"search_path": "relationship,public"},
        init=register_jsonb_codec,
        setup=as_relationship,
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def admin(projected):
    """The migration principal on the primary, for fixtures and readback only."""
    topology, _ = projected
    url = topology.engine.url
    conn = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database=url.database,
        server_settings={"search_path": "relationship,public"},
    )
    await register_jsonb_codec(conn)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def inbox(projected, tmp_path: Path):
    """Settings the job reads from env in the pod, with a 0600 password file."""
    topology, _ = projected
    url = make_url(topology.container.get_connection_url())
    secret = tmp_path / "password"
    secret.touch(mode=0o600)
    secret.write_text(topology.role_passwords["mcp_proposals_reader"] + "\n")
    assert url.host is not None and url.port is not None
    return importer.InboxSettings(
        host=url.host, port=url.port, database="butlers_public", password_file=secret
    )


class World:
    """One test's entities, client and inbox writer."""

    def __init__(self, projected, admin):
        self.projection = projected[1]
        self.admin = admin
        self.cid = f"https://client-{uuid.uuid4().hex[:10]}.example/meta"

    async def person(self, *, roles=None, metadata=None) -> uuid.UUID:
        return await self.admin.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type, roles, metadata) "
            "VALUES ('Synthetic person ' || gen_random_uuid(), 'person', $1, $2) "
            "RETURNING id",
            roles or [],
            metadata or {},
        )

    def proposal(self, subject, obj, **over) -> dict:
        row = {
            "proposal_id": uuid.uuid4(),
            "sub": SUB,
            "cid": self.cid,
            "idempotency_key": uuid.uuid4().hex,
            "subject_entity_id": subject,
            "predicate": "works-at",
            "object_entity_id": obj,
            "evidence_url": "https://example.com/team/page",
            "note": "Listed on the team page.",
            "received_at": RECEIVED,
        }
        row.update(over)
        return row

    def send(self, *rows: dict) -> list[uuid.UUID]:
        with self.projection.begin() as conn:
            for row in rows:
                conn.execute(
                    text(
                        "INSERT INTO mcp_inbox.proposals (proposal_id, sub, cid, idempotency_key, "
                        "subject_entity_id, predicate, object_entity_id, evidence_url, note, "
                        "received_at) VALUES (:proposal_id, :sub, :cid, :idempotency_key, "
                        ":subject_entity_id, :predicate, :object_entity_id, :evidence_url, :note, "
                        ":received_at)"
                    ),
                    row,
                )
        return [row["proposal_id"] for row in rows]

    def in_inbox(self, ids) -> int:
        with self.projection.connect() as conn:
            return conn.execute(
                text("SELECT count(*) FROM mcp_inbox.proposals WHERE proposal_id = ANY(:ids)"),
                {"ids": list(ids)},
            ).scalar_one()

    async def states(self, ids) -> dict[uuid.UUID, str]:
        rows = await self.admin.fetch(
            f"SELECT proposal_id, state FROM {_TABLE} WHERE proposal_id = ANY($1::uuid[])",
            list(ids),
        )
        return {row["proposal_id"]: row["state"] for row in rows}

    async def actions(self, proposal_id) -> list[asyncpg.Record]:
        return await self.admin.fetch(
            "SELECT * FROM pending_actions WHERE deduplication_key = $1",
            f"external_mcp:{proposal_id}",
        )

    async def switch(self, domain: str, state: str) -> None:
        await self.admin.execute(
            "UPDATE mcp_surface.controls SET state = $2, changed_at = now() WHERE domain = $1",
            domain,
            state,
        )


@pytest.fixture
async def world(projected, admin):
    world = World(projected, admin)
    try:
        yield world
    finally:
        await world.switch("proposals", "enabled")
        await world.switch("all", "enabled")


async def _pass(primary, inbox, now=NOW):
    return await importer.run_public_mcp_proposal_import(primary, now=now, inbox=inbox)


# --- Migration shape --------------------------------------------------------------------


async def test_payload_columns_are_additive_and_bounded(admin) -> None:
    """rel_043: nullable payload mirrors the inbox bounds; a status-only row stays valid."""
    columns = {
        row["column_name"]: row["is_nullable"]
        for row in await admin.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'relationship' AND table_name = 'external_fact_proposals'"
        )
    }
    for name in (
        "sub",
        "cid",
        "idempotency_key",
        "subject_entity_id",
        "predicate",
        "object_entity_id",
        "received_at",
        "imported_at",
        "pending_action_id",
    ):
        assert columns[name] == "YES", name
    status_only = uuid.uuid4()
    await admin.execute(f"INSERT INTO {_TABLE} (proposal_id) VALUES ($1)", status_only)
    try:
        for statement in (
            f"UPDATE {_TABLE} SET idempotency_key = repeat('k', 65) WHERE proposal_id = $1",
            f"UPDATE {_TABLE} SET evidence_url = repeat('u', 2049) WHERE proposal_id = $1",
            f"UPDATE {_TABLE} SET sub = '7' WHERE proposal_id = $1",
            f"UPDATE {_TABLE} SET state = 'parked' WHERE proposal_id = $1",
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                await admin.execute(statement, status_only)
    finally:
        await admin.execute(f"DELETE FROM {_TABLE} WHERE proposal_id = $1", status_only)


# --- Copy, then delete (REQ-public-mcp-proposals-004) ------------------------------------


async def test_valid_proposal_parks_exactly_one_action(primary, inbox, world) -> None:
    """REQ-public-mcp-proposals-005: valid -> parked with exactly one pending action."""
    a, b = await world.person(), await world.person()
    row = world.proposal(a, b)
    (pid,) = world.send(row)

    report = await _pass(primary, inbox)

    assert report["switch_enabled"] is True and report["intake"] == "copied"
    assert world.in_inbox([pid]) == 0
    stored = await world.admin.fetchrow(f"SELECT * FROM {_TABLE} WHERE proposal_id = $1", pid)
    assert stored["state"] == "parked" and stored["decided_at"] is None
    assert stored["imported_at"] == NOW and stored["received_at"] == RECEIVED
    assert (stored["sub"], stored["cid"], stored["subject_entity_id"]) == (SUB, world.cid, a)
    (action,) = await world.actions(pid)
    assert stored["pending_action_id"] == action["id"]
    assert action["status"] == "pending" and action["tool_name"] == "relationship_assert_fact"
    assert action["expires_at"] - action["requested_at"] == timedelta(days=14)
    # The pending action carries no outside text: not the note, not the URL path.
    flat = json.dumps({k: str(v) for k, v in dict(action).items()})
    assert "team page" not in flat and "/team/page" not in flat
    refs = {item["ref"] for item in action["evidence"]}
    assert "external_mcp:url_host=https://example.com" in refs


async def test_crash_between_copy_and_delete_creates_nothing_twice(primary, inbox, world) -> None:
    """REQ-public-mcp-proposals-004 'Crash between copy and delete is safe'."""
    a, b = await world.person(), await world.person()
    ids = world.send(world.proposal(a, b), world.proposal(b, a, predicate="knows"))
    reader = await asyncpg.connect(
        host=inbox.host,
        port=inbox.port,
        user="mcp_proposals_reader",
        password=inbox.read_password(),
        database="butlers_public",
    )
    try:
        rows = await reader.fetch(
            "SELECT * FROM mcp_inbox.proposals WHERE proposal_id = ANY($1::uuid[])", ids
        )
    finally:
        await reader.close()
    # The pass that "crashed": the copy committed, the delete never ran.
    assert await importer.copy_to_primary(primary, rows, NOW) == 2
    assert world.in_inbox(ids) == 2

    await _pass(primary, inbox)
    assert world.in_inbox(ids) == 0
    assert await world.states(ids) == dict.fromkeys(ids, "parked")
    for pid in ids:
        assert len(await world.actions(pid)) == 1

    # Re-import: an identical retry reaches the inbox again after import.
    world.send(*(dict(r) for r in rows))
    report = await _pass(primary, inbox)
    assert report["already_present"] >= 2 and world.in_inbox(ids) == 0
    for pid in ids:
        assert len(await world.actions(pid)) == 1
    assert (
        await world.admin.fetchval(
            f"SELECT count(*) FROM {_TABLE} WHERE proposal_id = ANY($1::uuid[])", ids
        )
        == 2
    )


async def test_a_failed_copy_keeps_the_inbox_rows(primary, inbox, world, monkeypatch) -> None:
    """Delete follows a committed copy only; a copy failure drains nothing."""
    a, b = await world.person(), await world.person()
    ids = world.send(world.proposal(a, b))

    async def refuse(*_args, **_kwargs):
        raise asyncpg.PostgresConnectionError("primary unavailable")

    monkeypatch.setattr(importer, "copy_to_primary", refuse)
    report = await _pass(primary, inbox)
    monkeypatch.undo()

    assert report["intake"] == "failed" and report["errors"]["intake"] == 1
    assert world.in_inbox(ids) == 1
    await _pass(primary, inbox)
    assert world.in_inbox(ids) == 0
    assert await world.states(ids) == {ids[0]: "parked"}


async def test_without_a_password_intake_is_skipped_and_the_rest_runs(
    primary, inbox, world, tmp_path
) -> None:
    """An unmounted Secret skips intake visibly; reconcile still runs."""
    a, b = await world.person(), await world.person()
    (pid,) = world.send(world.proposal(a, b))
    await _pass(primary, inbox)
    (action,) = await world.actions(pid)
    await world.admin.execute(
        "UPDATE pending_actions SET status = 'rejected', decided_at = $2 WHERE id = $1",
        action["id"],
        NOW,
    )
    missing = importer.InboxSettings(
        host=inbox.host,
        port=inbox.port,
        database=inbox.database,
        password_file=tmp_path / "absent",
    )
    (late,) = world.send(world.proposal(b, a, predicate="knows"))

    report = await importer.run_public_mcp_proposal_import(primary, now=NOW, inbox=missing)

    assert report["intake"] == "skipped_no_credential"
    assert world.in_inbox([late]) == 1
    assert await world.states([pid]) == {pid: "owner_rejected"}
    await _pass(primary, inbox)
    assert world.in_inbox([late]) == 0


# --- Validation order (REQ-public-mcp-proposals-004, -002) -------------------------------


async def test_each_validation_outcome(primary, inbox, world) -> None:
    owner = await world.person(roles=["owner"])
    a, b, c = await world.person(), await world.person(), await world.person()
    merged = await world.person(metadata={"merged_into": str(a)})
    deleted = await world.person(metadata={"deleted_at": "2026-10-01T00:00:00Z"})
    await world.admin.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, "
        "validity) VALUES ($1, 'works-at', $2, 'entity', 'test', 'active')",
        a,
        str(c),
    )
    cases = {
        "merged subject": (world.proposal(merged, b), "invalid"),
        "deleted object": (world.proposal(a, deleted), "invalid"),
        "owner subject": (world.proposal(owner, b), "invalid"),
        "missing object": (world.proposal(a, uuid.uuid4()), "invalid"),
        "family predicate": (world.proposal(a, b, predicate="parent-of"), "invalid"),
        "secret note": (
            world.proposal(a, b, note="token ghp_" + "A1b2C3d4E5" * 4),
            "invalid",
        ),
        "secret url": (
            world.proposal(a, b, evidence_url="https://example.com/p?access_token=abc"),
            "invalid",
        ),
        "non-http url": (world.proposal(a, b, evidence_url="ftp://example.com/x"), "invalid"),
        "identical active fact": (world.proposal(a, c), "duplicate"),
        "owner as object": (world.proposal(a, owner, predicate="knows"), "parked"),
    }
    ids = world.send(*(row for row, _ in cases.values()))

    report = await _pass(primary, inbox)

    states = await world.states(ids)
    for (name, (_row, expected)), pid in zip(cases.items(), ids, strict=True):
        assert states[pid] == expected, name
        actions = await world.actions(pid)
        assert len(actions) == (1 if expected == "parked" else 0), name
    decided = await world.admin.fetch(
        f"SELECT decided_at FROM {_TABLE} WHERE proposal_id = ANY($1::uuid[]) "
        "AND state <> 'parked'",
        ids,
    )
    assert {row["decided_at"] for row in decided} == {NOW}
    assert report["outcomes"]["invalid"] >= 8


async def test_invalid_wins_over_later_checks(primary, inbox, world) -> None:
    """The first failing check is recorded: a secret note on a duplicate is invalid."""
    a, b = await world.person(), await world.person()
    await world.admin.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, "
        "validity) VALUES ($1, 'works-at', $2, 'entity', 'test', 'active')",
        a,
        str(b),
    )
    (pid,) = world.send(world.proposal(a, b, note="key " + "AKIA" + "ABCDEFGHIJKLMNOP"))
    await _pass(primary, inbox)
    assert await world.states([pid]) == {pid: "invalid"}


async def test_both_payloads_in_one_pass_the_earliest_proceeds(primary, inbox, world) -> None:
    """REQ-public-mcp-proposals-002 'Both payloads arrive in one pass'."""
    a, b, c = await world.person(), await world.person(), await world.person()
    first = world.proposal(a, b, idempotency_key=REUSED)
    # Same receipt instant: proposal_id breaks the tie, so the smaller id is earliest.
    changed = world.proposal(a, c, idempotency_key=REUSED)
    early, late = sorted((first, changed), key=lambda r: r["proposal_id"])
    later_still = world.proposal(
        a, b, idempotency_key=REUSED, received_at=RECEIVED + timedelta(seconds=1)
    )
    # Sent in reverse, so inbox order is not insertion order.
    world.send(later_still, late, early)

    await _pass(primary, inbox)

    states = await world.states([early["proposal_id"], late["proposal_id"]])
    assert states == {early["proposal_id"]: "parked", late["proposal_id"]: "conflict"}
    assert await world.states([later_still["proposal_id"]]) == {
        later_still["proposal_id"]: "conflict"
    }
    assert len(await world.actions(late["proposal_id"])) == 0


async def test_a_reused_key_after_import_is_a_conflict(primary, inbox, world) -> None:
    """REQ-public-mcp-proposals-002 'Reused key with a changed payload is a conflict'."""
    a, b, c = await world.person(), await world.person(), await world.person()
    (first,) = world.send(world.proposal(a, b, idempotency_key=REUSED_AFTER))
    await _pass(primary, inbox)
    (second,) = world.send(
        world.proposal(
            a, c, idempotency_key=REUSED_AFTER, received_at=RECEIVED + timedelta(minutes=10)
        )
    )
    await _pass(primary, inbox)
    assert await world.states([first, second]) == {first: "parked", second: "conflict"}


async def test_twenty_per_day_per_client(primary, inbox, world) -> None:
    """REQ-public-mcp-proposals-004: the 21st proposal of one UTC day is rate_limited."""
    people = [await world.person() for _ in range(22)]
    rows = [
        world.proposal(people[0], people[i + 1], received_at=RECEIVED + timedelta(seconds=i))
        for i in range(21)
    ]
    # The next UTC day starts a fresh count.
    tomorrow = world.proposal(
        people[0],
        people[21],
        received_at=RECEIVED.replace(hour=0) + timedelta(days=1, minutes=1),
    )
    ids = world.send(*rows, tomorrow)

    await _pass(primary, inbox, now=NOW + timedelta(days=1))

    states = await world.states(ids)
    assert [states[pid] for pid in ids[:20]] == ["parked"] * 20
    assert states[ids[20]] == "rate_limited"
    assert states[ids[21]] == "parked"


async def test_fifty_unresolved_per_client(primary, inbox, world) -> None:
    """REQ-public-mcp-proposals-004: 50 parked and undecided -> the next is rate_limited."""
    people = [await world.person() for _ in range(53)]
    day = timedelta(days=1)
    rows = [
        world.proposal(
            people[0], people[i + 1], received_at=RECEIVED - (i // 20) * day + timedelta(seconds=i)
        )
        for i in range(51)
    ]
    ids = world.send(*rows)
    await _pass(primary, inbox)
    states = await world.states(ids)
    assert sorted(states.values()).count("parked") == 50
    limited = [pid for pid in ids if states[pid] == "rate_limited"]
    assert len(limited) == 1

    # Deciding one frees a slot for the next proposal.
    parked = next(pid for pid in ids if states[pid] == "parked")
    (action,) = await world.actions(parked)
    await operations.reject_action(primary, str(action["id"]), reason="not true")
    await _pass(primary, inbox)
    # Next UTC day, so the day cap (20 already on day 0) does not decide it.
    (late,) = world.send(world.proposal(people[0], people[52], received_at=RECEIVED + day))
    await _pass(primary, inbox, now=NOW + day)
    assert await world.states([late]) == {late: "parked"}


# --- Kill switch (REQ-public-mcp-proposals-007) ------------------------------------------


async def test_disabled_switch_keeps_ten_valid_proposals_unparked(primary, inbox, world) -> None:
    a = await world.person()
    targets = [await world.person() for _ in range(10)]
    ids = world.send(*(world.proposal(a, t) for t in targets))
    await world.switch("proposals", "disabled")

    report = await _pass(primary, inbox)

    assert report["switch_enabled"] is False
    assert world.in_inbox(ids) == 0, "intake still drains the inbox while disabled"
    assert await world.states(ids) == dict.fromkeys(ids, "imported")
    for pid in ids:
        assert await world.actions(pid) == []

    await world.switch("proposals", "enabled")
    await world.switch("all", "disabled")
    await _pass(primary, inbox)
    assert await world.states(ids) == dict.fromkeys(ids, "imported")

    await world.switch("all", "enabled")
    await _pass(primary, inbox)
    assert await world.states(ids) == dict.fromkeys(ids, "parked")
    for pid in ids:
        assert len(await world.actions(pid)) == 1


async def test_the_switch_is_read_on_the_primary(primary, inbox, world, projected) -> None:
    """'The importer reads the replicated switch at its source' with replication stalled."""
    _, projection = projected
    a, b = await world.person(), await world.person()
    (pid,) = world.send(world.proposal(a, b))
    # Earlier tests' re-enables must have replicated before replication stalls.
    _wait(
        lambda: _scalar(
            projection,
            "SELECT count(*) = 0 FROM mcp_read.mcp_surface__controls "
            "WHERE fields->>'state' <> 'enabled'",
        )
    )
    with projection.begin() as conn:
        conn.exec_driver_sql("ALTER SUBSCRIPTION butlers_public_sub DISABLE")
    try:
        await world.switch("proposals", "disabled")
        assert (
            _scalar(
                projection,
                "SELECT fields->>'state' FROM mcp_read.mcp_surface__controls "
                "WHERE fields->>'domain' = 'proposals'",
            )
            == "enabled"
        )
        await _pass(primary, inbox)
        assert await world.states([pid]) == {pid: "imported"}
    finally:
        with projection.begin() as conn:
            conn.exec_driver_sql("ALTER SUBSCRIPTION butlers_public_sub ENABLE")
    await world.switch("proposals", "enabled")
    await _pass(primary, inbox)
    assert await world.states([pid]) == {pid: "parked"}


async def test_parked_actions_stay_decidable_while_disabled(primary, inbox, world) -> None:
    a, b = await world.person(), await world.person()
    (pid,) = world.send(world.proposal(a, b))
    await _pass(primary, inbox)
    (action,) = await world.actions(pid)
    await world.switch("proposals", "disabled")

    decided = await operations.reject_action(primary, str(action["id"]), reason="no")
    assert "error" not in decided
    await _pass(primary, inbox)

    assert await world.states([pid]) == {pid: "owner_rejected"}


# --- Reconcile and retention (REQ-public-mcp-proposals-006) ------------------------------


async def _parked(world, primary, inbox) -> tuple[uuid.UUID, uuid.UUID]:
    a, b = await world.person(), await world.person()
    (pid,) = world.send(world.proposal(a, b))
    await _pass(primary, inbox)
    (action,) = await world.actions(pid)
    return pid, action["id"]


async def test_reconcile_maps_each_owner_outcome(primary, inbox, world) -> None:
    executed, executed_action = await _parked(world, primary, inbox)
    rejected, rejected_action = await _parked(world, primary, inbox)
    failed, failed_action = await _parked(world, primary, inbox)
    waiting, _ = await _parked(world, primary, inbox)
    await world.admin.execute(
        "UPDATE pending_actions SET status = 'executed', decided_at = $2, "
        "execution_result = '{\"success\": true}'::jsonb WHERE id = $1",
        executed_action,
        NOW,
    )
    await world.admin.execute(
        "UPDATE pending_actions SET status = 'rejected', decided_at = $2 WHERE id = $1",
        rejected_action,
        NOW,
    )
    await world.admin.execute(
        "UPDATE pending_actions SET status = 'approved', decided_at = $2 WHERE id = $1",
        failed_action,
        NOW,
    )
    await world.admin.execute(
        "INSERT INTO approval_events (action_id, event_type, actor, occurred_at) "
        "VALUES ($1, 'action_execution_failed', 'system:executor', $2)",
        failed_action,
        NOW,
    )
    later = NOW + timedelta(hours=1)

    report = await _pass(primary, inbox, now=later)

    assert await world.states([executed, rejected, failed, waiting]) == {
        executed: "applied",
        rejected: "owner_rejected",
        failed: "apply_failed",
        waiting: "parked",
    }
    decided = await world.admin.fetch(
        f"SELECT proposal_id, decided_at FROM {_TABLE} WHERE proposal_id = ANY($1::uuid[])",
        [executed, rejected, failed, waiting],
    )
    assert {r["proposal_id"]: r["decided_at"] for r in decided} == {
        executed: later,
        rejected: later,
        failed: later,
        waiting: None,
    }
    assert report["reconciled"]["apply_failed"] >= 1

    # A failed execution stays retryable; a later success is reported as applied.
    await world.admin.execute(
        "UPDATE pending_actions SET status = 'executed', "
        "execution_result = '{\"success\": true}'::jsonb WHERE id = $1",
        failed_action,
    )
    await _pass(primary, inbox, now=later + timedelta(hours=1))
    assert await world.states([failed]) == {failed: "applied"}


async def test_silence_expires_and_denies(primary, inbox, world) -> None:
    """'Silence denies': the importer expires its own unanswered action at expires_at."""
    pid, action_id = await _parked(world, primary, inbox)
    expires_at = await world.admin.fetchval(
        "SELECT expires_at FROM pending_actions WHERE id = $1", action_id
    )
    await _pass(primary, inbox, now=expires_at - timedelta(minutes=1))
    assert await world.states([pid]) == {pid: "parked"}

    after = expires_at + timedelta(minutes=1)
    await _pass(primary, inbox, now=after)

    assert (
        await world.admin.fetchval("SELECT status FROM pending_actions WHERE id = $1", action_id)
        == "expired"
    )
    row = await world.admin.fetchrow(
        f"SELECT state, decided_at FROM {_TABLE} WHERE proposal_id = $1", pid
    )
    assert (row["state"], row["decided_at"]) == ("expired", after)


async def test_retention_clears_outside_text_after_thirty_days(primary, inbox, world) -> None:
    """'Terminal proposals lose their outside text' at 31 days, not at 29."""
    a, b = await world.person(), await world.person()
    (pid,) = world.send(world.proposal(a, b, predicate="parent-of"))
    await _pass(primary, inbox)
    before = await world.admin.fetchrow(
        f"SELECT proposal_id, state, decided_at, received_at, imported_at FROM {_TABLE} "
        "WHERE proposal_id = $1",
        pid,
    )
    assert before["state"] == "invalid" and before["decided_at"] == NOW

    await _pass(primary, inbox, now=NOW + timedelta(days=29))
    kept = await world.admin.fetchrow(
        f"SELECT note, evidence_url FROM {_TABLE} WHERE proposal_id = $1", pid
    )
    assert kept["note"] is not None and kept["evidence_url"] is not None

    await _pass(primary, inbox, now=NOW + timedelta(days=31))
    after = await world.admin.fetchrow(f"SELECT * FROM {_TABLE} WHERE proposal_id = $1", pid)
    assert after["note"] is None and after["evidence_url"] is None
    assert {k: after[k] for k in before.keys()} == dict(before)


# --- rel_043 downgrade (last: it needs the table's rows) ---------------------------------


class _CollectingOp:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(str(statement))


def _rel_043(direction: str) -> list[str]:
    from butlers.testing.migration import load_migration

    module = load_migration(
        Path(str(importer.__file__)).parents[1]
        / "migrations"
        / "043_external_fact_proposal_import.py"
    )
    assert (module.revision, module.down_revision) == ("rel_043", "rel_042")
    op = _CollectingOp()
    module.__dict__["op"] = op
    getattr(module, direction)()
    return op.statements


async def test_rel_043_downgrade_refuses_imported_proposals_then_round_trips(admin) -> None:
    downgrade, upgrade = _rel_043("downgrade"), _rel_043("upgrade")
    assert await admin.fetchval(f"SELECT count(*) FROM {_TABLE} WHERE sub IS NOT NULL") > 0
    with pytest.raises(asyncpg.RaiseError, match="imported proposals exist"):
        async with admin.transaction():
            for statement in downgrade:
                await admin.execute(statement)

    columns = (
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'relationship' "
        "AND table_name = 'external_fact_proposals' AND column_name = 'pending_action_id'"
    )
    transaction = admin.transaction()
    await transaction.start()
    try:
        await admin.execute(f"DELETE FROM {_TABLE}")
        for statement in downgrade:
            await admin.execute(statement)
        assert await admin.fetchval(columns) == 0
        for statement in upgrade:
            await admin.execute(statement)
        assert await admin.fetchval(columns) == 1
    finally:
        await transaction.rollback()
