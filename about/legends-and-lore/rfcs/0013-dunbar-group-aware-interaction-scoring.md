# RFC 0013: Dunbar Group-Aware Interaction Scoring

**Status:** Accepted
**Date:** 2026-04-16

## Summary

The Dunbar scoring engine treats all interaction facts equally: a message in a
500-person community channel scores the same as a direct message. This RFC
defines three mechanisms to fix this: direction-weighted scoring (outgoing
interactions are weighted 10x incoming), group-size-divided scoring (interaction
weight is inversely proportional to participant count), and connector-level
participant gating (chats with >20 participants are excluded from interaction
tracking entirely). It also defines `interaction_log_group`, a batch MCP tool
that fans out group interactions deterministically without requiring per-member
LLM tool calls.

## Motivation

The Dunbar tier system ranks contacts by exponential-decay scoring over
interaction facts. The current implementation has four problems:

1. **No direction awareness.** The owner sending a message is a much stronger
   signal of active engagement than receiving one. An outgoing DM to a close
   friend scores the same as an inbound message from an acquaintance in a group
   chat.

2. **No group dilution.** A message to a 10-person group creates 10 interaction
   facts at full weight. This inflates Dunbar scores for peripheral contacts
   who happen to share a group chat with the owner, pushing them into higher
   tiers than their actual relationship warrants.

3. **Entity blowup from large groups.** Community channels and broadcast groups
   with hundreds of members generate identity resolution attempts for every
   sender. This creates hundreds of temporary entities, interaction facts, and
   embeddings with zero relationship value.

4. **LLM tool call explosion.** When the runtime LLM processes a group
   interaction, it must call `interaction_log` once per group member. For a
   20-person group, that is 20 tool calls consuming LLM context tokens. This
   work is purely deterministic and should not require LLM reasoning.

## Design

### D1: Direction-Weighted Scoring

Interaction facts carry a `direction` field in `metadata` (values: `incoming`,
`outgoing`, `mutual`). The Dunbar scoring SQL MUST apply a direction multiplier
when computing decay scores.

**Multipliers:**

| Direction | Multiplier | Rationale |
|-----------|------------|-----------|
| `outgoing` | 10.0 | Owner actively chose to communicate. Strongest engagement signal. |
| `mutual` | 5.0 | Bidirectional exchange in the same session/day. |
| `incoming` | 1.0 | Baseline. Receiving a message is passive. |
| NULL/unknown | 1.0 | Backward compatibility for pre-existing facts without direction. |

**Scoring formula (updated):**

```
score = SUM(
    EXP(-lambda * days_since_interaction)
    * direction_weight
    * (1.0 / group_size)
)
```

The direction multiplier is read from `facts.metadata->>'direction'`. The
`compute_dunbar_scores()` SQL query MUST be updated to include this weighting.

### D2: Group-Size-Divided Scoring

Interaction facts MAY carry a `group_size` integer in `metadata`. When present,
the scoring formula divides the interaction's contribution by `group_size`.

| Context | `group_size` | Effective weight |
|---------|-------------|-----------------|
| DM (1:1) | 1 or NULL | 1.0 (full) |
| 3-person group | 3 | 0.333 |
| 10-person group | 10 | 0.1 |
| 20-person group (max) | 20 | 0.05 |

**Storage:** `group_size` is stored in `facts.metadata` alongside `type` and
`direction`:

```json
{
  "type": "telegram_user_client",
  "direction": "incoming",
  "group_size": 8
}
```

**Scoring SQL fragment:**

```sql
EXP(-$1 * GREATEST(EXTRACT(EPOCH FROM (now() - f.valid_at)) / 86400.0, 0.0))
* CASE f.metadata->>'direction'
    WHEN 'outgoing' THEN 10.0
    WHEN 'mutual'   THEN 5.0
    ELSE 1.0
  END
* (1.0 / GREATEST(COALESCE((f.metadata->>'group_size')::float, 1.0), 1.0))
```

### D3: Connector-Level Participant Gating

Chat connectors (Telegram user client, WhatsApp user client) MUST enforce a
participant count gate before submitting interaction-eligible envelopes.

**Threshold:** 20 participants (configurable per connector via
`max_interaction_group_size` in connector config). Chats with more participants
than the threshold are excluded from interaction-relevant processing.

**Connector responsibilities:**

1. **Query participant count.** The Telegram user client connector MUST query
   `chat.participants_count` via the Telethon client when building envelopes.
   The WhatsApp user client connector MUST read group membership count from the
   whatsmeow bridge metadata.

2. **Enrich the envelope.** Connectors MUST include `participant_count` and
   `chat_type` in the envelope for downstream consumers:

   ```json
   {
     "sender": {
       "identity": "multiple",
       "participants": {"123": "Alice", "456": "Bob"},
       "participant_count": 8,
       "chat_type": "group"
     }
   }
   ```

   Valid `chat_type` values: `"private"` (DM), `"group"` (small group),
   `"supergroup"` (Telegram supergroup), `"channel"` (broadcast),
   `"community"` (WhatsApp community).

3. **Gate submission.** When `participant_count > max_interaction_group_size`:
   - Individual messages: the connector MAY still submit the envelope for
     signal extraction and routing purposes, but MUST set
     `control.interaction_eligible = false` in the envelope. The interaction
     sync job MUST skip messages where `request_context->>'interaction_eligible'`
     is `'false'`. When absent, `interaction_eligible` defaults to `true`
     (backward compatible with existing envelopes).
   - Batch conversation-history envelopes: the connector MAY skip submission
     entirely or submit with `policy_tier = "metadata_only"`.
   - Connectors SHOULD emit an OTel counter
     `butlers.<connector>.interaction_gated` with attributes
     `{chat_type, participant_count_bucket}` when gating fires, so operators
     can monitor how much traffic is being filtered.

**Why at the connector level (not Switchboard or interaction_sync):**

The connector holds the transport client with access to chat metadata. The
Switchboard receives normalized envelopes and cannot query Telegram for
participant counts without violating the transport abstraction (see
architecture.md, "Why Connectors Are the Computational Cost Boundary"). The
interaction_sync job runs hours after ingestion and would have already incurred
storage, deduplication, and potential LLM classification costs.

### D4: Interaction Sync Group-Aware Pre-Grouping

The `interaction_sync` background job MUST be updated to handle group context.
Currently it groups messages by `(source_sender_identity, source_channel,
DATE(received_at))` with no awareness that multiple senders may belong to the
same chat.

**Updated algorithm:**

```
1. Query message_inbox grouped by
   (source_thread_identity, source_channel, DATE(received_at))
   collecting DISTINCT source_sender_identity per chat per day.
   Also check request_context->>'interaction_eligible' — skip messages
   where this is explicitly 'false'.

2. For each (chat, channel, date) group:
   a. Read participant_count from request_context if available.
      Fall back to COUNT(DISTINCT source_sender_identity) in the group.
   b. If participant_count > max_interaction_group_size (20): SKIP.
   c. Determine group_size = participant_count (or distinct sender count).
      For DM chats (only one non-owner sender), group_size = 1.
   d. Partition senders into owner_sent (boolean) and non-owner senders.
   e. For each non-owner sender:
      - Resolve sender to contact_id via contact_info.
      - If owner_sent is true for this chat+date (the owner also
        sent messages in this chat on this date):
        → log an OUTGOING interaction for this contact
          (the owner reached out to them)
      - Always log an INCOMING interaction for this contact
        (they sent messages the owner received)
      - Both facts carry group_size in metadata.
      NOTE: The owner's own sender_identity is still excluded from
      contact resolution (no self-interaction). The owner's presence
      as a sender is used solely to determine direction for the OTHER
      participants' interaction facts.
```

**Direction semantics clarification:** "Outgoing" means the owner actively
engaged with a contact. In a group chat, if the owner sent at least one message,
all other participants in that chat on that day receive an outgoing interaction
fact (the owner chose to communicate in their presence). If the owner did not
send any messages, participants receive only incoming interaction facts. This
means a single chat on a single day MAY produce two interaction facts per
non-owner contact: one incoming (they messaged) and one outgoing (the owner
messaged).

**Deduplication contract:** The existing `interaction_log()` deduplicates by
`(contact_id, valid_at::date, metadata->>'type')`. Since both incoming and
outgoing facts share the same `type` (e.g., `telegram_user_client`), a second
fact would collide. To support both directions on the same day, the outgoing
fact MUST use a distinct `occurred_at` hour offset. The interaction_sync
channel hour offsets are extended:

| Channel | Incoming offset | Outgoing offset |
|---------|----------------|-----------------|
| `telegram_user_client` | 0 | 12 |
| `whatsapp_user_client` | 1 | 13 |
| `email` | 2 | 14 |

This preserves the existing deduplication contract and allows at most two
interaction facts per contact per channel per day (one incoming, one outgoing).
The hour values are arbitrary time markers for idempotency; they do not
represent actual event times.

**Request context enrichment:** The Switchboard ingest pipeline MUST propagate
`participant_count` and `chat_type` from the envelope's `sender` section into
`request_context` when present:

```json
{
  "source_channel": "telegram_user_client",
  "source_sender_identity": "multiple",
  "source_thread_identity": "12345678",
  "participant_count": 8,
  "chat_type": "group"
}
```

### D5: Batch Group Interaction Tool

A new MCP tool `interaction_log_group` MUST be registered on the relationship
butler. It accepts a group identifier and fans out interaction facts for all
group members deterministically, without requiring per-member LLM tool calls.

**Tool signature:**

```python
async def interaction_log_group(
    pool: asyncpg.Pool,
    group_id: uuid.UUID,
    direction: str = "mutual",
    occurred_at: datetime | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
```

**Behavior:**

1. Resolve group membership from `relationship.group_members` table.
2. If the group has no members: return `{"logged": 0, "skipped": 0, "group_size": 0}`.
3. Compute `group_size = len(members)`.
4. If `group_size > 20`: return early with `{"skipped": "group_too_large", "group_size": group_size}`.
5. For each member: call `interaction_log()` with
   `metadata={"group_size": group_size, "group_id": str(group_id)}`.
6. Return `{"logged": N, "skipped": M, "group_size": group_size}`.

**Cost savings:** One LLM tool call instead of N. For a 15-person group, this
saves 14 tool calls (14 * ~500 tokens of tool-call overhead = ~7000 tokens per
group interaction).

### D6: Backward Compatibility

Existing interaction facts without `direction` or `group_size` in metadata
MUST continue to score correctly:

- Missing `direction` → defaults to multiplier 1.0 (incoming baseline).
- Missing `group_size` → defaults to divisor 1.0 (DM weight).

No migration of existing facts is required. The scoring SQL uses
`COALESCE` for both fields.

## Integration

- **RFC 0003 / Envelope models:** The ingest.v1 envelope gains optional
  `sender.participant_count` (int | None), `sender.chat_type` (str | None),
  and `control.interaction_eligible` (bool, default True) fields. Because
  `IngestSenderV1` and `IngestControlV1` in
  `roster/switchboard/tools/routing/contracts.py` use `extra="forbid"`,
  these fields MUST be added to the Pydantic models before connectors can
  include them. Adding optional fields with defaults is backward compatible —
  existing envelopes without these fields will continue to validate.
- **RFC 0003 / Request context:** `_build_request_context()` in
  `roster/switchboard/tools/ingestion/ingest.py` MUST propagate
  `participant_count`, `chat_type`, and `interaction_eligible` from the
  parsed envelope into `request_context` when present. These are optional
  additive keys; omitting them preserves existing behavior.
- **RFC 0004:** Identity resolution for group members uses the existing
  `resolve_contact_by_channel()` contract. No changes to the identity model.
- **Architecture (heart-and-soul):** The connector-level participant gating
  implements the "Connectors Are the Computational Cost Boundary" principle
  defined in `architecture.md`.
- **Passive Interaction Sync spec:** The `openspec/specs/passive-interaction-sync/spec.md`
  capability spec MUST be updated to reflect group-aware pre-grouping (D4),
  direction tracking, and the participant count gate.

## Deployment Impact

**Score redistribution:** When the weighted scoring formula is deployed, Dunbar
scores will shift immediately because `compute_dunbar_scores()` reads weights
from metadata at query time. Contacts whose interaction facts are primarily from
group chats will see their scores drop (1/n dilution). Contacts with whom the
owner exchanges DMs will see relative score increases (outgoing multiplier).
This is intentional — the new scores better reflect actual relationship
investment. Hysteresis (D3 in the Dunbar scoring engine) will buffer tier
downgrades by 2 ranks, preventing sudden tier oscillation.

**No data migration required:** Existing interaction facts without `direction`
or `group_size` metadata will score at 1.0x multiplier and 1.0x divisor
respectively (identical to current behavior). New facts from the updated
interaction_sync will carry the enriched metadata. Over time (30-day half-life),
old facts decay away and new weighted facts dominate the score.

## Constants

| Constant | Value | Location | Hot/Cold |
|----------|-------|----------|----------|
| `DIRECTION_WEIGHT_OUTGOING` | 10.0 | `dunbar.py` | Cold (code constant) |
| `DIRECTION_WEIGHT_MUTUAL` | 5.0 | `dunbar.py` | Cold |
| `DIRECTION_WEIGHT_INCOMING` | 1.0 | `dunbar.py` | Cold |
| `MAX_INTERACTION_GROUP_SIZE` | 20 | connector config | Hot (per-connector) |
| `_LAMBDA` (decay half-life) | ln(2)/30 | `dunbar.py` | Cold (unchanged) |

## Alternatives Considered

**Weight at query time vs. stored weight.** We considered storing a pre-computed
weight on each interaction fact. Rejected because the weights (direction
multipliers, group size) may be tuned over time, and recomputing at query time
from metadata is cheap. Storing derived values creates a migration burden when
weights change.

**Cutoff at interaction_sync instead of connector.** Rejected because the
Switchboard still processes, stores, and potentially LLM-classifies messages
before the sync job runs (hours later). The connector is the cheapest place to
prevent cost explosion because it has native access to chat metadata and runs
before any downstream processing.

**Exclude group interactions entirely.** Rejected because small group
interactions (family chat, friend group) are meaningful relationship signals.
The 1/n dilution correctly models that a message to 5 people is worth roughly
1/5th of a direct message to each.

**Separate interaction type for groups.** We considered a distinct
`predicate = 'group_interaction'` instead of enriching `interaction` metadata.
Rejected because the Dunbar scoring query would need to scan two predicates,
and the conceptual model is the same: an interaction happened, it just has
context about how many people were involved.
