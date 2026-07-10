## Why

The `contacts-identity` and `module-contacts` specs still carry per-requirement
scenarios written against the `public.contacts` and `public.contact_info` tables
as if those tables were authoritative. They are not. Both tables were dropped:
`public.contact_info` by `core_115_drop_contact_info` and `public.contacts` by
`core_134_drop_public_contacts`. The guardrail contract
`tests/contracts/test_contacts_schema_retired.py` fails RED the moment any live
SQL reads or writes either table. Identity resolution and outbound notify were
re-pointed onto the entity graph: non-secret channel identifiers live in
`relationship.entity_facts` (keyed by `entity_id`), `public.entities` holds the
identity anchor and roles, and `public.entity_info` is a secrets-only store
(RFC 0004 Amendment 3). `src/butlers/identity.py` performs entity-graph
resolution and always returns `contact_id = None`.

Prior work (PR #2698, bead bu-2vxk1e) added reality-sync banners to both specs
flagging that the table-centric material is superseded, but the underlying
requirements and their scenarios still stand in the canonical specs as if they
described a live contract. The owner decision on bead bu-qtsy4 (2026-07-02) is
explicit: **archive the table-centric requirements, do not rewrite them.**
Because the tables are already dropped, rewriting every scenario into an
entity-graph equivalent is not warranted. The authoritative contracts for the
live model already exist in the `entity-identity` and `relationship-facts`
specs, so the archived requirements point there rather than duplicating them.

## What Changes

- **`contacts-identity`: remove 16 table-centric requirements.** Every
  requirement whose contract is the existence, shape, constraints, or
  behavior of the retired `public.contacts` / `public.contact_info` tables is
  removed via a `## REMOVED Requirements` delta, each with a `Reason` and a
  `Migration` note pointing at the authoritative `entity-identity` /
  `relationship-facts` contract or the live resolver.

- **`contacts-identity`: retain three non-table requirements.** "I/O model
  removal" (module tool-naming contract, not table-centric), "Secret key
  renames" (owner-identity secret-key rename in `butler_secrets`, not
  table-centric), and "[TARGET-STATE] Contact search endpoint for typeahead"
  (the live, shipped entity-graph `GET /api/contacts/search`, wired by PRs
  #3020 / #3025 and implemented in `src/butlers/api/routers/contacts.py`) are
  not superseded and stay in the spec.

- **`contacts-identity`: fix pre-existing strict-validation drift.** The
  canonical spec opens with a "## Current Reality" prose block and has no
  "## Purpose" section, so it fails `openspec validate --strict` today. This
  change adds a proper "## Purpose" and tightens the reality note so the spec
  validates and the banner matches the post-archival contents.

- **`module-contacts`: remove one table-centric requirement.** "Public Schema
  Tables" exists solely to assert that the backfill writes contact channel data
  to `public.contact_info`; that write path is retired (writes now land on
  `relationship.entity_facts`). It is removed with a `Migration` note. The
  module's sync mechanics (providers, canonical model, runtime, MCP tools,
  backfill lifecycle) remain live and are left in place; their stale table names
  in scenarios are already superseded by the module's reality-sync banner, and
  rewriting them is out of scope per the owner decision.

- **`module-contacts`: fix pre-existing strict-validation drift.** Thirteen of
  the fourteen requirement statements stated their contract in descriptive prose
  without a SHALL/MUST keyword, so the spec fails `openspec validate --strict`
  today (independent of this change) and blocks archival of the retired
  requirement. This change adds the keyword to each affected statement with no
  change in meaning. (The fourteenth, "Public Schema Tables", is removed by this
  change rather than fixed.)

## Impact

- Specs only. No code, no migrations, no schema changes.
- `openspec/specs/contacts-identity/spec.md`: 16 requirements removed, "##
  Purpose" added, reality note tightened.
- `openspec/specs/module-contacts/spec.md`: 1 requirement removed.
- No live code depends on the removed contracts (the tables are already dropped
  and the retirement test guards against reintroduction), so there is no
  runtime or test impact.

## Out of Scope

- **Rewriting the module-contacts backfill scenarios** into entity-graph form.
  The owner decision is archive, not rewrite; the reality-sync banner already
  reframes the stale table references.
- **Editing `entity-identity` or `relationship-facts`.** They are the
  authoritative references and are not modified here.
- **Relocating or retiring the "I/O model removal" requirement.** It is a
  tool-naming contract mislodged in this spec but is not table-centric; a
  follow-up should decide its proper home.
