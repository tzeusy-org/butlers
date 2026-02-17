# Dashboard Connectors

Connectors dashboard for monitoring external data sources integrated into the Butlers platform. Provides real-time liveness and health status, ingestion volume tracking, fanout distribution matrices, and per-connector detail pages.

Connectors represent external systems (APIs, message queues, file systems, etc.) that feed data into the Butlers platform. Each connector is identified by a tuple of `connector_type` (e.g., "kafka", "s3", "webhook") and `endpoint_identity` (e.g., "prod-queue", "raw-input-bucket").

## ADDED Requirements

### Requirement: Connector overview list API

The dashboard API SHALL expose `GET /api/connectors` which returns a list of all registered connectors with their current status and today's summary.

The response MUST be an array of `ConnectorSummary` objects, each containing:
- `connector_type` -- string identifier for the connector type
- `endpoint_identity` -- string identifier for the specific endpoint
- `liveness` -- one of `"online"`, `"stale"`, `"offline"` (determined by heartbeat freshness)
- `state` -- one of `"healthy"`, `"degraded"`, `"error"` (self-reported by connector)
- `error_message` -- string error description (only present if state is `"degraded"` or `"error"`)
- `version` -- optional string version identifier
- `uptime_s` -- optional number of seconds the connector has been in current liveness state
- `last_heartbeat_at` -- optional ISO 8601 timestamp of the most recent heartbeat
- `first_seen_at` -- ISO 8601 timestamp when the connector was first registered
- `today` -- optional `ConnectorDaySummary` object containing today's metrics

The `ConnectorDaySummary` object MUST contain:
- `messages_ingested` -- count of messages successfully ingested today
- `messages_failed` -- count of messages that failed to ingest today
- `uptime_pct` -- percentage of time (0-100) the connector was healthy today

#### Scenario: Fetch list of all registered connectors

- **WHEN** `GET /api/connectors` is called
- **THEN** the API MUST return an array of all connectors with their current status and today's summary
- **AND** each connector MUST include all required fields from `ConnectorSummary`
- **AND** the response status MUST be 200

#### Scenario: Connector with complete day summary

- **WHEN** `GET /api/connectors` is called and a connector has been actively ingesting messages today
- **THEN** the connector's `today` object MUST include `messages_ingested`, `messages_failed`, and `uptime_pct` all populated with numeric values

#### Scenario: Connector with offline liveness

- **WHEN** `GET /api/connectors` is called and a connector has not sent a heartbeat within the stale threshold
- **THEN** the connector's `liveness` MUST be `"offline"`
- **AND** the `last_heartbeat_at` MUST reflect the timestamp of the last successful heartbeat

#### Scenario: Connector with error state

- **WHEN** a connector reports itself in an error state via its heartbeat
- **THEN** the `state` field MUST be `"error"`
- **AND** the `error_message` field MUST contain the error description provided by the connector

#### Scenario: No connectors registered

- **WHEN** `GET /api/connectors` is called and no connectors are registered in the system
- **THEN** the API MUST return an empty array
- **AND** the response status MUST be 200

---

### Requirement: Connector detail API

The dashboard API SHALL expose `GET /api/connectors/{connectorType}/{endpointIdentity}` which returns full details for a specific connector.

The response MUST be a `ConnectorDetail` object, which extends `ConnectorSummary` with:
- `instance_id` -- optional UUID identifying the specific connector instance
- `registered_via` -- string describing how the connector was registered (e.g., "manual", "discovery", "import")
- `checkpoint` -- optional object containing:
  - `cursor` -- optional string representing the current checkpoint position (e.g., Kafka offset, file position, sequence number)
  - `updated_at` -- optional ISO 8601 timestamp when the checkpoint was last updated
- `counters` -- optional object with lifetime monotonic counters:
  - `messages_ingested` -- total messages successfully ingested
  - `messages_failed` -- total messages that failed to ingest
  - `source_api_calls` -- total API calls made to the source system
  - `checkpoint_saves` -- count of times the checkpoint was persisted
  - `dedupe_accepted` -- count of deduplicated messages accepted as unique

#### Scenario: Fetch full details for a specific connector

- **WHEN** `GET /api/connectors/kafka/prod-queue` is called
- **THEN** the API MUST return a `ConnectorDetail` object with all fields from `ConnectorSummary` plus `instance_id`, `registered_via`, `checkpoint`, and `counters`
- **AND** the response status MUST be 200

#### Scenario: Connector with checkpoint data

- **WHEN** `GET /api/connectors/s3/raw-input-bucket` is called and the connector tracks a checkpoint
- **THEN** the `checkpoint` object MUST include `cursor` (the current position, e.g., object key or timestamp) and `updated_at`

#### Scenario: Connector with lifetime counters

- **WHEN** `GET /api/connectors/webhook/api-events` is called
- **THEN** the `counters` object MUST include all fields with accurate lifetime counts:
  - `messages_ingested` -- sum of all messages across all runs
  - `messages_failed` -- sum of all failed messages across all runs
  - `source_api_calls` -- count of API calls made to the source system
  - `checkpoint_saves` -- count of checkpoint persistence events
  - `dedupe_accepted` -- count of unique deduplicated messages

#### Scenario: Connector not found

- **WHEN** `GET /api/connectors/unknown-type/unknown-identity` is called and no such connector exists
- **THEN** the API MUST return a 404 response with an error message

#### Scenario: Connector with no checkpoint

- **WHEN** `GET /api/connectors/webhook/simple` is called and the connector does not track checkpoints
- **THEN** the `checkpoint` field MUST be null or omitted

---

### Requirement: Connector statistics time series API

The dashboard API SHALL expose `GET /api/connectors/{connectorType}/{endpointIdentity}/stats` which returns time series data for volume and health metrics.

The endpoint SHALL accept the following query parameter:
- `period` (required) -- one of `"24h"`, `"7d"`, `"30d"` specifying the aggregation window

The response MUST be a `ConnectorStats` object containing:
- `connector_type` -- string
- `endpoint_identity` -- string
- `period` -- the requested period string
- `summary` -- an object with aggregate metrics over the period:
  - `messages_ingested` -- total messages ingested
  - `messages_failed` -- total messages failed
  - `error_rate_pct` -- percentage of messages that failed (0-100)
  - `uptime_pct` -- percentage of time the connector was in healthy state
  - `avg_messages_per_hour` -- average ingestion rate
- `timeseries` -- an array of `ConnectorStatsBucket` objects ordered by timestamp ascending

Each `ConnectorStatsBucket` object MUST contain:
- `bucket` -- ISO 8601 timestamp representing the start of the bucket (aggregation interval)
- `messages_ingested` -- count for this bucket
- `messages_failed` -- count for this bucket
- `healthy_count` -- count of heartbeats with state `"healthy"` in this bucket
- `degraded_count` -- count of heartbeats with state `"degraded"` in this bucket
- `error_count` -- count of heartbeats with state `"error"` in this bucket

#### Scenario: Fetch 24-hour time series with 1-hour buckets

- **WHEN** `GET /api/connectors/kafka/prod-queue/stats?period=24h` is called
- **THEN** the API MUST return up to 24 buckets (one per hour)
- **AND** each bucket MUST include the start timestamp, messages ingested/failed, and health state counts
- **AND** the `summary` MUST show aggregate totals over the 24-hour window

#### Scenario: Fetch 7-day time series with 1-day buckets

- **WHEN** `GET /api/connectors/s3/raw-input/stats?period=7d` is called
- **THEN** the API MUST return up to 7 buckets (one per day)
- **AND** each bucket MUST cover a 24-hour period

#### Scenario: Fetch 30-day time series

- **WHEN** `GET /api/connectors/webhook/events/stats?period=30d` is called
- **THEN** the API MUST return up to 30 buckets (one per day)

#### Scenario: Zero ingestion period

- **WHEN** `GET /api/connectors/offline-source/archive/stats?period=24h` is called and the connector has been offline with no messages
- **THEN** each bucket MUST have `messages_ingested = 0` and `messages_failed = 0`
- **AND** the `summary` error_rate_pct MUST be `0` (or `null` if no data)

#### Scenario: Mixed health states in period

- **WHEN** `GET /api/connectors/flaky-source/input/stats?period=7d` is called and the connector alternates between healthy, degraded, and error states
- **THEN** each bucket MUST accurately count heartbeats by state
- **AND** the `uptime_pct` in `summary` MUST reflect the percentage of buckets with at least one healthy heartbeat

#### Scenario: Missing required period parameter

- **WHEN** `GET /api/connectors/kafka/queue/stats` is called without a `period` query parameter
- **THEN** the API MUST return a 400 response with an error message indicating the missing parameter

#### Scenario: Invalid period value

- **WHEN** `GET /api/connectors/kafka/queue/stats?period=invalid` is called with an invalid period value
- **THEN** the API MUST return a 400 response with an error message

---

### Requirement: Cross-connector summary API

The dashboard API SHALL expose `GET /api/connectors/summary` which returns aggregate metrics across all connectors for a given period.

The endpoint SHALL accept the following query parameter:
- `period` (required) -- one of `"24h"`, `"7d"`, `"30d"`

The response MUST be a `ConnectorCrossSummary` object containing:
- `period` -- the requested period string
- `total_connectors` -- total count of all registered connectors
- `connectors_online` -- count of connectors with liveness `"online"`
- `connectors_stale` -- count of connectors with liveness `"stale"`
- `connectors_offline` -- count of connectors with liveness `"offline"`
- `total_messages_ingested` -- sum of messages ingested across all connectors in the period
- `total_messages_failed` -- sum of messages failed across all connectors in the period
- `overall_error_rate_pct` -- percentage of total messages that failed (0-100)
- `by_connector` -- an array of lightweight `ConnectorSummary` objects for quick reference

#### Scenario: Fetch cross-connector summary for 24 hours

- **WHEN** `GET /api/connectors/summary?period=24h` is called
- **THEN** the API MUST return aggregate metrics showing the liveness distribution and total messages across all connectors in the last 24 hours
- **AND** `total_connectors` MUST equal the sum of `connectors_online + connectors_stale + connectors_offline`

#### Scenario: Fetch cross-connector summary for 7 days

- **WHEN** `GET /api/connectors/summary?period=7d` is called
- **THEN** the API MUST return 7-day aggregate metrics
- **AND** the `by_connector` array MUST include all registered connectors with their 7-day summary data

#### Scenario: No connectors registered

- **WHEN** `GET /api/connectors/summary?period=24h` is called and no connectors are registered
- **THEN** `total_connectors` MUST be `0`
- **AND** all health counts (`connectors_online`, `connectors_stale`, `connectors_offline`) MUST be `0`
- **AND** all message counts MUST be `0`

#### Scenario: Mixed connector states

- **WHEN** `GET /api/connectors/summary?period=24h` is called and the system has 5 online, 2 stale, and 1 offline connector
- **THEN** `total_connectors` MUST be `8`
- **AND** `connectors_online` MUST be `5`
- **AND** `connectors_stale` MUST be `2`
- **AND** `connectors_offline` MUST be `1`

#### Scenario: Missing required period parameter

- **WHEN** `GET /api/connectors/summary` is called without a `period` query parameter
- **THEN** the API MUST return a 400 response with an error message

---

### Requirement: Connector fanout distribution API

The dashboard API SHALL expose `GET /api/connectors/fanout` which returns a matrix of how messages from each connector are distributed to butlers.

The endpoint SHALL accept the following query parameter:
- `period` (required) -- one of `"7d"`, `"30d"`

The response MUST be a `ConnectorFanout` object containing:
- `period` -- the requested period string
- `matrix` -- an array of `ConnectorFanoutEntry` objects

Each `ConnectorFanoutEntry` object MUST contain:
- `connector_type` -- string
- `endpoint_identity` -- string
- `targets` -- an object mapping butler names to message counts (e.g., `{ "general": 5234, "health": 3142, "relationship": 1056 }`)

#### Scenario: Fetch fanout distribution for 7 days

- **WHEN** `GET /api/connectors/fanout?period=7d` is called
- **THEN** the API MUST return a matrix showing how each connector's messages were distributed to each butler over the last 7 days
- **AND** each entry MUST include the connector identification and a `targets` object with butler-to-count mappings

#### Scenario: Fetch fanout distribution for 30 days

- **WHEN** `GET /api/connectors/fanout?period=30d` is called
- **THEN** the API MUST return the 30-day fanout matrix

#### Scenario: Connector with uneven butler distribution

- **WHEN** `GET /api/connectors/fanout?period=7d` is called and a connector routes 80% of messages to one butler and 20% to another
- **THEN** the connector's entry in the matrix MUST accurately reflect the distribution (e.g., `{ "general": 8000, "health": 2000 }`)

#### Scenario: Connector with no fanout data

- **WHEN** `GET /api/connectors/fanout?period=7d` is called and a connector has not routed any messages in the period
- **THEN** the connector's entry MUST still appear in the matrix with a `targets` object containing zero counts (or the entry may be omitted at implementation discretion, but consistency is required)

#### Scenario: Missing required period parameter

- **WHEN** `GET /api/connectors/fanout` is called without a `period` query parameter
- **THEN** the API MUST return a 400 response with an error message

#### Scenario: Invalid period value

- **WHEN** `GET /api/connectors/fanout?period=24h` is called (24h not supported for fanout)
- **THEN** the API MUST return a 400 response with an error message

---

### Requirement: Connectors overview page

The frontend SHALL render a connectors overview page at `/connectors` displaying aggregate connector metrics, volume trends, and fanout distribution.

The page MUST contain the following sections:

1. **Connector overview cards** -- a grid of cards displaying registered connectors. Each card MUST show:
   - Connector type icon (visual identifier for the connector type)
   - Endpoint identity (name/label for the specific endpoint)
   - Liveness badge (online/stale/offline with distinct color)
   - Health state badge (healthy/degraded/error with distinct color)
   - Uptime percentage for today
   - Last heartbeat age (e.g., "2m ago", "1h ago")
   - Today's ingestion count

2. **Volume time series chart** -- a line or bar chart showing ingestion volume over time. The chart MUST support:
   - Period selector toggle: 24h / 7d / 30d
   - Per-connector visibility toggle (can show/hide lines or bars for individual connectors)
   - X-axis: time (hours for 24h, days for 7d/30d)
   - Y-axis: message count
   - Tooltip on hover showing date/time, connector name, and count

3. **Fanout distribution table** -- a matrix table showing connector-to-butler message distribution. The table MUST display:
   - Rows: connectors (sorted by total volume descending)
   - Columns: butler names
   - Cells: message count for the connector-butler pair in the selected period (7d or 30d)
   - Footer row: column totals (messages per butler)

4. **Error log panel** -- a log of recent unhealthy heartbeats sorted by timestamp descending. Each entry MUST show:
   - Timestamp
   - Connector type + endpoint identity
   - Health state (degraded or error)
   - Error message (if available)
   - Limit display to the 20 most recent errors

5. **Cross-connector summary stats** -- a set of metric cards displaying:
   - Total connectors (with count)
   - Online count (with count and percentage of total)
   - Stale count (with count and percentage of total)
   - Offline count (with count and percentage of total)
   - Total messages ingested (formatted with thousand separators)
   - Total messages failed (formatted with thousand separators)
   - Overall error rate (percentage, with color coding for severity)

#### Scenario: Load connectors overview page with default 24-hour view

- **WHEN** a user navigates to `/connectors`
- **THEN** the page MUST load and display all registered connectors in the overview card grid
- **AND** the volume time series chart MUST show 24-hour data by default
- **AND** the fanout distribution table MUST be visible showing the 7-day distribution
- **AND** the error log panel MUST display recent unhealthy heartbeats
- **AND** the cross-connector summary stats MUST show aggregate metrics for the last 24 hours

#### Scenario: Switch volume chart period to 7 days

- **WHEN** a user clicks the "7d" toggle on the connectors overview page
- **THEN** the volume time series chart MUST update to display 7 days of data with daily buckets
- **AND** the chart MUST maintain the per-connector visibility state (selected connectors remain toggled)
- **AND** the summary stats MUST update to reflect 7-day metrics

#### Scenario: Toggle connector visibility in volume chart

- **WHEN** a user clicks on a connector's name in the chart legend to toggle visibility
- **THEN** that connector's line or bar MUST disappear from the chart
- **AND** the visibility state MUST persist while the user remains on the page
- **AND** other connectors' visibility MUST remain unchanged

#### Scenario: View fanout distribution for 30 days

- **WHEN** a user clicks the "30d" selector in the fanout distribution table
- **THEN** the fanout matrix MUST update to show 30-day message counts
- **AND** the period selector MUST be independent from the volume chart period selector

#### Scenario: Connector with no messages today

- **WHEN** a connector has `today.messages_ingested = 0` and `today.messages_failed = 0`
- **THEN** the connector card MUST display "0" for today's ingestion count
- **AND** the card MUST remain visible in the grid (not hidden)

#### Scenario: Offline connector with recent error

- **WHEN** a connector has `liveness = "offline"` and `state = "error"` with an error message
- **THEN** the connector card MUST display the offline liveness badge and error state badge with distinct colors
- **AND** the error message MUST be visible in the error log panel with the most recent timestamp

#### Scenario: Empty state when no connectors registered

- **WHEN** a user navigates to `/connectors` and no connectors are registered
- **THEN** the page MUST display an empty state message (e.g., "No connectors registered yet")
- **AND** the charts and tables MUST render gracefully without errors
- **AND** the summary stats MUST all show zero counts

#### Scenario: High error rate color coding

- **WHEN** the overall error rate exceeds 10%
- **THEN** the error rate metric card MUST display in a warning color (orange or yellow)
- **AND** when error rate exceeds 25%, the card MUST display in a critical color (red)

#### Scenario: Fanout table shows distribution breadth

- **WHEN** the fanout distribution table displays a connector that routes to 3 butlers with counts `{ "general": 5000, "health": 3000, "relationship": 2000 }`
- **THEN** the cells for that row MUST display these counts in the corresponding butler columns
- **AND** the row total MUST be 10,000

---

### Requirement: Connector detail page

The frontend SHALL render a connector detail page at `/connectors/:connectorType/:endpointIdentity` displaying focused metrics and status information for a single connector.

The page MUST contain the following sections:

1. **Connector identity card** -- a card displaying:
   - Connector type
   - Endpoint identity
   - Instance ID (if available)
   - Version (if available)
   - Registered via (e.g., "manual", "discovery")
   - First seen at (timestamp)

2. **Current status card** -- a card displaying:
   - Liveness badge with status text (online/stale/offline)
   - Health state badge with status text (healthy/degraded/error)
   - Error message (if state is degraded or error)
   - Uptime percentage (for today)
   - Last heartbeat age (e.g., "5m ago")

3. **Counters card** -- a card displaying lifetime monotonic counters:
   - Messages ingested (total, formatted with thousand separators)
   - Messages failed (total, formatted with thousand separators)
   - Source API calls (total)
   - Checkpoint saves (count)
   - Dedupe accepted (count, representing deduplicated messages)

4. **Checkpoint card** (if applicable) -- a card displaying:
   - Current cursor value (e.g., Kafka offset, file position)
   - Last updated timestamp
   - Clear indication of checkpoint type (context-dependent on connector)

5. **Volume and health time series** -- a dual-axis chart showing:
   - Primary Y-axis (left): message ingestion volume (line or bar)
   - Secondary Y-axis (right): health state distribution (stacked bar showing healthy/degraded/error)
   - X-axis: time (hours for 24h, days for 7d/30d)
   - Period selector: 24h / 7d / 30d
   - Tooltip on hover showing detailed metrics

6. **Per-butler fanout breakdown** -- a table or horizontal bar chart showing:
   - Butler name
   - Message count (for selected period)
   - Percentage of total messages from this connector (in the period)
   - Bars or columns sorted by message count descending

#### Scenario: View detail page for a healthy online connector

- **WHEN** a user clicks on a connector card or navigates to `/connectors/kafka/prod-queue`
- **THEN** the page MUST load and display all sections with data from the `ConnectorDetail` endpoint
- **AND** the identity card MUST show the connector's type, endpoint identity, instance ID, version, and registration method
- **AND** the status card MUST show `liveness = "online"` and `state = "healthy"` with appropriate badges
- **AND** the uptime percentage MUST be displayed (likely 100% or close to it)

#### Scenario: Detail page for offline connector with error

- **WHEN** a user navigates to `/connectors/webhook/unreliable` and the connector is offline with an error state
- **THEN** the status card MUST display the offline liveness badge and error state badge in red/critical colors
- **AND** the error message from the connector's most recent heartbeat MUST be visible and readable
- **AND** the last heartbeat age MUST show a significant duration (e.g., "3h ago")

#### Scenario: Detail page with checkpoint data

- **WHEN** a user navigates to `/connectors/s3/raw-input` and the connector tracks checkpoint positions
- **THEN** the checkpoint card MUST display:
  - The current cursor value (e.g., "s3://bucket/path/to/last/processed/file")
  - The timestamp of when the checkpoint was last updated
  - Contextual information about the checkpoint type

#### Scenario: Detail page time series for 7 days

- **WHEN** a user navigates to a connector detail page and clicks the "7d" period selector
- **THEN** the volume and health time series chart MUST update to display 7 days of data with daily buckets
- **AND** each day MUST show the stacked health state distribution (healthy/degraded/error counts)

#### Scenario: Fanout breakdown shows uneven distribution

- **WHEN** a user views the per-butler fanout breakdown for a connector that routes `{ "general": 8000, "health": 1500, "relationship": 500 }` in 7 days
- **THEN** the table/chart MUST display:
  - General: 8000 messages (80%)
  - Health: 1500 messages (15%)
  - Relationship: 500 messages (5%)
- **AND** the bars/columns MUST be sorted by message count descending

#### Scenario: Connector with no historical data

- **WHEN** a user navigates to a connector detail page for a newly registered connector with no messages yet
- **THEN** the page MUST still load and display all sections gracefully
- **AND** the volume and health time series MUST show empty/zero states
- **AND** the counters card MUST display all zero values
- **AND** the fanout breakdown MUST show an empty state

#### Scenario: Navigate back from detail to overview

- **WHEN** a user is on a connector detail page and clicks a back button or breadcrumb to return to `/connectors`
- **THEN** the page MUST navigate back to the connectors overview page
- **AND** the previous state (period selection, scroll position) SHOULD be restored if possible

#### Scenario: Detail page for connector with degraded state

- **WHEN** a user navigates to `/connectors/kafka/backup` and the connector has `state = "degraded"`
- **THEN** the status card MUST display the degraded state badge in a warning color (orange/yellow)
- **AND** any error message associated with the degraded state MUST be visible
- **AND** the uptime percentage MUST reflect partial availability (less than 100%)

---

### Requirement: Connector cards visual design

Each connector overview card on `/connectors` page SHALL display information in a consistent, scannable layout.

The card layout MUST include (in visual order):
1. **Top left:** Connector type icon (visual identifier)
2. **Top right:** Liveness badge (small pill/badge with color and text)
3. **Second row:** Endpoint identity (larger text, primary identifier)
4. **Third row:** Health state badge, error message (truncated if needed)
5. **Fourth row:** Uptime % (left), Last heartbeat age (right)
6. **Bottom:** Today's ingestion count (large number, formatted)

The card MUST be clickable and navigate to the connector detail page. Hover effects MUST provide visual feedback.

#### Scenario: Card displays all required information

- **WHEN** the connectors overview page renders a connector card
- **THEN** the card MUST display the type icon, endpoint identity, liveness badge, health badge, error message (if any), uptime percentage, heartbeat age, and today's count
- **AND** all text MUST be readable and not cut off
- **AND** badges MUST have distinct colors (online=green, stale=yellow, offline=red; healthy=green, degraded=yellow, error=red)

#### Scenario: Card with long endpoint identity

- **WHEN** a connector's endpoint identity is very long (e.g., "long-bucket-name-with-many-hyphens-for-aws-s3.region.datalake")
- **THEN** the endpoint identity text MUST wrap or truncate gracefully
- **AND** the full identity MUST be visible on hover (tooltip)

#### Scenario: Card with no error message

- **WHEN** a connector has `state = "healthy"`
- **THEN** the card MUST not display an error message section
- **AND** the health badge MUST show only the state text (e.g., "Healthy")

#### Scenario: Click connector card navigates to detail

- **WHEN** a user clicks anywhere on a connector card on `/connectors`
- **THEN** the page MUST navigate to `/connectors/{connectorType}/{endpointIdentity}`

---

### Requirement: Volume time series chart interactions

The volume time series chart on the connectors overview page `/connectors` SHALL support interactive period selection and per-connector visibility toggling.

The chart MUST support:
- **Period toggle buttons** (24h / 7d / 30d) to change aggregation granularity and recalculate volume buckets
- **Legend with per-connector checkboxes** to show/hide individual connector lines or bars
- **Tooltip on hover** displaying date/time, connector name, and message count
- **Responsive layout** that adapts to viewport width

#### Scenario: User toggles from 24h to 7d period

- **WHEN** a user is viewing the 24h volume chart and clicks the "7d" button
- **THEN** the chart MUST:
  - Clear the existing 24-hour bucketed data
  - Fetch fresh 7-day data from all connectors
  - Redraw with daily buckets (7 data points instead of 24)
  - Update the X-axis labels to show day names or dates
  - Maintain the current connector visibility toggles

#### Scenario: User toggles connector visibility in legend

- **WHEN** a user unchecks a connector in the chart legend
- **THEN** that connector's line or bar MUST immediately disappear from the chart
- **AND** other connectors' visual representation MUST remain unchanged
- **AND** when the user re-checks the connector, the line/bar MUST reappear

#### Scenario: Tooltip shows exact values on hover

- **WHEN** a user hovers over a data point in the volume chart
- **THEN** a tooltip MUST appear showing:
  - The date/time of the bucket
  - The connector name (if a single series is hovered)
  - The message count (ingested and/or failed)

#### Scenario: Chart with many connectors

- **WHEN** the system has 10+ connectors and the chart legend is long
- **THEN** the legend MUST be scrollable or wrapped
- **AND** all connector visibility toggles MUST remain accessible

#### Scenario: Chart updates when new data arrives

- **WHEN** a user is viewing the 24h volume chart and a new connector heartbeat arrives with message counts
- **THEN** the chart MAY update in real-time (if auto-refresh is enabled)
- **AND** the latest bucket MUST be updated with the new counts
- **AND** chart transitions MUST be smooth (animated)

---

### Requirement: Fanout distribution table

The fanout distribution table on the connectors overview page `/connectors` SHALL display a connector x butler matrix showing message distribution.

The table layout MUST include:
- **Left column:** Connector type + endpoint identity (row headers), sorted by total volume descending
- **Column headers:** Butler names (Switchboard, General, Relationship, Health, Heartbeat, etc.)
- **Cells:** Message count (integer) for each connector-butler pair in the selected period
- **Footer row:** Column totals (sum of messages per butler)
- **Right margin:** Row totals (sum of messages per connector)

The table MUST support:
- **Period selector** (7d / 30d) to choose the aggregation window
- **Responsive layout** (may become a scrollable table or card layout on narrow viewports)

#### Scenario: Fanout table shows distribution across 5 butlers

- **WHEN** the connectors overview page displays the fanout distribution table
- **THEN** the table MUST show columns for all active butlers (Switchboard, General, Relationship, Health, Heartbeat)
- **AND** each row MUST represent one connector
- **AND** cells MUST display the message count routed to that butler in the selected period

#### Scenario: Fanout table sorted by connector volume

- **WHEN** the fanout distribution table is rendered
- **THEN** connectors MUST be sorted by total row sum (total messages) descending
- **AND** the connector with the most messages MUST appear at the top

#### Scenario: Switch fanout table period from 7d to 30d

- **WHEN** a user clicks the "30d" selector in the fanout table
- **THEN** the table data MUST refresh to show 30-day message counts
- **AND** the period selector MUST update to show "30d" as selected
- **AND** row and column totals MUST recalculate

#### Scenario: Fanout table with uneven distribution

- **WHEN** a connector routes 95% of messages to one butler and 5% to another
- **THEN** the cells MUST accurately reflect this disparity
- **AND** the row total MUST equal the sum of all cells in that row

#### Scenario: Fanout table footer shows column totals

- **WHEN** the fanout distribution table is displayed
- **THEN** the footer row MUST display the sum of each column (total messages per butler in the period)

#### Scenario: Empty fanout cells

- **WHEN** a connector has routed no messages to a specific butler in the period
- **THEN** the cell MUST display `0` or be left blank (consistent with other cells)

---

### Requirement: Error log panel

The error log panel on the connectors overview page `/connectors` SHALL display recent connector errors from unhealthy heartbeats.

The panel MUST display:
- **Limit:** Show the 20 most recent errors
- **Columns:** Timestamp, Connector (type + identity), State (degraded/error), Error message
- **Sorting:** Ordered by timestamp descending (most recent first)
- **Severity indicators:** Visual distinction between degraded (yellow/orange) and error (red) states
- **Scrolling:** If more than 20 entries exist, the panel MUST be scrollable

#### Scenario: Error log shows recent heartbeats with non-healthy states

- **WHEN** the connectors overview page loads
- **THEN** the error log panel MUST display recent heartbeats where `state != "healthy"`
- **AND** each entry MUST show the timestamp, connector identification, health state, and error message
- **AND** entries MUST be sorted by timestamp descending (most recent at the top)

#### Scenario: Distinguish between degraded and error states

- **WHEN** the error log displays an entry for a degraded connector
- **THEN** that row MUST be highlighted in yellow or orange
- **AND** when an error state is shown, it MUST be highlighted in red
- **AND** the state badge text MUST clearly indicate "Degraded" or "Error"

#### Scenario: Error log truncates long error messages

- **WHEN** a connector's error message is very long (e.g., a stack trace)
- **THEN** the message MUST be truncated in the table view
- **AND** the full message MUST be visible on click/hover (tooltip or detail drawer)

#### Scenario: No recent errors

- **WHEN** the connectors overview page loads and all connectors are in healthy state
- **THEN** the error log panel MUST display an empty state message (e.g., "No errors in the last 24 hours")
- **AND** the panel MUST remain visible (not hidden)

#### Scenario: Error log auto-updates with new heartbeats

- **WHEN** a user is viewing the error log panel and a new unhealthy heartbeat arrives
- **THEN** the new error entry MUST appear at the top of the log (if auto-refresh is enabled)
- **AND** the entry count MUST not exceed 20 (oldest entries are removed)

---

### Requirement: Cross-connector summary stats

The cross-connector summary stats section on the connectors overview page `/connectors` SHALL display aggregate metrics as individual metric cards.

The section MUST include the following metric cards:
1. **Total connectors** -- count of all registered connectors
2. **Online count** -- count and percentage of online connectors
3. **Stale count** -- count and percentage of stale connectors
4. **Offline count** -- count and percentage of offline connectors
5. **Total messages ingested** -- formatted with thousand separators (e.g., "1,234,567")
6. **Total messages failed** -- formatted with thousand separators
7. **Overall error rate** -- percentage (0-100) with color coding

Each metric card MUST display:
- **Metric name** (label)
- **Primary value** (large, bold text)
- **Secondary information** (percentage, counts, or sub-values)

Color coding for overall error rate:
- 0-5%: Green (healthy)
- 5-10%: Yellow (caution)
- 10-25%: Orange (warning)
- 25%+: Red (critical)

#### Scenario: Summary stats show distribution across liveness states

- **WHEN** the connectors overview page displays the summary stats
- **THEN** the online, stale, and offline counts MUST sum to the total connector count
- **AND** each card MUST display the count and percentage relative to total

#### Scenario: Summary stats format large message counts

- **WHEN** the total messages ingested is 1,234,567
- **THEN** the card MUST display "1,234,567" (with comma separators)
- **AND** the metric MUST be visually prominent and easy to read

#### Scenario: Error rate color indicates severity

- **WHEN** the overall error rate is 0.5%
- **THEN** the card MUST display in green (healthy)
- **WHEN** the error rate is 15%
- **THEN** the card MUST display in orange (warning)
- **WHEN** the error rate is 35%
- **THEN** the card MUST display in red (critical)

#### Scenario: Summary stats reflect selected period

- **WHEN** a user selects "7d" period in the volume chart
- **THEN** the summary stats MUST update to reflect 7-day metrics (if the summary stats period matches the chart period, or retain 24h summary independently based on design intent)

#### Scenario: No connectors registered edge case

- **WHEN** the system has zero registered connectors
- **THEN** all counts MUST be `0`
- **AND** the percentage cards MUST show "N/A" or `0%` gracefully

#### Scenario: All connectors offline

- **WHEN** all registered connectors are in offline state
- **THEN** the offline count MUST equal the total connector count
- **AND** the online and stale counts MUST be `0`

---

### Requirement: API response error handling

All connector APIs (`/api/connectors*`) SHALL return consistent error responses for invalid requests or server-side failures.

Error responses MUST include:
- **HTTP status code** (4xx for client errors, 5xx for server errors)
- **Error message** (descriptive text indicating the problem)
- **Error code** (optional, machine-readable identifier)

#### Scenario: Invalid connector type or identity

- **WHEN** `GET /api/connectors/invalid-type/invalid-identity` is called
- **THEN** the API MUST return 404 with a descriptive error message

#### Scenario: Invalid query parameter

- **WHEN** `GET /api/connectors/stats?period=invalid` is called
- **THEN** the API MUST return 400 with a message indicating the invalid period value

#### Scenario: Database connection failure

- **WHEN** the connector database becomes unavailable during an API request
- **THEN** the API MUST return 503 Service Unavailable with a message indicating the temporary failure
- **AND** the frontend MUST display a user-friendly error (e.g., "Unable to load connector data. Please try again later.")

#### Scenario: Successful API call includes proper status code

- **WHEN** `GET /api/connectors` is called and succeeds
- **THEN** the response MUST be HTTP 200
- **AND** the response body MUST be valid JSON matching the `ConnectorSummary[]` schema

---

### Requirement: Frontend real-time updates (stretch goal)

The connectors pages (`/connectors` and `/connectors/:type/:identity`) MAY support optional auto-refresh of data to show real-time connector status changes.

If auto-refresh is implemented:
- It MUST be toggled via a settings control (e.g., toggle button, checkbox in settings)
- The default interval SHOULD be 30 seconds (configurable in settings)
- When a refresh occurs, the page MUST update visual elements (cards, charts, tables) smoothly
- The auto-refresh MUST not interfere with user interactions (e.g., chart period selection)
- The user MUST see a visual indicator of refresh status (e.g., a spinner or "last updated X seconds ago" label)

#### Scenario: Auto-refresh enabled updates connector cards

- **WHEN** auto-refresh is enabled and 30 seconds have elapsed
- **THEN** the connectors overview cards MUST update with fresh status data
- **AND** liveness badges MAY change if a heartbeat status changed
- **AND** the last heartbeat age MUST decrement by the refresh interval

#### Scenario: User disables auto-refresh

- **WHEN** a user toggles auto-refresh off in settings
- **THEN** the pages MUST no longer fetch updates at regular intervals
- **AND** a manual refresh button or action MUST be available for on-demand updates

