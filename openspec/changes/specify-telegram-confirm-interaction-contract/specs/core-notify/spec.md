# Core Notify — Delta

## ADDED Requirements

### Requirement: [TARGET-STATE] Confirm Delivery Intent

A sixth delivery intent, `confirm`, SHALL be supported by `notify()` alongside `send`,
`reply`, `react`, and `insight`. Instead of a one-way message, `intent="confirm"` presents a
bounded, caller-defined set of options to the resolved recipient and, on an interactive
channel, resolves to a chosen `value` delivered back to the calling butler as described by
the `notify-confirm-interaction` capability. This requirement is additive: the existing
`Delivery Intent Validation` requirement's four documented intents and their scenarios are
unchanged.

#### Scenario: Confirm intent requires options

- **WHEN** `notify(intent="confirm", message="Proceed?")` is called with no `options`
- **THEN** the tool returns a structured validation error — `options` is required for
  `intent="confirm"`

#### Scenario: Confirm intent envelope and resolution contract

- **WHEN** `notify(channel="telegram", intent="confirm", options=[{value:"yes",
  label:"Yes"},{value:"no",label:"No"}], message="Proceed with the refund?")` is called
- **THEN** the envelope, callback token, identity, replay, expiry, and reply-to-origin
  behavior follow the `notify-confirm-interaction` capability's requirements in full
- **AND** the tool's immediate response reports `status="ok"` with the created `confirm_id`

#### Scenario: Confirm intent does not affect other intents

- **WHEN** `notify()` is called with `intent` set to `send`, `reply`, `react`, or `insight`
- **THEN** its behavior is exactly as defined by the existing `Delivery Intent Validation`
  requirement, unaffected by the addition of `intent="confirm"`
