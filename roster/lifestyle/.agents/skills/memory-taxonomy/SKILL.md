---
name: memory-taxonomy
description: Lifestyle domain memory classification — subject/predicate taxonomy, permanence levels, tags, and example fact patterns for storing taste, consumption, and hobby knowledge
version: 1.0.0
tools_required:
  - memory_store_fact
  - memory_recall
  - memory_search
---

# Lifestyle Memory Taxonomy Skill

## Purpose

Load this skill when classifying and storing a taste preference, consumption
note, hobby, or Spotify-enriched fact. It defines the subject/predicate pairs,
permanence levels, tags, and worked example calls for `memory_store_fact()`.

## Lifestyle Domain Taxonomy

Use `memory_store_fact()` with the following subject/predicate pairs:

**Subject**: Use `"user"` for all personal preference facts. Use `"spotify:artist:{id}"` or `"spotify:playlist:{id}"` for Spotify-enriched facts.

**Taste Preference Predicates (`stable` permanence)**:
- `likes_genre`: music genre preferences and dislikes
- `likes_artist`: favourite artists or acts
- `likes_cuisine`: cuisine types the user enjoys
- `favorite_restaurant`: preferred dining spots and why
- `favorite_recipe`: beloved recipes or dishes
- `hobby`: active hobbies and leisure interests
- `food_preference`: dietary patterns, ingredient preferences
- `food_dislike`: foods to avoid (allergies, aversions, dislikes)
- `routine`: daily routine patterns (morning rituals, evening wind-downs, focus modes)

**Current Consumption State Predicates (`volatile` permanence)**:
- `watches`: currently watching (TV shows, films)
- `reads`: currently reading (books, articles, comics)
- `plays`: currently playing (video games, board games)
- `listens_to`: current listening focus (album, artist rotation, playlist)

**Spotify-Enriched Predicates (`stable` permanence, Spotify subjects)** (listed below as `Subject Type | Predicate`; pass only the predicate after the `|` to `memory_store_fact(predicate=...)`):
- `spotify:artist:{id} | listening_pattern`: rotation intensity and frequency over time
- `spotify:playlist:{id} | purpose`: what the playlist is for (focus, commute, party, etc.)
- `spotify:playlist:{id} | context`: when/where/why the playlist is used

**Permanence levels** (these map to the `memory_store_fact(permanence=...)` parameter):
- `stable`: Stable preferences that persist, such as cuisine tastes, genre opinions, favourite artists, hobbies, and routines
- `volatile`: Temporal state, covering what's currently being watched, read, played, or listened to

**Tags**: Use tags like `music`, `food`, `restaurant`, `cuisine`, `tv`, `film`, `book`, `game`, `hobby`, `routine`, `artist`, `genre`, `spotify`, `dislike`, `allergy`

## Example Facts

```python
# User mentions loving a genre
memory_store_fact(
    subject="user",
    predicate="likes_genre",
    content="loves jazz — especially 1960s modal jazz and contemporary jazz-fusion",
    permanence="stable",
    importance=8.0,
    tags=["music", "genre", "jazz"]
)

# User names a favourite artist
memory_store_fact(
    subject="user",
    predicate="likes_artist",
    content="Bill Evans — cites his trio recordings as some of their favourite music",
    permanence="stable",
    importance=8.0,
    tags=["music", "artist", "jazz"]
)

# User mentions a food they avoid
memory_store_fact(
    subject="user",
    predicate="food_dislike",
    content="dislikes coriander / cilantro — strong aversion, not just mild preference",
    permanence="stable",
    importance=9.0,
    tags=["food", "dislike", "allergy-adjacent"]
)

# User mentions a favourite restaurant
memory_store_fact(
    subject="user",
    predicate="favorite_restaurant",
    content="Koya — favourite udon spot, love the cold noodle dishes in summer",
    permanence="stable",
    importance=7.0,
    tags=["restaurant", "japanese", "udon"]
)

# User mentions what they're currently watching
memory_store_fact(
    subject="user",
    predicate="watches",
    content="currently watching Severance S2 — halfway through, loving it",
    permanence="volatile",
    importance=5.0,
    tags=["tv", "watching", "severance"]
)

# Spotify-enriched artist rotation fact
memory_store_fact(
    subject="spotify:artist:4tZwfgrHOc3mvqYlEYSvVi",
    predicate="listening_pattern",
    content="AC/DC — heavy rotation every morning commute, consistent across 3 months",
    permanence="stable",
    importance=6.0,
    tags=["spotify", "artist", "rotation", "music"]
)
```
