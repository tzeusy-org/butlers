"""Regression: the Groups response payload must NOT carry a `labels` field.

Context (bu-56088.1): the Groups dashboard "Labels" column was structurally
dead — the API set ``labels=[]`` literals with no ``group_labels`` table behind
it, and the UI rendered an always-empty column. The dead column was dropped for
an honest UI. This test guards against the field silently coming back.

If group labels are genuinely built later (separate feature bead), this test
should be updated alongside the new write path.
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


def test_group_model_has_no_labels_field() -> None:
    """`labels` must not be a field on the Group response model."""
    Group = _load_models_module().Group
    assert "labels" not in Group.model_fields


def test_group_response_schema_omits_labels() -> None:
    """The Group JSON schema (the wire contract) must not expose `labels`."""
    module = _load_models_module()
    Group = module.Group
    # models.py uses ``from __future__ import annotations``; resolve the forward
    # refs against the module namespace before introspecting the JSON schema.
    Group.model_rebuild(_types_namespace=vars(module))
    schema_properties = Group.model_json_schema().get("properties", {})
    assert "labels" not in schema_properties
    # Sanity: the honest payload fields are still present.
    assert {"id", "name", "member_count", "created_at"} <= set(schema_properties)
