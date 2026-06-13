## MODIFIED Requirements

### Requirement: Model Catalog Settings API
The dashboard SHALL expose REST endpoints for full CRUD management of the global model catalog with **server-side sort** `(complexity_tier, priority DESC, enabled DESC, alias ASC)`.

#### Scenario: List catalog entries (server-sorted)
- **WHEN** `GET /api/settings/models` is called
- **THEN** all model catalog entries are returned in the canonical sort order `(complexity_tier ASC under tier order [reasoning, workhorse, cheap, specialty, local, legacy], priority DESC, enabled DESC, alias ASC)`
- **AND** each entry includes `id`, `alias`, `runtime_type`, `model_id`, `extra_args`, `complexity_tier`, `enabled`, `priority`, `state`, `last_verified_at`, `last_verified_latency_ms`, `last_verified_ok`, `usage_24h_calls`, `usage_30d_calls`, `spend_7d_usd`, `used_by` (butlers list), `failures_7d`, `created_at`, `updated_at`
- **AND** the frontend MUST NOT re-sort the response; it MAY only filter.

### Requirement: Catalog Priority Stepper API
The dashboard SHALL expose `PUT /api/settings/models/{id}/priority {delta: int}` to adjust a model's priority idempotently.

#### Scenario: Increment priority
- **WHEN** `PUT /api/settings/models/{id}/priority` is called with `{delta: 5}`
- **THEN** the model's `priority` is updated to `max(0, current + 5)`
- **AND** `audit.append("model.priority", target=model_id, note=str(delta))` is invoked
- **AND** the response is the updated catalog entry.

#### Scenario: Priority floor at zero
- **WHEN** a stepper call would push priority below 0
- **THEN** the priority is clamped to 0 (no error).

### Requirement: Catalog Verify-All API
The dashboard SHALL expose `POST /api/settings/models/verify-all` to re-verify every enabled model in parallel.

#### Scenario: Verify-all parallel execution
- **WHEN** `POST /api/settings/models/verify-all` is called
- **THEN** the system issues a 1-token completion against each enabled model concurrently with a bounded concurrency of 8
- **AND** for each model, `last_verified_at`, `last_verified_latency_ms`, and `last_verified_ok` are persisted
- **AND** the call is rate-limited to once per minute system-wide; subsequent calls within the minute return `429 Too Many Requests`
- **AND** `audit.append("models.verify_all")` is invoked once per accepted run.

### Requirement: Catalog Failures Tail API
The dashboard SHALL expose `GET /api/settings/models/{id}/failures?since=24h` returning recent failure entries.

#### Scenario: Failures tail
- **WHEN** `GET /api/settings/models/{id}/failures?since=24h` is called
- **THEN** the response is `PaginatedResponse[FailureEntry]` ordered `ts DESC`
- **AND** each `FailureEntry` includes `ts`, `error_code`, `error_message`, `butler`, `session_id`.

### Requirement: Routing Selection Contract
The runtime SHALL select a model for a butler-requested complexity tier `T` as follows.

#### Scenario: Tier match with multiple candidates
- **WHEN** a butler requests a model in tier `T`
- **THEN** the runtime selects the highest-priority enabled model in `T` whose `state ∈ {verified, untested}`
- **AND** if no such model exists in `T`, the runtime falls through to the next tier in the canonical order `reasoning → workhorse → cheap → specialty → local → legacy`
- **AND** if no tier yields a candidate, the runtime raises a `NoEligibleModel` error.

#### Scenario: Disabled models are skipped
- **WHEN** the runtime selects within a tier
- **THEN** models with `enabled = false` MUST NOT be selected even if their priority is highest.

#### Scenario: Models in error state are skipped
- **WHEN** the runtime selects within a tier
- **THEN** models with `state ∈ {error, offline, deprecated, rate-limited, anomaly}` MUST NOT be selected.

### Requirement: Models Page Dispatch Language
The `/settings/models` page SHALL render the catalog in the Dispatch design language with tier-grouped sections.

#### Scenario: Tier-grouped layout
- **WHEN** a user navigates to `/settings/models`
- **THEN** the catalog is rendered as six tier sections in the canonical order
- **AND** each section contains rule-separated rows for its models
- **AND** each row exposes: model name, role, priority stepper (↑/↓), enable toggle, `Test →`, `Edit →`, `Delete →`
- **AND** filter chips (provider, state) constrain the visible rows but do not re-order them.

#### Scenario: Empty tier
- **WHEN** a tier section has no models
- **THEN** the section renders a single serif-italic line "Nothing in this tier." and no rows
- **AND** the section header remains visible (do not hide the eyebrow).

#### Scenario: Priority stepper round-trip
- **WHEN** a user clicks the up or down stepper on a model row
- **THEN** the page calls `PUT /api/settings/models/{id}/priority` and re-fetches the list
- **AND** in dev, the visible round-trip MUST complete within 200ms.

## Source References
- PLAN.md §2 settled decisions (six-tier catalog, sort contract, routing contract) and §5 `/settings/models` API.
- `pr/overview/settings-refactor/settings-redesign.jsx :: ModelCatalogExpanded` is the visual reference.
- Reuses `audit.append()` from dashboard-audit-log on every mutation.
