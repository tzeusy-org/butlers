## Why

`bu-yk2hb` closed the open policy question the current `dashboard-api` spec deliberately left
undecided: should an operator-authored system/CLI credential `description` or `category` be
visible on `GET /api/secrets/inventory`? The owner chose Option B — withhold `description` and
`category` from `system[]` and `cli[]` inventory rows while retaining the raw `key`, because a
label like "Stripe live secret for billing webhooks" is reconnaissance-useful even though the
operator, not a credential provider, authored it. Retaining `key` is the deliberate boundary: a
fully opaque system inventory is unadministerable, and `key` is the only field the passport uses
to let an operator identify which row is which.

This corrected draft proposes the exact spec artifact for that ruling. It amends only the `dashboard-api`
"Secrets Inventory and Per-Credential Read Endpoints" requirement's inventory-row scenario. It
changes no code and authorizes no handler or runtime change. The original `bu-y5uq4` acceptance
criterion 7 requires separate owner approval of the exact artifact, now tracked against this
corrected draft by `bu-qj0ekp`, before `_content_blind_system` / `_content_blind_cli` in
`src/butlers/api/routers/secrets_v2.py` are touched.

Because an OpenSpec `MODIFIED Requirements` block replaces the whole requirement, the draft also
collision-preserves the detail DTO clauses already implemented under the merged but unarchived
`project-secret-read-endpoints-content-blind` change. Those clauses remain that sibling change's
pending baseline-adoption responsibility; carrying them here prevents this inventory proposal from
silently restoring the older baseline DTOs and is not a second proposal to change either detail
endpoint. The two obsolete baseline DTO sentences are repeated in the delta only as explicitly
superseded archive provenance so the overwrite guard can prove they were considered; the sentences
are not response-shape requirements and do not compete with the implemented detail DTO clauses.

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
- State explicitly that the per-credential detail endpoints are unaffected, with their families
  described precisely: `GET /api/secrets/system/<key>` continues to publish system `key`,
  `category`, and `description`; `GET /api/secrets/cli/<id>` continues to publish CLI `id` and
  `label` (the stored description projected under the CLI field name), not fields named `key`,
  `category`, or `description`, and not raw scopes. The detail-by-selection navigation pattern is
  rationale for the deliberate inventory/detail asymmetry, not an authentication boundary.
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
- Future bounded implementation (`bu-qj0ekp`, blocked on owner approval of this corrected exact
  artifact, not part of this change): `src/butlers/api/routers/secrets_v2.py` (`SystemSecretSummary`,
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
- Existing detail evidence remains unchanged in
  `tests/api/test_secrets_v2_per_credential.py`: `SystemCredentialDetail` publishes system
  `key`/`category`/`description`, while `CliCredentialDetail` publishes `id`/`label` and fixed
  capability categories without raw scopes, `last_used`, or fields named `key`, `category`, or
  `description`.
- Out of scope: changing `GET /api/secrets/system/<key>` or `GET /api/secrets/cli/<id>`, raw
  credential value access, key renaming, opaque-ID redesign, and deployment.
- Coexistence note: `openspec/changes/project-secret-read-endpoints-content-blind` also carries an
  unarchived `MODIFIED Requirements` block for this same `dashboard-api` requirement (it restates
  the whole requirement to change only the per-credential detail-endpoint field lists, per
  `AGENTS.md`'s two-unarchived-changes-same-requirement hazard). Whichever of the two changes
  archives second must rebuild its block against the refreshed baseline rather than archive its
  stale copy. The sibling's implemented detail DTO clauses are pending until that sibling is
  archived; this draft carries them solely as whole-requirement collision preservation. Before
  either change is archived, the second block MUST be rebuilt against the refreshed baseline.
