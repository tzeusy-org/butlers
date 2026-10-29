"""Native serialization schemas for dashboard response models.

Validation still accepts declared defaults. Ordinary response serialization emits
those values, so output schemas mark them required. Models used by routes that
exclude unset/default/null values deliberately retain ordinary BaseModel instead.
"""

from pydantic import BaseModel, ConfigDict


class ResponseSchemaModel(BaseModel):
    """Describe emitted response defaults without changing validation or payloads."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
