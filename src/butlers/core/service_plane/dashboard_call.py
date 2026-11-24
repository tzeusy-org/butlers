"""Which dashboard tool calls take the control plane (RFC 0038 D7).

One rule, shared by the dashboard client and Switchboard's service-call route:
a tool declared LLM-presentable, or not declared at all, goes through the
control plane; a tool declared service-only (``presentable=False``) stays
direct. The capability that carries a routed call is
``butlers.core.runtime_probe_control.service_call``.
"""

from __future__ import annotations


def routes_through_control_plane(tool: str) -> bool:
    """Whether a dashboard call to *tool* takes the control plane.

    Declared presentable, or undeclared: yes. An undeclared tool is assumed
    model-reachable, so a missing declaration fails closed rather than direct.
    """
    from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME

    declared = TOOL_PRESENTATION_BY_NAME.get(tool)
    return declared is None or declared.llm_presentable
