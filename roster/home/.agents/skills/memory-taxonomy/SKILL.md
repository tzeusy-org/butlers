---
name: memory-taxonomy
description: Home domain memory taxonomy for service-provider entity resolution, subjects, predicates, permanence, tags, and example facts.
version: 1.0.0
tools_required:
  - memory_entity_resolve
  - memory_entity_create
  - memory_store_fact
---

# Home Memory Taxonomy Skill

## Purpose

Load this skill when storing home-domain memory facts, especially facts about
service providers that must be anchored to resolved entities.

### Service Providers: Resolve Before Storing

When the user mentions a home service provider (plumber, electrician, HVAC technician, cleaning
company, etc.), **resolve or create a transitory entity before storing facts about them.** Never
store facts with only a raw string subject for external organizations or people.

**Entity type inference for home domain:**

| Home entity | `entity_type` |
|-------------|---------------|
| Plumber, electrician, HVAC tech, contractor | `person` or `organization` (use `organization` if a company name; `person` if an individual) |
| Cleaning service, pest control, landscaping company | `organization` |
| Appliance manufacturer or brand | `organization` |
| Individual tradesperson (e.g., "Mike the plumber") | `person` |

**Resolve-or-create pattern for service providers:**

```python
# "Called Mike's Plumbing to fix the leaking pipe under the kitchen sink"
candidates = memory_entity_resolve(name="Mike's Plumbing", entity_type="organization")
# → zero candidates: create transitory entity
try:
    result = memory_entity_create(
        canonical_name="Mike's Plumbing",
        entity_type="organization",
        metadata={
            "unidentified": True,
            "source": "fact_storage",
            "source_butler": "home",
            "source_scope": "home"
        }
    )
    provider_entity_id = result["entity_id"]
except ValueError:
    candidates = memory_entity_resolve(name="Mike's Plumbing", entity_type="organization")
    provider_entity_id = candidates[0]["entity_id"]

memory_store_fact(
    subject="Mike's Plumbing",
    predicate="service_provider",
    content="plumbing — fixed kitchen sink leak; reliable, called for emergencies",
    entity_id=provider_entity_id,
    permanence="stable",
    importance=6.0,
    tags=["service-provider", "plumbing", "maintenance"]
)
```

The entity appears in the dashboard "Unidentified Entities" section for the owner to confirm.
**Never fall back to a bare string subject for a service provider.**

Room, device, and scene subjects (e.g., `"bedroom"`, `"thermostat"`, `"movie-night"`) are
internal identifiers; they do not require entity resolution.

### Home Domain Taxonomy

**Subject**:
- For room-specific knowledge: room name (e.g., `"bedroom"`, `"living-room"`, `"kitchen"`), no entity required
- For device-specific knowledge: device identifier (e.g., `"thermostat"`, `"front-door-lock"`), no entity required
- For scene knowledge: scene name (e.g., `"movie-night"`, `"bedtime"`), no entity required
- For user preferences: `"comfort_preference"`, `"energy_preference"`, no entity required
- For service providers: company/person name; it MUST be resolved to an entity (see above)

**Predicates**:
- `comfort_preference`: User's temperature, humidity, lighting, or air quality preferences
- `comfort_deviation`: Detected deviation from user's comfort preferences (temporary alert)
- `scene_preference`: User's preferences for scene timing, trigger conditions, or modifications; also used when a scene is created or modified
- `automation_schedule`: A scheduled automation linked to a scene or recurring action
- `schedule_pattern`: Observed patterns in room usage or device activation (e.g., "living room always used 7-10pm")
- `device_issue`: Known device problems, quirks, maintenance needs, or firmware history (use tags to distinguish: `battery`, `offline`, `firmware`, `quirk`, `maintenance`)
- `energy_baseline`: Typical energy consumption by device or time period (used for anomaly detection)
- `energy_spike`: Anomalous energy consumption detected above baseline (volatile)
- `energy_pattern`: Observed patterns in energy consumption over time (standard)
- `usage_pattern`: Observed patterns in how user interacts with devices or scenes
- `service_provider`: Known home service providers such as plumbers, electricians, cleaners, and contractors (fact anchored to service provider entity)

**Permanence levels**:
- `stable`: Long-term preferences that persist across seasons and living patterns (e.g., "user prefers bedroom at 68°F at night")
- `standard`: Current preferences and typical patterns (e.g., "user usually activates movie night at 7pm on weekends")
- `volatile`: Temporary states, immediate issues, or time-sensitive alerts (e.g., "basement sensor battery at 15%", "HVAC firmware update available")

**Tags**: Use tags like `temperature`, `humidity`, `lighting`, `energy`, `comfort`, `scene`, `device`, `maintenance`, `urgent`, `seasonal`, `service-provider`

### Example Facts

```python
# From: "I like the bedroom cooler at night around 68 degrees"
memory_store_fact(
    subject="bedroom",
    predicate="comfort_preference",
    content="user prefers 68°F (67-69°F range) at night for sleeping",
    permanence="stable",
    importance=8.0,
    tags=["temperature", "comfort", "bedroom", "night"]
)

# From: observing user activates movie night every Friday at 7pm
memory_store_fact(
    subject="movie-night-scene",
    predicate="usage_pattern",
    content="user typically activates movie night scene on Friday evenings around 7pm",
    permanence="standard",
    importance=6.0,
    tags=["pattern", "scene", "movie-night", "weekend"]
)

# From: device status check showing basement sensor battery at 15%
memory_store_fact(
    subject="basement-sensor",
    predicate="device_issue",
    content="basement sensor battery at 15% — needs replacement soon",
    permanence="volatile",
    importance=7.0,
    tags=["maintenance", "battery", "urgent"]
)

# From: analyzing energy consumption data
memory_store_fact(
    subject="hvac",
    predicate="energy_baseline",
    content="HVAC typically uses 40% of daily energy in winter, 25% in summer. Peak usage 7-9am and 6-8pm.",
    permanence="standard",
    importance=6.0,
    tags=["energy", "hvac", "baseline"]
)

# From: "I like it bright in the kitchen during the day"
memory_store_fact(
    subject="kitchen",
    predicate="comfort_preference",
    content="user prefers bright lighting (80-100%) during daytime hours (8am-6pm)",
    permanence="stable",
    importance=7.0,
    tags=["lighting", "comfort", "kitchen", "daytime"]
)
```
