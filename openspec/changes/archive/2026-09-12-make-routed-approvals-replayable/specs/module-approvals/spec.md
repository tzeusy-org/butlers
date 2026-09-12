## ADDED Requirements

### Requirement: Pending Actions Store Replayable Executable Commands

An inline approval producer MUST persist the exact registered native tool name and a
`tool_args` object accepted by that tool's handler. Executable arguments MUST NOT
contain routing-only fields, and the same materialized command values MUST be used for
immediate execution when approval is not required.

#### Scenario: Routed delivery is parked

- **WHEN** an outbound routed delivery requires approval
- **THEN** the pending action stores the registered native delivery tool name
- **AND** `tool_args` contains every required handler argument and no routing-only argument

#### Scenario: Immediate and deferred execution are equivalent

- **WHEN** equivalent routed deliveries take the immediate and approval-replay paths
- **THEN** both invoke the same native handler with the same normalized argument values

#### Scenario: Stored command is malformed

- **WHEN** an approved historical action cannot be accepted by its registered handler
- **THEN** dispatch MUST fail without rewriting or guessing its executable arguments
- **AND** the action MUST remain `approved` with `execution_result = null`
- **AND** an immutable `action_execution_failed` event MUST be recorded

## MODIFIED Requirements

### Requirement: Defense-in-Depth at the Delivery Layer

The approval gate operates at two layers. Both MUST enforce gating independently.

**Layer 1 - MCP tool wrapping** (gate.py): Intercepts gated tool calls at the MCP
boundary before the tool handler runs. This is the primary gate for direct tool
invocations.

**Layer 2 (inline delivery gate)** (`core_tools/_notifications.py` and
`core_tools/_routing.py`): Outbound delivery paths call module methods directly
(e.g., `_send_email()`, `_send_message()`), bypassing MCP tool wrappers entirely.
An inline approval gate MUST re-enforce role-based gating at this layer for every
outbound channel. `notify()` (`_notifications.py`) enforces the gate on all channels:
the email channel via `check_email_recipient` and every non-email channel via the
channel-general `check_recipient` guard. Messenger `route.execute` synchronous
delivery (`_routing.py`) likewise gates email via `check_email_recipient` and
Telegram, WhatsApp, and future non-email channels via `check_recipient`.

#### Scenario: route.execute enforces approval gate for email delivery

- **WHEN** the Messenger's `route.execute` handler processes a `notify.v1` envelope with `channel="email"`
- **THEN** it MUST resolve the target contact by email address via `public.contacts`
- **AND** if the target contact is NOT an owner, it MUST check standing approval rules
- **AND** if no standing rule matches, delivery MUST be blocked with a descriptive error
- **AND** if the target is an owner, delivery proceeds without rule check

#### Scenario: route.execute enforces approval gate for telegram delivery

- **WHEN** the Messenger's `route.execute` handler processes a `notify.v1` envelope with `channel="telegram"` and `intent` of `"send"` or `"reply"`
- **THEN** it MUST resolve the target contact by telegram chat ID via `public.contacts`
- **AND** if the target contact is NOT an owner, it MUST check standing approval rules
- **AND** if no standing rule matches, delivery MUST be blocked with a descriptive error
- **AND** if the target is an owner, delivery proceeds without rule check

#### Scenario: route.execute enforces approval gate for WhatsApp delivery

- **WHEN** the Messenger's `route.execute` handler processes a `notify.v1` envelope with `channel="whatsapp"` and `intent` of `"send"` or `"reply"`
- **THEN** it MUST resolve the target contact by WhatsApp recipient identity via `public.contacts`
- **AND** if the target contact is NOT an owner, it MUST check standing approval rules
- **AND** if no standing rule matches, delivery MUST be blocked with a descriptive error
- **AND** if the target is an owner, delivery proceeds without rule check

#### Scenario: route.execute skips gate for emoji reactions

- **WHEN** the Messenger's `route.execute` handler processes a `notify.v1` envelope with `channel="telegram"` and `intent="react"`
- **THEN** the inline approval gate is NOT applied (reactions are low-risk, non-content operations)

#### Scenario: All channels have parity

- **WHEN** a new outbound channel is added to the Messenger butler
- **THEN** the `route.execute` handler MUST include an inline approval gate for that channel matching the email/telegram/WhatsApp pattern
- **AND** the absence of an inline gate for any outbound channel is a spec violation
