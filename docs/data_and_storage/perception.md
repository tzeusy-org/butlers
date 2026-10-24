# Dark perception representation

RFC 0038 S3/S3.1/S3.2 and the `perception-registry` requirements govern this
representation. It is usable by shadow consumers. It does not activate a
producer, model tool, API router, preamble, scheduler, condition evaluator or
notification reader. The existing context bus, DND guard, concern ledger and
UI timeline saved views remain separate and unchanged.

`butlers.perception.EventRequest` freezes canonical request bytes.
`PerceptionStore` uses an already authenticated connection; it neither selects
roles nor commits a caller's transaction. `write` returns a stable assertion and
optional retraction receipt, or a CAS conflict with the current generation.
`read` returns four-state knowledge, original evidence/source/age, freshness,
labels and a reason. `timeline` includes immutable confirmation, late and target
metadata. A retraction outside a query page/window still marks its original
assertion. Live transition reads skip late/retracted assertions, records and
assertions older than ten minutes; they cannot undo effects.

The registry has eight closed kinds and eight mode IDs. `owner.mode` reads the
single versioned `owner_modes` projection; it is never copied into a situation
slot. Conditions and suppression instances are finite, immutable, source-bound
representations. `bind_condition` registers an **open dark record**, with no
subscriber, evaluation, fire operation or executable authority. A suppression
must reference an existing registered suppression authority; it does not mutate
DND. A later evaluator must validate the current goal/grant binding before
activating any recorded condition. Storage of an opaque goal reference is not
a grant or proof that the goal exists.

## Authentication and bootstrap

`core_276` invokes the fixed privileged installer staged by `scripts/init-db.sql`.
The ordinary migration login cannot own the tables/functions, grant service
membership or bootstrap itself. The private owner is membership-free,
`NOLOGIN NOINHERIT NOBYPASSRLS`. All representation/configuration tables use
forced owner-only RLS. The public gateways are fixed definers with exactly
`pg_catalog,pg_temp` search paths. They derive the authenticated actor from the
actual active PostgreSQL role (or connecting role), never a request's writer
string or the definer's `current_user`.

Dedicated `perception_*_writer` identities bind Home Dots, the context switch,
health, confirmed expectations, sensing and canonical attention projections.
`perception_owner_reader` is the private shadow reader;
`perception_availability_reader` returns only unknown/optional busy-until.
Ordinary domain, connector, dashboard and model roles have neither direct
storage access nor permission to assume these identities. This change does not
provision credentials or memberships, or turn a domain daemon into a trusted
reducer. Actual service authentication/provisioning is a separate activation.

The privileged `perception_private.sources` profile binds the fixed source,
writer and kind, permitted subject identities, evidence source set and a finite
positive freshness maximum. `source_refs` resolves opaque evidence, named place,
locality, principal and evidence references. Expectation enums are declared by the source
profile; suppression references have a real foreign key to canonical DND mutation
receipts, without granting perception direct DND reads or writes. These
are configuration custody, with no registration tool or runtime DML grant.
Profiles start **unconfigured**; missing profiles/evidence refuse mutation.
Producer owners must verify original source expiry and the existing context
signal's maximum before configuring a profile. Conversion never renews an old
TTL or promotes an unsafe alias to a mode or direct writer. The twelve legacy
signals retain their original receipts/individual expiry; unsafe mappings remain
unknown and block that consumer's activation.

Before label activation, accepted principal references remain resolved and all
writes conservatively join `world` provenance and all six sensitivity categories.
A typed shape or reference never launders D6 exposure. The held typed-value and
cited-label choices remain held. The reader does not accept a caller Boolean to
unlock private detail; the exposed reader cannot query private history.

Bootstrap replays broad public grants first, then the perception section
reinstalls fixed functions and restores restrictive RLS/ACLs. No data is copied
or deleted. Read-only bootstrap admission includes this managed surface; it is
not an installer. Run bootstrap under the distinct existing privileged identity
before online migrations. The installer refuses adoption of tables owned by an
ordinary identity or a differently owned private schema.

## Implementation Notes

- A single transaction-scoped serializer covers nullable slot identity, original
  receipt lookup, CAS and optional retraction. PostgreSQL also enforces unique
  `(kind,subject)` with nulls not distinct and unique source/event keys/targets.
- Event UUIDv5 uses RFC 0038's fixed namespace and canonical UTF-8 JSON name.
  Request bytes, source evidence, labels and confirmation participate in the
  replay digest. A retry returns original receipts even after later generations;
  changed bytes refuse. A caller-owned mutable dictionary cannot change a
  frozen `EventRequest`.
- Older observations append explicitly historical records without rolling the
  slot back. `late` is derived from immutable `confirmed_at-at > 15 minutes`.
  Exactly fifteen minutes is not late. No retry regenerates either timestamp.
- Earned Home re-entry within ten minutes creates one enter assertion and one
  bound auxiliary `record/retract`, with union labels and two stable receipts,
  in the same slot CAS. The original leave is never updated/deleted. Insert
  failure rolls the complete transaction back.
- Timeline queries use one consistent statement/snapshot for the page and its
  target relation. History retains original records and links. There is no
  second observation, clock, life-record or retraction ingestion API.
- Empty logical downgrade retains additive shared objects for other core schemas.
  A nonempty slot/history/condition/mode refuses downgrade; remediation is
  forward and history-preserving. Re-upgrade is idempotent.
- Representation landing does not prove consumer parity. Dots `.17` alone owns
  place hysteresis; `.21` owns clock/activity/mode projection and all live reader
  switches; `.14` owns dependency health and the confirmed routine feeder owns
  expectations. None is enabled by these tables. Place history's 400-day policy
  does not extend raw-coordinate retention; no raw coordinate is accepted here.
