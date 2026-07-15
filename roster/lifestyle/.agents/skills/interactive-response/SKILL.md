---
name: interactive-response
description: Lifestyle interactive response framework — detection, the five response modes (React, Affirm, Follow-up, Answer, React+Reply), and complete worked examples for replying to messages from interactive channels
version: 1.0.0
tools_required:
  - memory_store_fact
  - memory_search
  - memory_recall
  - notify
---

# Lifestyle Interactive Response Skill

## Purpose

Load this skill when processing a message that originated from an interactive
channel (e.g. Telegram) and you need to decide how to respond. It defines the
detection rule, the five response modes, and complete worked examples.

When processing messages that originated from Telegram or other interactive channels, respond interactively. Activated when a REQUEST CONTEXT JSON block is present with a `source_channel` field set to an interactive channel (`telegram_bot`).

**Email is NOT an interactive channel.** Do not reply to, forward, or send emails in response to routed email content. Use `notify(channel="telegram")` if the user needs to be informed about something from an email.

**Spotify is NOT an interactive channel.** Do not reply to, react to, or send Telegram messages in response to routed Spotify connector events. See the "Spotify Connector Events" guidance in AGENTS.md.

## Detection

Check context for a REQUEST CONTEXT JSON block. If present and `source_channel` is user-facing, engage interactive response mode.

## Response Mode Selection

1. **React**: Emoji-only acknowledgment
   - Use when: A preference was noted and no further comment is needed
   - Example: User says "I've been loving Radiohead lately" → React with ✅

2. **Affirm**: Brief confirmation message
   - Use when: The action needs a short, warm confirmation
   - Example: "Saved: you love Thai food, especially green curry."

3. **Follow-up**: Proactive question or suggestion
   - Use when: A captured fact suggests a related preference worth recording, or you want to offer a gentle prompt
   - Example: "Sounds like you're really into jazz. Any favourite artists I should remember?"

4. **Answer**: Substantive response to a direct question
   - Use when: User asked what they've been into, what they like, or for a recall of preferences
   - Example: User asks "What restaurants have I mentioned liking?" → search memory, return a list

5. **React + Reply**: Combined emoji + message
   - Use when: You want immediate acknowledgment and a short substantive reply
   - Example: React with ✅ then "Remembered. That makes Baba Ghanouj your third favourite Middle Eastern spot."

## Complete Examples

### Example 1: Taste Capture from Chat (Affirm)

**User message**: "Just had the most amazing ramen at Ippudo, would definitely go back"

**Actions**:
1. `memory_store_fact(subject="user", predicate="favorite_restaurant", content="Ippudo — excellent ramen, would return", permanence="stable", importance=7.0, tags=["restaurant", "ramen", "japanese"])`
2. `notify(channel="telegram", message="Saved — Ippudo's ramen is on the list.", intent="reply", request_context=...)`

---

### Example 2: Music Opinion Capture (React)

**User message**: "I can't stand country music"

**Actions**:
1. `memory_store_fact(subject="user", predicate="likes_genre", content="dislikes country music — finds it grating", permanence="stable", importance=6.0, tags=["music", "genre", "dislike"])`
2. `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`

---

### Example 3: Current Consumption State (Affirm)

**User message**: "I'm halfway through The Last of Us season 2"

**Actions**:
1. `memory_store_fact(subject="user", predicate="watches", content="currently watching The Last of Us season 2 — halfway through", permanence="volatile", importance=5.0, tags=["tv", "watching", "hbo"])`
2. `notify(channel="telegram", message="Got it — The Last of Us S2, halfway through.", intent="reply", request_context=...)`

---

### Example 4: Preference Query (Answer)

**User message**: "What cuisines do I like?"

**Actions**:
1. `memory_search(query="cuisine preference food likes")`
2. `memory_recall(subject="user")`
3. `notify(channel="telegram", message="From what I've remembered: Thai (especially green curry), Japanese (ramen in particular), and Italian. You've also mentioned loving a good Spanish tapas spread.", intent="reply", request_context=...)`

---

### Example 5: Hobby Capture + Follow-up (React + Reply)

**User message**: "Been getting really into sourdough baking lately, made my third loaf this week"

**Actions**:
1. `memory_store_fact(subject="user", predicate="hobby", content="sourdough baking — actively practicing, third loaf this week", permanence="stable", importance=7.0, tags=["hobby", "baking", "sourdough"])`
2. `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`
3. `notify(channel="telegram", message="Three loaves in a week — that's commitment. Want me to set a recurring reminder for your bake days?", intent="reply", request_context=...)`
