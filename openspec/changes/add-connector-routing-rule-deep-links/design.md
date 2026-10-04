## Context

Connector Detail lists exact ingestion-rule ids. Filters already reads active and archived rules but the active read only requests enabled rules. The design preserves the existing route and editing controls.

## Decisions

- Use `encodeURIComponent` for each rule id and the router query parser for decoding. Match full ids by equality, never name, prefix, or CSS selector interpolation.
- Read the complete nonarchived set, including disabled rules. A successful active read can resolve a present target independently; archived resolution waits for both successful reads and expands the archived section.
- Treat missing/malformed data, degraded-source metadata, and a total exceeding returned rows as incomplete. Only complete active and archived reads establish absence. Failed refreshes do not authorize stale targets.
- Reset a focus receipt only when the decoded target changes (including removal). Reveal and focus in component effects after the exact row mounts. Use instant scrolling, semantic groups, a linked-rule label, and the existing focus token without suppressing the global outline.
- Retain a focus receipt through refetches, retries, and repeated navigation to the same id. A new target receives one focus transfer; returning to a previous id after another target is a new transfer.

## Scope and rollback

Navigation never opens an editor or invokes toggle, restore, delete, connector, credential, or runtime mutations. Rollback removes the query target handling and link query strings; stale shared URLs continue to fail honestly while the implementation is present. No changes to the foreign run-12 worktree or ResearchTracker.

## Verification

Extend the existing connector link test and add five Filters behavior cases: active exact/disabled target, delayed archive reveal, complete absence, unavailable/incomplete reads and retry, and target-change/refetch stability. Run focused Vitest, frontend lint/Knip/build, planner, guards, strict OpenSpec and scratch composition in both archive orders. Independent review and terminal hosted CI are coordinator handoff gates.
