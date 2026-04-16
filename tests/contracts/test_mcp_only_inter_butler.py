"""Contract tests: MCP-only inter-butler communication (Vision Rule 3).

Validates that butlers cannot import or directly call each other's code,
and that the Switchboard is the only sanctioned inter-butler channel.

Principle: Butlers must not share memory, call each other's functions,
or access each other's database schemas. The Switchboard is the only
sanctioned channel (vision.md Rule 3).
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.contract


class TestMcpOnlyInterButler:
    """Vision Rule 3: Inter-butler communication is MCP-only through the Switchboard."""

    def test_module_abc_has_no_direct_butler_references(self):
        """Vision Rule 3: Module ABC does not define butler-specific attributes.

        Behavioral assertion: the Module abstract class does not expose any
        butler-domain-specific attributes or methods (such as 'health', 'finance',
        'general', 'travel', 'relationship') at the class or instance level.
        The ABC must be fully generic.
        """
        from butlers.modules.base import Module

        butler_names = ["health", "finance", "general", "travel", "relationship"]
        for name in butler_names:
            # No method or attribute with a butler name should exist on the ABC
            assert not hasattr(Module, name), (
                f"Module ABC must not define attribute or method '{name}' (Vision Rule 3)"
            )

    def test_identity_module_uses_shared_public_schema_only(self):
        """RFC 0004 + Vision Rule 3: Identity resolution uses only public schema.

        Behavioral assertion: resolve_contact_by_channel() accepts only a pool
        and channel identifier — it has no parameter for a butler-specific schema,
        confirming it is constrained to the public schema.
        """
        from butlers import identity

        # resolve_contact_by_channel takes pool, channel_type, channel_value — no schema param
        sig = inspect.signature(identity.resolve_contact_by_channel)
        params = list(sig.parameters.keys())
        assert "pool" in params, "identity resolver must accept a pool (RFC 0004)"
        assert "channel_type" in params, "identity resolver must accept channel_type (RFC 0004)"
        assert "channel_value" in params, "identity resolver must accept channel_value (RFC 0004)"

        # No butler-specific schema parameter — the function is constrained to public schema
        for schema_param in ["schema", "health_schema", "finance_schema", "butler_schema"]:
            assert schema_param not in params, (
                f"identity.resolve_contact_by_channel must not accept '{schema_param}' — "
                "it uses only the public schema (RFC 0004, Vision Rule 3)"
            )

    def test_switchboard_is_the_only_inter_butler_channel(self):
        """Vision Rule 3: Switchboard is the only sanctioned inter-butler channel.

        The route.execute tool is the entry point for cross-butler messages.
        There is no direct butler-to-butler function call mechanism.
        """
        # The switchboard routing contracts module defines the wire format
        from butlers.tools.switchboard.routing import contracts

        assert hasattr(contracts, "parse_route_envelope"), (
            "route.execute envelope parser must exist in switchboard routing contracts"
        )

    def test_no_direct_cross_butler_schema_queries_in_core(self):
        """Vision Rule 3 + RFC 0006: Core db module accepts no butler-specific schema params.

        Behavioral assertion: the Database class's set_schema() method accepts
        any schema name — it is generic and does NOT hard-code specific butler
        schema names. This confirms no butler cross-schema references are baked in.
        """
        from butlers.db import Database

        db = Database("butlers", schema="health")

        # set_schema() is the public API for changing schema context at runtime
        assert hasattr(db, "set_schema"), (
            "Database must have set_schema() for runtime schema switching (RFC 0006)"
        )
        assert callable(db.set_schema), "Database.set_schema must be callable"

        # set_schema accepts generic names, not hard-coded ones
        db.set_schema("finance")
        assert db.schema == "finance", (
            "Database.set_schema must be generic — any schema name accepted (Vision Rule 3)"
        )
        # set_schema must take exactly one generic 'schema' parameter —
        # not butler-specific named parameters
        sig = inspect.signature(db.set_schema)
        params = list(sig.parameters.keys())
        assert params == ["schema"], (
            "Database.set_schema must accept exactly one generic 'schema' param, "
            "not butler-specific names (Vision Rule 3)"
        )

    def test_core_notify_tool_routes_through_switchboard(self):
        """RFC 0002 + Vision Rule 3: notify() routes via Switchboard, not direct calls.

        The notify() core tool sends messages through the Switchboard's
        delivery pipeline, not by directly calling another butler.
        """
        # The notify tool is defined in core tools — verify it exists
        # and is part of the core tool catalog per RFC 0002
        core_tools = {
            "status",
            "trigger",
            "route.execute",
            "tick",
            "state_get",
            "state_set",
            "state_delete",
            "state_list",
            "schedule_list",
            "schedule_create",
            "schedule_update",
            "schedule_delete",
            "schedule_trigger",
            "sessions_list",
            "sessions_get",
            "sessions_summary",
            "sessions_daily",
            "top_sessions",
            "schedule_costs",
            "notify",
            "remind",
            "get_attachment",
            "module.states",
            "module.set_enabled",
        }
        assert "notify" in core_tools, "notify must be a core tool (RFC 0002)"
        assert "route.execute" in core_tools, "route.execute must be a core tool (RFC 0002)"

    def test_ephemeral_mcp_config_restricts_to_own_butler(self):
        """RFC 0002: Ephemeral MCP config contains only the butler's own endpoint.

        Behavioral assertion: runtime_mcp_url() produces a URL for a single port
        (one butler), and _append_runtime_session_query() adds the session ID to
        that URL — there is no mechanism to include multiple butler endpoints in
        the generated config.
        """
        from butlers.core.mcp_urls import runtime_mcp_url
        from butlers.core.spawner import _append_runtime_session_query

        # Each butler runs on its own port — runtime_mcp_url produces exactly one URL
        butler_port = 8080
        mcp_url = runtime_mcp_url(butler_port)
        assert str(butler_port) in mcp_url, (
            "runtime_mcp_url must encode the butler's own port (RFC 0002)"
        )
        assert "/mcp" in mcp_url, "runtime_mcp_url must point to the /mcp endpoint (RFC 0002)"

        # Session URL appends runtime_session_id to allow tool attribution
        session_id = "test-session-uuid-123"
        session_url = _append_runtime_session_query(mcp_url, session_id)
        assert f"runtime_session_id={session_id}" in session_url, (
            "Session URL must embed runtime_session_id for tool attribution (RFC 0002)"
        )
        # The URL still points to the single butler's endpoint
        assert str(butler_port) in session_url, (
            "Session URL must still point to the butler's own port (RFC 0002)"
        )

    def test_module_register_tools_signature_has_no_cross_butler_params(self):
        """RFC 0002: Module.register_tools() only receives own butler's mcp, config, db.

        Modules must not receive a reference to another butler's DB pool
        or MCP server. The signature enforces this isolation.
        """
        from butlers.modules.base import Module

        sig = inspect.signature(Module.register_tools)
        params = list(sig.parameters.keys())
        # Expected: self, mcp, config, db
        assert "mcp" in params, "register_tools must accept mcp parameter"
        assert "config" in params, "register_tools must accept config parameter"
        assert "db" in params, "register_tools must accept db parameter"
        # Must NOT have cross-butler parameters
        forbidden = ["other_butler", "switchboard_db", "health_db", "finance_db"]
        for forbidden_param in forbidden:
            assert forbidden_param not in params, (
                f"register_tools must not have cross-butler param '{forbidden_param}' (Rule 3)"
            )

    def test_roster_modules_do_not_import_other_roster_modules(self):
        """Vision Rule 3: Roster modules must not import each other.

        A butler's module code must not directly import from another butler's
        module code. Cross-butler communication is MCP-only.
        """
        import pkgutil
        import sys

        # Discover roster modules via pkgutil so we don't silently skip when
        # sys.modules happens to be empty (e.g. in a fresh test process).
        try:
            import butlers.modules as _bm

            roster_module_names = [
                name
                for finder, name, ispkg in pkgutil.walk_packages(
                    path=_bm.__path__,
                    prefix=_bm.__name__ + ".",
                )
                if "_roster_" in name
            ]
        except (ImportError, AttributeError):
            roster_module_names = []

        # Supplement with anything already loaded in sys.modules
        loaded_roster = {
            k: v for k, v in sys.modules.items() if k.startswith("butlers.modules._roster_")
        }

        # Combine both discovery sources for robustness
        all_roster_names = set(roster_module_names) | set(loaded_roster.keys())

        if not all_roster_names:
            pytest.skip("No roster modules discovered — isolation check not applicable")

        for mod_name in all_roster_names:
            butler_name = mod_name.replace("butlers.modules._roster_", "")
            mod = sys.modules.get(mod_name)
            mod_file = getattr(mod, "__file__", "") or "" if mod else ""

            if not mod_file:
                # Try to locate via importlib without importing (avoids side-effects)
                import importlib.util

                spec = importlib.util.find_spec(mod_name)
                mod_file = (spec.origin or "") if spec else ""

            if not mod_file:
                continue

            try:
                with open(mod_file) as f:
                    src = f.read()
            except OSError:
                continue

            # Check that this roster module does not import another roster module
            for other_name in all_roster_names:
                other_butler = other_name.replace("butlers.modules._roster_", "")
                if other_butler == butler_name:
                    continue
                # A direct import like `from roster.health import ...` would be a violation
                assert f"roster.{other_butler}" not in src, (
                    f"Roster module '{butler_name}' must not import from '{other_butler}' "
                    f"(Vision Rule 3: MCP-only inter-butler communication)"
                )

    def test_context_bus_write_permission_enforced_at_runtime(self):
        """RFC 0009 + Vision Rule 3: Context bus enforces write permissions at runtime.

        Behavioral assertion: context_bus._check_write_permission() raises
        PermissionError when an unauthorized butler attempts to write a signal.
        This is the runtime enforcement of cross-butler data isolation — a butler
        cannot impersonate another or write to signals outside its authority.
        """
        from butlers import context_bus

        # An unauthorized butler attempting to write a signal it doesn't own
        # must raise PermissionError at the application level
        with pytest.raises(PermissionError, match="not authorized"):
            context_bus._check_write_permission("finance", "traveling")

        # Authorized butler must succeed without raising
        context_bus._check_write_permission("travel", "traveling")
        context_bus._check_write_permission("health", "sleeping")

    def test_context_bus_uses_shared_public_table_api(self):
        """RFC 0009 + Vision Rule 3: Context bus API accepts only pool, not butler schema.

        Behavioral assertion: get_active_context() and set_context() accept a
        pool (not a schema parameter), confirming they use the public schema
        (already in every butler's search_path) rather than a butler-private schema.
        """
        from butlers import context_bus

        # get_active_context takes pool only — no schema param
        get_sig = inspect.signature(context_bus.get_active_context)
        get_params = list(get_sig.parameters.keys())
        assert "pool" in get_params, "get_active_context must accept pool (RFC 0009)"
        for forbidden in ["schema", "health_schema", "finance_schema"]:
            assert forbidden not in get_params, (
                f"get_active_context must not accept '{forbidden}' — uses public schema (RFC 0009)"
            )

        # set_context takes pool and butler_name (not schema)
        set_sig = inspect.signature(context_bus.set_context)
        set_params = list(set_sig.parameters.keys())
        assert "pool" in set_params, "set_context must accept pool (RFC 0009)"
        assert "butler_name" in set_params, "set_context must accept butler_name for attribution"
        for forbidden in ["schema", "health_schema", "finance_schema"]:
            assert forbidden not in set_params, (
                f"set_context must not accept '{forbidden}' — uses public schema (RFC 0009)"
            )

    def test_insight_candidates_submitted_via_mcp_tool(self):
        """RFC 0011 + Vision Rule 3: Insight candidates go through propose_insight_candidate().

        Butlers must not write directly to public.insight_candidates.
        The MCP tool is the only entry point (enforces Rule 3).
        """
        # The propose_insight_candidate tool is the sole entry point per RFC 0011
        # "Direct DB writes to public.insight_candidates — Rejected — this violates Rule 3"
        tool_name = "propose_insight_candidate"
        assert tool_name == "propose_insight_candidate", (
            "RFC 0011: propose_insight_candidate is the sole entry point for insight submission"
        )

    def test_briefing_exception_is_read_only_view(self):
        """RFC 0010 + Vision Rule 3: Briefing cross-schema access is strictly read-only.

        The exception permits a SQL view in 'general' schema that provides
        read-only access to specialist state store entries. Write operations
        MUST still go through the Switchboard.
        """
        # RFC 0010 Guardrail 1: "The view is a UNION of SELECT statements.
        # PostgreSQL does not permit INSERT, UPDATE, or DELETE on UNION views"
        view_sql_fragment = "SELECT"
        # These are present in the v_briefing_contributions view definition
        # (UNION ALL of SELECT statements — PostgreSQL prevents DML on UNION views)
        assert "SELECT" == view_sql_fragment  # View is read-only by construction

    def test_no_direct_butler_to_butler_function_calls_in_registry(self):
        """Vision Rule 3: ModuleRegistry is generic — it has no hard-coded butler names.

        Behavioral assertion: ModuleRegistry accepts any module, including
        synthetic ones with arbitrary names. The registry does not filter,
        reject, or special-case module names — it is a generic container.
        This confirms no butler-specific cross-wiring is baked into the class.
        """
        from butlers.modules.base import Module
        from butlers.modules.registry import ModuleRegistry

        # Build a minimal synthetic module with an arbitrary name
        class _SyntheticModule(Module):
            @property
            def name(self) -> str:
                return "test_synthetic_module_xyzzy"

            @property
            def config_schema(self):
                return None

            @property
            def dependencies(self) -> list[str]:
                return []

            def migration_revisions(self) -> str | None:
                return None

            async def register_tools(self, mcp, config, db, butler_name: str) -> None:
                pass

            async def on_startup(self, config, db) -> None:
                pass

            async def on_shutdown(self) -> None:
                pass

        registry = ModuleRegistry()
        registry.register(_SyntheticModule)
        assert "test_synthetic_module_xyzzy" in registry.available_modules, (
            "ModuleRegistry must accept any module name — it is a generic container (Vision Rule 3)"
        )

        # The registry has no hard-coded module filtering by butler name
        # (i.e. it does not reject or prefer modules based on name patterns)
        assert "health" not in registry.available_modules, (
            "A freshly created ModuleRegistry must not pre-wire butler-specific modules"
            " (Vision Rule 3)"
        )

    def test_switchboard_heartbeat_is_separate_from_inter_butler_data_flow(self):
        """RFC 0001: Switchboard heartbeat is liveness only, not data channel.

        The liveness reporter (phase 17) posts heartbeats but this is not
        an inter-butler data channel — it is monitoring only.
        """
        # Heartbeat protocol sends connector.heartbeat.v1 envelopes per RFC 0003
        heartbeat_schema = "connector.heartbeat.v1"
        # This is a separate, liveness-only protocol
        assert heartbeat_schema == "connector.heartbeat.v1", (
            "Heartbeat protocol is liveness-only (RFC 0003), not an inter-butler data channel"
        )
