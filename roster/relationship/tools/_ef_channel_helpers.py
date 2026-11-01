"""Compatibility exports for deterministic canonical channel helpers.

The shared implementation lives outside the aggregated Relationship tools package
so read-only API consumers do not import unrelated Memory mutation tools.
"""

from butlers.entity_facts_channels import (  # noqa: F401 - public compatibility exports
    TELEGRAM_HANDLE_PREFIX,
    ef_object_to_display_value,
    ef_predicate_to_ci_type,
    encode_handle_object,
    entity_facts_channels_by_entity,
)
