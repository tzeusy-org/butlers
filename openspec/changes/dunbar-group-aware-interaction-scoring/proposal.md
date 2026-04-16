## Why

The Dunbar scoring engine treats all interaction facts equally — a message in a
500-person Telegram community channel scores the same as a direct message. This
produces three problems: group chat chatter inflates peripheral contacts into
higher tiers, community channels cause unbounded entity/fact creation, and
logging group interactions via the LLM requires N tool calls for N members.
RFC 0013 defines the design contract; this change updates capability specs and
implements the scoring, gating, and tooling changes.

## What Changes

- **Direction-weighted scoring:** Interaction decay scores gain a direction
  multiplier (outgoing 10x, mutual 5x, incoming 1x) so the owner's active
  engagement counts far more than passively received messages.
- **Group-size-divided scoring:** Each interaction's contribution is divided by
  the number of participants in the chat, so a 10-person group message counts
  1/10th of a DM.
- **Connector-level participant gating:** Telegram and WhatsApp connectors
  enrich envelopes with `participant_count` and `chat_type`, and exclude chats
  with >20 participants from interaction-eligible processing.
- **Interaction sync group-aware pre-grouping:** The background sync job groups
  by `source_thread_identity` (chat_id), detects owner messages for direction,
  and injects `group_size` into fact metadata.
- **Batch group interaction tool:** New `interaction_log_group` MCP tool fans
  out interaction facts for all group members in a single deterministic call.

## Capabilities

### New Capabilities

_(none — all changes modify existing capabilities)_

### Modified Capabilities

- `dunbar-tier-scoring`: Scoring formula gains direction and group_size weighting (D1, D2 from RFC 0013)
- `passive-interaction-sync`: Group-aware pre-grouping, direction tracking, interaction_eligible gate, >20 cutoff (D4)
- `connector-telegram-user-client`: Envelope enrichment with participant_count/chat_type, connector-level >20 gating (D3)
- `butler-relationship`: Add `interaction_log_group` to tool inventory (D5)

## Impact

- **Dunbar scoring SQL** (`roster/relationship/tools/dunbar.py`): `compute_dunbar_scores()` query updated with direction + group_size weighting
- **Interaction sync job** (`roster/relationship/jobs/relationship_jobs.py`): Rewritten grouping logic, direction detection, group_size injection
- **Telegram connector** (`src/butlers/connectors/telegram_user_client.py`): Participant count query, envelope enrichment, >20 gating
- **WhatsApp connector** (`src/butlers/connectors/whatsapp_user_client.py`): Same enrichment pattern
- **Switchboard ingest** (`roster/switchboard/tools/ingestion/ingest.py`): Propagate new fields to request_context
- **Envelope models** (`roster/switchboard/tools/routing/contracts.py`): Add optional fields to `IngestSenderV1` and `IngestControlV1`
- **Relationship tools** (`roster/relationship/tools/interactions.py`): New `interaction_log_group` function
- **Relationship module** (`roster/relationship/modules/tools.py`): Register `interaction_log_group` as MCP tool
- **No database migrations required** — all changes use existing JSONB metadata fields
