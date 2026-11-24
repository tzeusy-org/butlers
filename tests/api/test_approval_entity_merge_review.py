"""Dashboard detail carries a typed merge review instead of a generic preview.

REQ-module-approvals-008 "Unsupported merge arguments remain unapprovable" and
REQ-module-approvals-009 "Older clients remain safely unable to approve merges".
Pure mapping from a stored row to the dossier; the approve-route binding lives in
test_approval_dashboard_call_binding.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from butlers.api.routers import approvals as routes
from butlers.modules.approvals.models import PendingAction
from butlers.modules.approvals.previews import args_digest

pytestmark = pytest.mark.unit

_SOURCE = "28039ce4-c849-49a4-a7bf-c951b68fcb41"
_TARGET = "22b6c972-626e-49b8-be9b-c13f8b882573"
_ARGS = {"source_entity_id": _SOURCE, "target_entity_id": _TARGET}


def _detail(
    tool_name="memory_entity_merge", tool_args=None, status="pending", butler="relationship"
):
    action = PendingAction.from_row(
        {
            "id": uuid4(),
            "tool_name": tool_name,
            "tool_args": _ARGS if tool_args is None else tool_args,
            "status": status,
            "requested_at": datetime.now(UTC),
        }
    )
    return routes._pending_action_to_detail(action, butler)


@pytest.mark.parametrize("tool_name", ["memory_entity_merge", "entity_merge"])
def test_pending_relationship_merge_gets_a_typed_review_and_no_generic_preview(tool_name):
    detail = _detail(tool_name)
    assert detail.call_preview is None
    review = detail.entity_merge_review
    assert review is not None
    assert (review.source_entity_id, review.target_entity_id) == (_SOURCE, _TARGET)
    # The stored name is kept: a legacy row is reviewed as stored, never renamed.
    assert review.displayed_call.tool_name == tool_name
    assert review.displayed_call.args_digest == args_digest(_ARGS)


@pytest.mark.parametrize(
    ("tool_args", "status", "butler"),
    [
        ({"source_entity_id": _SOURCE, "target_entity_id": _SOURCE}, "pending", "relationship"),
        ({"source_entity_id": _SOURCE, "target_entity_id": "nope"}, "pending", "relationship"),
        ({**_ARGS, "extra": "unshown"}, "pending", "relationship"),
        ({"source_entity_id": _SOURCE}, "pending", "relationship"),
        (_ARGS, "approved", "relationship"),
        (_ARGS, "rejected", "relationship"),
        (_ARGS, "pending", "general"),
    ],
)
def test_unshowable_or_decided_merges_get_no_review(tool_args, status, butler):
    detail = _detail(tool_args=tool_args, status=status, butler=butler)
    assert detail.entity_merge_review is None
    assert detail.call_preview is None


def test_other_tools_keep_their_generic_preview():
    detail = _detail(
        "telegram_send_message", {"chat_id": "test-recipient", "text": "Hello"}, butler="general"
    )
    assert detail.entity_merge_review is None
    assert detail.call_preview is not None
