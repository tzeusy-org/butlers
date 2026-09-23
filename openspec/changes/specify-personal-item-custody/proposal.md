## Why

General can save and retrieve a collection item, but a freeform note does not
say whether an object is still where it was last reported or whether a
particular borrowing episode ended. The owner should be able to ask where an
identified adapter was last put and see the report and date behind the answer.
Two identical adapters must remain two objects.

The released planning gate `bu-04i7om` authorized this specification packet,
not the behavior. `bu-2jtfw.9` separately owns General capture, collection
vocabulary, search, and ownership refusal. This change does not edit that
worker's General baseline spec or manifesto.

## What Changes

- Propose a `personal-item-custody` capability for existing
  `general.collection_items.id` objects: source-qualified last reports and an
  opt-in, versioned owner-reported custody history inside the item.
- Propose `possession_locate` and `possession_record` as narrow General tools.
  Recording requires the selected item UUID, operation identity, expected
  revision, and an immutable server-held receipt for an owner-confirmed
  statement. A caller-supplied source reference is not owner authority.
- Define move, borrow, lend, return, retire, and superseding correction without
  claiming that Butlers moved an object, obtained a receipt, or performed a
  physical return.
- Fence every generic update and delete path that can reach an opted-in item,
  including collection cascade deletion and stale whole-document writes.
- Propose bounded General manifesto and Switchboard routing language in
  `general-manifesto-and-routing-amendment.md`; it is not applied here.

## Capabilities

### New Capabilities

- `personal-item-custody`: source-qualified locate, typed custody episodes,
  atomic replay-safe history, privacy, and specialist authority boundaries.

### Modified Capabilities

None in this package. The canonical `butler-general` tool inventory and
General manifesto require a separately coordinated amendment after
`bu-2jtfw.9` settles. This change must be reviewed again against that result
before implementation.

## Non-Goals

No asset database, inventory connector, ownership inference from receipts,
valuation, insurance or legal claim, consumable stock, warranty window,
financial loan, borrower outreach, reminder, physical work, device actuation,
provider call, or new dashboard page. No new schema namespace or public entity
graph publication of private object locations.

## Authority and Impact

General owns the remembered personal-item report. Finance owns transaction and
receipt truth; optional receipt references are resolved only through
Switchboard-brokered Finance MCP under separately reviewed read authority.
Switchboard must also attest owner-statement provenance through a narrow
server-held confirmation and read-only validation seam before a typed General
transition can be enabled. The LLM may propose fields but cannot mint that
attestation.
Relationship owns canonical person identity, and Home owns connected-device
automation while refusing physical installation and maintenance. A freeform
name never grants any of those authorities.

The initial consumer is an owner conversation and the existing General item
read surface. Future work may add General-local tools and protected profile
data in `collection_items.data`; this PR changes no runtime code, schema,
manifesto, routing rule, live data, credentials, or provider integration.
Exact spec and manifesto adoption, implementation allocation, independent
review, and any later landing are separate gates.

Tests: +0 ~0 -0.
