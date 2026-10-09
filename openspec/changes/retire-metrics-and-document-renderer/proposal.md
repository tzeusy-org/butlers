# Retire the undeclared Metrics and Document renderer modules

## Why

Owner bu-lsxqb0.5 option A was applied on 2026-10-04; doctrine PR4343 records both modules outside v1. This change carries that existing decision through source and native delivery without removing live core or connector instrumentation.

## What Changes

Both asynchronous instant and range HTTP query functions move unchanged to `butlers.core.prometheus`. The two dashboard API callers and five test patch bindings use that namespace. The two retired packages, their seven MCP declarations, exclusive module tests/docs and direct Markdown dependency are removed. Aggregate/cache/backlog/degraded behavior, existing connector metrics, stored metric catalogue state and rendered blobs remain unchanged.

The owned native change declares `schema: spec-driven` and `retire_capabilities: true`, removes all10 Metrics requirements/27 scenarios and both Renderer requirements/3 scenarios, and adds REQ-core-telemetry-015/seven scenarios. ONLY nine exact Metrics formatting separators are accounted in the retained guarded input/inverse; no normative text or arbitrary leftover is suppressed.

The ROOT-bound published a9 checkpoint174170f9500bc4bdc23ce962d38fc29c05812fa3 supplies the complete aggregate5REQ23SC and twelve preparation tasks (six checked/six unchecked). Its prospective union changes exactly two helper namespace tokens, with every foreign body/flag preserved. Native integration is serialized by ROOT against the actual current published/adopted artifact; no private WIP or foreign task completion is taken here.

## Delivery Status

This is SOURCE preparation. Ten countable tasks cover actual preparation, tests, read-only installed native rehearsal and genuine independent preparation review. Actual validated disposable application, own sync/archive/readback and fresh resulting-head normal/nonauthor/protected/squash remain mandatory outside that checkbox list. Preparation does not claim native retirement, current hosted delivery or whole completion. No stored-data cleanup, schema/role migration, deployment or renewed owner decision is authorized.
