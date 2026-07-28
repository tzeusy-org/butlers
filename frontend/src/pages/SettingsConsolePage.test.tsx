// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
        attention_all: [],
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
    return Promise.resolve({ data: { total_pending: 0 }, meta: {} });
  }
  return Promise.resolve({ data: {} });
}

const apiFetchMock = vi.fn(defaultApiFetchImpl);

const malformedApprovalMetricsResponses: Array<[string, unknown]> = [
  ["missing meta", { data: { total_pending: 0 } }],
  ["a non-object meta", { data: { total_pending: 0 }, meta: "home" }],
  ["missing data", { meta: {} }],
  ["a non-object data", { data: [], meta: {} }],
  ["missing total_pending", { data: {}, meta: {} }],
  ["a non-numeric total_pending", { data: { total_pending: "0" }, meta: {} }],
  [
    "a non-array pending source list",
    { data: { total_pending: 0 }, meta: { pending_actions_sources_degraded: "home" } },
  ],
];

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
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
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
        return Promise.resolve({
          data: { mtd_usd: 42.5, ceiling_source_error: false },
        });
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
        return Promise.resolve({
          data: { mtd_usd: 0, ceiling_source_error: true },
        });
      }
      return defaultApiFetchImpl(path);
    });

    renderPageAsync();

    const panel = await screen.findByLabelText("Go to Spend");
    expect(await within(panel).findByText(/ledger unavailable/i)).toBeTruthy();
    expect(within(panel).queryByText("$0.00")).toBeNull();
  });

  it("Approvals panel names a partial pending source instead of rendering a fabricated zero", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/approvals/metrics") {
        return Promise.resolve({
          data: { total_pending: 0 },
          meta: { pending_actions_sources_degraded: ["home"] },
        });
      }
      return defaultApiFetchImpl(path);
    });

    renderPageAsync();

    const panel = await screen.findByLabelText("Go to Approvals");
    const note = await within(panel).findByTestId("settings-console-approvals-degraded");
    expect(note.textContent).toContain("Pending approvals: home unavailable");
    expect(within(panel).queryByText("0")).toBeNull();
    expect(within(panel).getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("Approvals panel renders a complete pending metric", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/approvals/metrics") {
        return Promise.resolve({ data: { total_pending: 2 }, meta: {} });
      }
      return defaultApiFetchImpl(path);
    });

    renderPageAsync();

    const panel = await screen.findByLabelText("Go to Approvals");
    expect(await within(panel).findByText("2")).toBeTruthy();
    expect(within(panel).getByText("approvals pending")).toBeTruthy();
    expect(within(panel).queryByText("Failed to load.")).toBeNull();
  });

  it.each(malformedApprovalMetricsResponses)(
    "Approvals panel renders %s metrics as a retryable query error instead of an all-clear",
    async (_caseName, response) => {
      apiFetchMock.mockImplementation((path: string) => {
        if (path === "/approvals/metrics") return Promise.resolve(response as never);
        return defaultApiFetchImpl(path);
      });

      renderPageAsync();

      const panel = await screen.findByLabelText("Go to Approvals");
      expect(await within(panel).findByText("Failed to load.")).toBeTruthy();
      expect(within(panel).getByRole("button", { name: /^Retry/ })).toBeTruthy();
      expect(within(panel).queryByText("0")).toBeNull();
      expect(within(panel).queryByText("approvals pending")).toBeNull();
      expect(
        within(panel).queryByTestId("settings-console-approvals-degraded"),
      ).toBeNull();
    },
  );

  it("retries malformed approval metrics from the query-error state", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/approvals/metrics") {
        return Promise.resolve({ data: { total_pending: 0 } });
      }
      return defaultApiFetchImpl(path);
    });

    renderPageAsync();

    const panel = await screen.findByLabelText("Go to Approvals");
    const retry = await within(panel).findByRole("button", { name: /^Retry/ });
    const approvalMetricsCalls = () =>
      apiFetchMock.mock.calls.filter(([path]) => path === "/approvals/metrics").length;
    const callsBeforeRetry = approvalMetricsCalls();

    await userEvent.setup().click(retry);

    await waitFor(() => expect(approvalMetricsCalls()).toBeGreaterThan(callsBeforeRetry));
  });

  it("expands and collapses real omitted attention items inline without an audit-log door", async () => {
    const allAttention = Array.from({ length: 6 }, (_, index) => ({
      id: `auth_renewal:provider-${index + 1}`,
      tone: "red" as const,
      kind: "auth_renewal",
      text: `Provider ${index + 1} needs auth.`,
      action_route: `/secrets?focus=c:cli-auth/provider-${index + 1}`,
    }));

    apiFetchMock.mockImplementation((path: string) => {
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
            attention: allAttention.slice(0, 5),
            attention_all: allAttention,
            attention_truncated_count: 1,
          },
        });
      }
      return defaultApiFetchImpl(path);
    });

    renderPageAsync();

    const expand = await screen.findByRole("button", { name: /1 more/i });
    expect(expand.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Provider 6 needs auth.")).toBeNull();
    expect(screen.queryByLabelText("Go to /audit-log")).toBeNull();

    const user = userEvent.setup();
    expand.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Provider 6 needs auth.")).toBeTruthy();

    const collapse = screen.getByRole("button", { name: /show 1 fewer/i });
    expect(collapse.getAttribute("aria-expanded")).toBe("true");
    collapse.focus();
    await user.keyboard(" ");
    expect(screen.queryByText("Provider 6 needs auth.")).toBeNull();
  });
});
