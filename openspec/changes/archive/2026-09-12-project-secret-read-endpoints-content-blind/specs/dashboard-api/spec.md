## MODIFIED Requirements

### Requirement: Secrets Inventory and Per-Credential Read Endpoints
The dashboard API SHALL expose a `/api/secrets/*` namespace that backs the passport-book `/secrets` page. All endpoints conform to the `ApiResponse<T>` envelope contract (RFC 0007 §Response Envelope); list/aggregate endpoints embed nested arrays inside `data`, never as top-level fields. Per-credential test evidence SHALL describe the current credential value rather than a prior value retained in shared probe history.

#### Scenario: Inventory endpoint shape
- **WHEN** `GET /api/secrets/inventory?identity=<uuid>` is called
- **THEN** the response is `ApiResponse<{ cli: CliRuntimeSummary[], system: SystemSecretSummary[], user: UserSecretSummary[] }>` with `meta` containing severity counts, aggregate tri-state `failing_count` / `unverified_count` fields, and matching `failing_count_by_family` / `unverified_count_by_family` maps keyed by `cli`, `system`, and `user` (bu-976n0; replaces the prior single `needs_hand_count`, which conflated a genuinely failed/expired/expiring credential with one that was merely set-but-never-probed)
- **AND** `failing_count` counts credentials in a genuinely broken or imminently-expiring state (`expired`, `failing`, `expiring`); `unverified_count` counts credentials in the `warn` state (set, but either never successfully probed or whose prior successful verification is stale) — a `warn` row is an unknown, not a failure, and MUST NOT inflate `failing_count`
- **AND** both counts are computed over a row set deduplicated by conceptual credential (one row per system-secret key / per user provider+identity / per CLI id) — not the raw per-butler-schema row set, so the aggregate matches what the grouped UI displays
- **AND** each family-map entry is computed over the same family-specific deduplicated row set; the passport's per-family KPI captions SHALL consume these maps rather than recomputing failure or unverified counts from adapted rows
- **AND** the `?identity=` query parameter filters the `user` array to credentials associated with the specified entity (projection-lens semantics; see `butler-secrets`)
- **AND** when `?identity=` is omitted, the owner identity is used as the default
- **AND** every credential row includes `state`, `fingerprint` (sha256 first-8 hex, computed on-read, never persisted), and per-family identity (`provider` / `key` / `id`)
- **AND** the response does NOT include any raw secret values

#### Scenario: Inventory rows are content-blind
- **WHEN** `GET /api/secrets/inventory` builds its `user` array
- **THEN** each row is a `UserSecretSummary` carrying only `id`, `entity_id`, `provider`, `state`, `fingerprint`, `issued`, `expires`, `last_verified`, `capabilities_required`, `capabilities_granted`, `test` (most recent probe outcome), `audit[]`, and `capabilities[]` (per-capability probe outcome) — the same content-blind contract the per-credential detail endpoint publishes, one row per credential
- **AND** capability evidence SHALL be published ONLY as members of the fixed vocabulary `calendar`, `gmail`, `drive`, `health`, `connectivity`, `other`, built by filtering that vocabulary against the capabilities a credential's scopes map to, never by filtering the scope or provider strings themselves; an input that maps to no known family SHALL become `other`
- **AND** `provider` SHALL be a member of a fixed published vocabulary (the provider catalogue's slugs plus `email` and `other`); an `entity_info.type` that maps to no known provider SHALL be published as `other` rather than as a prefix of its own spelling
- **AND** the row SHALL NOT contain any raw OAuth scope identifier, the persisted `entity_info.type` or `label`, a probe message, or an audit note — including audit rows written by producers outside the secrets router, because the projection is enforced on read rather than at each writer
- **AND** the projection SHALL be an explicit field-by-field bridge from the router's internal read record, so a new field on that record cannot reach a client without being consciously allowed through
- **AND** the `system[]` and `cli[]` arrays of the same response SHALL likewise omit every probe message (both the cached `last_test_message` column and the probe row's free-text `message`) and every audit note, each row being an explicit field-by-field projection (`SystemSecretSummary` / `CliRuntimeSummary`) of the router's internal read record
- **AND** operator-authored labels on system and CLI rows — `key`, `category`, `description` — are outside this contract and continue to be published; they name infrastructure keys rather than carrying evidence derived from credential content

#### Scenario: Per-credential read endpoints
- **WHEN** `GET /api/secrets/user/<provider>?identity=<uuid>` is called
- **THEN** the response is `ApiResponse<UserSecretDetail>` with the content-blind evidence payload: `id`, `entity_id`, `provider`, `state`, `fingerprint`, `issued`, `expires`, `last_verified`, `capabilities_required`, `capabilities_granted`, `test` (most recent probe outcome), `audit[]` (last 10), and `capabilities[]` (per-capability probe outcome)
- **AND** capability evidence SHALL be published ONLY as members of the fixed vocabulary `calendar`, `gmail`, `drive`, `health`, `connectivity`, `other`; an input that maps to no known family SHALL become `other`, and the projection SHALL be a strict allowlist rather than a filtered passthrough of a persisted or provider-supplied string
- **AND** the payload SHALL NOT contain any raw OAuth scope identifier, the persisted `entity_info.type` or `label`, the failure tail, a probe message, or an audit note — the credential's capabilities are published, never its content
- **AND** the same content-blind payload backs `POST /api/secrets/user/<provider>/rotate`, so no mutation response reintroduces those fields
- **AND** `GET /api/secrets/system/<key>` returns `ApiResponse<SystemCredentialDetail>` with `key`, `category`, `description`, `state`, `fingerprint`, `row_state` (one of `shared` / `local` / `missing`), `source`, `target`, `last_verified`, `used_by[]`, `test` (probe outcome), `audit[]`, `butler`
- **AND** `GET /api/secrets/cli/<id>` returns `ApiResponse<CliCredentialDetail>` with `id`, `label`, `state`, `fingerprint`, `issued`, `expires`, `capabilities_required`, `capabilities_granted`, `test` (probe outcome)
- **AND** both payloads SHALL be explicit field-by-field projections of the router's internal read record, on the same terms as the user payload above: no probe message (cached `last_test_message` or the probe row's free-text `message`), no audit note, and no raw OAuth scope identifier — the CLI payload publishes capability categories from the fixed vocabulary in place of `scopes_required` / `scopes_granted`, which nothing populates today and which a future writer therefore must not be able to leak by populating
- **AND** fields with no authoritative source are absent rather than published as always-empty placeholders: the system payload drops `breaks[]` (never populated, and each entry would carry raw scopes) and the CLI payload drops `last_used`
- **AND** `key`, `category`, and `description` continue to be published on these detail endpoints on exactly the operator-authored-naming grounds this requirement already establishes for the inventory rows above; the CLI payload's `label` is not an independent field but that same `description` column surfaced under the CLI field name (`_fetch_single_cli_secret` builds the record as `label=row["description"]`), so publishing it widens nothing this requirement does not already permit
- **AND** none of these endpoints return raw secret values; values are returned only by explicit mutation endpoints in the specific cases defined below

#### Scenario: User-credential detail refuses to fabricate empty audit history
- **WHEN** `GET /api/secrets/user/<provider>` cannot read `public.audit_log` (missing table or a query failure)
- **THEN** the endpoint SHALL return a sanitized `503` naming only the unavailable source, never an empty `audit[]` presented as a truthful history, and never the underlying database error text
- **AND** `POST /api/secrets/user/<provider>/rotate`, `/disconnect`, `/probe`, and `/reauthorize` SHALL retain their successful mutation semantics while the audit source is unavailable — audit strictness is confined to this evidence read

#### Scenario: Probe-log LRU integration
- **WHEN** any per-credential read endpoint computes the `test` field for a
  credential whose current test-state cache is populated
- **THEN** the field is sourced from the most recent row in `public.secret_probe_log` matching `(credential_scope, credential_key)` ordered by `recorded_at DESC`
- **AND** the `at` field is server-formatted to a human-friendly relative timestamp (e.g. `"14:21 today"`, `"yesterday 09:08"`) before serialization
- **AND** when no probe has ever been recorded for the credential, `test` is `null`

#### Scenario: Credential replacement suppresses prior CLI probe history

- **WHEN** a CLI credential value is replaced and its test-state cache is
  atomically reset
- **THEN** inventory and per-credential reads SHALL return `test: null` until
  the replacement has been probed
- **AND** a retained historical `secret_probe_log` row for the prior value
  SHALL NOT be presented as the replacement credential's last test
