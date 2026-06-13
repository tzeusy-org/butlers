## MODIFIED Requirements

### Requirement: Full Route Map

The router SHALL define all application routes as children of the root layout.
All routes SHALL share the shell, header, error boundary, and sidebar.

The route map SHALL include the ingestion dispatch console routes:

- `/ingestion` -- ingestion Timeline ledger.
- `/ingestion/connectors` -- ingestion connector roster.
- `/ingestion/connectors/:connectorType/:endpointIdentity` -- ingestion
  connector detail.
- `/ingestion/filters` -- ingestion Filters pipeline.

These routes SHALL be first-class child routes. The redesigned ingestion
surface SHALL NOT rely on a single `/ingestion` component with page-level
`?tab=` state as its primary route map.

#### Scenario: Ingestion sub-routes share the dashboard shell

- **WHEN** the owner opens `/ingestion/connectors`
- **THEN** the route renders inside the root dashboard shell
- **AND** the sidebar and page header remain present
- **AND** the content is the ingestion connector roster, not a legacy tab panel

#### Scenario: Ingestion connector detail is route-addressable

- **WHEN** the owner opens
  `/ingestion/connectors/:connectorType/:endpointIdentity`
- **THEN** the router loads the connector detail route directly
- **AND** refresh or deep-link navigation preserves the selected connector

#### Scenario: Legacy tab query state is compatibility only

- **WHEN** a legacy `/ingestion?tab=filters` URL is visited
- **THEN** the app normalizes it to `/ingestion/filters`
- **AND** future route ownership remains in `dashboard-ingestion-dispatch-console`
  rather than the shell spec
