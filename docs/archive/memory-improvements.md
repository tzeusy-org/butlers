> **ARCHIVED** — This document is historical. It was archived on 2026-03-21.
> **Reason:** Historical improvement notes — changes have been incorporated.

# Memory System Project Plan

Audience: Claude Opus 4.6 operating as an implementation agent inside this repository.
Status: proposed implementation skeleton.
Scope: memory module only, with small core ACL/runtime adjustments where required.

---

## How to use this document

Treat this as the execution skeleton for implementation work, not as a high-level essay.

Implementation rules:

1. Prefer **additive, reversible changes** before destructive cleanup.
2. Preserve existing public MCP tool names unless adding aliases.
3. When repository docs and live code conflict, use this precedence order:
   1. **DB/runtime isolation model** from `README.md`, `src/butlers/db.py`, and `alembic/versions/core/core_001_target_state_baseline.py`
   2. **Target behavior** from `docs/modules/memory.md`
   3. **Backward-compatibility constraints** from the live module/tool surface in `src/butlers/modules/memory/`
4. Do **not** introduce a dedicated shared memory daemon/service.
5. Do **not** allow direct cross-butler SQL reads into another butler schema’s memory tables.
6. Canonical memory remains **local to the hosting butler schema**. Any shared cross-domain index is discovery-only.
7. Keep all work incrementally testable. A migration without tests is incomplete.

---

## Problem statement

The repository already has a substantial memory subsystem:
- a normative target-state spec in `docs/modules/memory.md`
- a live memory module with 18 MCP tools
- local tables for `episodes`, `facts`, `rules`, links, and events
- entity tooling, temporal facts, consolidation, hybrid search, and decay sweeps

However, the live implementation is **not yet aligned** with the target-state architecture. The biggest issues are:

1. **Schema drift** between the target spec and live tables/code.
2. **Entity registry drift** between migrations and tool implementations.
3. **Lineage/tenant context missing** from most memory rows and tools.
4. **Retrieval ranking drift** (`effective_confidence` exists, but recall still uses raw confidence).
5. **Consolidation correctness drift** (state machine and schema fields do not line up cleanly).
6. **Retention is too simple** for a personal Jarvis spanning many domains.
7. **Context assembly is too lightweight** for long-lived agent sessions.

This plan closes those gaps while preserving the repo’s architectural intent.

---

## Current-state observations that matter for implementation

### Good foundations already present
- Runtime topology targets **one PostgreSQL database with per-butler schemas plus `shared`**.
- Runtime search path is schema-scoped as `own_schema,shared,public`.
- The memory module already exposes a broad MCP surface and has working storage/search/consolidation code.
- The target-state memory spec is unusually detailed and can serve as the main behavioral contract.

### Important drifts to fix first
1. **Entity storage location mismatch**
   - `mem_002` creates an unqualified `entities` table.
   - `tools/entities.py` reads/writes `shared.entities`.
   - `storage.py` validates `entity_id` / `object_entity_id` against `shared.entities`.
   - `tools/entities.py` expects a `roles` column that `mem_002` does not create.

   Decision: **`shared.entities` becomes authoritative**. Do not keep multiple canonical entity tables.

2. **Tenant / request lineage missing from core memory tables and tools**
   - The target spec requires tenant-bounded reads/writes and optional `request_context`.
   - Current entity tools take `tenant_id`, but episodes/facts/rules/search/context mostly do not.

   Decision: add lineage everywhere, derive defaults from request context, and keep direct `tenant_id` exposure mostly for admin/debug tooling.

3. **Retrieval contract mismatch**
   - `search.py` defines `effective_confidence(...)`.
   - `recall(...)` still filters/ranks using raw `confidence` rather than decayed confidence.

   Decision: read-path ranking must use `effective_confidence` consistently.

4. **Consolidation state mismatch**
   - The target spec requires deterministic batching and exactly one terminal state.
   - The live consolidation path groups by `butler` only and queries `rules.status`, but the schema uses `maturity`, not `status`.

   Decision: rework consolidation into a lease-based state machine.

5. **Context assembly is not yet target-state quality**
   - Current `memory_context` is a simple recall + render step with rough `chars / 4` budgeting.
   - The target spec requires deterministic sections, ordering, and quota-aware budgets.

   Decision: turn context assembly into an explicit deterministic compiler.

6. **Retention policy is too coarse**
   - Episodes default to 7-day TTL.
   - Facts/rules decay, but domain-aware retention classes do not exist.

   Decision: add row-level retention classes and config-driven policies.

---

## End-state goals

The end-state memory system should provide:

1. **Local canonical memory per butler schema**
   - `episodes`, `facts`, `rules`, `memory_links`, `memory_events`, and supporting tables.

2. **Shared identity plane**
   - `shared.entities` is authoritative and safe to reference from local facts.

3. **Tenant-aware and request-aware reads/writes**
   - every durable memory row can be traced to a tenant and optionally a request.

4. **Deterministic context assembly**
   - stable, budgeted memory snippets for Claude/Codex agent sessions.

5. **Deterministic consolidation lifecycle**
   - pending → leased → consolidated/failed/dead_letter, with retries and audits.

6. **Retention classes and decay**
   - different periods and policies by memory type and relevance.

7. **Optional cross-domain discovery**
   - a shared search catalog that points back to canonical local memory, without becoming a shared source of truth.

---

## Non-goals

- No separate memory microservice.
- No direct cross-butler reads from one butler schema into another butler schema’s memory tables.
- No replacement of operational KV state with memory artifacts.
- No large MCP surface expansion for marginal features before correctness is fixed.

---

## Core architectural decisions

### 1) Canonical memory is local
Each butler owns its own memory rows in its own schema.

### 2) Entities are shared
Use `shared.entities` as the single authoritative entity registry.
Facts in local schemas reference shared entity IDs.

### 3) Shared search is discovery-only
If implemented, `shared.memory_catalog` contains summarized, searchable cards that point back to canonical memory rows.
It does **not** become the source of truth.

### 4) Request context is a first-class internal primitive
Introduce a canonical internal request context shape:

```python
class RequestContext(BaseModel):
    tenant_id: str = "owner"
    request_id: str | None = None
    session_id: str | None = None
    caller: str | None = None
    route: str | None = None
    metadata: dict[str, Any] = {}

Rules:

runtime agent flows derive tenant/request context from authenticated execution context

admin/debug tools may still pass tenant explicitly

models should not be trusted to invent tenant IDs

5) Shared writes must be explicit and narrow

Because current core ACLs mostly make shared read-only to runtime roles, shared writes must go through one of:

narrowly granted table privileges on explicit shared tables, or

security-definer SQL functions / a small internal publisher path

Preferred end state:

local schema tables: direct RW by owning butler

shared tables (shared.entities, optional shared.memory_catalog): narrow, explicit write surfaces only

Target architecture
Data-plane split

Local per-butler schema

episodes

facts

rules

memory_links

memory_events

rule_applications

embedding_versions

Shared schema

shared.entities

shared.entity_info / related shared identity tables already present in repo

optional shared.memory_catalog

Core features to implement
Feature 1: authoritative shared entity registry
Requirements

shared.entities is authoritative.

facts.entity_id and facts.object_entity_id reference shared.entities(id).

entity tools read/write shared.entities only.

support roles TEXT[] because current entity tools already expect it.

keep tombstoning via metadata (e.g. merged_into) until/unless a dedicated tombstone column is introduced.

Why this is first

The live code already assumes shared.entities; the migration history does not fully match that assumption.
Fixing this early removes a core integrity risk.

Feature 2: tenant-aware and request-aware memory rows
Requirements

Add lineage fields across the memory subsystem:

tenant_id

request_id

retention_class

sensitivity

These belong on:

episodes

facts

rules

memory_events

rule_applications

optional shared.memory_catalog

Desired behavior

default tenant for personal Jarvis use: owner

writes always stamp tenant

reads filter by tenant by default

request context is preserved in audits/events

Feature 3: deterministic context assembly
Requirements

Replace the current memory_context implementation with a deterministic assembler:

sectioned output

budgeted output

stable ranking and tie-breakers

optional recent episodes

reusable section quotas

Default sections

Profile Facts

Task-Relevant Facts

Active Rules

Recent Episodes (optional)

Default ordering

Within each section:

score DESC

created_at DESC

id ASC

Default budgeting

Introduce a TokenBudgeter abstraction.

Phase 1 implementation:

deterministic conservative estimator

one centralized helper used everywhere

Do not keep ad hoc token_budget * 4 logic spread across files.

Feature 4: correct retrieval scoring and filtering
Requirements

use effective_confidence on the read path

support structured filters

make time-aware filtering first-class

keep hybrid retrieval as the default

Minimum supported filters

scope

entity_id

predicate

source_butler

time_from

time_to

retention_class

sensitivity

Scoring

Use the target-state formula:

score = 0.4*relevance + 0.3*importance + 0.2*recency + 0.1*effective_confidence

Important:

min_confidence should apply to effective_confidence, not raw confidence

recency should use last_referenced_at or another explicit anchor, not arbitrary implicit behavior

Feature 5: lease-based consolidation state machine
Requirements

deterministic ordering within (tenant_id, butler)

bounded batch size

retry scheduling

exactly one terminal state per episode

audit emission on every transition

Target states

pending

leased

consolidated

failed

dead_letter

Notes

The existing boolean consolidated can remain temporarily for compatibility, but the state machine should become authoritative.

Feature 6: retention classes and policy-driven cleanup
Goal

Support selective retention for a personal Jarvis spanning many domains.

Proposed retention classes

scratch

episodic

operational

personal_profile

health_log

financial_log

rule

anti_pattern

Suggested initial policy defaults

scratch: 3–14 days

episodic: 30–90 days

operational: long-lived, decay-driven, no hard delete by default

personal_profile: long-lived

health_log: long-lived, time-series aware

financial_log: long-lived, time-series aware

rule: long-lived

anti_pattern: retain until explicitly retired

Keep policy definitions in module config first. A DB-backed policy table is optional future work.

Feature 7: richer temporal fact semantics
Requirements

Keep valid_at, and add explicit support for fact invalidation / interval semantics.

Minimum additions:

invalid_at

observed_at

idempotency_key

Behavior

valid_at IS NULL: property fact

valid_at IS NOT NULL: temporal fact

temporal facts can coexist

duplicate temporal writes should be blocked by an idempotency mechanism

stale property facts should supersede cleanly

This is especially important for health, finance, routines, and longitudinal memory.

Feature 8: optional shared discovery catalog

This is a phase 3 feature, not a prerequisite for correctness.

Purpose

Enable cross-domain discovery without violating schema isolation or creating a shared source of truth.

Behavior

each butler can publish searchable summary rows to shared.memory_catalog

catalog rows contain only summary text and provenance pointers

full recall still routes back to the source butler/module

Important constraint

Because shared writes need explicit ACL handling, this feature should be gated behind a config flag until the local memory core is correct.

Proposed schema end state
Local schema tables
episodes

Required columns after cutover:

id

tenant_id

request_id

butler

session_id

content

importance

consolidated (legacy compatibility)

consolidation_status

consolidation_attempts

last_consolidation_error

next_consolidation_retry_at

leased_until

leased_by

dead_letter_reason

retention_class

sensitivity

created_at

expires_at

metadata

facts

Required columns after cutover:

id

tenant_id

request_id

entity_id (FK to shared.entities)

object_entity_id (FK to shared.entities)

subject

predicate

content

scope

validity

confidence

decay_rate

permanence

source_butler

source_episode_id

supersedes_id

valid_at

invalid_at

observed_at

idempotency_key

retention_class

sensitivity

created_at

last_confirmed_at

last_referenced_at

metadata

tags

rules

Required columns after cutover:

id

tenant_id

request_id

content

scope

maturity

confidence

decay_rate

effectiveness_score

applied_count

success_count

harmful_count

source_butler

source_episode_id

retention_class

sensitivity

created_at

last_applied_at

last_evaluated_at

last_confirmed_at

last_referenced_at

metadata

tags

memory_events

Expand this beyond the minimal current table.
Suggested columns:

id

event_type

actor

actor_butler

tenant_id

request_id

memory_type

memory_id

payload

created_at

rule_applications

Track rule usage independently from aggregate counters.
Suggested columns:

id

tenant_id

request_id

rule_id

session_id

outcome

notes

created_at

embedding_versions

Track active embedding model / re-embed migrations.
Suggested columns:

id

model_name

dimensions

is_active

created_at

Shared schema tables
shared.entities (authoritative)

Required columns:

id

tenant_id

canonical_name

entity_type

aliases

roles

metadata

created_at

updated_at

optional shared.memory_catalog

Suggested columns:

id

tenant_id

source_schema

source_table

source_id

source_butler

memory_type

title

search_text

embedding

search_vector

entity_id

object_entity_id

predicate

scope

valid_at

invalid_at

confidence

importance

retention_class

sensitivity

created_at

updated_at

Migration plan

Use new corrective migrations. Do not rewrite old migrations in place during the first pass.

mem_013_shared_entities_alignment.py
Goals

create/repair shared.entities

add missing roles column if absent

backfill/migrate any local entities rows into shared.entities

repair fact foreign keys to point at shared.entities

optionally replace local entities tables with compatibility views or drop them after backfill

Notes

This migration is mandatory because the live code already assumes shared.entities.

Backfill strategy

ensure shared.entities exists

ensure required columns exist, including roles

copy local entities rows into shared.entities

remap any fact/entity references if IDs must change

repair constraints / indexes

remove or deprecate local entities

mem_014_memory_lineage_additive.py
Goals

Add lineage and policy columns without dropping old ones yet.

Add columns

tenant_id

request_id

retention_class

sensitivity

Backfill defaults

tenant_id = 'owner'

retention_class

episodes → episodic

facts → operational

rules → rule

sensitivity = 'normal'

Indexes

Add tenant-scoped indexes for hot query paths.

mem_015_consolidation_leases.py
Goals

Make consolidation state explicit and safe.

Add columns

consolidation_attempts

last_consolidation_error

next_consolidation_retry_at

leased_until

leased_by

dead_letter_reason

Compatibility rule

Do not drop retry_count / last_error immediately. Backfill into new columns, cut code over, then clean up later.

mem_016_temporal_validity_and_idempotency.py
Goals

Make temporal facts safer and more expressive.

Add columns

invalid_at

observed_at

idempotency_key

Constraints / indexes

partial uniqueness for property facts stays DB-enforced

add uniqueness on (tenant_id, idempotency_key) where key is not null

add time-series indexes for active temporal fact families

mem_017_memory_events_enrichment.py
Goals

Upgrade auditability.

Add columns to memory_events

request_id

memory_type

memory_id

actor_butler

New tables

rule_applications

embedding_versions

mem_018_optional_shared_memory_catalog.py
Goals

Introduce discovery-only shared search.

Preconditions

local canonical memory is already correct

shared ACL / write surface decision is implemented

publishing is feature-flagged

mem_019_legacy_cleanup.py
Goals

Remove compatibility scaffolding only after cutover is stable.

Candidates for cleanup

retry_count

last_error

legacy metadata flags that were replaced by canonical lifecycle states

local compatibility entities tables/views if no longer needed

Code change map
src/butlers/modules/memory/__init__.py
Implement

additive optional request_context params on read/write/context tools

additive retention_class / sensitivity for write tools where appropriate

preserve current memory_* tool names

if desired, add aliases later, but do not break the current surface

Important

Keep entity tool names backward-compatible (memory_entity_*).
The spec can be updated later to acknowledge the prefixed tool names if needed.

src/butlers/modules/memory/tools/writing.py
Implement

parse and validate optional request_context

stamp lineage and retention into writes

pass new fields to storage layer

preserve old function signatures as default-safe

src/butlers/modules/memory/storage.py
Implement

authoritative shared entity FK behavior

tenant/request lineage on writes

retention class persistence

fact invalidation / idempotency support

memory event emission helpers

rule application recording helpers

compatibility shims for old columns during cutover

Also fix

any remaining assumptions that entities are local rather than shared

any raw metadata['forgotten'] lifecycle semantics that should normalize to canonical states

src/butlers/modules/memory/search.py
Implement

tenant-bounded retrieval

effective-confidence filtering and scoring

structured filters

time-window filtering

stable tie-breakers

Important

Refactor recall() so that it uses decayed confidence consistently.

src/butlers/modules/memory/tools/context.py
Implement

deterministic context assembler

section quotas

centralized token budgeting

optional recent episode inclusion

stable formatting

Suggested rendering format

Keep it compact and easy for model consumption. Prefer a fixed, repeatable markdown structure.

src/butlers/modules/memory/consolidation.py
Implement

lease acquisition

bounded deterministic batching

(tenant_id, butler) shard ordering

retry/dead-letter policy

event emission

Fix immediately

remove or replace any use of rules.status in favor of the actual rule lifecycle fields

src/butlers/modules/memory/consolidation_executor.py
Implement

transition episodes to terminal states explicitly

emit memory events for each write / confirmation / failure

preserve source episode provenance

support idempotency and duplicate suppression where needed

src/butlers/modules/memory/tools/entities.py
Implement

align fully to authoritative shared.entities

add tests around roles, merge/tombstone behavior, and tenant scoping

optionally move shared writes behind helper functions if ACL hardening is implemented

alembic/versions/core/
Minimal required work

Adjust core ACL/runtime setup so the chosen shared write model is possible.

Preferred options:

narrow grants on approved shared tables, or

security-definer functions with EXECUTE granted to runtime roles

Do not broadly grant RW over the entire shared schema.

Retrieval and context design details
Composite score inputs

relevance: hybrid search score normalized to [0,1]

importance: normalized importance field

recency: deterministic function of last_referenced_at (or explicit chosen anchor)

effective_confidence: exponential decay from last_confirmed_at

Minimum viable memory_search improvements

Support:

memory_search(
    query,
    types=None,
    scope=None,
    mode="hybrid",
    limit=10,
    min_confidence=0.2,
    filters={
        "entity_id": "...",
        "predicate": "...",
        "source_butler": "...",
        "time_from": "...",
        "time_to": "...",
        "retention_class": "...",
        "sensitivity": "...",
    },
    request_context=None,
)
Minimum viable memory_context behavior

Use a deterministic section plan such as:

45% budget: facts

30% budget: rules

25% budget: recent episodes

If a section has insufficient content, its budget can roll forward to the next section.
The reallocation algorithm must also be deterministic.

Retention and lifecycle policy
Recommended initial policy behavior
Episodes

default retention class: episodic

default TTL from config

unconsolidated rows are protected from capacity cleanup

cleanup prefers deleting expired and oldest already-consolidated rows

Facts

profile/operational facts are usually decay-driven, not hard-deleted immediately

temporal logs can keep much longer windows, especially health/finance

superseded facts are retained for provenance unless an explicit archival policy says otherwise

Rules

candidate/established/proven rules decay over time

harmful repeated rules convert into anti_pattern

anti-patterns should be retained longer than normal rules by default

Optional cross-domain discovery design

This is only for after the local memory core is correct.

Publishing model

Each butler may publish a search card to shared.memory_catalog containing:

summary text

embedding

source schema/table/id

source butler

memory type

entity handles and predicate when applicable

time validity when applicable

Retrieval model

Switchboard or another routed tool searches the shared catalog

returned hits are only discovery pointers

full memory retrieval still routes to the owning butler/tool

Why this fits the repo

This preserves local ownership while enabling global discovery across domains.

Testing plan
Must-have migration tests

fresh install applies full chain successfully

upgrade from current live schema succeeds

shared.entities alignment works even if a local entities table exists

tenant columns are backfilled correctly

consolidation columns backfill without data loss

temporal idempotency constraints behave correctly

legacy cleanup migration is safe after cutover

Must-have storage tests

writing an episode/fact/rule stamps tenant and retention correctly

property facts supersede correctly

temporal facts coexist correctly

duplicate temporal writes are suppressed

invalid shared entity IDs are rejected

Must-have retrieval tests

recall uses effective_confidence, not raw confidence

tenant isolation is enforced

time-window filters behave correctly

stable tie-breakers produce deterministic ordering

context assembly remains deterministic under the same inputs

Must-have consolidation tests

leasing prevents duplicate processing

retries reschedule correctly

terminal states are exclusive

failed rows can dead-letter after max attempts

fact/rule outputs keep source episode provenance

Must-have entity tests

entity creation/update hits shared.entities

roles round-trip correctly

merge/tombstone metadata is preserved

tenant-scoped entity queries behave as expected

Phased implementation order
Phase 1: correctness foundations

mem_013_shared_entities_alignment.py

mem_014_memory_lineage_additive.py

storage/tool updates for lineage

retrieval fix to use effective_confidence

immediate consolidation bugfix (rules.status mismatch)

Phase 2: lifecycle and context quality

mem_015_consolidation_leases.py

consolidation worker/executor refactor

deterministic memory_context

structured search filters

retention-class-aware cleanup

Phase 3: temporal and audit richness

mem_016_temporal_validity_and_idempotency.py

mem_017_memory_events_enrichment.py

rule_applications

embedding_versions

Phase 4: optional cross-domain discovery

shared ACL/write-surface decision

mem_018_optional_shared_memory_catalog.py

publisher helper path

switchboard/global search integration

Phase 5: cleanup

remove compatibility shims

mem_019_legacy_cleanup.py

tighten docs to match final implementation exactly

Acceptance criteria

The project is complete when all of the following are true:

shared.entities is the sole authoritative entity table used by live memory code.

All durable memory writes stamp tenant_id and support request_id.

Retrieval and min-confidence semantics use effective_confidence.

Consolidation is lease-based and reaches exactly one terminal state per processed episode.

memory_context is deterministic, sectioned, and budgeted.

Retention classes exist and are respected by cleanup behavior.

Temporal facts support invalidation/idempotency semantics safely.

Tests cover migration correctness, tenant isolation, retrieval determinism, and consolidation invariants.

Optional shared discovery remains discovery-only and does not bypass local ownership.