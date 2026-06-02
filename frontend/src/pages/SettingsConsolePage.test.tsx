// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import SettingsConsolePage from "@/pages/SettingsConsolePage";

vi.mock("@/api/client", () => ({
  apiFetch: vi.fn((path: string) => {
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
    if (path === "/spend?period=30d") {
      return Promise.resolve({ data: { total_cost_usd: 0 } });
    }
    if (path === "/approvals/metrics") {
      return Promise.resolve({ data: { total_pending: 0 } });
    }
    return Promise.resolve({ data: {} });
  }),
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

describe("SettingsConsolePage", () => {
  it("links owner configuration to /settings/owner", () => {
    const html = renderPage();

    expect(html).toContain("Owner Config");
    expect(html).toContain('aria-label="Go to Owner Config"');
  });
});
