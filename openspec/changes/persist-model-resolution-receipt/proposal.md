## Why

Intent-aware routing computes a prompt-free explanation of every candidate and
the winning tie-break, but the spawner discards it before writing dispatch
provenance. Historical sessions therefore cannot answer why a model won.

## What Changes

- Persist a bounded resolution receipt on every dispatch-attempt row.
- Carry the preceding failure class onto a failover attempt's receipt.
- Expose receipts through dispatch/model reads and session detail.
- Render a truthful session disclosure, including explicit legacy absence.

## Impact

- `public.model_dispatch_attempts`, core routing/spawner persistence, session and
  Models API reads, the session dossier, and focused migration/routing/UI tests.
- Routing decisions and attempt-spend accounting remain unchanged.
