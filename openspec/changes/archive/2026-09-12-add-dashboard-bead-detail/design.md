## Context

The dashboard already consumes a host-exported Beads JSONL file through a
single read-only bind mount. That is deliberately the only Beads material
available to the container: the live Dolt service, `bd`, tracker credentials,
and the rest of `.beads/` are outside its trust boundary. The Decisions digest
uses that export for a narrow summary, but a decision or blocker currently has
no safe, same-origin drill-down.

The raw export is not an API contract. It can contain notes, metadata,
comments, identities, credentials, arbitrary URLs, and other fields that the
dashboard must never materialize. A detail route must also distinguish a
missing record from an untrustworthy source: a 404 is meaningful only after a
fresh, readable snapshot was fully checked.

## Goals / Non-Goals

**Goals:**

- Serve one Bead detail from the existing read-only export only.
- Centralize bounded snapshot health assessment and safe projection so an API
  route never serializes a raw record.
- Make freshness visible on successful and unavailable responses.
- Let Decisions and escalation blockers navigate only to same-origin
  `/beads/:id` detail routes.
- Render the allowed `external_ref` as inert text, never as a link.

**Non-Goals:**

- No `bd`, Dolt, GitHub, database, credential, or external-network bridge.
- No tracker mutation, Beads lifecycle mutation, or database migration.
- No generic snapshot browser, raw JSON endpoint, pagination, search, or
  access to non-allowlisted fields.
- No attempt to infer missing dependency, timestamp, type, or metadata values.

## Decisions

### Bounded safe projection is the only detail representation

`BeadSnapshotReader` will stat and fully parse the mounted JSONL under fixed
file, record, line, string, label, and dependency limits. It constructs a
safe-record index and returns a `BeadDetail` model whose fields are explicitly
enumerated: `id`, `title`, `status`, `priority`, `type`, `description`,
`design`, `acceptance_criteria`, `labels`, safe timestamps,
`dependencies`, and `external_ref`. It will never return the parsed mapping
or accept a caller-selected field list.

Dependency summaries are resolved only from other safe records in the same
snapshot and are capped at 20 direct `depends_on_id` records. Each summary has
only `id`, `title`, `status`, `priority`, and `type`; raw edges, relation
metadata, and transitive traversal remain internal and unavailable.

This keeps raw parsing isolated while retaining the source's current
read-only deployment shape. A generic raw reader or an endpoint that passed
through source keys was rejected because a later serializer or UI could leak a
newly-added export field by accident.

### Source availability is determined before not-found

The reader considers the snapshot available only after it is a regular file,
its mtime is no more than 14 days old, and its bounded full parse completes.
Missing, stale, oversized, unreadable, or malformed snapshots result in
`503 BEAD_SNAPSHOT_UNAVAILABLE`; the standard `ErrorResponse.error.details`
always includes `export_as_of` when the file mtime was known. Only an available
snapshot that lacks the requested ID results in `404 BEAD_NOT_FOUND`.

Full parsing before lookup is intentional: returning a record found before a
malformed later line would incorrectly represent the snapshot as readable.
The `export_as_of` timestamp is the source file mtime, not a synthetic refresh
claim.

### Same-origin navigation is constructed, not sourced

The frontend generates all Bead links with
`/beads/${encodeURIComponent(id)}` and uses React Router `Link`; it never
uses a snapshot value as an `href`. The Decision row preserves its existing
keyboard selection button while adding an explicit same-origin detail link;
blocker references use the same helper. The detail page presents
`external_ref` with plain text semantics.

This rejects direct external tracker links: they would make the dashboard's
trust, availability, and credential behavior depend on a source that this
feature does not own.

### Detail UX is a semantic, rule-separated read surface

`/beads/:id` uses the established detail page shell, a record-identity title,
semantic sections, visible export-as-of information, native links and retry
controls, and no mutation affordances. Its body uses rules and spacing rather
than Card chrome. Loading, not-found, and unavailable conditions remain
distinct; an unavailable snapshot never becomes a calm absent-record state.

## Risks / Trade-offs

- **A snapshot can age between polling intervals** → The server checks the
  mtime on each request, responds 503 at the existing 14-day boundary, and
  surfaces `export_as_of` on every known source state.
- **A malicious or unexpectedly large export can exhaust request resources**
  → Fixed byte, record, line, field, label, and dependency bounds fail closed
  as source-unavailable before a response is built.
- **Dependency edges can reference missing records** → A bounded summary keeps
  the safe ID with null optional summary facts; it never invents a title or
  follows another source.
- **The source can contain sensitive text in otherwise allowed fields** → The
  product scope explicitly allows the named Bead-authored fields only; all
  other raw fields are non-materialized and privacy tests sentinel their
  absence.

## Migration Plan

1. Deploy the additive route and frontend route with no schema or data change.
2. The existing read-only compose mount remains the sole source; no deployment
   configuration, credential, or tracker change is required.
3. Rollback removes the additive routes and links. It does not alter the
   snapshot, Beads state, database, or external system.

## Open Questions

None. The owner-approved Option B fixes the source, field allowlist,
unavailability posture, and navigation boundary for this change.
