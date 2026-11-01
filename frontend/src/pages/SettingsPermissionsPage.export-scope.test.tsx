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

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  apiFetch,
  resolveApiHref: (path: string) => `/api${path}`,
}));
vi.mock("@/hooks/use-audit-log", () => ({
  useAuditLog: () => ({ data: { data: [] }, isLoading: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
afterEach(() => {
  cleanup();
  clearOwnerSession();
});

function RegisteredActions() {
  return useCommandMenuActions().map((command) => (
    <button key={command.id} onClick={command.perform}>
      {command.label}
    </button>
  ));
}

it("describes finite supported scopes and exports all through the rendered and palette actions", async () => {
  apiFetch.mockImplementation(async (path: string) => {
    if (path === "/data/export")
      return {
        data: {
          signed_url: "/data/export/download/synthetic",
          expires_at: "later",
        },
      };
    if (path === "/permissions")
      return { data: { butlers: [], permissions: [], cells: {} } };
    return { data: [] };
  });
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
      apiFetch.mock.calls.filter(([path]) => path === "/data/export"),
    ).toHaveLength(2),
  );
  for (const [, init] of apiFetch.mock.calls.filter(
    ([path]) => path === "/data/export",
  )) {
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ scope: "all" });
  }
});
