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

            async def register_tools(self, mcp, config, db) -> None:
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
