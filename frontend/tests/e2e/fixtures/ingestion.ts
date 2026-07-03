/**
 * Shared IngestionEventSummary fixture builder for Playwright e2e specs
 * (bu-lvu81).
 *
 * Why this exists: ingestion-timeline.spec.ts and ingestion-visual-parity.spec.ts
 * used to hand-write IngestionEventSummary mock literals directly inside
 * `JSON.stringify(...)` calls. Those literals were never checked against the
 * real `IngestionEventSummary` type (frontend/src/api/types.ts) — plain
 * object literals passed straight into JSON.stringify are untyped — so when
 * bu-4utdw.3 added the `sessions` / `session_count` / `tokens_in` /
 * `tokens_out` / `sender_display` rollup fields, the fixtures silently kept
 * omitting them. DispatchTicksCell was patched defensively (PR #2859) to
 * survive the gap, but any future component that assumes those fields exist
 * would hit the same whole-ledger render crash without warning.
 *
 * `makeIngestionEventSummary()` returns a value typed as the real
 * `IngestionEventSummary`, so the object literal inside this function is
 * itself checked by tsc: adding a new required field to the interface and
 * forgetting to default it here is a compile error at this one site, instead
 * of a runtime crash discovered by a component deep in the tree.
 */

import type {
  IngestionEventListSessionSummary,
  IngestionEventSummary,
} from "@/api/index.ts";

/**
 * A single realistic butler session, for fixtures that want to exercise the
 * dispatch-ticks cell (DispatchTicksCell.tsx) rather than its empty state.
 */
export function makeIngestionSession(
  overrides: Partial<IngestionEventListSessionSummary> = {},
): IngestionEventListSessionSummary {
  return {
    butler_name: "general",
    duration_ms: 4_200,
    cost_usd: 0.0123,
    success: true,
    ...overrides,
  };
}

/**
 * Full IngestionEventSummary fixture with every field defaulted to a
 * realistic value. Pass `overrides` to customize just the fields a given
 * test cares about — everything else stays a valid, fully-populated event so
 * components reading rollup fields (dispatch ticks, cost, token counts,
 * sender display) render their real (non-empty-state) path by default.
 */
export function makeIngestionEventSummary(
  overrides: Partial<IngestionEventSummary> = {},
): IngestionEventSummary {
  return {
    id: "aabbccdd-0000-0000-0000-000000000001",
    received_at: "2026-05-17T14:05:00Z",
    source_channel: "email",
    source_provider: null,
    source_endpoint_identity: null,
    source_sender_identity: "alice@example.com",
    source_thread_identity: null,
    external_event_id: null,
    dedupe_key: null,
    dedupe_strategy: null,
    ingestion_tier: null,
    policy_tier: "standard",
    triage_decision: null,
    triage_target: null,
    status: "ingested",
    filter_reason: null,
    error_detail: null,
    cost_usd: 0.0123,
    tokens_in: 1_200,
    tokens_out: 340,
    session_count: 1,
    sessions: [makeIngestionSession()],
    sender_display: null,
    ...overrides,
  };
}
