## Why

Fact and rule detail pages must answer who supplied the content using the server's existing authority stamps. Facts currently omit these fields from the dashboard read model; rules omit their recorded endorser. A held-guidance link also needs a working filtered destination.

## What Changes

- Project persisted fact authority and rule endorser fields; resolve canonical author/endorser names independently of a fact's subject.
- Render a shared source-attribution line on both existing detail routes without deriving authorship from request IDs or inventing a device, schedule, or record age.
- Wire the existing held-rule query through the API client and URL-backed rules register, with a visible way to clear it.
- Preserve existing episode availability, lifecycle mutations, failure states, and retained tests.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-domain-pages`: record the approved memory-detail attribution and held-destination behavior.

## Impact

Read-only dashboard API projections, memory detail views, the rules register filter, and new-file behavior tests. No schema, admission, endorsement, budget, or runtime authority change. Foreign PR #4829 owns separate paging/degradation work; this change preserves its public API fields and does not adopt its unlanded implementation.
