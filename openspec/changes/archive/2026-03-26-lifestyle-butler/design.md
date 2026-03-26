## Context

The butler roster currently has 9 domain-specialized butlers (Switchboard, General, Health, Relationship, Finance, Education, Travel, Home, Messenger). A class of daily quality-of-life signals — music, entertainment, food preferences, hobbies, routines — has no domain owner and defaults to General, which is designed as a freeform catch-all, not a domain specialist.

The Lifestyle butler fills this gap. It follows the exact same creation pattern as every other butler (manifesto, butler.toml, CLAUDE.md, AGENTS.md, database schema, switchboard registration) but with a domain scope tuned to taste, rhythm, and daily enrichment.

## Goals / Non-Goals

**Goals:**
- Stand up a fully operational butler at `roster/lifestyle/` following established patterns
- Define a clear domain boundary: what Lifestyle owns vs. what Health/Relationship/Education own
- Establish a memory taxonomy for taste profiles, listening patterns, entertainment, food preferences, and hobbies
- Register as a Switchboard routing target for lifestyle-domain messages
- Prepare for the Spotify module as the first domain-specific tool surface

**Non-Goals:**
- Implementing the Spotify module (separate `spotify-module` change)
- Building custom MCP tools unique to Lifestyle (v1 uses standard modules + Spotify)
- Nutrition/diet tracking (Health's domain — Lifestyle owns food *preferences*, not nutrition *data*)
- Social event planning (Relationship's domain)
- Smart home scene management (Home's domain)
- Entertainment recommendation engine (the LLM is the intelligence; the butler provides tools and memory)

## Decisions

### 1. Domain scope: taste, rhythm, and daily enrichment

**Decision:** Lifestyle owns signals about what the user *enjoys*, *consumes*, and *does for fun* — not what they *need to do* (Health, Education) or *who they do it with* (Relationship).

**Boundary rules:**
- Music & listening → Lifestyle (playback, playlists, discovery, taste profiles)
- Food & dining preferences → Lifestyle (favorite restaurants, cuisines, recipes). Nutrition tracking → Health
- Entertainment → Lifestyle (movies, TV, books, games, podcasts)
- Hobbies & interests → Lifestyle (things you're into). Formal study → Education
- Daily routines → Lifestyle (morning/evening patterns, focus modes). Sleep/exercise metrics → Health
- Social dining, hosting → Relationship (it's about the people)

**Rationale:** This boundary follows the heart-and-soul principle of domain specialization. Each butler sees shared signals through its own domain lens — the same "listened to sad music all week" event can be a Lifestyle observation (curate an uplifting playlist) and a Health signal (mood concern), with the Switchboard routing to both.

### 2. Port 41109, schema `lifestyle`

**Decision:** Port 41109 (next sequential after Home at 41108). Database schema `lifestyle` in the shared `butlers` database.

**Rationale:** Follows the established convention. No other butler uses 41109.

### 3. Standard modules only for v1

**Decision:** Enable `memory`, `calendar`, `contacts` as base modules. The `spotify` module will be enabled when the separate `spotify-module` change lands.

**Rationale:** The butler needs to be functional before its flagship module arrives. With memory and calendar, it can already store taste preferences, track habits, and schedule routine-related tasks. The Spotify module adds the first set of domain-specific tools.

### 4. Memory taxonomy: taste-centric predicates

**Decision:** Define a memory taxonomy centered on taste profiles and consumption patterns:

| Subject | Predicate | Example |
|---------|-----------|---------|
| user | `likes_genre` | "jazz, especially bebop" |
| user | `likes_artist` | "Khruangbin" |
| user | `likes_cuisine` | "Thai, especially northern Isaan style" |
| user | `favorite_restaurant` | "Soi 38, Hackney" |
| user | `favorite_recipe` | "Thai basil chicken from Pailin's Kitchen" |
| user | `hobby` | "film photography, mostly 35mm" |
| user | `watches` | "currently watching Severance S2" |
| user | `reads` | "reading Project Hail Mary" |
| user | `routine` | "morning: espresso → news → walk" |
| user | `food_preference` | "vegetarian on weekdays" |
| user | `food_dislike` | "cilantro (tastes like soap)" |
| spotify:artist:{id} | `listening_pattern` | "heavy rotation in March 2026" |
| spotify:playlist:{id} | `purpose` | "deep focus coding sessions" |

**Rationale:** Follows the same subject/predicate/permanence pattern used by Health and Relationship butlers. Taste predicates are predominantly `DURABLE` (preferences persist) with some `TRANSIENT` (currently watching/reading).

### 5. Scheduled tasks: weekly taste digest + briefing contribution

**Decision:** Three scheduled task types beyond standard memory maintenance:
- **Weekly taste digest** (Sunday evening): Summarize the week's listening, entertainment, food experiences. Surface trends and discoveries.
- **Daily briefing contribution** (6:55am): Contribute lifestyle highlights to the cross-butler morning briefing.
- **Standard memory maintenance**: consolidation (6h), episode cleanup (4am), purge superseded (4:10am).

**Rationale:** The weekly digest is the lifestyle equivalent of Health's weekly summary. It's the moment where patterns become visible. The daily briefing ensures lifestyle context appears alongside calendar, health, and relationship updates in the morning brief.

### 6. Manifesto tone: warm, enthusiastic, non-prescriptive

**Decision:** The manifesto should feel like a knowledgeable friend who remembers your taste — not a critic, not a recommendation algorithm. It celebrates what you enjoy without ranking or judging it.

**Rationale:** Lifestyle is inherently personal and subjective. The butler should amplify your taste, not impose one. This mirrors the Health butler's "companion, not a doctor" positioning.

## Risks / Trade-offs

- **[Scope creep into Health]** Food preferences vs. nutrition is a blurry line. → Mitigation: Lifestyle owns *taste* ("I love Thai food"), Health owns *data* ("I ate 2000 kcal today"). If both butlers receive a food message, they extract different facts.
- **[Thin initial tool surface]** Without the Spotify module, the butler only has memory/calendar/contacts. → Mitigation: This is intentional. The butler is immediately useful for taste memory ("remember I liked that restaurant") and the Spotify module follows closely.
- **[10th butler adds routing complexity]** More butlers = more classification decisions for the Switchboard. → Mitigation: Lifestyle has a clear, distinct domain. Music/entertainment/food signals are rarely ambiguous with other domains.

## RFC Amendments

Two RFCs require updates to accommodate the new butler:

- **RFC 0006 (Database Schema Isolation):** Add `lifestyle` schema to the schema tree diagram alongside general, relationship, health.
- **RFC 0010 (Cross-Butler Briefing Exception):** Update specialist butler count from 6 to 7. Add `GRANT SELECT ON lifestyle.state TO general_role` to the briefing aggregation view. Update the LLM session cost comparison from "8 sessions" to "9 sessions" for the pure-MCP alternative. The Lifestyle butler's `daily_briefing_contribution` job writes taste highlights to `state['briefing/daily/YYYY-MM-DD']` following the same structured JSON format as other specialists.
