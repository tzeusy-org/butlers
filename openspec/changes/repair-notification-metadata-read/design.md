## Context

The completed `fix-notification-jsonb-metadata-write` change corrects the
production Switchboard writer at
`roster/switchboard/tools/notification/log.py`: it passes a JSON-safe mapping
to the registered asyncpg JSONB codec instead of pre-serializing it. That
change intentionally did not reinterpret historical rows.

`NotificationSummary.metadata` is an object-or-null API field used by the
global notification list, the butler-scoped list, and the mark-read response.
The current shared reader accepts mappings and converts every other decoded
value to `null`. A legacy JSONB string scalar therefore loses an encoded object
even though its original string can be interpreted safely once. The
Switchboard-specific migration chain owns the underlying `notifications`
relation; it must remain the only repair path for historical data.

The deployment topology also matters: the local checkout, an image tag, and a
running process can disagree, especially when a dev hotreload container has a
bind source. A merged commit is consequently not evidence that the process
which can call the notification writer is fixed.

## Goals / Non-Goals

**Goals:**

- Return a truthful, stable object-or-null metadata shape on every notification
  response path.
- Preserve an unrecoverable legacy string exactly under `_raw` instead of
  silently presenting recoverable data as absent.
- Repair only the bounded historical Switchboard candidate set after deployed
  writer evidence proves that no active writer can recreate it.
- Keep migration and operational evidence diagnosable without logging raw
  notification metadata.

**Non-Goals:**

- Amend, broaden, or rerun the completed write-side OpenSpec change.
- Change delivery, retries, status/effective-status semantics, timeline
  projection, session/trace/request provenance, frontend display, or
  notification authorization.
- Add a JSONB object constraint, a batch-loop repair, a manual database update,
  or a runtime restart/deployment action to this planning change.

## Decisions

### D1 — Normalize exactly one legacy JSON layer at the API boundary

The shared notification response normalizer is the sole reader for all three
`NotificationSummary` construction paths. It applies this deterministic table:

| Decoded database value | API `metadata` value |
| --- | --- |
| mapping/object | a shallow object copy |
| `null` | `null` |
| string whose one JSON parse yields an object | that parsed object |
| malformed string, or a string whose one parse yields an array, string, number, boolean, or `null` | `{"_raw": <the original outer string>}` |
| non-string non-object value (for example, an actual JSONB array, number, or boolean) | `null` |

The normalizer never recursively parses a decoded result, manufactures
provenance, or changes unrelated response fields. Returning `_raw` for a
legacy string is intentional compatibility: it represents the exact stored
outer value, not a best-effort schema inference.

**Alternatives considered:** keeping every non-object as `null` preserves the
old API behavior but continues to fabricate absence for recoverable encoded
objects. Allowing arbitrary JSON values would break the established
object-or-null model contract. Recursive parsing would turn ambiguous text
into a hidden data transformation.

### D2 — Keep the documentation contract and response construction aligned

`docs/frontend/backend-api-contract.md` will describe the table above and name
all three response paths. The API model remains object-or-null; no frontend
display change is required for this slice. The direct normalizer and endpoint
tests use the same cases so the global list, butler-scoped list, and mark-read
response cannot drift apart.

**Alternative considered:** documenting only the list endpoint. Rejected
because the same stored row can be returned by a butler-scoped query or
mark-read mutation and would otherwise have contradictory shapes.

### D3 — Repair is one guarded, transactional Switchboard migration

The future implementation adds the next Switchboard-chain migration, not a
core migration or an operator SQL recipe. It captures one migration-start
cutoff and updates only rows in the target Switchboard `notifications` relation
whose `created_at` is before that cutoff and whose metadata is a JSONB string.
Mappings, `null`, and ordinary non-string non-object JSONB values remain
untouched along with every other column.

The migration uses the target schema's normal unqualified relation under the
Switchboard migration search path, with an explicit absent-relation guard. If
that relation is absent, it records an aggregate no-op and succeeds; this keeps
core-only or partially initialized databases safe. When present, one atomic
set-based update converts one-layer encoded objects to objects and stores all
malformed/non-object string cases as `{"_raw": <original string>}`. It does
not batch, manually replay, or emit raw values. Any migration evidence is
aggregate-only: cutoff/candidate-band bounds and counts of converted and
`_raw`-preserved rows, never message bodies, recipients, identifiers, or
metadata payloads.

The one-layer parser is explicit and exception-safe. A session-local `pg_temp`
PL/pgSQL helper receives only an eligible JSONB string's outer text and attempts
exactly one `::jsonb` parse. It catches `invalid_text_representation` (SQLSTATE
`22P02`) for malformed inner JSON and returns SQL `NULL`; it does not catch
unrelated errors. The set-based update consumes that one result: a parsed object
replaces `metadata`, while a parse failure or any parsed non-object produces
`jsonb_build_object('_raw', <original outer text>)`. Thus malformed input is a
deterministic `_raw` case rather than a transaction abort, with no recursive
parse. Keeping the helper in `pg_temp` avoids adding a durable function to the
Switchboard schema, and any error outside the malformed-input parse path still
rolls back the atomic repair.

The downgrade is intentionally a data no-op. Canonicalized objects and `_raw`
fallbacks are not re-encoded into ambiguous legacy strings, so downgrade cannot
recreate the corrupted representation or claim a reversible historical repair.

**Alternatives considered:** a JSONB object constraint would turn a legacy-data
repair into an unrelated write policy and can reject valid historical shapes.
Batch updates and manual SQL lack the transaction/replay boundary and leave
partial-state ambiguity. A hard failure on an absent relation makes ordinary
upgrade topologies unsafe.

### D4 — Authorize historical repair from the serving writer, not repository state

Historical repair is conditional on a documented, read-only deployment gate.
Before the repair migration is allowed to run, an operator must establish all
of the following evidence:

1. Enumerate every active process that can reach the Switchboard notification
   writer, including the actual container/process identity, image digest or
   runtime revision, command, and any bind-mounted source.
2. Prove each serving source is #3458 (`7d2bea3bc`) or a descendant, including
   a source/image relationship check. A host checkout, branch name, or merged
   pull request alone is insufficient.
3. Verify the active Switchboard migration frontier and record only aggregate
   candidate-band counts and bounds. No evidence command may print raw
   metadata, messages, recipients, or row identifiers.
4. Observe a bounded post-deploy window in which no new string-shaped metadata
   appears and the historical candidate band does not grow.

Missing evidence, a stale process/image/bind source, a migration-frontier
mismatch, or any new string-shaped row is an external deployment blocker. It
must stop the workflow before a historical mutation; it is not a condition to
work around with manual SQL. The gate is deliberately separate from the writer
regression: the regression proves the source behavior, while the gate proves
the code actually serving production writers.

**Alternative considered:** checking only the merge SHA or image tag. Rejected
because a long-lived container, an old image cached under a tag, or a bind mount
from another worktree can keep the unfixed writer live.

## Risks / Trade-offs

- **[Risk]** `_raw` exposes a legacy value that was previously hidden as
  `null`. **Mitigation:** it is returned only through the existing notification
  API surface; operational evidence and migration notices remain aggregate-only
  and never print payloads.
- **[Risk]** a malformed legacy value could abort an all-or-nothing migration.
  **Mitigation:** the one-layer parser catches only the malformed-input
  `22P02` case and maps it deterministically to `_raw`; unrelated migration
  failures still roll the transaction back.
- **[Risk]** a writer remains stale after the source fix merged. **Mitigation:**
  the repair gate fails closed on actual process/image/mount evidence and a
  bounded clean observation window.
- **[Risk]** a database has not initialized the Switchboard relation. **Mitigation:**
  the absent-relation guard produces a deliberate aggregate no-op rather than
  an upgrade failure.

## Migration Plan

1. Implement and test the API normalizer and update the backend API contract;
   deploy it with the already-merged writer correction.
2. Run the read-only serving-writer gate. Record the process/image/bind-source
   proof, migration frontier, aggregate candidate bounds, and clean observation
   window in the operational handoff.
3. Only after that gate succeeds, deploy the guarded Switchboard migration via
   the normal migration runner. Do not issue a manual update or an ad hoc
   replay.
4. Record aggregate post-migration evidence: zero pre-cutoff JSONB string
   candidates and no new string-shaped rows in the observation window.
5. On a deployment or evidence failure, stop before repair and report the
   external blocker. After repair, retain canonical data; the migration
   downgrade is intentionally a no-op and must not be used to reconstruct
   strings.

## Open Questions

None. The scope fixes the reader matrix, writer-evidence gate, migration
boundary, and explicit non-goals.
