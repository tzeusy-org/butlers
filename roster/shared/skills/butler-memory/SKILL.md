---
name: butler-memory
description: Memory classification framework — permanence levels, tagging strategy, and extraction philosophy
version: 1.0.0
---

### Memory Classification

Extract facts from conversational messages and store them using the butler's domain tools and memory tools.

**Permanence** determines how long facts persist — choose the level that matches how stable the information is:
- `permanent`: Facts unlikely to ever change (identity, birth dates)
- `stable`: Facts that change slowly over months or years (workplace, location, chronic conditions)
- `standard` (default): Current state that may change over weeks or months (active projects, interests)
- `volatile`: Temporary states or rapidly changing information (acute symptoms, time-sensitive reminders)

**Tags** enable cross-cutting queries and discovery. Choose tags that support finding facts across different contexts.

**Extraction philosophy**: Capture facts proactively from conversational messages, even if tangential to the main request. Use appropriate permanence and importance levels to ensure useful recall later.
