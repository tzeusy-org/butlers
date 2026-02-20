---
name: data-organizer
description: Structured patterns for organizing collections and entities in the General butler's freeform data store
trigger_patterns:
  - organize my data
  - set up collections
  - how should I structure
  - clean up my entities
  - merge duplicates
  - archive old data
---

# Data Organizer

This skill provides structured patterns, conventions, and workflows for organizing freeform data in the General butler's JSONB-based entity store.

## Quick Start

The General butler stores arbitrary JSON entities in named collections. Use this skill when you need to:
- Design a collection taxonomy
- Create consistent entity schemas
- Query entities effectively
- Maintain data hygiene over time

## Collection Naming Conventions

Collections organize entities by domain or purpose. Follow these patterns for consistency:

### Format Rules
- **kebab-case**: Use lowercase letters, digits, and hyphens only (`projects`, `reading-list`)
- **Singular nouns**: Collections are containers, so use singular form (`bookmark`, not `bookmarks`)
- **Domain prefixes**: For complex taxonomies, use prefixes (`work-project`, `personal-project`)
- **No consecutive hyphens**: `web-dev` not `web--dev`
- **Start with a letter**: `project-alpha` not `2026-project`

### Common Collection Patterns

#### By Domain
```
personal-note         # Personal journal entries
work-task            # Work-related tasks
learning-resource    # Educational materials
```

#### By Type
```
bookmark             # Web links and references
recipe               # Cooking recipes
contact              # People and contact info
project              # Projects and initiatives
```

#### By Status/Lifecycle
```
inbox                # Unsorted incoming items
active-project       # Currently active projects
archive              # Historical records
```

#### Recommendation
Start simple with top-level collections (`project`, `note`, `bookmark`). Add domain prefixes only when you have overlapping types across domains.

## Entity Schema Templates

Entities are freeform JSONB, but consistency helps with querying and maintenance. Here are proven templates:

### Template 1: Project

Track initiatives, goals, or multi-step endeavors.

```json
{
  "title": "Build AI Agent Framework",
  "status": "active",
  "priority": "high",
  "description": "A framework for long-running AI butlers with MCP integration",
  "goals": [
    "Core infrastructure complete",
    "Three working butlers deployed"
  ],
  "milestones": [
    {
      "name": "v1 MVP",
      "due": "2026-03-01",
      "status": "in_progress"
    }
  ],
  "tags": ["ai", "framework", "mcp"],
  "started_at": "2026-01-15",
  "updated_at": "2026-02-09",
  "notes": "Using Python 3.12, FastMCP, Claude Agent SDK"
}
```

**Key fields:**
- `title` (required): Human-readable name
- `status` (required): Enum-like value (`active`, `paused`, `completed`, `archived`)
- `priority`: `low` | `medium` | `high` | `critical`
- `tags`: Array of strings for filtering
- Timestamps: `started_at`, `updated_at`, `completed_at`

### Template 2: Bookmark

Save web links, articles, resources, and references.

```json
{
  "url": "https://example.com/article",
  "title": "Effective AI Agent Patterns",
  "description": "Deep dive into agent architecture for production systems",
  "tags": ["ai", "architecture", "reference"],
  "category": "technical",
  "added_at": "2026-02-09",
  "read": false,
  "rating": null,
  "notes": "Referenced in project design docs"
}
```

**Key fields:**
- `url` (required): The link
- `title` (required): Page title or custom label
- `tags`: Array for multi-dimensional categorization
- `category`: Primary classification (`technical`, `personal`, `news`, etc.)
- `read`: Boolean flag for tracking
- `rating`: Numeric score (1-5) or null

### Template 3: Note

Capture thoughts, journal entries, meeting notes, or observations.

```json
{
  "title": "Daily Standup - Feb 9",
  "content": "Completed the General butler data store tools. Next: create skills for common workflows.",
  "note_type": "journal",
  "tags": ["standup", "progress"],
  "created_at": "2026-02-09T10:00:00Z",
  "related_entities": [
    "uuid-of-related-project"
  ],
  "private": false
}
```

**Key fields:**
- `title`: Optional subject line
- `content` (required): Main text body (markdown supported)
- `note_type`: `journal` | `meeting` | `idea` | `reference` | `task`
- `related_entities`: Array of UUIDs linking to other entities
- `private`: Boolean for visibility control

### Template 4: List

Organize items into ordered or unordered collections (shopping, reading queue, etc.).

```json
{
  "title": "2026 Reading List",
  "description": "Technical books to read this year",
  "list_type": "reading_queue",
  "items": [
    {
      "title": "Designing Data-Intensive Applications",
      "author": "Martin Kleppmann",
      "status": "reading",
      "priority": 1
    },
    {
      "title": "The Pragmatic Programmer",
      "author": "Hunt & Thomas",
      "status": "pending",
      "priority": 2
    }
  ],
  "tags": ["reading", "technical", "books"],
  "created_at": "2026-01-01",
  "updated_at": "2026-02-09"
}
```

**Key fields:**
- `title` (required): List name
- `list_type`: `reading_queue` | `shopping` | `todo` | `watchlist` | `general`
- `items` (required): Array of structured items (each item can have custom fields)
- `tags`: For categorization

### Template 5: Recipe

Store cooking recipes with ingredients and instructions.

```json
{
  "title": "Sourdough Bread",
  "cuisine": "French",
  "prep_time_minutes": 30,
  "cook_time_minutes": 45,
  "servings": 8,
  "difficulty": "intermediate",
  "ingredients": [
    {"item": "bread flour", "amount": "500g"},
    {"item": "sourdough starter", "amount": "100g"},
    {"item": "water", "amount": "350ml"},
    {"item": "salt", "amount": "10g"}
  ],
  "instructions": [
    "Mix flour and water, autolyse for 30 minutes",
    "Add starter and salt, knead for 10 minutes",
    "Bulk ferment for 4-6 hours with stretch-and-folds",
    "Shape and proof for 2-3 hours",
    "Bake at 230°C for 45 minutes"
  ],
  "tags": ["bread", "sourdough", "fermentation"],
  "rating": 5,
  "notes": "Best when baked in a Dutch oven",
  "source_url": null
}
```

**Key fields:**
- `title` (required): Recipe name
- `cuisine`: Type or origin
- `ingredients` (required): Array of objects with `item` and `amount`
- `instructions` (required): Ordered array of steps
- `tags`: For discovery
- `rating`: 1-5 scale

### Template 6: Contact

Store people, organizations, or contact information.

```json
{
  "name": "Jane Smith",
  "contact_type": "professional",
  "email": "jane@example.com",
  "phone": "+1-555-0123",
  "company": "Acme Corp",
  "role": "Engineering Manager",
  "tags": ["colleague", "engineering", "networking"],
  "notes": "Met at conference 2025, working on similar AI projects",
  "last_contact": "2026-01-15",
  "social": {
    "linkedin": "https://linkedin.com/in/janesmith",
    "github": "https://github.com/janesmith"
  }
}
```

**Key fields:**
- `name` (required): Full name or organization
- `contact_type`: `personal` | `professional` | `business`
- `email`, `phone`: Primary contact methods
- `tags`: For grouping and filtering
- `last_contact`: ISO date of last interaction

## JSONB Query Patterns

The General butler uses PostgreSQL's JSONB containment operator (`@>`) with a GIN index for efficient querying.

### Basic Containment

Find entities with specific top-level fields:

```python
# Find all active projects
await entity_search(
    pool,
    collection_name="project",
    query={"status": "active"}
)

# Find high-priority items
await entity_search(
    pool,
    collection_name="project",
    query={"priority": "high"}
)
```

### Nested Field Matching

Query nested objects using path notation:

```python
# Find projects with specific milestone status
await entity_search(
    pool,
    collection_name="project",
    query={
        "milestones": [
            {"status": "in_progress"}
        ]
    }
)
```

**Note**: JSONB containment requires exact substructure match. The query `{"milestones": [{"status": "in_progress"}]}` matches entities where `milestones` contains at least one object with `status: "in_progress"`, but it also requires other fields in that milestone object to match if present in the query.

### Tag Filtering

Tags are arrays, so use array containment:

```python
# Find entities tagged with "ai"
await entity_search(
    pool,
    collection_name="bookmark",
    query={"tags": ["ai"]}
)

# Find entities with multiple tags (AND logic via containment)
# This finds entities where tags array contains BOTH "ai" AND "reference"
await entity_search(
    pool,
    query={"tags": ["ai", "reference"]}
)
```

**Limitation**: The `@>` operator requires the queried array to be a subset of the stored array. For OR logic across tags, you'll need to run multiple queries or use a script to post-process results.

### Combining Filters

Combine multiple field queries in a single containment check:

```python
# Find unread technical bookmarks
await entity_search(
    pool,
    collection_name="bookmark",
    query={
        "read": False,
        "category": "technical"
    }
)
```

### Full-Text Search Alternative

For text content searches (not supported by basic containment), consider:
1. Fetching all entities and filtering in Python
2. Adding a separate full-text search index in a future migration
3. Using regex patterns on exported data

### Performance Tips

- **Use collection_name filter**: Always specify the collection when possible to reduce scan size
- **Index coverage**: The GIN index on `entities.data` covers all JSONB queries
- **Avoid wildcards**: Containment is exact-match; partial string matching requires fetching all entities
- **Query specificity**: More specific queries (more fields) = faster results

## Data Hygiene Workflows

Over time, entity stores accumulate duplicates, stale data, and inconsistencies. Use these workflows to maintain quality.

### Workflow 1: Deduplication

**Goal**: Identify and merge duplicate entities within a collection.

**Steps:**
1. **Export the collection**:
   ```python
   entities = await collection_export(pool, "bookmark")
   ```

2. **Identify duplicates**: Group by a unique key (e.g., `url` for bookmarks, `title` for projects):
   ```python
   from collections import defaultdict
   
   seen = defaultdict(list)
   for entity in entities:
       key = entity["data"].get("url")
       if key:
           seen[key].append(entity)
   
   duplicates = {k: v for k, v in seen.items() if len(v) > 1}
   ```

3. **Merge duplicates**: For each duplicate group, choose a canonical entity (e.g., oldest by `created_at` or most complete by field count), then merge fields:
   ```python
   for url, dupes in duplicates.items():
       # Sort by created_at to prefer oldest
       dupes_sorted = sorted(dupes, key=lambda e: e["created_at"])
       canonical = dupes_sorted[0]
       
       # Merge fields from other duplicates
       merged_data = canonical["data"].copy()
       for dupe in dupes_sorted[1:]:
           for field, value in dupe["data"].items():
               if field not in merged_data:
                   merged_data[field] = value
       
       # Update canonical entity
       await entity_update(pool, canonical["id"], merged_data)
       
       # Delete duplicates
       for dupe in dupes_sorted[1:]:
           await entity_delete(pool, dupe["id"])
   ```

**Caution**: This is a destructive operation. Consider exporting a backup before running.

### Workflow 2: Archive Stale Entities

**Goal**: Move old or inactive entities to an archive collection to reduce active data clutter.

**Steps:**
1. **Create an archive collection**:
   ```python
   await collection_create(pool, "archive", "Historical entities no longer active")
   ```

2. **Define staleness criteria** (e.g., `status: "completed"` and `completed_at` older than 6 months):
   ```python
   from datetime import datetime, timedelta
   
   cutoff = datetime.now() - timedelta(days=180)
   ```

3. **Fetch candidates**:
   ```python
   all_projects = await entity_search(pool, collection_name="project")
   stale = [
       e for e in all_projects
       if e["data"].get("status") == "completed"
       and datetime.fromisoformat(e["data"].get("completed_at", "2099-12-31")) < cutoff
   ]
   ```

4. **Move to archive**: Create new entities in `archive` collection, then delete originals:
   ```python
   for entity in stale:
       # Add source collection to metadata
       archive_data = entity["data"].copy()
       archive_data["_archived_from"] = "project"
       archive_data["_archived_at"] = datetime.now().isoformat()
       
       await entity_create(pool, "archive", archive_data)
       await entity_delete(pool, entity["id"])
   ```

**Alternative**: Add an `archived: true` field instead of moving to a separate collection, then filter queries with `{"archived": False}`.

### Workflow 3: Normalize Tags

**Goal**: Ensure consistent tag naming (e.g., `ai` vs `AI` vs `artificial-intelligence`).

**Steps:**
1. **Audit existing tags**:
   ```python
   all_entities = await entity_search(pool)  # All collections
   tag_set = set()
   for entity in all_entities:
       tags = entity["data"].get("tags", [])
       tag_set.update(tags)
   
   print(sorted(tag_set))
   ```

2. **Define a canonical tag mapping**:
   ```python
   tag_map = {
       "AI": "ai",
       "artificial-intelligence": "ai",
       "ML": "machine-learning",
       "web-dev": "web-development"
   }
   ```

3. **Update entities**:
   ```python
   for entity in all_entities:
       tags = entity["data"].get("tags", [])
       normalized = [tag_map.get(tag, tag) for tag in tags]
       
       if normalized != tags:
           await entity_update(pool, entity["id"], {"tags": normalized})
   ```

### Workflow 4: Schema Validation

**Goal**: Ensure all entities in a collection conform to an expected schema.

**Steps:**
1. **Define required fields** (e.g., for `project`: `title`, `status`):
   ```python
   required_fields = ["title", "status"]
   ```

2. **Validate entities**:
   ```python
   projects = await entity_search(pool, collection_name="project")
   invalid = []
   
   for entity in projects:
       missing = [f for f in required_fields if f not in entity["data"]]
       if missing:
           invalid.append((entity["id"], missing))
   ```

3. **Fix or flag invalid entities**:
   ```python
   for entity_id, missing_fields in invalid:
       print(f"Entity {entity_id} missing: {missing_fields}")
       # Option 1: Add default values
       defaults = {"status": "unknown", "title": "Untitled"}
       await entity_update(pool, entity_id, {f: defaults[f] for f in missing_fields})
       
       # Option 2: Tag for manual review
       await entity_update(pool, entity_id, {"_validation_errors": missing_fields})
   ```

### Workflow 5: Bulk Tagging

**Goal**: Add tags to a batch of entities based on criteria.

**Steps:**
1. **Fetch target entities** (e.g., all bookmarks with `category: "technical"`):
   ```python
   technical_bookmarks = await entity_search(
       pool,
       collection_name="bookmark",
       query={"category": "technical"}
   )
   ```

2. **Add tags without overwriting existing ones**:
   ```python
   for entity in technical_bookmarks:
       existing_tags = entity["data"].get("tags", [])
       new_tags = list(set(existing_tags + ["reference", "dev"]))
       await entity_update(pool, entity["id"], {"tags": new_tags})
   ```

**Tip**: Use Python's `set` operations to ensure no duplicate tags.

## Usage Examples

### Example 1: Set Up a New Project Tracker

```python
# Create collection
await collection_create(pool, "project", "Personal and work projects")

# Add first project
project_id = await entity_create(
    pool,
    "project",
    {
        "title": "Learn PostgreSQL JSONB",
        "status": "active",
        "priority": "medium",
        "goals": ["Master JSONB queries", "Build a sample app"],
        "tags": ["learning", "database"],
        "started_at": "2026-02-09"
    }
)
```

### Example 2: Search and Update

```python
# Find all active high-priority projects
active_high = await entity_search(
    pool,
    collection_name="project",
    query={"status": "active", "priority": "high"}
)

# Mark the first one as completed
if active_high:
    project_id = active_high[0]["id"]
    await entity_update(
        pool,
        project_id,
        {"status": "completed", "completed_at": "2026-02-09"}
    )
```

### Example 3: Export and Backup

```python
# Export all bookmarks to JSON file
bookmarks = await collection_export(pool, "bookmark")

import json
with open("bookmarks_backup.json", "w") as f:
    json.dump(bookmarks, f, indent=2, default=str)  # default=str handles UUIDs/dates
```

## Best Practices

1. **Start Simple**: Begin with a few collections and templates. Add complexity as needs grow.
2. **Consistent Naming**: Stick to kebab-case for collections and consistent field names across entities of the same type.
3. **Tag Early**: Add tags from the start for easier filtering and future organization.
4. **Regular Hygiene**: Schedule periodic reviews (monthly or quarterly) to deduplicate, archive, and normalize.
5. **Document Schemas**: Keep this skill updated with new templates as you discover new entity types.
6. **Use Scripts for Bulk Ops**: For operations touching 10+ entities, write a Python script in the skill directory or use `entity_search` + loops.
7. **Backup Before Destructive Ops**: Always export collections before running deduplication or bulk deletions.

## Extending This Skill

As you use the General butler, you may discover new entity types or workflows. To extend this skill:

1. **Add new templates**: Follow the format of existing templates (required fields + key fields + example JSON)
2. **Document new query patterns**: If you find useful JSONB queries, add them to the Query Patterns section
3. **Capture workflows**: When you run a multi-step data operation more than once, document it as a workflow
4. **Create helper scripts**: For complex or frequently-used operations, add a Python script to this skill directory (e.g., `deduplicate.py`, `archive_stale.py`)

## Related Tools

- `collection_create(name, description)`: Initialize a new collection
- `collection_list()`: View all collections
- `entity_create(collection_name, data)`: Add a new entity
- `entity_get(entity_id)`: Retrieve a single entity
- `entity_update(entity_id, data)`: Merge updates into an entity (deep merge)
- `entity_search(collection_name, query)`: Find entities using JSONB containment
- `entity_delete(entity_id)`: Remove an entity
- `collection_export(collection_name)`: Export all entities from a collection

---

**Version**: 1.0  
**Last Updated**: 2026-02-09  
**Author**: General Butler Team
