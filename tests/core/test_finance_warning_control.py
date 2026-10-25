"""The fixed Finance read must bind the real invocation and its whole lifetime."""

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _boot_id():
    from butlers.core.utils import generate_uuid7_string

    return UUID(generate_uuid7_string())


def _install_publication(daemon):
    from butlers.core.catalog_publication import CatalogPublisher

    publisher = CatalogPublisher(daemon.db.pool, daemon.config.name)
    from unittest.mock import AsyncMock

    publisher.retire = AsyncMock()  # publication teardown is outside this controlled SQL owner
    publisher.revision = uuid4()
    publisher.generation_digest = daemon._tool_catalog.generation_digest
    daemon._catalog_publication = publisher
    return publisher


def _registration(daemon):
    from types import SimpleNamespace

    publisher = daemon._catalog_publication
    return SimpleNamespace(
        instance_id=publisher.instance,
        revision_id=publisher.revision,
        epoch=11,
        catalog=daemon._tool_catalog,
        body_digest="controlled-body",
        authorization_digest="controlled-auth",
    )


def _snapshot(registrations, **daemons):
    from types import SimpleNamespace

    return SimpleNamespace(
        registration=registrations.__getitem__,
        boot=lambda name: (daemons[name]._boot_instance_id, daemons[name]._boot_epoch),
    )


@pytest.fixture(autouse=True)
def _fixed_owner_calendar(monkeypatch):
    from butlers.tools.finance import budgets

    monkeypatch.setattr(budgets, "_period_anchor", lambda zone, now=None: date(2026, 10, 25))


def test_warning_tuple_is_strict_and_selected_dates_precede_window_filter():
    from butlers.core.finance_warning_control import warning_tuple
    from butlers.tools.finance.subscriptions import selected_renewal

    subject = UUID(int=1)
    assert warning_tuple(str(subject), "2026-10-25", 2) == (subject, date(2026, 10, 25), 2)
    for value in [True, -1, 2**63, "2"]:
        with pytest.raises(ValueError):
            warning_tuple(str(subject), "2026-10-25", value)
    row = {
        "next_renewal": date(2026, 12, 1),
        "field_claims": [
            {"field": "renewal", "value": "2026-10-24", "resolved": False},
            {"field": "renewal", "value": "2026-10-27", "resolved": False},
        ],
    }
    assert selected_renewal(row, date(2026, 10, 25), date(2026, 11, 8)) == date(2026, 10, 27)


async def _registered_finance(pool):
    """Real module closures and final daemon metadata; no fake tool declaration."""
    from types import SimpleNamespace

    from fastmcp import FastMCP

    from butlers.config import load_config
    from butlers.daemon import ButlerDaemon
    from butlers.modules.registry import default_registry

    root = _ROOT
    daemon = ButlerDaemon(root / "roster/finance", db=SimpleNamespace(pool=pool))
    daemon.config = load_config(daemon.config_dir)
    daemon.mcp = FastMCP("finance-control-test")
    registry = default_registry()
    daemon._modules = [registry._modules["finance"]()]
    daemon._module_configs = daemon._validate_module_configs()
    await daemon._register_module_tools()
    await daemon._finalize_tool_catalog()
    daemon._boot_epoch = 1
    _install_publication(daemon)
    return daemon


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", ["in-process", "actual-http"])
async def test_actual_registered_read_requires_pending_current_one_use_claim(monkeypatch, wire):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    from fastmcp import Client
    from starlette.applications import Starlette

    from butlers.core import finance_warning_control as control

    class Pool:
        def __init__(self):
            self.conn = SimpleNamespace(
                fetchrow=AsyncMock(
                    return_value={
                        "next_renewal": date(2026, 10, 27),
                        "date_epoch": 0,
                        "field_claims": [],
                        "status": "active",
                        "status_source": "extracted",
                    }
                )
            )
            self.acquired = self.released = 0

        async def acquire(self):
            self.acquired += 1
            return self.conn

        async def release(self, conn):
            assert conn is self.conn
            self.released += 1

    pool = Pool()
    finance = await _registered_finance(pool)
    source = SimpleNamespace(
        db=SimpleNamespace(pool=object()),
        config=SimpleNamespace(name="switchboard"),
        _tool_catalog=finance._tool_catalog,
        _boot_instance_id=_boot_id(),
        _boot_epoch=1,
    )
    _install_publication(source)
    issuer = control.FinanceWarningControlOwner(source)
    source._finance_warning_control_owner = issuer
    owner = control.FinanceWarningControlOwner(
        finance, verifier_url="http://fixed/internal/finance-warning/verify"
    )
    owner._receiver_publication_current = AsyncMock(return_value=True)
    finance._finance_warning_control_owner = owner
    registrations = {
        name: _registration(d) for name, d in [("switchboard", source), ("finance", finance)]
    }
    issuer.registrations = AsyncMock(
        return_value=_snapshot(registrations, finance=finance, switchboard=source)
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        control.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.ASGITransport(app=Starlette(routes=[issuer.route()])), **kwargs
        ),
    )
    args = {"subject": str(UUID(int=1)), "selected_date": "2026-10-27", "epoch": 0}
    token = control._pending.set("pending-opaque")
    try:
        from contextlib import AsyncExitStack

        from fastmcp.client.transports import StreamableHttpTransport

        from butlers.daemon import ButlerDaemon

        async with AsyncExitStack() as stack:
            target = finance.mcp
            if wire == "actual-http":
                app = ButlerDaemon._build_mcp_http_app(
                    finance.mcp, butler_name="finance", finance_warning_control_owner=owner
                )
                raw = app._app._app
                await stack.enter_async_context(raw.router.lifespan_context(raw))
                target = StreamableHttpTransport(
                    "http://fixed/mcp",
                    headers={control.HEADER: "pending-opaque"},
                    httpx_client_factory=lambda **kw: real_client(
                        transport=httpx.ASGITransport(app=app), **kw
                    ),
                )
            async with Client(target) as client:
                # Initialize and discovery cannot consume, query, or manufacture authority.
                assert control.TOOL in {tool.name for tool in await client.list_tools()}
                assert pool.acquired == 0
                unissued = await client.call_tool(control.TOOL, args)
                assert unissued.data is None and pool.acquired == 0
                issuer.claims["pending-opaque"] = (
                    args,
                    control._registration_binding(await issuer.registrations(), "switchboard"),
                    control._registration_binding(await issuer.registrations(), "finance"),
                    asyncio.get_running_loop().time() + 5,
                )
                bad = await client.call_tool(
                    control.TOOL, {**args, "epoch": True}, raise_on_error=False
                )
                assert bad.is_error
                assert "pending-opaque" in issuer.claims and pool.acquired == 0
                result = await client.call_tool(control.TOOL, args)
                assert result.data is True
                assert pool.acquired == pool.released == 1
                assert "pending-opaque" not in issuer.claims
                replay = await client.call_tool(control.TOOL, args)
                assert replay.data is None and pool.acquired == 1
                issuer.claims["pending-opaque"] = (
                    args,
                    control._registration_binding(await issuer.registrations(), "switchboard"),
                    control._registration_binding(await issuer.registrations(), "finance"),
                    asyncio.get_running_loop().time() + 5,
                )
                finance._boot_epoch += 1
                changed = await client.call_tool(control.TOOL, args)
                assert changed.data is None and pool.acquired == 1
    finally:
        control._pending.reset(token)
        await owner.close(1)
        await issuer.close(1)
        assert not owner.tasks and not owner.children and control._pending.get() is None


@pytest.mark.asyncio
async def test_native_pool_release_is_retained_across_deadline_repeat_cancel_and_shutdown(
    monkeypatch,
):
    """Use installed Pool.release AND its holder/reset; no network or subclassed Pool."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import asyncpg

    from butlers.config import load_config
    from butlers.core import finance_warning_control as control
    from butlers.daemon import ButlerDaemon
    from butlers.db import Database
    from butlers.lifecycle import run_shutdown

    reset_entered, release_reset = asyncio.Event(), asyncio.Event()
    pool = await asyncpg.create_pool(min_size=0, max_size=1)
    holder = pool._holders[0]

    class Connection:
        _protocol = SimpleNamespace(queries_count=0, _is_cancelling=lambda: False)
        closed = False

        def _set_proxy(self, proxy):
            self.proxy = proxy

        def is_closed(self):
            return self.closed

        def _on_release(self):
            pass

        async def reset(self, **kwargs):
            reset_entered.set()
            await release_reset.wait()

        async def close(self, **kwargs):
            self.closed = True
            holder._release_on_close()

        def terminate(self):
            self.closed = True
            holder._release_on_close()

    holder._con = Connection()
    holder._generation = pool._generation
    root = _ROOT
    db = Database(db_name="software-only", schema="finance")
    db.pool = pool
    daemon = ButlerDaemon(root / "roster/finance", db=db)
    daemon.config = load_config(daemon.config_dir)
    daemon.config.shutdown_timeout_s = 0.01
    daemon._disconnect_switchboard = AsyncMock()
    owner = control.FinanceWarningControlOwner(daemon)
    daemon._finance_warning_control_owner = owner
    errors = []

    async def operation():
        try:
            async with owner.connection():
                return True
        except Exception as exc:
            errors.append(repr(exc))
            raise

    deadline = asyncio.get_running_loop().time() + 0.025
    caller = asyncio.create_task(owner._bounded(operation, deadline))
    try:
        await asyncio.wait_for(reset_entered.wait(), 1)
        assert not errors, errors
        assert await asyncio.wait_for(caller, 1) is None
        assert owner.tasks and owner.children and owner.quarantined
        assert not holder._in_use.done() and pool._queue.qsize() == 0
        # Repeated shutdown cancellation never cancels native holder/reset a second time.
        shutdown = asyncio.create_task(run_shutdown(daemon))
        await asyncio.sleep(0)
        shutdown.cancel()
        shutdown.cancel()
        await asyncio.sleep(0.03)  # beyond the unchanged shared shutdown budget
        assert not pool.is_closing()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(shutdown, 1)
        assert daemon._chronicler_cleanup_task and not daemon._chronicler_cleanup_task.done()
        assert db.pool is pool and not pool.is_closing() and not holder._con.closed
        assert await owner._bounded(operation, asyncio.get_running_loop().time() + 1) is None
        release_reset.set()
        await asyncio.wait_for(daemon._chronicler_cleanup_task, 1)
        assert pool.is_closing() and not owner.tasks and not owner.children
        assert control.lookup_owner(pool) is None
    finally:
        release_reset.set()
        await owner.close(1)
        if vars(daemon).get("_chronicler_cleanup_task"):
            await asyncio.gather(daemon._chronicler_cleanup_task, return_exceptions=True)
        if "shutdown" in locals():
            await asyncio.gather(shutdown, return_exceptions=True)
        if vars(daemon).get("_catalog_shutdown_task"):
            await asyncio.gather(daemon._catalog_shutdown_task, return_exceptions=True)
        await pool.close()


def test_claims_keep_baseline_complete_pairs_history_and_explicit_owner_epochs():
    from decimal import Decimal

    from butlers.tools.finance.expected_signals import FinanceSignalSource
    from butlers.tools.finance.subscriptions import merge_field_claims, selected_renewal

    baseline = {
        "amount": Decimal("10.00"),
        "currency": "USD",
        "next_renewal": date(2026, 12, 1),
        "renewal_source": "owner",
        "price_source": "owner",
        "date_epoch": 2,
        "price_epoch": 3,
        "field_claims": [],
    }
    source = FinanceSignalSource("connector:gmail", "gmail:synthetic")
    state = merge_field_claims(baseline, date(2026, 10, 27), 99, "EUR", False, source)
    assert (state["amount"], state["currency"], state["next_renewal"]) == (
        Decimal("10"),
        "USD",
        date(2026, 12, 1),
    )
    assert state["date_epoch"] == 2 and state["price_epoch"] == 3
    assert state["field_claims"][1]["value"] == {"amount": "99", "currency": "EUR"}
    replay = merge_field_claims(state, date(2026, 10, 27), 99, "EUR", False, source)
    assert replay == state and baseline["field_claims"] == []
    assert selected_renewal(state, date(2026, 10, 25), date(2026, 11, 8)) == date(2026, 10, 27)
    confirmed = merge_field_claims(
        state, date(2026, 11, 1), 12, "USD", True, FinanceSignalSource("owner")
    )
    assert (confirmed["date_epoch"], confirmed["price_epoch"]) == (3, 4)
    assert all(claim["resolved"] for claim in confirmed["field_claims"])
    assert [c["value"] for c in state["field_claims"]] == [
        c["value"] for c in confirmed["field_claims"]
    ]
    assert selected_renewal(confirmed, date(2026, 10, 25), date(2026, 11, 8)) == date(2026, 11, 1)
    same = merge_field_claims(baseline, baseline["next_renewal"], 10.0, "USD", False, source)
    assert same["field_claims"] == []


@pytest.mark.asyncio
async def test_registered_storage_attestation_lock_and_write_share_committing_connection(
    monkeypatch,
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from butlers.tools.finance import subscriptions
    from butlers.tools.finance.expected_signals import FinanceSignalSource

    events = []
    row = {
        "id": UUID(int=9),
        "amount": 10,
        "currency": "USD",
        "next_renewal": date(2026, 12, 1),
        "date_epoch": 0,
        "price_epoch": 0,
        "field_claims": [],
    }
    conn = SimpleNamespace()
    conn.execute = AsyncMock(side_effect=lambda *args: events.append("lock"))

    async def fetch(sql, *args):
        if sql.startswith("SELECT *"):
            assert events == ["tx", "tx", "attest", "commit", "lock"] and "FOR UPDATE" in sql
            return row
        events.append("write")
        assert args[0:3] == (10, "USD", date(2026, 12, 1))
        assert args[14][0]["value"] == "2026-10-27"
        return {**row, "field_claims": args[14]}

    conn.fetchrow = fetch

    @asynccontextmanager
    async def transaction():
        events.append("tx")
        yield
        events.append("commit")

    conn.transaction = transaction

    @asynccontextmanager
    async def acquire():
        yield conn
        events.append("release")

    pool = SimpleNamespace(acquire=acquire)

    async def source(connection):
        assert connection is conn
        events.append("attest")
        return FinanceSignalSource("connector:gmail", "gmail:synthetic")

    monkeypatch.setattr(subscriptions, "runtime_signal_source", source)
    result = await subscriptions.track_subscription(
        pool,
        "Synthetic",
        99,
        "EUR",
        "yearly",
        "2026-10-27",
        metadata={"expected_signal_source": {"producer": "owner"}},
    )
    assert events == ["tx", "tx", "attest", "commit", "lock", "write", "commit", "release"]
    assert result["field_claims"][0]["source"]["producer"] == "connector:gmail"


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["normal", "deadline", "repeated-cancel"])
async def test_actual_route_forces_current_receiver_even_cutover_off_and_binds_headers(
    monkeypatch, cleanup
):
    import asyncio
    import importlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    from fastmcp import Client
    from starlette.applications import Starlette

    from butlers.core import finance_warning_control as control

    route_mod = importlib.import_module("butlers.tools.switchboard.routing.route")
    pool = SimpleNamespace(fetchrow=AsyncMock(return_value=None), execute=AsyncMock())
    finance_pool = SimpleNamespace(acquire=AsyncMock(), release=AsyncMock())
    finance_pool.acquire.return_value = SimpleNamespace(
        fetchrow=AsyncMock(
            return_value={
                "next_renewal": date(2026, 10, 27),
                "date_epoch": 0,
                "field_claims": [],
                "status": "active",
                "status_source": "extracted",
            }
        )
    )
    finance = await _registered_finance(finance_pool)
    source = SimpleNamespace(
        db=SimpleNamespace(pool=pool),
        config=SimpleNamespace(name="switchboard"),
        _tool_catalog=finance._tool_catalog,
        _boot_instance_id=_boot_id(),
        _boot_epoch=1,
    )
    _install_publication(source)
    owner = control.FinanceWarningControlOwner(source, route_call=route_mod.route)
    source._finance_warning_control_owner = owner
    receiver = control.FinanceWarningControlOwner(
        finance, verifier_url="http://fixed" + control.VERIFY_PATH
    )
    receiver._receiver_publication_current = AsyncMock(return_value=True)
    finance._finance_warning_control_owner = receiver
    registrations = {
        name: _registration(d) for name, d in [("finance", finance), ("switchboard", source)]
    }
    owner.registrations = AsyncMock(
        return_value=_snapshot(registrations, finance=finance, switchboard=source)
    )
    actual_http = httpx.AsyncClient
    monkeypatch.setattr(
        control.httpx,
        "AsyncClient",
        lambda **kw: actual_http(
            transport=httpx.ASGITransport(app=Starlette(routes=[owner.route()])), **kw
        ),
    )
    decision = SimpleNamespace(
        state="ready",
        endpoint_url="http://fixed/mcp",
        boot_instance_id=finance._boot_instance_id,
        boot_epoch=finance._boot_epoch,
    )
    current = AsyncMock(return_value=(decision, "ready", False))
    monkeypatch.setattr(route_mod, "_resolve_receiver_route_target", current)
    legacy = AsyncMock(side_effect=AssertionError("legacy route must not authorize fixed read"))
    monkeypatch.setattr(route_mod, "resolve_routing_target", legacy)
    monkeypatch.setattr(route_mod, "receiver_route_cutover_enabled", lambda: False)
    monkeypatch.setattr(route_mod, "_log_routing", AsyncMock())

    # Real fresh FastMCP initialize/list/call/close; substitute only the HTTP wire
    # with an in-process transport, carrying the exact projected pending header.
    close_entered, release_close = asyncio.Event(), asyncio.Event()

    class HeldClient(Client):
        async def __aexit__(self, *exc):
            if cleanup != "normal":
                close_entered.set()
                while not release_close.is_set():
                    try:
                        await release_close.wait()
                    except asyncio.CancelledError:
                        pass  # deliberately incomplete actual MCP-client close
            return await super().__aexit__(*exc)

    @asynccontextmanager
    async def client(transport):
        token = control._pending.set(transport.headers[control.HEADER])
        try:
            prior_queries = finance_pool.acquire.await_count
            async with HeldClient(finance.mcp) as peer:
                await peer.list_tools()
                assert owner.claims and finance_pool.acquire.await_count == prior_queries
                yield peer
        finally:
            control._pending.reset(token)

    monkeypatch.setattr(route_mod, "MCPClient", client)
    args = {"subject": str(UUID(int=1)), "selected_date": "2026-10-27", "epoch": 0}
    try:
        if cleanup == "normal":
            assert await owner.read(args) is True
        else:
            assert control.DEADLINE_SECONDS == 5
            monkeypatch.setattr(control, "DEADLINE_SECONDS", 0.5)
            call = asyncio.create_task(owner.read(args))
            await asyncio.wait_for(close_entered.wait(), 1)
            assert finance_pool.acquire.await_count == finance_pool.release.await_count == 1
            assert not call.done()  # successful SQL is not success before MCP cleanup
            if cleanup == "repeated-cancel":
                call.cancel()
                call.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(call, 1)
            else:
                assert await asyncio.wait_for(call, 1) is None
            assert owner.quarantined and owner.tasks
            assert await owner.read(args) is None  # no reconnect/read while incomplete
            assert current.await_count == 1
            release_close.set()
            await asyncio.wait_for(owner.settle(), 1)
            assert not owner.tasks and not owner.children and not owner.quarantined
        assert current.await_count == 1 and legacy.await_count == 0
        assert finance_pool.acquire.await_count == finance_pool.release.await_count == 1
        assert not owner.claims and control._pending.get() is None
        # No installed owner, held call or side effects on ordinary lookup/OFF.
        assert control.lookup_owner(object()) is None
        if cleanup == "normal":
            from butlers import scheduled_jobs
            from butlers.tools.switchboard.insight import broker

            # Actual registered daily AND urgent job -> real broker revalidation
            # -> held read -> real route/MCP/registered Finance SQL. An owner
            # confirmation after candidate persistence withdraws it before send.
            finance_pool.acquire.return_value.fetchrow.return_value["date_epoch"] = 1
            candidate = {
                "id": UUID(int=19),
                "origin_butler": "finance",
                "priority": 95,
                "dedup_key": "synthetic",
                "channel": None,
                "premise": {
                    "kind": "probe",
                    "butler": "finance",
                    "probe": control.TOOL,
                    "args": args,
                },
            }
            pool.fetch = AsyncMock(
                side_effect=lambda query, *a: [candidate] if "premise" in query else []
            )
            pool.execute.return_value = "UPDATE 1"
            notify = AsyncMock(return_value={"status": "sent"})
            monkeypatch.setattr(
                scheduled_jobs, "_build_switchboard_insight_notify_fn", lambda p: notify
            )
            monkeypatch.setattr(
                scheduled_jobs, "_build_switchboard_insight_amend_fn", lambda p: None
            )
            for name, value in {
                "get_insight_settings": {"verbosity": "normal", "custom_budget": None},
                "get_approvals_policy_quiet_hours": {
                    "quiet_start_hour": None,
                    "quiet_end_hour": None,
                    "timezone": "UTC",
                },
                "get_suppressing_context_signal": None,
                "expire_candidates": 0,
                "record_attention_event": None,
            }.items():
                monkeypatch.setattr(broker, name, AsyncMock(return_value=value))
            for name in (
                "_run_switchboard_insight_delivery_cycle_job",
                "_run_switchboard_insight_urgent_subcycle_job",
            ):
                result = await getattr(scheduled_jobs, name)(pool, None)
                assert result["withdrawn"] == 1 and result["delivered"] == []
                assert not owner.claims
            notify.assert_not_awaited()
            assert current.await_count == 3 and legacy.await_count == 0
            assert finance_pool.acquire.await_count == finance_pool.release.await_count == 3
        owner.stop_admission()
        assert await owner.read(args) is None
    finally:
        release_close.set()
        await receiver.close(1)
        await owner.close(1)
        await owner.settle()
        assert not owner.tasks and not owner.children


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored_epoch,status,origin,expected",
    [
        (1, "active", "owner", False),
        (0, "cancelled", "owner", False),
        (0, "cancelled", "extracted", True),
        (0, "active", "extracted", True),
    ],
)
async def test_actual_owning_query_rejects_confirmation_epoch_without_new_source_authority(
    stored_epoch, status, origin, expected
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from butlers.tools.finance.subscriptions import subscription_warning_still_relevant

    connection = SimpleNamespace(
        fetchrow=AsyncMock(
            return_value={
                "date_epoch": stored_epoch,
                "status": status,
                "status_source": origin,
                "next_renewal": date(2026, 10, 27),
                "field_claims": [],
            }
        ),
        fetchval=AsyncMock(return_value=None),
    )
    assert (
        await subscription_warning_still_relevant(connection, UUID(int=1), date(2026, 10, 27), 0)
        is expected
    )
    assert connection.fetchrow.call_args.args[1] == UUID(int=1)


@pytest.mark.asyncio
async def test_issuer_exact_wire_expiry_catalog_and_revocation_cannot_consume(monkeypatch):
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from butlers.core import finance_warning_control as control

    finance = await _registered_finance(object())
    source = SimpleNamespace(
        db=SimpleNamespace(pool=object()),
        config=SimpleNamespace(name="switchboard"),
        _tool_catalog=finance._tool_catalog,
        _boot_instance_id=_boot_id(),
        _boot_epoch=1,
    )
    _install_publication(source)
    owner = control.FinanceWarningControlOwner(source)
    source._finance_warning_control_owner = owner
    source_reg = _registration(source)
    receiver_reg = _registration(finance)
    owner.registrations = AsyncMock(
        return_value=_snapshot(
            {"finance": receiver_reg, "switchboard": source_reg},
            finance=finance,
            switchboard=source,
        )
    )
    snapshot = await owner.registrations()
    identity = control._registration_binding(snapshot, "switchboard")
    receiver = control._registration_binding(snapshot, "finance")
    args = {"subject": str(UUID(int=1)), "selected_date": "2026-10-27", "epoch": 0}
    body = {
        "token": "opaque",
        "args": args,
        "receiver": list(control._local_identity(receiver)),
        "nonce": "n" * 43,
    }

    def renew():
        owner.claims["opaque"] = (args, identity, receiver, asyncio.get_running_loop().time() + 5)

    try:
        renew()
        for bad in (
            {**body, "args": {**args, "epoch": False}},
            {**body, "args": {**args, "epoch": 2}},
            {**body, "receiver": [str(UUID(int=8)), 1, receiver[2]]},
            {**body, "nonce": "short"},
            {**body, "operation": "other"},
            {**body, "token": "forged"},
        ):
            assert await owner.verify_consume(bad) is None
            assert "opaque" in owner.claims
        owner.claims["opaque"] = (args, identity, receiver, asyncio.get_running_loop().time() - 1)
        prior_reads = owner.registrations.await_count
        assert await owner.verify_consume(body) is None
        assert owner.registrations.await_count == prior_reads
        renew()
        receiver_reg.catalog = replace(receiver_reg.catalog, generation_digest="changed")
        assert await owner.verify_consume(body) is None
        receiver_reg.catalog = finance._tool_catalog
        owner.stop_admission()
        assert await owner.verify_consume(body) is None and not owner.claims
    finally:
        await owner.close(1)


@pytest.mark.asyncio
async def test_actual_native_pool_owner_closed_lookup_is_inert_and_settled_lifetime_collects():
    import asyncio
    import gc
    import weakref

    import asyncpg

    from butlers.config import load_config
    from butlers.core import finance_warning_control as control
    from butlers.daemon import ButlerDaemon
    from butlers.db import Database

    async def lifetime():
        pool = await asyncpg.create_pool(min_size=0, max_size=1)
        db = Database(db_name="software-only", schema="finance")
        db.pool = pool
        # Actual partial startup isolates the owner registry from tool-schema
        # caches. Fully registered MCP and native lifecycle are tested above.
        daemon = ButlerDaemon(_ROOT / "roster/finance", db=db)
        daemon.config = load_config(daemon.config_dir)
        owner = control.FinanceWarningControlOwner(daemon)
        daemon._finance_warning_control_owner = owner
        assert control.lookup_owner(pool) is owner
        assert pool.get_size() == 0  # constructor/lookup/OFF does not connect
        refs = (weakref.ref(owner), weakref.ref(daemon), weakref.ref(pool._queue))
        assert await owner.close(0) is True
        assert control.lookup_owner(pool) is None
        assert await owner.read({}) is None
        assert pool.get_size() == 0
        with pytest.raises(RuntimeError, match="already_installed"):
            control.FinanceWarningControlOwner(daemon)
        await pool.close()
        return refs

    refs = await lifetime()
    # asyncpg.close's completed gather callbacks release their holder references
    # on the next loop turn; owner/daemon already have no external lifetime.
    await asyncio.sleep(0)
    gc.collect()
    assert all(ref() is None for ref in refs)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["acquire", "query"])
async def test_native_acquire_and_query_remain_owned_after_the_original_deadline(phase):
    import asyncio
    from types import SimpleNamespace

    import asyncpg

    from butlers.config import load_config
    from butlers.core import finance_warning_control as control
    from butlers.daemon import ButlerDaemon
    from butlers.db import Database

    pool = await asyncpg.create_pool(min_size=0, max_size=1)
    holder = pool._holders[0]
    query_entered, release_query = asyncio.Event(), asyncio.Event()

    class Connection:
        _protocol = SimpleNamespace(queries_count=0, _is_cancelling=lambda: False)
        closed = False

        def _set_proxy(self, proxy):
            self.proxy = proxy

        def is_closed(self):
            return self.closed

        def _on_release(self):
            pass

        async def reset(self, **kwargs):
            pass

        async def fetchval(self, *args):
            query_entered.set()
            while not release_query.is_set():
                try:
                    await release_query.wait()
                except asyncio.CancelledError:
                    pass  # query cancellation has not settled yet
            return True

        async def close(self, **kwargs):
            self.closed = True
            holder._release_on_close()

        def terminate(self):
            self.closed = True
            holder._release_on_close()

    holder._con = Connection()
    holder._generation = pool._generation
    db = Database(db_name="software-only", schema="finance")
    db.pool = pool
    daemon = ButlerDaemon(_ROOT / "roster/finance", db=db)
    daemon.config = load_config(daemon.config_dir)
    owner = control.FinanceWarningControlOwner(daemon)
    daemon._finance_warning_control_owner = owner
    blocker = await pool.acquire() if phase == "acquire" else None

    async def operation():
        async with owner.connection() as conn:
            return await owner._child(conn.fetchval("SELECT 1"), cancel=True)

    caller = asyncio.create_task(
        owner._bounded(operation, asyncio.get_running_loop().time() + 0.025)
    )
    try:
        if phase == "query":
            await asyncio.wait_for(query_entered.wait(), 1)
        assert await asyncio.wait_for(caller, 1) is None
        assert owner.tasks and owner.children and owner.quarantined
        assert await owner._bounded(operation, asyncio.get_running_loop().time() + 1) is None
        assert await owner.close(0.01) is False
        assert not pool.is_closing()
        if blocker is not None:
            await pool.release(blocker)
            blocker = None
        release_query.set()
        await asyncio.wait_for(owner.settle(), 1)
        assert not owner.tasks and not owner.children
        assert holder._in_use is None and pool._queue.qsize() == 1
        assert query_entered.is_set() is (phase == "query")
    finally:
        release_query.set()
        if blocker is not None:
            await pool.release(blocker)
        await owner.close(1)
        await owner.settle()
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["resolver", "confirmed-bookkeeping", "failed-bookkeeping"])
@pytest.mark.parametrize("interrupt", ["deadline", "repeated-cancel"])
async def test_actual_route_native_release_stays_owned_until_shutdown_settlement(
    monkeypatch, phase, interrupt
):
    """The real route must retain implicit native pool release at every DB phase."""
    import asyncio
    import importlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import asyncpg

    from butlers.config import load_config
    from butlers.core import finance_warning_control as control
    from butlers.daemon import ButlerDaemon
    from butlers.db import Database
    from butlers.lifecycle import run_shutdown

    route = importlib.import_module("butlers.tools.switchboard.routing.route")
    baseline_tasks = set(asyncio.all_tasks())
    entered, release = asyncio.Event(), asyncio.Event()
    pool = await asyncpg.create_pool(min_size=0, max_size=1)
    holder = pool._holders[0]
    events = []

    class Connection:
        _protocol = SimpleNamespace(queries_count=0, _is_cancelling=lambda: False)
        closed = False

        def _set_proxy(self, proxy):
            self.proxy = proxy

        def is_closed(self):
            return self.closed

        def _on_release(self):
            pass

        async def fetchrow(self, sql, *args, **kwargs):
            events.append("receiver-query")
            return None

        async def execute(self, sql, *args, **kwargs):
            assert "switchboard.routing_log" in sql or "switchboard.butler_registry" in sql
            events.append("route-bookkeeping")
            return "INSERT 0 1"

        async def reset(self, **kwargs):
            entered.set()
            await release.wait()
            events.append("native-release-settled")

        async def close(self, **kwargs):
            self.closed = True
            events.append("physical-close")
            holder._release_on_close()

        def terminate(self):
            self.closed = True
            holder._release_on_close()

    holder._con = Connection()
    holder._generation = pool._generation
    db = Database(db_name="software-only", schema="switchboard")
    db.pool = pool
    daemon = ButlerDaemon(_ROOT / "roster/switchboard", db=db)
    daemon.config = load_config(daemon.config_dir)
    daemon.config.shutdown_timeout_s = 0.02
    daemon._disconnect_switchboard = AsyncMock()

    class Catalog(dict):
        classification_complete = True
        generation_digest = "controlled-catalog"

    catalog = Catalog({control.TOOL: SimpleNamespace(effect_class="read", owner_authority=False)})
    daemon._tool_catalog = catalog
    daemon._boot_epoch = 1
    _install_publication(daemon)
    owner = control.FinanceWarningControlOwner(daemon, route_call=route.route)
    daemon._finance_warning_control_owner = owner
    finance = SimpleNamespace(
        db=SimpleNamespace(pool=object()),
        config=SimpleNamespace(name="finance"),
        _tool_catalog=catalog,
        _boot_instance_id=_boot_id(),
        _boot_epoch=2,
    )
    _install_publication(finance)
    registrations = {"switchboard": _registration(daemon), "finance": _registration(finance)}
    owner.registrations = AsyncMock(
        return_value=_snapshot(registrations, switchboard=daemon, finance=finance)
    )
    decision = SimpleNamespace(
        state="ready",
        endpoint_url="http://controlled.invalid/mcp",
        boot_instance_id=finance._boot_instance_id,
        boot_epoch=finance._boot_epoch,
    )
    if phase == "resolver":
        # Real receiver resolver, real native fetch/release; only policy row decoding is controlled.
        async def receiver_query(operation_pool, *args, **kwargs):
            await operation_pool.fetchrow("SELECT controlled_receiver")
            return decision

        monkeypatch.setattr(route, "resolve_control_plane_target", receiver_query)
    else:
        monkeypatch.setattr(
            route,
            "_resolve_receiver_route_target",
            AsyncMock(return_value=(decision, "ready", False)),
        )

    class Client:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            assert pool._queue.qsize() == 1  # no native lease crosses network/client IO
            return self

        async def __aexit__(self, *exc):
            pass

        async def call_tool(self, *args, **kwargs):
            if phase == "failed-bookkeeping":
                raise ConnectionError("synthetic peer refusal")
            return SimpleNamespace(isError=False, structuredContent=True)

    monkeypatch.setattr(route, "MCPClient", Client)
    caller = None
    try:
        assert control.DEADLINE_SECONDS == 5.0
        started = asyncio.get_running_loop().time()
        caller = asyncio.create_task(
            owner.read(
                {
                    "subject": str(UUID(int=1)),
                    "selected_date": "2026-10-27",
                    "epoch": 0,
                }
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        assert not caller.done()  # even a confirmed peer cannot succeed before physical release
        if interrupt == "repeated-cancel":
            caller.cancel()
            await asyncio.sleep(0)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(caller, 1)
        else:
            assert await asyncio.wait_for(caller, 6) is None
            assert 4.8 <= asyncio.get_running_loop().time() - started < 6
        await asyncio.sleep(0)
        assert owner.tasks and owner.children and owner.quarantined
        assert holder._in_use is not None and not holder._in_use.done()
        before_readmission = list(events)
        assert (
            await owner.read(
                {
                    "subject": str(UUID(int=1)),
                    "selected_date": "2026-10-27",
                    "epoch": 0,
                }
            )
            is None
        )
        assert events == before_readmission
        assert not await owner.close(0)
        shutdown_started = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError, match="cleanup incomplete"):
            await run_shutdown(daemon)
        assert asyncio.get_running_loop().time() - shutdown_started < 0.5
        assert not pool.is_closing() and "physical-close" not in events
        release.set()
        await asyncio.wait_for(daemon._chronicler_cleanup_task, 2)
        assert not owner.tasks and not owner.children
        assert events.count("physical-close") == 1
        assert events.index("native-release-settled") < events.index("physical-close")
    finally:
        release.set()
        if caller is not None:
            await asyncio.gather(caller, return_exceptions=True)
        await owner.settle()
        if vars(daemon).get("_catalog_shutdown_task"):
            await asyncio.gather(daemon._catalog_shutdown_task, return_exceptions=True)
        await pool.close()
        await asyncio.sleep(0)
    assert not [task for task in asyncio.all_tasks() - baseline_tasks if not task.done()]


@pytest.mark.asyncio
async def test_boot_and_publication_rotations_cannot_consume_a_captured_claim():
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from butlers.core import finance_warning_control as control

    finance = await _registered_finance(object())
    source = SimpleNamespace(
        db=SimpleNamespace(pool=object()),
        config=SimpleNamespace(name="switchboard"),
        _tool_catalog=finance._tool_catalog,
        _boot_instance_id=_boot_id(),
        _boot_epoch=3,
    )
    _install_publication(source)
    owner = control.FinanceWarningControlOwner(source)
    source._finance_warning_control_owner = owner
    registrations = {"switchboard": _registration(source), "finance": _registration(finance)}
    snapshot = _snapshot(registrations, switchboard=source, finance=finance)
    owner.registrations = AsyncMock(return_value=snapshot)
    args = {"subject": str(uuid4()), "selected_date": "2026-10-27", "epoch": 0}
    source_binding = control._registration_binding(snapshot, "switchboard")
    receiver_binding = control._registration_binding(snapshot, "finance")
    assert source_binding[0] != source_binding[2] and source_binding[1] != source_binding[3]
    assert receiver_binding[0] != receiver_binding[2] and receiver_binding[1] != receiver_binding[3]
    body = {
        "token": "held",
        "args": args,
        "receiver": list(control._local_identity(receiver_binding)),
        "nonce": "n" * 43,
    }

    def issue():
        owner.claims["held"] = (
            args,
            source_binding,
            receiver_binding,
            asyncio.get_running_loop().time() + 5,
        )

    try:
        for domain, daemon in (("switchboard", source), ("finance", finance)):
            registration = registrations[domain]
            for name, changed in (("_boot_instance_id", _boot_id()), ("_boot_epoch", 8)):
                original = getattr(daemon, name)
                issue()
                setattr(daemon, name, changed)
                assert await owner.verify_consume(body) is None
                assert "held" in owner.claims
                setattr(daemon, name, original)
            for name, changed in (
                ("instance_id", uuid4()),
                ("revision_id", uuid4()),
                ("epoch", 12),
                ("body_digest", "rotated-body"),
                ("authorization_digest", "rotated-auth"),
                ("catalog", replace(registration.catalog, generation_digest="rotated-generation")),
            ):
                original = getattr(registration, name)
                issue()
                setattr(registration, name, changed)
                assert await owner.verify_consume(body) is None
                assert "held" in owner.claims
                setattr(registration, name, original)
        for target, attribute in (
            (source._catalog_publication, "_publication_pending"),
            (source, "_catalog_publication_failed"),
        ):
            issue()
            setattr(target, attribute, True)
            with pytest.raises(PermissionError):
                await owner.verify_consume(body)
            assert "held" in owner.claims
            setattr(target, attribute, False)
        issue()
        proof = await owner.verify_consume(body)
        assert proof["binding"] == list(receiver_binding)
        assert "held" not in owner.claims
        assert await owner.verify_consume(body) is None
    finally:
        await owner.close(1)
        assert not owner.tasks and not owner.children


@pytest.mark.asyncio
async def test_receiver_current_publication_checks_real_query_and_local_pending_state():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from butlers.core import finance_warning_control as control

    finance = await _registered_finance(object())
    owner = control.FinanceWarningControlOwner(finance)
    finance._finance_warning_control_owner = owner
    registration = _registration(finance)
    binding = control._registration_binding(
        _snapshot({"finance": registration}, finance=finance), "finance"
    )
    row = {
        "instance_id": registration.instance_id,
        "revision_id": registration.revision_id,
        "epoch": registration.epoch,
        "live": True,
        "boot_current": True,
        "body_digest": registration.body_digest,
        "authorization_digest": registration.authorization_digest,
    }
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value=row))
    try:
        assert await owner._receiver_publication_current(conn, binding) is True
        assert conn.fetchrow.call_args.args[1:] == (
            "finance",
            finance._boot_instance_id,
            finance._boot_epoch,
        )
        for field, changed in (
            ("instance_id", uuid4()),
            ("revision_id", uuid4()),
            ("epoch", 12),
            ("live", False),
            ("body_digest", "new-body"),
            ("authorization_digest", "new-auth"),
        ):
            conn.fetchrow.return_value = {**row, field: changed}
            assert await owner._receiver_publication_current(conn, binding) is False
        conn.fetchrow.return_value = row
        finance._catalog_publication._publication_pending = True
        with pytest.raises(PermissionError):
            await owner._receiver_publication_current(conn, binding)
        finance._catalog_publication._publication_pending = False
        finance._catalog_publication_failed = True
        with pytest.raises(PermissionError):
            await owner._receiver_publication_current(conn, binding)
        finance._catalog_publication_failed = False
        assert await owner._receiver_publication_current(conn, binding) is True
    finally:
        await owner.close(1)
        assert not owner.children


@pytest.mark.asyncio
async def test_boot_and_catalog_reads_use_one_repeatable_read_snapshot(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from butlers.core import finance_warning_control as control
    from butlers.core.catalog_reader import PublishedCatalogProvider

    boots = {name: (_boot_id(), i) for i, name in enumerate(("switchboard", "finance"), 1)}
    transaction = SimpleNamespace(start=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    conn = SimpleNamespace(
        transaction=Mock(return_value=transaction),
        fetch=AsyncMock(
            return_value=[
                {"name": name, "boot_instance_id": boot, "boot_epoch": epoch}
                for name, (boot, epoch) in boots.items()
            ]
        ),
    )
    pool = SimpleNamespace(acquire=AsyncMock(return_value=conn), release=AsyncMock())
    daemon = SimpleNamespace(
        db=SimpleNamespace(pool=pool), config=SimpleNamespace(name="switchboard")
    )
    owner = control.FinanceWarningControlOwner(daemon)
    daemon._finance_warning_control_owner = owner
    publication = object()
    capture = AsyncMock(return_value=publication)
    monkeypatch.setattr(PublishedCatalogProvider, "snapshot", capture)
    try:
        result = await owner.registrations()
        conn.transaction.assert_called_once_with(isolation="repeatable_read")
        capture.assert_awaited_once_with(conn)
        assert result.catalogs is publication and result.boots == boots
        transaction.commit.assert_awaited_once()
        transaction.rollback.assert_not_awaited()
        pool.release.assert_awaited_once_with(conn)
    finally:
        await owner.close(1)
        assert not owner.children


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before-read", "after-read", "local-pending", "local-failed"])
async def test_registered_receiver_rechecks_publication_around_owning_query(monkeypatch, phase):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    from fastmcp import Client
    from starlette.applications import Starlette

    from butlers.core import finance_warning_control as control

    class Pool:
        def __init__(self):
            self.acquire = AsyncMock(return_value=SimpleNamespace(fetchrow=self.fetchrow))
            self.release = AsyncMock()
            self.publication_reads = self.data_reads = 0

        async def fetchrow(self, sql, *args):
            if "capability_catalog_current" in sql:
                self.publication_reads += 1
                changed = phase == "before-read" or (
                    phase == "after-read" and self.publication_reads == 2
                )
                return {
                    "instance_id": registration.instance_id,
                    "revision_id": uuid4() if changed else registration.revision_id,
                    "epoch": registration.epoch,
                    "live": True,
                    "boot_current": True,
                    "body_digest": registration.body_digest,
                    "authorization_digest": registration.authorization_digest,
                }
            assert "FROM subscriptions" in sql
            self.data_reads += 1
            return {
                "next_renewal": date(2026, 10, 27),
                "date_epoch": 0,
                "field_claims": [],
                "status": "active",
                "status_source": "extracted",
            }

    pool = Pool()
    finance = await _registered_finance(pool)
    registration = _registration(finance)
    source = SimpleNamespace(
        db=SimpleNamespace(pool=object()),
        config=SimpleNamespace(name="switchboard"),
        _tool_catalog=finance._tool_catalog,
        _boot_instance_id=_boot_id(),
        _boot_epoch=2,
    )
    _install_publication(source)
    issuer = control.FinanceWarningControlOwner(source)
    source._finance_warning_control_owner = issuer
    receiver = control.FinanceWarningControlOwner(
        finance, verifier_url="http://fixed" + control.VERIFY_PATH
    )
    finance._finance_warning_control_owner = receiver
    issuer.registrations = AsyncMock(
        return_value=_snapshot(
            {"switchboard": _registration(source), "finance": registration},
            switchboard=source,
            finance=finance,
        )
    )
    args = {"subject": str(uuid4()), "selected_date": "2026-10-27", "epoch": 0}
    original_client = httpx.AsyncClient
    verifier = Starlette(routes=[issuer.route()])
    monkeypatch.setattr(
        control.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.ASGITransport(app=verifier), **kwargs),
    )
    captured = issuer.registrations.return_value
    import asyncio

    issuer.claims["pending"] = (
        args,
        control._registration_binding(captured, "switchboard"),
        control._registration_binding(captured, "finance"),
        asyncio.get_running_loop().time() + 5,
    )
    token = control._pending.set("pending")
    try:
        if phase == "local-pending":
            finance._catalog_publication._publication_pending = True
        elif phase == "local-failed":
            finance._catalog_publication_failed = True
        async with Client(finance.mcp) as client:
            result = await client.call_tool(control.TOOL, args)
        assert result.data is None
        assert pool.data_reads == (1 if phase == "after-read" else 0)
        assert pool.publication_reads == ({"before-read": 1, "after-read": 2}.get(phase, 0))
        assert pool.acquire.await_count == pool.release.await_count
        if phase in {"local-pending", "local-failed"}:
            assert "pending" in issuer.claims  # failed local state cannot consume the bearer
        else:
            assert "pending" not in issuer.claims
    finally:
        control._pending.reset(token)
        await receiver.close(1)
        await issuer.close(1)
        assert not receiver.tasks and not receiver.children


@pytest.mark.asyncio
@pytest.mark.parametrize("admission", ["expired", "healthy"])
async def test_registered_receiver_never_starts_sql_after_awaited_admission_cutoff(
    monkeypatch, admission
):
    """Issuer expiry bounds new SQL, even while the handler's original 5s remains."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    from fastmcp import Client
    from starlette.applications import Starlette

    from butlers.core import finance_warning_control as control

    entered, release = asyncio.Event(), asyncio.Event()
    observed_remaining = []

    class Pool:
        def __init__(self):
            self.acquire = AsyncMock(return_value=SimpleNamespace(fetchrow=self.fetchrow))
            self.release = AsyncMock()
            self.publication_reads = self.data_reads = 0

        async def fetchrow(self, sql, *args):
            if "capability_catalog_current" in sql:
                self.publication_reads += 1
                if self.publication_reads == 1:
                    entered.set()
                    await asyncio.wait_for(release.wait(), 2)
                return {
                    "instance_id": registration.instance_id,
                    "revision_id": registration.revision_id,
                    "epoch": registration.epoch,
                    "live": True,
                    "boot_current": True,
                    "body_digest": registration.body_digest,
                    "authorization_digest": registration.authorization_digest,
                }
            assert "FROM subscriptions" in sql
            self.data_reads += 1
            return {
                "next_renewal": date(2026, 10, 27),
                "date_epoch": 0,
                "field_claims": [],
                "status": "active",
                "status_source": "extracted",
            }

    pool = Pool()
    finance = await _registered_finance(pool)
    registration = _registration(finance)
    source = SimpleNamespace(
        db=SimpleNamespace(pool=object()),
        config=SimpleNamespace(name="switchboard"),
        _tool_catalog=finance._tool_catalog,
        _boot_instance_id=_boot_id(),
        _boot_epoch=2,
    )
    _install_publication(source)
    issuer = control.FinanceWarningControlOwner(source)
    source._finance_warning_control_owner = issuer
    receiver = control.FinanceWarningControlOwner(
        finance, verifier_url="http://fixed" + control.VERIFY_PATH
    )
    finance._finance_warning_control_owner = receiver
    captured = _snapshot(
        {"switchboard": _registration(source), "finance": registration},
        switchboard=source,
        finance=finance,
    )
    issuer.registrations = AsyncMock(return_value=captured)
    verify = issuer.verify_consume

    async def short_authority(*args, **kwargs):
        proof = await verify(*args, **kwargs)
        assert proof is not None
        # A legitimate shorter issuer lifetime, not a replacement handler deadline.
        proof["remaining"] = min(proof["remaining"], 0.1 if admission == "expired" else 2)
        observed_remaining.append(proof["remaining"])
        return proof

    monkeypatch.setattr(issuer, "verify_consume", short_authority)
    original_client = httpx.AsyncClient
    verifier = Starlette(routes=[issuer.route()])
    monkeypatch.setattr(
        control.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.ASGITransport(app=verifier), **kwargs),
    )
    args = {"subject": str(uuid4()), "selected_date": "2026-10-27", "epoch": 0}
    caller = None
    token = control._pending.set("pending")
    try:
        # Cold tool discovery precedes the authority probe; no product deadline is changed.
        async with Client(finance.mcp) as client:
            issuer.claims["pending"] = (
                args,
                control._registration_binding(captured, "switchboard"),
                control._registration_binding(captured, "finance"),
                asyncio.get_running_loop().time() + 5,
            )
            started = asyncio.get_running_loop().time()
            caller = asyncio.create_task(client.call_tool(control.TOOL, args))
            await asyncio.wait_for(entered.wait(), 2)
            assert not caller.done() and pool.data_reads == 0
            assert observed_remaining
            if admission == "expired":
                await asyncio.sleep(observed_remaining[0] + 0.025)
            release.set()
            result = await asyncio.wait_for(caller, 2)
        assert asyncio.get_running_loop().time() - started < control.DEADLINE_SECONDS
        assert result.data is (None if admission == "expired" else True)
        assert pool.data_reads == (0 if admission == "expired" else 1)
        assert pool.publication_reads == (1 if admission == "expired" else 2)
        assert pool.acquire.await_count == pool.release.await_count == 1
        assert "pending" not in issuer.claims
        assert not receiver.tasks and not receiver.children
    finally:
        release.set()
        control._pending.reset(token)
        if caller is not None and not caller.done():
            caller.cancel()
        if caller is not None:
            await asyncio.gather(caller, return_exceptions=True)
        await receiver.close(1)
        await issuer.close(1)
        await receiver.settle()
        await issuer.settle()
        assert not receiver.tasks and not receiver.children


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boot_state", ["current", "rotated", "missing-control", "missing-receipt", "outage"]
)
async def test_registered_receiver_fences_authoritative_boot_after_callback(
    monkeypatch, boot_state
):
    """A committed successor may leave old local fields and publication unchanged."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    from fastmcp import Client
    from starlette.applications import Starlette

    from butlers.core import finance_warning_control as control

    authoritative = "current"

    class Pool:
        def __init__(self):
            self.acquire = AsyncMock(return_value=SimpleNamespace(fetchrow=self.fetchrow))
            self.release = AsyncMock()
            self.reads = self.fences = 0

        async def fetchrow(self, sql, *args):
            if "capability_catalog_current" in sql:
                self.fences += 1
                row = {
                    "instance_id": registration.instance_id,
                    "revision_id": registration.revision_id,
                    "epoch": registration.epoch,
                    "live": True,
                    "body_digest": registration.body_digest,
                    "authorization_digest": registration.authorization_digest,
                }
                if "finance_warning_boot_current" in sql:
                    assert args[1:] == (finance._boot_instance_id, finance._boot_epoch)
                    if authoritative == "outage":
                        raise PermissionError("controlled_boot_read_outage")
                    row["boot_current"] = {
                        "current": True,
                        "rotated": False,
                        "missing-control": None,
                        "missing-receipt": None,
                    }[authoritative]
                return row
            assert "FROM subscriptions" in sql
            self.reads += 1
            return {
                "next_renewal": date(2026, 10, 27),
                "date_epoch": 0,
                "field_claims": [],
                "status": "active",
                "status_source": "extracted",
            }

    pool = Pool()
    finance = await _registered_finance(pool)
    registration = _registration(finance)
    source = SimpleNamespace(
        db=SimpleNamespace(pool=object()),
        config=SimpleNamespace(name="switchboard"),
        _tool_catalog=finance._tool_catalog,
        _boot_instance_id=_boot_id(),
        _boot_epoch=2,
    )
    _install_publication(source)
    issuer = control.FinanceWarningControlOwner(source)
    source._finance_warning_control_owner = issuer
    receiver = control.FinanceWarningControlOwner(
        finance, verifier_url="http://fixed" + control.VERIFY_PATH
    )
    finance._finance_warning_control_owner = receiver
    captured = _snapshot(
        {"switchboard": _registration(source), "finance": registration},
        switchboard=source,
        finance=finance,
    )
    issuer.registrations = AsyncMock(return_value=captured)
    verify = issuer.verify_consume

    async def rotate_after_atomic_consume(body):
        nonlocal authoritative
        proof = await verify(body)
        assert proof is not None and "pending" not in issuer.claims
        authoritative = boot_state
        return proof

    monkeypatch.setattr(issuer, "verify_consume", rotate_after_atomic_consume)
    original_client = httpx.AsyncClient
    app = Starlette(routes=[issuer.route()])
    monkeypatch.setattr(
        control.httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.ASGITransport(app=app), **kw),
    )
    args = {"subject": str(uuid4()), "selected_date": "2026-10-27", "epoch": 0}
    issuer.claims["pending"] = (
        args,
        control._registration_binding(captured, "switchboard"),
        control._registration_binding(captured, "finance"),
        asyncio.get_running_loop().time() + 5,
    )
    token = control._pending.set("pending")
    try:
        identity = receiver.identity()
        async with Client(finance.mcp) as client:
            result = await client.call_tool(control.TOOL, args)
        assert receiver.identity() == identity
        assert registration.revision_id == finance._catalog_publication.revision
        assert result.data is (True if boot_state == "current" else None)
        assert pool.reads == (1 if boot_state == "current" else 0)
        assert pool.fences == (2 if boot_state == "current" else 1)
        assert pool.acquire.await_count == pool.release.await_count == 1
        assert "pending" not in issuer.claims
    finally:
        control._pending.reset(token)
        await receiver.close(1)
        await issuer.close(1)
        await receiver.settle()
        await issuer.settle()
        assert not receiver.children and not receiver.tasks
