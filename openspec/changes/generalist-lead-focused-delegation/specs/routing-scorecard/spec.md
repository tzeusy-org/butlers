## REMOVED Requirements

### Requirement: Routing accuracy evaluation
**Reason**: `ingest_v1()` no longer returns a `triage_target`: route dispatch and policy `route_to:<butler>` targets are removed at cutover (RFC 0038 D1, Hard cutover), so there is no routing decision to score.
**Migration**: Benchmarks score whole goals: the cutover gate's replay evaluation measures whole-goal success, constraint preservation, corrections, latency and cost (RFC 0038 Cutover gate), and model assignments come from replay evaluation (D16).

### Requirement: Per-model routing accuracy score
**Reason**: With no routing decision there is no routing accuracy to compute per model (RFC 0038 D1).
**Migration**: Per-model comparison uses tool-call accuracy and cost (`scorecard-reporting`) and the replay evaluation that sets role defaults (RFC 0038 D16).

### Requirement: Per-category routing breakdown
**Reason**: The breakdown partitions routing accuracy, which is removed with route dispatch (RFC 0038 D1).
**Migration**: Scenario tags still filter runs and tool-call results (`ingress-injection`, Requirement: Scenario corpus with unified definition).

### Requirement: Confusion matrix
**Reason**: Predicted-versus-expected butler targets no longer exist; one lead fulfills each goal (RFC 0038 D1, `REQ-goal-orchestration-001`).
**Migration**: Misdirected work shows up as missing or wrong effects in tool-call capture and the goal's effect ledger (RFC 0038 D17).
