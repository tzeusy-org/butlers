---
name: butler-notifications
description: Usage patterns for the notify() tool — required parameters, intents, and examples
version: 1.1.0
---

### Notify Usage

Call `notify()` to send responses back to the user via the channel they messaged you from.

**REQUIRED PARAMETERS — the tool call WILL FAIL without these:**
- **`message`** (REQUIRED for reply/send intents): Your response text. This is the most important parameter — never omit it.
- **`channel`** (REQUIRED): Extract from `request_context.source_channel` (e.g., "telegram_bot")
- **`request_context`** (REQUIRED): Pass through the exact REQUEST CONTEXT object from your context above. Do NOT rename this to `trace_context` or anything else. Must be a dict/object value, not a JSON string and not a quoted placeholder.
  - Reply/react `request_context` MUST include: `request_id`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`.
  - For Telegram reply/react, `request_context.source_thread_identity` is also required.

**DO NOT REPLY to `telegram_user_client` ingestions.**
If `request_context.source_channel` is `"telegram_user_client"`, you MUST NOT call `notify()` with intent "reply" or "send". These are passively captured messages — the user did not message a butler, so replying would be unexpected and incorrect. You may still process/store the content, but do not send any response back. Only `telegram_bot` (and other direct-message channels) should trigger replies.

**Optional parameters:**
- `intent`: One of "send", "reply", "react"
  - Use "reply" when responding in context of the incoming message
  - Use "react" for emoji-only acknowledgment (message not required for react)
  - Use "send" for new outbound messages
- `emoji`: Required when intent is "react" (e.g., "✅", "👍", "❤️")

### Approval dossier for non-owner delivery

If an outbound tool is advertised with `_why`, `_blast_radius`, `_reversibility`,
and `_evidence`, it is approval-gated. For a target that is not the verified
owner (or cannot be resolved as the owner), include an honest decision dossier:

- `_why`: a concrete, human-readable reason for this delivery.
- `_blast_radius`: `none`, `self`, `contact`, or `external` when known.
- `_reversibility`: `reversible`, `compensable`, or `irreversible` when known.
- `_evidence`: zero or more exact objects with `type` (`fact`, `entity`, `url`,
  or `text`), `ref`, and `note`. Never pass free-form strings as evidence.

The owner-role path is exempt. Do not add these kwargs to a tool whose advertised
schema does not include them. If a gated call returns a retryable dossier error,
correct the named field and retry; do not retry without the required rationale.

```python
email_send_message(
    to="friend@example.com",
    subject="Requested update",
    body="Here is the update you asked for.",
    _why="The recipient explicitly requested this update in the linked thread.",
    _blast_radius="contact",
    _reversibility="compensable",
    _evidence=[{
        "type": "url",
        "ref": "https://example.test/conversation/42",
        "note": "The recipient's request",
    }],
)
```

**Examples**:

```python
ctx = {
    "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
    "source_channel": "telegram_bot",
    "source_endpoint_identity": "switchboard",
    "source_sender_identity": "general",
    "source_thread_identity": "12345",
}

# React only
notify(
    channel="telegram",
    intent="react",
    emoji="✅",
    request_context=ctx
)

# Reply with message
notify(
    channel="telegram",
    message="Done! Here's what I found...",
    intent="reply",
    request_context=ctx
)

# React + reply (call notify twice)
# First react
notify(
    channel="telegram",
    intent="react",
    emoji="✅",
    request_context=ctx
)
# Then reply
notify(
    channel="telegram",
    message="Saved. You now have 12 entries this month.",
    intent="reply",
    request_context=ctx
)
```
