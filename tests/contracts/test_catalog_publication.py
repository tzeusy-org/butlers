"""D4 actual final registry publication and complete fleet reader contracts.

REQ-capability-authorization-002.
"""

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastmcp import FastMCP

from butlers.core.catalog_publication import (
    CatalogPublisher,
    authorization_digest,
    close_catalog_publication,
    digest,
    publication,
    start_catalog_publication,
    verify_role,
)
from butlers.core.catalog_reader import DOMAINS, EXCLUSIONS, PublishedCatalogProvider
from butlers.core.goal_admission import Evidence, EvidenceRefused, GoalAdmission, derive_admission
from butlers.core.tool_catalog import RecipientParameters, build_tool_catalog
from butlers.modules.base import ToolMeta
from butlers.testing.fake_pool import AsyncCtx

pytestmark = pytest.mark.contract


def test_admission_defaults_to_production_catalog_reader():
    admission = GoalAdmission(None, None)
    from butlers.core.catalog_reader import PublishedCatalogProvider

    assert isinstance(admission.catalog, PublishedCatalogProvider)


# This stateful SQL protocol double is SOFTWARE evidence only. The integration
# owner separately exercises actual functions, roles, RLS, commit and races.


async def registered_catalog(
    domain="health",
    *,
    description="Read a native value.",
    approval_enabled=False,
):
    mcp = FastMCP("publication-fixture")

    @mcp.tool(description=description)
    async def lookup(query: str) -> str:
        return query

    if approval_enabled:
        from butlers.config import parse_approval_config
        from butlers.modules.approvals.gate import apply_approval_gates

        await apply_approval_gates(
            mcp,
            parse_approval_config({"enabled": True, "gated_tools": {"lookup": {}}}),
            pool=object(),
            butler_name=domain,
        )
    catalog = await build_tool_catalog(
        mcp,
        tool_owners={"lookup": "native"},
        tool_metadata={
            "lookup": ToolMeta(
                canonical_name="lookup",
                module_name="native",
                group_name="read",
                namespace="native.read",
                llm_presentable=True,
                load_posture="deferred",
                owner_domain=domain,
                effect_class="read",
                output_trust="untrusted",
                output_sensitivity="ordinary",
                resource_params=(),
                selection_bound={},
                safety_args=(),
                approval_policy={"enabled": approval_enabled},
                idempotency="none",
                owner_authority=False,
                recipient_params=RecipientParameters(),
            ),
        },
    )
    return mcp, catalog


class Store:
    def __init__(self):
        self.enabled = True
        self.members = [
            {"domain": name, "included": True, "exclusion_reason": None} for name in DOMAINS
        ] + [
            {"domain": name, "included": False, "exclusion_reason": reason}
            for name, reason in EXCLUSIONS.items()
        ]
        self.current = {}
        self.history = []
        self.lost_reply = False


class Connection:
    def __init__(self, store, domain):
        self.store, self.domain = store, domain
        self.tx = False
        self.calls = []

    def is_in_transaction(self):
        return self.tx

    @asynccontextmanager
    async def transaction(self):
        old_current, old_history = copy.deepcopy(self.store.current), list(self.store.history)
        self.tx = True
        try:
            yield
        except BaseException:
            self.store.current, self.store.history = old_current, old_history
            raise
        else:
            if self.store.lost_reply:
                self.store.lost_reply = False
                raise ConnectionError("controlled commit reply lost")
        finally:
            self.tx = False

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if "FROM pg_roles" in sql:
            return {"name": f"butler_{self.domain}_rw", "rolsuper": False, "rolbypassrls": False}
        assert "AS registrations" in sql
        return {
            "enabled": self.store.enabled,
            "members": copy.deepcopy(self.store.members),
            "registrations": copy.deepcopy(list(self.store.current.values())),
        }

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return [
            {
                "relname": name,
                "relrowsecurity": True,
                "relforcerowsecurity": False,
                "database_owned": True,
                "owner_member": False,
            }
            for name in args[0]
        ]

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        if "pg_has_role" in sql:
            return False
        if "SELECT count(*) FROM pg_class" in sql:
            return 4
        if "to_regclass" in sql:
            return "capability_catalog_control"
        if "SELECT enabled" in sql:
            return self.store.enabled
        if "SELECT EXISTS" in sql:
            domain, instance, revision = args
            row = self.store.current.get(domain)
            return bool(
                row
                and row["instance_id"] == str(instance)
                and row["revision_id"] == str(revision)
                and row["live"]
            )
        assert "renew_capability_catalog" in sql
        domain, instance, revision, lease = args
        assert lease == 90
        row = self.store.current[domain]
        return bool(
            row["live"]
            and row["instance_id"] == str(instance)
            and row["revision_id"] == str(revision)
        )

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        if "publish_capability_catalog" in sql:
            domain, instance, revision, body, body_digest, auth_digest, lease, expected = args
            assert domain == self.domain and lease == 90 and self.store.enabled
            old = self.store.current.get(domain)
            if expected is None:
                if any(row["instance_id"] == str(instance) for row in self.store.history):
                    raise EvidenceRefused("catalog_unavailable")
            elif not old or (old["instance_id"], old["revision_id"], old["live"]) != (
                str(instance),
                str(expected),
                True,
            ):
                raise EvidenceRefused("catalog_unavailable")
            row = {
                "domain": domain,
                "instance_id": str(instance),
                "revision_id": str(revision),
                "epoch": old["epoch"] + 1 if old else 1,
                "body": copy.deepcopy(body),
                "body_digest": body_digest,
                "authorization_digest": auth_digest,
                "live": True,
            }
            self.store.current[domain] = row
            self.store.history.append(copy.deepcopy(row))
        else:
            assert "SET active=false" in sql
            domain, instance = args
            row = self.store.current.get(domain)
            if row and row["instance_id"] == str(instance):
                row["live"] = False


class Pool:
    def __init__(self, store, domain):
        self.connection = Connection(store, domain)

    async def fetchval(self, sql, *args):
        return await self.connection.fetchval(sql, *args)

    def acquire(self):
        return AsyncCtx(self.connection)


async def test_publisher_accepts_maintenance_posture_and_refuses_runtime_bypass():
    class RoleConnection(Connection):
        role = {"name": "butler_health_rw", "rolsuper": False, "rolbypassrls": False}
        changed = None

        async def fetchrow(self, sql, *args):
            if "FROM pg_roles" in sql:
                return self.role
            return await super().fetchrow(sql, *args)

        async def fetchval(self, sql, *args):
            if "SELECT count(*) FROM pg_class" in sql:
                # Real core275 tables are ENABLED/non-FORCE. The former
                # FORCE-only predicate therefore sees zero suitable tables.
                return 0
            return await super().fetchval(sql, *args)

        async def fetch(self, sql, *args):
            rows = await super().fetch(sql, *args)
            if self.changed == "missing":
                rows.pop()
            elif self.changed:
                field, value = self.changed
                rows[0][field] = value
            return rows

    store = Store()
    connection = RoleConnection(store, "health")
    pool = Pool(store, "health")
    pool.connection = connection
    _, catalog = await registered_catalog()
    await CatalogPublisher(pool, "health").publish(catalog)
    assert store.current["health"]["live"]
    for field, value in (("name", "butler_general_rw"), ("rolsuper", True), ("rolbypassrls", True)):
        connection.role = {**RoleConnection.role, field: value}
        with pytest.raises(EvidenceRefused, match="catalog_unavailable"):
            await verify_role(connection, "health")
    connection.role = RoleConnection.role
    for changed in (
        "missing",
        ("database_owned", False),
        ("owner_member", True),
        ("relrowsecurity", False),
        ("relforcerowsecurity", True),
    ):
        connection.changed = changed
        with pytest.raises(EvidenceRefused, match="catalog_unavailable"):
            await verify_role(connection, "health")


async def complete_fleet():
    store = Store()
    owners = {}
    for domain in DOMAINS:
        _, catalog = await registered_catalog(domain)
        owners[domain] = CatalogPublisher(Pool(store, domain), domain)
        await owners[domain].publish(catalog)
    return store, owners


async def test_real_registry_repository_reader_and_admission_projection():
    store, owners = await complete_fleet()
    reader = PublishedCatalogProvider()
    connection = Connection(store, "switchboard")
    async with connection.transaction():
        snapshot = await reader.snapshot(connection)
        admission = GoalAdmission(None, None)
        view = await admission.catalog.load(connection)
        plan = derive_admission(Evidence("message", "owner-channel", owner=True), view)
        assert view.digest == snapshot.digest
        assert len(snapshot.registrations) == len(DOMAINS)
        assert {grant.capability for grant in plan.ceiling} == {
            f"{domain}__lookup" for domain in DOMAINS
        }
        health = snapshot.registration("health")
        assert (
            await reader.require_registration(
                connection,
                domain="health",
                revision_id=health.revision_id,
                generation_digest=health.catalog.generation_digest,
            )
            == health
        )
        with pytest.raises(TypeError):
            health.catalog["lookup"].input_schema["type"] = "forged"
        assert health.catalog["lookup"].selection_bound == {}
        with pytest.raises(TypeError):
            health.catalog["lookup"].selection_bound["unexpected"] = False
        assert health.authorization_digest == authorization_digest(health.catalog)
    # Complete canonical registry listing still has its real registered tool.
    mcp, catalog = await registered_catalog()
    assert [tool.name for tool in await mcp.list_tools()] == list(catalog)
    assert len(store.history) == len(DOMAINS)
    assert await owners["health"].resolve(Connection(store, "health"))

    # Actual daemon/native registration and the SAME repository/reader/admission
    # consumer, not a hand-forged Capability(owner_default=True). Repository SQL
    # here is the existing software protocol double, never real PostgreSQL proof.
    from butlers.core.goal_admission import Obligation
    from tests.contracts.test_tool_presentation_inventory import _core_catalog

    native = await _core_catalog("general", ("state", "notifications"))
    await owners["general"].publish(native)
    async with connection.transaction():
        current = await reader.snapshot(connection)
        actual_view = await admission.catalog.load(connection)
        assert actual_view.digest == current.digest
        projected = {item.id: item for item in actual_view.capabilities}
        assert projected["general__notify"].owner_default is True
        assert all(
            not item.owner_default for key, item in projected.items() if key != "general__notify"
        )
        obligation = derive_admission(
            Evidence(
                "occurrence",
                "declared-general",
                obligation=Obligation("general", "git-owned-declaration"),
            ),
            actual_view,
        )
        third_party = derive_admission(Evidence("message", "external-channel"), actual_view)
        for plan in (obligation, third_party):
            notify = next(g for g in plan.ceiling if g.capability == "general__notify")
            assert notify.park_only is False and notify.resources == ()
            assert all(g.capability in projected for g in plan.ceiling)
            assert all(projected[g.capability].effect_class != "transact" for g in plan.ceiling)
        assert third_party.exposed and third_party.origin == "third_party"
        assert all(
            projected[g.capability].effect_class in {"read", "record", "communicate"}
            for g in third_party.ceiling
        )
        assert all(not projected[g.capability].owner_authority for g in third_party.ceiling)
        assert all(
            projected[g.capability].owner_domain == "general" or g.capability == "general__notify"
            for g in obligation.ceiling
        )
        recipients = current.registration("general").catalog["notify"].recipient_params
        # Eligibility does not resolve an explicit target or an inherited party.
        assert recipients.undeterminable is True
        assert recipients.argument_names == ("recipient", "entity_id", "request_context")
        assert recipients.default_owner_condition and recipients.undeterminable_condition
        assert all(
            item.effect_class != "communicate"
            or item.id == "general__notify"
            or not item.owner_default
            for item in actual_view.capabilities
        )

    from butlers.core.catalog_publication import decode_publication, generation

    body = publication(native, "general")
    declared = next(d for d in body["descriptors"] if d["canonical_name"] == "notify")
    assert declared["recipient_params"]["owner_notify_branch"] == (
        "telegram_send_insight_without_party"
    )
    for invalid in (True, "owner", "all_communications"):
        malformed = copy.deepcopy(body)
        next(d for d in malformed["descriptors"] if d["canonical_name"] == "notify")[
            "recipient_params"
        ]["owner_notify_branch"] = invalid
        malformed["generation_digest"] = generation(malformed["descriptors"])
        with pytest.raises(EvidenceRefused, match="catalog_unavailable"):
            decode_publication(malformed, "general")
    # Old publications remain readable and conservative: the SAME native notify
    # name and descriptive prose without the typed branch confer no eligibility.
    legacy = copy.deepcopy(body)
    next(d for d in legacy["descriptors"] if d["canonical_name"] == "notify")[
        "recipient_params"
    ].pop("owner_notify_branch")
    legacy["generation_digest"] = generation(legacy["descriptors"])
    decoded = decode_publication(legacy, "general")
    assert publication(decoded, "general") == legacy
    assert decoded.generation_digest != native.generation_digest
    assert authorization_digest(decoded) != authorization_digest(native)
    await owners["general"].publish(decoded)
    async with connection.transaction():
        legacy_view = await reader.load(connection)
        assert legacy_view.digest != actual_view.digest
        assert not next(
            c for c in legacy_view.capabilities if c.id == "general__notify"
        ).owner_default
        conservative = derive_admission(Evidence("message", "external-channel"), legacy_view)
        assert next(g for g in conservative.ceiling if g.capability == "general__notify").park_only
        defaults = derive_admission(
            Evidence(
                "occurrence",
                "declared-general",
                obligation=Obligation("general", "git-owned-declaration"),
            ),
            legacy_view,
        )
        assert "general__notify" not in {g.capability for g in defaults.ceiling}
        with pytest.raises(EvidenceRefused):
            await reader.require_registration(
                connection,
                domain="general",
                revision_id=current.registration("general").revision_id,
                generation_digest=native.generation_digest,
            )


async def test_current_generation_restart_drift_and_exact_registration_refusal(monkeypatch):
    store, owners = await complete_fleet()
    connection = Connection(store, "switchboard")
    reader = PublishedCatalogProvider()
    async with connection.transaction():
        before = await reader.snapshot(connection)
    _, same = await registered_catalog()
    replacement = CatalogPublisher(Pool(store, "health"), "health")
    await replacement.publish(same)
    await owners["health"].retire()  # Stale teardown cannot retire the replacement.
    with pytest.raises(EvidenceRefused):
        await owners["health"].renew()
    async with connection.transaction():
        after = await reader.snapshot(connection)
        assert before.digest == after.digest
        assert before.generation_digest != after.generation_digest
        with pytest.raises(EvidenceRefused):
            await reader.require_registration(
                connection,
                domain="health",
                revision_id=before.registration("health").revision_id,
                generation_digest=same.generation_digest,
            )
    _, changed = await registered_catalog(description="New actual registered description.")
    await replacement.publish(changed)
    async with connection.transaction():
        assert (await reader.snapshot(connection)).digest != after.digest
    mcp, wrapped = await registered_catalog(approval_enabled=True)
    assert "_why" in wrapped["lookup"].input_schema["properties"]
    from butlers.daemon import ButlerDaemon

    daemon = SimpleNamespace(
        mcp=mcp,
        _tool_catalog=changed,
        _tool_module_map={"lookup": "native"},
        _resolved_tool_metadata={"lookup": wrapped["lookup"]},
        _catalog_publication=replacement,
    )
    assert await ButlerDaemon._finalize_tool_catalog(daemon) is daemon._tool_catalog
    async with connection.transaction():
        gated = await reader.snapshot(connection)
        assert gated.registration("health").catalog["lookup"].approval_policy["enabled"] is True
        assert gated.registration("health").catalog.generation_digest != changed.generation_digest
        assert gated.registration("health").authorization_digest != authorization_digest(changed)
    # A failed replacement must retire this boot's previous definition too;
    # the attempted revision was not necessarily the committed current row.
    new_mcp, new_catalog = await registered_catalog(description="Changed again.")
    daemon.mcp = new_mcp
    daemon._resolved_tool_metadata = {"lookup": new_catalog["lookup"]}
    execute = replacement.pool.connection.execute

    async def reject_publication(sql, *args):
        if "publish_capability_catalog" in sql:
            raise ConnectionError("controlled publication unavailable")
        return await execute(sql, *args)

    monkeypatch.setattr(replacement.pool.connection, "execute", reject_publication)
    with pytest.raises(ConnectionError):
        await ButlerDaemon._finalize_tool_catalog(daemon)
    assert store.current["health"]["live"] is False
    async with connection.transaction():
        with pytest.raises(EvidenceRefused):
            await reader.load(connection)


async def test_incomplete_failed_malformed_and_forged_publications_refuse():
    baseline, _ = await complete_fleet()
    # Each failure changes an actual producer-owned record, not the provider API.
    for fault in (
        "omitted",
        "expired",
        "digest",
        "schema",
        "selection",
        "authorization",
        "member",
        "disabled",
    ):
        store = copy.deepcopy(baseline)
        row = store.current["health"]
        if fault == "omitted":
            del store.current["health"]
        elif fault == "expired":
            row["live"] = False
        elif fault == "digest":
            row["body_digest"] = "0" * 64
        elif fault == "schema":
            row["body"]["descriptors"][0]["input_schema"]["type"] = "array"
            row["body_digest"] = digest(row["body"])
        elif fault == "selection":
            del row["body"]["descriptors"][0]["selection_bound"]
            row["body_digest"] = digest(row["body"])
        elif fault == "authorization":
            row["authorization_digest"] = "0" * 64
        elif fault == "member":
            store.members.pop()
        else:
            store.enabled = False
        connection = Connection(store, "switchboard")
        async with connection.transaction():
            with pytest.raises(EvidenceRefused):
                await PublishedCatalogProvider().load(connection)
    _, catalog = await registered_catalog()
    forged = CatalogPublisher(Pool(baseline, "relationship"), "health")
    old = copy.deepcopy(baseline.current)
    with pytest.raises(EvidenceRefused):
        await forged.publish(catalog)
    with pytest.raises(EvidenceRefused):
        publication(replace(catalog, generation_digest="0" * 64), "health")
    with pytest.raises(EvidenceRefused):
        publication(catalog, "relationship")
    assert baseline.current == old
    with pytest.raises(EvidenceRefused):
        await PublishedCatalogProvider().load(Connection(baseline, "switchboard"))


async def test_lost_commit_reply_requires_exact_readback_not_success_retry():
    store = Store()
    publisher = CatalogPublisher(Pool(store, "health"), "health")
    _, catalog = await registered_catalog()
    store.lost_reply = True
    with pytest.raises(ConnectionError):
        await publisher.publish(catalog)
    assert len(store.history) == 1
    assert await publisher.resolve(Connection(store, "health"))
    publisher.revision = uuid4()
    assert not await publisher.resolve(Connection(store, "health"))
    assert len(store.history) == 1
    # The daemon retains its actual uncertain operation for teardown/readback,
    # even when startup has not reached task construction.
    store.lost_reply = True
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="health"),
        tool_catalog=catalog,
        _module_statuses={},
        _tool_registration_failures=[],
    )
    with pytest.raises(ConnectionError):
        await start_catalog_publication(daemon, Pool(store, "health"))
    assert await daemon._catalog_publication.resolve(Connection(store, "health"))
    await close_catalog_publication(daemon)
    assert store.current["health"]["live"] is False


async def test_phased_startup_publishes_final_catalog_using_its_database_state(monkeypatch):
    from butlers import lifecycle

    monkeypatch.setenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", "1")
    store = Store()
    pool = Pool(store, "health")
    _, catalog = await registered_catalog()

    async def committed_boot_registration():
        return True

    daemon = SimpleNamespace(
        config=SimpleNamespace(name="health", port=9100),
        _register_boot_epoch=committed_boot_registration,
        _module_statuses={},
        _tool_registration_failures=[],
        tool_catalog=catalog,
    )
    try:
        await lifecycle._startup_readiness(daemon, lifecycle.StartupState(pool=pool))
        assert daemon._accepting_connections is True
        assert daemon._started_at > 0
        assert store.current["health"]["live"] is True
        assert store.current["health"]["body"] == publication(catalog, "health")
        assert len(store.history) == 1
    finally:
        await close_catalog_publication(daemon)
    assert store.current["health"]["live"] is False


async def test_startup_off_no_publication_and_enabled_failed_registry_refuses(monkeypatch):
    store = Store()
    store.enabled = False
    daemon = SimpleNamespace(config=SimpleNamespace(name="health"))
    await start_catalog_publication(daemon, Pool(store, "health"))
    await close_catalog_publication(daemon)
    assert store.current == {} and not hasattr(daemon, "_catalog_publication_task")
    store.enabled = True
    daemon._module_statuses = {"native": SimpleNamespace(status="failed")}
    with pytest.raises(EvidenceRefused):
        await start_catalog_publication(daemon, Pool(store, "health"))
    assert store.current == {}

    # Actual delayed-boot producer publishes before advertising acceptance.
    from butlers.daemon import ButlerDaemon

    _, catalog = await registered_catalog()
    daemon.tool_catalog = catalog
    daemon.db = SimpleNamespace(pool=Pool(store, "health"))
    daemon._shutting_down = False
    daemon._accepting_connections = False
    daemon._boot_epoch = None
    original_sleep = asyncio.sleep

    async def elapsed_boot_backoff(seconds):
        await original_sleep(0 if seconds == 5 else seconds)

    async def committed_boot():
        daemon._boot_epoch = 1
        return True

    monkeypatch.setattr(asyncio, "sleep", elapsed_boot_backoff)
    daemon._register_boot_epoch = committed_boot
    await ButlerDaemon._retry_boot_registration(daemon)
    assert daemon._accepting_connections is False
    assert daemon._catalog_publication_failed is True
    assert store.current == {}
    daemon._module_statuses = {}
    daemon._boot_epoch = None
    await ButlerDaemon._retry_boot_registration(daemon)
    try:
        assert daemon._accepting_connections is True
        assert store.current["health"]["live"] is True
        assert daemon._catalog_publication_task is not None
    finally:
        await close_catalog_publication(daemon)


async def test_owned_lifecycle_repeated_cancellation_waits_for_retirement(monkeypatch):
    store = Store()
    _, catalog = await registered_catalog()
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="health"),
        tool_catalog=catalog,
        _module_statuses={},
        _tool_registration_failures=[],
    )
    pool = Pool(store, "health")
    await start_catalog_publication(daemon, pool)
    worker = daemon._catalog_publication_task
    entered, release = asyncio.Event(), asyncio.Event()
    execute = pool.connection.execute

    async def delayed_retirement(sql, *args):
        if "SET active=false" in sql:
            entered.set()
            await release.wait()
        return await execute(sql, *args)

    monkeypatch.setattr(pool.connection, "execute", delayed_retirement)
    close = asyncio.create_task(close_catalog_publication(daemon))
    try:
        async with asyncio.timeout(2):
            await entered.wait()
            close.cancel()
            await asyncio.sleep(0)
            close.cancel()
            await asyncio.sleep(0)
            assert not close.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await close
        assert worker.done() and daemon._catalog_publication_close_task.done()
        assert daemon._catalog_publication is None
        assert store.current["health"]["live"] is False
        await close_catalog_publication(daemon)
    finally:
        release.set()
        if not close.done():
            close.cancel()
        await asyncio.gather(close, return_exceptions=True)
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    # A real storage error cannot become successful slot clearing.
    await start_catalog_publication(daemon, pool)
    worker = daemon._catalog_publication_task

    async def unavailable(sql, *args):
        raise ConnectionError("controlled retirement unavailable")

    monkeypatch.setattr(pool.connection, "execute", unavailable)
    with pytest.raises(ConnectionError):
        await close_catalog_publication(daemon)
    assert worker.done() and daemon._catalog_publication is not None
    assert store.current["health"]["live"] is True


async def test_superseded_finalizer_cannot_republish_or_retry_unknown_commit(monkeypatch):
    from butlers.daemon import ButlerDaemon

    store, owners = await complete_fleet()
    old = owners["health"]
    successor = CatalogPublisher(Pool(store, "health"), "health")
    _, catalog = await registered_catalog()
    await successor.publish(catalog)
    mcp, changed = await registered_catalog(description="Changed old boot definition.")
    daemon = SimpleNamespace(
        mcp=mcp,
        _tool_catalog=catalog,
        _tool_module_map={"lookup": "native"},
        _resolved_tool_metadata={"lookup": changed["lookup"]},
        _catalog_publication=old,
    )
    before = copy.deepcopy((store.current, store.history))
    with pytest.raises(EvidenceRefused):
        await ButlerDaemon._finalize_tool_catalog(daemon)
    assert (store.current, store.history) == before
    connection = Connection(store, "switchboard")
    async with connection.transaction():
        assert (await PublishedCatalogProvider().snapshot(connection)).registration(
            "health"
        ).revision_id == successor.revision
    # The actual current boot may replace its own definition normally.
    await successor.publish(changed)
    assert store.current["health"]["revision_id"] == str(successor.revision)
    uncertain = CatalogPublisher(Pool(store, "health"), "health")
    store.lost_reply = True
    with pytest.raises(ConnectionError):
        await uncertain.publish(catalog)
    before = copy.deepcopy((store.current, store.history))
    with pytest.raises(EvidenceRefused):
        await uncertain.publish(changed)
    assert (store.current, store.history) == before
    assert await uncertain.resolve(Connection(store, "health"))
    await uncertain.publish(changed)
    assert store.current["health"]["revision_id"] == str(uncertain.revision)


@pytest.mark.parametrize("outcome", ["error", "timeout", "cancel", "cancel_error"])
async def test_actual_shutdown_continues_after_catalog_retirement_outcome(
    tmp_path, monkeypatch, outcome
):
    from butlers.config import load_config
    from butlers.daemon import ButlerDaemon

    (tmp_path / "butler.toml").write_text('[butler]\nname="health"\nport=9100\n')
    daemon = ButlerDaemon(tmp_path)
    daemon.config = load_config(tmp_path)
    store = Store()
    pool = Pool(store, "health")
    _, catalog = await registered_catalog()
    daemon._tool_catalog = catalog
    daemon._module_statuses = {}
    await start_catalog_publication(daemon, pool)
    worker = daemon._catalog_publication_task
    events = []
    entered, release = asyncio.Event(), asyncio.Event()
    original = pool.connection.execute

    async def retirement(sql, *args):
        if "SET active=false" in sql:
            entered.set()
            await release.wait()
            if outcome in {"error", "cancel_error"}:
                raise ConnectionError("controlled retirement failure")
            if outcome == "timeout":
                raise TimeoutError("controlled retirement deadline")
        return await original(sql, *args)

    monkeypatch.setattr(pool.connection, "execute", retirement)
    if outcome == "timeout":
        monkeypatch.setattr("butlers.core.catalog_publication.TRANSACTION_SECONDS", 0.05)
    daemon._mcp_socket = SimpleNamespace(close=lambda: events.append("socket"))

    async def server():
        events.append("server")

    daemon._server_task = asyncio.create_task(server())
    daemon._server = SimpleNamespace(should_exit=False)

    async def scheduler():
        try:
            await asyncio.Event().wait()
        finally:
            events.append("scheduler")

    daemon._scheduler_loop_task = asyncio.create_task(scheduler())

    async def module_close():
        events.append("module")

    module = SimpleNamespace(name="native", on_shutdown=module_close)
    daemon._modules = [module]

    async def db_close():
        events.append("db")

    daemon.db = SimpleNamespace(pool=pool, close=db_close)
    task = asyncio.create_task(daemon.shutdown())
    try:
        async with asyncio.timeout(2):
            await entered.wait()
            if outcome in {"cancel", "cancel_error"}:
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            if outcome != "timeout":
                release.set()
            expected = {
                "error": ConnectionError,
                "timeout": TimeoutError,
                "cancel": asyncio.CancelledError,
                "cancel_error": asyncio.CancelledError,
            }[outcome]
            with pytest.raises(expected):
                await task
        assert set(events) == {"server", "socket", "scheduler", "module", "db"}
        assert worker.done()
        assert daemon._scheduler_loop_task is None
        if outcome != "cancel":
            assert daemon._catalog_publication is not None
            assert store.current["health"]["live"] is True
        else:
            assert daemon._catalog_publication is None
            assert store.current["health"]["live"] is False
    finally:
        release.set()
        for owned in (task, worker, daemon._scheduler_loop_task):
            if owned is not None and not owned.done():
                owned.cancel()
        await asyncio.gather(
            *(x for x in (task, worker, daemon._scheduler_loop_task) if x is not None),
            return_exceptions=True,
        )


async def test_repeated_shutdown_joins_retained_owner_after_retirement(tmp_path):
    from butlers.config import load_config
    from butlers.daemon import ButlerDaemon

    (tmp_path / "butler.toml").write_text('[butler]\nname="health"\nport=9100\n')
    daemon = ButlerDaemon(tmp_path)
    daemon.config = load_config(tmp_path)
    store = Store()
    pool = Pool(store, "health")
    _, daemon._tool_catalog = await registered_catalog()
    await start_catalog_publication(daemon, pool)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def module_close():
        calls.append("module")
        entered.set()
        await release.wait()

    async def db_close():
        calls.append("db")

    daemon._modules = [SimpleNamespace(name="native", on_shutdown=module_close)]
    daemon.db = SimpleNamespace(pool=pool, close=db_close)
    tasks = [asyncio.create_task(daemon.shutdown())]
    try:
        async with asyncio.timeout(2):
            await entered.wait()
            assert daemon._catalog_publication is None
            tasks.append(asyncio.create_task(daemon.shutdown()))
            await asyncio.sleep(0)
            assert calls == ["module"]
            assert all(not task.done() for task in tasks)
            release.set()
            await asyncio.gather(*tasks)
        assert calls == ["module", "db"]
        assert daemon._catalog_shutdown_task.done()
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
