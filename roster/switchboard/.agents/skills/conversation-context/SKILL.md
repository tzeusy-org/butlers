---
name: conversation-context
description: How to use conversation history when routing messages — loading strategy, usage rules, and worked examples
trigger_patterns:
  - "conversation history"
  - "follow-up message"
  - "routing with context"
  - "history-aware routing"
---

# Conversation Context Skill

## Purpose

When routing messages from real-time messaging channels or email, you may receive recent conversation history alongside the current message. This skill explains how to interpret that history and how to use it (and when not to use it) to improve routing accuracy.

---

## History Loading Strategy

| Channel | What you receive |
|---|---|
| **Real-time messaging** (Telegram, WhatsApp, Slack, Discord) | Union of last 15 minutes OR last 30 messages, whichever is more, ordered chronologically |
| **Email** | Full email chain, truncated to 50,000 tokens — preserves newest messages, discards oldest when over limit |
| **Other channels** (API, MCP) | No history loaded |

---

## History Format

When conversation history is provided, it appears before the current message in this format:

```
## Recent Conversation History

**sender_id** (timestamp):
user message content

**butler → butler_name** (timestamp):
butler response content

---

## Current Message

<current message to route>
```

- Lines prefixed with `**sender_id**` are user messages
- Lines prefixed with `**butler → butler_name**` are responses from specialist butlers (e.g., `**butler → relationship**`, `**butler → finance**`)

---

## Usage Rules

**IMPORTANT WARNINGS — read carefully:**

- **Prior messages in the history MAY be completely unrelated to the current message.** Do not assume topical continuity.
- **ONLY route the CURRENT message.** Do NOT attempt to re-route or re-process prior messages from the history.
- **Use history context ONLY to improve routing of the CURRENT message.** Look for follow-up language ("it", "that", "also", "too") or explicit references to previous topics.
- **When in doubt, route based on the current message alone.** History is supplementary, not primary.

Use the conversation history to:

- Understand ongoing context and previous topics
- Detect conversation continuity (e.g., follow-up questions, references to "it", "that", etc.)
- Route more accurately when the current message alone would be ambiguous
- Maintain consistency with previous routing decisions in the same thread

---

## Worked Examples

### Example 1: Follow-up question

**History:**
```
**user123** (2026-02-16T10:00:00Z):
Track my metformin 500mg twice daily

**butler → health** (2026-02-16T10:00:05Z):
Done! I've recorded metformin 500mg twice daily.
```

**Current message:** "When should I take it?"

**Action:** Call `route_to_butler(butler="health", prompt="When should I take my metformin 500mg? (Context: User previously asked to track metformin 500mg twice daily)")`

**Response:** "Routed to health butler for medication timing question (continuation of medication tracking conversation)."

---

### Example 2: Referential follow-up using butler response as context

**History:**
```
**user456** (2026-02-16T09:55:00Z):
Dua um lives in 71 nim road 804975

**butler → relationship** (2026-02-16T09:55:05Z):
Got it! I've stored Dua um's address as 71 nim road 804975.
```

**Current message:** "So does da pe pe"

**Action:** Call `route_to_butler(butler="relationship", prompt="Da pe pe also lives at 71 nim road 804975. (Context: User previously stored Dua um's address at 71 nim road 804975, and is now saying Da pe pe lives there too)")`

**Response:** "Routed to relationship butler — user is indicating Da pe pe shares the same address just stored."

---

### Example 3: Multi-turn context with finance

**History:**
```
**user789** (2026-02-16T14:20:00Z):
My electric bill is due tomorrow

**butler → finance** (2026-02-16T14:20:05Z):
Got it! I've noted your electric bill is due tomorrow.
```

**Current message:** "What was last month's bill amount?"

**Action:** Call `route_to_butler(butler="finance", prompt="What was last month's electric bill amount? (Context: User's electric bill is due tomorrow)")`

**Response:** "Routed to finance butler for bill history query (continuation of bill tracking conversation)."

---

### Example 4: Bare confirmation of a proposed action

**History:**
```
**user101** (2026-03-15T13:54:05Z):
send an email to tze.notifications.dev@gmail.com with the content 'hello world'.

**butler → general** (2026-03-15T13:56:17Z):
I can do that, but I need a quick confirmation because this request arrived via routed content.

Confirm I should send an email to `tze.notifications.dev@gmail.com` with body: `hello world` (no subject)? Reply "yes" or tell me edits.
```

**Current message:** "yes"

**Reasoning:** "yes" by itself is meaningless. The history shows the user is confirming a previously proposed email send. Resolve the reference and route with the full resolved action.

**Action:** Call `notify(channel="email", message="hello world", recipient="tze.notifications.dev@gmail.com", intent="send")`

**Response:** "User confirmed the previously proposed email — sent email to tze.notifications.dev@gmail.com with body 'hello world'."

**WRONG approach:** `route_to_butler(butler="general", prompt="yes")` — the target butler has NO history and will not understand what "yes" means.

---

### Example 5: Short reply selecting an option

**History:**
```
**user202** (2026-03-15T10:00:00Z):
What flights do I have coming up?

**butler → travel** (2026-03-15T10:00:10Z):
You have two upcoming flights:
1. SQ321 SIN→LHR on March 20
2. UA456 SFO→NRT on April 3
Which one would you like details on?
```

**Current message:** "the first one"

**Reasoning:** "the first one" only makes sense with the history. Resolve the reference before routing.

**Action:** Call `route_to_butler(butler="travel", prompt="Show me details for flight SQ321 SIN→LHR on March 20. (Context: User asked about upcoming flights and selected this one from a list)")`

**Response:** "Routed to travel butler — user selected flight SQ321 from the list."
