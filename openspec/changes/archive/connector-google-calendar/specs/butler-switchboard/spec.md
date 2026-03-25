# Switchboard Butler (Delta)

## MODIFIED Requirements

### Requirement: LLM-Driven Routing Contract
Switchboard performs discretionary routing through a pluggable LLM CLI runtime. The router classifies incoming messages and decomposes multi-domain requests into segments routed to specialist butlers. Each segment SHALL include a complexity classification alongside the routing decision.

#### Scenario: Conversation history context for routing
- **WHEN** the source channel is a real-time messaging channel (Telegram, WhatsApp, Slack, Discord)
- **THEN** recent conversation history (last 15 minutes or last 30 messages, whichever is more) is provided to the router for context
- **AND** the router only routes the current message, using history only to improve routing accuracy

#### Scenario: Calendar event routing context
- **WHEN** the source channel is `google_calendar`
- **THEN** no conversation history is provided (each calendar event change is self-contained)
- **AND** the event's normalized text contains sufficient context for routing (event type, title, time, attendees, organizer)

#### Scenario: Calendar event domain classification
- **WHEN** a `google_calendar` event arrives for classification
- **THEN** the router SHALL consider the event title, attendees, and description for domain signals
- **AND** health-related calendar events (e.g., doctor appointments) SHALL route to the health butler
- **AND** travel-related calendar events (e.g., flight itineraries) SHALL route to the travel butler
- **AND** the default routing target for calendar events with no clear domain signal SHALL be the general butler
