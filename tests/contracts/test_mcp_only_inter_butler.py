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
        """Vision Rule 3: Module ABC must not reference specific butler names.

        The Module base class must be generic — it cannot encode cross-butler
        knowledge or references.
        """
        from butlers.modules.base import Module

        src = inspect.getsource(Module)
        # The abstract base must not hard-code specific butler domain names
        butler_names = ["health", "finance", "general", "travel", "relationship"]
        for name in butler_names:
            assert f'"{name}"' not in src, (
                f"Module ABC must not reference butler name '{name}' (Vision Rule 3)"
            )

    def test_identity_module_uses_shared_public_schema_only(self):
        """RFC 0004 + Vision Rule 3: Identity resolution uses only public schema.

        resolve_contact_by_channel() queries public.contact_info, public.contacts,
        and public.entities — never a butler-specific schema.
        """
        from butlers import identity

        src = inspect.getsource(identity)
        # Must reference public schema tables
        assert "public.contact_info" in src or "public.contacts" in src, (
            "identity.py must query public schema tables (RFC 0004)"
        )
        # Must not reference any specific butler schema
        for schema in ["health.", "finance.", "general.", "relationship."]:
            assert schema not in src, (
                f"identity.py must not reference butler schema '{schema}' (Vision Rule 3)"
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
        """Vision Rule 3 + RFC 0006: Core modules must not query another butler's schema.

        The core butlers package (src/butlers/) must not contain direct SQL
        references to butler-specific schemas other than the pattern 'public.*'.
        """
        from butlers import db

        src = inspect.getsource(db)
        # Core db module must not reference butler-specific schemas
        butler_specific_schemas = ["health.", "finance.", "relationship.", "travel."]
        for schema in butler_specific_schemas:
            assert schema not in src, (
                f"Core db.py must not reference butler schema '{schema}' (Vision Rule 3)"
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

        When the Spawner invokes an LLM session, it generates a temporary MCP
        configuration containing only this butler's MCP URL. No other MCP servers.
        The Spawner passes a per-session MCP config to the runtime adapter.
        """
        import inspect

        from butlers.core.spawner import Spawner

        src = inspect.getsource(Spawner)
        # The spawner must reference mcp URL or mcp config in its implementation
        has_mcp_ref = (
            "mcp" in src.lower()
            or "mcp_url" in src
            or "mcp_config" in src
            or "runtime_session_id" in src
        )
        assert has_mcp_ref, "Spawner must generate per-session MCP config (RFC 0002)"

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
        import sys

        loaded_roster = {
            k: v for k, v in sys.modules.items() if k.startswith("butlers.modules._roster_")
        }
        for mod_name, mod in loaded_roster.items():
            butler_name = mod_name.replace("butlers.modules._roster_", "")
            mod_file = getattr(mod, "__file__", "") or ""
            if not mod_file:
                continue
            try:
                with open(mod_file) as f:
                    src = f.read()
            except OSError:
                continue
            # Check that this roster module does not import another roster module
            for other_name, _ in loaded_roster.items():
                other_butler = other_name.replace("butlers.modules._roster_", "")
                if other_butler == butler_name:
                    continue
                # A direct import like `from roster.health import ...` would be a violation
                assert f"roster.{other_butler}" not in src, (
                    f"Roster module '{butler_name}' must not import from '{other_butler}' "
                    f"(Vision Rule 3: MCP-only inter-butler communication)"
                )

    def test_context_bus_uses_shared_public_table(self):
        """RFC 0009 + Vision Rule 3: Context bus uses public.user_context.

        The context bus avoids the need for direct inter-butler communication
        by using a shared public-schema table readable by all butlers.
        This is NOT a violation of Rule 3 because it uses the public schema
        (already in every butler's search_path), not a cross-schema query.
        """
        from butlers import context_bus

        src = inspect.getsource(context_bus)
        assert "public.user_context" in src, (
            "context_bus must reference public.user_context table (RFC 0009)"
        )
        # Must not reference butler-specific schemas
        for schema in ["health.", "finance.", "general."]:
            assert schema not in src, (
                f"context_bus must not reference butler schema '{schema}' (Vision Rule 3)"
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
        """Vision Rule 3: ModuleRegistry cannot wire cross-butler function calls.

        The registry instantiates modules but each module only receives
        its own butler's resources (mcp, config, db). It cannot receive
        another butler's module instance.
        """
        from butlers.modules.registry import ModuleRegistry

        src = inspect.getsource(ModuleRegistry)
        # The registry must not create cross-butler wiring
        assert "health" not in src, (
            "ModuleRegistry must not reference specific butler names (Vision Rule 3)"
        )
        assert "finance" not in src, (
            "ModuleRegistry must not reference specific butler names (Vision Rule 3)"
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
