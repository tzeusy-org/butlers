## MODIFIED Requirements

### Requirement: Telegram API Integration

The implementation SHALL provide the behavior described by this requirement.
The module uses httpx to call Telegram Bot API endpoints. Telegram send and
reply operations SHALL report success only after an HTTP success response
contains a JSON object with `ok` exactly `true`, a result object, and a positive
integer `result.message_id`. Boolean, zero, negative, missing, string, float, or
otherwise malformed message identifiers are invalid. HTTP success alone is not
provider acceptance. An invalid response SHALL record error rather than success
and SHALL NOT return a successful adapter result.

#### Scenario: Send message
- **WHEN** a send_message tool is invoked with `chat_id` and `text`
- **THEN** a POST to `https://api.telegram.org/bot{token}/sendMessage` is made
- **AND** the response JSON is returned
- **AND** success returns the validated response containing the positive integer `result.message_id`

#### Scenario: Reply to message
- **WHEN** a reply_to_message tool is invoked with `chat_id`, `message_id`, and `text`
- **THEN** a sendMessage call is made with `reply_to_message_id` set
- **AND** success requires the same validated provider receipt as an ordinary send

#### Scenario: Set webhook
- **WHEN** `webhook_url` is configured and `on_startup` runs
- **THEN** a POST to `setWebhook` API is made with the configured URL

#### Scenario: Telegram application error on HTTP success
- **WHEN** Telegram returns HTTP success with invalid JSON, a non-object body, `ok` other than exactly `true`, or no valid positive integer `result.message_id`
- **THEN** the operation fails as a provider-delivery error
- **AND** no successful Telegram audit or delivery receipt is emitted
