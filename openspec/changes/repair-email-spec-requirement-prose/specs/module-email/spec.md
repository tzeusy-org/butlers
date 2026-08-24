## MODIFIED Requirements

### Requirement: Classification Pipeline Integration (Removed)

The `email_check_and_route_inbox` tool has been removed. The email module SHALL
NOT register any inbox classification or routing tool; email ingestion is
handled by `GmailConnector` via the connector-based pipeline.

#### Scenario: No inbox classification tool is registered

- **WHEN** the email module registers tools
- **THEN** `email_check_and_route_inbox` SHALL NOT be among them
- **AND** incoming email reaches butlers through `GmailConnector` and the
  connector-based ingestion pipeline rather than through a module-side
  classification tool
