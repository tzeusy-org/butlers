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
DND. A later evaluator (the dark [`butlers.conditions`](conditions.md) seam) must validate the
current goal/grant binding before activating any recorded condition. Storage of an opaque goal reference is not
a grant or proof that the goal exists.

## Authentication and bootstrap

`core_322` invokes the fixed privileged installer staged by `scripts/init-db.sql`.
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
storage access nor permission to assume these identities. Service roles start NOLOGIN with no memberships. A later explicitly authorized
host provisioning may enable direct service login without changing this API;
bootstrap continues to reject memberships and ordinary-runtime inheritance.
This change provisions no credentials/memberships and turns no domain daemon
into a trusted reducer. Actual service authentication/provisioning is a separate activation.
The first such activation is `perception_health_writer` (capability health): host provisioning gives
that one role `LOGIN` and the dashboard-api job connects as it from `BUTLERS_HEALTH_WRITER_DSN`
(see `docs/runtime/capability-health.md`). Bootstrap still refuses `LOGIN` on `perception_owner`.

The privileged `perception_private.sources` profile binds the fixed source,
writer and kind, permitted subject identities, evidence source set and a finite
positive freshness maximum. `source_refs` resolves opaque evidence, named place,
locality, principal and evidence references. Expectation enums are declared by the source
profile; suppression references have a real foreign key to canonical DND mutation
receipts, without granting perception direct DND reads or writes. These
are configuration custody, with no registration tool or runtime DML grant.
The SQL read boundary resolves legacy aliases to that same canonical row. A
nullable, bounded `source_refs.read_alias` names only registered dependencies;
`system.dep.<name-or-UUID>` must resolve uniquely to a configured dependency
subject. `expect.<registered-kind>[UUID]` requires the configured routine subject
and matching source enum. Conflicting, unknown, unregistered or mismatched
subjects refuse; dynamic aliases are private-reader only. Static aliases retain
the canonical exposed-reader restriction. None is accepted by the writer.
`system.dep` is capability health (RFC 0038 S6): its value is `{state, cause,
observed_at}`. `state` is one of `available`, `degraded`, `blind`, `down`, `held`,
`unconfigured`; `cause` is `null` for `available` and otherwise one of the closed
`oauth_revoked`, `credential_missing`, `provider_unavailable`, `domain_unreachable`,
`signal_unmeasurable`, `policy_held`, `not_configured`, `unknown`; `observed_at` is
when the underlying signal was last measured, so source age is `now - observed_at`
while the slot's own `observed_at` is the publish time. A bare state string is
refused by both the Python validator and SQL `valid_value`. `.14` owns the only
producer, so the `health.dependencies` profile is the one exception to "starts
unconfigured": the installer gives it a 900-second freshness maximum and one subject
(`dependency_subject`) and one evidence reference per dependency declared in
`butlers.core.capability_dependencies`, with `read_alias` equal to the dependency ID
(`system.dep.google`). It does this only while the profile is unconfigured, so an
operator-configured profile is never overwritten, and the installer refuses to run
over any `system.dep` row still holding the old string. Adding a dependency ID needs
its own migration to register its subject.

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

## Typed situation reads (dark, `.21`)

`butlers.perception.situation` is the consumer surface for `situation.read` and
`timeline.query`. It is dark: `perception.flags.SITUATION_READS_LIVE` is off,
`situation_capabilities()` offers nothing, and no runtime role is granted a
reader role. At activation, the runtime serving an owner-authenticated read needs
`perception_owner_reader`; the runtime serving an exposed or third-party goal
needs `perception_availability_reader`. That grant lands with the switch.

- `situation_read(connection, audience=..., kind, subject, as_of)` first checks
  that the connection's `current_user` is the audience's reader role, so an owner
  connection can never serve an exposed caller.
- The owner read returns one of the four states, with source, observed/fresh
  instants, age, labels, generation and availability reason. A value is carried
  exactly when the state is `known`; a value past its freshness reads `unknown`
  with `stale_last_value`. Any malformed row fails closed.
- The availability read is a fixed three-field projection: `state` (always
  `unknown`), `busy_until` and `availability_reason` (`detail_restricted`). It
  copies nothing else from the database, whatever the row holds.
- `timeline_query` is owner-only and refuses another audience before any database
  call. It returns typed entries with derived `retracted` and `late`, without the
  writer, digest or event key, and defaults to `live=True`, which skips late and
  retracted assertions.

## The /now card (dark, `.21`)

`butlers.perception.now` renders the owner's `/now` card from owner reads of
`owner.place`, `owner.activity`, `owner.mode`, `owner.clock` and
`owner.suppression`, with no model. `render_now` is pure and `render_text` gives
the plain-text form a chat reply would use. The dashboard serves it at
`GET /api/now` (owner-authenticated), which answers 404 while
`perception.flags.NOW_LIVE` is off.

- Every state renders. A missing, malformed or unrenderable read is "Unknown
  (unreadable)"; a known read past `fresh_until` renders as stale. The card is
  `all_current` only when every signal is known and fresh.
- Place is coarse: "At a known place" or "Elsewhere". No place, locality,
  evidence or record reference reaches the card.
- When live, the route reads on a fresh `BUTLERS_PERCEPTION_READER_DSN`
  connection and switches to `perception_owner_reader` with `SET LOCAL ROLE`
  inside one transaction. At activation that login role needs membership of
  `perception_owner_reader` (and not of `perception_availability_reader`).
- There is no Telegram `/now` command yet: the Telegram connector has no
  slash-command path, so wiring one is separate connector work.

## The owner clock (dark, `.21`)

`butlers.perception.clock` holds the owner-clock policy and the owner-local time
basis. Everything is pure and takes an injected instant. It is dark:
`perception.flags.OWNER_CLOCK_LIVE` is off, nothing schedules the reducer or
writes `owner.clock`, and no consumer has switched.

- `reduce_clock` switches automatically only on a phone fix in a new zone, with
  a notice and an undo. An unconfirmed booking or flight asks once per booking
  and never switches; an owner-confirmed booking switches
  (`owner_confirmed_booking`); an owner-set zone holds against phone fixes until
  the owner clears it. Undo reverts an automatic switch and holds that phone
  zone off for 24 hours. Holds are per zone and judged at the reducer's injected
  instant, not the fix's own stamp. A later automatic switch keeps every hold;
  only an owner set, confirm or clear removes them. With no evidence the clock is
  the home zone, else UTC (`utc_fallback`); no zone is hardcoded.
- `brief_due` gives once-a-day idempotence on the owner-local date: the key is
  the local date at the tick, and a watermark (the last key sent) stops a
  repeat after a westward jump, a DST repeated hour or an undo. A missed tick
  catches up later the same local day. A watermark later than the UTC date plus
  one day is no real local date and is ignored, so it cannot suppress the brief
  forever. The caller persists each returned key over the watermark; a clock
  skewed backward by more than a day may repeat one day (accepted trade-off).
- `clock_parity` is the shadow-parity receipt: legacy zone (the saved
  `settings.general` zone, else the `Asia/Singapore` fallback the briefing
  uses) against the clock zone and basis, with both local dates. While dark,
  `owner_time_basis` returns the legacy zone and always yields the receipt.
- `clock_event` builds the `owner.clock` record for the `context.clock` source.
  At activation the writer needs `perception_context_writer`, and the
  `context.clock` profile needs its owner subject, a finite freshness maximum,
  its evidence sources and one registered evidence reference per clock basis.

## Owner modes (dark, `.21`)

`butlers.perception.modes` holds the owner mode rules recorded in bu-u33sb7.
It is dark: `perception.flags.MODE_CONTROLS_LIVE` is off, and no route or tool
calls it.

- The modes are the eight git IDs. Display aliases resolve to them; any other
  name refuses. "In flight" is a posture that only travel carries, never a mode.
- Only the owner enters a mode; an inferred entry refuses, and there is no
  automatic entry rule (M7 owns those).
- `classify_change` says whether a change tightens or loosens. Exit, a shorter
  duration, fewer held classes or more break-through senders loosen; a mixed
  edit counts as loosening. An entry is judged against `now` and the mode's
  active entry: one ending at or before `now` refuses, and one that ends an
  active mode sooner (or at all, when it is open-ended) loosens.
- `authorize_mode_change` passes a tightening and spends one dashboard step-up
  of kind `mode_loosen` for a loosening. It re-derives the binding from the
  owner action, expected version, namespace, current profile and active entry,
  and refuses any binding that differs. An edit's target binds its resulting
  state (`state_text`: `duration|holds|senders`).
- Obligation kinds are closed (`medication`, `safety`, `reply`, `bill`,
  `proactive`); an unknown kind refuses, so a misnamed medication or safety
  item is never held. A profile carrying a pinned class refuses in `held_by`.
- Defaults: sick holds proactive items except medication, safety, bills due
  within 48 hours and replies to people. Safety and medication are pinned, so no
  mode may hold them. Only owner-declared or owner-confirmed travel arms the
  absence plan. The brief follows the wake signal, never before 06:00, else
  09:30.
- Owner edits to a mode's duration, holds and break-through senders live in
  `switchboard.owner_mode_overlay` (sw_076): one row per edited mode, keyed by
  mode and versioned; a mode without a row has its default at version 0.
  `butlers.perception.mode_overlay.ModeOverlayWriter` writes a tightening with no
  step-up and a loosening only in the transaction that admits a `mode_loosen`
  step-up, whose target is `mode:new-version:digest16` over the resulting
  `duration|holds|senders` state (`modes.state_text`). The finish rule binds the
  retained result's `rule_id` to `mode:new-version`, so a step-up is never spent
  on another edit or a stale version. The table's trigger enforces the same rule
  on any owner SQL; no row is deleted; the runtime role has no privilege.
- Entering and leaving a mode are the `owner.mode` projection, not the overlay;
  their writer is a later change.

## Complete private backup

All ten durable relations and their exact fixed schema travel through
`perception_backup`, after complete DND history in the ordinary backup snapshot.
The dump identity gains no private privilege; the two capability roles stay
NOLOGIN and membership-free until a separate activation. Fixed catalog drift,
foreign owners/ACLs, missing interfaces and nonempty restore populations refuse.
Source profiles, original event IDs, retraction targets and receipts restore as
history without disabling immutable triggers or calling live writers. Artifacts
retain expired/private history for up to the existing fourteen-day backup window.
The fixed catalog fingerprint sorts textual metadata with PostgreSQL's `C`
collation, so an otherwise identical catalog has the same fingerprint across
database locales. Ownership, ACL, column, constraint and function drift still
refuse export.
See [the complete-artifact contract](../operations/backup-restore.md#private-history-export).
