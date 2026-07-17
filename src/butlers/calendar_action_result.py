"""Shared normalization for ``calendar_action_log.action_result`` values."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def reconstruct_action_result(value: object) -> dict[str, Any]:
    """Normalize an ``action_result`` JSONB value into its merged object form.

    Handles canonical object rows as well as legacy rows corrupted by the
    double-JSON-encoding bug (bu-x92jw). Postgres's ``||`` between a JSONB
    object and a JSONB string scalar coerced both values into an array, such as
    ``[{...original...}, "{\\"undo\\": {...}}"]``. Merge each array element in
    order so a later marker wins, matching the intended JSONB-object concat
    semantics. Non-object values remain an empty result.
    """
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    if isinstance(value, list):
        merged: dict[str, Any] = {}
        for element in value:
            if isinstance(element, Mapping):
                merged.update(element)
            elif isinstance(element, str):
                try:
                    parsed_element = json.loads(element)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed_element, Mapping):
                    merged.update(parsed_element)
        return merged
    return {}
