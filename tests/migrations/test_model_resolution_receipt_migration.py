"""Contract tests for core_244 model-resolution receipt storage."""

from pathlib import Path

from butlers.core.dispatch_outcomes import bound_resolution_receipt

MIGRATION = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_244_model_resolution_receipt.py"
)


def test_migration_is_ordered_and_reversible() -> None:
    source = MIGRATION.read_text()
    assert 'revision = "core_244"' in source
    assert 'down_revision = "core_243"' in source
    assert "ADD COLUMN IF NOT EXISTS resolution_receipt JSONB" in source
    assert "DROP COLUMN IF EXISTS resolution_receipt" in source


def test_oversized_receipt_is_truncated_not_dropped() -> None:
    receipt = {
        "policy_version": "dispatch-fit-v1",
        "winner": {"model_id": "winner"},
        "candidates": [
            {"catalog_entry_id": str(index), "model_id": "x" * 2_000} for index in range(30)
        ],
    }
    bounded = bound_resolution_receipt(receipt)
    assert bounded is not None
    assert bounded["truncated"] is True
    assert bounded["candidate_count"] == 30
    assert len(bounded["candidates"]) < 30
