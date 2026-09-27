# Project system and CLI credential detail reads content-blind

## Why

`GET /api/secrets/system/<key>` and `GET /api/secrets/cli/<id>` were the two
per-credential read endpoints that never received the content-blind projection
treatment the user-credential endpoint got. They serialised the router's internal
read record more or less directly, so the wire shape was whatever the record
happened to hold: the cached `last_test_message`, the probe row's free-text
`message`, the audit row's operator-authored `note`, the system `breaks[]`
entries (each carrying a `feature` label plus raw `required_scopes`), and the CLI
token's raw `scopes_required` / `scopes_granted`.

Nothing populates the scope lists, the audit list, or `breaks[]` on these two
routes today, so there is no live leak to point at. That is precisely the problem
worth fixing rather than a reason to defer: the endpoints are one `SELECT` column
away from publishing raw scopes and provider diagnostics, and no test would have
noticed. `Secrets Inventory and Per-Credential Read Endpoints` already binds the
user endpoint to a strict allowlist; these two were simply never brought under it.

## What Changes

- `GET /api/secrets/system/<key>` publishes a new `SystemCredentialDetail`, and
  `GET /api/secrets/cli/<id>` a new `CliCredentialDetail`. Both are built
  field-by-field from the internal record, so a column added to the query cannot
  reach a client without being consciously allowed through.
- The CLI payload publishes `capabilities_required` / `capabilities_granted` —
  members of the fixed capability vocabulary — in place of the raw scope lists.
- Probe messages, audit notes, `breaks[]`, and the always-null `last_used` leave
  the payloads. Withheld diagnostics keep being written server-side; content
  blindness is about the wire, not about destroying operator forensics.
- `key`, `category`, `description`, and the CLI `label` continue to be published,
  unchanged. This is not a new carve-out. The requirement already permits
  publishing `key` / `category` / `description` on system and CLI rows, and the
  CLI `label` is not a fourth field: `_fetch_single_cli_secret` builds the record
  as `label=row["description"]`, so it is that same permitted column under a
  different field name. Dropping any of them here alone would split the
  list/detail contract without closing a leak.

The internal records keep their existing shape because the system mutation route
still serialises `SystemSecretDetail` directly. Reshaping it to fix a read
endpoint would have silently changed what a `POST` emits.

## Impact

- Affected specs: `dashboard-api`
- Affected code: `src/butlers/api/routers/secrets_v2.py`,
  `tests/api/test_secrets_v2_per_credential.py`, `frontend/src/api/types.ts`,
  `frontend/src/api/client.ts`
- Breaking for any client reading `scopes_required` / `scopes_granted`,
  `last_used`, or `breaks[]` off these two endpoints. The frontend types and
  client are updated in the same change; no other consumer exists in-repo.
- `POST /api/secrets/system/<key>` still returns the unprojected record and is
  the one remaining gap on this surface (bu-m9s61), as is the standalone audit
  history endpoint (bu-rh8z5). Neither is in scope here.
