# dashboard-model-settings

## MODIFIED Requirements

### Requirement: Catalog Verify-All API
The dashboard SHALL expose `POST /api/settings/models/verify-all` to re-verify every enabled model in parallel. The verification core is shared (`butlers.api.routers.model_settings.run_verify_all_models`) between this manual endpoint and the hourly automated sweep (see Hourly Automated Verification Sweep) so the two can never disagree about what "verified" means or how the result is persisted.

ID: REQ-dashboard-model-settings-003
Source: dashboard-owner-auth successor design D1-D9; existing dashboard-model-settings behavior preserved except explicit owner-auth supersession
Scope: v1-mandatory

#### Scenario: Verify-all parallel execution
- **WHEN** `POST /api/settings/models/verify-all` is called
- **THEN** the system issues a 1-token completion against each enabled model concurrently with a bounded concurrency of 8
- **AND** rows that share an allowance account (`COALESCE(allowance_account, runtime_type)`, across runtimes) are probed one after another within the overall bound of 8, so one account never sees concurrent probes
- **AND** for each model, `last_verified_at`, `last_verified_latency_ms`, `last_verified_ok`, and `last_verified_error` are persisted
- **AND** `last_verified_error` is set to the truncated exception text on failure (or `"verification returned an empty response"` when the probe completed with no usable output) and cleared to `NULL` on success
- **AND** the call is rate-limited to once per minute system-wide; subsequent calls within the minute return `429 Too Many Requests`
- **AND** `audit.append("models.verify_all", actor="owner")` is invoked once per accepted run.

#### Scenario: Cross-sweep exclusion
- **WHEN** a verify-all sweep is running, started by `POST /api/settings/models/verify-all` or by the hourly sweep, in any dashboard-api process
- **THEN** a second sweep SHALL NOT probe any model: the endpoint refuses with `429 Too Many Requests` and the hourly sweep skips that tick and logs it
- **AND** exclusion is a non-blocking Postgres advisory lock taken inside `run_verify_all_models` and held for the whole sweep, so no allowance account sees probes from two sweeps at once
- **AND** the lock is released when the sweep ends and by PostgreSQL if the holding connection dies
- **AND** the dashboard waits for a synchronous verify-all response for a time derived from the per-probe budget and the longest serial account group, not the default 15 second read timeout
