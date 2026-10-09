from __future__ import annotations

import uuid

import pytest

from butlers.core.sessions import session_complete, session_create


class _FakePool:
    def __init__(self) -> None:
        self.fetchval_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchval(self, query: str, *args: object) -> object:
        self.fetchval_calls.append((query, args))
        return uuid.uuid4()

    async def fetchrow(self, query: str, *args: object) -> dict[str, object]:
        self.fetchrow_calls.append((query, args))
        return {"id": args[0] if args else uuid.uuid4(), "model": None}

    async def execute(self, query: str, *args: object) -> str:
        return "INSERT 0 0"


@pytest.mark.asyncio
async def test_session_create_strips_untranslatable_prompt_chars() -> None:
    pool = _FakePool()

    await session_create(
        pool,
        prompt="hello\x00\ud83dworld",
        trigger_source="tick",
        request_id=str(uuid.uuid4()),
    )

    assert pool.fetchval_calls
    _query, args = pool.fetchval_calls[0]
    assert args[0] == "helloworld"
    await _assert_opaque_session_policy_first()


async def _assert_opaque_session_policy_first():
    """Actual configured branch, software only; no row/string mints lineage."""
    from contextlib import asynccontextmanager

    from butlers.chronicler.location_copy_pools import _copy_pools
    from butlers.chronicler.location_policy import PolicyUnavailableError

    class Pool:
        role = "butler_chronicler_rw"

        def __init__(self):
            self.trace = []
            self.row = uuid.uuid4()

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.trace.append("begin")
            yield
            self.trace.append("commit")

        async def fetchval(self, query, *args):
            if "current_user" in query:
                return self.role
            if "INSERT INTO sessions" in query:
                self.trace.append("insert")
                return self.row
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            if "FROM location_retention_policy" in query:
                self.trace.append("policy")
                return {"version": 1}
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "pg_advisory_xact_lock" in query:
                self.trace.append("producer")
            elif "pg_notify" not in query:
                raise AssertionError(query)
            return "SELECT 1"

    pool = Pool()
    _copy_pools.add(pool)  # Explicit constructor registry double, not SQL authority.
    try:
        assert (
            await session_create(
                pool, "opaque ordinary input", "tick", request_id=str(uuid.uuid4())
            )
            == pool.row
        )
        assert "policy" in pool.trace and "producer" in pool.trace
        assert (
            pool.trace.index("policy") < pool.trace.index("producer") < pool.trace.index("insert")
        )
        assert pool.trace.index("insert") < pool.trace.index("commit")
        pool.trace.clear()
        pool.role = "other_role"
        with pytest.raises(PolicyUnavailableError, match="identity differs"):
            await session_create(
                pool, "opaque ordinary input", "tick", request_id=str(uuid.uuid4())
            )
        assert "insert" not in pool.trace
    finally:
        _copy_pools.discard(pool)


@pytest.mark.asyncio
async def test_session_complete_sanitizes_jsonb_and_text_payloads() -> None:
    pool = _FakePool()
    session_id = uuid.uuid4()

    await session_complete(
        pool,
        session_id,
        output="done\x00\ud83d",
        tool_calls=[
            {
                "name": "tool\x00\ud83d",
                "arguments": {"value": "bad\x00\ud83dtext", "items": ["ok", "\x00\ud83d"]},
            }
        ],
        duration_ms=12,
        success=False,
        error="boom\x00\ud83d",
        cost={"raw": "cost\x00\ud83d"},
    )

    assert pool.fetchrow_calls
    _query, args = pool.fetchrow_calls[0]
    assert args[1] == "done"
    # tool_calls and cost are passed as Python objects (asyncpg JSONB codec handles encoding)
    assert args[2] == [
        {
            "name": "tool",
            "arguments": {"value": "badtext", "items": ["ok", ""]},
        }
    ]
    assert args[4] == {"raw": "cost"}
    assert args[6] == "boom"


@pytest.mark.asyncio
async def test_native_location_completion_floor_fences_late_copy_and_preserves_unrelated():
    """REQ-location-retention-005; actual completion wiring, software-only SQL double."""
    from contextlib import asynccontextmanager

    from butlers.chronicler import location_retention

    class OwningPool(_FakePool):
        forgotten = True
        role = "butler_chronicler_rw"
        input_bound = True
        execute_calls = None

        async def execute(self, query, *args):
            if self.execute_calls is None:
                self.execute_calls = []
            self.execute_calls.append((query, args))
            return "INSERT 0 0"

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            yield

        async def fetchval(self, query, *args):
            if "current_user" in query:
                return self.role
            if "location_native_dispatch_sessions" in query:
                return self.input_bound
            if "location_native_copy_dispositions" in query:
                return self.forgotten
            return await super().fetchval(query, *args)

        async def fetchrow(self, query, *args):
            if "location_retention_policy" in query:
                return {"version": 1}
            return await super().fetchrow(query, *args)

    pool = OwningPool()
    # Test configuration of the actual source-owned pool identity seam; this
    # does not claim real-role, accepted input or migrated PostgreSQL evidence.
    location_retention._copy_pools.add(pool)
    try:
        session_id = uuid.uuid4()
        await session_complete(
            pool, session_id, "synthetic exact point", [{"name": "synthetic"}], 17, True
        )
        _, args = pool.fetchrow_calls[-1]
        assert args[1] == "[Location-derived output forgotten]" and args[2] == []
        assert args[3] == 17 and args[5] is True
        from butlers.core.session_process_logs import write as process_write

        await process_write(
            pool, session_id, command="synthetic command", stderr="synthetic bytes", exit_code=3
        )
        args = pool.execute_calls[-1][1]
        assert (
            args[2] == 3
            and args[3] == "[Location-derived diagnostic forgotten]"
            and args[4] is None
        )
        pool.input_bound = False
        await process_write(
            pool,
            session_id,
            command="independent original command",
            stderr="derived output",
            exit_code=3,
        )
        assert pool.execute_calls[-1][1][3] == "independent original command"
        assert pool.execute_calls[-1][1][4] is None
        await session_complete(pool, session_id, None, [], 17, False, error="synthetic error")
        assert pool.fetchrow_calls[-1][1][5] is False
        assert pool.fetchrow_calls[-1][1][6] == "Location-derived failure evidence withheld"
        pool.forgotten = False
        await session_complete(pool, session_id, "unrelated output", [], 19, True)
        assert pool.fetchrow_calls[-1][1][1] == "unrelated output"
        pool.role = "butler_other_rw"
        with pytest.raises(location_retention.PolicyUnavailableError, match="identity differs"):
            await session_complete(pool, session_id, "wrong owning pool", [], 19, True)
        assert pool.fetchrow_calls[-1][1][1] == "unrelated output"
    finally:
        location_retention.unregister_native_copy_pool(pool)
    # Source-unconfigured legacy completion follows its unchanged normal path.
    await session_complete(pool, uuid.uuid4(), "ordinary legacy", [], 21, True)
    assert pool.fetchrow_calls[-1][1][1] == "ordinary legacy"


@pytest.mark.asyncio
async def test_native_location_dispatch_binds_exact_input_before_session_admission():
    """REQ-location-retention-005; actual session hook, software-only SQL double.

    Fixed adapter and private producer scope are exercised here; this does not
    authenticate a real producer, execute SQL or prove runtime/provider proof.
    """
    import hashlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from butlers.chronicler import location_retention
    from butlers.chronicler.location_export_lifetime import (
        _current_location_export,
        _LocationExportScope,
    )
    from butlers.chronicler.location_input_binding import (
        NativeLocationDispatch,
        _current_dispatch_input,
        _dispatchers,
        dispatch_with_native_input,
        register_dispatch_runtime,
    )
    from butlers.core.sessions import session_create
    from butlers.core.spawner import Spawner

    class Pool:
        def __init__(self):
            self.role = "butler_chronicler_rw"
            self.trace = []
            self.reservations = {}
            self.parents = []
            self.sessions = {}
            self.receiving = {}
            self.session_id = uuid.uuid4()
            self.valid = True
            self.unknown = False
            self.mutate_prompt = False
            self.fail_insert = False

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.trace.append("begin")
            try:
                yield
            except BaseException:
                self.trace.append("rollback")
                raise
            else:
                self.trace.append("commit")

        async def fetchrow(self, query, *args):
            if "location_retention_policy" in query:
                self.trace.append("policy")
                return {"version": 1}
            if "location_native_dispatch_inputs" in query:
                return self.reservations.get(args[0])
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "source_adapter_state" in query:
                return []
            if "location_native_dispatch_parents" in query:
                return self.parents
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            if "current_schema" in query:
                return "chronicler"
            if "current_user" in query:
                return self.role
            if "bool_and" in query:
                return self.valid
            if "count(*) FROM location_native_dispatch_parents" in query:
                return 0 if self.unknown else len(self.parents)
            if "INSERT INTO sessions" in query:
                self.trace.append("session_insert")
                if self.fail_insert:
                    raise RuntimeError("synthetic interrupted session birth")
                self.session_prompt = args[0]
                if len(args) == 13:
                    self.session_id = args[12]
                return self.session_id
            if "receiving_session FROM location_native_dispatch_reservations" in query:
                self.trace.append("receiver_readback")
                return None if self.unknown else self.receiving.get(args[0])
            if "receiving_session FROM location_native_dispatch_sessions" in query:
                self.trace.append("admission_readback")
                return None if self.unknown else self.sessions[args[0]]
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "INSERT INTO location_native_dispatch_inputs" in query:
                self.trace.append("capture")
                self.reservations[args[0]] = {
                    "server_request": args[1],
                    "prompt_digest": args[2],
                    "parent_count": args[3],
                }
            elif "INSERT INTO location_native_dispatch_parents" in query:
                self.parents.append({"copy_generation": args[1], "input_digest": args[2]})
            elif "INSERT INTO location_native_dispatch_reservations" in query:
                self.trace.append("receiver_reservation")
                self.receiving[args[0]] = args[1]
            elif "INSERT INTO location_native_dispatch_sessions" in query:
                self.trace.append("session_binding")
                self.sessions[args[0]] = args[1]
            elif "INSERT INTO location_native_copy_births" in query:
                self.trace.append("receiving_birth")
            return "INSERT 0 1"

    pool = Pool()
    parent = uuid.uuid4()
    scope = _LocationExportScope(copies=[(pool, "native_read", parent, b"x" * 32)])
    # Source factory and pool registry doubles isolate this software contract.
    location_retention._copy_pools.add(pool)
    spawner = Spawner.__new__(Spawner)
    spawner._config = SimpleNamespace(name="chronicler")
    spawner._pool = pool

    async def trigger(*, prompt, trigger_source):
        pool.trace.append("dispatch")
        sid = await session_create(
            pool,
            prompt + ("changed" if pool.mutate_prompt else ""),
            trigger_source,
            request_id=str(uuid.uuid4()),
        )
        pool.trace.append("runtime_admitted")
        return SimpleNamespace(session_id=sid)

    spawner.trigger = trigger
    with pytest.raises(ValueError, match="constructor differs"):
        NativeLocationDispatch(spawner)
    register_dispatch_runtime(spawner, type(None))
    adapter = NativeLocationDispatch(spawner)
    from butlers.api.app import create_app

    configured = create_app(api_key="", chronicler_spawner=spawner)
    module = next(
        module for name, module in configured.state.butler_routers if name == "chronicler"
    )
    installed = configured.dependency_overrides[module._get_day_close_dispatch_fn]()
    assert isinstance(installed, NativeLocationDispatch) and installed._spawner is spawner
    unconfigured = create_app(api_key="")
    ordinary_module = next(
        module for name, module in unconfigured.state.butler_routers if name == "chronicler"
    )
    assert ordinary_module._get_day_close_dispatch_fn not in unconfigured.dependency_overrides
    token = _current_location_export.set(scope)
    prompt = "Fixed generated instruction plus actual source bundle"
    try:
        result = await dispatch_with_native_input(
            pool, adapter, prompt=prompt, source="api:explain"
        )
        assert result.session_id == pool.session_id
        assert (
            pool.reservations[next(iter(pool.reservations))]["prompt_digest"]
            == hashlib.sha256(prompt.encode()).digest()
        )
        trace = pool.trace
        assert trace.index("capture") < trace.index("commit") < trace.index("dispatch")
        assert trace.index("policy", trace.index("dispatch")) < trace.index("session_insert")
        assert trace.index("receiver_reservation") < trace.index("receiving_birth")
        assert trace.index("receiving_birth") < trace.index("receiver_readback")
        assert trace.index("receiver_readback") < trace.index("dispatch")
        assert trace.index("session_binding") < trace.index("admission_readback")
        assert trace.index("admission_readback") < trace.index("runtime_admitted")
        assert _current_dispatch_input.get() is None
        for mode in ("forged", "mixed", "unknown", "wrong_role", "changed", "interrupted"):
            pool.trace.clear()
            pool.parents.clear()
            pool.valid = mode != "mixed"
            pool.unknown = mode == "unknown"
            pool.role = "other_role" if mode == "wrong_role" else "butler_chronicler_rw"
            pool.mutate_prompt = mode == "changed"
            pool.fail_insert = mode == "interrupted"
            with pytest.raises((location_retention.PolicyUnavailableError, RuntimeError)):
                await dispatch_with_native_input(
                    pool,
                    trigger if mode == "forged" else adapter,
                    prompt=prompt,
                    source="api:explain",
                )
            assert "runtime_admitted" not in pool.trace
            assert _current_dispatch_input.get() is None
        # No actual native parents: unrelated existing callback remains valid.
        empty_token = _current_location_export.set(_LocationExportScope())
        called = []

        async def ordinary(**kwargs):
            called.append(kwargs)
            return "ordinary"

        try:
            assert (
                await dispatch_with_native_input(
                    pool, ordinary, prompt="independent", source="api:ordinary"
                )
                == "ordinary"
            )
            assert called == [{"prompt": "independent", "trigger_source": "api:ordinary"}]
        finally:
            _current_location_export.reset(empty_token)
    finally:
        _current_location_export.reset(token)
        location_retention._copy_pools.discard(pool)
        _dispatchers.pop(pool, None)
