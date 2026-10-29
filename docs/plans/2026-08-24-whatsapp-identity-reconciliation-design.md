# WhatsApp Identity Resolution and Reconciliation Design

**Date:** 2026-08-24
**Status:** Approved for implementation
**Scope:** WhatsApp sender identity resolution, conversation decomposition identity propagation,
fact-storage protection, and guarded cleanup of existing false transitory entities

## Problem

The WhatsApp connector is healthy and message ingestion succeeds, but routing-time identity
resolution receives the transport channel name `whatsapp_user_client`. The canonical resolver only
activates WhatsApp JID and phone-number matching for the identity type `whatsapp_jid`. Known senders
therefore enter the unknown-sender flow and acquire unnecessary transitory entities.

Buffered conversations have a second identity leak. The connector translates opaque WhatsApp LIDs
to phone JIDs in `sender.participants`, but separately copies raw `sender_jid` values into normalized
text and `conversation_history`. Conversation decomposition then renders those transport identifiers
as speaker names. A downstream fact-extraction session can interpret the identifier as a person name
and create another transitory entity with `source=fact_storage`.

Current live evidence demonstrates both failures:

- the connector is healthy and ingestion errors are zero;
- Google and Telegram contact synchronization is current; there is no WhatsApp address-book sync
  provider;
- raw WhatsApp JID/LID transitory entities exist after the earlier participant-identity fix;
- most of those identifiers have one unambiguous match to an existing confirmed entity through the
  existing phone-number resolution rules;
- the affected raw-identifier entities are currently empty identity shells, but the repair must not
  assume future rows remain unreferenced.

The desired outcome is not to import the WhatsApp address book. It is to make message-time identity
resolution consistently reuse the canonical entity graph, preserve per-speaker attribution in
buffered group conversations, and provide an explicit, content-blind operator workflow for existing
false entities.

## Goals

1. Resolve `whatsapp_user_client` senders through the canonical `whatsapp_jid` identity rules without
   changing the persisted transport channel used for ingestion, telemetry, and policy.
2. Translate LIDs before any sender identifier becomes LLM-visible.
3. Carry a deterministic entity anchor for every decomposed speaker, including genuinely unknown
   senders.
4. Prevent memory fact extraction from creating entities whose canonical names are WhatsApp transport
   identifiers.
5. Add a reconciliation command that is dry-run by default, content-blind, drift-detecting, and unable
   to mutate referenced or ambiguous entities.
6. Preserve the existing owner-notification, sender-reservation, merge-review, and entity-lifecycle
   contracts.

## Non-goals

- Implementing WhatsApp address-book synchronization.
- Trusting WhatsApp PushName as a canonical identity.
- Automatically executing reconciliation during migration, startup, deployment, or connector polling.
- Merging genuinely unmatched or ambiguous senders.
- General redesign of every entity-merge path. Existing broader merge-service drift is outside this
  fix; the reconciler is deliberately restricted to reference-free transitory shells.
- Deleting entities or rewriting historic ingestion payloads.

## Considered Approaches

### A. Structured per-speaker identity propagation (selected)

Translate the transport channel at the identity boundary, normalize conversation-history sender
identities, resolve every distinct speaker in Switchboard, and carry `sender_entity_id` through
decomposition. Add a memory-layer transport-identifier guard and a narrowly constrained cleanup tool.

This is the only approach that handles direct messages and groups without either losing facts or
creating duplicate identities. It keeps identity decisions deterministic and outside the LLM.

### B. Transport alias plus memory regex rejection

Adding the missing alias would fix the primary sender. Rejecting JID-shaped entity names would stop
new junk rows. This is smaller, but it gives additional speakers in a group no entity anchor. Their
facts would fail or be silently omitted, so it does not meet the entity-anchoring contract.

### C. Connector-side contact enrichment

The connector could query contacts and render display names. This puts canonical identity ownership in
the transport process, duplicates Switchboard resolution, and still cannot safely treat PushName as a
unique identity. It violates the connector/butler responsibility boundary and is rejected.

## Architecture

### 1. Canonical channel-to-identity translation

Introduce one shared, deterministic translation used by routing-time identity resolution and the
post-resolution channel-fact assertion:

```text
whatsapp_user_client -> whatsapp_jid
```

The ingest envelope and persisted request context continue to say
`source_channel=whatsapp_user_client`. Only the identity lookup type changes. This preserves connector
metrics, ingestion rules, replay behavior, and passive-interaction queries.

The translation must live at the identity boundary rather than in each caller. Existing Telegram
transport aliases already establish that the identity layer accepts transport-specific channel names.
Direct calls with `whatsapp_jid` remain valid.

### 2. Connector history normalization

Before constructing `payload.normalized_text` or `payload.raw.conversation_history`, the connector
normalizes every event sender:

- strip a device ordinal from phone and LID JIDs;
- translate a mapped `@lid` through `public.whatsmeow_lid_map`;
- retain an unmapped raw identity only as structured internal data;
- never render a JID or LID as a human display label.

The connector emits a structured conversation-history entry with at least:

```text
message_id
sender_identity
sender
text
timestamp
reply_to
```

`sender_identity` is the normalized machine identifier. `sender` is a safe display label; until
Switchboard enrichment it is a neutral label such as `Unknown WhatsApp sender`, not the identifier.
No canonical entity decision is made in the connector.

### 3. Switchboard batch-speaker resolution

The conversation-decomposition branch keeps the structured history rather than reducing it to a
formatted string before identity work. It resolves every distinct `sender_identity` using the
canonical identity type for the source channel.

For a known sender, the enriched speaker record contains the canonical entity UUID and canonical
display name. For an unknown sender, Switchboard calls the existing reservation-backed transitory
entity flow, receives or reuses one entity UUID, and performs the existing relationship-owned channel
fact assertion. Owner notifications retain their durable once-per-sender claim.

The top-level sender identity used for the routing preamble is selected from this same resolution map.
It is not resolved a second time.

The enriched history and conceptual excerpts carry:

```text
sender
sender_identity
sender_entity_id
text
timestamp
message_id
```

The LLM sees `sender`, which is a canonical or neutral human label. The machine identifier remains
structured context and is never promoted to a name. The downstream butler uses `sender_entity_id` for
facts about that speaker.

### 4. Fact-storage defense in depth

The runtime `memory_entity_create` wrapper rejects WhatsApp JID/LID-shaped `canonical_name` values
when the request is attempting fact-storage transitory creation. Its structured error directs the
caller to the conceptual excerpt's `sender_entity_id`.

The guard does not guess which entity a group speaker represents and does not substitute the
top-level sender UUID. Incorrect attribution is worse than a rejected write. Normal entity creation,
including email-like organization names and the direct unknown-sender flow in `create_temp_contact`,
is unaffected.

The shared memory and relationship fact-extraction instructions are updated to treat
`sender_entity_id` as authoritative and to state that transport identifiers are not entity names.

## Reconciliation Command

### Interface

Add a repository-owned Python command with PEP 723 metadata. Its default invocation performs no
writes. Apply mode requires both explicit intent and the digest produced by a previous dry run:

```text
uv run scripts/reconcile_whatsapp_entities.py
uv run scripts/reconcile_whatsapp_entities.py --apply --plan-digest <digest>
```

The database DSN is read from an environment variable and never printed. No secret may be accepted
as a command-line argument. The command invokes a FastAPI-free relationship service directly, so it
does not accept or bypass dashboard API authentication.

### Candidate discovery

The planner considers live transitory person entities whose canonical names are individual WhatsApp
phone JIDs or LIDs and whose provenance is either:

- `source_channel=whatsapp_user_client`; or
- `source=fact_storage`, `source_butler=general`, and
  `source_scope` in the observed routed scopes `general` or `global`.

It excludes group JIDs and malformed identifiers. LIDs are translated through the existing map. The
planner enumerates all live confirmed candidates using the same exact-phone and bounded digit-suffix
rules as the canonical resolver. It must count distinct candidates directly; it must not rely on a
first-match bulk resolver when deciding uniqueness.

Every source is classified into one of these content-blind categories:

- `unique_empty_shell`
- `unmatched`
- `ambiguous`
- `invalid_identifier`
- `owner_or_system_target`
- `existing_review_decision`
- `referenced_source`
- `plan_drift`

Only `unique_empty_shell` enters the plan.

### Empty-shell safety contract

Before planning and again under the apply transaction's row locks, the source must satisfy all of the
following:

- transitory, live, and not already merged or deleted;
- person entity with no roles and no user-authored aliases;
- metadata limited to the expected transitory/provenance keys;
- no active subject- or object-side relationship facts;
- no memory facts or edge facts;
- no rows in any foreign-key relation targeting `public.entities`;
- no protected owner, system-account, credential, calendar, Chronicler, priority-contact, view-mark,
  or source-link ownership;
- no rejected, abandoned, or conflicting exact-pair review decision.

Reference discovery must use PostgreSQL catalog metadata plus explicit checks for UUIDs stored as text
objects. A source with any reference is skipped, not partially repaired.

Because these sources are proven empty shells, the operation does not need to reconcile aliases,
roles, metadata, or cross-module facts. The existing audited relationship merge transaction is
factored behind a FastAPI-free service so the dashboard endpoint and reconciliation command share
locking, tombstoning, conflict checks, and merge-review audit behavior. The reconciliation caller adds
the strict empty-shell precondition inside the same transaction.

### Plan and apply safeguards

The dry run prints only aggregate category counts, an opaque candidate count, and a SHA-256 plan
digest. It does not print names, JIDs, phone numbers, fact values, raw exceptions, or merge evidence.

The digest covers ordered source/target UUID pairs and the source/target state needed to detect drift,
including update timestamps and relevant review-decision state. Apply mode recomputes the complete plan
before writing and refuses to proceed unless the digest matches exactly.

Pairs execute sequentially. Each pair is revalidated under deterministic source/target row locks. The
command aborts on the first failed invariant. After each operation it verifies:

- source tombstoned to the expected target;
- target still live;
- no source references remain;
- exactly one merged review outcome exists for the pair;
- the pair no longer appears in a fresh reconciliation plan.

Pair transactions remain independently audited commits. If a later pair or postcondition stops the
authorized apply after one or more pair transactions committed, the command returns a distinct
content-blind `partial_apply` result with the committed count, planned count, opaque plan digest, and
fixed stop category. It never claims those committed pairs were rolled back. A failure before the
first pair commits retains the existing zero-write rejection category.

The command is included and tested but is not run automatically as part of this change.

## Error Handling and Observability

- Missing LID mappings create or reuse a legitimate unknown-sender transitory entity; they do not
  reveal the LID in prompts or logs.
- Ambiguous phone resolution returns unknown and never selects the first row.
- Identity database failures remain fail-open for message routing but produce structured warning
  telemetry without identifiers.
- Per-speaker resolution failures retain a neutral label and omit `sender_entity_id`; downstream fact
  storage then fails closed rather than inventing an entity name.
- Reconciliation errors report only category, opaque pair fingerprint, and failure class. Detailed
  internal evidence remains in the database audit, not stdout.
- Temporary debugging instrumentation uses a unique investigation tag and is removed before commit.

## Specification Changes

Create one OpenSpec change, without a Beads issue, covering these existing capabilities:

- `switchboard-identity`: transport-channel translation and batch per-speaker resolution;
- `connector-base-spec`: LID normalization in every LLM-facing sender representation;
- `conversation-decomposition`: additive `sender_identity` and `sender_entity_id` excerpt fields and
  the safe-label rule;
- `entity-identity`: transport identifiers must not become fact-storage entity names when structured
  sender identity exists, plus the guarded reconciliation contract.

No RFC or manifesto change is required. The work restores the existing deterministic identity and
connector-normalization doctrine rather than creating an architectural exception.

## Test Strategy

Implementation follows red-green-refactor in these focused layers:

1. Identity unit tests prove `whatsapp_user_client` activates the same exact and phone fallback as
   `whatsapp_jid`, while persisted source-channel values remain unchanged.
2. Connector tests prove mapped LIDs and device-suffixed JIDs are normalized in participants,
   normalized text, and conversation history; raw transport IDs are not rendered as speaker labels.
3. Pipeline tests prove one resolution per distinct batch speaker, reuse of the primary resolution,
   unknown-sender reservation/fact assertion behavior, and identity fields surviving excerpt
   normalization.
4. Integration tests cover a mixed known/unknown multi-speaker WhatsApp batch and verify facts use the
   intended entity UUIDs without creating JID-named entities.
5. Memory tests prove fact-storage JID/LID creation is rejected with an actionable structured error
   while ordinary entity creation remains unchanged.
6. Reconciliation tests cover dry-run default, content-blind output, exact digest enforcement,
   ambiguity, owner/system exclusion, existing review decisions, reference detection, plan drift,
   transactional revalidation, merge audit, and post-apply recount.

Targeted tests run first. Verification then expands to the affected connector, pipeline, memory,
relationship API, OpenSpec, Ruff, format, and repository quality gates. Frontend tests are unnecessary
unless API response contracts change.

## Delivery and Rollout

All work occurs on a dedicated branch and goes through a pull request. No Beads issue is created or
mutated.

The runtime fix and reconciliation command ship together, but reconciliation remains an explicit
operator action. The forward identity fix must be deployed and observed healthy before any apply run.
The operator first runs a dry run, reviews aggregate categories, then supplies the exact digest to an
apply invocation in a separate command. A final dry run must report zero remaining planned pairs.

Live verification after deployment checks connector heartbeat, ingestion error count, known-contact
resolution, absence of new JID/LID fact-storage entities, and the unidentified-entity queue trend.
Existing unmatched identities remain reviewable transitory entities by design.
