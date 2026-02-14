"""Source connectors for external message ingestion.

Connectors are transport-only adapters that:
- Read events from external systems (Telegram, Gmail, etc.)
- Normalize to canonical ingest.v1 format
- Submit to Switchboard ingest API
- Handle checkpointing and resume logic
"""

__all__ = ["gmail"]
