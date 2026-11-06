# Memory Module

> **Purpose:** Persistent memory subsystem providing episodes, facts, rules, and entity-anchored knowledge with retrieval, consolidation, and decay lifecycle.
> **Audience:** Contributors and module developers.
> **Prerequisites:** [Module System](module-system.md).

## Overview

The Memory module gives butlers the ability to remember. It is a reusable module that any butler loads locally -- each butler owns its own memory data in its own database schema, with no cross-butler SQL access.

The module is responsible for:

- **Persisting memory artifacts** (episodes, facts, rules) with provenance in the hosting butler's database.
- **Low-latency retrieval** for runtime context injection via semantic, keyword, and hybrid search.
- **Lifecycle maintenance** including consolidation (episodes into facts/rules), confidence decay, fading, expiry, and cleanup.
- **Entity resolution** -- mapping ambiguous name strings to stable identity anchors in the `public.entities` table.

Source: `src/butlers/modules/memory/__init__.py` and `src/butlers/modules/memory/tools/`.

## Configuration

Enable in `butler.toml`:

```toml
[modules.memory]
# Retrieval defaults
[modules.memory.retrieval]
context_token_budget = 3000
default_limit = 20
default_mode = "hybrid"  # "semantic", "keyword", or "hybrid"

[modules.memory.retrieval.score_weights]
relevance = 0.4
importance = 0.3
recency = 0.2
confidence = 0.1

# Optional: write summaries to public.memory_catalog
enable_shared_catalog = false
```

## Memory Artifact Types

### Episodes (Observations)

High-volume, short-lived session observations. Append-only. Each episode records what happened during a butler session, tagged with importance and a session ID. Episodes have a TTL and are eligible for consolidation into durable artifacts.

Consolidation states: `pending` -> `consolidated | failed | dead_letter`.

### Facts (Semantic Memory)

Durable subject-predicate-content knowledge. Facts are the primary retrieval unit for context injection. Key properties:

- **Entity-anchored**: Facts link to entities via `entity_id` for stable identity (resolves the "which Chloe?" problem).
- **Scoped**: Facts live in namespaces (`global`, `health`, `relationship`, `finance`, etc.).
- **Lifecycle states**: `active` -> `fading` -> `superseded | expired | retracted`.
- **Confidence decay**: Effective confidence decreases over time via `confidence * exp(-decay_rate * days_since_last_confirmed)`.
- **Permanence levels**: `permanent`, `stable`, `standard`, `volatile`, `ephemeral` -- controlling decay rate and retention.

### Rules (Procedural Memory)

Behavior guidance learned from repeated outcomes. Rules track maturity (`candidate` -> `established` -> `proven` or `anti_pattern`), effectiveness scores, and application counts. Harmful evidence is weighted more heavily than helpful evidence, so bad rules demote faster than good rules promote.

### Content authority

Every episode, fact, and rule carries a server-stamped `content_authority`
(`owner`, `owner_device`, `third_party`, `system`, or `mixed`) derived from the
Switchboard-resolved routing context of the session that wrote it. It is never
taken from tool arguments or from text inside content, so a forged
`[Source: Owner ...]` line in a stranger's email grants nothing. Sessions with
no routing context stamp `system`, not `owner`.

Consolidation splits each claimed `(tenant_id, butler)` group into an owner-class
partition (`owner`, `owner_device`), a `system` partition and a non-owner
partition (`third_party`, `mixed` and unstamped NULL), each with its own runtime
session, request ID and episode set; see [Consolidation](#consolidation). It gives each derived
fact and rule the weakest authority among its evidence episodes (differing or
unknown authority yields `mixed`).

A tool that edits a fact by writing a successor keeps the old text or metadata, so the
successor's authority depends on its source as well as its writer: the one exception to
deriving authority only from the routing context. This applies to
`gift_update_status`, `loan_settle`, `task_complete`, `condition_update`,
`medication_update`, `medication_log_dose` and `research_update`, and to any session:

| Writer | Source | Stored | What the owner sees |
| --- | --- | --- | --- |
| owner-class | owner-class | the writer's class | a normal fact |
| owner-class or `system` | `third_party`, `mixed` | `mixed`, same sender | "partly reported by" the sender, held, with a Vouch action |
| owner-class | NULL | `mixed` | "partly reported by a third party", held, with a Vouch action |
| owner-class | `system` | `system` | unlabelled, not held, not a Profile Fact |
| `system` | owner-class, `system`, NULL | `system` | unlabelled, not held, not a Profile Fact |

The label on an owner's successor of a NULL row may overstate it, because a legacy row's
source is unknown; a vouch resolves the hold. A `system` writer over a NULL row is an accepted
exception to RFC 0038 S1: the successor loses the "source unverified" label, later owner edits
keep it `system`, and it still reads as unclean. "Not held" in the rules below describes NULL and `system`
rows themselves, not a `mixed` successor. A vouch does not carry: a vouched fact's successor
has no `endorsed_at`, so it is held again after the next edit.

`condition_update`, `medication_update` and `task_complete` accept an already superseded
fact's id. If a current owner-authored fact has replaced it, a `mixed` successor is refused
("A non-owner-authored fact cannot supersede an owner-authored fact"). A `system` successor
is not refused: it replaces the owner's current text with text built from the stale row, and
reads as unclean.

`gift_update_status` also accepts a superseded id, but it stores a temporal fact, which skips
supersession and this guard. A stale gift id is never refused and replaces nothing: it adds a
second active row beside the current one.

Steering memory is gated on that stamp:

- **Rules** from non-owner authority are *held*: stored, but absent from recall,
  Active Rules, and `public.memory_catalog` until the owner endorses them with
  `POST /api/memory/rules/{id}/endorse` (list them with `GET /api/memory/rules?held=true`).
  Endorsement is atomic with its audit event and catalog entry, and idempotent.
- **Facts** from `third_party` or `mixed` authority are *held* while live and not
  vouched for: they stay recallable as attributed reports (never Profile Facts), and
  the owner can vouch for one from the dashboard (fact detail, **Vouch**) or with
  `POST /api/memory/facts/{id}/vouch`; `GET /api/memory/facts?held=true` lists them.
  Vouching is owner-only and is not an MCP tool, so a session cannot do it. It is
  additive: `endorsed_at`/`endorsed_by` are set and authority and taint are unchanged,
  so clearing `endorsed_at` restores the prior state. A retracted or superseded fact
  returns 409; a repeat returns the same receipt and writes one audit event
  (`fact_vouched`) in total. Vouch uses the shared `is_fact_held` predicate: live
  legacy (NULL), `system`, and owner-class facts return the unchanged receipt without
  changing endorsement stamps, audit events, or catalog rows. The
  dashboard Confirm action records `owner` as its actor in `memory_events`; the MCP
  `memory_confirm` tool records `session`. The re-asserted catalog entry keeps the
  fact's original `content_authority`, so catalog readers still attribute it. Rollback:
  withdraw the route and the dashboard action; `fact_vouched` events stay in
  `memory_events`. Un-vouching one fact is clearing its two stamps; downgrading
  `mem_016` drops both columns (every vouch is lost, the audit trail is kept). Neither
  path touches authority or promotes a fact to owner-class. Not yet built: Telegram vouch cards and
  vouched facts joining Profile Facts, which wait on the goal-runtime work (RFC 0038).
- **Profile Facts** admit only owner-class, durable facts. Event and transaction
  predicates (purchases, doses, meals, sleep sessions, interactions) are excluded
  even when owner-authored; they stay stored and recallable. A fact is event-class
  when its predicate is registered `predicate_registry.is_temporal` (seeded, and
  set automatically for any predicate first stored with `valid_at`), so legacy rows
  need no backfill and no second list exists. A predicate missing from the registry
  is treated as durable.
  Every other rendered fact,
  whoever it is about (the owner or Bob), is a Task-Relevant Fact qualified as
  `reported by <sender>` (`third_party`), `partly reported by <sender>` (`mixed`)
  or `source unverified` (NULL). An unresolvable sender name renders as
  `a third party`; the qualification never depends on the lookup. `system` facts
  carry no label, and `source_butler` names the curating butler, never the reporter.
- A non-owner write cannot supersede an owner-authored fact.
- **Legacy rows** (written before `mem_013`) have no stamp and fail closed: legacy
  rules are held and legacy owner facts are not Profile Facts. They are never
  backfilled by guessing; the migration reports the counts it left unclassified.

### Goal lineage and taint (RFC 0038 D6, dark)

A write made inside a broker-verified call (see
[Capability Broker](../architecture/capability-broker.md#forwarding-and-domain-verification-d7-bu-pkuny2))
derives `content_authority` from the call's verified goal lineage, never from
the routing context: exposed or third-party lineage is `third_party`, unexposed
owner lineage is `owner`, obligation and staffer lineage is `system`. An explicit
owner-class authority under an exposed lineage is refused. When the lineage is
exposed the episode, fact or rule also stamps `taint_goal_id` and
`taint_invocation_id` (`mem_017`), a pointer to the goal and invocation whose
exposure wrote it rather than a boolean, so containment can find every row one
goal wrote; an episode update keeps an existing stamp. Contact notes and decision
memory are facts and carry the same stamp. Reads return both columns with the
row. NULL means written outside a verified call; until the M6 switch every row is
NULL. Rollback: downgrading `mem_017` drops both columns.

### Owner knowledge gaps

A sourceless decline in the dashboard answer lane no longer ends the question. The routed butler
may pass `gap={entity_id, predicate}` to `conversation_reply`; the server validates the live
entity and registered predicate, takes the origin (conversation, request id, channel) from the
routing context, and records an `open` row in `knowledge_gaps` (one `knowledge_gap_origins` row per
asking thread, at most one open gap per entity and predicate). The decline reply persists either
way.

A later write of a matching active fact -- `store_fact`, or the relationship butler's
`relationship_assert_fact` -- moves the gap to `answerable` in the same transaction as the write,
recording the fact reference, a value excerpt and the fact's server-stamped authority. A gap
captured while the fact already exists is born `answerable`. Capture, closure and that re-check
serialize on an advisory lock, so a racing write cannot strand an open gap.
A closure that raises never fails the fact write: the write commits, the gap stays open, and
`butlers.memory.knowledge_gap_closure_failures_total` (`stage=closure`, or `stage=reservation` when the pair
reservation fails; per `butler`) is
incremented after a warning log; a failure to record the counter is logged and never fails
the write.

When `store_fact` creates a registered inverse or symmetric narrative edge, the generated
active row also answers the original object's gap. Its citation is the inverse row's own UUID,
its value is the swapped subject label, and its authority is the original server stamp.
Mirrored writers reserve both schema-qualified advisory keys in signed database-hash order
before locking or inserting facts, preventing reciprocal writers from taking opposite lock
orders. These reservations and both answers commit or roll back with the facts and their graph
projections. Identity and registry-relational predicates still belong to the Relationship writer.

Gap bookkeeping is best effort: a missing local gap table skips it; an ordinary reservation
failure skips both closures, while an individual closure failure leaves that gap open without
discarding a successful sibling. Savepoints protect the canonical fact transaction, and failures
use the warning and the counter above. Cancellation or a canonical write/projection error still rolls
back the enclosing write. Temporal replay and an already-existing inverse key create no new
answer; inactive retained rows are never reactivated. There is no historical repair scan, and
later retraction retains the existing answer snapshot. Gaps retain the local single-owner contract:
they have no tenant or sensitivity field, so this fix adds neither a tenancy nor a delivery policy.

The `memory_knowledge_gap_delivery` maintenance job (every ten minutes, no LLM session) posts one
notice per origin thread into `public.dashboard_messages` and marks the gap `delivered`. Owner-class
and system authority read "Now known"; any other authority reads "Reported by <sender>, not
verified". A failed post keeps the gap `answerable` and retries with bounded backoff
(`delivery_attempts`, `next_attempt_at`); after five attempts it is recorded on the attention
ledger and listed with `delivery_failed`. Open gaps past `expires_at` (ninety days) become
`expired`; `dismissed` and `expired` are terminal and never reopen.
Delivery remains dashboard-only and at least once: a crash after the independently committed
message post but before the delivery marker commits can repeat the notice. Normal committed
delivery and fact replay do not post it again.

`memory_open_gaps` and `GET /api/memory/gaps` (a keyset `Page` with top-level `sources_degraded`) list
gaps with a `coverage_state` from the same vocabulary as predicate coverage: an open gap reads
`unknown` unless `relationship.fact_coverage` receipts say `absent_proven` or `unavailable`.
`POST /api/memory/gaps/{id}/dismiss` is the owner's terminal dismissal. Only the dashboard channel
captures gaps today; the Telegram answer path and an entity-detail "Still unknown" section are a
later slice.

The dashboard's `/memory` page has a Questions register (`register=questions`, also reachable from
the "Open questions" command-palette verb). It lists open and answerable gaps with their coverage
in words ("never looked", "looked, nothing there", "unavailable", "answered"), asked date and
thread count, and offers dismiss with a confirm. Above the rows, a per-predicate demand summary
reads `GET /api/memory/gaps/demand`: open gaps and asking threads per predicate, open gaps only,
aggregated per pool in SQL and summed across memory pools, so the count is right whatever the
page size, with failing pools named in `meta.sources_degraded`.

## Tools Provided

Tools are registered in `MemoryModule.register_tools` (`src/butlers/modules/memory/__init__.py`),
with implementations under `src/butlers/modules/memory/tools/`; read it for the current names and
signatures. Each tool is registered under a tool group (`core`, `entity`, ...), so a butler's
`groups` config can load only part of the surface (see [Module System](module-system.md)). The
families:

- **Writing** (`memory_store_episode`, `memory_store_fact`, `memory_store_rule`) -- store raw
  episodes, entity-anchored facts with predicate validation, and behavioural rules.
- **Reading and context** (`memory_search`, `memory_recall`, `memory_get`, `memory_context`) --
  retrieval (below) and token-budgeted, sectioned context assembly.
- **Feedback** (`memory_confirm`, `memory_mark_helpful`, `memory_mark_harmful`) -- reset decay and
  drive rule maturity.
- **Management** (`memory_forget`, `memory_stats`) -- retraction and statistics.
- **Knowledge gaps** (`memory_open_gaps`) -- owner questions still unanswered (below).
- **Preferences** (`memory_set_preference`, `memory_get_preferences`) -- owner preferences stored
  as `preferences:<domain>_<name>` facts.
- **Predicates and entities** (`memory_predicate_*`, `memory_entity_*`) -- predicate registry
  search, and entity create/get/update/resolve/merge/neighbour traversal.
- **Maintenance** (`memory_run_consolidation`, `memory_run_episode_cleanup`, `memory_reembed*`) --
  consolidation, episode expiry, and re-embedding stale vectors (dry-run by default).
- **Catalog** (`memory_catalog_search`) -- search the shared `public.memory_catalog`. Following a
  catalog pointer (`memory_catalog_fetch`) is a core tool, not part of this module
  (`src/butlers/core_tools/_memory_catalog.py`).

The Relationship butler additionally gets `memory_reclassify`, registered only when
`butler_name == "relationship"` and approval-gated for its episodic-predicate curation job. It only
moves an active fact to `volatile` permanence; its memory type, fact id and target permanence are
all safety-critical approval-rule arguments.

## Retrieval

The module supports three retrieval modes fused via composite scoring:

- **Semantic**: Vector similarity search using embeddings (MiniLM).
- **Keyword**: PostgreSQL full-text search (tsvector).
- **Hybrid**: Reciprocal-rank fusion over semantic and keyword results.

`memory_recall`'s `entity_id` and `predicate` filters narrow the candidate set in the search
query, before the per-source limit, so a fact is found however many more relevant facts exist
about other entities. The entity filter matches a fact's subject or object entity; rules carry
neither, so either filter excludes them. The other filter keys apply after scoring.

Composite score formula:

```
score = 0.4 * relevance + 0.3 * importance + 0.2 * recency + 0.1 * effective_confidence
```

### Context Assembly (`memory_context`)

The [`memory_context` interface](../../src/butlers/modules/memory/tools/context.py)
assembles sections in the order below and omits empty sections. Its
`token_budget` is converted to a hard character budget using four characters
per token, not a model tokenizer. The preamble, section headers, and the
content-blind `withheld: N` privacy receipt all consume that same total budget.

| Section | Character allocation | Ordering and selection |
|---|---|---|
| Profile Facts | 20% | Owner-authored owner facts by importance descending, creation time descending, then ID ascending |
| Task-Relevant Facts | 35% | Recall facts excluding profile duplicates, by composite score descending, creation time descending, then ID ascending |
| Active Rules | 20% | Owner-authored or endorsed rules only; maturity rank descending, effectiveness descending, creation time descending, then ID ascending |
| Recent Episodes | 15% | Newest first; opt-in with `include_recent_episodes=True` |
| Fleet Knowledge | 10% | Opt-in with `include_fleet_knowledge=True`; other butlers' catalog entries under the server-held catalog read policy |

The first three sections have explicit stable tie breakers. Recent episodes
are ordered by creation time without a secondary ID sort. These are section
quotas totaling 100% across all five sections, including the two opt-in
sections. Enabling Fleet Knowledge does not add an extra quota beyond the hard
total budget. Catalog search failure omits that optional section instead of
failing assembly.

The retired memory design proposed a shared `TokenBudgeter` abstraction and a
uniform tie-breaking rule for every section. Those implementation proposals are
not present in the current interface and are not recorded here as completed
requirements. The [successor map](../archive/README.md) distinguishes the
retired design from maintained requirements and outstanding delivery work.

## Consolidation

A background worker consumes eligible episodes in deterministic batches and emits
new facts, rules, supersessions, confirmations, and provenance links. Successful
episodes become `consolidated`; failures are retried after bounded backoff or
become `dead_letter` when the retry budget is exhausted. Consolidation output is
schema-validated before persistence.

**Authority partitions (bu-dhloi1.1, bu-dhloi1.26).** After the fenced claim,
each `(tenant_id, butler)` group is split by the episodes' persisted
`content_authority` into an owner-class partition, a `system` partition and a
non-owner partition (`third_party`, `mixed` and NULL). `system` is kept apart
from third-party content because a `system` fact renders without attribution: a
shared session could restate a stranger's words citing only a system episode and
the fact would lose its label. Each nonempty partition gets its own runtime
session, request ID and allowed episode set, and its dedup context lists only
facts and rules of the same partition (filtered in SQL before the `LIMIT`). That
dedup context is the only stored memory the session sees: the spawner skips
recall-based memory-context injection for `schedule:consolidation` sessions, and
the MCP wrappers suppress every tool call such a session makes
(`consolidation_session_tool_policy`), reads and writes alike, since consolidation
is output-only. The owner and system sessions therefore have no path to
third-party content they could restate under their own authority. The suppression keys on the session's
`trigger_source` parameter the spawner writes into the session's MCP server URL.
Inside the persistence fence the executor revalidates every source episode's stamp
against its partition and admits `updated_facts` targets and confirmations only
within the same tenant, source butler and partition. Evidence citing an episode
outside the partition fails that partition through the ordinary failed/dead-letter
path before any fact, rule, link or completion write; the sibling partitions still
commit independently, and valid third-party evidence stays `third_party`. Each
successful partition writes its own `public.consolidation_runs` row, and the run's
`groups_consolidated` counts partitions.
The row is inserted as the daemon's own `butler_<name>_rw` role; the table is
append-only for runtime roles (SELECT and INSERT, no UPDATE or DELETE; core_308).

The partitions stop new laundering; they do not reclassify facts consolidated
before this change. The historical demotion the owner adopted (bu-7ry1sy q1) is an
operator-only job, `scripts/memory_authority_demotion.py` over
`butlers.modules.memory.authority_demotion`. It is never scheduled or exposed as a
tool or API. It reads one memory schema plus an operator-supplied evidence manifest,
classifies owner-class facts and rules as candidate, excluded, unchanged, UNKNOWN or
endorsement anomaly, and by default writes nothing. An apply bound to an exact
approved dry run changes only `content_authority` to `mixed`, disowns the catalog row
and appends a `content_authority_demoted` receipt per row, atomically per schema.
Missing historical evidence is UNKNOWN and blocks apply. No live dry-run, demotion or
restore has been run or authorized; see the
[authority demotion runbook](../operations/memory-authority-demotion.md).

The dashboard owner can recover an eligible dead-letter episode with
`POST /api/memory/episodes/{episode_id}/requeue`. The owner guard and Origin/CSRF
checks run before target resolution. Recovery is confined to the owning source's
memory schema; Chronicler's private `chronicler_mem` schema is excluded. One
conditional transaction changes `dead_letter` to `pending`, resets
`consolidation_attempts` to `0`, clears error, retry, terminal-reason and lease
fields, and records one sanitized lifecycle event. A competing request or a
non-dead-letter episode receives a conflict without a second recovery event.
The response means queued for a later ordinary schedule: it does not execute
consolidation, run a schedule immediately, or expose bulk replay or an MCP
recovery tool.

The active [memory honesty change](../../openspec/changes/memory-honesty-last-mile/tasks.md)
also requires a future partitioned obligation-goal implementation. That conversion
and its switch remain open; the recovery and dashboard changes do not establish
goal-runtime delivery.

## Decay and Hygiene

A scheduled sweep computes effective confidence for facts and rules:

- Above retrieval threshold: normal (active).
- Below retrieval threshold but above expiry threshold: `fading`.
- Below expiry threshold: `expired` or `retracted`.

For facts, `fading` is recorded on the `validity` column itself (not merely a
`metadata.status` key) — every reader of the fading count (the dashboard API,
the `memory_stats` MCP tool) queries `validity = 'fading'` directly. Fading
facts are still live: recall/search, entity-scoped reads, the catalog
backfill, and `store_fact`'s supersession lookup all continue to include
them, and the sweep re-evaluates `validity IN ('active', 'fading')` on every
run so a fading fact can recover to `active` (e.g. after `memory_confirm`) or
progress to `expired`. Rules have no `validity` column and keep using
`metadata.status = 'fading'`.

Rules' terminal soft-delete state — the equivalent of a fact's `expired` /
`retracted` validity — is the boolean `metadata->>'forgotten'` flag, set by
`memory_forget` and by the decay sweep once a rule's effective confidence
falls below its expiry threshold. Unlike fading, there is no separate
"forgotten" column to add: a rule has exactly two liveness states (live, or
forgotten), not a multi-value lifecycle, so a boolean JSONB flag is the
right-sized representation. Every reader that reports rule
counts or lists rules — the dashboard API (`GET /api/memory/stats`'s
`candidate_rules`/`established_rules`/`proven_rules`/`anti_pattern_rules`,
including the "Proven rules" KPI; `GET /api/memory/rules`; the
`GET /api/memory/inspect?kind=rule` search bar), the `memory_stats` MCP tool,
and the recall/search/consolidation/catalog-backfill internals — filters on
`(metadata->>'forgotten')::boolean IS NOT TRUE` so a forgotten rule never
counts as a live belief or shows up in a browse/search list. `GET
/api/memory/rules` accepts an explicit `?forgotten=true` override for
auditing forgotten rules; `GET /api/memory/rules/{id}` (fetch by ID) and
`GET /api/memory/activity` (the chronological creation feed) are intentionally
unfiltered, mirroring how a fact stays fetchable by ID regardless of
validity.

Anti-pattern detection: rules with repeated harmful, low-effectiveness outcomes transition to `anti_pattern` status and are surfaced as warnings rather than guidance.

Episode cleanup removes expired rows and enforces capacity limits starting with the oldest consolidated rows. Before an episode is deleted, a database trigger records a content-free `episode_tombstones` row. Durable facts and rules retain their source UUID, and a generic `memory_links` endpoint names the deleted episode as `expired`; neither path retains raw episode content or authorizes a historical cleanup drain.

### HNSW recall and churn observability

`memory_ann_observability` is a daily, module-default deterministic job for
the local HNSW indexes on `episodes`, `facts`, and `rules`. Its aggregate
result is recorded in the scheduled task's `last_result`; it does not add an
MCP tool or send a proactive notification.

The monitor deliberately trades coverage for live safety. It uses a tiny,
bounded physical-page sample only when PostgreSQL estimates that the complete
searchable corpus is at or below 2,000 rows and its heap has at most 1,024
pages. The physical-page cap prevents a stale row estimate from admitting a
broad exact scan. It then compares forced-HNSW
top-k results with exact top-k results under short read-only, lock, and
statement-timeout guards. Larger tables report `recall.status = degraded` and
`reason = corpus_exceeds_exact_row_cap` (or
`corpus_exceeds_exact_page_cap`) rather than issuing a production-wide exact
scan. A table with too few live rows reports `no_data`.

Every local table still reports non-sensitive churn and statistics freshness:
estimated rows, dead-tuple ratio, modified-since-analyze ratio, cumulative
write counters, and analyze/vacuum timestamps. Recall below 0.90 is a warning;
below 0.85 is critical. Dead-tuple or modified-since-analyze ratios of 10% and
25% are warning and critical thresholds respectively. Operators should inspect
query configuration or plan offline maintenance from those signals; the job
never runs `VACUUM`, `REINDEX`, or any write.

`public.memory_catalog` is intentionally excluded: it is the separate,
IVFFlat-backed discovery index rather than a local production memory table.

## Shared Discovery Catalog

When `enable_shared_catalog = true` (the module default), `store_fact`/`store_rule` write a searchable summary row to `public.memory_catalog` — a cross-butler discovery index (see `openspec/specs/memory-discovery-catalog/spec.md`). The catalog is a discovery index, not a canonical store: full retrieval always routes back to the owning butler's schema via the row's `(source_schema, source_table, source_id)` provenance pointer.

**Read authority.** Each butler holds its catalog ceiling in its DB-backed
`runtime_config.catalog_read_sensitivity` value, seeded on first boot from
`[butler.runtime_seed].catalog_read_sensitivity`; the default is `normal`. The
supported authority tiers are `normal`, `internal`, and `confidential`.
`internal` maps to the existing stored sensitivity values `normal` + `pii`; it
does not add a new sensitivity value. Both `memory_catalog_search` and the core
`memory_catalog_fetch` tool load this same server-held policy. Neither tool has
a caller-controlled sensitivity argument. A fetch above the held ceiling
returns only `{"status":"withheld","reason":"sensitivity"}`; it does not
return content, the stored classification, or provenance beyond what the
caller already supplied.

`memory_catalog_fetch` accepts the three provenance fields returned by search,
resolves the catalog row's owning butler, and asks that butler's existing
`memory_get` tool through the Switchboard route. It never grants cross-schema
database access. Inactive or missing pointers return `{"status":"not_found"}`.
Both tools are read-only and idempotent.

**Content authority.** Each catalog row carries the `content_authority` class of its source fact or rule (`owner`, `owner_device`, `third_party`, `system`, `mixed`, or NULL for unstamped legacy rows; core_299, REQ-module-memory-013). Only the class is public — the sender entity stays in the owning schema. Every catalog write (`store_fact`/`store_rule` write-behind, rule endorsement, the backfill) copies it in the same statement as the rest of the row, and `run_memory_catalog_backfill` re-copies it onto rows whose value drifted (for example rows cataloged before the column existed); a NULL source stays NULL. `search_catalog` and `GET /api/memory/catalog/search` return it per row; `memory_catalog_search` adds an `attribution` (`from a third party`, `partly from a third party`, or `source unverified`; none for owner/system content), and Fleet Knowledge lines show the same label.

**Atomic disownment.** Every path that disowns a canonical memory — `memory_forget` (plain and correction-driven), the decay sweep's terminal expiry transition (facts -> `expired`, rules -> `metadata.forgotten`), and `purge_superseded_facts` — cascades a matching catalog-row disownment (`confidence = 0`, `invalid_at` set) in the **same transaction** as the state change, so a crash between the two can never leave the catalog serving a memory the canonical store has already retracted. `store_fact`'s own supersession cascade remains a separate, best-effort, eventually-consistent write-behind (outside the write transaction) — that path predates and is unrelated to the disownment guarantee above. Fading transitions never cascade: fading facts stay live for retrieval per the memory-retention-policy spec.

Unqualified daemon disownment cascades resolve `source_schema` from the caller connection's own `current_schema()`; butler pools connect with `search_path = <schema>, public`, so this matches the schema where the row was cataloged. Dashboard callers that qualify a private memory relation pass that owning schema explicitly through the canonical write, correction event, and projection cascade; the shared function never infers it from its pinned search path.

**Purge semantics.** A purged fact's catalog row is marked stale, not deleted — butler roles hold no `DELETE` grant on `public.memory_catalog` (catalog GC is centralised), so a purge-triggered delete isn't even possible from a butler's own role.

**Consolidation.** `execute_consolidation`/`run_consolidation` accept `enable_shared_catalog`/`source_schema` and forward them to every `store_fact`/`store_rule` call, so consolidation-derived facts/rules are cataloged exactly like directly-stored ones.

### IVFFlat filtered-recall measurement

The live semantic retrieval contract is [`_catalog_semantic_search`](../../src/butlers/modules/memory/search.py): it orders `public.memory_catalog` by `embedding <=> query_embedding` after applying `tenant_id`, `invalid_at IS NULL`, optional `memory_type`, and the server-held sensitivity policy. In this catalog, the user-facing notion of a category is the persisted `memory_type` filter (`fact` or `rule`); there is no separate category column.

An operator can measure possible IVFFlat candidate shortfall without changing the live retrieval path, index, or database settings:

```bash
uv run python -m butlers.modules.memory.catalog_measurement \
  --vectors-json ./catalog-measurement-vectors.json \
  --memory-type fact \
  --limit 10
```

Run it only from an operator-controlled environment whose standard `POSTGRES_*`/`DATABASE_URL` configuration already targets the intended database. When `DATABASE_URL` is set, its database path is the target and must be present. Without `DATABASE_URL`, the command uses `POSTGRES_DB`, defaulting to `butlers`. The JSON file is a top-level list of precomputed, 384-dimensional numeric vectors with finite components representable as pgvector float32 values; it must not contain natural-language queries and is never echoed in the command output. `--max-sensitivity` is a visibility ceiling (default `normal`), resolved inclusively with the same helper as live retrieval. The command returns aggregate-only JSON: filter population, result counts, overlap/recall, latency, whether the planner selected `idx_memory_catalog_embedding`, and safe lifecycle/index/table statistics. Equal-distance results at the limit are treated as rank-equivalent, so arbitrary tie ordering cannot look like recall loss. It never returns catalog text, provenance, row IDs, or vectors.

[`measure_catalog_ivfflat`](../../src/butlers/modules/memory/catalog_measurement.py) uses one repeatable-read PostgreSQL read-only transaction **per vector**, plain `EXPLAIN (FORMAT JSON)`, and the same filters as the live query. Each snapshot contains that vector's candidate count, approximate query, plan observation, and (when under the cap) exact comparison, keeping the comparison coherent while the live catalog changes without retaining one snapshot for the whole vector batch. The exact reference is skipped before it runs when the filtered population exceeds the hard 50,000-row cap. A transaction therefore contains at most four read statements; a run is further bounded to 25 vectors, `limit <= 50`, and a 10-second client-side timeout per database operation. It does **not** issue DDL/DML, `SET`, `ANALYZE`, `VACUUM`, `REINDEX`, or any pgvector/index tuning command. The maintenance observations are read-only snapshots, not maintenance work.

The command can inform a later proposal but cannot authorize tuning. A proposal requires at least 20 observations for which both the exact comparator completed and the named IVFFlat index was planned, plus either mean recall@limit below 0.98 or a candidate-shortfall rate of at least 10% with p95 shortfall of at least one result. Re-run in a separate window and review the aggregate evidence before considering any change. This is catalog IVFFlat evidence only; it is deliberately separate from HNSW production-table work.

## Entity Resolution

The `memory_entity_resolve` tool maps ambiguous name strings to stable entity identities using a 4-tier waterfall: role match -> exact (canonical or alias) -> prefix/substring -> fuzzy (edit distance <= 2). Context boosting from graph neighborhood and caller-provided `context_hints` refines scoring.

Entities are never hard-deleted. Merging sets `metadata.merged_into`; the source entity is tombstoned and excluded from future resolution.

`memory_entity_merge` is a compatibility surface, not a second merge
implementation. It dispatches to Relationship's merge authority. Local memory
references are rebound from `public.entity_rebind_log`; each memory-enabled
daemon handles only its own schema, reacts to live `entity.rebound.v1` events,
establishes its listener before replaying pending receipts on startup, and
re-establishes the listener plus replay after a retained connection failure.

## Database Tables

The module owns tables in the hosting butler's schema (Alembic branch: `memory`):

- `episodes` -- session observations with TTL
- `episode_tombstones` -- content-free deletion evidence for durable episode provenance
- `facts` -- durable SPO knowledge with entity anchoring
- `rules` -- procedural memory with maturity tracking
- `memory_links` -- provenance edges between memory artifacts
- `memory_events` -- append-only audit stream
- `rule_applications` -- per-application outcome records
- `embedding_versions` -- model/version tracking
- `predicate_registry` -- predicate vocabulary with enforcement flags
- `knowledge_gaps`, `knowledge_gap_origins` -- unanswered owner questions and the threads that asked them

Entity identity tables live in the `public` schema: `public.entities`.

## Dependencies

None. The memory module is a leaf module with no dependencies on other modules.

## Implementation Notes

- Fact and rule dashboard details read persisted `content_authority` and `authority_entity_id`;
  rule endorsement reads `endorsed_at` and `endorsed_by`. Canonical entity names are resolved
  independently of a fact's subject. Request IDs never identify an author, a system stamp does
  not imply scheduled execution, and NULL stamps do not establish record age. The held-guidance
  link opens the existing rules register with `held=true`; source-episode availability retains
  its separate link/expired/unresolved presentation.

- Generated inverse gap closure stays on the writer's existing connection. Reserve both actual
  database advisory keys before any property supersession locks; do not replace the reservation
  with separate closure connections, text/Python hash ordering, or post-commit work. A skipped
  temporal inverse has no new UUID to cite. No migration, grant or schema change is needed.

- Importing Memory storage or tools leaves `sentence_transformers`, `transformers` and `torch`
  unloaded. `EmbeddingEngine` pays that dependency/model cost at first construction; registered
  async tools acquire it with `asyncio.to_thread`. The per-model synchronized cache, default/configured
  model and 384 dimensions remain unchanged. `_helpers.EmbeddingEngine` is the construction fake seam;
  storage/search/embedding/search_vector aliases are canonical module objects, not file copies.

- Catalog read authority is server-held in each schema's
  `runtime_config.catalog_read_sensitivity` (`normal|internal|confidential`). MCP search and fetch
  expose no caller ceiling. Cross-butler canonical fetches route through Switchboard to the owning
  butler's `memory_get`; never add cross-schema SELECT or a caller-parameterised SECURITY DEFINER
  shortcut.
- `/api/memory/*` fans out across the butler pools that carry memory tables and never requires
  `db.pool("memory")`. Pools without memory tables are skipped, so a deployment with none returns
  empty payloads (404 for ID lookups), not 503. `/api/memory/reembed/pending` skips non-memory
  schemas such as `chronicler`, whose `episodes` table has no `embedding` column.
- `store_fact` fills `source_butler` from runtime context and reuses the session's canonical
  episode when `source_episode_id` is omitted. A writer that bypasses it (bulk SQL, scheduled jobs)
  must pass `source_butler` or call `resolve_write_provenance(...)`, or the facts UI shows blank
  provenance.
- Tool metadata must describe list-only inputs with valid and invalid examples
  (`tags=["x"]`, `types=["fact"]`, never `types="facts"`), and `memory_search.types` / `.mode` are
  typed as `Literal` so the MCP schema exposes enums. The same applies to `notify.request_context`,
  which must be an object, never a JSON string.
- Entity merge tombstones the source (`metadata.merged_into`), hides it from list, search and
  `entity_resolve`, re-links `public.contacts.entity_id`, and unions only the source's `aliases`
  (not its `canonical_name`) onto the target. Dunbar enrichment keeps the highest `dunbar_score`
  across contacts sharing an `entity_id`.
- Entity-dedup curation treats any `pending`, `approved`, `rejected` or `abandoned` merge row for
  the ordered pair as existing state; retention keeps rejected and abandoned decisions, and curation
  never retries or mutates an approved or abandoned merge.
- `memory_ann_observability` measures only the local HNSW tables through the module's own pool, so
  private schemas stay isolated. Exact recall runs only below a 2,000-row estimate and a 1,024-page
  cap with local timeouts; otherwise it reports degraded or no-data and never runs maintenance.
- Entity-dedup curation keys `pending_actions.deduplication_key` as
  `relationship:entity-dedup:<source>:<target>`; `approvals_013` makes it unique for live lifecycle
  states while keeping NULL historic rows. The surviving target is the earliest `(created_at, id)`.
- `store_fact` defaults `sensitivity` to `'normal'`, so the failure mode is a domain write path that
  never passes one. There is no predicate-to-sensitivity map: each domain passes its own
  classification (`roster/health/tools/_helpers.py::HEALTH_SENSITIVITY_CONFIDENTIAL`). Audit new
  `store_fact`/`store_rule` call sites for an implicit `'normal'`.
- `memory_search`, `memory_recall` and `memory_get` closures pass one server-held
  `CatalogReadPolicy` from the owning daemon's `runtime_config.catalog_read_sensitivity`; a private
  memory schema never substitutes its own row. `memory_get` applies the ceiling inside its
  `UPDATE ... RETURNING`; `memory_context` reserves preamble and `withheld: N` text before fitting
  Profile Facts; an explicit higher sensitivity filter raises `SensitivityAuthorizationError`.

## Related Pages

- [Module System](module-system.md)
- [Knowledge Base](knowledge-base.md) -- entity data model and predicate vocabulary
- [Approvals Module](approvals.md) -- approval gates for memory mutations

### Explicit private-schema disownment

Dashboard callers can use a domain pool while passing `memory_schema` for an
owned private memory namespace. Forget and rule retirement carry that explicit
schema through canonical writes and catalog/graph provenance. Correction audit
rows are inserted into that same private namespace. The additive keyword-only
`source_schema` argument to `cascade_fact_retraction` provides the same contract
for external callers with qualified canonical SQL. Only omitted schemas resolve
`current_schema()` on the caller connection; an explicit schema never falls
back to its pool's search path. Canonical, audit and mandatory projection changes
remain in the same transaction, so a projection failure rolls them all back.

The mandatory cascade and catalog-only supersession write-behind use the shared
`public.invalidate_memory_projection(text,text,uuid[],timestamptz,boolean,boolean)`
function installed by core_315. Its invoker authority preserves the caller's
transaction and underlying table privileges. Graph invalidation is mandatory for
disownment; postcommit supersession remains catalog-only and best effort. The
optional final redaction flag additionally clears copied catalog summary, title,
embedding and search data; it requires graph invalidation. The primitive never
changes canonical memories or deletes catalog rows. Ordinary retraction retains
historical text. Exact provenance prevents changes to other source schemas or tables.

Core_314 supports managed-bootstrap and migration-only installs using explicit
public schema usage, catalog SELECT/UPDATE, graph SELECT/DELETE and exact function
EXECUTE grants for existing canonical runtime roles. PUBLIC and connector execution
are not granted. Bootstrap replay applies those grants when a canonical role is
created later; managed bootstrap's existing broader public-table grants remain a
separate deployment property. Missing permissions fail the mandatory transaction.
A schema-local downgrade retains this shared boundary and additive grants for
other schema/runtime consumers. No deployment can run the new helper before the
core revision is installed.


### Lifecycle transition timestamps

`facts.dropped_at` records entry into superseded/expired; `rules.forgotten_at`
records entry into the existing forgotten=true state. These nullable fields
have no default or legacy backfill. Duplicate terminal writes preserve the old
time, including NULL; initial forgotten rules use their insertion time. They do
not replace bitemporal `invalid_at` or rule-retirement `retired_at`.

The memory-access seven-day integer counts those two timestamps plus episode
`expires_at` inside one database-clock inclusive seven-day window. Future/NULL
times are excluded, so legacy terminal history can be undercounted. Existing
canonical/catalog/graph rollback boundaries remain; store_fact catalog
write-behind and quick-fact/gift separate pool calls do not acquire broader
atomicity from these fields.
