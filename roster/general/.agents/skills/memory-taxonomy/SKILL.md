---
name: memory-taxonomy
description: General domain memory classification — subject/predicate taxonomy, permanence levels, tagging strategy, and example facts
version: 1.0.0
---

# Memory Taxonomy — General Butler

This skill defines the classification framework for storing and retrieving freeform facts in the
General butler's memory layer. Use it whenever you call `memory_store_fact` to ensure consistent,
discoverable, and well-prioritized memory entries.

## General Domain Taxonomy

The General butler handles catch-all data that does not fit specialist domains (health, finance,
travel, education, etc.). Use flexible subject/predicate structures.

### Subject

The `subject` anchors the fact to a named entity, concept, or topic:

- **Personal facts**: Use `"user"` for facts about the owner
- **Project/concept**: Use the project or concept name (`"project-alpha"`, `"rust-programming"`)
- **Place or resource**: Use the place or resource name (`"coffee-shops"`, `"vacation-planning"`)

Examples: `"user"`, `"project-alpha"`, `"rust-programming"`, `"vacation-planning"`, `"coffee-shops"`

### Predicates

| Predicate     | When to use |
|---------------|-------------|
| `goal`        | Personal or project goals |
| `preference`  | User preferences not covered by a specialist butler |
| `resource`    | Useful links, articles, or tools |
| `idea`        | Brainstorming notes, future plans |
| `note`        | General observations or reminders |
| `deadline`    | Time-sensitive tasks or dates |
| `status`      | Current state of a project or activity |
| `recommendation` | Recommendations (places, books, tools) |

### Permanence Levels

| Level      | When to use |
|------------|-------------|
| `stable`   | Long-term preferences, recurring patterns unlikely to change |
| `standard` | Most general facts — current state that may change over weeks/months (default) |
| `volatile` | Temporary notes, time-sensitive reminders, one-off tasks |

### Tags

Use tags for cross-cutting organization. Good defaults:

`urgent`, `learning`, `work`, `personal`, `someday-maybe`, `places`, `action-required`

## Example Facts

```python
# From: "I want to learn Rust this year"
memory_store_fact(
    subject="rust-programming",
    predicate="goal",
    content="learn Rust programming language in 2026",
    permanence="standard",
    importance=6.0,
    tags=["learning", "programming", "2026-goals"]
)

# From: "Good coffee shop: Blue Bottle on 5th St"
memory_store_fact(
    subject="coffee-shops",
    predicate="recommendation",
    content="Blue Bottle on 5th St - good coffee",
    permanence="standard",
    importance=4.0,
    tags=["places", "coffee", "local"]
)

# From: "Password reset link expires in 24 hours"
memory_store_fact(
    subject="password-reset",
    predicate="deadline",
    content="password reset link expires in 24 hours",
    permanence="volatile",
    importance=7.0,
    tags=["urgent", "action-required"]
)
```

## Question Answering Flow

When the user asks a question:

1. **Search memory first**: `memory_search(query=<question>)` or `memory_recall(topic=<subject>)`
2. **Search entities**: `entity_search()` with relevant query terms
3. **Combine sources**: Synthesize information from memory and entity storage
4. **Respond**: `notify(channel=<channel>, message=<answer>, intent="reply", request_context=<ctx>)`

Example:
```
User: "What was that coffee shop I liked?"
1. memory_search(query="coffee shop recommendation")
2. entity_search(collection="places", query={"type": "coffee"})
3. Find: "Blue Bottle on 5th St"
4. notify(channel="telegram", message="Blue Bottle on 5th St — you saved that as a good coffee spot.",
          intent="reply", request_context=<from session>)
```

## Extraction Philosophy

- **Extract liberally** — capture facts even from casual notes or tangential remarks
- **Use `standard` by default** — only use `volatile` for urgent/time-sensitive facts, `stable` for long-term preferences
- **Tags enable discovery** — choose tags that support finding facts across different future contexts
- **Importance scale**: 1–10. Urgency and personal significance raise importance; passing remarks lower it
