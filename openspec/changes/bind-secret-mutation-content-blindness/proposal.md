## Why

`openspec/specs/dashboard-api/spec.md` states the content-blind contract for the
Secrets passport **reads** — the inventory rows and the per-credential detail
endpoint each carry an explicit "publish the vocabulary, never the content"
requirement. It says nothing about the **mutations**. The probe endpoints and
`POST /api/secrets/user/<provider>/reauthorize` sit outside that text.

The baseline never authorized free text there; it is simply silent. But silence
is what let a real leak survive two reviews: until bu-nz4sn (PR #3759) shipped,
reauthorize put the credential's persisted `entity_info.label` (the account
email) on the wire as `account_hint=<label>`, and the user and system probe
routes returned the persisted failure tail — or the provider's own response
text, or an exception string — as `TestResult.message`. Owner decision Option C
(2026-08-13) forbids both.

The shipped behaviour is now tighter than the contract describes. This change
writes the guarantee down so the next reader does not have to rediscover it from
the code, and so a future edit that widens a mutation response has a requirement
to fail against.

## What Changes

- Add one `dashboard-api` requirement binding the `/api/secrets/*` probe and
  reauthorize mutations to the same content-blind discipline the reads already
  carry.
- Name the two closed vocabularies that are the only permitted evidence:
  `PROBE_FAILURE_VOCABULARY` for probe failure categories, and the existing
  capability vocabulary for per-capability evidence.
- State that reauthorize's `redirect_url` carries `account_ref` — a system-issued
  entity identifier that holds no credential content — rather than a hint read
  out of the credential row, and that the reference is resolved into the
  provider hint server-side.
- State that the withheld free-text diagnostic is still persisted to
  `public.secret_probe_log`, the `last_test_message` cache column, and the audit
  row. Content-blind means withheld from the caller, not destroyed.

No behavioural change. This change documents shipped behaviour
(`src/butlers/api/routers/secrets_v2.py`, `src/butlers/api/routers/oauth.py`,
`tests/api/test_secrets_v2_probe_reauthorize_content_blind.py`).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-api`: add the content-blind contract for the Secrets probe and
  reauthorize mutations, closing the gap between the documented read-side
  contract and the shipped mutation behaviour.

## Impact

Specification only. No source, migration, API shape, or frontend change follows
from this proposal; the behaviour it describes is already merged and covered by
tests. The requirement is `## ADDED` rather than `## MODIFIED` so it cannot
overwrite any clause of the existing `Secrets Mutation Endpoints` or
`Secrets Inventory and Per-Credential Read Endpoints` requirements at archive.
