# Retire the healing-session GitHub token

## Why

The archived `2026-03-18-butler-self-healing` change gave sessions launched with
`trigger_source = "healing"` a `GH_TOKEN` so the local healing agent could open PRs. That local
dispatcher is retired (REQ-core-spawner-007): no caller launches a `healing` session any more, and
`healing` survives only as historical provenance. Under the QA evidence-first repair loop and
REQ-qa-investigation-dispatch-001, QA executors get no GitHub credential; publication goes only
through the restricted QA publisher. A credential handed to a provenance value nothing should
spawn is pure exposure (bu-cv57jc, discovered from bu-zwz09c).

## What Changes

- `core-spawner` "Healing Session MCP Restriction": replace the "Healing session receives GitHub
  token" scenario with "Healing session receives no GitHub credential". A session carrying
  historical `healing` provenance gets only `PATH` and trace context.
- `Spawner.trigger()` stops resolving `GH_TOKEN` for `trigger_source = "healing"`.

## Impact

- `src/butlers/core/spawner.py`, `tests/core/test_core_spawner.py`.
- No live caller changes behavior: nothing launches `healing` sessions today.
