// @vitest-environment jsdom
/**
 * GoogleHealthStatusCard — unit tests.
 *
 * Tests:
 *  - Not configured: renders not-configured card when accounts is empty
 *  - Single account (healthy): renders one widget with correct state/email
 *  - Single account (degraded): renders amber state
 *  - Single account (error): renders error state
 *  - Multi-account: renders one widget per account
 *  - Multi-account: primary badge on correct account
 *  - Per-account fields: email, state, sleep_sessions_7d, daily_summaries_7d
 *  - Back-compat: single-account layout matches expected single-card shape
 *
 * bead: bu-91zdb.8
 */

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { GoogleHealthStatusCard } from "./GoogleHealthStatusCard";
import type { GoogleHealthStatusResponse } from "@/api/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderCard(status: GoogleHealthStatusResponse) {
  return render(
    <MemoryRouter>
      <GoogleHealthStatusCard status={status} />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const BASE_STATUS: GoogleHealthStatusResponse = {
  connected: false,
  scopes_granted: [],
  last_ingest_at: null,
  last_token_refresh_at: null,
  rate_limit_remaining: null,
  test_mode: false,
  state: "not_configured",
  error_message: null,
  sleep_sessions_7d: 0,
  daily_summaries_7d: 0,
  accounts: [],
  primary_account_email: null,
};

const HEALTH_SCOPES = [
  "https://www.googleapis.com/auth/fitness.sleep.read",
  "https://www.googleapis.com/auth/fitness.activity.read",
  "https://www.googleapis.com/auth/fitness.heart_rate.read",
];

const SINGLE_ACCOUNT_STATUS: GoogleHealthStatusResponse = {
  ...BASE_STATUS,
  connected: true,
  scopes_granted: HEALTH_SCOPES,
  last_ingest_at: "2026-05-25T10:00:00Z",
  state: "healthy",
  sleep_sessions_7d: 7,
  daily_summaries_7d: 5,
  accounts: [
    {
      email: "user@example.com",
      state: "healthy",
      error_message: null,
      scopes_granted: HEALTH_SCOPES,
      last_ingest_at: "2026-05-25T10:00:00Z",
      last_token_refresh_at: null,
      rate_limit_remaining: null,
      sleep_sessions_7d: 7,
      daily_summaries_7d: 5,
    },
  ],
  primary_account_email: "user@example.com",
};

const MULTI_ACCOUNT_STATUS: GoogleHealthStatusResponse = {
  ...BASE_STATUS,
  connected: true,
  scopes_granted: HEALTH_SCOPES,
  state: "healthy",
  sleep_sessions_7d: 12,
  daily_summaries_7d: 9,
  accounts: [
    {
      email: "primary@example.com",
      state: "healthy",
      error_message: null,
      scopes_granted: HEALTH_SCOPES,
      last_ingest_at: "2026-05-25T10:00:00Z",
      last_token_refresh_at: null,
      rate_limit_remaining: null,
      sleep_sessions_7d: 7,
      daily_summaries_7d: 5,
    },
    {
      email: "secondary@example.com",
      state: "degraded",
      error_message: null,
      scopes_granted: [HEALTH_SCOPES[0]],
      last_ingest_at: null,
      last_token_refresh_at: null,
      rate_limit_remaining: null,
      sleep_sessions_7d: 5,
      daily_summaries_7d: 4,
    },
  ],
  primary_account_email: "primary@example.com",
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("GoogleHealthStatusCard — not configured", () => {
  afterEach(() => cleanup());

  it("renders not-configured card when accounts is empty", () => {
    renderCard(BASE_STATUS);
    expect(screen.getByTestId("google-health-not-configured")).toBeDefined();
    expect(screen.queryByTestId("google-health-account-widget")).toBeNull();
  });
});

describe("GoogleHealthStatusCard — single account", () => {
  afterEach(() => cleanup());

  it("renders one widget for a single healthy account", () => {
    renderCard(SINGLE_ACCOUNT_STATUS);
    const widgets = screen.getAllByTestId("google-health-account-widget");
    expect(widgets).toHaveLength(1);
  });

  it("shows the account email on the widget", () => {
    renderCard(SINGLE_ACCOUNT_STATUS);
    expect(screen.getByTestId("account-email").textContent).toBe("user@example.com");
  });

  it("shows the account state text on the widget", () => {
    renderCard(SINGLE_ACCOUNT_STATUS);
    expect(screen.getByTestId("account-state").textContent).toBe("healthy");
  });

  it("shows sleep_sessions_7d correctly", () => {
    renderCard(SINGLE_ACCOUNT_STATUS);
    expect(screen.getByTestId("sleep-sessions-7d").textContent).toBe("7");
  });

  it("shows daily_summaries_7d correctly", () => {
    renderCard(SINGLE_ACCOUNT_STATUS);
    expect(screen.getByTestId("daily-summaries-7d").textContent).toBe("5");
  });

  it("shows scope summary (3/3) when all scopes granted", () => {
    renderCard(SINGLE_ACCOUNT_STATUS);
    expect(screen.getByText("3 / 3 scopes")).toBeDefined();
  });

  it("renders outer container with data-testid google-health-status-card", () => {
    renderCard(SINGLE_ACCOUNT_STATUS);
    expect(screen.getByTestId("google-health-status-card")).toBeDefined();
  });
});

describe("GoogleHealthStatusCard — single account state colours", () => {
  afterEach(() => cleanup());

  it("renders degraded state on widget", () => {
    const degraded: GoogleHealthStatusResponse = {
      ...SINGLE_ACCOUNT_STATUS,
      state: "degraded",
      accounts: [
        { ...SINGLE_ACCOUNT_STATUS.accounts[0], state: "degraded" },
      ],
    };
    renderCard(degraded);
    expect(screen.getByTestId("account-state").textContent).toBe("degraded");
  });

  it("renders error state on widget", () => {
    const error: GoogleHealthStatusResponse = {
      ...SINGLE_ACCOUNT_STATUS,
      state: "error",
      accounts: [{ ...SINGLE_ACCOUNT_STATUS.accounts[0], state: "error" }],
    };
    renderCard(error);
    expect(screen.getByTestId("account-state").textContent).toBe("error");
  });
});

describe("GoogleHealthStatusCard — connector-failing (degraded) signal", () => {
  afterEach(() => cleanup());

  it("renders the connector-unavailable banner for a 403 (api_forbidden) degraded account", () => {
    const forbidden: GoogleHealthStatusResponse = {
      ...SINGLE_ACCOUNT_STATUS,
      connected: false,
      state: "degraded",
      error_message: "api_forbidden",
      accounts: [
        {
          ...SINGLE_ACCOUNT_STATUS.accounts[0],
          state: "degraded",
          error_message: "api_forbidden",
        },
      ],
    };
    renderCard(forbidden);
    // The degraded signal must render — NOT a silent empty/healthy state.
    const banner = screen.getByTestId("connector-error-banner");
    expect(banner).toBeDefined();
    expect(banner.textContent).toContain("unavailable");
    expect(banner.textContent).toContain("403");
  });

  it("renders the banner for an error account (token_invalid)", () => {
    const errored: GoogleHealthStatusResponse = {
      ...SINGLE_ACCOUNT_STATUS,
      connected: false,
      state: "error",
      error_message: "token_invalid",
      accounts: [
        {
          ...SINGLE_ACCOUNT_STATUS.accounts[0],
          state: "error",
          error_message: "token_invalid",
        },
      ],
    };
    renderCard(errored);
    const banner = screen.getByTestId("connector-error-banner");
    expect(banner.textContent).toContain("unavailable");
  });

  it("does NOT render the banner for a healthy account (empty-but-healthy stays empty)", () => {
    renderCard(SINGLE_ACCOUNT_STATUS);
    expect(screen.queryByTestId("connector-error-banner")).toBeNull();
  });

  it("does NOT render the banner for a degraded account with no error_message", () => {
    // Degraded-but-no-reason (e.g. stale heartbeat) is not a 'failing' signal.
    const degradedNoReason: GoogleHealthStatusResponse = {
      ...SINGLE_ACCOUNT_STATUS,
      state: "degraded",
      error_message: null,
      accounts: [
        { ...SINGLE_ACCOUNT_STATUS.accounts[0], state: "degraded", error_message: null },
      ],
    };
    renderCard(degradedNoReason);
    expect(screen.queryByTestId("connector-error-banner")).toBeNull();
  });

  it("renders a generic unavailable message with the raw code for unknown reasons", () => {
    const unknown: GoogleHealthStatusResponse = {
      ...SINGLE_ACCOUNT_STATUS,
      state: "degraded",
      error_message: "weird_new_code",
      accounts: [
        {
          ...SINGLE_ACCOUNT_STATUS.accounts[0],
          state: "degraded",
          error_message: "weird_new_code",
        },
      ],
    };
    renderCard(unknown);
    const banner = screen.getByTestId("connector-error-banner");
    expect(banner.textContent).toContain("weird_new_code");
  });
});

describe("GoogleHealthStatusCard — multi-account", () => {
  afterEach(() => cleanup());

  it("renders two widgets for two accounts", () => {
    renderCard(MULTI_ACCOUNT_STATUS);
    const widgets = screen.getAllByTestId("google-health-account-widget");
    expect(widgets).toHaveLength(2);
  });

  it("renders primary badge on the primary account widget only", () => {
    renderCard(MULTI_ACCOUNT_STATUS);
    const badges = screen.getAllByText("primary");
    expect(badges).toHaveLength(1);
  });

  it("shows both account emails", () => {
    renderCard(MULTI_ACCOUNT_STATUS);
    const emails = screen.getAllByTestId("account-email").map((el) => el.textContent);
    expect(emails).toContain("primary@example.com");
    expect(emails).toContain("secondary@example.com");
  });

  it("shows per-account sleep counts in multi-account layout", () => {
    renderCard(MULTI_ACCOUNT_STATUS);
    const sleepCounts = screen
      .getAllByTestId("sleep-sessions-7d")
      .map((el) => el.textContent);
    expect(sleepCounts).toContain("7");
    expect(sleepCounts).toContain("5");
  });

  it("shows per-account daily summary counts in multi-account layout", () => {
    renderCard(MULTI_ACCOUNT_STATUS);
    const dailyCounts = screen
      .getAllByTestId("daily-summaries-7d")
      .map((el) => el.textContent);
    expect(dailyCounts).toContain("5");
    expect(dailyCounts).toContain("4");
  });

  it("shows partial scope count (1/3) when fewer scopes granted", () => {
    renderCard(MULTI_ACCOUNT_STATUS);
    // secondary account has 1 scope; primary has 3
    expect(screen.getAllByText("3 / 3 scopes")).toHaveLength(1);
    expect(screen.getAllByText("1 / 3 scopes")).toHaveLength(1);
  });

  it("shows degraded state on secondary account", () => {
    renderCard(MULTI_ACCOUNT_STATUS);
    const states = screen.getAllByTestId("account-state").map((el) => el.textContent);
    expect(states).toContain("healthy");
    expect(states).toContain("degraded");
  });
});
