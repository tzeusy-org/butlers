## Why

`bu-yk2hb` closed the open policy question the current `dashboard-api` spec deliberately left
undecided: should an operator-authored system/CLI credential `description` or `category` be
visible on `GET /api/secrets/inventory`? The owner chose Option B — withhold `description` and
`category` from `system[]` and `cli[]` inventory rows while retaining the raw `key`, because a
label like "Stripe live secret for billing webhooks" is reconnaissance-useful even though the
operator, not a credential provider, authored it. Retaining `key` is the deliberate boundary: a
fully opaque system inventory is unadministerable, and `key` is the only field the passport uses
to let an operator identify which row is which.

This draft proposes the exact spec artifact for that ruling. It amends only the `dashboard-api`
"Secrets Inventory and Per-Credential Read Endpoints" requirement's inventory-row scenario. It
changes no code and authorizes no handler or runtime change; `bu-y5uq4` acceptance criterion 7
requires separate owner approval of this exact artifact before `_content_blind_system` /
`_content_blind_cli` in `src/butlers/api/routers/secrets_v2.py` are touched.

## What Changes

- Amend the `Inventory rows are content-blind` scenario of the `dashboard-api` requirement
  `Secrets Inventory and Per-Credential Read Endpoints`: `system[]` and `cli[]` inventory rows
  (`SystemSecretSummary`, `CliRuntimeSummary`) omit `description` and `category` as fields — not
  as null placeholders — while `key` continues to be published unchanged.
- Name this explicitly as **partial metadata minimization**, not content-blind identity: unlike
  the `user[]` family's projection (which withholds the credential's identity-bearing fields
  entirely), `key` remains a raw, operator-chosen, potentially purpose-revealing string published
  on the wire for exactly the reason `bu-yk2hb` gave — operators need to identify which key is
  which.
- State explicitly that `GET /api/secrets/system/<key>` and `GET /api/secrets/cli/<id>` (the
  per-credential detail endpoints) are unaffected: they continue to publish `key`, `category`, and
  `description` on the existing operator-authored-naming grounds already established for them, and
  this asymmetry between the inventory array and the detail-by-selection reads is deliberate.
- State degraded-source and compatibility behavior: the minimization applies identically whether
  or not the response also reports `meta.sources_degraded`, and an older client that reads
  `category` / `description` off a `system[]` / `cli[]` inventory row now receives an absent field
  and must treat that as normal, not as an error.
- No raw credential value, no key-renaming, no opaque-ID redesign, and no live credential mutation
  is introduced anywhere in this delta.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-api`: amend the `Secrets Inventory and Per-Credential Read Endpoints` requirement's
  inventory content-blindness scenario to withhold `system[]` / `cli[]` `description` and
  `category` while retaining `key`, and to name that boundary as partial metadata minimization.

## Impact

- Affected spec: `dashboard-api` (`openspec/specs/dashboard-api/spec.md`, requirement `Secrets
  Inventory and Per-Credential Read Endpoints`).
- Future bounded implementation (blocked on owner approval of this exact artifact, not part of
  this change): `src/butlers/api/routers/secrets_v2.py` (`SystemSecretSummary`,
  `CliRuntimeSummary`, `_content_blind_system`, `_content_blind_cli`), and
  `frontend/src/api/types.ts` / `frontend/src/api/client.ts` / the passport's system/CLI row
  rendering if either surfaced `description` or `category`.
- Test evidence for the target contract already exists ahead of implementation:
  `tests/api/test_secrets_v2_inventory.py::test_inventory_system_and_cli_rows_omit_description_and_category_but_retain_key`
  plants distinct `description` / `category` sentinels for both families, asserts they are absent
  from the response bytes and from the parsed row while `key` survives, and is marked
  `xfail(strict=True)` against today's handler pending this spec's approval and the matching code
  change (so it will fail loudly, not silently pass, once implemented and the marker is not
  removed).
- Out of scope: `GET /api/secrets/system/<key>`, `GET /api/secrets/cli/<id>` (governed by their
  existing content-blind contract, unchanged here), raw credential value access, key renaming,
  opaque-ID redesign, and deployment.
- Coexistence note: `openspec/changes/project-secret-read-endpoints-content-blind` also carries an
  unarchived `MODIFIED Requirements` block for this same `dashboard-api` requirement (it restates
  the whole requirement to change only the per-credential detail-endpoint field lists, per
  `AGENTS.md`'s two-unarchived-changes-same-requirement hazard). Whichever of the two changes
  archives second must rebuild its block against the refreshed baseline rather than archive its
  stale copy; neither change's content conflicts with the other's edits today.
