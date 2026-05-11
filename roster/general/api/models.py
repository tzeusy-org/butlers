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


class SizeHistogramBucket(BaseModel):
    """A bucket in the collection size distribution histogram."""

    bracket: str  # e.g., "0", "1-10", "11-100", "101+"
    count: int  # number of collections in this bracket


class GeneralStats(BaseModel):
    """Aggregated statistics for the general butler's collections."""

    total_collections: int
    total_entities: int  # sum of entities across all collections
    last_modified_collection: str | None  # name of most recently modified collection
    largest_collection_size: int
    size_histogram: list[SizeHistogramBucket]
