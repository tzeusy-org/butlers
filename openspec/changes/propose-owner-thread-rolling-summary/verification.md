## Authoring Checks

Run named `openspec validate propose-owner-thread-rolling-summary --strict`, actual parser `change show --json --deltas-only`, `openspec validate --all --strict`, `make check-spec-overwrites`, `make check-countable-tasks`, `make check-guards`, `npm run knip`, and `git diff --check`. Record exact exit status/receipts externally. CLI config's preexisting rules key `spec` may warn; no guard/config changes here. Parse must report both actual MODIFIED and all three ADDED requirements, not an ignored text/scaffold pass. Independently compare all 10 prior scenario bodies byte-for-byte and all original clauses/ACs against baseline.json. No broad pytest or product tests needed for docs/spec-only source. Tests: +0 ~0 -0.

## One Primary Future Gate Species per Invariant

| Invariant | Primary executing species and real seam |
| --- | --- |
| Same-owner/thread and hidden/tool/page/secret admission | Contract integration test: final .15 authenticated resolver -> trusted source projection -> actual generator input adapter; current role/content-only selection is insufficient |
| CAS/idempotence, concurrent mutation, invalidation/restart | Real-Postgres integration test: future private store through two connections/real runtime roles, actual source query and publication/consumer transaction |
| Real rolling summary and exact bounded marked fallback | Behavior unit test: actual `api/conversation_envelope.py` builder, real source-window selection adapter and deterministic fake generator output, including early context carried and caps/labels/escaping |
| Original route/Stop/retry/page_context/provenance compatibility | Contract integration test: actual `api/routers/conversations.py` -> build_dashboard_envelope -> durable turn/ingest adapter, retaining immutable message_id and original stored page_context |
| No client/tool/log exposure and derivative deletion custody | Contract integration test: future actual thread/list/search/message/SSE/tool projections and protected store deletion, with raw history unchanged and sentinel payload absent |

Nearest current tests: `tests/api/test_conversations.py` envelope pin/page_context/retry/Stop cases; `tests/integration/test_dashboard_conversation_lineage.py`; `tests/integration/test_conversation_history_db.py`; `tests/core_tools/test_conversation_recall.py`; `tests/core_tools/test_conversation_reply.py`. These are reuse pointers, not executed proof of new behavior. Future test inventory/net delta must be sized from adopted scenarios, not invented now. Fake transport/model only: source authoring performs no LLM/provider calls or live secret/thread reads.

## Original Acceptance Disposition

AC1: native exact candidate authored; named strict/overwrite/full-guard receipts and clause/scenario audit required, source-only portion reviewable. AC2: exact owner adoption pending. AC3: adopted-change implementation filing pending; proposed followups only. AC4: no product code/migration/prompt changes. A draft PR and green syntax are not whole-bead completion.
