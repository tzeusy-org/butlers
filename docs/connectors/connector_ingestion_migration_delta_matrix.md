# Connector Ingestion Migration Delta Matrix

Status: Draft (implementation migration map)  
Issue: `butlers-zb7.1`  
Epic: `butlers-zb7`  
Primary target contract: `docs/connectors/interface.md`

## Status Legend
- `keep`: preserve the component in target state (possibly with interface hardening).
- `replace`: move responsibility to a new target component; legacy path is transitional only.
- `remove`: retire after cutover.

## Current-to-Target Migration Matrix

| Current ingestion path | Current implementation anchors | Target-state component | Status | Owning follow-up bead(s) | Overlap boundary with `butlers-9aq` | Cutover + rollback checkpoint |
| --- | --- | --- | --- | --- | --- | --- |
| Telegram module polling ingestion loop | `src/butlers/modules/telegram.py` (`on_startup`, `_poll_loop`, `process_update`) | External Telegram bot connector runtime calling private Switchboard ingest API with `ingest.v1` | replace | `butlers-zb7.3`, then `butlers-zb7.6` | Reuse canonical ingest semantics from `butlers-9aq.2`/`butlers-9aq.3`; do not re-implement envelope parsing or context assignment in connector code | **CP-2**: enable connector in canary while legacy polling remains available. **RB-2**: disable connector process and resume module polling mode (`mode="polling"`). |
| Telegram module webhook ingestion path | `src/butlers/modules/telegram.py` (`on_startup`, `_set_webhook`, `process_update`) | Connector-owned webhook receiver that normalizes updates and submits to ingest API | replace | `butlers-zb7.3`, then `butlers-zb7.6` | `butlers-9aq` owns canonical ingest lifecycle behavior; webhook connector owns transport/auth/retry only | **CP-3**: move prod webhook target to connector endpoint. **RB-2**: repoint Telegram webhook to existing daemon endpoint and keep request lineage stable via same dedupe identity (`update_id` + endpoint identity). |
| Email inbox polling and direct route trigger | `src/butlers/modules/email.py` (`bot_email_check_and_route_inbox`, `process_incoming`, `_check_and_route_inbox`) | Gmail connector runtime (`watch` + history delta, bounded catch-up polling fallback) calling ingest API | replace | `butlers-zb7.4`, then `butlers-zb7.6` | Do not duplicate dedupe policy already tracked in `butlers-9aq.4`; connector must emit stable message identity and accept duplicate-accepted responses | **CP-4**: enable Gmail connector for one mailbox/label scope while inbox polling tool remains fallback. **RB-3**: stop connector, restore `bot_email_check_and_route_inbox` scheduling path. |
| In-daemon `MessagePipeline` ingress acceptance + inline classify/route | `src/butlers/modules/pipeline.py` (`_accept_ingress`, `process`), `src/butlers/daemon.py` (`_wire_pipelines`) | Switchboard private ingest API boundary (`202` + canonical request reference) decoupled from connector transport loops | replace | `butlers-zb7.2` (API surface), `butlers-zb7.6` (legacy loop deprecation) | `butlers-9aq.3` already established canonical ingest handler concept; `butlers-zb7.2` should harden/private-expose it for connectors, not fork a second ingest path | **CP-1**: ship ingest API behind auth and dark-launch validation. **RB-1**: disable endpoint exposure and keep module-wired pipeline as sole active ingress path. |
| Switchboard ingest envelope and route envelope schema primitives | `roster/switchboard/tools/routing/contracts.py` (`IngestEnvelopeV1`, `RouteEnvelopeV1`, parse helpers) | Shared canonical contract models used by ingest API and connector runtimes | keep | `butlers-zb7.2` (reuse), plus existing `butlers-9aq.2` baseline | Out of scope for `butlers-zb7` to redesign schema versions; any schema evolution remains under Switchboard contract epic | **CP-0**: lock connector work to current `ingest.v1`/`route.v1` contracts. **RB-0**: if connector runtime fails validation, keep traffic on legacy module paths while retaining unchanged schema models. |
| Switchboard dedupe/lifecycle persistence primitives | `src/butlers/modules/pipeline.py` (`_accept_ingress` writes `message_inbox`), `roster/switchboard/migrations/005_create_message_inbox_table.py` | Canonical ingest dedupe + lifecycle persistence behind API boundary (not in connector workers) | keep (ownership), replace (call path) | `butlers-zb7.2` depends on `butlers-9aq.4` + `butlers-9aq.9` | `butlers-9aq.4` (dedupe policy) and `butlers-9aq.9` (lifecycle store redesign) remain source-of-truth; connector epic consumes these, does not duplicate migrations/policy logic | **CP-1.5**: require `butlers-9aq.4` and `butlers-9aq.9` readiness before full connector cutover. **RB-1.5**: hold connector rollout until dedupe/lifecycle primitives are stable. |

## Overlap Boundaries with `butlers-9aq`

`butlers-zb7` must avoid duplicating these already-owned/ongoing Switchboard contract streams:

1. `ingest.v1` and `route.v1` contract-model ownership (`butlers-9aq.2` baseline).
2. Canonical ingest acceptance/context-assignment behavior (`butlers-9aq.3` baseline).
3. Ingest dedupe policy authority (`butlers-9aq.4`, currently blocked).
4. Lifecycle persistence/retention redesign (`butlers-9aq.9`, in progress).

`butlers-zb7` owns connector-runtime migration, API exposure/hardening for connector callers, and removal of module-owned direct ingestion loops once connector paths are proven.

## Phased Rollout Sequence

1. **Phase 0 - Contract freeze + dependency check**  
   Confirm connector work pins to existing `ingest.v1` contract and track dependency readiness (`butlers-9aq.4`, `butlers-9aq.9`).
2. **Phase 1 - Private ingest API surface** (`butlers-zb7.2`)  
   Expose authenticated connector-facing ingest endpoint returning canonical request references.
3. **Phase 2 - Telegram bot connector cutover** (`butlers-zb7.3`)  
   Move polling/webhook ingestion ownership from module loops to connector runtime.
4. **Phase 3 - Gmail connector cutover** (`butlers-zb7.4`)  
   Move inbox ingestion ownership from module tool path to Gmail watch/history connector.
5. **Phase 4 - Telegram user-client connector (gated)** (`butlers-zb7.5`)  
   Add v2/gated user-client ingestion path with explicit consent/privacy controls.
6. **Phase 5 - Deprecate legacy module ingestion loops** (`butlers-zb7.6`)  
   Remove/disable module-owned direct ingestion paths after connector stability burn-in.
7. **Phase 6 - Conformance + docs closure** (`butlers-zb7.7`)  
   Land connector-ingest conformance tests and synchronized operational docs.

## Rollback Checkpoints

- **RB-1 (after Phase 1):** keep endpoint dark/disabled and continue module-owned ingestion only.
- **RB-2 (after Telegram cutover):** disable Telegram connector runtime and restore module polling/webhook routing path.
- **RB-3 (after Gmail cutover):** disable Gmail connector runtime and resume inbox polling tool path.
- **RB-4 (after legacy deprecation start):** temporarily re-enable compatibility ingestion path behind explicit feature flag if connector regressions appear.

Rollback principle: revert traffic ownership first (connector -> legacy path) while keeping canonical dedupe identities stable to avoid duplicate downstream request lineage.
