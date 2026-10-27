## 1. Tool

- [x] 1.1 Add `workout_last` and its activity vocabulary (`roster/health/tools/workouts.py`)
- [x] 1.2 Register it with the `measurements` tool group and classify it in the tool inventory

## 2. Verification

- [x] 2.1 Unit tests: match, no match, latest wins, source attribution, DST and day boundary
- [x] 2.2 Real-Postgres tests over `workout_session` facts (CI only)
