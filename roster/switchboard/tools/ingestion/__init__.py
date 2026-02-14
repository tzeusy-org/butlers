"""Switchboard ingestion API — canonical ingest boundary for connectors."""

from butlers.tools.switchboard.ingestion.ingest import (
    IngestAcceptedResponse,
    ingest_v1,
)

__all__ = [
    "IngestAcceptedResponse",
    "ingest_v1",
]
