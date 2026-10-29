// @vitest-environment jsdom
/** Rendered export promises and the registered action. REQ-dashboard-permissions-001. */
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import SettingsPermissionsPage from "@/pages/SettingsPermissionsPage";
import {
  CommandRegistryProvider,
  useCommandMenuActions,
} from "@/lib/command-registry";
import { clearOwnerSession, rememberOwnerCsrf } from "@/api/owner-session";

// Keep the real apiFetch/ownerFetch serializers; observe only the HTTP boundary.
const fetchMock = vi.fn();
vi.mock("@/hooks/use-audit-log", () => ({
  useAuditLog: () => ({ data: { data: [] }, isLoading: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
afterEach(() => {
  cleanup();
  clearOwnerSession();
  vi.unstubAllGlobals();
});

function RegisteredActions() {
  return useCommandMenuActions().map((command) => (
    <button key={command.id} onClick={command.perform}>
      {command.label}
    </button>
  ));
}

it("describes finite supported scopes and exports all through the rendered and palette actions", async () => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    const data = url === "/api/data/export"
      ? { signed_url: "/data/export/download/synthetic", expires_at: "later" }
      : url === "/api/permissions"
        ? { butlers: [], permissions: [], cells: {} } : [];
    return new Response(JSON.stringify({ data }), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  rememberOwnerCsrf({
    csrf_token: "synthetic-export-scope-csrf",
    csrf_expires_at: new Date(Date.now() + 60_000).toISOString(),
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <CommandRegistryProvider>
          <SettingsPermissionsPage />
          <RegisteredActions />
        </CommandRegistryProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(
    screen
      .getByRole("combobox", { name: "Export scope" })
      .textContent?.replace(/\s+/g, " "),
  ).toMatch("All data (supported scopes)");
  expect(
    screen
      .getByText(/includes memory facts, rules and episodes/)
      .textContent?.replace(/\s+/g, " "),
  ).toMatch(
    /every configured memory butler; per-butler runtime config; the audit log, model catalog and permissions/,
  );
  expect(
    screen.getByText(/Other domain records/).textContent?.replace(/\s+/g, " "),
  ).toMatch(
    /other brain tables, sessions, schedules and credential stores are excluded/,
  );
  expect(
    screen
      .getByText(/consistent global snapshot/)
      .textContent?.replace(/\s+/g, " "),
  ).toMatch(/not a full backup/);
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Export all data (supported scopes)",
    }),
  );
  await screen.findByRole("link", { name: "/data/export/download/synthetic" });
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.filter(([path]) => path === "/api/data/export"),
    ).toHaveLength(2),
  );
  for (const [, init] of fetchMock.mock.calls.filter(
    ([path]) => path === "/api/data/export",
  )) {
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ scope: "all" });
  }
});
