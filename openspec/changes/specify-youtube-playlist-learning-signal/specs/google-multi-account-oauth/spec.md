## MODIFIED Requirements

### Requirement: Scope Set Registry

The OAuth start endpoint SHALL accept a `scope_set` query parameter enumerating one or more named scope sets to include in the authorization URL.

#### Scenario: Registered scope sets

- **WHEN** the scope catalog is consulted
- **THEN** it SHALL enumerate named scope sets including at least:
  - `base` — `openid email profile`
  - `calendar` — `https://www.googleapis.com/auth/calendar` and related read variants
  - `drive` — `https://www.googleapis.com/auth/drive.readonly` and related variants
  - `gmail` — existing Gmail scopes already used by `connector-gmail`
  - `health` — `https://www.googleapis.com/auth/googlehealth.sleep.readonly`, `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`, `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
  - `youtube` — `https://www.googleapis.com/auth/youtube.readonly`
- **AND** the `base` set SHALL always be included implicitly

#### Scenario: Single-set request

- **WHEN** `GET /api/oauth/google/start?scope_set=health` is called
- **THEN** the authorization URL SHALL include the `health` set's scopes unioned with any scopes already stored in `granted_scopes` for the hinted account
- **AND** SHALL implicitly include the `base` set

#### Scenario: Multi-set request

- **WHEN** `GET /api/oauth/google/start?scope_set=calendar,drive,health&force_consent=true&account_hint=owner@example.com` is called
- **THEN** the authorization URL SHALL include the union of scopes for all three requested sets (plus `base`)
- **AND** the callback SHALL update `granted_scopes` with the full union after successful consent

#### Scenario: Unknown scope set

- **WHEN** `GET /api/oauth/google/start?scope_set=bogus` is called
- **THEN** the endpoint SHALL return HTTP 400 with `{"error": "unknown_scope_set", "scope_set": "bogus", "known": [...]}`

#### Scenario: Backward compatibility for callers that omit scope_set

- **WHEN** `GET /api/oauth/google/start` is called with no `scope_set` parameter
- **THEN** the endpoint SHALL behave as it does today (existing default scope composition)
- **AND** Google Health scopes SHALL only be included when explicitly requested via `scope_set=health`
- **AND** YouTube scopes SHALL only be included when explicitly requested via `scope_set=youtube`

#### Scenario: Registering the youtube scope set does not authorize requesting it

- **WHEN** the `youtube` scope set is present in the scope catalog
- **THEN** no dashboard route, connector startup path, default scope composition, or OAuth start
  call in the current codebase SHALL request `scope_set=youtube` until a separately-approved
  implementation change adds that call
- **AND** the presence of this catalog entry SHALL NOT be read as owner approval of
  `connector-youtube-learning-signal` (see that capability's `Requirement: Owner Approval Gate`)
