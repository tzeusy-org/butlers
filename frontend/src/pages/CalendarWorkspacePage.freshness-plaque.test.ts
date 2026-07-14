import { describe, expect, it } from "vitest";

import type { CalendarWorkspaceSourceFreshness } from "@/api/types";

import { computeFreshnessPlaque } from "./CalendarWorkspacePage";

function freshSource(
  overrides: Partial<CalendarWorkspaceSourceFreshness> = {},
): CalendarWorkspaceSourceFreshness {
  return {
    source_id: "source-1",
    source_key: "google:primary",
    source_kind: "provider_event",
    lane: "user",
    provider: "google",
    calendar_id: "primary",
    butler_name: "general",
    display_name: "Primary",
    writable: true,
    metadata: {},
    cursor_name: "provider_sync",
    last_synced_at: "2026-03-01T10:00:00Z",
    last_success_at: "2026-03-01T10:00:00Z",
    last_error_at: null,
    last_error: null,
    full_sync_required: false,
    sync_state: "fresh",
    staleness_ms: 1000,
    error_kind: "none",
    sync_enabled: true,
    ...overrides,
  } as CalendarWorkspaceSourceFreshness;
}

describe("computeFreshnessPlaque — sources_available honesty (bu-sn71y)", () => {
  it("returns no plaque when sources are fresh and the fan-out was clean", () => {
    expect(computeFreshnessPlaque([freshSource()], "success", true)).toBeNull();
  });

  it("forces a degraded plaque when the sources fan-out failed, even though every returned source looks fresh", () => {
    // Without the fix a partial fan-out failure (200 OK, one schema silently
    // dropped) leaves only fresh sources -> the plaque reads all-clear. The
    // degraded flag must override that false all-clear.
    const plaque = computeFreshnessPlaque([freshSource()], "success", false);
    expect(plaque).toEqual({ detail: "Sync status unavailable", unknown: true });
  });

  it("does not show a degraded plaque while the meta query is still pending", () => {
    expect(computeFreshnessPlaque([freshSource()], "pending", false)).toBeNull();
  });

  it("defaults to healthy when sources_available is omitted (backward compatible)", () => {
    expect(computeFreshnessPlaque([freshSource()], "success")).toBeNull();
  });
});
