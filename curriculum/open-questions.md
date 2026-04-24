# Open Questions

The core curriculum is strongly evidence-backed. These items should be revisited as the repository evolves:

1. WhatsApp bridge maturity: the repo contains a Go sidecar and related OpenSpec design material, but it is narrower than the main Python/MCP/PostgreSQL system. Treat it as nice-later unless changing WhatsApp.
2. Frontend depth: dashboard/API contracts are real and should be learned before UI work, but the repo evidence points to server-state and REST-contract awareness rather than a full frontend architecture curriculum.
3. OpenSpec/doctrine workflow depth: specs and doctrine shape safe changes, but they are not a transferable technical prerequisite in the same way as async runtime or database migrations. This curriculum keeps them in contribution readiness rather than a standalone module.
4. External integration breadth: Gmail, Google Calendar, Discord, Telegram, Steam, Spotify, and other connectors/modules each have provider-specific APIs. The curriculum teaches the shared transport, OAuth, credential, checkpointing, and side-effect patterns rather than every provider API.
5. Production readiness: the README explicitly warns the project is experimental. Operational concepts are still included because the code uses real observability, Docker, DB roles, and retry machinery, but learners should verify current production assumptions against `docs/operations/` before deployment work.
