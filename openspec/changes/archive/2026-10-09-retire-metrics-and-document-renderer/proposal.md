# Retire the undeclared Metrics and Document renderer modules

## Why

Owner bu-lsxqb0.5 option A was applied on 2026-10-04; doctrine PR4343 records both modules outside v1. This change carries that existing decision through source and native delivery without removing live core or connector instrumentation.

## What Changes

Both asynchronous instant and range HTTP query functions move unchanged to `butlers.core.prometheus`. The two dashboard API callers and five test patch bindings use that namespace. The two retired packages, their seven MCP declarations, exclusive module tests/docs and direct Markdown dependency are removed. Aggregate/cache/backlog/degraded behavior, existing connector metrics, stored metric catalogue state and rendered blobs remain unchanged.

The owned native change declares `schema: spec-driven` and `retire_capabilities: true`, removes all10 Metrics requirements/27 scenarios and both Renderer requirements/3 scenarios, and adds REQ-core-telemetry-015/seven scenarios. ONLY nine exact Metrics formatting separators are accounted in the retained guarded input/inverse; no normative text or arbitrary leftover is suppressed.

The c03e SOURCE preparation used ROOT-bound published a9 checkpoint174170f9500bc4bdc23ce962d38fc29c05812fa3 (aggregate5REQ23SC, twelve flags six checked/six unchecked); that witness remains dated provenance. Actual a9 now landed as e17c0ceb844e31c577a843a48d834774840151a8 after protected37903239686. Its complete canonical aggregate7REQ32SC has SHA256794ab377863fc8c9c99dc1e3fad9e0fff2a940d37be642cdb0ad8ef911241df5. The owned full MODIFIED requirement changes ONLY its two helper namespace tokens. The dated archive5REQ23SC and all twelve genuinely completed foreign tasks remain byte-exact with their old namespace. Complete before/after/inverse bodies bind this public composition; no private WIP, foreign feature adoption or task tick occurs here.

## Delivery Status

This is SOURCE preparation. Ten countable tasks cover actual preparation, tests, read-only installed native rehearsal and genuine independent preparation review. Actual validated disposable application, own sync/archive/readback and fresh resulting-head normal/nonauthor/protected/squash remain mandatory outside that checkbox list. Preparation does not claim native retirement, current hosted delivery or whole completion. No stored-data cleanup, schema/role migration, deployment or renewed owner decision is authorized.
