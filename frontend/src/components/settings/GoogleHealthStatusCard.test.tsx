import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { GoogleHealthStatusCard } from "@/components/settings/GoogleHealthStatusCard";
import { computeTokenExpiry } from "@/components/settings/GoogleHealthStatusCard.utils";
import { useGoogleHealthStatus } from "@/hooks/use-google-health";
import { useGoogleAccounts } from "@/hooks/use-secrets";
import { computeTestModeBannerVariant } from "@/lib/google-health-test-mode";

vi.mock("@/hooks/use-google-health", () => ({
  useGoogleHealthStatus: vi.fn(),
}));

vi.mock("@/hooks/use-secrets", () => ({
  useGoogleAccounts: vi.fn(),
}));

const HEALTH_SCOPES = [
  "https://www.googleapis.com/auth/googlehealth.sleep",
  "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
  "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
];

function mockAccounts(accounts: Array<Record<string, unknown>>) {
  vi.mocked(useGoogleAccounts).mockReturnValue({
    data: accounts,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useGoogleAccounts>);
}

function mockStatus(override: Record<string, unknown>) {
  vi.mocked(useGoogleHealthStatus).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...override,
  } as unknown as ReturnType<typeof useGoogleHealthStatus>);
}

describe("computeTestModeBannerVariant", () => {
  it("returns orange when last_token_refresh_at is null", () => {
    expect(computeTestModeBannerVariant(null)).toBe("orange");
  });

  it("returns orange when refresh is recent", () => {
    const now = new Date("2026-04-24T12:00:00Z");
    const oneDayAgo = new Date(now.getTime() - 24 * 3600 * 1000).toISOString();
    expect(computeTestModeBannerVariant(oneDayAgo, now)).toBe("orange");
  });

  it("returns red when refresh is older than 5d6h", () => {
    const now = new Date("2026-04-24T12:00:00Z");
    const sixDaysAgo = new Date(now.getTime() - 6 * 24 * 3600 * 1000).toISOString();
    expect(computeTestModeBannerVariant(sixDaysAgo, now)).toBe("red");
  });

  it("returns red exactly at 5d6h boundary", () => {
    const now = new Date("2026-04-24T12:00:00Z");
    const thresholdMs = (5 * 24 + 6) * 3600 * 1000;
    const atThreshold = new Date(now.getTime() - thresholdMs).toISOString();
    expect(computeTestModeBannerVariant(atThreshold, now)).toBe("red");
  });

  it("returns orange just inside the threshold", () => {
    const now = new Date("2026-04-24T12:00:00Z");
    const thresholdMs = (5 * 24 + 6) * 3600 * 1000;
    const slightlyBefore = new Date(
      now.getTime() - thresholdMs + 60 * 1000,
    ).toISOString();
    expect(computeTestModeBannerVariant(slightlyBefore, now)).toBe("orange");
  });

  it("falls back to orange on invalid timestamp", () => {
    expect(computeTestModeBannerVariant("not-a-date")).toBe("orange");
  });
});

describe("GoogleHealthStatusCard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("does not render when no primary account exists", () => {
    mockAccounts([]);
    mockStatus({});
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toBe("");
  });

  it("does not render when primary lacks Google Health scopes", () => {
    mockAccounts([
      {
        id: "a1",
        is_primary: true,
        granted_scopes: ["https://www.googleapis.com/auth/calendar"],
      },
    ]);
    mockStatus({});
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toBe("");
  });

  it("renders card when primary has all Google Health scopes", () => {
    mockAccounts([
      {
        id: "a1",
        is_primary: true,
        granted_scopes: HEALTH_SCOPES,
      },
    ]);
    mockStatus({
      data: {
        state: "healthy",
        connected: true,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: "2026-04-24T11:57:00Z",
        last_token_refresh_at: "2026-04-24T10:00:00Z",
        rate_limit_remaining: 500,
        test_mode: false,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("Google Health");
    expect(html).toContain("Healthy");
    expect(html).toContain("google-health-status-card");
  });

  it("hides rate-limit row when rate_limit_remaining is null", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    mockStatus({
      data: {
        state: "healthy",
        connected: true,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: null,
        rate_limit_remaining: null,
        test_mode: false,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).not.toContain("gh-rate-limit-row");
    expect(html).not.toContain("Rate limit remaining");
  });

  it("renders orange test-mode banner when test_mode is true with recent refresh", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    const recent = new Date(Date.now() - 1 * 24 * 3600 * 1000).toISOString();
    mockStatus({
      data: {
        state: "healthy",
        connected: true,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: recent,
        rate_limit_remaining: null,
        test_mode: true,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("gh-test-mode-banner-orange");
    expect(html).not.toContain("gh-test-mode-banner-red");
    expect(html).toContain("Learn more");
  });

  it("renders red test-mode banner when last_token_refresh_at is older than 5d6h", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    const old = new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString();
    mockStatus({
      data: {
        state: "degraded",
        connected: false,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: old,
        rate_limit_remaining: null,
        test_mode: true,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("gh-test-mode-banner-red");
    expect(html).toContain("about to expire");
    expect(html).toContain("scope_set=health");
  });

  it("renders error banner with re-grant CTA when state is error", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    mockStatus({
      data: {
        state: "error",
        connected: false,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: null,
        rate_limit_remaining: null,
        test_mode: false,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("error state");
    expect(html).toContain("scope_set=health");
  });

  it("renders sleep session and daily summary 7-day counts", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    mockStatus({
      data: {
        state: "healthy",
        connected: true,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: null,
        rate_limit_remaining: null,
        test_mode: false,
        sleep_sessions_7d: 3,
        daily_summaries_7d: 28,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("gh-sleep-sessions-7d");
    expect(html).toContain("Sleep sessions (7d)");
    expect(html).toContain(">3<");
    expect(html).toContain("gh-daily-summaries-7d");
    expect(html).toContain("Daily summaries (7d)");
    expect(html).toContain(">28<");
  });

  it("renders zero counts when no events have been ingested", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    mockStatus({
      data: {
        state: "degraded",
        connected: false,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: null,
        rate_limit_remaining: null,
        test_mode: false,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    // Rows are always rendered; counts show 0 when empty.
    expect(html).toContain("gh-sleep-sessions-7d");
    expect(html).toContain("gh-daily-summaries-7d");
  });

  it("renders loading skeleton before first data arrives", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    mockStatus({ isLoading: true });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("Google Health");
  });

  // -------------------------------------------------------------------------
  // Token expiry row
  // -------------------------------------------------------------------------

  it("renders token expiry countdown for test-mode account", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    // refreshed 2 days ago → ~5 days remaining (exact hours depend on wall-clock)
    const twoDaysAgo = new Date(Date.now() - 2 * 24 * 3600 * 1000).toISOString();
    mockStatus({
      data: {
        state: "healthy",
        connected: true,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: twoDaysAgo,
        rate_limit_remaining: null,
        test_mode: true,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("gh-token-expiry-row");
    expect(html).toContain("Estimated expiry");
    // Should contain a countdown in the form "in ~Xd Yh"
    expect(html).toMatch(/in ~\d+d \d+h/);
  });

  it("renders 'Long-lived' expiry for production-mode account", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    mockStatus({
      data: {
        state: "healthy",
        connected: true,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: "2026-04-01T00:00:00Z",
        rate_limit_remaining: null,
        test_mode: false,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("gh-token-expiry-row");
    expect(html).toContain("Long-lived (production mode)");
  });

  it("renders 'Unknown' expiry when last_token_refresh_at is null in test mode", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    mockStatus({
      data: {
        state: "healthy",
        connected: true,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: null,
        rate_limit_remaining: null,
        test_mode: true,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("gh-token-expiry-row");
    expect(html).toContain("Unknown");
  });

  // -------------------------------------------------------------------------
  // Refresh indicator
  // -------------------------------------------------------------------------

  it("shows refresh indicator when isFetching is true", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    mockStatus({
      isFetching: true,
      data: {
        state: "healthy",
        connected: true,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: null,
        rate_limit_remaining: null,
        test_mode: false,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("gh-refresh-indicator");
  });

  it("hides refresh indicator when isFetching is false", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    mockStatus({
      isFetching: false,
      data: {
        state: "healthy",
        connected: true,
        scopes_granted: HEALTH_SCOPES,
        last_ingest_at: null,
        last_token_refresh_at: null,
        rate_limit_remaining: null,
        test_mode: false,
        sleep_sessions_7d: 0,
        daily_summaries_7d: 0,
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).not.toContain("gh-refresh-indicator");
  });
});

// ---------------------------------------------------------------------------
// computeTokenExpiry pure helper
// ---------------------------------------------------------------------------

describe("computeTokenExpiry", () => {
  const now = new Date("2026-04-24T12:00:00Z");

  it("returns 'Long-lived (production mode)' for production accounts", () => {
    expect(computeTokenExpiry(false, null, now)).toBe("Long-lived (production mode)");
    expect(computeTokenExpiry(false, "2026-04-20T00:00:00Z", now)).toBe(
      "Long-lived (production mode)",
    );
  });

  it("returns 'Unknown' when last_token_refresh_at is null in test mode", () => {
    expect(computeTokenExpiry(true, null, now)).toBe("Unknown");
  });

  it("returns 'Unknown' for an invalid timestamp in test mode", () => {
    expect(computeTokenExpiry(true, "not-a-date", now)).toBe("Unknown");
  });

  it("returns countdown string for test-mode with valid refresh timestamp", () => {
    // refreshed 2 days ago → 5 days 0 hours remaining
    const twoDaysAgo = new Date(now.getTime() - 2 * 24 * 3600 * 1000).toISOString();
    expect(computeTokenExpiry(true, twoDaysAgo, now)).toBe("in ~5d 0h");
  });

  it("returns 'Expired' when token is past the 7-day window", () => {
    const eightDaysAgo = new Date(now.getTime() - 8 * 24 * 3600 * 1000).toISOString();
    expect(computeTokenExpiry(true, eightDaysAgo, now)).toBe("Expired");
  });

  it("correctly rounds down partial hours", () => {
    // refreshed 3 days and 2.5 hours ago → 3 days and ~21.5 hours remaining → 3d 21h
    const refreshedAt = new Date(
      now.getTime() - (3 * 24 + 2.5) * 3600 * 1000,
    ).toISOString();
    expect(computeTokenExpiry(true, refreshedAt, now)).toBe("in ~3d 21h");
  });
});
