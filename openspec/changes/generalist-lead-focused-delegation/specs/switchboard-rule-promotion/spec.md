## REMOVED Requirements

### Requirement: Routing Verdict Log
**Reason**: The log exists to mine agreement between LLM routing verdicts and deterministic rules; RFC 0038 D1 and D5 remove LLM classification and route dispatch, so no `llm`, `pinned` or `spot_check` verdict is produced after cutover.
**Migration**: Deterministic pre-admission outcomes stay observable through the ingestion policy evaluation log (`ingestion-policy`); admission decisions are recorded in the goal ledger (RFC 0038 D17, `REQ-capability-authorization-012`). Existing rows remain readable history.

### Requirement: Promotion Trigger
**Reason**: Promotion mines consistent LLM routing verdicts, which no longer exist after cutover (RFC 0038 D1).
**Migration**: The owner authors ingestion rules directly through the ingestion rules API; there is no LLM verdict stream to promote from.

### Requirement: Rule Promotion Suggestion Data Model
**Reason**: Suggestions are produced only by the promotion trigger and spot-check demotion, both removed with LLM classification (RFC 0038 D1).
**Migration**: Existing `rule_promotion_suggestions` rows remain readable history and keep the provenance foreign key from promoted rules; no new suggestions are created.

### Requirement: Clearly-Automated Sender Classification
**Reason**: Automated-sender tiering governed auto-apply of LLM-mined promotions, which no longer occur (RFC 0038 D1).
**Migration**: None; owner-authored rules need no automated-tier gate.

### Requirement: Promotion Application (Owner-Confirmed with Automated-Tier Auto-Apply)
**Reason**: There are no promotion suggestions to apply after cutover, and `route_to` targets are removed from ingestion rules (RFC 0038 D1).
**Migration**: The owner creates or edits ingestion rules directly; rules already minted by promotion keep their provenance (Requirement: Rule Provenance).

### Requirement: Demotion via Spot-Check Sampling
**Reason**: Spot-checks route a sampled event through LLM classification to compare verdicts; LLM classification is removed at cutover (RFC 0038 D1, D5).
**Migration**: Promoted rules are reviewed and disabled by the owner like any other rule; no event is diverted to a classification session.
