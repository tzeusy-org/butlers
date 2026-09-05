"""Dispatcher: calls all domain register_* functions to wire up core tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from butlers.core_tools._base import ToolContext
from butlers.core_tools._chronicler import register_chronicler_tools
from butlers.core_tools._conversation_recall import register_conversation_recall_tool
from butlers.core_tools._conversation_reply import register_conversation_reply_tool
from butlers.core_tools._delegation import register_delegation_tools
from butlers.core_tools._domain_events import register_domain_event_tools
from butlers.core_tools._graph import register_graph_tools
from butlers.core_tools._infra import register_infra_tools
from butlers.core_tools._media import register_media_tools
from butlers.core_tools._memory_access import register_memory_access_tool
from butlers.core_tools._memory_catalog import register_memory_catalog_tools
from butlers.core_tools._messenger import register_messenger_tools
from butlers.core_tools._module_mgmt import register_module_mgmt_tools
from butlers.core_tools._notifications import register_notification_tools
from butlers.core_tools._routing import register_routing_tools
from butlers.core_tools._scheduling import register_scheduling_tools
from butlers.core_tools._sessions import register_session_tools
from butlers.core_tools._shutdown import register_shutdown_tool
from butlers.core_tools._state import register_state_tools
from butlers.core_tools._switchboard import register_switchboard_tools
from butlers.core_tools._temporal import register_temporal_tools


def register_all_core_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register all core tools by calling each domain module's register function.

    Order follows the original daemon.py registration sequence:
      1. State tools (state_get/set/delete/list)
      2. Infra tools (status, trigger, tick, correct)
      3. Scheduling tools (schedule_*)
      4. Session tools (sessions_*)
      5. Notification tools (remind, notify)
      6. Temporal tools (deadline_*, event_chain_*, seasonal_period_*)
      7. Media tools (get_attachment)
      8. Module management tools (module.states, module.set_enabled)
      9. Routing tool (route.execute)  — always registered, no group gate
      10. Switchboard tools (ingest, route_to_butler, connector.heartbeat, backfill.*)
      11. Messenger tools (delivery prefs, deferred notifications, delivery ops)
      12. Memory-access tool (memory_access) — always registered, degrades gracefully
      13. Memory catalog fetch — server-authorized and Switchboard-brokered
      14. Conversation-reply tool (conversation_reply) — always registered,
          dashboard chat confirm-loop reply channel
      15. Conversation-recall tools (conversation_recall, conversation_thread_read)
          — always registered, owner-scoped cross-butler dashboard chat recall
      16. Delegation tools (delegate_ask, delegate_receive, delegate_answer, delegate_wake)
      17. Domain-event tools (publish_event, subscribe_to_event,
          unsubscribe_from_event, list_my_subscriptions, receive_domain_event,
          report_event_reaction)
      18. Graph tools (entity_graph_walk, entity_graph_path) — always
          registered, zero-LLM recursive-CTE traversal over
          public.entity_graph_edges
      19. Shutdown tool (shutdown)
    """
    register_state_tools(ctx, mcp, _core_tool)
    register_infra_tools(ctx, mcp, _core_tool)
    register_chronicler_tools(ctx, mcp, _core_tool)
    register_scheduling_tools(ctx, mcp, _core_tool)
    register_session_tools(ctx, mcp, _core_tool)
    register_notification_tools(ctx, mcp, _core_tool)
    register_temporal_tools(ctx, mcp, _core_tool)
    register_media_tools(ctx, mcp, _core_tool)
    register_module_mgmt_tools(ctx, mcp, _core_tool)
    register_routing_tools(ctx, mcp, _core_tool)
    register_switchboard_tools(ctx, mcp, _core_tool)
    register_messenger_tools(ctx, mcp, _core_tool)
    register_memory_access_tool(ctx, mcp, _core_tool)
    register_memory_catalog_tools(ctx, mcp, _core_tool)
    register_conversation_reply_tool(ctx, mcp, _core_tool)
    register_conversation_recall_tool(ctx, mcp, _core_tool)
    register_delegation_tools(ctx, mcp, _core_tool)
    register_domain_event_tools(ctx, mcp, _core_tool)
    register_graph_tools(ctx, mcp, _core_tool)
    register_shutdown_tool(ctx, mcp, _core_tool)
