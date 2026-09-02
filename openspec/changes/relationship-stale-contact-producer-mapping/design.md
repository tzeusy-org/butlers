## Context

The stale-contact inputs are active `relationship`-scoped `interaction_%` facts joined to a contact
through its entity. Today both `run_insight_scan()` and `contacts_overdue()` reduce those rows to a
last-interaction timestamp and apply the existing Dunbar or `stay_in_touch_days` cadence. Neither
path retains a trustworthy producer identity.

The passive writer currently covers `email`, `telegram_user_client`, and
`whatsapp_user_client` inbox channels plus calendar attendees. All of those calls converge on
`interaction_log()`, whose caller-supplied metadata is nested under `extra_metadata`; therefore the
existing `extra_metadata.source="interaction_sync"` value is useful inventory evidence but is not
a server-attested authority boundary. The dashboard owner-write route, the MCP tool, and the group
fan-out also converge on `interaction_log()` without persisting a server-derived origin. Historic
rows and migration backfills likewise carry no authoritative producer.

PR #3965 merged the shared `public.expected_signals` primitive and RFC 0029 as commit
`af3017cb0`. This prerequisite is rebased on that contract and defines the Relationship mapping
that continued adoption under `bu-8cdl1.3` must consume in a new implementation PR.

## Goals / Non-Goals

**Goals:**

- Inventory every current Relationship interaction fact writer and every configured communication
  source relevant to a stale-contact claim.
- Define a deterministic one-contact-to-one-producer admission rule with no row-order choice or
  aggregate connector guess.
- Make unmeasurable suppress all stale-contact candidate and nudge paths while preserving the
  existing policy for a healthy elapsed producer.
- Give continued `bu-8cdl1.3` adoption an implementation-ready provenance, liveness, API,
  dashboard, and test handoff for a new PR.

**Non-Goals:**

- Reimplementing the shared expected-signals table/helper or rewriting merged PR #3965.
- Backfilling producer claims onto legacy facts.
- Adding Telegram bot or Discord to passive interaction sync.
- Treating calendar availability as connector liveness without a separately proven calendar
  producer.
- Changing outreach cadence, priority, ranking, deduplication, or delivery policy.
- Adding direct Relationship reads of Switchboard liveness tables or another butler schema.

## Decisions

### 1. Current writer, provenance, identity, and source inventory

| Input path | Current writer and stored provenance | Contact identity | Runtime/source configuration | Mapping consequence |
|---|---|---|---|---|
| Passive Gmail email | `run_interaction_sync()` -> `interaction_log()` writes `interaction_email`; caller metadata becomes `extra_metadata={source: interaction_sync, message_count, group_size}` | exact active `has-email` | `connector-gmail` in `docker-compose.yml` and Gmail pane in `scripts/dev.sh`; source channel `email`, heartbeat type `gmail` | eligible for `connector:gmail` only after reserved server attestation |
| Passive Telegram user-client | the same job writes `interaction_telegram_user_client` with the same nested source/count/group metadata | active `has-handle` object `telegram:<id>` | `connector-telegram-user` in Compose and user-client pane in `scripts/dev.sh`; heartbeat type `telegram_user_client` | eligible for `connector:telegram_user_client` only after reserved server attestation |
| Passive WhatsApp user-client | the same job writes `interaction_whatsapp_user_client` with the same nested source/count/group metadata | exact JID handle or canonical resolver's E.164 `has-phone` fallback | `connector-whatsapp-user` in Compose; heartbeat type `whatsapp_user_client` | eligible for `connector:whatsapp_user_client` only after reserved server attestation |
| Telegram bot | no Relationship passive interaction writer | Telegram handles share the `telegram:<id>` namespace with user-client evidence | `connector-telegram-bot` in Compose and bot pane in `scripts/dev.sh`; heartbeat type `telegram_bot` | unmeasurable; bot health cannot stand in for user-client health |
| Discord | no Relationship passive interaction writer | namespaced `has-handle` may identify `discord:<user_id>` | connector implementation uses source channel `discord` and heartbeat type `discord`; the standard Compose/dev stack does not launch it | unmeasurable even if a Discord runtime is separately configured |
| Calendar attendee sync | `run_interaction_sync()` writes `interaction_calendar_event` with nested `source`, `event_id`, and `event_title` only | exact active attendee `has-email` | Relationship calendar module is Google-backed, but the copied interaction row does not attest the event producer/source kind | unmeasurable; current attendee metadata cannot authorize calendar liveness |
| Dashboard/API manual entry | owner-gated `POST /entities/{id}/interactions` calls `interaction_log()` without origin metadata | none required | no connector | currently unmeasurable; eligible for `owner` after server principal attestation |
| MCP/manual and group entry | the MCP wrapper and `interaction_log_group()` converge on `interaction_log()` and accept caller metadata | none required | no connector | unmeasurable unless a server-derived owner principal is persisted |
| Legacy/backfill | migration `012_backfill_interaction_predicates.py` rewrites predicates but does not establish producer provenance | historic identity may differ from the writer-time identity | none provable | unmeasurable; no identity-based backfill |

`interaction_log()` is the sole live narrative interaction fact writer; its API, MCP, group, passive
message, and calendar callers are separate provenance paths even though they share the final
storage call. `relationship_assert_fact()` writes the distinct `relationship.entity_facts`
identity/edge store and is not an interaction writer.

### 2. A reserved server attestation, not free-form metadata, proves the writer

The downstream writer SHALL persist a reserved top-level
`metadata.expected_signal_source` object containing `producer`, `source_channel`, the exact
server-derived `source_endpoint_identity`, and `writer="interaction_sync"`, or
`producer="owner"` with a server-derived owner principal and no connector endpoint for a manual
observation. `run_interaction_sync()` must preserve the endpoint identity already carried by each
`message_inbox.request_context`; it must not infer it from the sender, contact identity, or another
row. Its message query and grouping key must include
`request_context->>'source_endpoint_identity'`; using `MAX`, the first row, or a thread/channel-only
group would silently merge two accounts and is forbidden. Public API/MCP metadata MUST NOT be able
to set or override this object.

Existing `metadata.extra_metadata.source` values are not upgraded in place: the same field is
caller-supplied through `interaction_log(metadata=...)`, so treating it as authority would allow a
manual call to impersonate an automatic producer. Legacy rows remain unmeasurable until a new
attested observation establishes a trustworthy baseline.

Alternative rejected: infer ownership from the predicate alone. `interaction_log()` accepts a
free-form type, so a manual caller can create `interaction_email` or
`interaction_telegram_user_client` without the corresponding connector.

### 3. Contact identities corroborate producer evidence; they never invent it

The mapping consumes active Relationship identity facts as follows:

- Gmail requires an exact active `has-email` identity.
- Telegram user-client requires an active `has-handle` value in the canonical
  `telegram:<id>` namespace.
- WhatsApp user-client requires the identity evidence used by the canonical resolver: an exact
  WhatsApp JID handle or the E.164 `has-phone` fallback.
- Owner-attested manual observations require no channel identity.

An identity proves that a producer could observe this contact; it does not prove that the producer
wrote a particular fact. A producer attestation that lacks its required identity corroboration is
unprovable and therefore unmeasurable.

Alternative rejected: map every `has-handle` row to a connector. Telegram, Discord, LinkedIn,
Twitter, and generic handles share a predicate family, so a non-namespaced handle cannot identify
one liveness source.

### 4. Authoritative producer/source matrix

The JSON block below is the machine-readable planning contract exercised by
`tests/contracts/test_relationship_stale_contact_producer_mapping.py`. `mapped` means the row can
participate only when its reserved server attestation and identity proof both hold.

<!-- relationship-stale-contact-producer-map:start -->
```json
[
  {
    "source_id": "gmail_email",
    "input_channel": "email",
    "writer": "interaction_sync",
    "fact_predicate": "interaction_email",
    "identity_proof": "has-email exact match",
    "producer": "connector:gmail",
    "endpoint_identity": "server-derived source_endpoint_identity",
    "mapping": "mapped",
    "kill_mode": "heartbeat"
  },
  {
    "source_id": "telegram_user_client",
    "input_channel": "telegram_user_client",
    "writer": "interaction_sync",
    "fact_predicate": "interaction_telegram_user_client",
    "identity_proof": "has-handle telegram:<id>",
    "producer": "connector:telegram_user_client",
    "endpoint_identity": "server-derived source_endpoint_identity",
    "mapping": "mapped",
    "kill_mode": "heartbeat"
  },
  {
    "source_id": "whatsapp_user_client",
    "input_channel": "whatsapp_user_client",
    "writer": "interaction_sync",
    "fact_predicate": "interaction_whatsapp_user_client",
    "identity_proof": "exact WhatsApp JID handle or E.164 has-phone fallback",
    "producer": "connector:whatsapp_user_client",
    "endpoint_identity": "server-derived source_endpoint_identity",
    "mapping": "mapped",
    "kill_mode": "heartbeat"
  },
  {
    "source_id": "manual_owner_attested",
    "input_channel": "manual",
    "writer": "server-authenticated interaction_log entry point",
    "fact_predicate": "interaction_<type>",
    "identity_proof": "server-derived owner principal",
    "producer": "owner",
    "endpoint_identity": null,
    "mapping": "mapped",
    "kill_mode": "attestation"
  },
  {
    "source_id": "manual_unattested",
    "input_channel": "manual",
    "writer": "current generic interaction_log entry points",
    "fact_predicate": "interaction_<type>",
    "identity_proof": "none persisted",
    "producer": null,
    "endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "telegram_bot",
    "input_channel": "telegram_bot",
    "writer": "no passive Relationship interaction writer",
    "fact_predicate": null,
    "identity_proof": "has-handle telegram:<id> is shared with user-client",
    "producer": null,
    "endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "discord",
    "input_channel": "discord",
    "writer": "no passive Relationship interaction writer",
    "fact_predicate": null,
    "identity_proof": "has-handle discord:<user_id>",
    "producer": null,
    "endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "calendar_event",
    "input_channel": "calendar",
    "writer": "interaction_sync calendar attendee path",
    "fact_predicate": "interaction_calendar_event",
    "identity_proof": "has-email attendee match",
    "producer": null,
    "endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "legacy_or_unknown",
    "input_channel": "unknown",
    "writer": "legacy migration, missing attestation, or unknown writer",
    "fact_predicate": "interaction_%",
    "identity_proof": "missing or unprovable",
    "producer": null,
    "endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  }
]
```
<!-- relationship-stale-contact-producer-map:end -->

Connector liveness is keyed by the exact pair `(connector_type, endpoint_identity)`. Gmail's type
is `gmail`; Telegram bot and Telegram user-client are deliberately not collapsed (`telegram_bot`
versus `telegram_user_client`); WhatsApp's type is `whatsapp_user_client`. A healthy sibling
endpoint of the same connector type MUST NOT satisfy the attested endpoint's liveness check.
Discord's runtime heartbeat type is `discord`, but it is not a mapped stale-contact producer
because the Relationship passive writer does not ingest that channel.

Continued adoption SHALL extend the shared expected-signal representation with nullable
`producer_endpoint_identity`: it is required and non-empty whenever `producer` starts with
`connector:`, and null for `owner`. Connector measurability must query
`public.v_qa_connector_state` by both the producer's connector type and this exact endpoint value.
It must not keep RFC 0029's initial type-only lookup for Relationship signals.

### 5. One producer or unmeasurable

For each contact, adoption builds the producer/endpoint pair set from:

1. the active contact identities whose configured source can write Relationship interactions; and
2. the reserved producer attestation on the observation that establishes `last_observed_at`.

The result is measurable only when the set contains exactly one producer, the attestation's exact
endpoint identity and contact identity agree, and no participating evidence is missing or
unprovable. Zero producers, two or more producers, conflicting attestations, a missing/mixed
endpoint identity, an unsupported identity path, tied latest observations from different
producers/endpoints, or an unreadable source all yield `unmeasurable`. The evaluator MUST NOT pick
the first row, the newest healthy connector, a primary contact field, or any healthy sibling
endpoint of the same provider family.

For a contact with no interaction fact, exactly one corroborated mapped contact identity may define
the expected producer; otherwise the signal is unmeasurable. A legacy last-interaction row cannot
be backfilled from today's identities. It stays unmeasurable until a later server-attested
observation establishes the baseline.

### 6. State and consumer matrix

| Producer mapping | Producer evidence | Cadence | Signal result | Candidate / nudge |
|---|---|---|---|---|
| Exactly one mapped connector + endpoint | that exact endpoint healthy and heartbeat current | not elapsed | `present` | no |
| Exactly one mapped connector + endpoint | that exact endpoint healthy and heartbeat current | elapsed | `absent` | existing policy may emit |
| Exactly one mapped connector + endpoint | exact endpoint stale, dead/offline, unhealthy, missing, or unreadable (even if a sibling endpoint is healthy) | any | `unmeasurable` | no |
| Attested `owner` manual source | server attestation valid | not elapsed | `present` | no |
| Attested `owner` manual source | server attestation valid | elapsed | `absent` | existing policy may emit |
| Manual source | attestation missing or caller-asserted | any | `unmeasurable` | no |
| Missing, unsupported, mixed, or conflicting source | any | any | `unmeasurable` | no |

"Existing policy may emit" preserves the current boundaries: overdue means strictly greater than
the effective cadence; priority is 35 through two-times cadence and 45 above two-times cadence;
tier 1500 without `stay_in_touch_days` remains excluded. This mapping neither creates nor expands
an outreach policy.

The gate applies before all owner-facing stale-contact consumers:

- `run_insight_scan()` category `stale-contact`;
- `contacts_overdue()` / `contacts_overdue_with_tiers()`, including the Monday
  `relationship-maintenance` notification and on-demand `reconnect-planner` output;
- the Relationship Contacts tab overdue KPI/list; and
- the Plex "Worth attention" rail.

An unmeasurable contact is absent from overdue results and candidates. If any requested aggregate
contains unmeasurable contacts, its API/dashboard availability metadata must prevent "Cadence all
clear" or an equivalent complete healthy claim.

On current `main`, the Contacts tab still calls the retired
`GET /api/relationship/contacts/overdue` route and therefore shows an error rather than live
overdue data. The Plex rail is live and derives overdue contacts client-side from
`GET /api/relationship/dunbar/ranking`. Downstream adoption must carry measurability through the
live ranking path and must not portray the dead Contacts endpoint as evidence that the dashboard
contract is already satisfied; restoring or retiring that declared surface remains an explicit
implementation choice within the approved dashboard requirement.

### 7. Continued `bu-8cdl1.3` adoption handoff

After this prerequisite merges, continued Relationship adoption under `bu-8cdl1.3` SHALL open a
new implementation PR from the resulting `main` and:

1. extend RFC 0029's Relationship adoption section by reference to this mapping;
2. reserve and protect `metadata.expected_signal_source` at the writer boundary, including the
   exact server-derived endpoint identity;
3. derive one `relationship:stale-contact:{contact-id}` producer or `unmeasurable` without a
   cross-schema shortcut;
4. persist the shared tri-state before every stale-contact consumer can emit owner-facing output;
5. expose aggregate availability so both dashboard overdue surfaces avoid false all-clears; and
6. add `producer_endpoint_identity` to the expected-signals producer representation and bind the
   Relationship liveness lookup to `(connector_type, endpoint_identity)` without weakening existing
   Health behavior; and
7. convert this prerequisite's executable planning matrix into migrated-PostgreSQL integration
   tests against the real expected-signals helper and liveness projection, including a two-endpoint
   kill matrix where a healthy sibling cannot substitute.

No source in this mapping is considered adopted merely because this planning change merges.

## Risks / Trade-offs

- [Risk] Fail-closed classification suppresses many current suggestions until new attested evidence
  exists. -> Mitigation: expose unmeasurable as instrument/provenance unavailability and do not
  backfill a claim that cannot be proved.
- [Risk] Contact identities can be multi-channel, making a useful aggregate unmeasurable. ->
  Mitigation: preserve the one-producer contract now; a future multi-producer conjunction requires
  its own policy/spec change rather than a hidden aggregate.
- [Risk] A caller forges current free-form `extra_metadata.source`. -> Mitigation: only the reserved,
  server-written top-level attestation is authoritative.
- [Risk] Suppressing rows makes an empty dashboard look healthy. -> Mitigation: aggregate
  availability is explicit and forbids all-clear copy when any relevant source is unmeasurable.

## Migration Plan

1. Merge this main-based mapping prerequisite.
2. Open a new Relationship adoption PR for `bu-8cdl1.3` from that exact `main` head.
3. Implement the RFC 0029 Relationship adoption handoff above without backfilling legacy
   producer claims.
4. Roll out writer attestation before enabling stale-contact expected-signal evaluation.
5. Enable candidate/dashboard consumption only after the per-producer kill tests pass.

Rollback is to disable Relationship expected-signal adoption while retaining the mapping and
unmeasurable data. Rollback MUST NOT restore elapsed-time-only nudges.
