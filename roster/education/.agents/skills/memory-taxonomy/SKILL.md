---
name: memory-taxonomy
description: Education domain memory taxonomy for concept entity resolution, predicates, permanence, tags, and example fact patterns.
version: 1.0.0
tools_required:
  - memory_store_fact
  - mind_map_node_create
  - mind_map_node_get
  - curriculum_next_node
  - spaced_repetition_pending_reviews
---

# Education Memory Taxonomy Skill

## Purpose

Load this skill when storing education-domain memory facts or resolving a mind
map concept entity for those facts.

### Entity Resolution for Education Concepts

Every mind map node has an `entity_id` field backed by `public.entities`. This entity uniquely
identifies the concept across the butler system and enables memory deduplication: facts stored
with `entity_id` are linked to the canonical entity rather than relying on free-text subject
matching.

**Canonical name pattern:** `'<map_title> > <label>'`
For example, a node labelled "list comprehensions" in the "Python" map has canonical name
`"Python > list comprehensions"`.

**Where to find `entity_id`:**
- `mind_map_node_create()`: returned in the response dict as `entity_id`
- `mind_map_node_get()`: included in the node dict
- `curriculum_next_node()`: included in the node dict
- `spaced_repetition_pending_reviews()`: does NOT return `entity_id`; call
  `mind_map_node_get(node_id=<id>)` for each due node to retrieve its `entity_id`

**Always pass `entity_id` to `memory_store_fact()`** for concept-level facts
(`learning_outcome`, `struggle_area`, `prerequisite_mastered`). This ensures facts are linked
to the correct entity and not silently duplicated by subject-string variation.

Topic-level and user-level facts (`study_pattern`, `learning_preference` with
`subject="user"`) may use `entity_id` when a relevant map-level entity is available, but it is
not required for those predicates.

### Education Domain Taxonomy

**Subject**:
- For topic-level knowledge: topic name (e.g., `"Python"`, `"calculus"`, `"TCP/IP"`)
- For concept-level knowledge: concept name (e.g., `"Python list comprehensions"`, `"recursion"`, `"TCP handshake"`)
- For user-level learning preferences: `"user"`

**Predicates**:
- `learning_outcome`: What the user successfully understood or mastered
- `struggle_area`: Concepts where the user consistently makes errors or expresses confusion
- `prerequisite_mastered`: Foundational knowledge confirmed as solid (feeds into curriculum planning)
- `learning_preference`: User's stated or inferred preferences (e.g., "prefers code examples over theory")
- `study_pattern`: Observed patterns in how, when, or how much the user studies

**Permanence levels**:
- `stable`: Long-term transferable skills that persist across topics (e.g., "user has mastered recursion across languages")
- `standard` (default): Topic-specific knowledge in active study (e.g., "user knows Python list comprehensions")
- `volatile`: Temporary confusion, current struggle areas, or paused study states

**Tags**: Use tags like `mastered`, `struggle`, `python`, `math`, `paused`, `preference`, `pattern`

### Example Facts

```python
# From: user correctly answers quiz on recursion
memory_store_fact(
    subject="recursion",
    predicate="learning_outcome",
    content="user correctly explained base case, recursive case, and call stack behavior",
    permanence="stable",
    importance=8.0,
    tags=["recursion", "mastered", "fundamentals"],
    entity_id=<recursion_node_entity_id>  # from mind_map_node_get() or curriculum_next_node()
)

# From: user repeatedly struggles with closures
memory_store_fact(
    subject="Python closures",
    predicate="struggle_area",
    content="user confused about variable capture semantics in closures — mixes up early and late binding",
    permanence="volatile",
    importance=7.0,
    tags=["python", "closures", "struggle"],
    entity_id=<closures_node_entity_id>  # from mind_map_node_get() or curriculum_next_node()
)

# From: diagnostic — user already knows basic algebra
memory_store_fact(
    subject="algebra",
    predicate="prerequisite_mastered",
    content="user demonstrated solid understanding of algebraic manipulation and equation solving",
    permanence="standard",
    importance=7.0,
    tags=["math", "prerequisite", "algebra"],
    entity_id=<algebra_node_entity_id>  # from the relevant node dict
)

# From: user says "I prefer seeing code examples before theory"
# entity_id not required for user-level preference facts
memory_store_fact(
    subject="user",
    predicate="learning_preference",
    content="prefers concrete code examples before abstract theory",
    permanence="stable",
    importance=8.0,
    tags=["preference", "learning-style"]
)

# From: observing user studies in evening sessions
# entity_id not required for user-level study pattern facts
memory_store_fact(
    subject="user",
    predicate="study_pattern",
    content="tends to study in the evenings (after 8pm), short 20-30 minute sessions",
    permanence="standard",
    importance=5.0,
    tags=["pattern", "study-time"]
)
```
