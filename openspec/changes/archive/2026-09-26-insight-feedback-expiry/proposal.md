## Why

The proactive broker infers engagement globally, offers no explicit owner feedback,
and silently drops expired unseen candidates from its attention evidence. One noisy
category can therefore quiet every domain while expiring time-sensitive insights
leave no durable trace.

## What Changes

- Add useful, bounded not-now, and reversible never feedback through MCP, REST,
  delivered-message metadata, and the existing dashboard insight row.
- Shape selection by per-category engagement weights under the unchanged global cap.
- Record expired unseen candidates in the attention ledger and prefer equal-priority
  candidates that cannot survive until the next regular cycle.

## Out of Scope

Producer and scan coverage, generic notification routing, new verbosity controls,
and live/external activation do not change.
