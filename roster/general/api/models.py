"""Pydantic models for the general butler API.

Provides models for collections and entities.
"""

from __future__ import annotations

from pydantic import BaseModel


class Collection(BaseModel):
    """A named collection of entities in the general butler."""

    id: str
    name: str
    description: str | None = None
    entity_count: int = 0  # computed via COUNT
    created_at: str


class Entity(BaseModel):
    """An entity within a collection."""

    id: str
    collection_id: str
    collection_name: str | None = None  # joined from collections
    data: dict = {}  # JSONB
    tags: list[str] = []  # JSONB
    created_at: str
    updated_at: str
