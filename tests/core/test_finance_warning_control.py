"""The fixed Finance read must bind the real invocation and its whole lifetime."""

from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

_ROOT = Path(__file__).resolve().parents[2]


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
    daemon._boot_instance_id, daemon._boot_epoch = UUID(int=2), 1
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
        _boot_instance_id=UUID(int=3),
        _boot_epoch=1,
    )
    issuer = control.FinanceWarningControlOwner(source)
    source._finance_warning_control_owner = issuer
    owner = control.FinanceWarningControlOwner(
        finance, verifier_url="http://fixed/internal/finance-warning/verify"
    )
    finance._finance_warning_control_owner = owner
    registrations = {
        name: SimpleNamespace(
            instance_id=d._boot_instance_id, epoch=d._boot_epoch, catalog=d._tool_catalog
        )
        for name, d in [("switchboard", source), ("finance", finance)]
    }
    issuer.registrations = AsyncMock(
        return_value=SimpleNamespace(registration=registrations.__getitem__)
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
                    issuer.identity(),
                    owner.identity(),
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
                    issuer.identity(),
                    owner.identity(),
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
        _boot_instance_id=UUID(int=7),
        _boot_epoch=1,
    )
    owner = control.FinanceWarningControlOwner(source, route_call=route_mod.route)
    source._finance_warning_control_owner = owner
    receiver = control.FinanceWarningControlOwner(
        finance, verifier_url="http://fixed" + control.VERIFY_PATH
    )
    finance._finance_warning_control_owner = receiver
    registrations = {
        name: SimpleNamespace(
            instance_id=d._boot_instance_id, epoch=d._boot_epoch, catalog=d._tool_catalog
        )
        for name, d in [("finance", finance), ("switchboard", source)]
    }
    owner.registrations = AsyncMock(
        return_value=SimpleNamespace(registration=registrations.__getitem__)
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
        _boot_instance_id=UUID(int=3),
        _boot_epoch=1,
    )
    owner = control.FinanceWarningControlOwner(source)
    source._finance_warning_control_owner = owner
    identity = owner.identity()
    receiver = (str(finance._boot_instance_id), 1, finance._tool_catalog.generation_digest)
    source_reg = SimpleNamespace(
        instance_id=source._boot_instance_id, epoch=1, catalog=source._tool_catalog
    )
    receiver_reg = SimpleNamespace(
        instance_id=finance._boot_instance_id, epoch=1, catalog=finance._tool_catalog
    )
    owner.registrations = AsyncMock(
        return_value=SimpleNamespace(
            registration={"finance": receiver_reg, "switchboard": source_reg}.__getitem__
        )
    )
    args = {"subject": str(UUID(int=1)), "selected_date": "2026-10-27", "epoch": 0}
    body = {"token": "opaque", "args": args, "receiver": list(receiver), "nonce": "n" * 43}

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
    daemon._boot_instance_id, daemon._boot_epoch = UUID(int=7), 1
    owner = control.FinanceWarningControlOwner(daemon, route_call=route.route)
    daemon._finance_warning_control_owner = owner
    registrations = {
        "switchboard": SimpleNamespace(instance_id=UUID(int=7), epoch=1, catalog=catalog),
        "finance": SimpleNamespace(instance_id=UUID(int=8), epoch=1, catalog=catalog),
    }
    owner.registrations = AsyncMock(
        return_value=SimpleNamespace(registration=registrations.__getitem__)
    )
    decision = SimpleNamespace(
        state="ready",
        endpoint_url="http://controlled.invalid/mcp",
        boot_instance_id=UUID(int=8),
        boot_epoch=1,
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
