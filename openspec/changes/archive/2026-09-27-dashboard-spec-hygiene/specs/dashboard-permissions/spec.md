## MODIFIED Requirements

### Requirement: Data Operations API
The dashboard SHALL expose data export and wipe endpoints under strict guards.

#### Scenario: Encrypted export
- **WHEN** `POST /api/data/export {scope}` is called with `scope ∈ {all, memory, audit, config}` (`full` accepted as an alias of `all`)
- **THEN** the response is `ApiResponse[ExportResult]` with `signed_url` valid for 60 minutes and `expires_at`
- **AND** the download the signed URL serves is an **encrypted zip** (not plaintext NDJSON), and any UI copy describing it ("encrypted zip") is therefore truthful
- **AND** `audit.append("data.export", note=scope)` is invoked.

#### Scenario: Every export scope yields its real data
- **WHEN** the signed URL for a given `scope` is downloaded
- **THEN** the archive contains the actual data for that scope, never an empty/near-empty file behind a success response:
  - `memory` → the memory module's facts/rules/episodes data
  - `audit` → `public.audit_log`
  - `config` → runtime/config tables (`public.runtime_config`, `public.model_catalog`, `public.permissions`)
  - `all` → the union of every scope above
- **AND** a scope that resolves to zero rows is reported as such (explicit empty marker), but a *known* scope MUST NOT silently map to "no tables" — the export must cover the data the scope name promises.

#### Scenario: Wipe feature disabled
- **WHEN** the `/settings/permissions` page renders
- **THEN** no usable "wipe all data" control is presented to the operator (the panel is removed, or rendered disabled with a "temporarily disabled" note) — the UI MUST NOT offer a button that triggers an irreversible wipe.
- **WHEN** `DELETE /api/data/wipe` is called while the feature is disabled
- **THEN** the endpoint refuses with `503 Service Unavailable` body `{error: "wipe_disabled"}` and performs no destruction, regardless of the phrase supplied
- **AND** no schemas or tables are dropped.
