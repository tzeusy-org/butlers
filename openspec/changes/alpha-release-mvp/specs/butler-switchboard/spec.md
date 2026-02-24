# Switchboard Butler Role

## Purpose
The Switchboard (port 40100) is the single ingress point for all external messages. It classifies messages via LLM, decomposes multi-domain messages into self-contained segments, and routes each segment to the appropriate specialist butler via MCP.

## ADDED Requirements

### Requirement: Switchboard Identity and Runtime
The switchboard operates as the central routing control plane with codex runtime and ingestion buffer.

#### Scenario: Identity and port
- **WHEN** the switchboard butler is running
- **THEN** it operates on port 40100 with description "Routes incoming messages to specialist butlers"
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions

#### Scenario: Module profile
- **WHEN** the switchboard butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `telegram` (bot-only, user disabled), `email` (bot-only, user disabled), and `memory`

#### Scenario: Ingestion buffer configuration
- **WHEN** messages arrive at the switchboard
- **THEN** they are queued in a buffer with capacity 100, processed by 3 workers, with a scanner running every 30 seconds on batches of 50 with 10 seconds grace period

### Requirement: Switchboard Scheduled Tasks
The switchboard runs six native-mode cron jobs for statistics, memory, and registry maintenance.

#### Scenario: Scheduled task inventory
- **WHEN** the switchboard daemon is running
- **THEN** it executes six scheduled jobs: `connector-stats-hourly-rollup` (5 * * * *), `connector-stats-daily-rollup` (15 0 * * *), `connector-stats-pruning` (30 1 * * *), `memory-consolidation` (0 */6 * * *), `memory-episode-cleanup` (0 4 * * *), and `eligibility-sweep` (*/5 * * * *)
- **AND** all are dispatched as native jobs via `dispatch_mode = "job"`

### Requirement: Switchboard Classification
The switchboard uses an LLM classifier to assign incoming messages to specialist butlers with domain-specific routing rules.

#### Scenario: Domain classification rules
- **WHEN** a message arrives for classification
- **THEN** the LLM classifier applies domain-specific rules: finance for payment/billing/subscription signals, travel for booking/itinerary/flight signals, relationship for contacts/interactions/social, health for medications/measurements/symptoms/diet/nutrition, and general as the catch-all fallback
- **AND** finance wins tie-breaks against general when explicit payment semantics are present
- **AND** travel wins tie-breaks against general when explicit booking/itinerary semantics are present

#### Scenario: Multi-domain decomposition
- **WHEN** a compound multi-domain message is classified
- **THEN** the switchboard decomposes it into multiple self-contained sub-prompts, one per target butler, each independently understandable with all relevant entities, context, and actions included

#### Scenario: Conversation history context for real-time channels
- **WHEN** a message arrives from a real-time channel (Telegram, WhatsApp, Slack, Discord)
- **THEN** the classifier receives the union of messages from the last 15 minutes or the last 30 messages (whichever is more), ordered chronologically
- **AND** for email, the full chain is provided truncated to 50,000 tokens

#### Scenario: Classification fallback
- **WHEN** classification fails (LLM timeout, parse error, empty response)
- **THEN** the switchboard routes the entire message to the `general` butler with the original text intact

### Requirement: Switchboard Skills
The switchboard has two specialized skills for message triage and relationship extraction.

#### Scenario: Skill inventory
- **WHEN** the switchboard operates
- **THEN** it has access to `message-triage` (classification and routing with confidence scoring) and `relationship-extractor` (structured relationship data extraction for 8 signal types: contact, interaction, life_event, date, fact, sentiment, gift, loan)
