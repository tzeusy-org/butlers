## Why

Every incoming message through a connector triggers downstream LLM processing; messages from spam domains, bot accounts, or unwanted Telegram chats waste tokens with zero signal value. A pre-ingest filter gate — evaluated at the connector before any Switchboard submission — eliminates this waste without touching the Switchboard or butler layers.

## What Changes

- Introduce a `source_filters` table in the switchboard schema: named, reusable filter objects with a `filter_mode` (blacklist/whitelist), a `source_key_type` specific to the connector's source (e.g. `domain`, `sender_address`, `substring` for email; `chat_id` for Telegram), and a list of patterns.
- Introduce a `connector_source_filters` join table: many-to-many relationship between connectors and named filters, with per-connector `enabled` flag and `priority` ordering.
- Add a REST API (in the switchboard API router) for CRUD on named filters and for managing connector filter assignments.
- Add pre-ingest filter enforcement to all connectors (GmailConnector, TelegramBotConnector, TelegramUserClientConnector, DiscordConnector): connectors load their active filters at startup and refresh on a configurable TTL; messages that fail the filter gate are dropped with a Prometheus counter increment and never submitted to Switchboard.
- Add a Filters UI to the ingestion dashboard: each ConnectorCard/ConnectorDetailPage gets a Filters button that opens a dialog showing all named filters with per-connector enable/disable checkboxes. A separate Manage Filters panel supports creating, editing, and deleting named filter objects.

## Capabilities

### New Capabilities

- `source-filter-registry`: Named filter objects — data model, DB schema (`source_filters` + `connector_source_filters`), CRUD REST API, and the connector assignment API. This is the shared registry that all connectors draw from.
- `connector-source-filter-enforcement`: The pre-ingest filter evaluation contract: how connectors load their active filters, cache them with a TTL, evaluate each message against the filter set, and record blocked messages in Prometheus. Includes the per-source-type key taxonomy (which filter key types are valid for which connector/channel).
- `dashboard-connector-filter-ui`: Frontend UI spec — Filters button on ConnectorCard and ConnectorDetailPage, the filter assignment dialog (all named filters with per-connector checkboxes), and the Manage Filters panel (CRUD for named filter objects).

### Modified Capabilities

- `connector-base-spec`: The connector pipeline now has a mandatory pre-submit filter gate step. After normalizing an event but before calling the Switchboard ingest API, every connector MUST evaluate active source filters and drop messages that fail. This is a spec-level behavioral change to the connector responsibility boundary.
- `connector-gmail`: Adds the `domain`, `sender_address`, and `substring` source key types as the valid filter keys for Gmail connectors. These are applied against the normalized `From` header.
- `connector-telegram-bot`: Adds `chat_id` (Telegram chat/user ID) as the valid filter key type for Telegram bot connectors.

## Impact

- **DB**: New switchboard migration (sw_026): `source_filters` and `connector_source_filters` tables.
- **Backend API**: New routes in `roster/switchboard/api/router.py` and `models.py` — 5 filter CRUD endpoints + 2 connector assignment endpoints.
- **Connectors**: `src/butlers/connectors/gmail.py`, any Telegram connector, Discord connector — each needs a filter loader (async DB query) and a pre-submit evaluation call. A shared `src/butlers/connectors/source_filter.py` module holds the generic evaluation logic.
- **Frontend**: New components in `frontend/src/components/ingestion/` — `ConnectorFiltersDialog.tsx`, `ManageSourceFiltersPanel.tsx`; modified `ConnectorCard.tsx` and `ConnectorDetailPage.tsx`; new hooks in `use-ingestion.ts`.
- **Tests**: Unit tests for filter evaluation (all key types, blacklist/whitelist logic, TTL cache); API tests for CRUD and assignment endpoints; frontend component tests for the dialog.
