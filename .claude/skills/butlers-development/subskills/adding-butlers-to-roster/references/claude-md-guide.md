# CLAUDE.md Guide (runtime system prompt)

The system prompt for ephemeral LLM CLI instances spawned by this butler. Every
interaction gets it as context — keep it concise. Reference implementation:
`roster/health/CLAUDE.md`.

## Structure

```markdown
# <Name> Butler

You are the <Name> butler — <one-sentence role description>.

## Your Tools
- **tool_group/action1/action2**: Brief description
- **other_tool**: Brief description
- **calendar_list_events/get_event/create_event/update_event**: Calendar management

## Guidelines
- Key behavioral rule 1
- Key behavioral rule 2
- Domain-specific conventions

## Calendar Usage
- Use calendar tools for domain-relevant scheduling
- Write Butler-managed events to the shared butler calendar, not the user's primary calendar
- Default conflict behavior is `suggest`

## Interactive Response Mode
(pattern below)

### Memory Classification
(pattern below)
```

## Rules

- Under 50 lines for the core section. IRM and Memory Classification add more.
- List every tool from tools.py with a brief description.
- Include behavioral guidelines (ambiguity handling, proactive behaviors, data conventions).
- Imperative tone, not conversational.

## Interactive Response Mode (user-facing butlers)

Every butler that receives routed user messages (Telegram, email, etc.) needs an
Interactive Response Mode section covering:

1. **Detection**: Check for REQUEST CONTEXT JSON block with `source_channel`.
2. **Response Mode Selection** — five modes:
   - **React**: Emoji-only acknowledgment (simple actions)
   - **Affirm**: Brief confirmation message
   - **Follow-up**: Proactive question or suggestion
   - **Answer**: Substantive response to a question
   - **React + Reply**: Combined emoji + message
3. **Complete Examples**: 5-7 full interaction flows showing the mode in action with tool calls.

## Memory Classification (butlers with `[modules.memory]`)

Define a domain-specific taxonomy for `memory_store_fact()`:

- **Subject**: Valid entity types (e.g., user, condition names, medication names)
- **Predicates**: Domain-specific predicates (e.g., `medication`, `dosage`, `symptom_pattern`, `goal`, `preference`)
- **Permanence levels**: `stable` (long-term/chronic), `standard` (default), `volatile` (temporary/acute)
- **Tags**: Domain-specific tags for cross-cutting organization
- **Example facts**: 3-5 complete `memory_store_fact()` calls with realistic data
