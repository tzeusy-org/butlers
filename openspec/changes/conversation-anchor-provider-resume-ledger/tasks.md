## 1. Ledger

- [x] 1.1 Add `core_185` migration: `source_channel`, `source_thread_identity`, `provider_session_id`, `provider_runtime_type`, `provider_session_updated_at` on `public.dashboard_conversations`, plus the partial unique thread-anchor index.
- [x] 1.2 Add `conversation_get_or_create_by_thread`, provider-session CRUD, and `resolve_resume_handle` to `butlers.api.conversations`.
- [x] 1.3 Real-Postgres integration tests for the upsert/conflict semantics and provider-session round-trip/eviction; mocked-pool unit tests for the pure eviction/TTL logic.

## 2. Provider Adapter

- [x] 2.1 Add `RuntimeAdapter.supports_resume` capability flag (default `False`).
- [x] 2.2 `ClaudeCodeAdapter`: accept `resume_session_id`, emit `--resume`, capture the CLI-reported `session_id` into `last_process_info["provider_session_id"]`.
- [x] 2.3 Unit tests for the conditional `--resume` flag and session-id capture (present/absent/non-JSON output).

## 3. Verification

- [x] 3.1 Run targeted pytest for the touched modules; run the full suite once before handoff.
- [x] 3.2 `ruff check` / `ruff format --check` on touched files.
- [x] 3.3 `openspec validate --strict` on this change.

## 4. Follow-up (filed as beads, not part of this change)

- [x] 4.1 Wire `conversation_get_or_create_by_thread` into the Telegram/email ingest paths (bu-bkthr). Landed in `src/butlers/core_tools/_routing.py`'s `route.execute` background processor rather than `pipeline.py`: the target `butler_name` the anchor must be scoped to is only known once Switchboard's classification has chosen a route, and `_process_route` already holds the parsed, typed `source_channel`/`source_thread_identity` for that target butler.
- [x] 4.2 Wire resume-handle lookup/persist/fallback-to-cold into `Spawner`'s invoke path for interactive trigger sources (bu-bkthr). Gated on `trigger_source == "route"`; a failed resume attempt with no confirmed side-effecting tool call transparently retries the same candidate cold without writing a `runtime_failure` provenance row or consuming a same-tier failover slot.

  Landing evidence: [#3592](https://github.com/Tzeusy/butlers/pull/3592)
  reviewed head `3b992b1d70da16a8b5577caeac0f5cc9ca3d7cd9` against base
  `10661019436644ba8253a880c3fac385781987f5`, passed CI run `30185159686`
  (`check`, `frontend`, `frontend-e2e`, `em-dash-guard`, and
  `session-link-guard`), and landed as squash
  `91fff3a5a9f9fc067818c882f5e1e9947b74405e` on `2026-07-26T03:28:52Z`.
- [ ] 4.3 First-token streaming to the chat widget / Telegram typing surface (rank #8 slice 3).
- [ ] 4.4 Unified Conversations read surface + inline-undo action receipts (rank #8 slice 4).
