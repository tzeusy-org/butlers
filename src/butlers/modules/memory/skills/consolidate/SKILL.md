# Memory Consolidation

You are performing memory consolidation for the butler ecosystem. Review the episodes below and extract durable knowledge.

## Instructions

1. **New Facts**: Extract facts with subject-predicate-content structure. Classify permanence:
   - `permanent`: Identity, medical, biographical facts that never change
   - `stable`: Long-term preferences, professional info (~346-day half-life)
   - `standard`: Current interests, opinions, ongoing projects (~87-day half-life)
   - `volatile`: Temporary states, short-term plans (~23-day half-life)
   - `ephemeral`: One-off events, what happened today (~7-day half-life)

2. **Updated Facts**: If an episode contradicts or updates an existing fact, specify which fact to supersede.

3. **New Rules**: Extract behavioral patterns worth remembering as candidate rules.

4. **Confirmations**: If episodes support existing facts without changing them, list those fact IDs.

## Output Format

Respond with a JSON block:

```json
{
  "new_facts": [
    {"subject": "...", "predicate": "...", "content": "...", "permanence": "...", "importance": 5.0, "tags": []}
  ],
  "updated_facts": [
    {"target_id": "uuid-of-existing-fact", "subject": "...", "predicate": "...", "content": "...", "permanence": "..."}
  ],
  "new_rules": [
    {"content": "...", "tags": []}
  ],
  "confirmations": ["uuid-of-fact-1", "uuid-of-fact-2"]
}
```

## Guidelines

- Do NOT extract ephemeral small talk or greetings
- Do NOT duplicate existing facts that haven't changed
- Do NOT create rules that duplicate existing rules
- When updating a fact, always specify the target_id of the fact being superseded
- Set importance on a 1-10 scale (1=trivial, 5=normal, 10=critical)
- Prefer fewer, higher-quality extractions over many low-quality ones
