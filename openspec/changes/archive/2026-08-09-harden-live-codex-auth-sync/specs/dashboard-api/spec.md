## MODIFIED Requirements

### Requirement: Secrets Inventory and Per-Credential Read Endpoints

The dashboard API SHALL expose a `/api/secrets/*` namespace that backs the
passport-book `/secrets` page. All endpoints conform to the `ApiResponse<T>`
envelope contract (RFC 0007 §Response Envelope); list/aggregate endpoints
embed nested arrays inside `data`, never as top-level fields. Per-credential
test evidence SHALL describe the current credential value rather than a prior
value retained in shared probe history.

#### Scenario: Probe-log LRU integration

- **WHEN** any per-credential read endpoint computes the `test` field for a
  credential whose current test-state cache is populated
- **THEN** the field is sourced from the most recent row in
  `public.secret_probe_log` matching `(credential_scope, credential_key)`
  ordered by `recorded_at DESC`
- **AND** the `at` field is server-formatted to a human-friendly relative
  timestamp (for example, `"14:21 today"` or `"yesterday 09:08"`) before
  serialization
- **AND** when no probe has ever been recorded for the credential, `test` is
  `null`

#### Scenario: Credential replacement suppresses prior CLI probe history

- **WHEN** a CLI credential value is replaced and its test-state cache is
  atomically reset
- **THEN** inventory and per-credential reads SHALL return `test: null` until
  the replacement has been probed
- **AND** a retained historical `secret_probe_log` row for the prior value
  SHALL NOT be presented as the replacement credential's last test
