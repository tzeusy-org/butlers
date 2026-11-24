# Memory Authority Demotion

> **Status:** built and tested against synthetic fixtures only. No dry run, apply,
> restore or delete has been run against any environment, and none is authorized
> by this document. Running it needs separate operational authorization.

Before consolidation was partitioned by content authority (bu-dhloi1.1, PR #5189),
one consolidation session could read an owner episode and a third party's episode
together, then stamp a fact or rule derived from both as `owner`. The owner decided
(bu-7ry1sy q1, 2026-10-20 14:33 UTC) to demote the owner-class facts and rules
consolidated after mem_013 by a butler that also consolidated third-party
episodes. They stay as `mixed`: kept, attributed, and no longer steering. That
decision authorizes the mechanism. It does not authorize running it against any
database, and it approves no count.

The job is `scripts/memory_authority_demotion.py`, over
`src/butlers/modules/memory/authority_demotion.py`. No daemon, scheduler, MCP tool
or API calls it.

## What it decides

The job reads one memory schema and an **evidence manifest** for that schema. Each
`facts`/`rules` row of a cohort `(tenant_id, source_butler)` gets one disposition:

| Disposition | When |
| --- | --- |
| `unchanged` | Not owner-class: already `mixed`/`third_party`/`system`, or NULL legacy (never guessed upward). |
| `excluded` | Outside the cohort, created before the mem_013 boundary, or at/after the partition-fix boundary. |
| `unknown` | Any evidence the decision needs is missing (see below). |
| `anomaly` | An owner-class rule that carries a genuine owner endorsement (`endorsed_at`). |
| `candidate` | Owner-class, inside both boundaries, affected butler evidenced, at least one `derived_from` link to an episode, and the manifest attests those links are consolidation-only. |

A row is `unknown` when:

- the manifest gives no evidence for the mem_013 or partition-fix install boundary
  (`mem013_boundary_unevidenced`, `partition_fix_boundary_unevidenced`);
- the cohort member has no evidence that it consolidated third-party episodes
  (`affected_butler_unevidenced`);
- the row has no `derived_from` link (`consolidation_derivation_unproven`). A
  direct owner write and a consolidation artifact whose evidence was never linked
  look identical;
- the manifest does not attest that `derived_from` links in this schema were written
  only by consolidation (`derivation_evidence_unattested`).

Apply refuses while any row is `unknown` or `anomaly`. An unknown is never counted
as clean, and a partial run is never reported as a complete disposition.

## Evidence manifest

One JSON file per memory schema. Unknown keys, a wrong version, an unsafe schema
name or a timestamp without a UTC offset are refused before anything is read.
`evidence` values are operator references (a receipt row, a deploy record, a
reviewed query), recorded so a reviewer can check them. A `null` boundary,
`evidence`, or `derivation_evidence` is allowed and yields `unknown` rows, so a dry
run still measures the unresolved remainder.

```json
{
  "manifest_version": 1,
  "predicate_version": "authority-demotion/v1",
  "schema": "general",
  "mem013_boundary": {"at": "2026-10-01T00:00:00+00:00", "evidence": "<reference>"},
  "partition_fix_boundary": {"at": "2026-11-02T00:00:00+00:00", "evidence": "<reference>"},
  "affected_cohort": [
    {"tenant_id": "shared", "source_butler": "general", "evidence": "<reference>"}
  ],
  "derivation_evidence": {
    "derived_from_links_are_consolidation_only": true,
    "evidence": "<reference>"
  }
}
```

The cohort is the owner's affected-butler cohort as recorded. Do not narrow it to
an invented time window or widen it beyond the record.

## Dry run (default)

```bash
printf '%s\n' "$DSN" | uv run python scripts/memory_authority_demotion.py \
  --manifest manifest.json > dry-run.json
```

Supply the DSN on stdin from a secret store, never as an argument or a logged
variable. The dry run is one read-only, repeatable-read transaction. It first
checks that the schema has every column it depends on (`unsupported_schema`
otherwise) and whether `public.memory_catalog` with `content_authority` exists
(`catalog: present`, or `not_enabled` when the catalog is absent). Its output holds
no memory text, sender names or credentials. It contains:

- per `tenant/butler/type` counts by `disposition:reason`;
- `unknown_count`, `anomaly_count` and `complete` (true only when both are 0);
- the candidate IDs, each with a preimage digest (row identity, authority, sender
  entity, timestamps, state, a content hash and its link count);
- `manifest_digest` and `candidate_digest`, which an approval must name.

## Approval

The owner must see the actual dry-run counts and approve that exact result. The
operator records the approval as:

```json
{
  "approval_version": 1,
  "schema": "general",
  "operation_key": "demote-general-<date>",
  "manifest_digest": "<from dry run>",
  "candidate_digest": "<from dry run>",
  "candidate_count": 42,
  "approved_by": "<reference to the recorded owner approval>"
}
```

`approved_by` is recorded in every receipt, but the code grants it no authority.
The approval holds only while the manifest digest, candidate digest and count still
match the database.

## Apply

```bash
printf '%s\n' "$DSN" | uv run python scripts/memory_authority_demotion.py \
  --manifest manifest.json --apply --approval approval.json
```

One transaction per schema:

1. A schema-scoped advisory lock serializes demotion runs.
2. If the `operation_key` already has an operation receipt, the original receipts
   are returned and nothing is written (`replayed: true`). A different approval
   under a reused key is refused (`operation_key_reused`).
3. Every cohort row is re-read under `FOR UPDATE` locks in id order and
   re-classified. It refuses on `unresolved_unknown`, `endorsement_anomaly`,
   `manifest_drift`, `candidate_count_drift`, `candidate_drift` (any preimage
   changed, for example by an owner correction or endorsement after the dry run) or
   `approval_schema_mismatch`.
4. Per candidate: a compare-and-swap sets `content_authority = 'mixed'` only if
   the prior authority is unchanged. Content, IDs, provenance, sender entity,
   sensitivity, validity and maturity are untouched. The matching
   `public.memory_catalog` row gets `content_authority = 'mixed'`. A fact stays
   searchable with its attribution label. A rule's row is also staled, because a
   demoted, unendorsed rule is held. Then one `content_authority_demoted`
   `memory_events` receipt is appended, carrying the prior authority, preimage
   digest, digests, approval reference and catalog effect.
5. One `content_authority_demotion_operation` receipt closes the operation.

A lost compare-and-swap, a failing catalog write or a failing receipt insert
rolls back the whole schema. An absent catalog is reported as `not_enabled`. A
present catalog without the authority column is refused
(`catalog_authority_unsupported`). Refusals exit 2 with a closed reason code and
commit nothing.

## Recovery boundary

There is no restore command, inverse promotion or delete. Rolling back this code
does not promote any `mixed` row. The receipts keep each row's prior authority and
preimage digest, so a later owner-controlled recovery (for example Vouch,
bu-jv1moa.14) can decide row by row. An owner who wants a demoted rule to steer
again endorses it through the existing endorsement path.

## Unresolved: historical evidence and operator approval

These are open. Nothing in the code or this runbook resolves them:

- **Install boundaries.** The memory schema does not record when mem_013 or the
  partition fix took effect per schema. mem_013's legacy receipt is conditional and
  count-only, and an Alembic revision carries no install time. An operator must
  evidence both boundaries per schema, or every row stays `unknown`.
- **Affected-butler membership.** `consolidation_runs` holds no tenant, schema or
  episode membership, successful consolidation clears leases, and
  `episode_consolidated` events carry no batch. Which `(tenant, butler)` pairs
  consolidated third-party episodes must be evidenced by the operator, or those
  rows stay `unknown`.
- **Derivation proof.** A `derived_from` link is the only in-database trace of
  consolidation, and today only the consolidation executor writes one. The operator
  must attest that this held for the schema's whole window. Rows with no link
  (including direct owner writes) stay `unknown`. Apply is therefore blocked for
  any schema that has owner-class rows without links in the window, until the owner
  records a disposition for that measured remainder. That disposition does not
  exist and is not inferred here.
- **Endorsement anomalies.** An endorsed owner-class rule blocks apply. Whether to
  demote it needs an explicit owner instruction.
- **Operator approval.** The adopted q1 decision approves the mechanism, not a
  count. Running even a dry run against a real database needs separate operational
  authorization, and an apply needs the owner's approval of that dry run's exact
  digests and count. Owner release bu-276pli permits scheduling only, not
  deployment or execution.
