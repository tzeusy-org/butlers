## Why

Episode cleanup currently deletes the source row for durable facts and rules.
Their foreign keys silently set `source_episode_id` to null while generic
`memory_links` can retain an unresolved episode id. The resulting dashboard
looks like a source-less fact or exposes a door to a deleted episode, making
retention loss indistinguishable from never having provenance.

The owner has authorized this bounded provenance repair before any historical
retention cleanup. It must preserve durable derived evidence without retaining
raw owner-message content beyond the episode's retention period.

## What Changes

- Preserve the identifier of an expired source episode for durable facts and
  rules, and record a minimal, content-free tombstone when an episode is
  deleted.
- Make generic `memory_links` to a deleted episode resolve as `expired`,
  rather than as a live or silently missing source.
- Expose typed episode-source availability through memory API responses and
  render expired sources as truthful non-clickable provenance in detail and
  register surfaces.
- Keep raw episode content, prompts, runtime output, credentials, and raw
  owner-message bodies out of the retained provenance record.
- Do not run, schedule, or design a historical retention drain in this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-memory`: durable source-episode provenance and generic link semantics
  across episode deletion.
- `memory-retention-policy`: episode cleanup's evidence-preserving deletion
  precondition and explicit non-goal for historical drains.
- `dashboard-api`: typed, truthful source-episode availability on memory
  resources and links.
- `dashboard-domain-pages`: expired source provenance has no dangling
  navigation affordance.

## Impact

Affected systems are the memory module schema and cleanup path, memory storage
and link readers, dashboard memory API models/projections, and memory detail
and register React surfaces. The change adds no dependency, MCP tool, direct
operator SQL workflow, or bulk retention operation.
