## Why

The personal-baselines read tool `metric_baseline_get` (bu-q7vx1q.77) is a new core tool in a new
`baselines` group, so the core-tool inventory the Core Tool Surface requirement describes gains one
group and one registration.

## What Changes

- Core Tool Surface: lists the `baselines` group (`metric_baseline_get`, read-only) and adds one
  scenario.

## Impact

- Spec: `core-daemon` (MODIFIED "Core Tool Surface", additive).
- Code: `src/butlers/core_tools/_baselines.py`, `src/butlers/core_tools/_dispatcher.py`,
  `src/butlers/core/tool_presentation_inventory.py`, `src/butlers/api/routers/runtime_config.py`.
- The retained case `test_core_tools_catalog_completeness` pins the registration totals and group
  set (87 to 88 tools, 79 to 80 grouped, plus `baselines`) and moves under contract evolution.
