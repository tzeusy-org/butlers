# Conversation search indexed-reuse evaluation

**Date:** 2026-09-12
**Bead:** `bu-u22ss`
**Baseline:** `origin/main` at `e74841f8a4592825921e61939b9d2b416af631bf`
**Status:** Evaluation complete; retain indexed substring search
**Tests:** `+0 ~0 -0`

## Decision

Keep `conversation_search` on its current indexed, case-insensitive substring
contract (option A). Do not rewrite it onto the message-level full-text
primitive.

The original index-reuse motivation is already satisfied by
`core_221_dashboard_messages_search_index.py`, not `core_216`. That landed
migration installs both:

- `idx_dashboard_messages_content_trgm`, a `gin_trgm_ops` GIN index that makes
  `content ILIKE '%query%'` index-eligible; and
- `idx_dashboard_messages_search_vector`, a GIN index for the generated English
  `search_vector` used by `message_search` and `conversation_recall`.

On the synthetic selective-token workload below, PostgreSQL used both indexes.
The full-text predicate was materially faster in this one environment, but it
returned different results for partial words, inflection, reordered multi-word
input, stop words, non-English prefixes, and wildcard characters. It also does
not supply the conversation-level grouping, most-recent-match selection,
distinct total, or offset pagination contract. A silent predicate substitution
would therefore be a behavior change, not an internal reuse refactor.

No runtime, API, frontend, schema, migration, role grant, spec, test, or ratchet
change is authorized or recommended by this report.

## Authority and boundaries

This report is an investigation artifact. Required behavior remains in the
canonical specs and code; this document does not amend either.

- The experiment used only synthetic rows in a disposable PostgreSQL container.
  It did not read owner or production data.
- It created no application roles and issued no grants. A future search change
  must reuse the existing `public.dashboard_conversations` and
  `public.dashboard_messages` access boundary.
- Per-butler conversation search must remain filtered by `c.butler_name`.
  Owner-wide message recall is intentionally different and must not be used to
  widen the per-butler endpoint.
- No implementation bead follows from this decision. Any later proposal to
  change user-visible matching or result shape first needs a governed contract
  decision.

The governing precedence is `about/README.md`: Heart and Soul, Legends and
Lore, Spec and Spine, Craft and Care, topology, roster, then code. The relevant
architecture rule is the per-butler database boundary in
`about/heart-and-soul/architecture.md`. The searches are allowed to use their
existing shared `public` tables, but this evaluation creates no new cross-schema
path.

## Contract and consumer map

### Per-butler conversation search

| Item | Current contract and behavior |
| --- | --- |
| Canonical requirement | `openspec/specs/dashboard-conversations/spec.md`, **Requirement: Conversation Search**, scenarios **Search conversations by content** and **Empty search query** |
| Related result requirement | Same spec, **Requirement: Conversation Pydantic Response Models**, scenario **ConversationSearchResult model** |
| UI requirement | `openspec/specs/dashboard-chat-ui/spec.md`, **Requirement: Conversation Search UI** |
| HTTP surface | `GET /api/butlers/{name}/conversations/search?q=...&limit=...&offset=...` |
| Caller scope | One named butler; SQL requires `c.butler_name = $1` |
| Match | `m.content ILIKE '%query%'`, case-insensitive SQL pattern matching |
| Grouping | `DISTINCT ON (c.id)` chooses one matching message per conversation before the outer page |
| Representative | Most recent matching message by `m.created_at DESC`; equal timestamps currently have no `m.id` tie-break |
| Order | Conversations by representative `msg_created_at DESC` |
| Result | `ConversationSummary` fields plus `snippet`; snippet is the first 200 characters of the selected matching message; `latest_assistant_reply_at` is aggregated only for the final page |
| Pagination | Offset/limit plus `COUNT(DISTINCT c.id)` total |
| Validation | Router returns HTTP 400 with `code: VALIDATION_ERROR` for missing, empty, or blank `q`; `limit` is 1..100 and `offset` is non-negative |
| Backend implementation | `src/butlers/api/conversations.py::conversation_search`; `src/butlers/api/routers/conversations.py::search_conversations`; `src/butlers/api/models/conversation.py::ConversationSearchResult` |
| Same-repo consumers | `frontend/src/api/client.ts::searchConversations`; `frontend/src/hooks/use-conversations.ts::useConversationSearch`; the primary list in `frontend/src/components/chat/ConversationList.tsx` |

The frontend client currently types `searchConversations` as
`ApiResponse<ConversationSummary[]>`, and the primary conversation list renders
those rows through `ConversationItem`. The response still carries `snippet`,
but that client type does not model it and the primary list does not highlight
it. The UI spec's phrase "full-text" and highlighted-snippet scenario are thus
not evidence that the backend predicate is `tsvector`; the canonical backend
requirement and implementation specify containment plus a first-200-character
snippet. This adjacent UI/type observation is not changed here.

Existing behavior checks include:

- `tests/api/test_conversations.py::test_search_conversations_returns_summary_fields_and_matching_snippet`
- `tests/api/test_conversations.py::test_conversation_search_paginates_before_latest_reply_aggregate`
- `tests/integration/test_conversation_reply_db.py::test_conversation_search_exposes_latest_assistant_reply_at`

### Owner-wide message-level search and recall

| Item | Current contract and behavior |
| --- | --- |
| Canonical requirement | `openspec/specs/dashboard-conversations/spec.md`, **Requirement: Message-Level Search**, scenarios **Search messages across every butler**, **Cursor stable across a concurrent insert**, **Optional filters**, **Empty or overlong query**, and **conversation_recall MCP tool** |
| HTTP surface | `GET /api/conversations/messages/search?q=...&limit=...&cursor=...&channel=...&butler=...&from=...&to=...` |
| MCP surface | Always-on `conversation_recall(query, since, until, limit, channel, butler)` plus `conversation_thread_read` |
| Caller scope | Owner-wide across all butlers by default; optional butler filter; the MCP tool has the same owner scope regardless of calling butler |
| Match | `search_vector @@ plainto_tsquery('english', query)` |
| Row identity | One row per matching message; multiple messages from one conversation remain multiple results |
| Order | `ts_rank DESC`, then `created_at DESC`, then `message_id DESC` |
| Result | Message id, conversation id, role, timestamp, butler, session, `ts_headline` snippet, highlight ranges, and deep link |
| Pagination | Stable keyset cursor over `(rank, created_at, message_id)`; no total or offset |
| Validation | HTTP query length 1..512 and malformed filters/cursors return 422; the data layer and MCP tool return an empty result for blank input |
| Backend implementation | `src/butlers/api/conversations.py::message_search`; `src/butlers/api/routers/conversations.py::search_messages`; `src/butlers/core_tools/_conversation_recall.py` |
| Same-repo consumers | `frontend/src/api/client.ts::searchMessages`; `frontend/src/hooks/use-conversations.ts::useMessageSearch`; the separate **Messages** section in `ConversationList.tsx`; every butler's always-on recall tool |

The HTTP router's `min_length=1` accepts whitespace-only `q`, after which the
data layer returns an empty 200 page. That differs from the canonical
Message-Level Search scenario, which says blank input returns 422. It is a
pre-existing validation discrepancy, not evidence for changing the search
predicate and not repaired by this report.

Existing behavior checks include:

- `tests/api/test_conversation_recall_search_db.py::test_message_search_returns_hits_across_butlers_ranked`
- `tests/api/test_conversation_recall_search_db.py::test_message_search_cursor_stable_across_insert`
- the remaining DB, router, tool, and frontend tests in
  `tests/api/test_conversation_recall_search_db.py` and
  `frontend/src/components/chat/ConversationList.test.tsx`

## Why message rows cannot simply be deduplicated

The contracts paginate different logical entities:

1. Conversation search finds every matching message for one butler.
2. It chooses the most recent matching message per conversation.
3. It counts all distinct matching conversations.
4. It orders those representatives by match recency.
5. Only then does it apply conversation offset/limit.

Message search instead ranks and pages individual messages. If a consumer takes
an already-limited message page and deduplicates its conversation ids, a
conversation with several high-ranked messages can consume several input rows,
producing a short conversation page. Conversations whose first message hit lies
beyond the message cursor can also be omitted from that page, and the distinct
conversation total cannot be recovered. Grouping must occur before
conversation pagination and counting.

The synthetic `raresearchtoken` case made this concrete: finance had four
matching messages in three conversations because one conversation had two
matches. The owner-wide message result had five rows in four conversations
after adding the home-butler hit. The per-butler conversation pages correctly
contained two conversations on offset 0 and one on offset 2.

## Synthetic PostgreSQL method

### Environment and cardinality

- PostgreSQL `16.15 (Debian 16.15-1.pgdg13+2)` in a disposable
  `postgres:16` container.
- `pg_trgm` enabled.
- Schema recreated only with the columns and three indexes needed by the two
  query shapes, including the exact generated English `search_vector`,
  `idx_dashboard_messages_search_vector`,
  `idx_dashboard_messages_content_trgm`, and the pre-existing
  `(conversation_id, created_at)` index.
- 50,011 conversations and 50,013 messages.
- 50 selective `benchmarkneedle` messages for finance and 50 for home.
- Thirteen hand-seeded messages across eleven conversations exercised the
  semantic matrix. All ids, timestamps, butler names, and text were synthetic.
- Tables were analyzed before measurement. Both indexes were warmed once before
  recording `EXPLAIN (ANALYZE, BUFFERS)`.

The bulk seed was generated with:

```sql
INSERT INTO public.dashboard_conversations
  (id, butler_name, title, status, created_at, updated_at,
   message_count, source_channel)
SELECT md5('filler-conv-' || gs)::uuid,
       CASE WHEN gs % 10 = 0 THEN 'home' ELSE 'finance' END,
       'filler-' || gs,
       'active',
       '2025-01-01'::timestamptz + gs * interval '1 second',
       '2025-01-01'::timestamptz + gs * interval '1 second',
       1,
       'dashboard'
FROM generate_series(1, 50000) gs;

INSERT INTO public.dashboard_messages
  (id, conversation_id, role, content, created_at)
SELECT md5('filler-msg-' || gs)::uuid,
       md5('filler-conv-' || gs)::uuid,
       'user',
       CASE
         WHEN gs % 1000 IN (0, 1)
           THEN 'routine benchmarkneedle record ' || gs
         ELSE 'routine filler record ' || gs
       END,
       '2025-01-01'::timestamptz + gs * interval '1 second'
FROM generate_series(1, 50000) gs;

ANALYZE public.dashboard_conversations;
ANALYZE public.dashboard_messages;
```

The hand-seeded content was:

| Conversation | Butler | Message content | Purpose |
| --- | --- | --- | --- |
| `multiple-match` | finance | `raresearchtoken old match`; `raresearchtoken newest match` | Multiple matches in one conversation and representative selection |
| `second-conversation` | finance | `raresearchtoken second conversation` | Pagination boundary |
| `third-conversation` | finance | `raresearchtoken third conversation` | Pagination boundary |
| `cross-butler` | home | `raresearchtoken home-only conversation` | Per-butler isolation versus owner scope |
| `partial-word` | finance | `Ask the landlord about the lease` | Partial query `land` |
| `inflection` | finance | `Those stories were vivid` | Query `story` |
| `multi-word` | finance | `alpha appears, then much later beta` | Reordered query `beta alpha` |
| `stop-word` | finance | `the quick brown fox` | English stop word |
| `non-english` | finance | `mañana café rendezvous` | Exact and partial non-English query |
| `wildcard` | finance | `alphaXbeta and 50 percent` | SQL pattern characters |
| `equal-timestamp` | finance | `tieword representative A`; `tieword representative B` at the same timestamp | Representative tie |

### Predicate comparison SQL

The semantic matrix used the predicates from the two production query shapes,
with the same finance filter:

```sql
-- Current per-butler substring predicate.
WHERE c.butler_name = 'finance'
  AND m.content ILIKE '%' || :query || '%'

-- Message-level full-text predicate.
WHERE c.butler_name = 'finance'
  AND m.search_vector @@ plainto_tsquery('english', :query)
```

The current grouping and pagination shape measured by EXPLAIN was:

```sql
SELECT sub.id, sub.butler_name, sub.title, sub.status,
       sub.created_at, sub.updated_at, sub.message_count,
       sub.routed_butler,
       (
         SELECT MAX(reply.created_at)
         FROM public.dashboard_messages reply
         WHERE reply.conversation_id = sub.id
           AND reply.role = 'assistant'
       ) AS latest_assistant_reply_at,
       sub.snippet, sub.msg_created_at
FROM (
  SELECT DISTINCT ON (c.id)
         c.id, c.butler_name, c.title, c.status,
         c.created_at, c.updated_at, c.message_count, c.routed_butler,
         substring(m.content, 1, 200) AS snippet,
         m.created_at AS msg_created_at
  FROM public.dashboard_conversations c
  JOIN public.dashboard_messages m ON m.conversation_id = c.id
  WHERE c.butler_name = 'finance'
    AND m.content ILIKE '%benchmarkneedle%'
  ORDER BY c.id, m.created_at DESC
) AS sub
ORDER BY sub.msg_created_at DESC
LIMIT 20 OFFSET 0;
```

The predicate-only FTS comparison changed just the inner predicate to:

```sql
m.search_vector @@ plainto_tsquery('english', 'benchmarkneedle')
```

It deliberately retained the same conversation grouping, representative,
ordering, total, snippet, and page shape. This isolates index/predicate cost;
it is not a proposal to use message-level result rows.

The message-level plan used the production CTE shape:

```sql
WITH q AS (
  SELECT plainto_tsquery('english', 'benchmarkneedle') AS tsq
), matches AS (
  SELECT m.id AS message_id, m.conversation_id, m.role, m.created_at,
         m.session_id, c.butler_name, c.source_channel,
         ts_rank(m.search_vector, q.tsq)::float8 AS rank,
         ts_headline('english', m.content, q.tsq, :headline_options) AS headline
  FROM public.dashboard_messages m
  JOIN public.dashboard_conversations c ON c.id = m.conversation_id
  CROSS JOIN q
  WHERE m.search_vector @@ q.tsq
)
SELECT *
FROM matches
ORDER BY rank DESC, created_at DESC, message_id DESC
LIMIT 21;
```

### Result matrix

Counts below are message counts and distinct conversation counts after the
finance filter. The last column is owner-wide and shows the access-boundary
difference.

| Case / query | ILIKE messages | ILIKE conversations | FTS messages | FTS conversations | Owner-wide FTS messages | Consequence |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| exact token / `raresearchtoken` | 4 | 3 | 4 | 3 | 5 | Same term matches, but row and scope contracts still differ |
| partial word / `land` | 1 | 1 | 0 | 0 | 0 | Substring finds `landlord`; `plainto_tsquery` does not |
| inflection / `story` | 0 | 0 | 1 | 1 | 1 | English stemming finds `stories`; substring does not |
| reordered multi-word / `beta alpha` | 0 | 0 | 1 | 1 | 1 | FTS ANDs lexemes without phrase order; substring requires the contiguous input |
| stop word / `the` | 3 | 3 | 0 | 0 | 0 | English FTS reduces the query to no lexemes |
| non-English exact / `mañana` | 1 | 1 | 1 | 1 | 1 | Both match this exact token in the sample |
| non-English partial / `mañ` | 1 | 1 | 0 | 0 | 0 | Substring prefix survives; FTS token does not match |
| percent wildcard / `%` | 45,012 | 45,010 | 0 | 0 | 0 | Current ILIKE parameter treats `%` as a pattern wildcard |
| underscore wildcard / `_` | 45,012 | 45,010 | 0 | 0 | 0 | Current ILIKE parameter treats `_` as a one-character wildcard |
| empty result / `zzz-no-such-term` | 0 | 0 | 0 | 0 | 0 | Both return no match |

For `raresearchtoken`, conversation page 1 was `multiple-match` using the newer
message, then `second-conversation`; page 2 was `third-conversation`. The home
conversation was absent. Message-level FTS instead returned five message rows,
including both messages from `multiple-match` and the home row.

For `tieword`, both messages had the same timestamp. The current conversation
query has no final key inside the `DISTINCT ON` order, so which snippet represents
an equal-timestamp pair is not contractually or structurally deterministic.
Message search has `message_id DESC` as its final key. A later proposal must
choose and specify a conversation representative tie-break before it can claim
stable results.

The `%` and `_` results describe existing SQL-pattern behavior. They do not
decide whether the product intends those characters literally. Escaping them,
rejecting them, or preserving patterns is a separate governed validation and
matching decision; replacing the predicate with FTS would not be a neutral fix.

### EXPLAIN (ANALYZE, BUFFERS) evidence

All timings are one warm-cache observation, not a benchmark claim.

| Query | Relevant plan evidence | Actual output | Buffers | Planning / execution |
| --- | --- | ---: | --- | --- |
| Current grouped ILIKE, selective token | `Bitmap Index Scan on idx_dashboard_messages_content_trgm`; bitmap heap scan; group with `Unique`; top-N sort | 100 index hits, 50 finance conversations, 20 page rows | 281 index buffers; 778 hits plus 1 read for full query | 0.782 ms / 5.750 ms |
| Same grouping, FTS predicate | `Bitmap Index Scan on idx_dashboard_messages_search_vector`; bitmap heap scan; same `Unique` and top-N sort | 100 index hits, 50 finance conversations, 20 page rows | 4 index buffers; 456 hits | 0.385 ms / 0.544 ms |
| Current distinct-count ILIKE | Trigram bitmap index and heap scan, then distinct aggregate | 50 finance messages/conversations | 281 index buffers; 635 hits | 1.203 ms / 5.403 ms |
| Hypothetical distinct-count FTS | Search-vector bitmap index and heap scan, then distinct aggregate | 50 finance messages/conversations | 4 index buffers; 355 hits | 0.761 ms / 0.297 ms |
| Production-shaped owner-wide message FTS | Search-vector bitmap index, rank sort, limit 21 | 100 matches, 21 rows | 4 index buffers; 362 hits | 0.301 ms / 0.702 ms |
| Current grouped ILIKE for input `%` | Sequential scans, hash join, external merge for grouping, top-N sort | 50,013 scanned messages, 45,010 finance conversations, 20 page rows | 1,775 hits; 3,552 kB temp sort | 0.203 ms / 131.856 ms |

The selective ILIKE plan proves the existing trigram index is usable by the
current predicate. It does not prove every query will use it. PostgreSQL chose
a sequential scan for the unselective wildcard input because no useful trigram
could be extracted and nearly every row matched. Conversely, the FTS speedup
for `benchmarkneedle` does not establish universal superiority: that generated
token is highly selective, the English FTS index had only four matching index
pages, and the trigram index touched 281 index pages. Different text length,
term distribution, locale, selectivity, cache state, table size, statistics,
hardware, concurrent write load, and query mix can change the plan and cost.

Further limitations:

- The dataset is 50,013 short synthetic messages, not the owner's distribution.
- The container ran locally with warm shared buffers and no concurrent traffic.
- Only one PostgreSQL major/version and one statistics state were observed.
- The experiment did not measure generated-column write amplification, index
  build time, production latency percentiles, or operational lock behavior.
- The ILIKE plan estimated five hits but found 100, while FTS estimated 100 and
  found 100. That estimator difference contributed to this plan result and may
  differ on real statistics.
- A tiny dataset might choose a sequential scan even when the index is valid;
  an index scan is a planner choice, not an API guarantee.

## Options evaluated

### A. Retain indexed substring behavior -- recommended

Benefits:

- Preserves the canonical per-butler, one-conversation-per-result contract.
- Preserves containment behavior for partial and non-English terms.
- Preserves most-recent-match ordering, first-200-character snippets,
  conversation totals, and offset pagination.
- Uses the trigram GIN index already landed in `core_221`; no migration or grant
  work is needed.
- Leaves the owner-wide ranked message contract and its stable cursor untouched.

Costs and residuals:

- On the selective synthetic token, trigram ILIKE touched more index pages and
  took longer than FTS.
- SQL wildcard characters are not escaped today.
- Equal-timestamp representative selection has no message-id tie-break.
- Offset pagination does not promise stability under concurrent inserts, unlike
  the message-level keyset contract.

Those residuals are existing contract or correctness questions. None makes an
FTS substitution semantics-preserving.

### B. Share internals while preserving both contracts -- no rewrite now

The useful physical infrastructure is already shared: both searches use the
same messages table and the index pair installed by `core_221`. Limited helper
reuse for validation or query fragments would not remove the separate grouping,
ordering, snippet, scope, total, and pagination logic, and would risk obscuring
the access-boundary difference. There is no evidenced maintenance problem that
justifies such a refactor in this evaluation.

### C. Amend conversation search to ranked FTS -- not recommended

Potential benefits are stemming, unordered multi-term matches, relevance
ranking, lower buffer work for this selective token, and reuse of headline
generation. The semantic and migration costs are substantial:

- partial-word, stop-word, wildcard, phrase-order, and non-English behavior
  changes;
- relevance versus recency must be decided explicitly;
- one message per row must be regrouped into one conversation before counting
  and pagination;
- representative selection and equal-rank/timestamp ties need deterministic
  rules;
- first-200-character snippets would become centered headlines unless the
  existing result contract is preserved separately;
- offset/total versus cursor/no-total is a public response decision;
- 400 versus 422 and blank/overlong query handling must be reconciled; and
- every same-repo consumer and test must migrate together.

A `tsvector` replacement must not be described as semantics-preserving without
an approved OpenSpec decision.

## Ownership and live overlap refresh

Refresh performed on 2026-09-12 after fetching the live remote state.

- PR #3960, `agent/bu-7exe4.2`, is open at head
  `cf0ac589f0777b01484a8a984ffbcee6d0ba2da9`, reports `UNKNOWN`, and targets
  `main`. It changes `src/butlers/api/conversations.py`,
  `tests/api/test_conversations.py`,
  `tests/integration/test_conversation_reply_db.py`, and
  `openspec/specs/dashboard-conversations/spec.md`, among many identity-split
  surfaces. Its current hunks do not change `conversation_search`,
  `message_search`, or the two search requirement headings. It nevertheless
  owns overlapping files and must not be competed with.
- Active dashboard-conversation deltas on main are
  `add-dashboard-question-lane`,
  `conversation-anchor-provider-resume-ledger`,
  `durable-dashboard-terminal-action-recovery`, and
  `reconcile-dashboard-conversation-contracts`. They modify message models,
  reply/SSE/intent behavior, conversation identity/provider resume, ingestion,
  and `ConversationSearchResult`'s carried model fields. None currently
  modifies **Requirement: Conversation Search** or **Requirement: Message-Level
  Search**.
- The relevant recent issues are closed `bu-0ynlk.9` / merged PR #4012, which
  introduced message-level search and `core_221`; closed review `bu-4md5x`; and
  closed `bu-sq1qz`, which restored the conversation summary watermark and
  moved its aggregate outside the pre-pagination subquery. This evaluation is
  the only open search-reuse issue found by the refreshed title/description
  search.

Because option A requires no implementation, PR #3960 is not a delivery
prerequisite for this report. If an owner later selects option C, implementation
must wait until:

1. PR #3960 and every active delta touching the same requirement/model have a
   resolved ownership and archive order.
2. An approved OpenSpec change explicitly modifies **Requirement: Conversation
   Search**, and, if result fields move, **Requirement: Conversation Pydantic
   Response Models**. Any UI behavior change must also modify **Requirement:
   Conversation Search UI**.
3. The contract chooses matching language/config, phrase behavior, partial and
   wildcard handling, representative selection and ties, relevance versus
   recency, snippets/highlights, total/count behavior, offset versus cursor,
   concurrent-insert semantics, and validation codes.
4. The implementation remains per-butler and uses existing pools, tables,
   indexes, and grants. No owner-data inspection or new privilege is implied.
5. Backend, frontend, spec, docs, and tests move atomically; already-paginated
   message rows are never deduplicated into a conversation page.

## Test handoff

The following exact current-behavior nodes passed against the refreshed base:

```text
tests/api/test_conversations.py::test_search_conversations_returns_summary_fields_and_matching_snippet
tests/api/test_conversations.py::test_conversation_search_paginates_before_latest_reply_aggregate
tests/integration/test_conversation_reply_db.py::test_conversation_search_exposes_latest_assistant_reply_at
tests/api/test_conversation_recall_search_db.py::test_message_search_returns_hits_across_butlers_ranked
tests/api/test_conversation_recall_search_db.py::test_message_search_cursor_stable_across_insert
```

Result after the final rebase: `5 passed in 11.45s` with `-n 0`.

If a governed change is ever approved, extend those existing tests rather than
adding redundant test files:

- Extend the summary/snippet test with the approved match semantics and exact
  snippet/highlight result shape.
- Extend the pagination-before-aggregate test with multiple matching messages
  per conversation, grouping before limit/count, page boundaries, and the
  selected equal-timestamp tie-break.
- Extend the real-Postgres reply test to prove
  `latest_assistant_reply_at` remains independent of the chosen match message.
- Extend the cross-butler ranked-message test to prove the owner-wide message
  contract remains distinct and that an optional butler filter does not become
  the per-butler conversation API.
- Keep the message cursor-stability test unchanged unless that public cursor
  contract itself is explicitly amended.
- Update the nearest `ConversationList.test.tsx` cases if the per-butler client
  begins typing/rendering snippets or if the two result sections change.

The full semantic seed matrix for any future DB test should include multiple
matches per conversation, cross-butler isolation, partial words, inflection,
multi-word order, stop words, non-English exact and partial input, `%` and `_`,
equal timestamps, empty results, and page boundaries.

## Rollback and disposition

This report has no runtime or data effect. Its rollback is a normal revert of
the documentation commit/PR; no database, role, migration, API, or client state
must be unwound.

If a separately approved future implementation changes the predicate, its
rollback should restore the ILIKE query, existing result model, and same-repo
consumers together. `core_221` should remain in place because both its trigram
and search-vector indexes serve shipped behavior; no data rewrite is required.

The rewrite question is resolved for current evidence: option A is sufficient,
option B already exists at the physical-index level, and option C lacks both a
semantics-preserving path and a governed product decision.
