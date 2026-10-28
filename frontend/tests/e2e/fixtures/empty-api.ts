import type { Route } from "@playwright/test";
import { mockJson, type ResponseOf } from "./api-responses";

export const emptyApprovalMetrics: ResponseOf<"getApprovalMetrics"> = {
  data: { avg_decision_latency_seconds: null, callback_secret_configured: null, delivery_due_count: 0, delivery_retry_wait_count: 0, delivery_expired_lease_count: 0, delivery_ambiguous_count: 0, delivery_stuck_count: 0, delivery_oldest_due_age_seconds: null, delivery_by_state: {}, delivery_by_reason: {}, delivery_sources_complete: false, total_pending: 0, total_approved_today: 0, total_rejected_today: 0,
    total_auto_approved_today: 0, total_expired_today: 0, auto_approval_rate: 0,
    rejection_rate: 0, failure_count_today: 0, active_rules_count: 0 }, meta: {},
};

export function emptyBoard(): ResponseOf<"getButlersBoard"> {
  return { data: { rows: [], generated_at: new Date().toISOString(), aggregates: { sessions_source_error: false,
    total: 0, butler_count: 0, staffer_count: 0, active: 0, overdue: 0, offline: 0,
    quarantined: 0, total_sessions_24h: 0, total_spend_today: 0, avg_load_pct: null,
    heartbeat_source_error: false, registry_source_error: false, cost_source_error: false,
    has_per_entry_errors: false, sources_partially_degraded: false,
  } }, meta: {} };
}

export function butlerDetail(name: string): ResponseOf<"getButler">["data"] {
  return { db: "", description: null, last_session_started_at: null, db_name: null, db_schema: null, process_facts: null, blind_spots: [], blind_spots_evaluated_at: null, blind_spots_query_failed: false, name, status: "running", port: 0, type: "butler", sessions_24h: 0,
    modules: [], schedules: [], skills: [] };
}

export function boardRow(name: string): ResponseOf<"getButlersBoard">["data"]["rows"][number] {
  return { stripe_source_error: false, name, type: "butler", description: null, status: "running", activity: "idle",
    cell_tone: "neutral", eligibility: "active", quarantine_reason: null, quarantined_at: null,
    sessions_24h: 0, cost_today: 0, load_pct: null, max_concurrent: null, active_session_count: 0,
    last_session_at: null, last_heartbeat_at: null, heartbeat_age_seconds: null,
    heartbeat_unavailable: false, schema_unreachable: false, hourly_stripe: [], hourly_total: 0,
    cadence_seconds: null, cadence_label: null, silence_seconds: null, cadence_status: "unknown" };
}

export function condition(overrides: Partial<ResponseOf<"createCondition">> = {}): ResponseOf<"createCondition"> {
  return { id: "synthetic-condition", name: "Synthetic condition", status: "active",
    diagnosed_at: null, notes: null, created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z", ...overrides };
}

/** Known empty dependencies only. Unknown requests retain the strict mock's 404. */
export function emptyApiJson(url: URL, method = "GET"): string | undefined {
  if (method !== "GET") return undefined;
  if (/^\/api\/butlers\/[^/]+\/state$/.test(url.pathname)) {
    return mockJson("getButlerState", { data: [], meta: {} });
  }
  if (/^\/api\/butlers\/[^/]+\/schedules$/.test(url.pathname)) {
    return mockJson("getButlerSchedules", { data: [], meta: {} });
  }
  switch (url.pathname) {
    case "/api/health": return mockJson("getHealth", { status: "ok" });
    case "/api/butlers": return mockJson("getButlers", { data: [], meta: {} });
    case "/api/butlers/board": return JSON.stringify(emptyBoard());
    case "/api/settings/pricing":
    case "/api/pricing": return mockJson("fetchPricingMap", { data: {}, meta: {} });
    case "/api/approvals/metrics": return JSON.stringify(emptyApprovalMetrics);
    case "/api/butlers/switchboard/conversations": return mockJson("listConversations", {
      data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } });
    case "/api/spend": return mockJson("getCostSummary", { data: { period: "today", measured_usd: 0, unmeasurable_attempts: 0, total_cached_input_tokens: 0, total_cache_creation_tokens: 0, cache_read_cost_usd: 0, cache_hit_rate: null, unpriced_models: [], no_cache_price_models: [], divergences: [], divergence_source_error: false, historical_attribution_note: null, source_error: false, unavailable_butlers: [], total_cost_usd: 0,
      total_sessions: 0, total_input_tokens: 0, total_output_tokens: 0, by_butler: {}, by_model: {} }, meta: {} });
    case "/api/ingestion/events/histogram": return mockJson("getIngestionEventsHistogram", { buckets: [], bucket: "1m" });
    case "/api/ingestion/rollup": return mockJson("getIngestionWindowRollup", { unpriced_session_count: 0, no_usage_session_count: 0,
      events: 0, sessions: 0, cost: 0, window: { from: url.searchParams.get("from"), to: url.searchParams.get("to") } });
    case "/api/ingestion/connectors/summaries": return mockJson("getConnectorSummaries", { data: { connector_registry_available: true, connectors: [] }, meta: {} });
    case "/api/health/conditions": return mockJson("getConditions", { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } });
    case "/api/health/research": return mockJson("getResearch", { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } });
    default: return undefined;
  }
}

export function fulfillEmptyApi(route: Route): Promise<void> {
  const body = emptyApiJson(new URL(route.request().url()), route.request().method());
  return body === undefined ? route.fallback() : route.fulfill({ status: 200, contentType: "application/json", body });
}
