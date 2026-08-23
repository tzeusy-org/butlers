# Bind the system-credential write response to the content-blind projection

## Why

`POST /api/secrets/system/<key>` re-reads the row it just wrote and hands the
caller the router's internal `SystemSecretDetail` verbatim. That record is the
unprojected shape: `description`, `test` with the probe's free-text `message`,
`audit` as a bare `list[dict]` including each row's operator-authored `note`,
and `breaks` as a bare `list[dict]` whose entries carry a free-text feature
label plus raw OAuth scopes.

`GET /api/secrets/system/<key>` publishes `SystemCredentialDetail` instead
(`project-secret-read-endpoints-content-blind`), and the user rotate mutation
already reuses the user read projection. The system write route was the last
`/api/secrets/*` handler still serialising a detail record unprojected — the
one gap that proposal named and deliberately left out of its scope, because
reshaping the internal record to fix a read endpoint would have silently
changed what this `POST` emits.

Nothing populates `audit` or `breaks` on this path today, so there is no live
leak to point at. That is the reason to close it now rather than a reason to
defer: a bare `list[dict]` is not an allowlist, and an extra column added to
the re-read query would ride straight onto the wire with no test noticing.

## What Changes

- `POST /api/secrets/system/<key>` publishes `SystemCredentialDetail` through
  the existing `_content_blind_system_detail` projector, at all three write
  branches (`shared`, `shared-public`, per-butler override). The override
  branch keeps its `row_state="local"` / `target=<butler>` marking, applied
  before the projection.
- The probe message, the cached `last_test_message`, every audit `note`, and
  `breaks[]` leave the response. Audit rows publish as `CredentialAuditOutcome`
  (`ts` / `actor` / `action`). Withheld text keeps being persisted and audited;
  content blindness is about the wire.
- `key`, `category`, and `description` continue to be published, unchanged from
  the read route. This is not a new carve-out: `Secrets Inventory and
  Per-Credential Read Endpoints` already establishes them as operator-authored
  naming for infrastructure keys rather than evidence derived from credential
  content. Dropping them here alone would split one row's contract across two
  endpoints without closing a leak.
- `SystemSecretDetail` keeps its shape and becomes fully internal: the probe
  and delete routes still read it to decide what they are acting on, and
  `_content_blind_system_detail` is now the only path from it to a client.
- Frontend: `setSystemCredential` returns `SecretsSystemCredentialDetail`; the
  now-unused `SecretsSystemDetail` type is removed. No component reads the
  response — the mutation hook discards it and invalidates the queries.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-api`: the `Secrets Mutation Endpoints` requirement's system
  scenario now names the published payload and binds it to the read route's
  content-blind projection.

## Impact

- Affected specs: `dashboard-api`
- Affected code: `src/butlers/api/routers/secrets_v2.py`,
  `tests/api/test_secrets_v2_system_mutations.py`, `frontend/src/api/types.ts`,
  `frontend/src/api/client.ts`
- Breaking for any client reading `breaks[]`, `test.message`, or an audit
  `note` off this response. No such consumer exists in-repo; the frontend types
  and client are updated in the same change.
- The `s:` write surface is now projected end to end. Not addressed here: the
  frontend's `SecretsUserDetail` type still describes the pre-projection user
  payload (`scopes_required`, `failure_tail`, `breaks`) that the backend
  stopped returning, which is stale typing rather than a leak.
