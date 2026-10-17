## Why

Vision rule 3 prohibits private inter-butler schema access, but RFC 0004
Amendment 3 and security.md currently sanction core identity reads of
`relationship.entity_facts`. The owner's 2026-10-14 `identity-direct-read`
choice D authorizes a source-owned public channel projection. This change
provides the complete RFC/doctrine/spec candidate for independent review and
owner adoption. It does not claim the public projection exists today.

## What Changes

- Propose RFC 0004 Amendment 4: exact projected columns, eligible population,
  source-owned same-transaction maintenance, deletion/failure/replay behavior,
  named-role ACL and forced-policy/bootstrap posture, bounded backfill and
  fail-closed public-only identity reads.
- Align security.md's Identity Resolution section and vision.md's rule 3
  with that public boundary while preserving the two named private-schema
  exceptions and the credential seam.
- Add five entity-identity requirements and modify the two existing
  Switchboard/Relationship direct-read requirements, preserving all seven
  existing scenario bodies and all other baseline/active requirements.
- Preserve normalized/bulk ambiguity, source candidate/adoption authority,
  owner-routing safety, unknown-sender notification, context/recipient
  behavior, and RFC 0031's separate graph projection.

## Capabilities

### New Capabilities

None. Channel projection is a change to existing identity capabilities.

### Modified Capabilities

- `entity-identity`: bounded derived channels, mandatory atomic maintenance,
  public-only reader equivalence, least-privilege reads and verified recovery.
- `switchboard-identity`: inbound source reads become public-only; unresolved
  routing and existing startup/owner-delivery semantics remain mandatory.
- `relationship-facts`: source-owned triples remain canonical while core
  resolves their derived rows, never private SQL or a private definer bridge.

## Impact

Only RFC 0004, security.md, vision.md and this native changeset are authored.
No runtime/product, migration, grant, policy, fixture, test or deployment
implementation is included. Tests: +0 ~0 -0.

Future implementation `bu-ys9hdx.4` requires adoption and its own actual
source/role/migration proof. Foreign-SQL fence `bu-ys9hdx.5` remains downstream
of implementation and retains its named sanctioned classes. Live backfill,
restore, privileges and production require their separate environment gates.
The held run-18 gate is unchanged.

## Adoption Boundary

This is an unadopted candidate. Independent review and the owner's merge are
required by `bu-ys9hdx.3`; publishing this PR supplies neither result. The
review covers grant scope, source atomicity, existing guarantees and the
complete source/implementation boundary. No duplicate approval of choice D
is requested, and no doctrine is adopted by the author.
