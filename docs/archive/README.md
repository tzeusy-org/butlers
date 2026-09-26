# Historical documentation

> **Purpose:** Identify retained historical evidence and canonical successors for retired plans.
> **Audience:** Maintainers following an old design or research reference.

This directory holds the successor map below and one retained decision record,
[`decisions/2026-05-19-contacts-listed-flag-migration.md`](decisions/2026-05-19-contacts-listed-flag-migration.md),
whose rationale the `core_103` migration and its test still cite. It is not an
implementation queue or a second specification tree. Preserve a
record when a decision or acceptance check still needs its evidence; otherwise
synthesize its durable content into the canonical contract and retire the body.
Use Git history to recover retired text at its original path.

## Retired plans and successors

| Retired artifact | Durable subject | Maintained successor |
|---|---|---|
| `memory-improvements.md` | Local canonical memory, shared entities, tenant/request lineage, consolidation and temporal facts | [Memory capability](../../openspec/specs/module-memory/spec.md), [entity identity](../../openspec/specs/entity-identity/spec.md), and [memory guide](../modules/memory.md) |
| `memory-improvements.md` | Context ordering, approximate section budgets, and the unimplemented `TokenBudgeter`/uniform tie-break proposals | [Memory guide: retrieval and context assembly](../modules/memory.md#retrieval) |
| `memory-improvements.md` | Retention and discovery-only shared catalog | [Retention policy](../../openspec/specs/memory-retention-policy/spec.md) and [discovery catalog](../../openspec/specs/memory-discovery-catalog/spec.md) |
| `memory-improvements-pt2.md` | Episode retention persistence; tenant grouping and executor propagation | [Retention policy](../../openspec/specs/memory-retention-policy/spec.md) and [memory consolidation contract](../../openspec/specs/module-memory/spec.md) |
| `memory-improvements-pt2.md` | Event lineage, catalog DDL, real-database migration verification | [Events enrichment](../../openspec/specs/memory-events-enrichment/spec.md), [catalog schema](../../openspec/specs/memory-catalog-schema/spec.md), and [migration integration tests](../../openspec/specs/memory-migration-integration-tests/spec.md) |
| `memory-improvements-pt2.md` | Proposed `embedding_versions` table | [Events enrichment](../../openspec/specs/memory-events-enrichment/spec.md) explicitly supersedes this table with per-row model-version tracking; it is not a build target |
| `health-wearable-draft.md` | Obsolete Fitbit integration research | [Google Health connector](../../openspec/specs/connector-google-health/spec.md), [Google Health module](../../openspec/specs/module-google-health/spec.md), and [recorded API pivot](../../openspec/changes/archive/2026-04-24-google-health-connector/design.md) |

The memory successor mapping establishes where requirements belong, **not that
implementation is complete**. The three retention, tenant-grouping, and
executor-lineage gaps reported from the residual plan were fixed under bead
`bu-txa1n` (closed, PR #4055) with regression coverage; verify current code rather
than historical source line numbers before relying on either.

The wearable research's provider comparisons and draft schema are superseded
research, not additional requirements for Google Health or approval to implement
Apple Health or Health Connect integrations.

## Maintenance and verification

Behavior changes update the canonical spec and affected guide in the same change.
Before retiring another record, identify its remaining decision or evidence use,
map durable clauses to successors, and update incoming live references. Archived
OpenSpec records remain dated provenance and may name a retired historical path.
Check successor links from this index and search the repository for each retired
basename; do not interpret an old archive citation as a new implementation task.

## Retired execution recipes

The following files formerly lived under `docs/plans/` (the first eight rows)
and `docs/superpowers/plans/` (the remaining rows). Their temporary archive
copies are also retired. The table names the maintained contract, active change,
or interface that replaces each recipe; an **active change remains active**,
regardless of whether its redundant execution plan has been removed. Changes
marked archived have since landed; their successor is the archived record.

| Retired filename | Durable subject | Successor |
|---|---|---|
| `2026-07-03-dashboard-chat-widget-design.md` | Conversation identity and terminal send/retry behavior | [dashboard-conversations](../../openspec/specs/dashboard-conversations/spec.md) |
| `2026-07-05-chronicler-time-inference-deep-dive.md` | Intent, evidence, activity and corroborated inference | [chronicler-intent-evidence-activity](../../openspec/specs/chronicler-intent-evidence-activity/spec.md) |
| `2026-07-14-spotify-lifecycle-alert-exclusion-design.md` | Spotify system rows excluded from credential lifecycle alerts | [core-credentials](../../openspec/specs/core-credentials/spec.md) |
| `2026-07-17-chat-send-retry-semantics.md` | Durable retry identity and ambiguous outcome refusal | [dashboard-conversations](../../openspec/specs/dashboard-conversations/spec.md) |
| `2026-07-28-replayable-routed-approvals-design.md` | Replayable approval origin and deferred delivery | [make-routed-approvals-replayable (archived change)](../../openspec/changes/archive/2026-09-12-make-routed-approvals-replayable/) |
| `2026-07-28-talk-to-butlers-maturity-design.md` | Conversation reliability and superseded question-lane decision | [add-dashboard-question-lane (archived change)](../../openspec/changes/archive/2026-09-12-add-dashboard-question-lane/) |
| `2026-08-24-secrets-authority-state-repair-design.md` | Canonical credential authority and connector state | [repair-secrets-authority-projections (archived change)](../../openspec/changes/archive/2026-09-12-repair-secrets-authority-projections/) |
| `2026-08-30-agent-test-ladder-and-five-minute-lanes.md` | Graduated test scope and measured five-minute target | [Owning document or interface](../../about/craft-and-care/testing-and-verification.md) |
| `2026-07-19-dashboard-attention-current.md` | Attention and briefing surface contracts | [dashboard-briefing](../../openspec/specs/dashboard-briefing/spec.md) |
| `2026-07-28-replayable-routed-approvals.md` | Replayable approval delivery | [make-routed-approvals-replayable (archived change)](../../openspec/changes/archive/2026-09-12-make-routed-approvals-replayable/) |
| `2026-07-29-dashboard-chat-stop-handoff-remediation.md` | Stop and terminal-effect recovery | [durable-dashboard-terminal-action-recovery (active change)](../../openspec/changes/durable-dashboard-terminal-action-recovery/) |
| `2026-08-01-calendar-sync-dispatch.md` | Durable calendar sync queue | [calendar-workspace-sync-queue (archived change)](../../openspec/changes/archive/2026-09-12-calendar-workspace-sync-queue/) |
| `2026-08-01-durable-approval-recurrence.md` | Standing approval rules and suppression | [module-approvals](../../openspec/specs/module-approvals/spec.md) |
| `2026-08-02-dashboard-chat-current-contract.md` | Pending terminal receipt and reconciliation contract | [durable-dashboard-terminal-action-recovery (active change)](../../openspec/changes/durable-dashboard-terminal-action-recovery/) |
| `2026-08-09-harden-live-codex-auth-sync.md` | Device auth reconciliation | [core-credentials](../../openspec/specs/core-credentials/spec.md) |
| `2026-08-10-codex-failover-env-isolation.md` | Caller environment isolation | [Owning document or interface](../../src/butlers/core/runtimes/codex.py) |
| `2026-08-10-ingestion-ledger-review-gates.md` | Fail-closed replay policy | [ingestion-event-registry](../../openspec/specs/ingestion-event-registry/spec.md) |
| `2026-08-13-compose-restore-drill-password-preflight.md` | Metadata-only prerequisite before lifecycle actions | [Owning document or interface](../operations/backup-restore.md#bootstrap-prerequisite) |
| `2026-08-13-restore-drill-dev-opt-in.md` | Explicit protected topology selection | [Owning document or interface](../operations/backup-restore.md#bootstrap-prerequisite) |
| `2026-08-23-bounded-secrets-inventory-availability.md` | Bounded source reads and unavailable evidence | [bound-secrets-inventory-availability (active change)](../../openspec/changes/bound-secrets-inventory-availability/) |
| `2026-08-24-secrets-authority-state-repair.md` | Credential authority projections | [repair-secrets-authority-projections (archived change)](../../openspec/changes/archive/2026-09-12-repair-secrets-authority-projections/) |
| `2026-08-24-whatsapp-identity-reconciliation.md` | WhatsApp identity reconciliation; detailed design retained | [repair-whatsapp-identity-reconciliation (active change)](../../openspec/changes/repair-whatsapp-identity-reconciliation/) |
| `2026-08-28-core-connector-liveness.md` | Completed policy relocation | [Owning document or interface](../../src/butlers/core/liveness.py) |
| `2026-08-28-frontend-timezone-alias-cleanup.md` | Completed timezone import consolidation | [Owning document or interface](../../frontend/src/components/ui/timezone-context.tsx) |
| `2026-08-28-home-assistant-reliability.md` | Subscription truth and measurement recovery | [connector-home-assistant](../../openspec/specs/connector-home-assistant/spec.md) |
| `2026-08-28-nightly-ci-clock-schema-repair.md` | Python and database clock-domain verification | [Owning document or interface](../testing/testing-strategy.md) |
| `2026-08-28-oauth-module-token-payload.md` | Validate token response before cache mutation | [Owning document or interface](../../src/butlers/oauth_token_payload.py) |
| `2026-08-28-test-fixture-registration-cleanup.md` | Canonical fixture registration | [testing](../../openspec/specs/testing/spec.md) |

The conversation and chat UI baselines govern durable identity, Stop, and retry.
The terminal-action recovery change retains pending effect receipts,
reconciliation, and rollout work. The question-lane proposal records the
September 2 owner decision superseding the July 28 restriction; it preserves
specialist routing, honest declines, receipts, and Stop semantics. Retiring the
older design neither completes those changes nor restores the superseded choice.

## Retired spent records (bu-60pwv6.2)

These bodies were deleted after their durable content was confirmed in the
successor below. Recover any of them with `git log --all -- <original path>`.

| Retired path | Durable subject | Successor |
|---|---|---|
| `docs/archive/2026-07-14-doctrine-spec-code-reconciliation-synthesis.md` | Doctrine/spec/code reconciliation (bu-0ss2f3); both owner escalations decided | Beads `bu-p14g0` (digest channel, PR #2804) and `bu-4b4n7` (egress deny: won't-do) |
| `docs/archive/butlers-review-improvement-cycles.md` | Closed review-cycle program `bu-dl98i` | Archived cycle changes under [changes/archive](../../openspec/changes/archive/) |
| `docs/archive/decisions/2026-05-22-credential-readpath-deferred.md` | Credential read-path deferral until `bu-akads` (both closed) | [core-credentials](../../openspec/specs/core-credentials/spec.md) |
| `docs/archive/decisions/2026-05-23-chronicler-adapter-entity-id-audit.md` | Self-declared superseded audit (column dropped, bu-cfsgy) | None; not actionable |
| `docs/archive/draft-discord.md` | Discord connector research | [connector-discord](../../openspec/specs/connector-discord/spec.md), [RFC 0018](../../about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md) |
| `docs/archive/home-assistant-draft.md` | Home Assistant research | [connector-home-assistant](../../openspec/specs/connector-home-assistant/spec.md), [module-home-assistant](../../openspec/specs/module-home-assistant/spec.md) |
| `docs/archive/photos-screenshots-draft.md` | Photo and screenshot ingestion research | Superseded research, not a build target |
| `docs/archive/voice-draft.md` | Voice capture research | [connector-live-listener](../../openspec/specs/connector-live-listener/spec.md) and the open voice changes under [changes](../../openspec/changes/) |
| `docs/archive/whatsapp-draft.md` | WhatsApp research | [WhatsApp connector guide](../connectors/whatsapp.md), [repair-whatsapp-identity-reconciliation](../../openspec/changes/repair-whatsapp-identity-reconciliation/) |
| `docs/archive/reports/bu-hpmgo-finance-bill-payment-reconciliation.md` (+ diagrams) | Finance bill/payment reconciliation epic report | [butler-finance](../../openspec/specs/butler-finance/spec.md) |
| `docs/archive/reports/dashboard-permissions-parity-bu-9q1dx.13.md` | Permissions parity gate; residual wipe gap in bead `bu-9q1dx.14` | [dashboard-permissions](../../openspec/specs/dashboard-permissions/spec.md) |
| `docs/plans/bu-9srh2-stash-provenance.md` | Machine-local stash audit; no action proposed | None |
| `docs/plans/bu-jpqrz-xdist-closed-channel.md` | Upstream pytest-xdist contribution draft | Owner-decision bead `bu-60pwv6.8` |
| `docs/plans/bu-u22ss-conversation-search-evaluation.md` | Keep substring conversation search, not FTS | `conversation_search` docstring in `src/butlers/api/conversations.py` |
| `docs/plans/spec-debt-provenance-next-tranche.md` | Point-in-time spec-debt research (PR #4148) | Beads `bu-w8uno`, `bu-tk618`; [system prompt authority decision](../plans/system-prompt-authority-decision.md) |
| `docs/reports/2026-09-05-run-09-release-inventory.md` | Run-09 release inventory snapshot | Note on bead `bu-xf54r` (commit `d684c5776`) |
| `docs/reports/2026-09-12-ingestion-read-optimization.md` | Ingestion read-pressure PR notes (#4155) | [dashboard-ingestion-dispatch-console](../../openspec/specs/dashboard-ingestion-dispatch-console/spec.md) |
| `docs/redesigns/2026-06-12-entity-brief-v3.md`, `2026-06-20-health-brief.md`, `design-language.md`, `2026-09-22-jarvis-pursuit-report.html` | Shipped briefs, redirect stub, rendered dossier copy | [Redesigns index](../redesigns/README.md) |
