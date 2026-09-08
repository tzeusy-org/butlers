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
- **AND** the `system[]` and `cli[]` arrays SHALL publish `key` unconditionally, as a raw, operator-chosen string, so an operator can identify which credential a row names; withholding it would make the inventory unadministerable, and `bu-yk2hb`'s owner ruling deliberately preserved it
- **AND** the `system[]` and `cli[]` arrays SHALL NOT publish `category` or `description`: `SystemSecretSummary` and `CliRuntimeSummary` SHALL omit both fields entirely (absent from the serialized object, never published as `null`) — owner decision 2026-08-13/2026-09-02 (`bu-iph56` Option C; `bu-yk2hb` Option B), because an operator-authored label such as "Stripe live secret for billing webhooks" can itself be reconnaissance-useful even though the operator, not a credential provider, authored it
- **AND** this is **partial metadata minimization, not content-blind identity**: unlike the `user[]` family's projection above, which withholds every identity-bearing field, the `system[]` / `cli[]` rows keep one raw, purpose-revealing field (`key`) published on the wire by deliberate design; only `category` and `description` are withheld
- **AND** `GET /api/secrets/system/<key>` and `GET /api/secrets/cli/<id>` (the per-credential detail endpoints, defined in the Per-credential read endpoints scenario below) are unaffected by this scenario and continue to publish `key`, `category`, and `description` on the operator-authored-naming grounds that scenario already states; the resulting asymmetry between the inventory array and the detail-by-selection read is deliberate — a detail read is reached only after a caller has already selected one identified row — and MUST NOT be reconciled by widening or narrowing either endpoint without a separate proposal
- **AND** this minimization applies identically to a response that also reports `meta.sources_degraded`: a `system[]` / `cli[]` row surfaced despite a degraded companion source still omits `category` and `description`; degradation is never a reason to publish more per-row detail than a healthy response would
- **AND** an older client reading `category` or `description` off a `system[]` / `cli[]` inventory row now receives no such key in the row object; it SHALL treat that absence as normal rather than as an error, and no other field on these two summaries changes shape or meaning as a result of this scenario
- **AND** no raw secret value is published by this scenario in any case

#### Scenario: Per-credential read endpoints
- **WHEN** `GET /api/secrets/user/<provider>?identity=<uuid>` is called
- **THEN** the response is `ApiResponse<UserSecretDetail>` with the content-blind evidence payload: `id`, `entity_id`, `provider`, `state`, `fingerprint`, `issued`, `expires`, `last_verified`, `capabilities_required`, `capabilities_granted`, `test` (most recent probe outcome), `audit[]` (last 10), and `capabilities[]` (per-capability probe outcome)
- **AND** capability evidence SHALL be published ONLY as members of the fixed vocabulary `calendar`, `gmail`, `drive`, `health`, `connectivity`, `other`; an input that maps to no known family SHALL become `other`, and the projection SHALL be a strict allowlist rather than a filtered passthrough of a persisted or provider-supplied string
- **AND** the payload SHALL NOT contain any raw OAuth scope identifier, the persisted `entity_info.type` or `label`, the failure tail, a probe message, or an audit note — the credential's capabilities are published, never its content
- **AND** the same content-blind payload backs `POST /api/secrets/user/<provider>/rotate`, so no mutation response reintroduces those fields
- **AND** `GET /api/secrets/system/<key>` returns `ApiResponse<SystemSecret>` with `key`, `category`, `row_state` (one of `shared` / `local` / `missing`), `fingerprint`, `description`, `source`, `target`, `last_verified`, `used_by[]`, `breaks[]`, `test`, `audit[]`
- **AND** `GET /api/secrets/cli/<id>` returns `ApiResponse<CliRuntime>` with `id`, `label`, `fingerprint`, `state`, `issued`, `expires`, `last_used`, `scopes_required`, `scopes_granted`, `test`
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
