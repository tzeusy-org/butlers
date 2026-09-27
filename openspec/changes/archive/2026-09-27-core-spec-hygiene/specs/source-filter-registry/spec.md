## REMOVED Requirements

### Requirement: Source Filter Data Model
**Reason**: Superseded: `source_filters` and `connector_source_filters` were migrated to `ingestion_rules` (switchboard migration sw_027) and the tables no longer exist.

**Migration**: Use the ingestion-policy specs (`ingestion_rules`).

### Requirement: Connector Filter Assignment
**Reason**: Superseded by the unified ingestion-policy system; connector filter assignments are `ingestion_rules` rows.

**Migration**: Use the ingestion-policy specs (`ingestion_rules`).

### Requirement: Source Filter CRUD API
**Reason**: Superseded by the unified ingestion-policy system; the source-filter API was retired with its tables.

**Migration**: Use the ingestion-policy specs (`ingestion_rules`).

### Requirement: Connector Filter Assignment API
**Reason**: Superseded by the unified ingestion-policy system; the assignment API was retired with its tables.

**Migration**: Use the ingestion-policy specs (`ingestion_rules`).
