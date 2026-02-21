---
name: butler-notifications
description: Usage patterns for the notify() tool — required parameters, intents, and examples
version: 1.0.0
---

### Notify Usage

Call `notify()` to send responses back to the user via the channel they messaged you from.

**REQUIRED PARAMETERS — the tool call WILL FAIL without these:**
- **`message`** (REQUIRED for reply/send intents): Your response text. This is the most important parameter — never omit it.
- **`channel`** (REQUIRED): Extract from `request_context.source_channel` (e.g., "telegram")
- **`request_context`** (REQUIRED): Pass through the exact REQUEST CONTEXT object from your context above. Do NOT rename this to `trace_context` or anything else.
  - Reply/react `request_context` MUST include: `request_id`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`.
  - For Telegram reply/react, `request_context.source_thread_identity` is also required.

**Optional parameters:**
- `intent`: One of "send", "reply", "react"
  - Use "reply" when responding in context of the incoming message
  - Use "react" for emoji-only acknowledgment (message not required for react)
  - Use "send" for new outbound messages
- `emoji`: Required when intent is "react" (e.g., "✅", "👍", "❤️")

**Examples**:

```python
# React only
notify(
    channel="telegram",
    intent="react",
    emoji="✅",
    request_context=<the REQUEST CONTEXT object from your context above>
)

# Reply with message
notify(
    channel="telegram",
    message="Done! Here's what I found...",
    intent="reply",
    request_context=<the REQUEST CONTEXT object from your context above>
)

# React + reply (call notify twice)
# First react
notify(
    channel="telegram",
    intent="react",
    emoji="✅",
    request_context=<the REQUEST CONTEXT object from your context above>
)
# Then reply
notify(
    channel="telegram",
    message="Saved. You now have 12 entries this month.",
    intent="reply",
    request_context=<the REQUEST CONTEXT object from your context above>
)
```
