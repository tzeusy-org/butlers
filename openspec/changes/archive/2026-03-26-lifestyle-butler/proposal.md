## Why

The butler ecosystem has a gap: daily quality-of-life activities — music, entertainment, food preferences, hobbies, routines — have no domain owner. They currently fall to the General butler, which is designed as a freeform catch-all, not a domain specialist. Music listening is the clearest example: the Spotify connector ingests listening events, but no butler has the domain expertise or tools to act on them (create playlists, discover music, curate a library). The same pattern applies to other lifestyle signals that will arrive as future connectors mature.

A dedicated Lifestyle butler gives these signals a proper home, with domain-specific intelligence, memory taxonomy, and tool surface. It also establishes the pattern for the Spotify module (spec'd separately in the `spotify-module` change) to have a natural owner from day one.

## What Changes

- **New `lifestyle` butler** in `roster/lifestyle/`: Full butler with manifesto, CLAUDE.md, butler.toml, AGENTS.md, and database schema
- **Port 41109**: Next available in the butler port range
- **Database schema `lifestyle`**: In the shared `butlers` database
- **Modules enabled**: `memory`, `calendar`, `contacts`, `spotify` (when the spotify-module change lands)
- **Switchboard routing**: Lifestyle butler registered as a routing target for music, entertainment, food/dining, hobby, and routine-related messages
- **Scheduled tasks**: Memory maintenance (standard), weekly taste digest, daily briefing contribution
- **Memory taxonomy**: Subject/predicate pairs for taste profiles, listening patterns, entertainment consumption, food preferences, hobby tracking

## Capabilities

### New Capabilities
- `butler-lifestyle`: Butler identity, configuration, domain scope, memory taxonomy, scheduled tasks, interactive response mode, and routing contract

### Modified Capabilities
- `butler-switchboard`: Register `lifestyle` as a routing target with domain classification rules for music, entertainment, food preferences, hobbies, and daily routines

## Impact

- **`roster/lifestyle/`**: New directory with butler.toml, MANIFESTO.md, CLAUDE.md, AGENTS.md
- **Switchboard routing**: New domain target in LLM classification prompt and routing contracts
- **Database**: New `lifestyle` schema in the `butlers` database (Alembic migration)
- **v1 scope**: Expands the butler roster from 9 to 10 butlers
- **No new Python modules yet**: The Lifestyle butler starts with standard modules (memory, calendar, contacts). The spotify module is a separate change that enables `[modules.spotify]` in the butler's config.
