import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { GoogleHealthStatusCard } from "@/components/settings/GoogleHealthStatusCard";
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
      },
    });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("error state");
    expect(html).toContain("scope_set=health");
  });

  it("renders loading skeleton before first data arrives", () => {
    mockAccounts([{ id: "a", is_primary: true, granted_scopes: HEALTH_SCOPES }]);
    mockStatus({ isLoading: true });
    const html = renderToStaticMarkup(<GoogleHealthStatusCard />);
    expect(html).toContain("Google Health");
  });
});
