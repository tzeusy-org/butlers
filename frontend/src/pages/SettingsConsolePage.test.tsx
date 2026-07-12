// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import SettingsConsolePage from "@/pages/SettingsConsolePage";

// useSettingsConsoleLive subscribes to the shared EventBusProvider (bu-3quv8),
// which this page test does not mount (it renders via renderToStaticMarkup,
// synchronously, before any query resolves anyway) -- mirrors SpendPage.test.tsx's
// mock of useSpendTicker for the same reason. Pass-through keeps this test's
// only real dependency on the REST-fetched `consoleResp?.data` above.
vi.mock("@/hooks/use-settings-console-live", () => ({
  useSettingsConsoleLive: (data: unknown) => data,
}));

// GET /api/spend/forecast, not GET /spend?period=30d -- the Spend panel's
// "MTD" label is only true when it prices from the ledger (bu-7o89u.2).
const DEFAULT_FORECAST = { mtd_usd: 0, ceiling_source_error: false };

function defaultApiFetchImpl(path: string) {
  if (path === "/settings/console") {
    return Promise.resolve({
      data: {
        header_counts: {
          active_butlers: 0,
          spend_mtd_usd: 0,
          open_approvals: 0,
          models_verified: 0,
          models_total: 0,
        },
        attention: [],
        attention_truncated_count: 0,
      },
    });
  }
  if (path === "/settings/models") {
    return Promise.resolve({ data: [] });
  }
  if (path === "/spend/forecast") {
    return Promise.resolve({ data: DEFAULT_FORECAST });
  }
  if (path === "/approvals/metrics") {
    return Promise.resolve({ data: { total_pending: 0 } });
  }
  return Promise.resolve({ data: {} });
}

const apiFetchMock = vi.fn(defaultApiFetchImpl);

vi.mock("@/api/client", () => ({
  apiFetch: (path: string) => apiFetchMock(path),
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsConsolePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderPageAsync() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsConsolePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation(defaultApiFetchImpl);
});

describe("SettingsConsolePage", () => {
  it("links credentials to /secrets and no longer surfaces an owner-config panel", () => {
    const html = renderPage();

    // Google OAuth app credentials moved onto /secrets; the standalone
    // /settings/owner panel was removed.
    expect(html).toContain("Secrets");
    expect(html).toContain('aria-label="Go to Secrets"');
    expect(html).not.toContain("Owner Config");
    expect(html).not.toContain("/settings/owner");
  });

  it("Spend panel renders the ledger-priced MTD from GET /api/spend/forecast", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/forecast") {
        return Promise.resolve({ data: { mtd_usd: 42.5, ceiling_source_error: false } });
      }
      return defaultApiFetchImpl(path);
    });

    renderPageAsync();

    // Scoped to the Spend panel card (aria-label from PanelShell) -- the
    // header KPI strip has its own, independently-sourced "Spend MTD" cell.
    const panel = await screen.findByLabelText("Go to Spend");
    expect(await within(panel).findByText("$42.50")).toBeTruthy();
    expect(apiFetchMock).toHaveBeenCalledWith("/spend/forecast");
    expect(apiFetchMock).not.toHaveBeenCalledWith("/spend?period=30d");
  });

  it("Spend panel renders a degraded note instead of a fabricated $0 when the ledger source failed", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/forecast") {
        return Promise.resolve({ data: { mtd_usd: 0, ceiling_source_error: true } });
      }
      return defaultApiFetchImpl(path);
    });

    renderPageAsync();

    const panel = await screen.findByLabelText("Go to Spend");
    expect(await within(panel).findByText(/ledger unavailable/i)).toBeTruthy();
    expect(within(panel).queryByText("$0.00")).toBeNull();
  });
});
