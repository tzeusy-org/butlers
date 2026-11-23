"""Task scheduler package: cron-driven task dispatch with TOML sync.

Import from the submodule that owns a symbol; this package re-exports nothing.

- ``timing``: cron, timezone, stagger and quiet-window arithmetic
- ``rows``: row-field validation, normalization and JSONB coercion
- ``prompt``: scheduled-prompt preparation, continuity block, completion hooks
- ``sync``: ``[[butler.schedule]]`` TOML-to-DB synchronization
- ``claim``: allowance deferral, QA policy, butler dispatch gate
- ``dispatch``: ``tick()`` orchestrator
- ``deadline_pass`` / ``chain_pass`` / ``deferred_pass``: tick passes 1, 3 and 4
- ``crud`` / ``update``: runtime schedule create/list/delete and update/toggle
"""
