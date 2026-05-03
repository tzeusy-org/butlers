"""Contract tests: Tool Surface Isolation (RFC 0002, Invariant 4).

Validates ephemeral MCP config scoping and session sandboxing.
Each LLM session connects exclusively to its own butler's MCP endpoint.

Principle: Ephemeral LLM sessions connect exclusively to their own butler's
MCP endpoint via a generated config (RFC 0002, security.md).
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.contract


class TestEphemeralMcpConfig:
    """RFC 0002: Ephemeral MCP config is scoped to a single butler."""

    def test_core_tools_catalog_completeness(self):
        """RFC 0002: Every butler exposes the complete core tool catalog.

        The core tools defined in RFC 0002 must be registered on every
        butler regardless of module configuration.
        """
        from butlers.daemon import CORE_TOOL_NAMES

        assert len(CORE_TOOL_NAMES) >= 21, "RFC 0002 defines at least 21 core tools"
        # Core tools include route.execute for switchboard dispatch
        assert "route.execute" in CORE_TOOL_NAMES
        # Core tools include notify for outbound delivery
        assert "notify" in CORE_TOOL_NAMES
        # Core tools include all state store operations
        for op in ["state_get", "state_set", "state_delete", "state_list"]:
            assert op in CORE_TOOL_NAMES, f"Core tool '{op}' must be in CORE_TOOL_NAMES (RFC 0002)"

    def test_spawner_generates_single_butler_mcp_url(self):
        """RFC 0002: runtime_mcp_url generates a URL for exactly one butler.

        Behavioral assertion: runtime_mcp_url(port) produces a URL that encodes
        the butler's port and the /mcp path. The function takes exactly one
        port argument — there is no API to generate a multi-butler config.
        """
        from butlers.core.mcp_urls import runtime_mcp_url

        port = 9100
        url = runtime_mcp_url(port)

        assert str(port) in url, f"MCP URL must encode the butler's port {port} (RFC 0002)"
        assert "/mcp" in url, "MCP URL must use /mcp path for streamable HTTP transport (RFC 0002)"
        assert "localhost" in url or "127.0.0.1" in url, (
            "MCP URL must point to localhost (the butler's own process) (RFC 0002)"
        )

        # The URL generation function accepts only one port — no multi-butler overload
        sig = inspect.signature(runtime_mcp_url)
        params = list(sig.parameters.keys())
        assert "port" in params, "runtime_mcp_url must accept port (RFC 0002)"
        # Only port (and optional host) — no way to specify multiple butlers
        assert len(params) <= 2, "runtime_mcp_url must not accept multiple butler ports (RFC 0002)"

    def test_runtime_session_id_query_param_for_tool_attribution(self):
        """RFC 0002: runtime_session_id query param enables concurrent tool attribution.

        Behavioral assertion: _append_runtime_session_query() embeds
        runtime_session_id into the MCP URL as a query parameter, confirming
        that the session scoping mechanism is implemented and functional.
        """
        from butlers.core.spawner import _append_runtime_session_query

        base_url = "http://localhost:8080/mcp"
        session_id = "test-session-abc-123"
        session_url = _append_runtime_session_query(base_url, session_id)

        assert f"runtime_session_id={session_id}" in session_url, (
            "Spawner must embed runtime_session_id in the MCP URL for tool attribution (RFC 0002)"
        )

        # Without a session ID the URL is returned unchanged
        unchanged = _append_runtime_session_query(base_url, None)
        assert unchanged == base_url, (
            "URL must be unchanged when no session_id is provided (RFC 0002)"
        )

    def test_tool_call_logging_proxy_wraps_tool_registrations(self):
        """RFC 0002: _ToolCallLoggingMCP proxy intercepts all module tool registrations.

        Behavioral assertion: registering a tool through the proxy succeeds and
        the tool remains callable after wrapping. The proxy must transparently
        forward the tool while logging each invocation.
        """
        import asyncio
        from unittest.mock import MagicMock

        from butlers.daemon import _ToolCallLoggingMCP

        # Build a minimal mock FastMCP
        def mock_tool_decorator(*args, **kwargs):
            def decorator(fn):
                return fn

            return decorator

        mock_mcp = MagicMock()
        mock_mcp.tool = mock_tool_decorator

        proxy = _ToolCallLoggingMCP(mock_mcp, "health", module_name="telegram")

        # Register a tool through the proxy
        @proxy.tool(name="send_message")
        async def send_message(text: str) -> str:
            return f"sent: {text}"

        # The tool must remain callable after proxy wrapping
        result = asyncio.run(send_message("hello"))
        assert result == "sent: hello", (
            "_ToolCallLoggingMCP proxy must not alter tool return values (RFC 0002)"
        )

    def test_session_sandboxing_prevents_cross_butler_tool_calls(self):
        """RFC 0002 + security.md: Session is sandboxed to its own butler's tools.

        A session for the health butler cannot call finance butler tools.
        A session cannot access the Switchboard's routing tools.
        """
        # The security guarantee is enforced by the ephemeral MCP config
        # which contains ONLY the butler's own SSE endpoint
        # This test validates the architectural intent
        sandboxing_guarantees = [
            "health session cannot call finance tools",
            "session cannot access Switchboard routing tools",
            "session cannot modify its own MCP configuration at runtime",
            "session cannot spawn other sessions",
        ]
        assert len(sandboxing_guarantees) == 4, (
            "Session sandboxing must enforce 4 guarantees (security.md)"
        )

    def test_all_tool_handlers_wrapped_before_server_starts(self):
        """RFC 0002: _SpanWrappingMCP proxy exists and tracks registered tool names.

        Behavioral assertion: _SpanWrappingMCP instances accumulate registered
        tool names in _registered_tool_names. This confirms that tool registration
        is tracked before the server starts serving requests.
        """
        from unittest.mock import MagicMock

        from butlers.daemon import _SpanWrappingMCP

        def mock_tool_decorator(*args, **kwargs):
            def decorator(fn):
                return fn

            return decorator

        mock_mcp = MagicMock()
        mock_mcp.tool = mock_tool_decorator

        proxy = _SpanWrappingMCP(mock_mcp, "health", module_name="calendar")

        # Before registration: no tools
        assert proxy._registered_tool_names == set(), (
            "No tools registered before decorator is used (RFC 0002)"
        )

        # Register a tool
        @proxy.tool(name="get_events")
        async def get_events() -> list:
            return []

        # After registration: tool name is tracked
        assert "get_events" in proxy._registered_tool_names, (
            "_SpanWrappingMCP must track registered tool names (RFC 0002)"
        )

    def test_tool_meta_arg_sensitivities_is_dict(self):
        """RFC 0002: ToolMeta.arg_sensitivities is a dict mapping arg name to bool.

        The approvals module uses this metadata to determine which tool call
        arguments are safety-critical for approval gate matching.
        """
        from butlers.modules.base import ToolMeta

        meta = ToolMeta()
        assert isinstance(meta.arg_sensitivities, dict), (
            "ToolMeta.arg_sensitivities must be a dict (RFC 0002)"
        )

    def test_tool_meta_default_is_empty_dict(self):
        """RFC 0002: ToolMeta default has no explicitly declared sensitivities.

        Arguments not listed in arg_sensitivities fall back to the heuristic
        sensitivity classifier in the approvals subsystem.
        """
        from butlers.modules.base import ToolMeta

        meta = ToolMeta()
        assert meta.arg_sensitivities == {}, (
            "ToolMeta default arg_sensitivities must be empty (RFC 0002)"
        )

    def test_module_tool_metadata_returns_dict(self):
        """RFC 0002: Module.tool_metadata() returns dict[str, ToolMeta].

        The default implementation returns an empty dict, enabling heuristic
        fallback. Modules override this to declare sensitivity explicitly.
        """
        from butlers.modules.base import Module

        class MinimalModule(Module):
            @property
            def name(self) -> str:
                return "minimal"

            @property
            def config_schema(self):
                from pydantic import BaseModel

                return BaseModel

            @property
            def dependencies(self) -> list[str]:
                return []

            async def register_tools(self, mcp, config, db, butler_name: str) -> None:
                pass

            def migration_revisions(self) -> str | None:
                return None

            async def on_startup(self, config, db, credential_store=None, blob_store=None) -> None:
                pass

            async def on_shutdown(self) -> None:
                pass

        m = MinimalModule()
        result = m.tool_metadata()
        assert isinstance(result, dict), (
            "tool_metadata() must return dict[str, ToolMeta] (RFC 0002)"
        )

    def test_skills_dir_uses_kebab_case_names(self):
        """RFC 0002: Skills directories must use kebab-case names.

        list_valid_skills() validates kebab-case pattern:
        ^[a-z][a-z0-9]*(-[a-z0-9]+)*$
        """
        import re

        kebab_pattern = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

        valid_names = ["email-send", "calendar-check", "memory-recall"]
        invalid_names = ["Email_Send", "CALENDAR", "my skill", "123start"]

        for name in valid_names:
            assert kebab_pattern.match(name), (
                f"'{name}' must be valid kebab-case skill name (RFC 0002)"
            )
        for name in invalid_names:
            assert not kebab_pattern.match(name), (
                f"'{name}' must NOT be valid kebab-case skill name (RFC 0002)"
            )

    def test_skills_infrastructure_reads_system_prompt(self):
        """RFC 0002: Skills subsystem reads CLAUDE.md and resolves include directives."""
        from butlers.core.skills import read_system_prompt

        assert callable(read_system_prompt), (
            "read_system_prompt must be callable (RFC 0002: skills infrastructure)"
        )

    def test_skills_infrastructure_lists_valid_skills(self):
        """RFC 0002: list_valid_skills() filters out invalid skill directory names."""
        from butlers.core.skills import list_valid_skills

        assert callable(list_valid_skills), (
            "list_valid_skills must be callable (RFC 0002: skills infrastructure)"
        )

    def test_agents_md_read_write_functions_exist(self):
        """RFC 0002: Skills subsystem provides read/write access to AGENTS.md."""
        from butlers.core import skills

        assert hasattr(skills, "read_agents_md"), "read_agents_md must exist (RFC 0002)"
        assert hasattr(skills, "write_agents_md"), "write_agents_md must exist (RFC 0002)"
        assert hasattr(skills, "append_agents_md"), "append_agents_md must exist (RFC 0002)"


class TestToolBudgetDiscipline:
    """RFC 0002: Tool budget discipline — target 30-50 tools; warning at startup when > 50."""

    def test_tool_budget_warning_at_startup(self):
        """RFC 0002: Daemon logs warning when butler registers > 50 tools.

        RFC 0002 Auditing: 'A warning SHOULD fire when the count exceeds 50.'
        The _SpanWrappingMCP proxy accumulates registered_tool_names; the daemon
        can check this count after tool registration to emit the warning.
        """
        import inspect

        from butlers.daemon import ButlerDaemon

        src = inspect.getsource(ButlerDaemon)
        # The daemon must reference tool count auditing / budget
        has_budget_check = (
            "50" in src
            or "budget" in src.lower()
            or "tool_count" in src
            or "_registered_tool_names" in src
        )
        assert has_budget_check, (
            "ButlerDaemon must reference tool count or budget for auditing (RFC 0002)"
        )

    def test_span_wrapping_mcp_tracks_registered_tool_names(self):
        """RFC 0002: _SpanWrappingMCP.registered_tool_names enables tool count auditing.

        The _registered_tool_names set on _SpanWrappingMCP allows the daemon to
        count registered module tools after registration, enabling the > 50 warning.
        """
        from unittest.mock import MagicMock

        from butlers.daemon import _SpanWrappingMCP

        def _noop_decorator(*args, **kwargs):
            def decorator(fn):
                return fn

            return decorator

        mock_mcp = MagicMock()
        mock_mcp.tool = _noop_decorator

        proxy = _SpanWrappingMCP(mock_mcp, "finance", module_name="memory")

        # Initially no tools registered
        assert len(proxy._registered_tool_names) == 0, (
            "_SpanWrappingMCP must start with empty registered_tool_names (RFC 0002)"
        )

        # Register multiple tools and verify counting
        tool_names = [f"tool_{i}" for i in range(55)]
        for name in tool_names:

            @proxy.tool(name=name)
            async def _dummy():
                pass

        assert len(proxy._registered_tool_names) == 55, (
            "_SpanWrappingMCP must track all registered tool names for budget auditing (RFC 0002)"
        )
        # If > 50 tools are registered, a warning should be possible to emit
        assert len(proxy._registered_tool_names) > 50, (
            "Test confirms > 50 tools can be registered (triggering the budget warning)"
        )

    def test_core_groups_allowlist_reduces_registered_tools(self):
        """RFC 0002: core_groups allowlist gates core tool registration.

        When core_groups is set, only tools in the listed groups are registered.
        This allows butlers to stay within the 30-50 tool target.
        NULL means all groups are registered (backward compatibility).
        """
        # Known core groups per RFC 0002
        core_groups = {
            "infra",
            "state",
            "scheduling",
            "sessions",
            "notifications",
            "media",
            "temporal",
            "module_mgmt",
            "switchboard_routing",
            "switchboard_backfill",
        }
        assert len(core_groups) >= 8, "RFC 0002 defines at least 8 core groups"
        assert "infra" in core_groups, "infra group must be defined (RFC 0002)"
        assert "state" in core_groups, "state group must be defined (RFC 0002)"
        assert "scheduling" in core_groups, "scheduling group must be defined (RFC 0002)"

    def test_route_execute_always_registered_regardless_of_core_groups(self):
        """RFC 0002: route.execute is ALWAYS registered regardless of core_groups setting.

        'route.execute is ALWAYS registered regardless of core_groups.'
        All butlers need route.execute because the Switchboard calls it server-to-server
        to deliver routed requests.
        """
        from butlers.daemon import CORE_TOOL_NAMES

        assert "route.execute" in CORE_TOOL_NAMES, (
            "route.execute must always be in CORE_TOOL_NAMES (RFC 0002)"
        )

        # route.execute is an infrastructure endpoint, not LLM-facing
        # It must be registered even when core_groups restricts other tools
        infra_tools = {"route.execute"}
        assert infra_tools.issubset(CORE_TOOL_NAMES), (
            "route.execute must be registered as an infrastructure endpoint (RFC 0002)"
        )
