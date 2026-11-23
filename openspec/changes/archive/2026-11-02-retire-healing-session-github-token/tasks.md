## 1. Retire the healing GitHub token

- [x] 1.1 Replace the "Healing session receives GitHub token" scenario in the `core-spawner`
  baseline with "Healing session receives no GitHub credential".
- [x] 1.2 Stop resolving `GH_TOKEN` for `trigger_source = "healing"` in `Spawner.trigger()`.
- [x] 1.3 Pin the healing environment to `PATH` plus trace context in
  `tests/core/test_core_spawner.py`, with a `GH_TOKEN` present in the host environment.
