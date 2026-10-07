"""REQ-endpoint-custody-holds-002: custody wire values cannot mint authority."""

import json
import pickle
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone

import pytest
from asyncpg.pool import PoolConnectionHolder, PoolConnectionProxy

from butlers.core.custody_source import (
    CustodyError,
    VerifiedCustodyCall,
    canonical_json,
    canonical_targets,
    canonical_uuid,
    closed_object,
    current_custody_call,
    digest,
    parse_wire,
    sha256_hex,
    utc_timestamp,
    validate_minted_binding,
)


def test_canonical_wire_retains_exact_types_bytes_and_closed_shape():
    from dataclasses import replace
    from types import SimpleNamespace

    from butlers.core import custody_native
    from butlers.core.custody_bindings import owning_binding_publisher

    # A legacy connection wrapper cannot identify an enrolled pool. The actual
    # constructor allocation continues to return its exact guarded publisher.
    assert owning_binding_publisher(SimpleNamespace(acquire=lambda: None)) is None
    actual_pool, publisher = object(), object()
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(custody_native._installed_publishers, actual_pool, publisher)
        assert owning_binding_publisher(actual_pool) is publisher
        assert owning_binding_publisher(object()) is None

    from butlers.core.custody_bindings import channel_origin_digest, origins_affected_by_facts
    from butlers.core.custody_control import host_profile

    host = host_profile(digest({"kind": "profile-control"}))
    assert host.actor == "host-switchboard" and host.role == "butler_switchboard_rw"
    from butlers.core.custody_admission import CustodyProfile

    distinct = CustodyProfile(
        "configured-actor",
        "butler_Custom_2_rw",
        ("domain_evidence",),
        ("write",),
        ("configured-actor",),
        digest({"kind": "existing-owning-schema"}),
    )
    assert distinct.actor != "Custom_2" and distinct.role == "butler_Custom_2_rw"
    for role in ("butlers", "postgres", "butler_bad-schema_rw", "butler_9schema_rw"):
        with pytest.raises(CustodyError, match="invalid"):
            replace(distinct, role=role)

    for fields in (
        {"source_kinds": ("accepted_ingress", "host_command")},
        {"audiences": ("relationship", "switchboard")},
        {"operations": ("write",)},
        {"role": "dashboard_auth_api"},
    ):
        with pytest.raises(CustodyError, match="invalid"):
            replace(host, **fields)
    writer_only = CustodyProfile(
        "relationship",
        "butler_relationship_rw",
        ("domain_evidence",),
        (),
        (),
        digest({"kind": "native-api-identity-writer.v1"}),
    )
    for fields in (
        {"actor": "switchboard"},
        {"source_kinds": ("accepted_ingress",)},
        {"audiences": ("relationship",)},
        {"role": "butler_general_rw"},
    ):
        with pytest.raises(CustodyError, match="invalid"):
            replace(writer_only, **fields)
    value = {"z": "é\n/", "a": [True, 1, None]}
    assert canonical_json(value) == b'{"a":[true,1,null],"z":"\xc3\xa9\\n/"}'
    assert digest({"a": True}) != digest({"a": 1})
    for bad in ({"a": 1.0}, {"a": float("nan")}, {"a": "\ud800"}, {"a": ()}, {"é": 1}):
        with pytest.raises(CustodyError, match="invalid"):
            canonical_json(bad)
    with pytest.raises(CustodyError):
        canonical_json({"a": "long"}, maximum=3)
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(CustodyError, match="invalid"):
        canonical_json(cyclic)
    for bad in ({}, {"required": 1, "caller_actor": "owner"}, []):
        with pytest.raises(CustodyError):
            closed_object(bad, required={"required"})
    assert closed_object({"required": 1}, required={"required"}) == {"required": 1}
    call_id = str(uuid.uuid4())
    assert canonical_uuid(call_id) == call_id
    assert sha256_hex("a" * 64) == "a" * 64
    for bad in (call_id.upper(), call_id.replace("-", ""), 1):
        with pytest.raises(CustodyError):
            canonical_uuid(bad)
    for bad in ("A" * 64, "a" * 63, None):
        with pytest.raises(CustodyError):
            sha256_hex(bad)
    local = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))
    assert utc_timestamp(local) == "2025-12-31T16:00:00.000000Z"
    assert utc_timestamp(local.astimezone(UTC)) == utc_timestamp(local)
    with pytest.raises(CustodyError):
        utc_timestamp(datetime(2026, 1, 1))
    # Ingress aliases and canonical stored values must address the SAME pointer;
    # otherwise a writer could invalidate only one spelling of the binding.
    telegram_origin = channel_origin_digest("telegram", "ExampleUser")
    for channel, identifier in (
        ("telegram_bot", "@exampleuser"),
        ("telegram_user_client", "telegram:ExampleUser"),
        ("telegram_chat_id", "exampleuser"),
    ):
        assert channel_origin_digest(channel, identifier) == telegram_origin
    assert channel_origin_digest("email", "A@EXAMPLE.TEST") == channel_origin_digest(
        "email", "a@example.test"
    )
    assert channel_origin_digest("whatsapp", "123456789:2@s.whatsapp.net") == channel_origin_digest(
        "whatsapp_jid", "123456789@s.whatsapp.net"
    )
    assert channel_origin_digest("telegram", "123456789") != channel_origin_digest(
        "whatsapp_jid", "123456789@s.whatsapp.net"
    )
    for channel, identifier in (("unknown", "exampleuser"), ("email", "not-an-email")):
        with pytest.raises(CustodyError):
            channel_origin_digest(channel, identifier)
    # An old/deleted phone or newly competing foreign phone affects every
    # origin the adopted bounded suffix resolver could have accepted. This is
    # typed selector evidence only; the actual SQL currentness test is separate.
    affected = set(
        origins_affected_by_facts(
            [
                {"predicate": "has-phone", "object": "+12 (345) 678-901"},
                {"predicate": "has-email", "object": "Old@EXAMPLE.TEST"},
                {"predicate": "has-email", "object": "new@example.test"},
                {"predicate": "has-handle", "object": "telegram:ExampleUser"},
                {"predicate": "other", "object": "irrelevant"},
            ]
        )
    )
    assert {("email", "old@example.test"), ("email", "new@example.test")} <= affected
    assert ("telegram", "exampleuser") in affected
    for digits in ("12345678901", "2345678901", "345678901", "012345678901", "9912345678901"):
        assert ("whatsapp_jid", digits + "@s.whatsapp.net") in affected
    for digits in ("45678901", "99912345678901", "12345678902"):
        assert ("whatsapp_jid", digits + "@s.whatsapp.net") not in affected
    assert origins_affected_by_facts([]) == []
    endpoint = {
        "target_id": str(uuid.uuid4()),
        "target_kind": "endpoint",
        "binding_digest": "a" * 64,
        "binding_version": 2,
        "generation": 0,
    }
    account = endpoint | {"target_id": str(uuid.uuid4()), "target_kind": "account"}
    assert canonical_targets([endpoint, account]) == [account, endpoint]
    assert canonical_targets([account, endpoint]) == [account, endpoint]
    for bad in (
        [endpoint, endpoint],
        [endpoint, endpoint | {"target_id": str(uuid.uuid4())}],
        [endpoint, account | {"target_id": endpoint["target_id"]}],
        [endpoint | {"binding_version": True}],
        [endpoint | {"generation": -1}],
        [endpoint | {"generation": 2**63}],
    ):
        with pytest.raises(CustodyError, match="invalid"):
            canonical_targets(bad)

    # Rehashing a wrong binding must still be refused by the producer's
    # independently retained expected values. These are value-integrity tests,
    # not source authentication or real PostgreSQL evidence.
    receipt = {
        "process_id": str(uuid.uuid4()),
        "control_epoch": 1,
        "restore_epoch": str(uuid.uuid4()),
    }
    source_id = uuid.uuid4()
    operation = {"method": "custody.commit", "arguments": {"command_id": str(source_id)}}
    expires = "2026-01-01T00:00:00.000000Z"
    binding = {
        "call_ref": str(uuid.uuid4()),
        "source_ref": str(source_id),
        "source_digest": "a" * 64,
        "issuer_process": receipt["process_id"],
        "destination_process": str(uuid.uuid4()),
        "destination_actor": "switchboard",
        "operation": operation,
        "control_epoch": 1,
        "restore_epoch": receipt["restore_epoch"],
        "expires_at": expires,
    }
    minted = {
        "call_ref": binding["call_ref"],
        "operation_digest": digest(binding),
        "expires_at": expires,
        "binding": binding,
    }
    expected = {
        "source_ref": source_id,
        "source_digest": "a" * 64,
        "operation": operation,
        "destination_actor": "switchboard",
        "receipt": receipt,
    }
    assert validate_minted_binding(minted, **expected) == minted
    for field, changed in (
        ("source_ref", str(uuid.uuid4())),
        ("source_digest", "b" * 64),
        ("issuer_process", str(uuid.uuid4())),
        ("destination_actor", "relationship"),
        ("operation", {"method": "custody.result"}),
        ("control_epoch", 2),
        ("control_epoch", True),
        ("restore_epoch", str(uuid.uuid4())),
        ("call_ref", str(uuid.uuid4())),
    ):
        wrong = binding | {field: changed}
        with pytest.raises(CustodyError, match="refused"):
            validate_minted_binding(
                minted | {"binding": wrong, "operation_digest": digest(wrong)}, **expected
            )
    with pytest.raises(CustodyError, match="refused"):
        validate_minted_binding(minted | {"operation_digest": "b" * 64}, **expected)
    assert validate_minted_binding(minted, **expected) == minted


def test_wire_parser_rejects_duplicate_and_reserved_claims_without_context():
    value = {
        "version": "custody-wire.v1",
        "call_ref": str(uuid.uuid4()),
        "challenge_ref": str(uuid.uuid4()),
        "operation_digest": "b" * 64,
        "method": "custody.apply",
        "arguments": {},
    }
    assert parse_wire(canonical_json(value)) == value
    for bad in (
        canonical_json(value).decode().encode("utf-16"),
        canonical_json(value).decode().encode("utf-32"),
        b'{"version":"one","version":"two"}',
        canonical_json(value | {"actor": "owner"}),
        canonical_json(value | {"version": "unknown"}),
        canonical_json(value | {"call_ref": True}),
        canonical_json(value | {"method": "../other"}),
        json.dumps(value | {"arguments": {"value": 1.0}}).encode(),
        b" " * 8193,
        b"\xff",
        b"[" * 1500 + b"0" + b"]" * 1500,
    ):
        with pytest.raises(CustodyError):
            parse_wire(bad)
    forged = VerifiedCustodyCall(
        uuid.UUID(value["call_ref"]),
        value["challenge_ref"],
        value["operation_digest"],
        uuid.uuid4(),
        value["method"],
        {},
        1,
    )
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(forged)
    with pytest.raises(CustodyError, match="refused"):
        current_custody_call()

    # The inherited startup channel has actual bounded/duplicate-preserving
    # framing, not a JSON DTO or arbitrary public verifier selector. These are
    # wire controls only; actual process enrollment requires the SQL controls.
    import asyncio
    import socket
    import struct

    from butlers.api.owner_auth.config import OwnerAuthConfig
    from butlers.api.owner_auth.context import OwnerCustodyProof
    from butlers.core.custody_api import (
        dashboard_profile,
        receive_startup_frame,
        send_startup_frame,
    )
    from butlers.core.custody_api_parent import validate_dashboard_manifest

    config = OwnerAuthConfig("https://custody.example.test", "custody.example.test")
    profile = dashboard_profile(config)
    manifest = {
        "nonce": str(uuid.uuid4()),
        "adapter_incarnation": str(uuid.uuid4()),
        "logical_actor": profile.actor,
        "role": profile.role,
        "config_digest": profile.config_digest,
        "source_kinds": list(profile.source_kinds),
        "operations": list(profile.operations),
        "audiences": list(profile.audiences),
    }
    validate_dashboard_manifest(manifest, config)
    for delta in (
        {"logical_actor": "switchboard"},
        {"role": "butler_switchboard_rw"},
        {"source_kinds": ["host_command"]},
        {"audiences": ["relationship"]},
        {"config_digest": "b" * 64},
        {"nonce": True},
        {"verifier_url": "https://evil"},
    ):
        with pytest.raises(CustodyError):
            validate_dashboard_manifest(manifest | delta, config)
    proof = OwnerCustodyProof("a" * 64, "b" * 64, config.origin, config.rp_id, None)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(proof)

    async def framing():
        sender, receiver = socket.socketpair()
        sender.setblocking(False)
        receiver.setblocking(False)
        try:
            await send_startup_frame(sender, manifest)
            assert await receive_startup_frame(receiver) == manifest
            for raw in (b'{"nonce":"one","nonce":"two"}', b"[]", b"\xff"):
                await asyncio.get_running_loop().sock_sendall(
                    sender, struct.pack("!I", len(raw)) + raw
                )
                with pytest.raises(CustodyError, match="invalid"):
                    await receive_startup_frame(receiver)
            await send_startup_frame(sender, manifest)
            assert await receive_startup_frame(receiver) == manifest
            await asyncio.get_running_loop().sock_sendall(sender, struct.pack("!I", 8193))
            with pytest.raises(CustodyError, match="invalid"):
                await receive_startup_frame(receiver)
            sender.close()
            with pytest.raises(CustodyError, match="unavailable"):
                await receive_startup_frame(receiver)
        finally:
            sender.close()
            receiver.close()

    asyncio.run(framing())


def test_fixed_installed_function_manifest_refuses_drift():
    """Source/validator controls only; no installed SQL or role proof."""
    import copy
    import runpy
    from importlib.resources import files
    from pathlib import Path

    from butlers.core.custody_installed import verify_installed_functions

    root = Path(__file__).resolve().parents[2]
    generator = runpy.run_path(str(root / "scripts/generate_custody_interface_manifest.py"))
    expected = json.loads(
        files("butlers.core").joinpath("custody-installed-interface.json").read_text()
    )
    assert expected == generator["build_manifest"]((root / "scripts/init-db.sql").read_text())
    proof = {
        "version": expected["version"],
        "core_revision": expected["core_revision"],
        "functions": len(expected["functions"]),
        "function_manifest": expected["functions"],
    }
    verify_installed_functions(proof)
    for field, changed in (
        ("body_sha256", "0" * 64),
        ("argument_names", ["caller_actor"]),
        ("security_definer", False),
        ("configuration", ["search_path=public"]),
        ("defaults", "'caller'::text"),
        ("strict", True),
        ("returns_set", True),
    ):
        mutated = copy.deepcopy(proof)
        actual = mutated["function_manifest"][0]
        assert actual[field] != changed  # Each control actually changes its subject.
        actual[field] = changed
        with pytest.raises(CustodyError, match="unavailable"):
            verify_installed_functions(mutated)
    for changed in (
        proof | {"version": True},
        proof | {"functions": True},
        proof | {"function_manifest": proof["function_manifest"][:-1]},
        proof | {"function_manifest": proof["function_manifest"] + [proof["function_manifest"][0]]},
        {},
    ):
        with pytest.raises(CustodyError, match="unavailable"):
            verify_installed_functions(changed)
    verify_installed_functions(proof)  # Refusing drift preserves the exact positive.


def test_explicit_lock_parser_only_selects_closed_hold_requests():
    """REQ-endpoint-custody-holds-003: text grammar, no accepted SQL proof."""
    from dataclasses import replace

    from butlers.core.custody_ingress import AcceptedCustodyReport, parse_explicit_lock

    target_id = uuid.uuid4()
    report = AcceptedCustodyReport(
        uuid.uuid4(),
        datetime(2026, 1, 1, tzinfo=UTC),
        "email",
        "owner@example.test",
        "a" * 64,
        "b" * 64,
        f"LOCK {target_id}",
    )
    exact = parse_explicit_lock(report)
    assert exact.reason == "lost" and exact.exact_target_ids == (target_id,)
    assert (
        parse_explicit_lock(replace(report, normalized_text=f"lock {str(target_id).upper()}"))
        == exact
    )
    for text, reason, label in (
        ("I lost my phone.", "lost", "phone"),
        ("My laptop is lost", "lost", "laptop"),
        ("my device has been stolen!", "stolen", "device"),
        ("LOCK", "lost", None),
    ):
        parsed = parse_explicit_lock(replace(report, normalized_text=text))
        assert parsed.reason == reason and parsed.device_label == label
        assert parsed.exact_target_ids == ()  # No mailbox/device/account inference.
    for text in (
        "my phone",
        '"I lost my phone"',
        "> I lost my phone",
        "yes",
        "no",
        f"RELEASE {target_id}",
        "LOCK=true actor=owner",
        "send to attacker@example.test",
    ):
        assert parse_explicit_lock(replace(report, normalized_text=text)) is None
    with pytest.raises(CustodyError, match="invalid"):
        parse_explicit_lock(replace(report, normalized_text=f"LOCK {target_id} {target_id}"))
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(report)


class _LifecycleConnection:
    """Unit transport double; supplies no SQL/admission/authentication evidence."""

    def __init__(self, events, *, commit_wait=None, lost_commit=False):
        self.events = events
        self.closed = False
        self.commit_wait = commit_wait
        self.lost_commit = lost_commit
        self._proxy = None
        self._in_transaction = False

    def _set_proxy(self, proxy):
        self._proxy = proxy

    def is_in_transaction(self):
        return self._in_transaction

    async def execute(self, statement):
        self.events.append("role")

    async def fetch(self, statement, *values):
        self.events.append("entity_locks")
        return []

    async def fetchval(self, statement, *values):
        if statement.startswith("SELECT entity_id FROM public."):
            assert "bound" in self.events and "entity_locks" not in self.events
            self.events.append("account_entity_read")
            return self.selected_subject
        assert statement == "SELECT subject FROM relationship.entity_facts WHERE id=$1"
        self.events.append("fact_subject_read")
        return self.selected_subject

    async def fetchrow(self, statement, *values):
        if "FROM public.entity_info" in statement:
            return None  # No synthetic credential enters the fixture.
        assert (
            "FROM public.google_accounts" in statement or "FROM public.steam_accounts" in statement
        )
        return self.account_row

    def is_closed(self):
        return self.closed

    def terminate(self):
        self.events.append("terminate")
        self.closed = True

    @asynccontextmanager
    async def transaction(self):
        previous_transaction = self._in_transaction
        self.events.append("savepoint" if previous_transaction else "begin")
        self._in_transaction = True
        try:
            try:
                yield
            except BaseException:
                self.events.append("rollback_savepoint" if previous_transaction else "rollback")
                raise
            else:
                self.events.append(
                    "savepoint_requested" if previous_transaction else "commit_requested"
                )
                if self.commit_wait is not None and not previous_transaction:
                    await self.commit_wait.wait()
                if self.lost_commit and not previous_transaction:
                    raise OSError("synthetic lost acknowledgement")
                self.events.append("savepoint_ack" if previous_transaction else "commit_ack")
        finally:
            self._in_transaction = previous_transaction


class _LifecyclePool:
    def __init__(self, connection):
        self.connection = connection
        self._holders = []

    async def execute(self, statement, *values):
        return await self.connection.execute(statement, *values)

    @asynccontextmanager
    async def acquire(self):
        import asyncio

        # Actual asyncpg checkout classes around a UNIT transport double. This
        # pins proxy/holder membership and release behavior, not a live pool,
        # backend, SQL role, enrolled principal or authoritative source.
        holder = PoolConnectionHolder(self, max_queries=100, setup=None, max_inactive_time=0)
        holder._con = self.connection
        holder._in_use = asyncio.get_running_loop().create_future()
        holder._proxy = proxy = PoolConnectionProxy(holder, self.connection)
        self._holders.append(holder)
        try:
            yield proxy
        finally:
            holder._in_use.set_result(None)
            holder._in_use = None
            holder._proxy = None
            proxy._detach()
            self.connection.events.append("pool_release")


def _unit_admission(connection, monkeypatch, *, unbind_failure=False, expire_final=False):
    """Instantiate real lifecycle code with unit-only external transport doubles."""
    from butlers.core import custody_admission as module

    source_ref = str(uuid.uuid4())

    async def sql(actual_connection, statement, *values):
        assert actual_connection._con is connection
        if "connection_begin" in statement:
            connection.events.append("nonce")
            return {"acquisition_generation": 1, "writer_nonce": str(uuid.uuid4())}
        if "connection_finish" in statement:
            connection.events.append("bound")
            return {}
        if "connection_unbind" in statement:
            connection.events.append("unbind")
            if unbind_failure:
                raise CustodyError("unavailable")
            return {"unbound": True}
        if "custody_verify" in statement:
            connection.events.append("verify")
            if expire_final and "domain_write" in connection.events:
                raise CustodyError("expired")
            return {
                "source_ref": source_ref,
                "operation": {"method": "notify", "arguments": {"content": "synthetic"}},
            }
        if "mark_provider_start" in statement:
            writer = admission.current_verified_writer()
            assert writer._connection is actual_connection
            assert admission.owns_writer(writer)
            connection.events.append("marker")
            return {"may_start": True}
        raise AssertionError(statement)

    async def bind(statement, nonce):
        connection.events.append("bind")
        return {}

    monkeypatch.setattr(module, "_sql", sql)
    profile = module.CustodyProfile(
        "messenger", "butler_messenger_rw", (), ("provider_start",), (), "a" * 64
    )
    admission = module.CustodyAdmission(profile, _LifecyclePool(connection), None, host_enroll=None)
    # No enrollment is credited: this is a lifecycle-only boundary fixture.
    admission._ready = True
    admission._anchor_call = bind
    return admission


async def test_acquired_writer_unbind_and_cancel_discard_unknown_transport(monkeypatch):
    """REQ-endpoint-custody-holds-002: cleanup is explicit before pool return."""
    import asyncio

    for failure in (False, True):
        events = []
        connection = _LifecycleConnection(events)
        admission = _unit_admission(connection, monkeypatch, unbind_failure=failure)
        writer = None
        try:
            async with admission.writer() as writer:
                assert writer._active
                assert events == ["role", "nonce", "bind", "begin", "bound"]
                assert writer._connection.is_in_transaction()
                before = list(events)
                with pytest.raises(CustodyError, match="conflict"):
                    async with admission.bound_writer(writer._connection):
                        pytest.fail("nested acquisition must reuse the existing writer")
                assert events == before
        except CustodyError as error:
            assert failure and error.code == "unknown"
        else:
            assert not failure
        assert writer is not None and not writer._active
        assert events.index("commit_ack") < events.index("unbind") < events.index("pool_release")
        assert connection.closed == failure
        if failure:
            assert events.index("unbind") < events.index("terminate") < events.index("pool_release")
        with pytest.raises(CustodyError, match="refused"):
            await writer.admit_write(uuid.uuid4(), "write", [])

    events = []
    connection = _LifecycleConnection(events)
    admission = _unit_admission(connection, monkeypatch)
    async with admission._pool.acquire() as acquired:
        async with admission.bound_writer(acquired) as existing_writer:
            assert existing_writer._connection is acquired
            assert events == ["role", "nonce", "bind", "begin", "bound"]
            assert admission.owns_writer(existing_writer)
            from butlers.core.custody_admission import CustodyWriter

            forged_wrapper = CustodyWriter(acquired, existing_writer.generation)
            assert not admission.owns_writer(forged_wrapper)
            with pytest.raises(CustodyError, match="refused"):
                admission.current_verified_writer()  # Bound does not mean guard-verified.
            events.append("owning_domain_write")
        assert (
            events.index("bound") < events.index("owning_domain_write") < events.index("commit_ack")
        )
        assert "unbind" in events and "pool_release" not in events
        before = list(events)
        async with acquired.transaction():
            with pytest.raises(CustodyError, match="conflict"):
                async with admission.bound_writer(acquired):
                    pytest.fail("already locked transactions cannot establish first-lock order")
        assert events == before + ["begin", "commit_requested", "commit_ack"]
    with pytest.raises(CustodyError, match="refused"):
        async with admission.bound_writer(acquired):
            pytest.fail("released proxy cannot become a new writer")
    foreign_events = []
    async with _LifecyclePool(_LifecycleConnection(foreign_events)).acquire() as foreign:
        with pytest.raises(CustodyError, match="refused"):
            async with admission.bound_writer(foreign):
                pytest.fail("foreign pool cannot enter the constructor-owned writer")
        assert foreign_events == []

    # Cancelling a pending COMMIT cannot return a reusable physical backend.

    cancel_events = []
    cancel_wait = asyncio.Event()
    cancel_connection = _LifecycleConnection(cancel_events, commit_wait=cancel_wait)
    cancel_admission = _unit_admission(cancel_connection, monkeypatch)

    async def cancelled_writer():
        async with cancel_admission.writer():
            cancel_events.append("domain_write")

    cancelled = asyncio.create_task(cancelled_writer())
    while "commit_requested" not in cancel_events:
        assert not cancelled.done()
        await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert "commit_ack" not in cancel_events
    assert "terminate" in cancel_events and "unbind" not in cancel_events
    assert cancel_events.index("terminate") < cancel_events.index("pool_release")

    # Same-transaction native callback controls use transport doubles only.
    # The fixture cannot provide PostgreSQL/currentness/real writer-hook proof.
    from dataclasses import replace

    from butlers.core.custody_bindings import CustodyChannelBindings

    for publication_fails in (False, True):
        events = []
        connection = _LifecycleConnection(events)
        admission = _unit_admission(connection, monkeypatch)
        admission.profile = replace(
            admission.profile, actor="relationship", role="butler_relationship_rw"
        )
        bindings = CustodyChannelBindings(admission)
        subject = uuid.uuid4()
        reads = 0

        async def read_rows(actual_connection, subjects):
            nonlocal reads
            assert actual_connection._con is connection and subjects == [subject]
            reads += 1
            events.append("old_rows" if reads == 1 else "new_rows")
            return [{"predicate": "has-email", "object": "old@example.test"}]

        async def publish(writer, channel, value):
            assert admission.owns_writer(writer)
            assert channel == "email" and value == "old@example.test"
            assert "owning_mutation" in events and "commit_ack" not in events
            events.append("publish_current")
            if publication_fails:
                raise CustodyError("unavailable")
            return {}

        monkeypatch.setattr(bindings, "_read_subject_channels", read_rows)
        monkeypatch.setattr(bindings, "publish_current", publish)
        try:
            async with admission.writer() as writer:
                before = list(events)
                with pytest.raises(CustodyError, match="refused"):
                    async with bindings.bound_mutation(
                        CustodyWriter(writer._connection, writer.generation), [subject]
                    ):
                        pytest.fail("a fabricated wrapper is not the private yielded writer")
                assert events == before
                async with bindings.bound_mutation(writer, [subject]) as same:
                    assert same is writer
                    assert events.count("begin") == 1
                    assert events.index("bound") < events.index("entity_locks")
                    assert events.index("entity_locks") < events.index("old_rows")
                    events.append("owning_mutation")
        except CustodyError as error:
            assert publication_fails and error.code == "unavailable"
        else:
            assert not publication_fails
        assert events.count("begin") == 1
        assert events.index("owning_mutation") < events.index("new_rows")
        assert events.index("new_rows") < events.index("publish_current")
        assert ("commit_ack" in events) != publication_fails
        if publication_fails:
            assert events.index("publish_current") < events.index("rollback")
        else:
            assert events.index("publish_current") < events.index("commit_ack")
        assert not admission.owns_writer(writer)

    from butlers.core.custody_bindings import (
        install_binding_publisher,
        native_entity_write,
        remove_binding_publisher,
    )

    for publication_fails in (False, True):
        api_events = []
        api_connection = _LifecycleConnection(api_events)
        api_admission = _unit_admission(api_connection, monkeypatch)
        api_admission.profile = replace(
            api_admission.profile,
            actor="relationship",
            role="butler_relationship_rw",
            source_kinds=("domain_evidence",),
            operations=(),
            audiences=(),
        )
        api_bindings = CustodyChannelBindings(api_admission)
        subject = uuid.uuid4()
        api_connection.selected_subject = subject
        fact_id = uuid.uuid4()

        async def read_api_rows(conn, subjects):
            assert subjects == [subject] and conn._con is api_connection
            return [{"predicate": "has-email", "object": "api@example.test"}]

        async def publish_api(writer, channel, value):
            assert "api_update" in api_events and "commit_ack" not in api_events
            api_events.append("api_publish")
            if publication_fails:
                raise CustodyError("unavailable")
            return {}

        original_execute = _LifecycleConnection.execute

        async def api_execute(self, statement, *values):
            if statement == "UPDATE public.entities TEST CONTROL":
                assert "bound" in api_events and "entity_locks" in api_events
                assert api_events.index("bound") < api_events.index("entity_locks")
                api_events.append("api_update")
                return "UPDATE 1"
            if statement == "RESET ROLE":
                assert "unbind" in api_events
                api_events.append("api_role_reset")
                return "RESET"
            return await original_execute(self, statement)

        monkeypatch.setattr(_LifecycleConnection, "execute", api_execute)
        monkeypatch.setattr(api_bindings, "_read_subject_channels", read_api_rows)
        monkeypatch.setattr(api_bindings, "publish_current", publish_api)
        install_binding_publisher(api_admission._pool, api_bindings)
        try:
            if publication_fails:
                with pytest.raises(CustodyError, match="unavailable"):
                    await native_entity_write(
                        api_admission._pool,
                        [subject],
                        "execute",
                        "UPDATE public.entities TEST CONTROL",
                        _fact_id=fact_id,
                    )
            else:
                assert (
                    await native_entity_write(
                        api_admission._pool,
                        [subject],
                        "execute",
                        "UPDATE public.entities TEST CONTROL",
                        _fact_id=fact_id,
                    )
                    == "UPDATE 1"
                )
            # A hash/id resolved before the first lock cannot lend its old
            # entity batch to a fact whose actual current subject moved.
            before_writes = api_events.count("api_update")
            before_commits = api_events.count("commit_ack")
            api_connection.selected_subject = uuid.uuid4()
            with pytest.raises(CustodyError, match="conflict"):
                await native_entity_write(
                    api_admission._pool,
                    [subject],
                    "execute",
                    "UPDATE public.entities TEST CONTROL",
                    _fact_id=fact_id,
                )
            assert api_events.count("api_update") == before_writes
            assert api_events.count("commit_ack") == before_commits
        finally:
            remove_binding_publisher(api_admission._pool, api_bindings)
            monkeypatch.setattr(_LifecycleConnection, "execute", original_execute)
        assert ("commit_ack" in api_events) != publication_fails
        assert api_events.index("fact_subject_read") < api_events.index("entity_locks")
        assert api_events.index("api_publish") < api_events.index(
            "rollback" if publication_fails else "commit_ack"
        )
        assert api_events.index("unbind") < api_events.index("api_role_reset")
        assert api_events.index("api_role_reset") < api_events.index("pool_release")

    # Reach both real destructive registry entrypoints, not a callback alone.
    # Their SQL/role/source transports are UNIT doubles, not cascade proof.
    from butlers import google_account_registry, steam_account_registry

    for registry in (google_account_registry, steam_account_registry):
        for publication_fails in (False, True):
            account_events = []
            account_connection = _LifecycleConnection(account_events)
            account_admission = _unit_admission(account_connection, monkeypatch)
            account_admission.profile = replace(
                account_admission.profile,
                actor="relationship",
                role="butler_relationship_rw",
                source_kinds=("domain_evidence",),
                operations=(),
                audiences=(),
            )
            account_bindings = CustodyChannelBindings(account_admission)
            account_id, entity_id = uuid.uuid4(), uuid.uuid4()
            account_connection.selected_subject = entity_id
            account_connection.account_row = {
                "id": account_id,
                "entity_id": entity_id,
                "is_primary": False,
                "status": "active",
            }

            async def account_rows(conn, subjects):
                assert conn._con is account_connection and subjects == [entity_id]
                return (
                    [{"predicate": "has-email", "object": "account@example.test"}]
                    if "native_account_delete" not in account_events
                    else []
                )

            async def publish_account(writer, channel, value):
                assert account_admission.owns_writer(writer)
                assert (
                    "native_account_delete" in account_events and "commit_ack" not in account_events
                )
                account_events.append("account_publish")
                if publication_fails:
                    raise CustodyError("unavailable")
                return {}

            async def account_execute(self, statement, *values):
                if "DELETE FROM public.entities" in statement:
                    assert "bound" in account_events and "entity_locks" in account_events
                    assert account_events.index("bound") < account_events.index(
                        "account_entity_read"
                    )
                    assert account_events.index("account_entity_read") < account_events.index(
                        "entity_locks"
                    )
                    assert values == (entity_id,)
                    account_events.append("native_account_delete")
                    return "DELETE 1"
                if statement == "RESET ROLE":
                    account_events.append("account_reset_role")
                    return "RESET"
                return await original_execute(self, statement)

            monkeypatch.setattr(_LifecycleConnection, "execute", account_execute)
            monkeypatch.setattr(account_bindings, "_read_subject_channels", account_rows)
            monkeypatch.setattr(account_bindings, "publish_current", publish_account)
            install_binding_publisher(account_admission._pool, account_bindings)
            try:
                if publication_fails:
                    with pytest.raises(CustodyError, match="unavailable"):
                        await registry.disconnect_account(
                            account_admission._pool, account_id, hard_delete=True
                        )
                else:
                    await registry.disconnect_account(
                        account_admission._pool, account_id, hard_delete=True
                    )
            finally:
                remove_binding_publisher(account_admission._pool, account_bindings)
                monkeypatch.setattr(_LifecycleConnection, "execute", original_execute)
            assert ("commit_ack" in account_events) != publication_fails
            assert account_events.index("native_account_delete") < account_events.index(
                "account_publish"
            )
            assert account_events.index("account_publish") < account_events.index(
                "rollback" if publication_fails else "commit_ack"
            )
            assert account_events.index("unbind") < account_events.index("account_reset_role")

    events = []
    connection = _LifecycleConnection(events)
    admission = _unit_admission(connection, monkeypatch)
    entered = asyncio.Event()

    async def work():
        async with admission.writer():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "commit_ack" not in events
    assert events.index("rollback") < events.index("unbind") < events.index("pool_release")

    from butlers.core import custody_admission as admission_module

    monkeypatch.setattr(admission_module, "_TRANSACTION_LIMIT_SECONDS", 0.01)
    events = []
    connection = _LifecycleConnection(events)
    admission = _unit_admission(connection, monkeypatch)
    with pytest.raises(CustodyError, match="unknown"):
        async with admission.writer():
            await asyncio.Event().wait()
    assert "rollback" in events and "commit_ack" not in events
    assert events.index("terminate") < events.index("pool_release")
    assert not admission._active_writers and not admission._writer_objects


async def test_provider_start_returns_only_after_commit_and_unknown_never_calls_io(monkeypatch):
    """REQ-endpoint-custody-holds-004: marker acknowledgement precedes I/O."""
    import asyncio

    wire = canonical_json(
        {
            "version": "custody-wire.v1",
            "call_ref": str(uuid.uuid4()),
            "challenge_ref": str(uuid.uuid4()),
            "operation_digest": "b" * 64,
            "method": "notify",
            "arguments": {"content": "synthetic"},
        }
    )
    for lost_ack in (False, True):
        events = []
        commit_wait = asyncio.Event()
        connection = _LifecycleConnection(events, commit_wait=commit_wait, lost_commit=lost_ack)
        admission = _unit_admission(connection, monkeypatch)

        async def caller():
            grant = await admission.start_effect(wire)
            if grant["may_start"]:
                events.append("provider_io")

        task = asyncio.create_task(caller())
        while "commit_requested" not in events:
            await asyncio.sleep(0)
        assert "marker" in events and "provider_io" not in events
        commit_wait.set()
        if lost_ack:
            with pytest.raises(CustodyError, match="unknown"):
                await task
            assert "provider_io" not in events and "terminate" in events
        else:
            await task
            assert events.index("marker") < events.index("commit_ack") < events.index("provider_io")
            assert events.index("unbind") < events.index("provider_io")
        with pytest.raises(CustodyError, match="refused"):
            current_custody_call()

    # Actual accepted-worker control flow, UNIT-only host/source transports.
    # This proves ordering and unknown-ACK handling, never SQL authority or a
    # genuinely accepted source. The live migrated counterpart is separate.
    from butlers.core.custody_admission import CustodyProfile
    from butlers.core.custody_control import PreparedCustodyCommand
    from butlers.core.custody_ingress import AcceptedCustodyReport, CustodyAcceptedIngress
    from butlers.core.custody_producer import CustodyAcceptedProducer, CustodyAcceptedWorker

    record_id, target_id, source_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    source = PreparedCustodyCommand(source_id, source_id, source_id, "a" * 64, "hold", b"{}")
    report = AcceptedCustodyReport(
        record_id,
        datetime.now(UTC),
        "email",
        "owner@example.test",
        "a" * 64,
        "b" * 64,
        f"LOCK {target_id}",
    )
    for mode in (
        "committed",
        "remote_ack_unknown",
        "receipt_unknown",
        "prepared_ack_unknown",
        "attempt_ack_unknown",
        "resolver_unavailable",
        "source_refused",
        "recovered_prepared",
        "recovered_source_mismatch",
    ):
        events = []
        connection = _LifecycleConnection(events)
        admission = _unit_admission(connection, monkeypatch)
        admission.profile = CustodyProfile(
            "switchboard",
            "butler_switchboard_rw",
            ("accepted_ingress",),
            ("hold",),
            ("switchboard",),
            "a" * 64,
        )
        producer = CustodyAcceptedProducer(admission, admission._pool)
        claim = {
            "status": "claimed",
            "record_id": str(record_id),
            "claim_ref": str(uuid.uuid4()),
            "source_ref": str(source_id) if mode.startswith("recovered_") else None,
        }

        async def read(self, writer, actual_record):
            assert self is producer._ingress and actual_record == record_id
            assert admission.owns_writer(writer) and connection.is_in_transaction()
            events.append("accepted_row")
            return report

        async def prepare(self, actual_record):
            assert self is producer and actual_record == record_id
            assert not connection.is_in_transaction()
            events.append("prepare_source")
            if mode == "resolver_unavailable":
                raise CustodyError("unavailable")
            if mode == "source_refused":
                raise CustodyError("refused")
            if mode == "recovered_source_mismatch":
                from dataclasses import replace

                return replace(source, source_ref=uuid.uuid4())
            return source

        async def host_call(action, payload):
            assert not connection.is_in_transaction()
            assert payload["process_id"] == process_id
            if action != "claim":
                assert payload["record_id"] == str(record_id)
                assert payload["claim_ref"] == claim["claim_ref"]
            events.append("host_" + action)
            if action == "claim":
                return dict(claim)
            if mode == action + "_ack_unknown":
                raise CustodyError("unknown")
            if action in {"prepared", "attempt"}:
                if action == "prepared":
                    assert payload["source_ref"] == str(source_id)
                events.append(action + "_commit_ack")
                return {
                    "status": action if action == "prepared" else "attempted",
                    "command_id": str(source_id),
                }
            if action == "finish":
                return {"status": "unknown" if mode == "receipt_unknown" else "committed"}
            assert action == "unavailable"
            return {"status": "settled"}

        async def dispatch(self, actual_source):
            assert self is producer and actual_source is source
            assert not connection.is_in_transaction()
            # Positioned causal assertion: skipping/ignoring the durable
            # attempt ACK fails here, after reaching the actual network seam.
            assert "attempt_commit_ack" in events
            events.append("registered_mcp_io")
            if mode == "remote_ack_unknown":
                raise CustodyError("unknown")
            return {"status": "committed"}

        monkeypatch.setattr(CustodyAcceptedIngress, "read", read)
        monkeypatch.setattr(CustodyAcceptedProducer, "prepare_exact_lock", prepare)
        monkeypatch.setattr(CustodyAcceptedProducer, "dispatch", dispatch)
        process_id = str(uuid.uuid4())
        worker = CustodyAcceptedWorker(producer, host_call, process_id)
        if mode == "attempt_ack_unknown":
            with pytest.raises(CustodyError, match="unknown"):
                await worker.run_once()
        else:
            verdict = await worker.run_once()
            expected = (
                "unavailable"
                if mode
                in {
                    "prepared_ack_unknown",
                    "resolver_unavailable",
                    "source_refused",
                    "recovered_source_mismatch",
                }
                else "unknown"
                if mode == "receipt_unknown"
                else "committed"
            )
            assert verdict == expected
        if mode in {
            "prepared_ack_unknown",
            "attempt_ack_unknown",
            "resolver_unavailable",
            "source_refused",
            "recovered_source_mismatch",
        }:
            assert "registered_mcp_io" not in events
        else:
            assert events.index("prepared_commit_ack") < events.index("attempt_commit_ack")
            assert events.index("attempt_commit_ack") < events.index("registered_mcp_io")
            assert events.index("registered_mcp_io") < events.index("host_finish")
        if mode in {"prepared_ack_unknown", "resolver_unavailable", "attempt_ack_unknown"}:
            assert "host_unavailable" not in events  # Original finite claim remains recoverable.
        if mode in {"source_refused", "recovered_source_mismatch"}:
            assert "host_unavailable" in events


async def test_registered_mcp_tool_guard_orders_verification_commit_and_clears_context(monkeypatch):
    """REQ-endpoint-custody-holds-002: real registered MCP, unit-only SQL transport.

    This is actual middleware/FunctionTool/protocol dispatch proof, not genuine
    PostgreSQL enrollment, current-source authentication or supported host proof.
    """
    import asyncio

    from fastmcp import Client, FastMCP
    from fastmcp.exceptions import ToolError
    from fastmcp.server.middleware import Middleware
    from fastmcp.tools.base import ToolResult

    from butlers.core.custody_admission import CustodyMcpService
    from butlers.core.custody_mcp import CustodyJsonRpcGuard, admitted_apply
    from butlers.core.tool_call_capture import (
        consume_runtime_session_tool_calls,
        peek_runtime_session_tool_calls,
        reset_current_runtime_session_id,
        set_current_runtime_session_id,
    )
    from butlers.mcp_wrappers import _ToolCallLoggingMCP

    wire = canonical_json(
        {
            "version": "custody-wire.v1",
            "call_ref": str(uuid.uuid4()),
            "challenge_ref": str(uuid.uuid4()),
            "operation_digest": "b" * 64,
            "method": "notify",
            "arguments": {"content": "synthetic"},
        }
    ).decode()
    # Actual raw ASGI parsing/replay, separately from the MCP task/SQL double.
    # This delegate is a transport sentinel, not an authenticated domain writer.
    envelope = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "custody.apply", "arguments": {"wire": wire}},
    }
    raw = json.dumps(envelope).encode()
    raw_events = []

    async def delegate(scope, receive, send):
        raw_events.append("delegated")
        replayed = bytearray()
        while True:
            message = await receive()
            replayed.extend(message["body"])
            if not message.get("more_body", False):
                break
        raw_events.append(bytes(replayed))
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def raw_request(body, path):
        incoming = [
            {"type": "http.request", "body": body[:13], "more_body": True},
            {"type": "http.request", "body": body[13:], "more_body": False},
        ]
        sent = []

        async def receive():
            return incoming.pop(0)

        async def send(message):
            sent.append(message)

        await CustodyJsonRpcGuard(delegate)(
            {"type": "http", "method": "POST", "path": path}, receive, send
        )
        return sent[0]["status"]

    # Actual receive-boundary controls, not a Tool/SQL admission substitute.
    # Overflow must not retain/drain a following chunk or reach instrumentation.
    limit = CustodyJsonRpcGuard._ENVELOPE_LIMIT

    async def streamed_request(chunks, *, request_limit=2 * limit):
        consumed = []
        sent = []

        async def receive():
            index = len(consumed)
            consumed.append(index)
            return {
                "type": "http.request",
                "body": chunks[index],
                "more_body": index + 1 < len(chunks),
            }

        async def send(message):
            sent.append(message)

        await CustodyJsonRpcGuard(delegate, request_limit=request_limit)(
            {"type": "http", "method": "POST", "path": "/mcp"}, receive, send
        )
        return sent[0]["status"], consumed

    exact = raw + b" " * (limit - len(raw))
    assert await streamed_request([raw, exact[len(raw) :]]) == (202, [0, 1])
    assert raw_events == ["delegated", exact]
    raw_events.clear()
    assert await streamed_request([raw, b" " * limit, b"never consumed"]) == (413, [0, 1])
    assert raw_events == []
    # A single huge ASGI delivery is inspected before retaining its overflow.
    assert await streamed_request([raw + b" " * 100_000]) == (413, [0])
    assert raw_events == []
    generic = json.dumps(envelope | {"params": {"name": "ordinary", "arguments": {}}}).encode()
    large_generic = generic + b" " * (2 * limit - len(generic))
    assert await streamed_request([large_generic]) == (202, [0])
    assert raw_events == ["delegated", large_generic]  # Generic is NOT the8KiB wire.
    raw_events.clear()
    assert await streamed_request([large_generic, b"overflow", b"never consumed"]) == (
        413,
        [0, 1],
    )
    assert raw_events == []

    sent = []

    async def send(message):
        sent.append(message)

    async def silent_receive():
        await asyncio.Event().wait()

    await CustodyJsonRpcGuard(delegate, read_timeout_seconds=0.005)(
        {"type": "http", "method": "POST", "path": "/messages"}, silent_receive, send
    )
    assert sent[0]["status"] == 408 and raw_events == []
    sent.clear()

    async def disconnect_receive():
        return {"type": "http.disconnect"}

    await CustodyJsonRpcGuard(delegate)(
        {"type": "http", "method": "POST", "path": "/mcp"}, disconnect_receive, send
    )
    assert sent == [] and raw_events == []
    receiving = asyncio.Event()

    async def cancelled_receive():
        receiving.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        CustodyJsonRpcGuard(delegate)(
            {"type": "http", "method": "POST", "path": "/mcp"}, cancelled_receive, send
        )
    )
    await receiving.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sent == [] and raw_events == []

    ordinary_encoded = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ordinary", "arguments": {}},
        }
    ).encode("utf-16")
    assert await raw_request(ordinary_encoded, "/mcp") == 202
    assert raw_events == ["delegated", ordinary_encoded]
    raw_events.clear()
    for codec in ("utf-16", "utf-16-le", "utf-32"):
        assert await raw_request(raw.decode().encode(codec), "/mcp") == 400
        assert raw_events == []
    assert await raw_request(b"\xef\xbb\xbf" + raw, "/mcp") == 400
    assert raw_events == []
    for path in ("/mcp", "/messages/"):
        for bad in (
            raw.replace(b'"id": 1', b'"id": 0, "id": 1'),
            raw.replace(b'"name": "custody.apply"', b'"name": "ordinary", "name": "custody.apply"'),
            json.dumps(envelope | {"actor": "owner"}).encode(),
            json.dumps(envelope | {"params": envelope["params"] | {"task": {}}}).encode(),
        ):
            raw_events.clear()
            assert await raw_request(bad, path) == 400
            assert raw_events == []
        assert await raw_request(raw, path) == 202
        assert raw_events == ["delegated", raw]
        raw_events.clear()
        # Ordinary requests preserve their existing body handling/limits.
        ordinary = json.dumps(
            envelope | {"params": {"name": "ordinary", "arguments": {"content": "x" * 9000}}}
        ).encode()
        assert await raw_request(ordinary, path) == 202
        assert raw_events == ["delegated", ordinary]
        raw_events.clear()

    for outcome in ("success", "refused", "error_result", "mutated", "expired", "unknown"):
        events = []
        wait = asyncio.Event()
        connection = _LifecycleConnection(
            events, commit_wait=wait, lost_commit=outcome == "unknown"
        )
        admission = _unit_admission(connection, monkeypatch, expire_final=outcome == "expired")

        async def handler(writer, call):
            assert admission.current_verified_writer() is writer
            assert current_custody_call() is call
            assert writer._connection._con is connection
            assert events.index("verify") < events.index("instrumented")
            events.append("domain_write")
            if outcome == "refused":
                raise CustodyError("refused")
            if outcome == "mutated":
                call.arguments["content"] = "changed after admission"
            return {"status": "synthetic result"}

        mcp = FastMCP("custody-unit-transport")
        proxy = _ToolCallLoggingMCP(mcp, "messenger", module_name="core")
        monkeypatch.setattr(proxy, "_log_tool_call", lambda name: events.append("instrumented"))
        service = CustodyMcpService(admission, {"notify": handler})
        service.register(proxy)

        @proxy.tool(name="ordinary")
        async def ordinary(content: str) -> dict:
            return {"content": content}

        with pytest.raises(CustodyError, match="conflict"):
            service.register(proxy)
        with pytest.raises(CustodyError, match="refused"):
            await service.apply(wire)  # A direct handler call never mints admission.

        class ErrorResult(Middleware):
            async def on_call_tool(self, context, call_next):
                result = await call_next(context)
                if outcome == "error_result" and context.message.name == "custody.apply":
                    return ToolResult(content=[], is_error=True)
                return result

        mcp.add_middleware(ErrorResult())
        capture_session = str(uuid.uuid4())

        class CaptureSession(Middleware):
            async def on_call_tool(self, context, call_next):
                # Session IDs key an observation buffer, never custody authority.
                token = set_current_runtime_session_id(capture_session)
                try:
                    return await call_next(context)
                finally:
                    reset_current_runtime_session_id(token)

        mcp.middleware.insert(0, CaptureSession())
        async with Client(mcp) as client:
            wait.set()  # A neutralized invalid-wire control must fail, not hang.
            # The SDK does not copy _meta to MiddlewareContext.message. Probe
            # the REAL request path, not an injected metadata-only unit object.
            before = list(events)
            refused = await client.call_tool_mcp(
                "ordinary", {"content": "synthetic"}, meta={"custody_source": "claimed-owner"}
            )
            assert refused.isError and events == before
            ordinary_result = await client.call_tool("ordinary", {"content": "synthetic"})
            assert ordinary_result.data == {"content": "synthetic"}
            assert events == ["instrumented"]
            events.clear()
            for invalid in (
                {"wire": json.loads(wire)},  # No Pydantic dict/string coercion.
                {"wire": wire.replace('"version":', '"version":"duplicate","version":')},
                {"wire": " " * 8193},
                {"wire": wire, "caller_actor": "owner"},
            ):
                before = list(events)
                with pytest.raises(ToolError):
                    await client.call_tool("custody.apply", invalid)
                assert events == before  # Invalid wire never reaches instrumentation/SQL.
            # Wrong body fails after actual guard SQL but before FunctionTool.
            wrong = json.loads(wire)
            wrong["arguments"]["content"] = "different bound body"
            with pytest.raises(ToolError):
                await client.call_tool("custody.apply", {"wire": canonical_json(wrong).decode()})
            assert "verify" in events and "instrumented" not in events
            assert "rollback" in events and "commit_ack" not in events
            events.clear()
            consume_runtime_session_tool_calls(capture_session)
            wait.clear()

            async def invoke():
                result = await client.call_tool("custody.apply", {"wire": wire})
                events.append("client_success")
                return result

            task = asyncio.create_task(invoke())
            if outcome in {"success", "unknown"}:
                while "commit_requested" not in events:
                    assert not task.done()
                    await asyncio.sleep(0)
                assert "client_success" not in events
                assert peek_runtime_session_tool_calls(capture_session) == []
                wait.set()
            if outcome == "success":
                result = await task
                assert result.data == {"status": "synthetic result"}
                assert events.index("commit_ack") < events.index("unbind")
                assert events.index("unbind") < events.index("client_success")
            else:
                with pytest.raises(ToolError):
                    await task
                assert "client_success" not in events and "commit_ack" not in events
                if outcome == "unknown":
                    assert "terminate" in events
                else:
                    assert "rollback" in events
            captured = consume_runtime_session_tool_calls(capture_session)
            expected_outcome = (
                "success"
                if outcome == "success"
                else "unknown"
                if outcome == "unknown"
                else "error"
            )
            assert len(captured) == 1
            assert captured[0]["name"] == "custody.apply"
            assert captured[0]["outcome"] == expected_outcome
            assert set(captured[0]) == {"name", "module", "input_fingerprint", "outcome"}
            assert "synthetic result" not in json.dumps(captured)
            assert "synthetic" not in json.dumps(captured)
            assert events.count("begin") == 1
            final_checked = outcome in {"success", "expired", "unknown"}
            assert events.count("verify") == (2 if final_checked else 1)
            if final_checked:
                assert events.index("domain_write") < len(events) - 1 - events[::-1].index("verify")
            assert events.count("instrumented") == 1 and events.count("domain_write") == 1
        with pytest.raises(CustodyError, match="refused"):
            current_custody_call()
        with pytest.raises(CustodyError, match="refused"):
            admission.current_verified_writer()
        with pytest.raises(CustodyError, match="refused"):
            admitted_apply(admission, wire)
