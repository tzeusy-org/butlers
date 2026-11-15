/**
 * SettingsPublicMcpPage — /settings/public-mcp
 *
 * REQ-dashboard-settings-console-001: the rows with state, time and actor; owner disable and
 * re-enable; no overstated effect; a loading state that is not actionable; a write failure that
 * keeps the truthful state; keyboard and assistive-technology access.
 */

// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PublicMcpControl } from "@/api/public-mcp-controls";
import SettingsPublicMcpPage from "@/pages/SettingsPublicMcpPage";

expect.extend(toHaveNoViolations);

const api = vi.hoisted(() => ({
  getPublicMcpControls: vi.fn(),
  getPublicMcpSummary: vi.fn(),
  setPublicMcpControl: vi.fn(),
}));
vi.mock("@/api/public-mcp-controls", () => api);

const DOMAINS = [
  "all",
  "knowledge",
  "relationship",
  "time",
  "travel",
  "finance",
  "health",
  "proposals",
];
const CHANGED = "2026-11-14T09:00:00Z";

function table(over: Record<string, Partial<PublicMcpControl>> = {}) {
  return DOMAINS.map((domain) => ({
    domain,
    state: "enabled",
    changed_at: CHANGED,
    changed_by: "system",
    ...over[domain],
  })) as PublicMcpControl[];
}

function listing(rows: PublicMcpControl[]) {
  api.getPublicMcpControls.mockResolvedValue({
    data: {
      controls: rows,
      summary: { disabled_domains: 0, total_domains: 7, all_disabled: false },
    },
  });
}

function view() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsPublicMcpPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// The switches exist while loading (inert), so wait for a loaded state before using one.
async function switchFor(name: string) {
  await screen.findByTestId("state-all");
  return screen.getByRole("switch", { name: `Public MCP ${name}` });
}

beforeEach(() => {
  api.getPublicMcpControls.mockReset();
  api.getPublicMcpSummary.mockReset();
  api.setPublicMcpControl.mockReset();
});
afterEach(cleanup);

describe("rows", () => {
  it("lists all and each domain with its state, change time and actor", async () => {
    listing(table({ finance: { state: "disabled", changed_by: "owner:dashboard" } }));
    view();

    await screen.findByTestId("state-all");
    const rows = screen.getAllByRole("listitem");
    expect(rows.map((row) => row.getAttribute("data-domain"))).toEqual(DOMAINS);
    const finance = rows[5];
    expect(within(finance).getByText("Disabled")).toBeTruthy();
    expect(within(finance).getByText(/by owner:dashboard/)).toBeTruthy();
    expect(finance.querySelector("time")).not.toBeNull();
    expect(within(rows[1]).getByText("Enabled")).toBeTruthy();
  });

  it("says a missing row has no record and turns off from it, never on", async () => {
    listing(table({ travel: { state: null, changed_at: null, changed_by: null } }));
    api.setPublicMcpControl.mockResolvedValue({ data: {} });
    view();
    const travel = await switchFor("Travel");
    expect(screen.getByText("No row")).toBeTruthy();
    expect(screen.getByText("No change recorded")).toBeTruthy();
    await userEvent.setup().click(travel);
    expect(api.setPublicMcpControl).toHaveBeenCalledWith("travel", "disabled");
  });
});

describe("writes", () => {
  it("disables a domain in one click, with no confirm step, and shows the server's answer", async () => {
    listing(table());
    api.setPublicMcpControl.mockImplementation(async () => {
      listing(table({ finance: { state: "disabled", changed_by: "owner:dashboard" } }));
      return { data: {} };
    });
    view();

    const finance = await switchFor("Finance");
    expect(finance.getAttribute("aria-checked")).toBe("true");
    await userEvent.setup().click(finance);

    expect(api.setPublicMcpControl).toHaveBeenCalledTimes(1);
    expect(api.setPublicMcpControl).toHaveBeenCalledWith("finance", "disabled");
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Public MCP Finance" }).getAttribute("aria-checked")).toBe("false"),
    );
    expect(screen.getByTestId("state-finance").textContent).toContain("Disabled");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("re-enables in one click", async () => {
    listing(table({ health: { state: "disabled" } }));
    api.setPublicMcpControl.mockResolvedValue({ data: {} });
    view();
    const health = await switchFor("Health");
    expect(health.getAttribute("aria-checked")).toBe("false");
    await userEvent.setup().click(health);
    expect(api.setPublicMcpControl).toHaveBeenCalledWith("health", "enabled");
  });

  it("does not flip the switch before the server answers", async () => {
    listing(table());
    let release: (value: unknown) => void = () => {};
    api.setPublicMcpControl.mockReturnValue(new Promise((resolve) => (release = resolve)));
    view();

    const finance = await switchFor("Finance");
    await userEvent.setup().click(finance);

    expect(screen.getByRole("switch", { name: "Public MCP Finance" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("state-finance").textContent).toContain("Enabled");
    // One write in flight: nothing else accepts a second activation.
    expect(
      screen.getAllByRole("switch").every((el) => (el as HTMLButtonElement).disabled),
    ).toBe(true);
    release({ data: {} });
  });

  it("keeps the previous state and shows the error and a retry when the write fails", async () => {
    listing(table());
    api.setPublicMcpControl.mockRejectedValueOnce(new Error("public_mcp_controls_unavailable"));
    view();

    const user = userEvent.setup();
    await user.click(await switchFor("Finance"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not disable Finance");
    expect(alert.textContent).toContain("public_mcp_controls_unavailable");
    expect(screen.getByRole("switch", { name: "Public MCP Finance" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("state-finance").textContent).toContain("Enabled");

    api.setPublicMcpControl.mockResolvedValueOnce({ data: {} });
    await user.click(within(alert).getByText("Retry →"));
    expect(api.setPublicMcpControl).toHaveBeenLastCalledWith("finance", "disabled");
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("states a saved change as a replication delay, not as immediate effect", async () => {
    listing(table());
    api.setPublicMcpControl.mockResolvedValue({ data: {} });
    view();
    await userEvent.setup().click(await switchFor("Time"));
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("within 15 minutes");
    expect(status.textContent).not.toMatch(/immediately|right away|instantly/i);
  });
});

describe("loading and failure", () => {
  it("shows a skeleton per row and no actionable switch while loading", async () => {
    api.getPublicMcpControls.mockReturnValue(new Promise(() => {}));
    view();

    expect(screen.getAllByTestId("switch-skeleton")).toHaveLength(DOMAINS.length);
    const switches = screen.getAllByRole("switch") as HTMLButtonElement[];
    expect(switches).toHaveLength(DOMAINS.length);
    expect(switches.every((el) => el.disabled)).toBe(true);
    await userEvent.setup().click(switches[5]);
    expect(api.setPublicMcpControl).not.toHaveBeenCalled();
    expect(screen.getByRole("list", { name: "Public MCP switches" }).getAttribute("aria-busy")).toBe("true");
  });

  it("offers a retry when the switches cannot be loaded", async () => {
    api.getPublicMcpControls.mockRejectedValueOnce(new Error("down"));
    view();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not load the switches.");
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
    listing(table());
    await userEvent.setup().click(within(alert).getByText("Retry →"));
    expect(await switchFor("All")).toBeTruthy();
  });
});

describe("copy", () => {
  it("states 15 minutes, the importer pass, the break-glass ConfigMap and scale-to-zero", async () => {
    listing(table());
    view();
    await switchFor("All");
    const copy = screen.getByRole("region", { name: "How a change takes effect" }).textContent ?? "";
    expect(copy).toContain("within 15 minutes");
    expect(copy).toContain("Relationship proposal importer honours it at its next pass");
    expect(copy).toContain("butlers-public-mcp-switches");
    expect(copy).toContain("disabled_domains");
    expect(copy).toContain("re-read every 10 seconds");
    expect(copy).toContain("scale the public server to zero");
    expect(copy).not.toMatch(/takes effect immediately|instantly/i);
  });
});

describe("keyboard and assistive technology", () => {
  it("names each switch by its domain, announces its state, and is reachable by Tab", async () => {
    listing(table({ finance: { state: "disabled" } }));
    view();
    await switchFor("All");

    const switches = screen.getAllByRole("switch");
    expect(switches.map((el) => el.getAttribute("aria-label"))).toEqual(
      ["All", "Knowledge", "Relationship", "Time", "Travel", "Finance", "Health", "Proposals"].map(
        (name) => `Public MCP ${name}`,
      ),
    );
    expect(switches[5].getAttribute("aria-checked")).toBe("false");
    expect(switches[1].getAttribute("aria-checked")).toBe("true");

    const user = userEvent.setup();
    await user.tab();
    expect(document.activeElement).toBe(switches[0]);
    await user.tab();
    expect(document.activeElement).toBe(switches[1]);
  });

  it("toggles from the keyboard with Space and with Enter", async () => {
    listing(table());
    api.setPublicMcpControl.mockResolvedValue({ data: {} });
    view();
    const user = userEvent.setup();
    const knowledge = await switchFor("Knowledge");
    knowledge.focus();
    await user.keyboard(" ");
    await waitFor(() => expect(api.setPublicMcpControl).toHaveBeenCalledWith("knowledge", "disabled"));
    await waitFor(() => expect((knowledge as HTMLButtonElement).disabled).toBe(false));
    await user.keyboard("{Enter}");
    await waitFor(() => expect(api.setPublicMcpControl).toHaveBeenCalledTimes(2));
  });

  it("never relies on colour alone: the state is a word beside the dot", async () => {
    listing(table({ proposals: { state: "disabled" } }));
    view();
    await switchFor("All");
    expect(screen.getByTestId("state-proposals").textContent).toBe("Disabled");
    expect(screen.getByTestId("state-time").textContent).toBe("Enabled");
  });

  it("has no axe violations loaded or loading", async () => {
    listing(table({ finance: { state: "disabled" } }));
    const { container } = view();
    await switchFor("All");
    expect(await axe(container)).toHaveNoViolations();
  });
});
