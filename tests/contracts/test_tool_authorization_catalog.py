"""D4 contracts against configured, final registered capability definitions.

REQ-capability-authorization-002, REQ-capability-authorization-014,
REQ-capability-authorization-015, REQ-capability-authorization-016.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

from butlers.config import load_config
from butlers.core.tool_catalog import (
    ToolCatalogError,
    build_tool_catalog,
    validate_authorization_catalog,
)
from butlers.core.tool_presentation_inventory import STORE_WRITERS
from butlers.daemon import ButlerDaemon
from butlers.modules.base import ToolMeta
from butlers.modules.registry import default_registry

_THIS_FILE = Path(__file__).resolve()

pytestmark = pytest.mark.contract
_ROOT = _THIS_FILE.parents[2]
_D4_FIELDS = (
    "owner_domain",
    "effect_class",
    "output_trust",
    "output_sensitivity",
    "resource_params",
    "selection_bound",
    "safety_args",
    "approval_policy",
    "idempotency",
    "owner_authority",
    "recipient_params",
)


async def _roster_catalog(name: str, *, expiry_hours: int | None = None):
    daemon = ButlerDaemon(
        _ROOT / "roster" / name,
        db=SimpleNamespace(
            pool=AsyncMock(),
            schema=name,
            db_name="butlers",
            host="localhost",
            port=5432,
            user="catalog-fixture",
            password=None,
            ssl=None,
            min_pool_size=1,
            max_pool_size=1,
        ),
    )
    daemon.config = load_config(_ROOT / "roster" / name)
    if expiry_hours is not None:
        daemon.config.modules["approvals"]["default_expiry_hours"] = expiry_hours
    daemon.mcp = FastMCP(f"authorization-{name}")
    daemon.spawner = MagicMock()
    registry = default_registry()
    daemon._modules = [registry._modules[module]() for module in daemon.config.modules]
    daemon._module_configs = daemon._validate_module_configs()
    before_tasks = asyncio.all_tasks()
    try:
        daemon._register_core_tools()
        await daemon._register_module_tools()
        await daemon._apply_approval_gates()
        catalog = await daemon._finalize_tool_catalog()
        assert not daemon._tool_registration_failures
        assert all(status.status != "failed" for status in daemon._module_statuses.values())
        return catalog
    finally:
        tasks = asyncio.all_tasks() - before_tasks
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_enabled_calendar_has_complete_post_approval_authorization(monkeypatch) -> None:
    """Both actual response owners retain their real gated conditional surface."""
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    for name in ("messenger", "relationship"):
        catalog = await _roster_catalog(name)
        response = catalog["calendar_respond"]
        assert {"calendar_respond", "calendar_response_fence_status"} <= set(catalog)
        assert response.classification_complete
        assert response.group_name == "response"
        assert catalog["calendar_response_fence_status"].group_name == "direct"
        assert all(getattr(response, field, None) is not None for field in _D4_FIELDS)
        assert response.effect_class == "communicate"
        assert response.owner_domain == name
        assert response.approval_policy["configured_gate"] is True
        assert not {"_command_id", "_command_digest"} & response.input_schema["properties"].keys()
        assert catalog["calendar_response_fence_status"].llm_presentable is False
        # The same actual owner also supports response-disabled registration:
        # its infrastructure fence remains registered without a response handler.
        from butlers.modules.calendar import CalendarModule

        module = CalendarModule()
        daemon = ButlerDaemon(
            _ROOT / "roster" / name,
            db=SimpleNamespace(pool=AsyncMock(), schema=name, db_name="butlers"),
        )
        daemon.config = load_config(_ROOT / "roster" / name)
        daemon.config.modules = {}
        daemon.mcp = FastMCP(f"authorization-response-disabled-{name}")
        daemon._modules = [module]
        await module.register_tools(
            daemon.mcp,
            module.config_schema(provider="google", response_enabled=False),
            daemon.db,
            name,
        )
        daemon._tool_module_map = {tool.name: "calendar" for tool in await daemon.mcp.list_tools()}
        daemon._registered_tool_names = set(daemon._tool_module_map)
        await daemon._apply_approval_gates()
        disabled = await daemon._finalize_tool_catalog()
        assert "calendar_respond" not in disabled
        assert "calendar_response_fence_status" in disabled
        assert all(d.authorization_complete for d in disabled.descriptors)
        validate_authorization_catalog(disabled, store_writers=STORE_WRITERS)


async def _complete_core_catalog(name):
    daemon = ButlerDaemon(_ROOT / "roster" / name, db=SimpleNamespace(pool=AsyncMock()))
    daemon.config = load_config(_ROOT / "roster" / name)
    daemon.config.runtime_seed = daemon.config.runtime_seed.model_copy(update={"core_groups": None})
    daemon.config.modules = {}
    daemon.mcp = FastMCP(f"authorization-core-{name}")
    daemon.spawner = MagicMock()
    daemon._register_core_tools()
    await daemon._apply_approval_gates()
    return await daemon._finalize_tool_catalog()


async def _provider_catalogs():
    from tests.contracts.test_tool_presentation_inventory import _CONFIG_OVERRIDES

    registry = default_registry()
    errors = []
    for name in registry.available_modules:
        module = registry._modules[name]()
        config = (
            module.config_schema(**_CONFIG_OVERRIDES.get(name, {})) if module.config_schema else {}
        )
        owner = "relationship" if name == "memory" else "inventory"
        daemon = ButlerDaemon(
            _ROOT / "roster" / "general",
            db=SimpleNamespace(pool=AsyncMock(), schema=owner, db_name="inventory"),
        )
        daemon.config = load_config(_ROOT / "roster" / "general")
        daemon.config.name = owner
        daemon.config.modules = {}
        daemon.mcp = FastMCP(f"authorization-provider-{name}")
        daemon._modules = [module]
        before_tasks = asyncio.all_tasks()
        try:
            await module.register_tools(daemon.mcp, config, daemon.db, owner)
            daemon._tool_module_map = {tool.name: name for tool in await daemon.mcp.list_tools()}
            daemon._registered_tool_names = set(daemon._tool_module_map)
            await daemon._apply_approval_gates()
            catalog = await daemon._finalize_tool_catalog()
            try:
                validate_authorization_catalog(catalog, store_writers=STORE_WRITERS)
            except ToolCatalogError as exc:
                errors.append(str(exc))
            for descriptor in catalog.descriptors:
                if not descriptor.authorization_complete:
                    errors.append(
                        (
                            descriptor.canonical_name,
                            tuple(descriptor.input_schema.get("properties", {})),
                            descriptor.resource_params,
                            descriptor.safety_args,
                        )
                    )
            if name == "calendar":
                assert "calendar_respond" not in catalog
                assert "calendar_response_fence_status" not in catalog
        finally:
            tasks = asyncio.all_tasks() - before_tasks
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    assert not errors, errors


def _catalog_runtime_databases(monkeypatch: pytest.MonkeyPatch):
    """Establish actual owner Databases while keeping driver I/O in memory.

    Runtime role validation, verification, codec setup and private derivation
    stay real. Only asyncpg connection/pool creation is replaced; these fake
    driver results are software setup, never PostgreSQL authorization evidence.
    """
    from butlers.db import Database

    databases = []

    def connection():
        conn = AsyncMock()
        conn.terminate = MagicMock()
        conn.fetchval.return_value = True
        return conn

    async def connect(**kwargs):
        return connection()

    async def create_pool(**kwargs):
        conn = connection()
        await kwargs["init"](conn)
        setup = kwargs.get("setup")
        if setup is not None:
            await setup(conn)
        pool = AsyncMock()
        pool.fetchval.return_value = None

        class Acquisition:
            async def get(self):
                if setup is not None:
                    await setup(conn)
                return conn

            def __await__(self):
                return self.get().__await__()

            async def __aenter__(self):
                return await self.get()

            async def __aexit__(self, *exc):
                await pool.release(conn)

        pool.acquire = MagicMock(side_effect=Acquisition)
        databases.append(kwargs["init"].__self__)
        return pool

    monkeypatch.setattr("butlers.db.asyncpg.connect", AsyncMock(side_effect=connect))
    monkeypatch.setattr("butlers.db.asyncpg.create_pool", AsyncMock(side_effect=create_pool))

    def primary_database(schema):
        db = Database(
            db_name="butlers",
            schema=schema,
            role=f"butler_{schema}_rw",
            host="localhost",
            port=5432,
            user="catalog-fixture",
            password=None,
            ssl=None,
            min_pool_size=1,
            max_pool_size=1,
        )
        return db

    async def close_databases():
        for db in reversed(databases):
            await db.close()
        databases.clear()

    return primary_database, close_databases


async def test_all_rosters_have_complete_post_approval_authorization_catalogs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute native registration and wrapping, never provider or handler bodies."""
    from unittest.mock import MagicMock

    from butlers.config import load_config
    from butlers.core.tool_catalog import validate_authorization_catalog
    from butlers.core.tool_presentation_inventory import STORE_WRITERS
    from butlers.daemon import ButlerDaemon
    from butlers.modules.registry import default_registry

    roster = _THIS_FILE.parents[2] / "roster"
    registry = default_registry()
    await _provider_catalogs()
    for variant in ("general", "switchboard", "messenger", "relationship", "chronicler"):
        synthetic = await _complete_core_catalog(variant)
        validate_authorization_catalog(synthetic, store_writers=STORE_WRITERS)
    # Keep real owner/private-memory establishment, with no driver network I/O.
    new_runtime_database, close_runtime_databases = _catalog_runtime_databases(monkeypatch)
    catalogs = {}
    for directory in sorted(roster.iterdir()):
        if not (directory / "butler.toml").exists():
            continue
        daemon = ButlerDaemon(
            directory,
            db=new_runtime_database(directory.name),
        )
        daemon.config = load_config(directory)
        daemon.mcp = FastMCP(f"authorization-{directory.name}")
        daemon.spawner = MagicMock()
        daemon._modules = [registry._modules[name]() for name in daemon.config.modules]
        daemon._module_configs = daemon._validate_module_configs()
        before_tasks = asyncio.all_tasks()
        try:
            await daemon.db.connect()
            daemon._register_core_tools()
            await daemon._register_module_tools()
            await daemon._apply_approval_gates()
            catalog = await daemon._finalize_tool_catalog()
            validate_authorization_catalog(catalog, store_writers=STORE_WRITERS)
            for descriptor in catalog.descriptors:
                assert descriptor.owner_domain == daemon.config.name
                if descriptor.llm_presentable:
                    assert descriptor.authorization_complete, descriptor.canonical_name
            catalogs[directory.name] = catalog
            assert not daemon._tool_registration_failures
            assert all(status.status != "failed" for status in daemon._module_statuses.values())
        finally:
            registration_tasks = asyncio.all_tasks() - before_tasks
            for task in registration_tasks:
                task.cancel()
            if registration_tasks:
                await asyncio.gather(*registration_tasks, return_exceptions=True)
            await close_runtime_databases()
    assert set(catalogs) == {p.name for p in roster.glob("*/butler.toml") for p in [p.parent]}
    for owner in ("messenger", "relationship"):
        response = catalogs[owner]["calendar_respond"]
        assert response.effect_class == "communicate"
        assert response.idempotency == "key_required"
        assert response.safety_args == ("entry_id", "response_status", "request_id", "send_updates")
        assert not {"_command_id", "_command_digest"} & response.input_schema["properties"].keys()
        assert response.approval_policy["enabled"] is True
        assert response.approval_policy["configured_gate"] is True
    union = {d.canonical_name: d for c in catalogs.values() for d in c.descriptors}
    assert "drive_share_file" not in union
    assert "drive_write_file" in union
    assert union["drive_write_file"].effect_class == "communicate"
    assert union["drive_write_file"].recipient_params.undeterminable
    for name in (
        "schedule_create",
        "schedule_update",
        "schedule_delete",
        "schedule_toggle",
        "schedule_trigger",
        "deadline_create",
        "deadline_update",
        "deadline_delete",
        "subscribe_to_event",
        "unsubscribe_from_event",
    ):
        assert union[name].effect_class == "administer"
    for name in (
        "approve_action",
        "reject_action",
        "dispatch_approved_action",
        "create_approval_rule",
        "create_rule_from_action",
        "revoke_approval_rule",
        "confirm_promotion_suggestion",
        "dismiss_promotion_suggestion",
    ):
        assert not union[name].llm_presentable
    assert union["correct"].owner_authority
    assert union["relationship_assert_fact"].owner_authority
    assert union["ha_call_service"].recipient_params.argument_names == ("target", "data")
    assert union["ha_call_service"].resource_params == ("target",)
    assert union["ha_call_service"].safety_args == ("target", "data")
    assert union["attachment_view"].output_trust == "untrusted"
    assert union["attachment_view"].output_sensitivity == "sensitive"
    assert union["workout_last"].effect_class == "read"
    assert union["workout_last"].read_stores == ("facts",)
    assert union["correct"].effect_class == "administer"
    assert union["remind"].recipient_params.argument_names == ("request_context",)
    assert union["delegate_answer"].recipient_params.undeterminable


async def test_authorization_catalog_rejects_real_definition_mutations() -> None:

    from butlers.core.tool_catalog import (
        AUTHORIZATION_FIELDS,
        RecipientParameters,
        validate_authorization_catalog,
    )
    from butlers.core.tool_presentation_inventory import STORE_WRITERS
    from tests.contracts.test_tool_presentation_inventory import _core_catalog

    catalog = await _core_catalog("general", ("state", "notifications"))
    reader = catalog["state_get"]
    assert reader.grantable

    def changed(descriptor):
        return replace(
            catalog,
            descriptors=tuple(
                descriptor if item.canonical_name == descriptor.canonical_name else item
                for item in catalog.descriptors
            ),
        )

    unclassified = replace(reader, classification_complete=False)
    assert not unclassified.grantable
    with pytest.raises(ToolCatalogError, match="classification_complete"):
        validate_authorization_catalog(changed(unclassified), store_writers=STORE_WRITERS)

    for field_name in AUTHORIZATION_FIELDS:
        missing = replace(reader, **{field_name: None})
        assert not missing.grantable
        with pytest.raises(ToolCatalogError, match=f"state_get.*{field_name}"):
            validate_authorization_catalog(changed(missing), store_writers=STORE_WRITERS)
    for field_name, malformed in (
        ("effect_class", "unknown"),
        ("output_trust", "claimed"),
        ("output_sensitivity", "public"),
        ("idempotency", "sometimes"),
        ("owner_authority", "yes"),
        ("resource_params", ("absent_parameter",)),
        ("selection_bound", {"absent_parameter": True}),
        ("selection_bound", {}),
        ("selection_bound", {"key": "yes"}),
        ("safety_args", ("_command_digest",)),
        ("owner_domain", " "),
        ("approval_policy", "enabled"),
        ("recipient_params", RecipientParameters(default_owner_condition=True)),
        ("recipient_params", RecipientParameters(undeterminable_condition=" ")),
        ("recipient_params", RecipientParameters(argument_names=("key", "key"))),
    ):
        invalid = replace(reader, **{field_name: malformed})
        assert not invalid.grantable
        with pytest.raises(ToolCatalogError, match=field_name):
            validate_authorization_catalog(changed(invalid), store_writers=STORE_WRITERS)
    transaction = replace(reader, effect_class="transact")
    assert transaction.authorization_complete
    assert not transaction.grantable
    for name in ("approve_action", "custody.challenge"):
        exposed = replace(reader, canonical_name=name)
        assert not exposed.grantable
        bad = replace(catalog, descriptors=(exposed,))
        with pytest.raises(ToolCatalogError, match="owner-decision/private-custody"):
            validate_authorization_catalog(bad, store_writers=STORE_WRITERS)
    assert "state_set" in STORE_WRITERS["state"].record_capabilities
    only_record = dict(STORE_WRITERS)
    only_record["state"] = replace(
        STORE_WRITERS["state"],
        native_writers=(),
        unknown_writers=False,
    )
    trusted = replace(reader, output_trust="trusted", unknown_output_source=False)
    with pytest.raises(ToolCatalogError, match="state_get.*state"):
        validate_authorization_catalog(changed(trusted), store_writers=only_record)
    assert catalog["state_set"].effect_class == "record"
    assert "state" in catalog["state_set"].write_stores
    with pytest.raises(ToolCatalogError, match="state_get.*state"):
        validate_authorization_catalog(changed(trusted), store_writers=STORE_WRITERS)
    unknown = replace(trusted, read_stores=("unknown_dynamic_store",))
    with pytest.raises(ToolCatalogError, match="unknown_dynamic_store"):
        validate_authorization_catalog(changed(unknown), store_writers=STORE_WRITERS)
    no_provenance = replace(trusted, read_stores=(), unknown_output_source=True)
    with pytest.raises(ToolCatalogError, match="unknown output provenance"):
        validate_authorization_catalog(changed(no_provenance), store_writers=STORE_WRITERS)
    notify = catalog["notify"]
    empty_parties = replace(notify, recipient_params=RecipientParameters())
    with pytest.raises(ToolCatalogError, match="notify.*recipient_params"):
        validate_authorization_catalog(changed(empty_parties), store_writers=STORE_WRITERS)
    assert notify.recipient_params.default_owner_condition
    assert notify.recipient_params.undeterminable
    for writer_kind in ("native_writers", "external_writers", "unknown_writers"):
        writers = dict(STORE_WRITERS)
        clean = replace(
            STORE_WRITERS["state"],
            record_capabilities=(),
            native_writers=(),
            external_writers=False,
            unknown_writers=False,
        )
        writers["state"] = replace(
            clean, **{writer_kind: ("native",) if writer_kind == "native_writers" else True}
        )
        with pytest.raises(ToolCatalogError, match="state_get.*state"):
            validate_authorization_catalog(
                replace(catalog, descriptors=(trusted,)), store_writers=writers
            )
    with pytest.raises(ToolCatalogError, match="stamp producer"):
        validate_authorization_catalog(
            changed(replace(reader, output_trust="stamped")), store_writers=STORE_WRITERS
        )


async def test_authorization_policy_freezes_and_changes_generation_only() -> None:
    policy = {"enabled": True, "constraints": {"recipient": ["owner"]}}
    parties = ["query"]

    from butlers.core.tool_catalog import RecipientParameters

    mcp = FastMCP("authorization-values")

    @mcp.tool(description="Read a local value.")
    async def read_value(query: str) -> str:
        return query

    metadata = ToolMeta(
        canonical_name="read_value",
        module_name="test",
        group_name="read",
        namespace="test.read",
        llm_presentable=True,
        load_posture="deferred",
        owner_domain="general",
        effect_class="read",
        output_trust="untrusted",
        output_sensitivity="sensitive",
        resource_params=("query",),
        selection_bound={"query": False},
        safety_args=(),
        approval_policy=policy,
        idempotency="none",
        owner_authority=False,
        recipient_params=RecipientParameters(argument_names=parties),
    )

    async def build():
        return await build_tool_catalog(
            mcp, tool_owners={"read_value": "test"}, tool_metadata={"read_value": metadata}
        )

    first = await build()
    assert first["read_value"].authorization_complete
    assert first.generation_digest == (await build()).generation_digest
    policy["constraints"]["recipient"].append("other")
    parties.append("absent")
    assert first["read_value"].approval_policy["constraints"]["recipient"] == ("owner",)
    assert first["read_value"].recipient_params.argument_names == ("query",)
    with pytest.raises(TypeError):
        first["read_value"].approval_policy["enabled"] = False
    second = await build()
    assert first.generation_digest != second.generation_digest
    assert first["read_value"].input_schema_digest == second["read_value"].input_schema_digest
    assert first["read_value"].description_digest == second["read_value"].description_digest
    metadata.owner_domain = "relationship"
    assert (await build()).generation_digest != second.generation_digest
    metadata = replace(metadata, owner_domain="general")
    assert (await build()).generation_digest == second.generation_digest

    mcp.tool(name="read_value", description="Read a different local value.")(read_value)
    described = await build()
    assert described["read_value"].description_digest != second["read_value"].description_digest
    assert described["read_value"].input_schema_digest == second["read_value"].input_schema_digest

    async def changed_read_value(query: int) -> int:
        return query

    mcp.tool(name="read_value", description="Read a different local value.")(changed_read_value)
    reshaped = await build()
    assert reshaped["read_value"].input_schema_digest != described["read_value"].input_schema_digest
    assert reshaped["read_value"].description_digest == described["read_value"].description_digest


async def test_registered_catalog_restarts_preserve_identity_and_bind_policy() -> None:
    """Independent interpreter starts rebuild the actual wrapped roster definitions."""
    import json
    import subprocess
    import sys

    program = """
import asyncio, json, sys
from unittest.mock import AsyncMock, patch
from tests.contracts.test_tool_authorization_catalog import _roster_catalog
async def main():
    with patch('butlers.db.Database.connect', AsyncMock()):
        catalog = await _roster_catalog('messenger', expiry_hours=int(sys.argv[1]))
    print(json.dumps({'generation': catalog.generation_digest,
        'definitions': {d.canonical_name: [d.input_schema_digest, d.description_digest]
                        for d in catalog.descriptors},
        'policy': dict(catalog['calendar_respond'].approval_policy)}))
asyncio.run(main())
"""

    def restart(expiry):
        result = subprocess.run(
            [sys.executable, "-c", program, str(expiry)],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return json.loads(result.stdout.splitlines()[-1])

    first, second, changed = (restart(expiry) for expiry in (48, 48, 49))
    assert first == second
    assert first["generation"] != changed["generation"]
    assert first["definitions"] == changed["definitions"]
    assert first["policy"]["expiry_hours"] == 48
    assert changed["policy"]["expiry_hours"] == 49


async def test_module_declarations_cannot_override_actual_owner_or_policy(monkeypatch) -> None:

    from butlers.modules.calendar import CalendarModule

    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    undeclared = FastMCP("undeclared-capability")

    @undeclared.tool()
    async def new_read(query: str) -> str:
        return query

    catalog = await build_tool_catalog(undeclared, tool_owners={"new_read": "test"})
    assert not catalog["new_read"].grantable
    with pytest.raises(ToolCatalogError, match="new_read.*effect_class"):
        validate_authorization_catalog(catalog, store_writers=STORE_WRITERS)
    original = CalendarModule.tool_metadata

    def incorrect_owner(module):
        metadata = original(module)
        if "calendar_respond" in metadata:
            metadata["calendar_respond"] = replace(
                metadata["calendar_respond"], owner_domain="other"
            )
        return metadata

    monkeypatch.setattr(CalendarModule, "tool_metadata", incorrect_owner)
    with pytest.raises(ToolCatalogError, match="calendar_respond.*owner_domain"):
        await _roster_catalog("messenger")

    def incorrect_policy(module):
        metadata = original(module)
        if "calendar_respond" in metadata:
            metadata["calendar_respond"] = replace(
                metadata["calendar_respond"], approval_policy={"enabled": False}
            )
        return metadata

    monkeypatch.setattr(CalendarModule, "tool_metadata", incorrect_policy)
    with pytest.raises(ToolCatalogError, match="calendar_respond.*approval_policy"):
        await _roster_catalog("messenger")


def test_inventory_marks_every_resource_param_selection_bound_or_not() -> None:
    """REQ-capability-authorization-002/-018: each resource param carries an explicit mark."""
    from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY

    unmarked = [
        (entry.canonical_name, entry.resource_params, entry.selection_bound)
        for entry in TOOL_PRESENTATION_INVENTORY
        if not isinstance(entry.selection_bound, dict)
        or set(entry.selection_bound) != set(entry.resource_params or ())
        or any(not isinstance(flag, bool) for flag in entry.selection_bound.values())
    ]
    assert not unmarked, unmarked

    # Selection-bound is true only where a spec requires the scope be exactly what the owner
    # picked (REQ-capability-authorization-018; RFC 0038 D7). A bound param makes its capability
    # neither listed nor callable without an owner-surface selection record, and obligation,
    # third-party, caused and message-admitted goals never carry one, so no current param
    # qualifies. Adding the first one is a deliberate edit to this pin.
    bound = {
        (entry.module_name, param)
        for entry in TOOL_PRESENTATION_INVENTORY
        for param, flag in entry.selection_bound.items()
        if flag
    }
    assert bound == set()


async def test_finance_fixed_warning_read_is_registered_sensitive_and_never_owner_authority():
    catalog = await _roster_catalog("finance")
    descriptor = catalog["subscription_warning_still_relevant"]
    assert descriptor.owner_domain == "finance"
    assert descriptor.effect_class == "read" and descriptor.owner_authority is False
    assert descriptor.output_sensitivity == "sensitive"
    assert descriptor.read_stores == ("subscriptions",) and descriptor.write_stores == ()
    assert descriptor.llm_presentable is False
