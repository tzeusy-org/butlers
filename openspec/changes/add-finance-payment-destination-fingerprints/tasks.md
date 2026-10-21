## 1. Implementation

- [x] 1.1 Add `roster/finance/tools/payment_destinations.py` (normalize, classify, HMAC fingerprint, key resolution).
- [x] 1.2 Add Finance migration `finance_018` (`payment_destinations`, two nullable bill columns).
- [x] 1.3 Extend `track_bill` and its MCP registration with `payment_destination`; stamp the verdict.
- [x] 1.4 Resolve the key at `FinanceModule.on_startup`; register the key name in `_SYSTEM_KEY_USED_BY`.
- [x] 1.5 Document the parameter in the `tool-reference` skill.

## 2. Verification

- [x] 2.1 Real-Postgres cases through `track_bill` in `roster/finance/tests/test_tools.py`, including the no-raw-storage scan.
- [x] 2.2 Unit tests for normalization, fingerprinting, key resolution and verdict logic.
