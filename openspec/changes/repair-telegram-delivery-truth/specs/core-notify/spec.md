## MODIFIED Requirements

### Requirement: [TARGET-STATE] Notify Response Envelope

The implementation SHALL provide the behavior described by this requirement.
Successful delivery returns `notify_response.v1` with `status="ok"` and delivery
metadata. Failed delivery returns `status="error"` with canonical error class and message.
An end-to-end notification SHALL be considered successfully delivered
only when the routed response is successful and contains a nested
`notify_response.v1` whose status is `ok`, whose delivery channel matches the
resolved request channel, and whose non-empty `delivery_id` is supplied by the
channel's adopted confirmation contract. Telegram message sends require the
provider's message id; provider-confirmed operations that return no native id,
and existing channels whose provider exposes no receipt id, MAY use their
established stable correlation reference. A Telegram message-send request id
or generated UUID SHALL NOT substitute for its provider message receipt.
Missing, malformed, mismatched, or error
responses SHALL never be persisted or returned as sent or delivered.

#### Scenario: Successful delivery response
- **WHEN** the Messenger successfully delivers the message
- **THEN** the notify tool returns a response with `status="ok"` and `delivery.channel` and `delivery.delivery_id`
- **AND** the routed response contains nested `notify_response.v1`, the requested channel, and a non-empty confirmed `delivery_id`
- **AND** only then may notification and attention evidence be recorded as sent or delivered

#### Scenario: Failed delivery response
- **WHEN** the Messenger fails to deliver
- **THEN** the notify tool returns a response with `status="error"`, `error.class`, and `error.message`, or the existing explicit uncertain-transport classification when provider start cannot be excluded
- **AND** no notification, ledger, or conversation-history record claims sent or delivered

#### Scenario: Nested route failure is not delivery
- **WHEN** the outer route call completes but `route_response.v1` or its nested `notify_response.v1` reports an error
- **THEN** the notify boundary propagates the nested failure
- **AND** it does not infer success from the absence of a top-level transport exception

#### Scenario: Missing or malformed receipt is not delivery
- **WHEN** the nested response is absent, malformed, names a different channel, or has an empty delivery id
- **THEN** the result is not classified, persisted, or returned as sent or delivered
