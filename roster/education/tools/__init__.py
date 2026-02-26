"""Education butler tools — mind maps, nodes, edges, queries, mastery, spaced repetition, analytics.

Re-exports all public symbols from the education tool sub-modules so that
``from butlers.tools.education import X`` works as a stable public API.
"""

from __future__ import annotations

from butlers.tools.education.analytics import (
    analytics_compute_all,
    analytics_compute_snapshot,
    analytics_get_cross_topic,
    analytics_get_snapshot,
    analytics_get_trend,
)
from butlers.tools.education.curriculum import (
    curriculum_generate,
    curriculum_next_node,
    curriculum_replan,
)
from butlers.tools.education.diagnostic import (
    diagnostic_complete,
    diagnostic_record_probe,
    diagnostic_start,
)
from butlers.tools.education.mastery import (
    mastery_detect_struggles,
    mastery_get_map_summary,
    mastery_get_node_history,
    mastery_record_response,
)
from butlers.tools.education.mind_map_edges import (
    mind_map_edge_create,
    mind_map_edge_delete,
)
from butlers.tools.education.mind_map_nodes import (
    mind_map_node_create,
    mind_map_node_get,
    mind_map_node_list,
    mind_map_node_update,
)
from butlers.tools.education.mind_map_queries import (
    mind_map_frontier,
    mind_map_subtree,
)
from butlers.tools.education.mind_maps import (
    mind_map_create,
    mind_map_get,
    mind_map_list,
    mind_map_update_status,
)
from butlers.tools.education.spaced_repetition import (
    sm2_update,
    spaced_repetition_pending_reviews,
    spaced_repetition_record_response,
    spaced_repetition_schedule_cleanup,
)
from butlers.tools.education.teaching_flows import (
    assemble_session_context,
    check_stale_flows,
    teaching_flow_abandon,
    teaching_flow_advance,
    teaching_flow_get,
    teaching_flow_list,
    teaching_flow_start,
)

__all__ = [
    # mind map CRUD
    "mind_map_create",
    "mind_map_get",
    "mind_map_list",
    "mind_map_update_status",
    # node CRUD
    "mind_map_node_create",
    "mind_map_node_get",
    "mind_map_node_list",
    "mind_map_node_update",
    # edge management
    "mind_map_edge_create",
    "mind_map_edge_delete",
    # queries
    "mind_map_frontier",
    "mind_map_subtree",
    # mastery tracking
    "mastery_record_response",
    "mastery_get_node_history",
    "mastery_get_map_summary",
    "mastery_detect_struggles",
    # spaced repetition
    "sm2_update",
    "spaced_repetition_record_response",
    "spaced_repetition_pending_reviews",
    "spaced_repetition_schedule_cleanup",
    # analytics
    "analytics_compute_snapshot",
    "analytics_compute_all",
    "analytics_get_snapshot",
    "analytics_get_trend",
    "analytics_get_cross_topic",
    # diagnostic assessment
    "diagnostic_start",
    "diagnostic_record_probe",
    "diagnostic_complete",
    # curriculum planning
    "curriculum_generate",
    "curriculum_replan",
    "curriculum_next_node",
    # teaching flows
    "teaching_flow_start",
    "teaching_flow_get",
    "teaching_flow_advance",
    "teaching_flow_abandon",
    "teaching_flow_list",
    "assemble_session_context",
    "check_stale_flows",
]
