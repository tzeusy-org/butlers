## ADDED Requirements

### Requirement: Batch Settings Apply Without Connector Restart
Changes to batch settings via the dashboard SHALL take effect on the connector's next flush scanner cycle without requiring a connector restart.

#### Scenario: Setting change propagation
- **WHEN** the user updates `flush_interval_s` via the dashboard
- **THEN** the connector picks up the new value on its next flush scanner cycle (within 60 seconds)
- **AND** no connector restart is required

## REMOVED Requirements

### Requirement: Live Reload Without Connector Restart

**Reason**: Its "Restart notice removed" scenario only described the absence of a retired notice.

**Migration**: The live-reload rule and its propagation scenario are unchanged in "Batch Settings Apply Without Connector Restart" in this spec.
