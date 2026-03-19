"""Search-specific Pydantic models.

Provides ``SearchResult`` and ``SearchResponse`` for the cross-butler
fan-out search endpoint that queries sessions, state, entities, and
contacts across butler databases and the shared schema.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """A single search result rendered in the command palette.

    Attributes
    ----------
    id:
        Unique identifier for the result (used as React key).
    butler:
        Name of the butler or source (e.g. ``"relationship"``, ``"system"``).
    type:
        Result category (e.g. ``"session"``, ``"entity"``, ``"contact"``).
    title:
        Display title for the result row.
    snippet:
        Secondary text shown below the title.
    url:
        Client-side route to navigate to when selected.
    """

    id: str
    butler: str
    type: str
    title: str
    snippet: str
    url: str


class SearchResponse(BaseModel):
    """Grouped search results from a cross-butler fan-out search.

    Results are grouped by category: entities, contacts, sessions, and
    state entries.
    """

    entities: list[SearchResult] = Field(default_factory=list)
    contacts: list[SearchResult] = Field(default_factory=list)
    sessions: list[SearchResult] = Field(default_factory=list)
    state: list[SearchResult] = Field(default_factory=list)
