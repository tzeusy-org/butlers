"""Positive contract: the Groups response payload carries a `labels` field.

Context (bu-ij93n / PR #2338, bu-jyz8j): group labels are a shipped feature.
``Group`` now includes ``labels: list[Label]`` so the dashboard "Labels" column
is backed by real data. This test guards the positive contract — the field must
be present, correctly typed, and exposed in the wire schema.

Replaced the old negative guard (test_group_no_labels_field.py, bu-56088.1)
that asserted the field must NOT exist. That guard was correct when labels were
dead (always-empty column with no backing table), but it is now wrong.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODELS_PATH = Path(__file__).resolve().parents[1] / "api" / "models.py"


def _load_models_module():
    spec = importlib.util.spec_from_file_location(
        "relationship_api_models_for_group_test", _MODELS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_group_model_has_labels_field() -> None:
    """`labels` must be a field on the Group response model, typed as list[Label]."""
    import typing

    module = _load_models_module()
    Group = module.Group
    Label = module.Label

    assert "labels" in Group.model_fields

    # models.py uses ``from __future__ import annotations``; resolve forward refs
    # before inspecting the annotation so we get the concrete type, not a ForwardRef.
    Group.model_rebuild(_types_namespace=vars(module))
    field = Group.model_fields["labels"]
    resolved = field.annotation

    # Should be list[Label].
    args = typing.get_args(resolved)
    assert len(args) == 1, f"Expected list[Label] but got {resolved!r}"
    assert args[0] is Label, f"Expected list[Label] inner type to be Label, got {args[0]!r}"


def test_group_response_schema_exposes_labels() -> None:
    """The Group JSON schema (the wire contract) must expose `labels` as an array."""
    module = _load_models_module()
    Group = module.Group
    # models.py uses ``from __future__ import annotations``; resolve the forward
    # refs against the module namespace before introspecting the JSON schema.
    Group.model_rebuild(_types_namespace=vars(module))
    schema = Group.model_json_schema()
    schema_properties = schema.get("properties", {})

    assert "labels" in schema_properties, (
        f"'labels' missing from Group JSON schema properties; got {list(schema_properties)}"
    )
    labels_schema = schema_properties["labels"]
    assert labels_schema.get("type") == "array", (
        f"Expected 'labels' to be array type in JSON schema, got {labels_schema!r}"
    )
    # Sanity: the core payload fields are still present.
    assert {"id", "name", "member_count", "created_at"} <= set(schema_properties)
