## MODIFIED Requirements

### Requirement: Messenger Butler Identity and Runtime
Messenger SHALL operate as a delivery-only staffer on port 41104. It SHALL NOT spawn reasoning sessions, and its channel credentials SHALL stay in the Messenger process: no goal invocation, session host, or sandbox SHALL receive a Telegram, email, or WhatsApp credential.

Source: RFC 0038 D1, D2 and D3
Scope: v1-mandatory

#### Scenario: Identity and module profile
- **WHEN** Messenger starts
- **THEN** it runs with `type = "staffer"` and the calendar, Telegram, email,
  WhatsApp, and approvals modules
- **AND** it does not load a delivery-tracking module

#### Scenario: Excluded from inbound routing
- **WHEN** an incoming user message is admitted as a goal
- **THEN** Messenger SHALL NOT lead the goal or receive a reasoning session for it
- **AND** Messenger only executes deliveries that are forwarded to it

#### Scenario: Channel credentials never reach a session
- **WHEN** a goal invocation's request results in a Telegram, email, or WhatsApp delivery
- **THEN** Messenger performs the delivery with credentials held only in its own process
- **AND** no credential value appears in the invocation's tool results, sandbox environment, or session host

### Requirement: Approval-Gated Delivery
Sensitive channel sends SHALL remain approval-gated before execution. A send or reply whose recipients do not all resolve uniquely to the live owner SHALL execute only under per-event owner approval or a matching standing rule the owner created, and no goal origin, grant, exposure state, lead, or child SHALL bypass this. A send requested by an exposed invocation to anyone other than the owner SHALL require per-event owner approval unless the matching standing rule pins the message body to a template.

Source: RFC 0038 D4 and D6; RFC 0021
Scope: v1-mandatory

#### Scenario: Gated channel tools
- **WHEN** Messenger starts with approvals enabled
- **THEN** its configured Telegram, email, and WhatsApp send/reply tools require
  approval before production use

#### Scenario: A goal sends to a non-owner without a standing rule
- **WHEN** an unexposed owner goal requests a send to a recipient who does not resolve uniquely to the owner and no owner-created standing rule matches
- **THEN** the send is parked for per-event owner approval and Messenger delivers nothing until the owner approves

#### Scenario: An exposed goal matches a free-text standing rule
- **WHEN** an exposed invocation requests a send to a non-owner recipient that matches a standing rule whose body is not template-pinned
- **THEN** the send is parked for per-event owner approval instead of executing under the rule
