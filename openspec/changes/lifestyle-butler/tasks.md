## 1. Butler Directory Scaffolding

- [ ] 1.1 Create `roster/lifestyle/` directory
- [ ] 1.2 Write `roster/lifestyle/butler.toml` with: `[butler]` (port 41109), `[butler.db]` (schema `lifestyle`), `[butler.switchboard]`, `[butler.seed_configs]` (codex/gpt-5.1), `[butler.runtime]` (max_concurrent=3), modules (memory, calendar, contacts), and scheduled tasks (memory_consolidation, memory_episode_cleanup, memory_purge_superseded, daily_briefing_contribution, weekly-taste-digest)
- [ ] 1.3 Write `roster/lifestyle/MANIFESTO.md` — identity, philosophy, domain scope, value proposition
- [ ] 1.4 Write `roster/lifestyle/CLAUDE.md` — start with `@../shared/AGENTS.md`, then system prompt with tool inventory, guidelines, calendar usage, interactive response mode, memory classification taxonomy (subject/predicate/permanence), and example facts
- [ ] 1.5 Write `roster/lifestyle/AGENTS.md` — initialize with `@../shared/AGENTS.md` reference and "# Notes to self" header
- [ ] 1.6 Create `roster/lifestyle/.agents/skills/` with shared symlinks: `butler-memory` → `../../../shared/skills/butler-memory`, `butler-notifications` → `../../../shared/skills/butler-notifications`
- [ ] 1.7 Create `roster/lifestyle/.claude` symlink → `.agents`

## 2. Database Schema

- [ ] 2.1 Create Alembic migration with `revision = "lifestyle_001"`, `branch_labels = ("lifestyle",)`, `down_revision = None` for `lifestyle` schema in the `butlers` database
- [ ] 2.2 Verify migration runs cleanly against the shared database alongside existing butler schemas

## 3. Switchboard Registration

- [ ] 3.1 Update `roster/switchboard/CLAUDE.md` — add `lifestyle` to Available Butlers section and add classification rule for music, entertainment, food preferences, hobbies, and routines
- [ ] 3.2 Add disambiguation rule: prefer `lifestyle` over `general` for taste/preference/entertainment messages
- [ ] 3.3 Add fanout rules for lifestyle + health overlap (food/mood signals)
- [ ] 3.4 Write tests for Switchboard routing to lifestyle butler (classification, disambiguation, fanout)

## 4. Scheduled Tasks

- [ ] 4.1 Implement weekly taste digest prompt — summarize music, entertainment, food, hobby highlights from the past week
- [ ] 4.2 Implement daily briefing contribution job — extract 24h lifestyle highlights for cross-butler morning briefing
- [ ] 4.3 Verify standard memory maintenance jobs (consolidation, episode cleanup, purge superseded) work with the lifestyle schema

## 5. Integration Testing

- [ ] 5.1 Write test: butler starts successfully with configured modules (memory, calendar, contacts)
- [ ] 5.2 Write test: butler stores and retrieves taste preference facts using the memory taxonomy
- [ ] 5.3 Write test: Switchboard routes a music-related message to the lifestyle butler
- [ ] 5.4 Write test: Switchboard routes a food preference message to lifestyle (not health)
- [ ] 5.5 Write test: Switchboard fans out a message with both lifestyle and health signals to both butlers

## 6. Documentation and RFC Amendments

- [ ] 6.1 Update `about/heart-and-soul/v1.md` to include the Lifestyle butler in the roster (10 butlers)
- [ ] 6.2 Amend RFC 0006 (Database Schema Isolation) — add `lifestyle` schema to the schema tree diagram
- [ ] 6.3 Amend RFC 0010 (Cross-Butler Briefing Exception) — update specialist count from 6 to 7, add `lifestyle` to the briefing aggregation view GRANT, update session cost estimate from 8 to 9
- [ ] 6.4 Create `openspec/specs/butler-lifestyle/spec.md` from the change spec (done at archive time)
