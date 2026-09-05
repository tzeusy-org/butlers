# Tool Quality Patterns

Before/after examples for fixing common issues found during audits.

## Docstring Patterns

**BAD — No guidance on when to use:**
```python
async def memory_store_fact(subject, predicate, object_value):
    """Store a fact."""
```

**GOOD — Purpose, params, return, LLM guidance:**
```python
async def memory_store_fact(subject, predicate, object_value):
    """Store a semantic triple (subject-predicate-object) in long-term memory.

    Use for durable facts about entities (people, places, preferences).
    For ephemeral session context, use memory_store_episode instead.

    Args:
        subject: Entity name or ID (e.g. "user", "Alice").
        predicate: Relationship type. See memory_predicate_list() for valid values.
        object_value: The fact value (string, number, or JSON-serializable dict).

    Returns:
        {"status": "ok", "fact_id": "<uuid>", "created": true|false}
    """
```

**BAD — Missing allowed values:**
```python
async def notify(channel, intent, message):
    """Send a notification."""
```

**GOOD — Enum values listed, hints for common mistakes:**
```python
async def notify(channel, intent, message):
    """Deliver a message to the user via their preferred channel.

    Args:
        channel: Delivery channel — "telegram" | "email" | "whatsapp".
        intent: Delivery intent — "send" (new message) | "reply" (respond to
            prior message, requires request_context) | "react" (emoji reaction).
        message: Message body. Markdown supported for telegram.

    Returns:
        {"status": "ok", "delivery_id": "<uuid>"} on success.
        {"status": "error", "error": "...", "retryable": bool} on failure.
    """
```

## Error Message Patterns

**BAD — Bare exception, no guidance:**
```python
except Exception as exc:
    return {"status": "error", "error": str(exc)}
```

**GOOD — Specific, actionable, retryable:**
```python
except ValueError as exc:
    return {
        "status": "error",
        "error": (
            f"Invalid predicate '{predicate}'. "
            f"Valid predicates: {', '.join(sorted(valid_predicates))}. "
            "Hint: call memory_predicate_list() to see all available predicates."
        ),
        "retryable": False,
    }
```

**BAD — Generic "not found":**
```python
return {"status": "error", "error": "Contact not found"}
```

**GOOD — Names the lookup value, suggests alternative:**
```python
return {
    "status": "error",
    "error": (
        f"No contact found with id={contact_id!r}. "
        "Try contact_search(query=...) to find the correct contact_id."
    ),
    "retryable": False,
}
```

## Tool Overlap Resolution

When two tools on the same butler do similar things, fix by:

1. **Distinct docstring first lines** — make the difference obvious in the first sentence
2. **"Use X instead when..."** cross-references in each tool's docstring
3. **If truly redundant** — remove one and alias if needed for backwards compat
