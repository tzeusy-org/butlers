## REMOVED Requirements

### Requirement: Conversation Decomposition Pipeline Step
**Reason**: Decomposing a conversation batch into per-butler conceptual messages exists only to feed classification and route dispatch, which RFC 0038 D1 and the RFC 0003 disposition remove at cutover.
**Migration**: A `conversation_history` batch envelope is admitted as one goal (`REQ-goal-orchestration-008`); its lead owns every domain signal in the batch (`REQ-goal-orchestration-001`).

### Requirement: Signal Extraction for Decomposition
**Reason**: Signal extraction was a routing-session model call; RFC 0038 D18 allows no model use outside goals except the kinds it names, and this is not one of them.
**Migration**: The goal's lead identifies the signals while fulfilling the goal, reading third-party spans fenced as data (RFC 0038 D15) and calling each owning domain's capabilities (`REQ-goal-orchestration-004`).

### Requirement: Cherry-Picked Message Excerpts
**Reason**: Excerpts were the payload of per-butler routed conceptual messages; with no route dispatch there is no per-butler payload to cut (RFC 0038 D1).
**Migration**: The lead reads the batch from the goal input and loads further history through capabilities; a child that needs a slice receives it in its governing brief (`REQ-goal-orchestration-002`).

### Requirement: Multi-Butler Fan-Out from Single Ingestion
**Reason**: Fan-out of one ingestion to several butlers is route dispatch and per-butler session spawning, which RFC 0038 deletes at cutover.
**Migration**: One goal per envelope; cross-domain work happens through the lead's direct capability calls or depth-1 children (`REQ-goal-orchestration-001`, `REQ-goal-orchestration-003`).

### Requirement: Empty Decomposition Handling
**Reason**: The decomposition step it handles is removed (RFC 0038 D1).
**Migration**: A goal whose batch holds no actionable signal completes with that outcome in the goal ledger (`REQ-goal-orchestration-006`, `REQ-capability-authorization-012`).

### Requirement: Decomposition Output Storage
**Reason**: Decomposition output records per-butler routing of conceptual messages, which no longer exists (RFC 0038 D1).
**Migration**: The goal ledger records admission, delegation tree, effects and outcome for the batch's goal (RFC 0038 D17, `REQ-capability-authorization-012`); historical decomposition rows stay readable as pre-cutover records.
