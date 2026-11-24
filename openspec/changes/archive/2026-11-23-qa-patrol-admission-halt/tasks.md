## 1. Batch admission halt (bu-ejow5p.3 S1)

- [x] 1.1 Read the breaker before each launch and hold the remaining findings as one decision
- [x] 1.2 Remove the per-finding breaker gate from `dispatch_qa_investigation`
- [x] 1.3 Queue every cap leftover in one write, including the first
- [x] 1.4 Count a failed hold as `hold_failed` and do not report the batch as held
- [x] 1.5 Real-Postgres and unit tests for each behaviour
