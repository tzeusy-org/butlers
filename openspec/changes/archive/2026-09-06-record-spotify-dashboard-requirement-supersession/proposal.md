## Why

The archived `connector-spotify` change declared the requirement names
`Spotify OAuth 2.0 PKCE Authorization Flow` and `Connection Status Card`.
Both names were superseded in the canonical `dashboard-spotify-setup` spec
by PR #3738, commit `3239b332f38831da342ecf215ad3dea52809fdec` (`docs:
reconcile Spotify OAuth authority`). The historical ratchet still records
the two predecessor names, so it needs an explicit provenance record before
those two frozen findings can be retired.

## What Changes

- Record the exact predecessor-to-successor mappings for the two requirements
  under `dashboard-spotify-setup`.
- Preserve the connector-owned Spotify OAuth authority and content-blind
  Passport projection already established by PR #3738.

The archived requirement bodies must not be restored. Their settings-page,
profile-content, raw-error, and credential-storage clauses are obsolete under
the approved connector-owned OAuth and content-blind projection contracts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This change records historical provenance only; the live capability
specification already contains the approved successor requirements and
behaviors.

## Impact

Only the OpenSpec historical archive and the two corresponding frozen
archived-requirement findings are affected. There is no API, frontend,
credential, runtime, data, migration, or test change.
