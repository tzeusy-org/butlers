## 1. Implementation
- [x] 1.1 `sw_055`: request columns and pending index on `attachment_refs`.
- [x] 1.2 Switchboard `attachment_materialize`: request, bounded wait, typed results, D4 declaration, dark.
- [x] 1.3 Gmail connector: request poll loop, owner-scoped claim, failure recording, qualified SQL, replay-safe upsert, declared-size pre-check and post-download size guard, blob store wiring.
- [x] 1.4 ATTACHMENTS prompt: `status=not_fetched` for lazy refs, one shared builder.

## 2. Verification
- [x] 2.1 Real-database test: request, claim, fetch, idempotence, replay, owner scoping, failure and retry, declared-size refusal.
- [x] 2.2 Prompt test over both route paths.
- [x] 2.3 Inventory contract: cataloged with D4 metadata on Switchboard only, not listed.
