"""Perception timeline rows as ``transition`` condition events (RFC 0038 S3, S4; dark).

bu-dhloi1.305 PR5c. A ``transition`` leaf waits on a perception slot's state edge. Its events
are the bu-dhloi1.9 timeline's own rows, as ``public.perception_timeline`` returns them (the
table's columns plus ``retracted``); nothing here reads the database, registers a producer or
feeds an evaluator. That wiring, and lifting the ``policy`` refusal of ``transition`` leaves, wait
on bu-jv1moa.27.

Each event carries the row's S1 label, so a fire it holds joins that label (PR5a). The timeline
stores its label as perception writes it: ``world`` or an opaque, server-resolved principal
reference (a UUID) for I and D, and the six categories for C. A reference becomes the S1
principal ``external:<uuid>``. A row with no label, or one that is not a well-formed version-1
label, yields an unlabelled event: missing provenance, which joins ``unknown``, never ``owner``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from butlers.conditions.model import ConditionEvent
from butlers.core.goal_labels import Label, LabelRefused
from butlers.perception.model import instant

#: Perception's unknown-provenance principal, which is also S1's.
_WORLD = "world"


def source_for(kind: str) -> str:
    """The condition source a ``transition`` leaf on ``kind`` subscribes to."""
    return f"perception:{kind}"


def timeline_label(value: Any) -> Label | None:
    """A timeline row's stored label as an S1 label, or None when it has none or is malformed."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, Mapping) or set(value) != {
        "version",
        "label_instr",
        "label_data",
        "label_sens",
    }:
        return None
    try:
        return Label(
            _principals(value["label_instr"]),
            _principals(value["label_data"]),
            frozenset(_names(value["label_sens"])),
            value["version"],
        )
    except (LabelRefused, ValueError, TypeError):
        return None


def _names(value: Any) -> list[str]:
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise LabelRefused("label component must be a list of names")
    return value


def _principals(value: Any) -> frozenset[str]:
    members = set()
    for item in _names(value):
        if item == _WORLD:
            members.add(_WORLD)
        elif str(UUID(item)) == item:
            members.add(f"external:{item}")
        else:
            raise LabelRefused("unknown perception principal")
    return frozenset(members)


def _time(value: Any) -> datetime:
    return value if isinstance(value, datetime) else instant(value)


def transition_event(row: Mapping[str, Any]) -> ConditionEvent | None:
    """The condition event a timeline row is, or None when it is no ``transition``.

    The event is the row: its id is the row's id, its time the row's ``at`` and its position the
    row's write time in microseconds. A late row, a row a later retraction withdrew, and a
    retraction itself are passed with those flags, which the evaluator never lets set a leaf.
    """
    if row.get("class") != "transition":
        return None
    subject = row.get("subject_entity")
    return ConditionEvent(
        id=str(UUID(str(row["id"]))),
        source=source_for(row["kind"]),
        position=int(_time(row["created_at"]).timestamp() * 1_000_000),
        at=_time(row["at"]),
        tag="transition",
        data={
            "kind": row["kind"],
            "subject": None if subject is None else str(subject),
            "from": row.get("from_state"),
            "to": row.get("to_state"),
        },
        late=bool(row.get("late")),
        retracted=bool(row.get("retracted")),
        record=row.get("operation") == "retract",
        label=timeline_label(row.get("labels")),
    )
