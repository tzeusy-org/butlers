## REMOVED Requirements

### Requirement: Delegated Return Wakes Are Not Briefing Contributions
**Reason**: RFC 0038 deletes the `delegate_*` tools and the shared cross-butler delegation ledger at cutover (D7, "Removed tools"), and the generic Switchboard `route` tool with them, so no delegated-answer callback, asker-local return task, or return wake exists for this requirement to fence.
**Migration**: A child's result returns to its lead inside the same goal and is recorded in the goal ledger (`REQ-goal-orchestration-006`); it never reads or writes a briefing state key, `general.v_briefing_contributions`, or a briefing envelope. RFC 0010's read-only batch aggregation exception stays limited to the deterministic contribution jobs this spec defines, and a deterministic briefing job that needs another domain's data uses a Switchboard service-plane endpoint, never direct sibling-schema access.
