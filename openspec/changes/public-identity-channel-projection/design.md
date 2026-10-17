## Context

Evidence baseline: `3bde6119914e066414451d3df609a8c20e84fa30`.
The owner applied option D on 2026-10-14: Relationship writes a public identity
projection and core resolution reads only public. Earlier pending-template
text is not the final decision. Current source still uses private SQL and
`public.resolve_owner_triple` as a private-schema definer bridge; this source
candidate neither replaces those readers nor asserts deployed equivalence.

RFC 0004 Amendment 4 is the authoritative detailed candidate. This file records
source allocation, alternatives and review obligations rather than a second
independent schema design.

## Goals / Non-Goals

Goals: define the exact public row contract, source-owned atomic write-behind,
least-privilege readable handles, deletion/failure/backfill/recovery semantics,
and preserve every existing identity/authorization behavior through cutover.

Non-goals: implementation; migration/RFC renumbering; new credentials or role
custody; live grants/DDL/backfill/restore; graph-projection changes; generic
private SQL exceptions; new authentication, candidate-adoption or uniqueness
policy; a new handle-enumeration API; changes to the held deployment decision.

## Decisions

1. Keep facts canonical in Relationship; expose only derived eligible channels.
   Source UUID is the replay key. Preserve canonical values and duplicate facts
   so normalized cross-entity ambiguity remains visible. Public entities supply
   current names/roles/liveness, not copied role snapshots.
2. Make maintenance mandatory in the source transaction through a source-table
   row trigger. A trigger-only, fixed-schema function owned by the existing
   Relationship role performs derived writes. This narrowly scoped definer
   supports existing public-entity FK cascades under other roles without
   granting those callers projection DML or a callable foreign-read bridge.
   An invoker-only trigger was considered but would refuse legitimate cascade
   deletion when the deleting runtime lacks projection DELETE. No new role,
   service credential or actor-admission surface is introduced.
3. Use exact table ACL plus ENABLED/FORCED RLS and a table-specific bootstrap
   installer. Revocation only in a migration was rejected because current
   bootstrap broadly regrants public DML. Preserve RFC 0031's unrelated grant
   rider; it is not a settled authority for this new table.
4. Keep owner corroboration's full equivalent candidate universe and strict
   failure behavior, replacing the private definer lookup with public-only
   grouped resolution. Preserve existing primacy/context/dossier/parking
   behavior. Missing projected data never authorizes a private fallback.
5. Backfill uses bounded source-keyed batches, existing sorted entity/handle
   locking, a short NOWAIT source-table SHARE lock, population recheck and
   same-transaction effects. Busy/changed populations roll back and retry
   finitely. This explicitly pauses source writes for each short batch;
   unlocked stale-snapshot upserts/cleanup were rejected as unsafe. Actual
   time/batch limits and runtime contention proof belong to implementation.

## Current Source and Future Allocation

| Current source | Existing behavior / future owner obligation |
|---|---|
| `src/butlers/identity.py` | Exact, email/Telegram/WhatsApp normalization, bounded phone suffix, bulk grouping, merged/deleted negatives; public-only replacement must preserve them |
| `src/butlers/identity.py` owner corroboration | Full equivalent candidate set before owner filter; missing infrastructure and owner/non-owner collision refuse bypass; delete private-read fallback |
| `src/butlers/modules/pipeline.py`, `roster/switchboard/tools/identity/inject.py`, `src/butlers/core_tools/_routing.py` | Inbound entity-only routing, explicit transitory marker and durable unknown-sender notification boundary |
| `src/butlers/daemon.py`, `src/butlers/core_tools/_notifications.py` | Per-entity forward resolution, nullable primacy/oldest-first order and deterministic delivery |
| `src/butlers/modules/approvals/gate.py`, `src/butlers/modules/approvals/email_guard.py` | Owner dossier/standing rules/pending action; email context classification, not context-preference sorting |
| `src/butlers/connectors/discretion.py`, `src/butlers/connectors/telegram_bot.py` | Existing connector identity/discretion uses connector_writer and the canonical resolver; no enumeration API |
| `src/butlers/api/routers/ingestion_events.py`, Relationship identity tools | Actual runtime-pool identity consumers; Relationship's own domain API remains permitted to read its canonical private source |
| `roster/relationship/tools/`, mounted API and Relationship migrations | Existing source authority and candidate/adoption/merge/receipt semantics; trigger must cover all actual source mutation producers |
| `roster/relationship/modules/tools.py`, `roster/relationship/tools/stale_contacts.py`, `roster/relationship/jobs/relationship_jobs.py`, `roster/relationship/tools/relationship_assert_fact.py` | Existing resolver/tool/job consumers and transitory fact hook; core lookup remains public-only while admitted Relationship source writes remain private |
| `scripts/init-db.sql`, core/Relationship migration owners | Current broad grants cannot settle the new posture; table-specific finalizer, forced policies, real SET ROLE and bootstrap negatives required |

This inventory is source evidence, not proof that every future caller has been
migrated. `.4` must refresh complete producers/callers and ownership, including
static helper and dynamic mounted-tool paths; `.5` independently measures
foreign SQL. No source owner, migration number, protected policy or unrelated
active changeset is commandeered by this candidate.

## Required Preservation

The MODIFIED blocks retain every current scenario body: five inbound identity
scenarios and both triple-resolution scenarios. All other current requirement
bodies remain untouched, including candidate/legacy active eligibility, private
source authority and lifecycle witness, exact owner adoption and receipts,
transitory reservation/notification, role/merge rules and WhatsApp deltas.
The source predicate/name/shape outcomes remain; only the sanctioned lookup
substrate changes after verified cutover. A missing row during partial recovery
has an explicit negative rather than a claim that source presence proves parity.

RFC 0031 graph fields, eligible edges, withheld visibility and grant rider remain
byte-identical. RFC 0017 owner/context/authorization contract remains unchanged.
The least-privilege guarantee is within current trusted runtime/DB scope; shared
login membership, trusted admin, table owner and BYPASSRLS do not become process
or credential isolation. Public routing context is a minimal existing label,
not full metadata or authenticated owner evidence.

## Review and Verification Boundary

This PR requires native strict validation with actual parsed requirement and
scenario counts, complete scenario-body preservation/overwrite checks, precise
source ownership and unchanged protected-source hashes, links/diff hygiene and
repository static guards. These establish source coherence only; Tests: +0 ~0 -0.

Implementation requires the real migrated PostgreSQL chain and committed
readback of successful/injected-failure source writes, source/cascade deletion,
all mutation producers, candidate/adoption/replay, both race orders, exact-set
backfill parity, normalized/bulk/forward/owner/context reader parity, real-role
ACL/RLS/TRUNCATE negatives after bootstrap replay, core-only safety and bounded
contention/restore/rollback. Native source validation is not that evidence.
Independent review precedes the owner's merge/adoption; `.4` and `.5` retain
their existing dependency gates. Live data operations require separate authority.
