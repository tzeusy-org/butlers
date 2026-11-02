## Decisions

- Gaps live in the recording domain's own memory schema (`knowledge_gaps`, `knowledge_gap_origins`);
  no cross-schema SQL. Closure runs on the connection of the fact write, so it commits or rolls
  back with it. A closure error rolls back only its savepoint; the fact still commits.
- Capture, closure and the born-answerable re-check serialize on a transaction advisory lock keyed
  on (entity, predicate). A fact write racing a gap insert therefore cannot leave the gap open.
- Origins are rows (`knowledge_gap_origins`), not scalar columns, so two declines merged into one
  open gap each keep their own thread and each receive exactly one notice.
- The gap never carries content authority: the closing fact's server-stamped authority decides
  whether the notice reads "now known" (owner-class or system) or "reported by <sender>" (any
  other).
- Dashboard threads are not a `notify` channel. The delivery job posts into
  `public.dashboard_messages` through the same persistence `conversation_reply` uses.
- Delivery is at-least-once under a row lock: the origin row is locked, the message posted, then
  `delivered_at` set in the same transaction. A crash between post and commit can repeat a notice;
  a replayed fact write or a re-run of the job cannot.

## Goal-runtime re-scope (bu-q7vx1q.93.2)

The collision disposition says a declined owner goal records the gap through granted Memory
capabilities, not a classifier lane. These decisions apply recorded decisions (RFC 0038 D2, D4,
D7, D12, D15, D19; owner adoption of the D19 gap amendment on 2026-10-27) and add no owner policy.

- [decision] The gap `record` capability's owner domain is the domain whose memory schema
  registers the predicate. Capture validates the predicate in its own schema and closure runs in
  the schema the matching fact is written to, so this is the only placement where a recorded gap
  can ever be answered. The lead picks the domain as for any capability; no domain imports another
  domain's memory hook, and `core/memory_hooks.py` stays only as the pre-switch seam.
- [decision] Descriptor: effect class `record`, `owner_authority` false, `output_trust` trusted,
  `resource_params` the entity, `idempotency` `key_required` (D12: the broker's `effect_id` is the
  key, and a repeated key returns the original result). Arguments are entity, predicate and
  question summary. Goal, thread, request and channel come from the broker assertion.
- [decision] Channel admission is a closed set. The store admits `dashboard` today. `telegram`
  joins only when `bu-q7vx1q.70` lands both halves: owner-channel authorization binding channel and
  sender (REQ-capability-authorization-003), and delivery through the canonical owner-delivery
  subject (REQ-owner-delivery-001, RFC 0038 S6, whose exact text is still pending adoption through
  `bu-dhloi1.19`; its writer is `bu-dhloi1.13`). Until both exist a Telegram origin is refused, so
  no gap is captured that cannot be delivered. Email, WhatsApp and every other channel are refused
  `channel_unsupported`; widening them needs its own change.
- Pre-switch, the landed answer-lane `conversation_reply` `gap` argument stays as frozen-path
  behavior (fixes only). It is removed with the dashboard answer lane at the switch, and the
  broker capability becomes the only seam. No dual seam runs after the switch.

### Boundaries and their owners

| Boundary | Behavior | Production owner | Existing tests |
| --- | --- | --- | --- |
| Happy path | one open gap and origin; fact write makes it answerable; one notice per origin | `modules/memory/knowledge_gaps.py` (`record_gap`, `close_matching_gaps`, `deliver_knowledge_gaps`) | `tests/modules/memory/test_knowledge_gaps.py::test_decline_records_one_open_gap_then_a_fact_write_answers_it_once` |
| Merge | duplicate decline adds an origin, not a gap | `record_gap` | `test_duplicate_declines_merge_and_each_thread_gets_one_notice` |
| Born answerable | fact already present | `record_gap` / `_existing_answer` | `test_a_fact_that_predates_the_gap_makes_it_born_answerable` |
| Rollback of the fact | closure rolls back with it | `close_matching_gaps` | `test_closure_commits_and_rolls_back_with_the_fact_write`; `test_knowledge_gaps_inverse_pair.py::test_connected_outer_failure_or_cancel_rolls_back_both_facts_and_answers` |
| Closure failure | fact commits, gap stays open, counted | `close_matching_gaps` | `test_a_closure_failure_never_fails_the_fact_write` (counter export is `bu-q7vx1q.75`) |
| Concurrency | fact racing capture never leaves the gap open | advisory lock in `knowledge_gaps.py` | `test_a_fact_write_racing_the_gap_insert_never_leaves_the_gap_open`; `test_inverse_capture_serializes_in_both_database_lock_orders` |
| Replay | replayed fact write and unchanged re-assertion never re-notify | `close_matching_gaps` (open only), `relationship_assert_fact` (`unchanged` skips) | `test_decline_records_one_open_gap_then_a_fact_write_answers_it_once`; `test_temporal_inverse_replay_never_fabricates_an_answer_or_reactivates_a_row` |
| Stale fact | a row that stopped being active does not answer | `stored_gap_authority` path | `tests/modules/memory/test_knowledge_gaps_entity_fact_authority.py::test_a_row_that_stopped_being_active_leaves_the_gap_open` |
| Third-party authority | "reported by", never established | `_notice_text`; `stored_gap_authority` | `test_a_third_party_fact_is_reported_not_established`; `test_an_unconfirmed_third_party_fact_stays_reported`; `test_a_legacy_null_authority_fails_closed_to_third_party` |
| Delivery failure | bounded backoff, exhaustion to `delivery_failed` | `_deliver_one`, `deliver_knowledge_gaps` | `test_delivery_failure_keeps_the_gap_retrying_then_surfaces_it`; `test_inverse_delivery_crash_repeats_only_at_the_adopted_post_commit_boundary` |
| Terminal states | expiry and dismissal never reopen | `deliver_knowledge_gaps`, `dismiss_gap` | `test_expired_and_dismissed_gaps_are_terminal` |
| Capture refusal | invalid pair, sourced reply, missing module never block the decline | `_conversation_reply._record_knowledge_gap`, `record_gap_result` | `tests/core_tools/test_conversation_reply.py::test_gap_is_refused_but_the_decline_still_persists`; `test_gap_hook_failure_never_blocks_the_decline`; `test_knowledge_gaps.py::test_invalid_entity_or_predicate_is_refused_without_blocking_the_reply` |
| Server-derived origin | origin from routing context | `_conversation_reply` | `test_decline_gap_takes_origin_from_routing_context_and_question_from_thread` |
| Listing | coverage states; degraded pools named | `list_gaps`; `api/routers/memory.py` | `test_list_reports_open_gaps_as_unknown_and_answered_ones_as_present`; `tests/api/test_memory.py::test_gaps_name_a_failing_pool_instead_of_reading_as_empty` |

Future seams, owned by `bu-q7vx1q.70` and tested there on real PostgreSQL: broker-asserted
capture with origin from goal provenance, refusal of non-owner and unsupported-channel origins,
effect-identity replay returning the original result, and receipted owner-delivery with
`unknown` provider outcomes reconciled rather than resent. Budget: a gap `record` call is one tool
call against the goal's D10 budget; the delivery job is model-free and charges no goal. Rollback:
withdrawing the grant or redeploying the previous release stops new capture and leaves gaps,
origins and receipts intact (RFC 0038 Rollback). The broker refusal of a `record` call after a
matching read and the D18 fast answer recording no gap are owned by REQ-dashboard-goal-continuity-008.

### Scenario trace

| Original scenario | Now |
| --- | --- |
| butler-switchboard: A gap on a sourced reply is refused | moved, same name, to REQ-module-memory-019; goal successor in REQ-dashboard-goal-continuity-008 "A sourced answer records no gap" |
| butler-switchboard: A decline without a memory module still replies | moved, same name, to REQ-module-memory-019; goal successor "Gap capture failure does not block the answer" |
| module-memory: A decline with a gap records exactly one open row | retained in REQ-module-memory-018, seam-neutral wording |
| module-memory: A duplicate decline merges | retained unchanged |
| module-memory: A fact write answers the gap in its own transaction | retained unchanged |
| module-memory: A fact that predates the gap yields an answerable gap | retained unchanged |
| module-memory: A third-party fact is reported, not established | retained unchanged |
| module-memory: Expiry and dismissal are terminal | retained unchanged |
| module-memory: Delivery failure never drops a gap silently | retained; adds the landed `delivery_failed` outcome |
| relationship-facts: An assert closes a relationship-owned gap | retained unchanged in REQ-relationship-facts-009 |

Added from merged code: "A closure failure never loses the fact" and "An inverse edge answers the
object entity's gap" (bu-q7vx1q.74). Added as goal-runtime seams for `.70`: "Model-supplied origin
fields are ignored", "An unverified sender or unsupported channel records nothing", "A replayed
record call does not duplicate" and "An ambiguous send is reconciled, not repeated".

## Risks

- Every entity-anchored fact write pays one catalog probe, one advisory lock and one indexed
  UPDATE; the probe skips writes in schemas without the table.
- A predicate registered in more than one domain's schema lets a gap be recorded where the answer
  is later written elsewhere; it then stays open until expiry. The lead's domain choice is the
  mitigation, and no cross-schema closure is added.
