"""Deterministic fact attribution read models shared by tools and dashboard.

This module has no mutation, Memory, embedding or model-runtime dependency.
"""

from __future__ import annotations

from typing import Any


def attribution_select_sql(alias: str = "f") -> str:
    """Read-model fields; the historical UUID is intentionally never joined."""
    return f"""
      {alias}.content_authority, {alias}.confirmed_at, {alias}.confirmed_by_entity_id,
      {alias}.confirmation_source,
      CASE
        WHEN {alias}.verified AND {alias}.confirmed_at IS NOT NULL
          AND {alias}.confirmed_by_original_entity_id IS NOT NULL
          AND {alias}.confirmation_source='owner_assertion' THEN 'owner_asserted'
        WHEN {alias}.verified AND {alias}.confirmed_at IS NOT NULL
          AND {alias}.confirmed_by_original_entity_id IS NOT NULL
          AND {alias}.confirmation_source IS NOT NULL THEN 'owner_confirmed'
        WHEN {alias}.verified THEN 'legacy_verified'
        ELSE 'unconfirmed' END AS confirmation_status,
      jsonb_build_object(
        'entity_id', (SELECT e.id FROM public.entities e WHERE e.id={alias}.authority_entity_id
          AND e.metadata->>'merged_into' IS NULL AND e.metadata->>'deleted_at' IS NULL),
        'name', (SELECT e.canonical_name FROM public.entities e
          WHERE e.id={alias}.authority_entity_id
          AND e.metadata->>'merged_into' IS NULL AND e.metadata->>'deleted_at' IS NULL),
        'availability', CASE
          WHEN {alias}.content_authority IS NULL THEN 'legacy_unknown'
          WHEN EXISTS(SELECT 1 FROM public.entities e WHERE e.id={alias}.authority_entity_id
            AND e.metadata->>'merged_into' IS NOT NULL) THEN 'merged'
          WHEN EXISTS(SELECT 1 FROM public.entities e WHERE e.id={alias}.authority_entity_id
            AND e.metadata->>'deleted_at' IS NOT NULL) THEN 'forgotten'
          WHEN EXISTS(SELECT 1 FROM public.entities e WHERE e.id={alias}.authority_entity_id
            AND e.metadata->>'merged_into' IS NULL AND e.metadata->>'deleted_at' IS NULL)
            THEN 'available'
          WHEN {alias}.content_authority='system' AND {alias}.authority_original_entity_id IS NULL
            THEN 'system'
          WHEN {alias}.authority_original_entity_id IS NULL THEN 'unresolved'
          WHEN {alias}.authority_entity_id IS NULL
            AND {alias}.authority_entity_created_at IS NOT NULL THEN 'deleted'
          ELSE 'unavailable' END) AS reported_by
    """


def attribution_response(row: Any) -> dict[str, Any]:
    response = {
        key: row.get(key)
        for key in (
            "content_authority",
            "reported_by",
            "confirmed_at",
            "confirmed_by_entity_id",
            "confirmation_source",
        )
    }
    status = row.get("confirmation_status")
    if status is None:
        # An old read-model row without the new computed status has no
        # complete confirmation witness. Preserve raw legacy verification,
        # without inventing a server-confirmed badge from partial fields.
        status = "legacy_verified" if row.get("verified") is True else "unconfirmed"
    response["confirmation_status"] = status
    return response
