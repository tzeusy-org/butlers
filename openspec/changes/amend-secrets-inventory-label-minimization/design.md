## Context

See `proposal.md` for motivation. `bu-yk2hb` recorded the owner ruling this draft encodes
verbatim: "Owner chose B: withhold description and category from system/CLI inventory rows while
retaining raw keys as an explicit partial metadata-minimization tradeoff. Routed to bu-y5uq4; no
API/runtime change authorized." This document exists to make that one-line ruling into an exact,
field-by-field contract a reviewer and an implementer can both check the code against, and to be
honest that it is a narrower cut than the `user[]` family's content-blind projection.

The current baseline (`openspec/specs/dashboard-api/spec.md`, requirement `Secrets Inventory and
Per-Credential Read Endpoints`, scenario `Inventory rows are content-blind`) already established
that `system[]` / `cli[]` rows omit every probe message and audit note (owner decision 2026-08-13,
Option C — `bu-iph56`), and left `key`, `category`, `description` unaddressed as "outside this
contract." `bu-yk2hb` is the deferred decision on that exact carve-out; this change only resolves
`category` and `description`, leaving `key` where Option C and `bu-yk2hb` both already left it —
published.

The merged, unarchived `project-secret-read-endpoints-content-blind` change has already established
and implemented the current detail projections: `SystemCredentialDetail` for the system family and
`CliCredentialDetail` for the CLI family. Its baseline adoption is still pending. This draft carries
those clauses in its whole-requirement replacement only to prevent a later archive from restoring
the baseline's obsolete `SystemSecret` / `CliRuntime` payloads; the inventory omission below is the
only new behavior proposed here. The delta retains those two obsolete baseline sentences solely as
explicitly superseded archive provenance for the body-level overwrite guard, never as alternative
payload contracts.

## Goals / Non-Goals

**Goals:**

- State the exact field-level projection: `system[]` / `cli[]` inventory rows keep `key`; they
  drop `category` and `description` as absent fields (not `null`).
- Name the boundary as partial metadata minimization, distinct from the `user[]` family's
  content-blind identity contract, so a future reader cannot mistake one guarantee for the other.
- Leave the per-credential detail endpoints and every other field on the two inventory summaries
  (`state`, `fingerprint`, `last_verified`, `test`, `audit[]`, `read_only`, `used_by[]`, `butler`
  for system; `state`, `fingerprint`, `issued`, `expires`, `last_verified`, `test` for CLI) exactly
  as already specified.
- Specify degraded-source and older-client compatibility behavior explicitly enough that neither
  needs to be inferred at implementation time.

**Non-Goals:**

- Touching `SystemSecret` / `CliRuntime` (the internal, unprojected records) or the mutation
  endpoints that still serialize them directly (`POST /api/secrets/system/<key>` — bu-m9s61,
  tracked separately).
- Touching `GET /api/secrets/system/<key>` or `GET /api/secrets/cli/<id>` (the per-credential
  detail endpoints). The system detail remains `SystemCredentialDetail` with `key`, `category`,
  and `description`; the CLI detail remains `CliCredentialDetail` with `id` and `label` (projected
  from the stored description), fixed capability categories, and no fields named `key`,
  `category`, or `description` and no raw scopes.
- Any handler, runtime, frontend, migration, or deployment change. The original `bu-y5uq4`
  acceptance criterion 7 requires separate owner approval of the exact artifact, now tracked by
  `bu-qj0ekp`, first.
- Redesigning `key` into an opaque identifier, renaming any credential key, or otherwise reaching
  raw credential values.

## Decisions

### Withhold `category` and `description` as absent fields, not nulled fields

`SystemSecretSummary` and `CliRuntimeSummary` stop constructing `category` / `description` at all,
mirroring how `SystemCredentialDetail` already drops `breaks[]` and `CliCredentialDetail` already
drops `last_used` for fields with no authoritative source to publish (established precedent:
`project-secret-read-endpoints-content-blind`'s "fields with no authoritative source are absent
rather than published as always-empty placeholders"). An absent field is unambiguous — a client
cannot mistake it for "this credential genuinely has no description" the way a published `null`
could.

Alternative considered: publish `category: null` / `description: null`. Rejected because a nulled
field is indistinguishable from "no description was ever set," which is a different and weaker
claim than "this response withholds the field." The test evidence (acceptance criterion 4) checks
for the field's absence from the parsed row, not merely a null value, for the same reason.

### Keep `key` published, unchanged

`key` remains exactly as specified today: a raw, operator-chosen string, published unconditionally
on every system/CLI inventory row. This is the deliberate boundary `bu-yk2hb` drew — a fully opaque
system inventory is unadministerable, and `key` is the only field the passport has to let an
operator tell rows apart. This is why the contract is named partial metadata minimization rather
than content-blind identity: the `user[]` family withholds identity-bearing fields entirely (see
the same requirement's `user[]` bullets), while `key` here is deliberately exempt.

Alternative considered: also opaque or hash the `key`. Rejected — out of scope per `bu-y5uq4`
("No ... key renaming, opaque-ID redesign") and would break the passport's administration surface,
which is exactly the failure mode `bu-yk2hb`'s framing warned against.

### Detail endpoints stay on their existing contract

`GET /api/secrets/system/<key>` continues to publish `SystemCredentialDetail`, including system
`key`, `category`, and `description`. `GET /api/secrets/cli/<id>` continues to publish
`CliCredentialDetail`, whose identity fields are `id` and `label`; the fetch layer projects the
stored description column as `label`, and the response has no fields named `key`, `category`, or
`description`. Its capability fields use the fixed vocabulary rather than raw scope identifiers.
These are the implemented clauses from `project-secret-read-endpoints-content-blind`, carried here
as collision preservation while that sibling's baseline adoption remains pending.

The asymmetry — the inventory array withholds two fields that remain available under family-specific
names on a detail read — is deliberate, not an oversight to reconcile. A caller commonly reaches a
detail read after selecting one identified row, while the inventory is the broader listable surface.
That navigation pattern explains the product trade-off; it does not authenticate the caller or add
an authorization boundary. The original `bu-y5uq4` non-goals name the detail endpoints as
unchanged, and the sibling change owns their current field contract.

Alternative considered: withhold the system `category` / `description` or the CLI `label` from the
detail endpoints too, for contract symmetry. Rejected — out of scope for this bead. The selected-row
navigation pattern makes the disclosure surface materially different, but supplies no independent
authentication or authorization guarantee.

### Degraded-source and compatibility behavior

The minimization applies identically whether a companion source in the same inventory response is
degraded (`meta.sources_degraded`) or fully healthy: a row's presence in a partial/degraded
response never widens what fields that row publishes. There is no "fall back to the fuller
projection under degradation" path — a degraded read is a reason to surface fewer or
staler rows, never a reason to publish more per-row detail than a healthy read would.

For compatibility: an older client reading `category` or `description` off a `system[]` / `cli[]`
inventory row now receives no such key in the JSON object (standard Pydantic field-omission
behavior, matching how `SystemCredentialDetail` already drops `breaks[]`), not an error and not a
schema-breaking response shape change elsewhere in the envelope. This is the same compatibility
posture the existing `bu-iph56` probe/audit-note withholding already established for this
endpoint — no version negotiation, no dual-shape response.

## Risks / Trade-offs

- [A future reader could assume this is the same content-blind guarantee as the `user[]` family]
  → Named explicitly as partial metadata minimization in both the spec scenario and this document,
  with `key`'s exemption stated as a deliberate, reasoned carve-out rather than an omission.
- [The inventory/detail asymmetry could look like an inconsistency to fix] → Stated explicitly as
  deliberate in both `proposal.md` and here, with the reasoning (broad-reach list vs. selected-row
  detail) so a future change does not "fix" it by silently widening or narrowing either endpoint.
- [An absent-vs-null field distinction is easy to get wrong in implementation] → The target test
  (`test_inventory_system_and_cli_rows_omit_description_and_category_but_retain_key`) asserts
  `"description" not in row` / `"category" not in row` against the parsed JSON object, not merely
  `row["description"] is None`, so an implementation that nulls instead of omits still fails it.

## Migration Plan

1. Obtain exact owner adoption of this corrected draft (`bu-qj0ekp`) before any
   handler change.
2. Remove `xfail(strict=True)` from
   `test_inventory_system_and_cli_rows_omit_description_and_category_but_retain_key` only as part
   of the implementation change, alongside updating the superseded positive assertions in
   `test_inventory_system_and_cli_rows_omit_every_probe_and_audit_sentinel` (which currently
   asserts `description` and `category` are published) to match the approved contract.
3. Implement by removing `category` / `description` construction from `_content_blind_system` /
   `_content_blind_cli` and the corresponding fields from `SystemSecretSummary` /
   `CliRuntimeSummary` in `src/butlers/api/routers/secrets_v2.py`; update
   `frontend/src/api/types.ts` and any passport row rendering that reads either field off the
   inventory response.
4. Deploy through the normal merge queue after exact-head review. No data migration, backfill, or
   runtime config change is needed — this is a response-projection change only.
5. Before archiving, apply the sibling-ordering rule: archive one same-requirement change, then
   rebuild the other against the refreshed baseline. Do not use this draft to adopt the sibling's
   still-pending detail clauses by implication.
6. Rollback is a code revert of the additive-removal change; no persisted data is touched.

## Open Questions

None. Widening this minimization to `key`, or narrowing/widening the per-credential detail
endpoints' field list, would be a separate owner decision and a separate proposal.
