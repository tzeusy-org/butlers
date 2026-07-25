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

- [ ] 4.1 Wire `conversation_get_or_create_by_thread` into the Telegram/email ingest paths in `src/butlers/modules/pipeline.py`.
- [ ] 4.2 Wire resume-handle lookup/persist/fallback-to-cold into `Spawner`'s invoke path for interactive trigger sources.
- [ ] 4.3 First-token streaming to the chat widget / Telegram typing surface (rank #8 slice 3).
- [ ] 4.4 Unified Conversations read surface + inline-undo action receipts (rank #8 slice 4).
