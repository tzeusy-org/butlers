## RENAMED Requirements
- FROM: `### Requirement: Graceful Fallback Policy`
- TO: `### Requirement: Fail-Closed Runtime Role Policy`

## MODIFIED Requirements

### Requirement: Fail-Closed Runtime Role Policy

Butler runtime pools SHALL establish their designated role before serving queries in every environment. Missing or unverifiable roles, inability to assume the role, and missing role configuration for a butler schema SHALL fail startup or the affected connection acquire. No development setting SHALL permit shared-user runtime continuation. Every acquire SHALL re-establish the designated role, including after connection reset or revoked membership. Partial resources owned by a failed connect SHALL be closed without returning a usable connection. Diagnostics SHALL omit DSNs, credentials and private driver payloads while preserving the causal exception for internal diagnosis. This SHALL NOT alter connector pools, dashboard DatabaseManager, managed bootstrap, migration provisioning or grants. Independent DND RLS/ACL, singleton and database-time admission checks SHALL remain required; verified runtime role establishment alone SHALL NOT authorize a DND mutation or durable admission.

ID: REQ-database-security-012
Source: about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md (Database Connection Scoping); bu-wws1eq recorded owner decision (2026-10-21)
Scope: v1-mandatory

#### Scenario: Runtime role cannot be established
- **WHEN** a butler schema lacks its designated role configuration, or role verification or assumption fails
- **THEN** startup or acquisition refuses before returning a usable runtime connection in every environment
- **AND** owned partial resources are closed and diagnostics omit private values
- **AND** no setting permits shared-user runtime continuation or self-bootstrap

#### Scenario: Unprovable DND admission fails closed
- **WHEN** a caller cannot prove its active runtime role, DND RLS/ACL boundary, singleton guard or database-time revalidation
- **THEN** DND mutation and durable admission refuse before DND row, guard, audit or admission changes
- **AND** migration-owner or shared-user authority does not substitute for the required evidence

#### Scenario: Membership revoked before reacquisition
- **WHEN** an established runtime connection is released and the connecting login loses membership in the designated role
- **THEN** the next acquire refuses before returning the connection or executing caller queries
- **AND** successful prior verification or an SSL retry does not disable the setup callback
