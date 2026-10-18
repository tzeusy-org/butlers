## Why

Since per-account probe serialization (bu-vothyr) a verify-all sweep lasts as long as its longest account group, up to n x the 45s probe budget. Two sweeps (the owner's button and the hourly job, in different processes) could still probe one allowance account together, and the dashboard's default 15s read timeout showed a false timeout while the server kept probing.

## What Changes

- A non-blocking Postgres advisory lock taken inside `run_verify_all_models`; the endpoint refuses with 429 and the hourly job skips the tick while it is held.
- The dashboard verify-all call gets an explicit timeout derived from the server's worst case; the call stays synchronous.
- Give the block `REQ-dashboard-model-settings-003`, the ID the pending harden-runtime-auth-and-breaker-attention change already assigns it, and add the "Cross-sweep exclusion" scenario.

## Impact

- `src/butlers/api/routers/model_settings.py`, `src/butlers/jobs/model_verify.py`, `frontend/src/api/client.ts`, `docs/runtime/model-routing.md`.
- Contract evolution: the pool fixtures of 13 retained tests gain an `acquire()` context manager; no assertion, case or parametrization changes.
