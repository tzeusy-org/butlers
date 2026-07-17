"""Unit tests for the calendar_action_log.action_result jsonb repair (bu-x92jw).

``reconstruct_action_result`` recovers the equivalent merged object from rows
corrupted by the action_result double-JSON-encoding bug (fixed in
api/routers/calendar_workspace.py's undo-marker writes): Postgres's
``||`` between a jsonb object and a jsonb-typed STRING scalar coerces both
operands into an array, so a corrupted row looks like
``[{...original...}, "{\"undo\": {...}}"]`` instead of a merged object.

``CalendarModule._load_projection_action`` is the idempotent-replay reader for
the *original* mutation's action_result (shared column with the undo guard in
calendar_workspace.py) -- these tests confirm it reconstructs and self-heals a
corrupted row instead of silently treating it as empty, which would otherwise
let a retried request with the same idempotency key re-execute the original
mutation instead of returning the cached result.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.api.routers.calendar_workspace import reconstruct_action_result as workspace_result
from butlers.calendar_action_result import reconstruct_action_result
from butlers.modules.calendar import CalendarModule
from butlers.modules.calendar import reconstruct_action_result as module_result

pytestmark = pytest.mark.unit


class TestReconstructActionResult:
    def test_workspace_and_module_share_the_single_normalizer(self):
        assert workspace_result is reconstruct_action_result
        assert module_result is reconstruct_action_result

    def test_plain_object_passes_through(self):
        value = {"status": "updated", "pre_state": {"event_id": "evt-1"}}
        assert reconstruct_action_result(value) == value

    def test_json_string_object_decodes(self):
        value = json.dumps({"status": "updated"})
        assert reconstruct_action_result(value) == {"status": "updated"}

    def test_none_and_garbage_return_empty_dict(self):
        assert reconstruct_action_result(None) == {}
        assert reconstruct_action_result(42) == {}
        assert reconstruct_action_result("not json") == {}

    def test_corrupted_array_merges_object_and_string_elements(self):
        """The canonical corruption shape: an original object element plus a
        double-encoded jsonb string element carrying the undo marker."""
        original = {"status": "updated", "pre_state": {"event_id": "evt-1"}}
        marker_string = json.dumps({"undo": {"status": "pending", "request_id": "undo-1"}})
        corrupted = [original, marker_string]

        result = reconstruct_action_result(corrupted)

        assert result["status"] == "updated"
        assert result["pre_state"] == {"event_id": "evt-1"}
        assert result["undo"] == {"status": "pending", "request_id": "undo-1"}

    def test_corrupted_array_later_marker_wins(self):
        """A row corrupted twice (provisional claim, then finalize) merges in
        order so the later (finalize) marker overrides the earlier one."""
        original = {"status": "updated"}
        pending_marker = json.dumps({"undo": {"status": "pending"}})
        final_marker = json.dumps({"undo": {"status": "updated"}})
        corrupted = [original, pending_marker, final_marker]

        result = reconstruct_action_result(corrupted)

        assert result["undo"] == {"status": "updated"}

    def test_array_with_unparseable_string_element_is_ignored(self):
        corrupted = [{"status": "updated"}, "not valid json"]
        result = reconstruct_action_result(corrupted)
        assert result == {"status": "updated"}


class TestLoadProjectionActionHealsCorruptedRows:
    def _make_module(self, *, action_result, action_status="applied", error=None):
        mod = CalendarModule()
        mod._projection_tables_available_cache = True
        mock_pool = SimpleNamespace(
            fetchrow=AsyncMock(
                return_value={
                    "action_status": action_status,
                    "action_result": action_result,
                    "error": error,
                }
            ),
            execute=AsyncMock(),
        )
        mod._db = SimpleNamespace(pool=mock_pool)
        return mod, mock_pool

    async def test_clean_object_row_returns_result_without_repair_write(self):
        clean = {"status": "updated", "pre_state": {"event_id": "evt-1"}}
        mod, mock_pool = self._make_module(action_result=clean)

        status, result, error = await mod._load_projection_action("key-1")

        assert status == "applied"
        assert result == clean
        assert error is None
        mock_pool.execute.assert_not_awaited()

    async def test_corrupted_array_row_reconstructs_and_heals(self):
        original = {"status": "updated", "pre_state": {"event_id": "evt-1"}}
        marker_string = json.dumps({"undo": {"status": "pending", "request_id": "undo-1"}})
        corrupted = [original, marker_string]
        mod, mock_pool = self._make_module(action_result=corrupted)

        status, result, error = await mod._load_projection_action("key-1")

        assert status == "applied"
        assert result is not None
        assert result["status"] == "updated"
        assert result["pre_state"] == {"event_id": "evt-1"}
        assert result["undo"] == {"status": "pending", "request_id": "undo-1"}
        assert error is None

        # Guarded self-heal: exactly one repair UPDATE, scoped to array-shaped
        # rows via jsonb_typeof, writing the reconstructed object back.
        mock_pool.execute.assert_awaited_once()
        sql, key_arg, healed_arg = mock_pool.execute.await_args.args
        assert "jsonb_typeof(action_result) = 'array'" in sql
        assert key_arg == "key-1"
        assert healed_arg == result
