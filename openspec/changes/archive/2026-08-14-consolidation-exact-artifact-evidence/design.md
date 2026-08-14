## Context

The consolidation prompt currently labels batch episodes by ordinal only. The
parser emits facts, updates, and rules without source evidence, and the
executor links each durable artifact to every claimed episode after its write.
That is unable to distinguish evidence from coincidental batch membership. It
also makes a failed link write able to leave the artifact committed.

The memory module already has a group-level retry/dead-letter path in
`run_consolidation`, a persisted-target lookup for updated facts, and durable
episode tombstones for later provenance readers. Those contracts are live and
remain outside this change.

## Goals / Non-Goals

**Goals:**

- Require exact source-episode evidence for every fact and rule emitted by a
  non-empty consolidation group.
- Reject invalid evidence before any group artifact write, using the existing
  group failure and retry path.
- Make an artifact write and its exact `derived_from` links atomic.
- Preserve the existing updated-fact target and stale-target behavior.

**Non-Goals:**

- No retention, cleanup, tombstone, backfill, migration, dashboard, API,
  relationship, or owner-approval changes.
- No new provenance table or parallel evidence representation.
- No change to confirmation semantics; confirmations are not new durable
  artifacts and therefore do not carry episode evidence.

## Decisions

### D1: Use `evidence_episode_ids` on fact and rule output entries

The prompt will render each claimed episode UUID and require a non-empty
`evidence_episode_ids` JSON array on `new_facts`, `updated_facts`, and
`new_rules`. The existing `memory_links` relation remains the durable
many-to-many evidence record; storing an array column or one arbitrary
`source_episode_id` would either require schema work or lose multi-source
provenance.

### D2: Validate the complete result before executing any action

The parser preserves each artifact's supplied evidence value so the executor
can validate it against the claimed group. Before any fact, rule, link, or
episode state write, the executor requires an evidence array that is non-empty,
contains only UUID strings, has no duplicate normalized UUIDs, and is wholly
contained in the group’s claimed episode IDs. An invalid value raises a
sanitized evidence-validation error; `run_consolidation` already catches group
errors and invokes `_mark_group_failed`, so no new failure lifecycle is needed.

Validating in the parser alone was rejected because membership is known only at
executor time. Treating malformed entries as ordinary parser warnings was
rejected because warnings currently permit partial persistence and terminal
consolidation.

### D3: Use an outer transaction per artifact with the existing storage APIs

For a valid artifact, the executor will acquire one connection and start one
outer transaction. A connection-backed pool adapter will route `store_fact`,
`store_rule`, and `create_link` through that same connection, allowing existing
storage validation and the #3488 optimistic supersession guard to remain
authoritative. The executor creates only the artifact’s validated evidence
links inside that transaction. A link failure therefore rolls back the artifact
and every link for that artifact before the normal per-action error isolation
continues.

### D4: Keep existing target and provenance behaviors unchanged

Updated facts still reload identity from their persisted target and classify
missing or stale targets as action-local skips. Evidence validation happens
before that action loop, while valid evidence does not alter target lookup.
The #3643 tombstone contract remains a reader/deletion concern: this change
only writes ordinary `memory_links` to live claimed episodes.

## Risks / Trade-offs

- [Older model output lacks evidence] → The group retries through its existing
  backoff/dead-letter lifecycle rather than recording unproven knowledge.
- [Nested storage transactions are needed] → The connection-backed adapter
  keeps all nested savepoints on one outer transaction; focused regressions
  prove the artifact is rolled back when evidence-link persistence fails.
- [Evidence IDs are model-controlled] → UUID, uniqueness, and claimed-group
  membership checks prevent foreign or fabricated rows from being linked.

## Migration Plan

1. Deploy the prompt, parser, and executor together so newly spawned sessions
   receive the required output contract.
2. Existing pending groups with old-format output fail safely and retry; no
   historical rewrite or backfill occurs.
3. Rollback restores the prior prompt/parse behavior. It does not alter
   already-created fact, rule, or provenance rows.

## Open Questions

None. The existing `memory_links` representation and group failure path supply
the required durable evidence and lifecycle semantics.
