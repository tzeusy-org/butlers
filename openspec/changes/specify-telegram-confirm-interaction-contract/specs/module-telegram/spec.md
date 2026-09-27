# Telegram Module — Delta

## ADDED Requirements

### Requirement: Confirm Inline Keyboard Rendering

The module's send tools SHALL support rendering a `notify(intent="confirm")` envelope as an
inline keyboard whose buttons carry `cfm1:<confirm_id>:<opt_idx>:<hmac>` callback tokens
(see the `notify-confirm-interaction` capability), one button per offered option, and SHALL
support editing that message to a resolved (`answered`/`expired`) state with the keyboard
removed. This requirement is additive alongside the existing (separately owned) approval
inline-keyboard rendering — the two share no button, token, or state machinery.

#### Scenario: Confirm message carries option buttons

- **WHEN** the Messenger delivers a `confirm` notify envelope with options `[{value:"yes",
  label:"Yes"},{value:"no",label:"No"}]` to the owner over telegram
- **THEN** the message includes a "Yes" and a "No" inline button, each bound to a distinct
  `cfm1:` token naming that option's index

#### Scenario: Message is edited on resolution

- **WHEN** a confirm resolves (answered or expired)
- **THEN** the originating message is edited to reflect the resolved state (the chosen
  option's label, or an expired indicator) and the inline keyboard is removed

#### Scenario: Confirm rendering does not alter approval rendering

- **WHEN** an `approval_request` envelope is delivered over telegram
- **THEN** it renders exactly per the existing approval inline-keyboard requirement,
  unaffected by the addition of confirm rendering
