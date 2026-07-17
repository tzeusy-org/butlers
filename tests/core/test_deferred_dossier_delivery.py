"""Decision-dossier preservation across deferred-notification flushing."""

from __future__ import annotations

import uuid

import pytest

from butlers.core.scheduler import _group_due_deferred_notifications

pytestmark = pytest.mark.unit


def _deferred_row(notification_id: uuid.UUID, *, message: str, why: str) -> dict[str, object]:
    return {
        "id": notification_id,
        "channel": "telegram",
        "message": message,
        "envelope": {
            "schema_version": "notify.v1",
            "origin_butler": "health",
            "delivery": {
                "intent": "send",
                "channel": "telegram",
                "message": message,
                "recipient": "900800700",
            },
            "decision_dossier": {
                "why": why,
                "evidence": [],
                "blast_radius": "contact",
                "reversibility": "compensable",
            },
        },
    }


def test_dossier_bearing_deferred_rows_are_not_coalesced() -> None:
    """Each non-owner action must retain its own validated delivery dossier."""
    first = _deferred_row(uuid.uuid4(), message="First update", why="First request")
    second = _deferred_row(uuid.uuid4(), message="Second update", why="Second request")

    groups = _group_due_deferred_notifications([first, second])

    assert groups == [[first], [second]]
