## Context

The Dunbar scoring engine (`roster/relationship/tools/dunbar.py`) ranks contacts
by summing `exp(-λ * days)` over interaction facts. It treats all facts equally —
no awareness of direction (who initiated), group context (how many people were
in the chat), or computational cost (N tool calls for N group members).

RFC 0013 (`about/legends-and-lore/rfcs/0013-dunbar-group-aware-interaction-scoring.md`)
defines the full design contract. This design document maps that contract to
implementation decisions across four subsystems: scoring engine, interaction
sync job, chat connectors, and MCP tool surface.

**Current pipeline:**
```
Connector → Switchboard ingest → message_inbox → interaction_sync job → interaction facts → Dunbar scoring SQL
```

The scoring formula change is query-time only (no data migration). The
interaction sync and connector changes affect the write path.

## Goals / Non-Goals

**Goals:**
- Direction-weighted scoring: outgoing 10x, mutual 5x, incoming 1x
- Group-size-divided scoring: interaction weight = 1/participant_count
- Connector-level >20 participant gating to prevent entity/cost blowup
- Single `interaction_log_group` tool call replacing N per-member calls
- Backward compatibility: existing facts score identically via COALESCE defaults

**Non-Goals:**
- Migrating existing interaction facts to add direction/group_size metadata
- Per-group configurable weight multipliers (use code constants)
- Changing the 30-day half-life decay constant
- Modifying urgency or tier cadence formulas (only the base score changes)
- Gating at Switchboard level (connector owns transport metadata)

## Decisions

### D1: Direction + group_size as query-time weights, not stored values

**Decision:** Store `direction` and `group_size` in `facts.metadata` JSONB, read
them at query time in `compute_dunbar_scores()` SQL with CASE/COALESCE.

**Why not pre-computed weight column?** Weights (10x/5x/1x) may be tuned. A
stored column would require re-migrating all facts on every weight change.
JSONB metadata + query-time computation is zero-migration when weights change.

### D2: Connector-level gating, not Switchboard or sync-time

**Decision:** Telegram/WhatsApp connectors query `chat.participants_count` and
set `control.interaction_eligible = false` for chats >20 participants.

**Why not Switchboard?** Switchboard receives normalized envelopes without
transport API access. Querying Telegram for participant counts would violate the
transport abstraction (architecture.md, Rule 7).

**Why not interaction_sync?** The sync job runs hours after ingestion. By then,
the Switchboard has already processed, stored, and potentially LLM-classified
the message — the cost has been incurred.

### D3: Hour-offset deduplication for incoming + outgoing facts

**Decision:** Use hour offset 0/1/2 for incoming and 12/13/14 for outgoing per
channel. This allows two facts per contact per channel per day within the
existing `interaction_log()` deduplication contract.

**Why not a new type suffix?** Changing `type` from `telegram_user_client` to
`telegram_user_client:outgoing` would break downstream queries filtering by
`metadata->>'type'`.

**Why not modify deduplication to include direction?** The idempotency check in
`store_fact()` uses `(entity_id, scope, predicate, valid_at)`, not metadata
fields. Changing store_fact's idempotency contract is a larger scope change than
warranted.

### D4: Pydantic model extensions for envelope enrichment

**Decision:** Add optional fields to `IngestSenderV1` (`participant_count: int | None`,
`chat_type: str | None`) and `IngestControlV1` (`interaction_eligible: bool = True`).

Both models use `extra="forbid"`, so fields must be added before connectors can
send them. Optional fields with defaults are backward compatible.

### D5: interaction_log_group as a relationship module MCP tool

**Decision:** Register on the relationship butler as an MCP tool exposed to
runtime LLM instances. Resolves membership from `relationship.group_members`,
fans out `interaction_log()` calls with `group_size` metadata.

**Why not a core tool?** Group interaction is domain-specific to the relationship
butler. Core tools serve cross-cutting infrastructure needs (Rule 2).

## Risks / Trade-offs

**[Telegram API rate limits for participant_count queries]**
→ Cache participant counts per chat_id with TTL (e.g., 1 hour). The count
rarely changes for small groups. For large groups crossing the threshold, a
stale count may over- or under-gate by a few participants — acceptable.

**[Score redistribution on deploy]**
→ Existing facts without direction/group_size score at 1.0x/1.0x (identical to
current behavior). New facts from updated sync carry enriched metadata. The
30-day half-life ensures gradual transition. Hysteresis buffers tier downgrades
by 2 ranks.

**[Interaction sync performance with group-aware pre-grouping]**
→ The SQL query changes from GROUP BY `(sender, channel, date)` to GROUP BY
`(thread_identity, channel, date)` with a sub-aggregation of distinct senders.
This is a single query rewrite, not an additional query. Performance impact is
negligible for the typical volume (<1000 messages/day).

**[WhatsApp participant count availability]**
→ The whatsmeow bridge may not expose participant counts for all group types.
Fallback: count distinct senders observed in the scan window. This
underestimates true group size but is conservative (may include groups that
should be gated).

## Open Questions

_(none — RFC 0013 resolved all design questions)_
