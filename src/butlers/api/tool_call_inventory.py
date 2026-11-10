"""Every dashboard call site that calls a butler tool, classified (RFC 0038 D7).

All of them call through :class:`butlers.api.deps.DashboardButlerClient`, the
only MCP client the dashboard constructs. With ``BUTLERS_SERVICE_PLANE_AUTH``
on, that client carries a model-reachable tool through Switchboard's service
listener under a signed service-call capability and leaves a service-only tool
direct. The classes below name what each site is, so a reviewer can see the
whole direct surface in one place:

``control_plane``
    The tool is a literal, declared LLM-presentable (or undeclared). It goes
    through Switchboard once the switch is on.
``service_direct``
    The tool is a literal declared ``presentable=False``: a service-only
    control tool (lifecycle, ingest, dispatch). It stays direct.
``runtime_classified``
    The tool name is a variable. The client applies the same rule per call, so
    a presentable name goes through Switchboard and a service-only one stays
    direct. ``butlers.call_butler_mcp_tool``, the generic ``/mcp/call`` proxy,
    is one of these: it reaches an arbitrary tool, and the capability path is
    what it gets for every model-reachable one.
``owner_capability``
    The call carries request meta. Only ``schedule_create`` with exactly the
    owner-creation claim stays direct, because the target verifies and
    consumes that single-use owner capability in its own transaction; any
    other meta on a model-reachable tool is refused by the client.

``tests/contracts/test_dashboard_tool_call_inventory.py`` scans the dashboard
source with ``ast`` and fails on a site missing here, a site listed here that
no longer exists, or a class that disagrees with the tool's declaration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

CallClass = Literal["control_plane", "service_direct", "runtime_classified", "owner_capability"]


@dataclass(frozen=True, slots=True)
class DashboardToolCall:
    """One ``call_tool`` site: file, enclosing function, tool, class."""

    path: str
    function: str
    #: The tool name for a literal site; the expression for a variable one.
    tool: str
    call_class: CallClass


def _sites(
    path: str, call_class: CallClass, *pairs: tuple[str, str]
) -> tuple[DashboardToolCall, ...]:
    return tuple(DashboardToolCall(path, function, tool, call_class) for function, tool in pairs)


_API = "src/butlers/api/routers"

DASHBOARD_TOOL_CALLS: Final[tuple[DashboardToolCall, ...]] = (
    *_sites(
        f"{_API}/butler_management.py",
        "control_plane",
        ("get_butler_memory_access", "memory_access"),
    ),
    *_sites(f"{_API}/butler_management.py", "service_direct", ("kill_butler", "shutdown")),
    *_sites(
        f"{_API}/approvals.py",
        "service_direct",
        ("_dispatch_approved_action_outcome", "dispatch_approved_action"),
    ),
    *_sites(f"{_API}/butlers.py", "runtime_classified", ("call_butler_mcp_tool", "tool_name")),
    *_sites(
        f"{_API}/butlers.py",
        "service_direct",
        ("_get_module_health_via_mcp", "status"),
        ("trigger_butler", "trigger"),
        ("force_butler_tick", "tick"),
    ),
    *_sites(
        f"{_API}/calendar_workspace.py",
        "control_plane",
        ("sync_workspace._sync_target", "calendar_force_sync"),
    ),
    *_sites(f"{_API}/calendar_workspace.py", "runtime_classified", ("_call_mcp_tool", "tool_name")),
    *_sites(
        f"{_API}/conversations.py",
        "service_direct",
        ("_submit_to_switchboard", "ingest"),
        ("_cancel_routed_session", "cancel_session"),
        ("_cancel_dashboard_message_turn", "cancel_session"),
    ),
    *_sites(f"{_API}/modules.py", "control_plane", ("_get_module_states_via_mcp", "module.states")),
    *_sites(f"{_API}/modules.py", "service_direct", ("set_module_enabled", "module.set_enabled")),
    *_sites(f"{_API}/qa.py", "service_direct", ("_force_patrol_via_daemon", "force_patrol")),
    *_sites(f"{_API}/runtime_config.py", "service_direct", ("_tool_surface_snapshot", "status")),
    *_sites(f"{_API}/schedules.py", "owner_capability", ("_call_mcp_tool", "tool_name")),
    *_sites(f"{_API}/schedules.py", "runtime_classified", ("_call_mcp_tool", "tool_name")),
    *_sites(
        f"{_API}/settings_console.py", "service_direct", ("_count_active_butlers._ping", "status")
    ),
    *_sites(
        f"{_API}/spend.py",
        "control_plane",
        ("_get_butler_session_stats", "sessions_summary"),
        ("_get_butler_session_stats_for_range", "sessions_daily"),
        ("_get_butler_daily_stats", "sessions_daily"),
        ("_get_butler_top_sessions", "top_sessions"),
        ("_get_butler_schedule_costs", "schedule_costs"),
    ),
    *_sites(
        f"{_API}/state.py",
        "control_plane",
        ("set_state", "state_set"),
        ("delete_state", "state_delete"),
    ),
    *_sites(
        "roster/chronicler/api/router.py",
        "service_direct",
        ("refresh_day_close", "chronicler_day_close_refresh"),
    ),
    *_sites(
        "roster/education/api/router.py", "service_direct", ("_run_curriculum_request", "trigger")
    ),
    *_sites(
        "roster/general/api/router.py",
        "control_plane",
        ("get_drive_file", "drive_get_file_metadata"),
    ),
    *_sites(
        "roster/relationship/api/router.py",
        "control_plane",
        ("_fetch_chronicler_activity", "chronicler_list_episodes"),
    ),
)
