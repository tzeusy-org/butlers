## REMOVED Requirements

### Requirement: Switchboard Complexity Classification
**Reason**: Complexity was a field of the Switchboard's LLM routing decision; RFC 0038 D1 and D5 remove LLM classification and make admission deterministic, so there is no routing call to piggyback on.
**Migration**: Model choice is per invocation from `public.model_catalog` by role (`lead`, `child`, `monitor`) and, for children, the tier the brief requests; a lead that judges a goal beyond its model calls `goal.escalate` (RFC 0038 D16).

### Requirement: Complexity Propagation Through Route Dispatch
**Reason**: `route.v1` dispatch and `route.execute` session spawning are deleted at cutover (RFC 0038 D1, Hard cutover), so no envelope carries complexity to a target butler.
**Migration**: The goal runner resolves each invocation's model by role and requested tier (RFC 0038 D16); domain butlers spawn no reasoning sessions (`REQ-goal-orchestration-008`).

### Requirement: Trigger API Complexity Parameter
**Reason**: The manual trigger path is removed; goals are the only path to a reasoning session (RFC 0038 D5, `REQ-goal-orchestration-008`).
**Migration**: The owner starts work through owner ingress, which admits an owner goal; its lead runs on the role default and escalates through the role's ladder when needed (RFC 0038 D16).

### Requirement: Tick Trigger Complexity
**Reason**: Scheduled prompts become obligation admissions and the routing-classification spawn is removed (RFC 0038 D1, D5), so neither path spawns a session at a stored complexity.
**Migration**: An obligation goal's lead runs as an obligation monitor on the role's default class and escalates with `goal.escalate` (RFC 0038 D16); historical `tick`/`classification` session rows stay readable for spend and QA history.
