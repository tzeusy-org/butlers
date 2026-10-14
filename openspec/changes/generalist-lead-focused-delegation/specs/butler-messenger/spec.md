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
Sensitive channel sends SHALL remain approval-gated before execution. A send or reply whose recipients do not all resolve uniquely to the live owner SHALL execute only under per-event owner approval or a matching standing rule the owner created, and no goal origin, grant, exposure state, lead, or child SHALL bypass this. A send requested by an exposed invocation to anyone other than the owner SHALL require per-event owner approval unless the matching standing rule is template-pinned (`REQ-capability-authorization-006`). Messenger's service-plane delivery endpoint (today `route.execute` on Messenger) SHALL apply the same non-owner rule to every delivery that Switchboard `deliver()` hands it, including deliveries from deterministic service-plane senders.

Source: RFC 0038 D4, D6 and D7; RFC 0021
Scope: v1-mandatory

#### Scenario: Gated channel tools
- **WHEN** Messenger starts with approvals enabled
- **THEN** its configured Telegram, email, and WhatsApp send/reply tools require
  approval before production use

#### Scenario: A goal sends to a non-owner without a standing rule
- **WHEN** an unexposed owner goal requests a send to a recipient who does not resolve uniquely to the owner and no owner-created standing rule matches
- **THEN** the send is parked for per-event owner approval and Messenger delivers nothing until the owner approves

#### Scenario: An exposed goal matches a free-text standing rule
- **WHEN** an exposed invocation requests a send to a non-owner recipient that matches a standing rule that is not template-pinned
- **THEN** the send is parked for per-event owner approval instead of executing under the rule

#### Scenario: A deterministic sender gets no bypass
- **WHEN** a scheduled job, with no goal, asks Switchboard `deliver()` for a message to a recipient who does not resolve uniquely to the owner and no owner-created standing rule matches
- **THEN** Messenger's delivery gate parks the delivery for per-event owner approval and sends nothing until the owner approves
- **AND** once the owner approves, Messenger's approvals executor dispatches the stored envelope exactly as approved

## ADDED Requirements

### Requirement: Inert-link delivery for exposed notifications
Every `notify.v1` envelope that Switchboard `deliver()` hands Messenger SHALL carry a `render_links_inert` flag. Switchboard SHALL set it from server-held facts only: true when the verified broker assertion of the `notify` call was exposed, or when the delivery comes from a reminder or other runtime-created task recorded as created under exposure, or when a deterministic sender marks its content as drawn from tainted or non-owner-authority records it read. A deterministic sender MAY set the flag true but never clear it, and no tool argument SHALL set or clear it. When the flag is true, Messenger's delivery endpoint SHALL deliver with link previews disabled and every URL rendered as inert text that the channel neither links nor fetches. When the resolved channel adapter cannot guarantee both, Messenger SHALL deliver nothing and SHALL park the delivery for per-event owner approval (`REQ-core-notify-033`).

ID: REQ-butler-messenger-003
Source: RFC 0038 D4, D6 and owner decision 3
Scope: v1-mandatory

#### Scenario: An exposed owner notification on Telegram
- **WHEN** Messenger receives an envelope for the owner's Telegram chat with `render_links_inert` true and a message containing a URL
- **THEN** it sends with link previews disabled and the URL as inert text that Telegram does not turn into a link

#### Scenario: A model cannot clear the flag
- **WHEN** an exposed invocation's `notify` call includes an argument asking for link previews
- **THEN** Switchboard still sets `render_links_inert` true and the argument has no effect

#### Scenario: A channel that cannot render inert links
- **WHEN** Messenger receives an envelope with `render_links_inert` true for a channel whose adapter cannot disable previews or neutralise URLs
- **THEN** nothing is delivered and the delivery is parked for per-event owner approval
